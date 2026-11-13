"""The Agent SDK side of a first-hour run: the product session and the owner model.

What a run does and measures is decided in ``harness.py`` and the modules beside it; this module
turns those calls into Claude Code sessions and nothing more. Each session runs with the run's
environment and throwaway home, with no MCP server, no connector, no user settings, the plugin
under test and the three companions loaded from the run, and a tool list (``policy.TOOLS``)
outside which nothing is approved. Every command the session runs goes through the sandbox's
shell prefix (``sandbox.py``). Every tool call passes ``policy.decide`` in a PreToolUse hook that
blocks when it fails, and every prompt passes it again in ``can_use_tool`` before the owner
answers.

It needs ``claude-agent-sdk``, which ``run.py`` declares; the unit suite does not install it, so
coverage omits this file and the suite drives ``harness.py`` with fakes instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
import time
from typing import TYPE_CHECKING, Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookContext,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    SystemMessage,
    TaskNotificationMessage,
    TaskStartedMessage,
    ToolPermissionContext,
    query,
)

from first_hour import harness, owner, policy, sandbox

if TYPE_CHECKING:
    from collections.abc import Callable

SETTLE_S = 20.0
IDLE_S = 30 * 60.0
WAITED_TASKS = frozenset({"local_workflow", "local_agent", "remote_agent"})
TERMINAL = frozenset({"completed", "failed", "stopped", "killed", "error"})
# Why: these ask nothing of the owner and the hook judges each call; prompting for them would
# count prompts no real owner sees.
AUTO = tuple(
    name
    for name, tool in policy.TOOLS.items()
    if tool.kind in (policy.Kind.READ, policy.Kind.SESSION, policy.Kind.MESSAGE)
)


class SdkOwner:
    """The scripted owner: a small model in character, with no tools, and the rule applied."""

    def __init__(self, run: harness.Run, token: str) -> None:
        """An owner for this run."""
        self.run = run
        self.token = token
        self.usd = 0.0

    async def _ask(self, prompt: str, schema: dict[str, object]) -> object:
        options = ClaudeAgentOptions(
            model=self.run.config.owner_model,
            system_prompt=owner.system_prompt(self.run.config.persona),
            setting_sources=[],
            allowed_tools=[],
            tools=[],
            strict_mcp_config=True,
            mcp_servers={},
            max_turns=3,
            output_format={"type": "json_schema", "schema": schema},
            cwd=str(self.run.layout.owner),
            cli_path=str(self.run.tools["claude"]),
            env={"CLAUDE_CODE_OAUTH_TOKEN": self.token},
        )
        found: object = ""
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                self.usd += message.total_cost_usd or 0.0
                self.run.log.emit("owner-cost", usd=message.total_cost_usd)
                found = (
                    message.structured_output
                    if message.structured_output is not None
                    else message.result or ""
                )
        return found

    async def answer(self, question: owner.Question, channel: owner.Channel) -> owner.Answer:
        """Answer one question in character, logged with its class."""
        prompt = owner.question_prompt(question)
        verdict = None
        for _ in range(2):
            try:
                verdict = owner.parse_verdict(await self._ask(prompt, owner.QUESTION_SCHEMA))
                break
            except (ValueError, TypeError) as error:
                self.run.log.emit("owner-error", error=str(error), question=question.text)
        if verdict is None:
            verdict = owner.Verdict(owner.Kind.DEFAULT_ABLE, (), "the owner model gave no verdict")
        reply = owner.answer(question, verdict)
        self.run.log.emit(**reply.event(channel))  # type: ignore[arg-type]
        return reply

    async def read_turn(self, text: str) -> owner.Reply:
        """Whether a turn's last message asks the owner something."""
        for _ in range(2):
            try:
                return owner.parse_reply(
                    await self._ask(owner.turn_prompt(text), owner.REPLY_SCHEMA)
                )
            except (ValueError, TypeError) as error:
                self.run.log.emit("owner-error", error=str(error), question="turn end")
        return owner.Reply(
            needs_reply=False,
            kind=owner.Kind.DEFAULT_ABLE,
            text="",
            reason="the owner model gave no reading",
        )


class SdkSession:
    """The product session: one Claude Code session the owner types into."""

    def __init__(
        self,
        run: harness.Run,
        owner_: harness.OwnerModel,
        token: str,
        *,
        decide: Callable[[str, dict[str, Any]], policy.Decision] | None = None,
    ) -> None:
        """The session's options; ``decide`` replaces the policy (the probe allows less)."""
        self.run = run
        self.owner = owner_
        self.scope = run.layout.scope()
        self.decide = decide or self._policy
        self.queue: asyncio.Queue[object] = asyncio.Queue()
        self.waiting: set[str] = set()
        self.agents: set[str] = set()
        self.last_text = ""
        self.costs: dict[str, float] = {}
        self.init: dict[str, Any] = {}
        self.stderr = (run.layout.root / "stderr.log").open("a", encoding="utf-8")
        layout = run.layout
        app = str(layout.app)
        plugins = [layout.pinned, *(sandbox.copied(layout, p) for p in run_plugins(run))]
        self.client = ClaudeSDKClient(
            ClaudeAgentOptions(
                cwd=app,
                model=run.config.model,
                cli_path=str(run.tools["claude"]),
                setting_sources=["project", "local"],
                settings=json.dumps(sandbox.settings(layout)),
                plugins=[{"type": "local", "path": str(p)} for p in plugins],
                tools=list(policy.TOOLS),
                allowed_tools=[*AUTO, *(f"{t}(/{app}/**)" for t in ("Edit", "Write"))],
                disallowed_tools=list(policy.DENIED_TOOLS),
                strict_mcp_config=True,
                mcp_servers={},
                permission_mode="default",
                can_use_tool=self.can_use_tool,
                hooks={
                    "PreToolUse": [
                        HookMatcher(matcher=None, hooks=[self.guard], on_failure="block")
                    ]
                },
                max_budget_usd=run.config.budget.usd,
                env={"CLAUDE_CODE_OAUTH_TOKEN": token},
                stderr=self._stderr,
            )
        )
        self.pump: asyncio.Task[None] | None = None

    def _stderr(self, line: str) -> None:
        self.stderr.write(line + "\n")

    def _policy(self, tool: str, tool_input: dict[str, Any]) -> policy.Decision:
        return policy.decide(tool, tool_input, self.scope, agents=self.agents)

    async def connect(self) -> None:
        """Connect, and start reading the session's messages."""
        await self.client.connect()
        self.pump = asyncio.create_task(self._pump())

    async def close(self) -> None:
        """Stop reading and disconnect, which ends the session's sandbox and all inside it."""
        if self.pump is not None:
            self.pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self.pump
        with contextlib.suppress(Exception):
            await self.client.disconnect()
        self.stderr.close()

    async def _pump(self) -> None:
        async for message in self.client.receive_messages():
            await self.queue.put(message)
        await self.queue.put(None)

    def _deny(self, tool: str, tool_input: dict[str, Any], *, why: str, where: str) -> str:
        self.run.log.emit(
            "denied", tool=tool, why=why, at=where, input=json.dumps(tool_input, default=str)[:600]
        )
        return f"The first-hour harness refuses this call: it {why}."

    async def guard(
        self, data: dict[str, Any], _id: str | None, _ctx: HookContext
    ) -> dict[str, Any]:
        """Judge every tool call before anything else does; deny what the policy refuses."""
        tool = str(data.get("tool_name", ""))
        tool_input = data.get("tool_input") or {}
        decision = self.decide(tool, tool_input)
        if decision.allowed:
            return {}
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": self._deny(
                    tool, tool_input, why=decision.why, where="hook"
                ),
            }
        }

    async def can_use_tool(
        self, tool_name: str, tool_input: dict[str, Any], context: ToolPermissionContext
    ) -> PermissionResultAllow | PermissionResultDeny:
        """Answer a prompt as the owner, after the policy; answer a question dialog."""
        decision = self.decide(tool_name, tool_input)
        if not decision.allowed:
            why = self._deny(tool_name, tool_input, why=decision.why, where="prompt")
            return PermissionResultDeny(message=why)
        if tool_name == "AskUserQuestion":
            answers = {}
            for question in owner.questions_of(tool_input):
                left = self.run.deadline - time.monotonic()
                try:
                    reply = await asyncio.wait_for(
                        self.owner.answer(question, owner.Channel.DIALOG), timeout=max(left, 0.1)
                    )
                except TimeoutError:
                    return PermissionResultDeny(message="The run's time is up.", interrupt=True)
                answers[question.text] = reply.text
            return PermissionResultAllow(updated_input={**tool_input, "answers": answers})
        self.run.log.emit(
            "prompt",
            tool=tool_name,
            decision="allow",
            reason=getattr(context, "decision_reason", None) or "",
            title=getattr(context, "title", None),
            input=json.dumps(tool_input, default=str)[:600],
        )
        return PermissionResultAllow()

    def _track(self, message: object) -> None:
        # Why: the SDK says not to replace the set with a snapshot, which can miss an agent
        # moved to the background later; ids are added from either and cleared on a terminal
        # status only, and IDLE_S bounds an id nothing ever clears.
        if isinstance(message, TaskStartedMessage):
            self.agents.add(message.task_id)
            if message.task_type in WAITED_TASKS:
                self.waiting.add(message.task_id)
        elif isinstance(message, SystemMessage) and message.subtype == "init":
            self.init = dict(message.data)
        elif isinstance(message, SystemMessage) and message.subtype == "background_tasks_changed":
            tasks = message.data.get("tasks") or []
            self.waiting |= {
                str(t.get("task_id"))
                for t in tasks
                if isinstance(t, dict) and t.get("task_type") in WAITED_TASKS
            }
        elif isinstance(message, TaskNotificationMessage) and message.status in TERMINAL:
            self.waiting.discard(message.task_id)
        elif isinstance(message, SystemMessage) and message.subtype == "task_updated":
            patch = message.data.get("patch") or {}
            if isinstance(patch, dict) and patch.get("status") in TERMINAL:
                self.waiting.discard(str(message.data.get("task_id")))

    async def turn(self, text: str, deadline: float) -> str:
        """Type ``text``; return the last result once no workflow or agent runs behind it."""
        await self.client.query(text)
        settled = False
        quiet_since = time.monotonic()
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                with contextlib.suppress(Exception):
                    await self.client.interrupt()
                raise harness.Deadline
            wait = min(SETTLE_S if settled and not self.waiting else IDLE_S, left)
            try:
                message = await asyncio.wait_for(self.queue.get(), timeout=wait)
            except TimeoutError:
                if settled and not self.waiting:
                    return self.last_text
                if time.monotonic() - quiet_since > IDLE_S:
                    self.run.log.emit("idle", waiting=sorted(self.waiting))
                    return self.last_text
                continue
            quiet_since = time.monotonic()
            if message is None:
                msg = "the product session ended"
                raise RuntimeError(msg)
            self.run.log.message(message)
            self._track(message)
            if isinstance(message, ResultMessage):
                settled = True
                self.last_text = message.result or self.last_text
                session = message.session_id
                self.costs[session] = max(
                    self.costs.get(session, 0.0), message.total_cost_usd or 0.0
                )
                if "budget" in (message.subtype or ""):
                    raise harness.OverBudget
            elif isinstance(message, AssistantMessage):
                settled = False


def run_plugins(run: harness.Run) -> list[sandbox.Plugin]:
    """The companion plugins the run copied in, from its own registry."""
    registry = json.loads((run.layout.plugins / "installed_plugins.json").read_text("utf-8"))
    markets = json.loads((run.layout.plugins / "known_marketplaces.json").read_text("utf-8"))
    return [
        p
        for p in sandbox.installed(registry, markets, harness.OTHER_PLUGINS)
        if p.path != run.layout.pinned
    ]


class SdkModels:
    """Starts the run's owner model and product session through the Agent SDK."""

    def __init__(self, token: str) -> None:
        """Models signed in with the operator's current model access token."""
        self.token = token

    def owner(self, run: harness.Run) -> SdkOwner:
        """The owner model."""
        return SdkOwner(run, self.token)

    async def session(self, run: harness.Run, owner_: harness.OwnerModel) -> SdkSession:
        """The product session, connected."""
        session = SdkSession(run, owner_, self.token)
        await session.connect()
        return session


# Why: three values say whether the launch holds: gh's config is the run's, the session is not
# the launcher's, and the model token is not handed to the session's shell. The last two lines
# print only a login state and a count: whether gh is signed in, and how many processes the shell
# can see whose environment holds the token (the Claude Code process itself, if it is visible).
PROBE_COMMANDS = (
    (
        'echo "GH_CONFIG_DIR=$GH_CONFIG_DIR CLAUDE_CODE_SESSION_ID=$CLAUDE_CODE_SESSION_ID'
        ' TOKEN_IN_SHELL=${CLAUDE_CODE_OAUTH_TOKEN:+yes}"'
    ),
    "gh auth status",
    "cat /proc/[0-9]*/environ 2>/dev/null | tr '\\0' '\\n' | grep -c '^CLAUDE_CODE_OAUTH_TOKEN='",
)
PROBE = (
    "First, list the exact names of every tool you have, one per line, and nothing else."
    " Then run each of these commands with the Bash tool, one call each, exactly as written:\n"
    + "\n".join(f"{c}" for c in PROBE_COMMANDS)
    + "\nThen stop. Do not run anything else."
)


async def probe(run: harness.Run, token: str) -> dict[str, object]:
    """One session with the run's launch, allowed nothing but the probe's printing commands."""

    def decide(tool: str, tool_input: dict[str, Any]) -> policy.Decision:
        if tool == "Bash" and str(tool_input.get("command", "")).strip() in PROBE_COMMANDS:
            return policy.ALLOW
        return policy.Decision(allowed=False, why="is not one of the probe's commands")

    class Mute:
        usd = 0.0

        async def answer(self, question: owner.Question, channel: owner.Channel) -> owner.Answer:
            raise NotImplementedError

        async def read_turn(self, text: str) -> owner.Reply:
            raise NotImplementedError

    session = SdkSession(run, Mute(), token, decide=decide)
    await session.connect()
    try:
        text = await session.turn(PROBE, run.deadline)
    finally:
        await session.close()
        shutil.rmtree(run.layout.runtime, ignore_errors=True)
    records = [
        json.loads(line)
        for line in (run.layout.root / "transcript.jsonl").read_text("utf-8").splitlines()
    ]
    results = [
        str(block.get("content"))[:400]
        for record in records
        for block in record["msg"].get("content") or []
        if isinstance(block, dict) and block.get("_type") == "ToolResultBlock"
    ]
    tools = [str(t) for t in session.init.get("tools", [])]
    return {
        "tools": tools,
        "mcp_tools": [t for t in tools if t.startswith("mcp__")],
        "mcp_servers": session.init.get("mcp_servers"),
        "plugins": session.init.get("plugins"),
        "results": results,
        "reply": text,
        "cost_usd": sum(session.costs.values()),
    }
