"""The run's sandbox: every command of a run sees its own directory and the network, no more.

Every shell command of a run (each Bash call and hook of the product session, through Claude
Code's ``CLAUDE_CODE_SHELL_PREFIX``, and each pasted command, route call and app check of the
harness) runs under ``bwrap``: the whole file system read-only, every home directory and the
per-user runtime directory hidden, ``/tmp`` replaced by the run's own, a fresh process namespace
that dies with its parent, and no model credential in its environment. Inside, ``HOME`` is the
run's throwaway home: no forge login, no SSH key or agent, no git credential helper.

The Claude Code processes themselves run outside ``bwrap``, the split Claude Code's own sandbox
makes: they hold the model token, which a command must not see, and a sandbox cannot start another
inside it here. They run with the same throwaway home and environment, so they load no settings,
memory, MCP server or connector of the operator's; their file tools reach only what the policy
(``policy.py``) allows. What is shared is the network: the model and the package registries need
it.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from first_hour import policy

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

# Why: these live under the operator's home, which the sandbox hides; each is bound back read-only.
TOOLCHAIN = ("claude", "uv", "uvx", "just")
# Why: Claude Code holds the model token in its environment and passes its environment on.
CREDENTIALS = (
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_SESSION_ACCESS_TOKEN",
)
KEEP = ("LANG", "LC_ALL", "LC_CTYPE", "TERM", "TZ", "USER", "LOGNAME")
SYSTEM_PATH = "/usr/local/bin:/usr/bin:/bin"
INSTALLED_AT = "2026-01-01T00:00:00.000Z"
GITCONFIG = """\
[user]
\tname = First-hour owner
\temail = owner@first-hour.invalid
[init]
\tdefaultBranch = main
[credential]
\thelper =
[url "file:///nonexistent/first-hour-never-pushes/"]
\tpushInsteadOf = https://
\tpushInsteadOf = http://
\tpushInsteadOf = ssh://
\tpushInsteadOf = git://
\tpushInsteadOf = git@
"""


@dataclass(frozen=True)
class Layout:
    """Where one run keeps everything. The sessions write to ``writable`` and nowhere else."""

    root: Path

    @property
    def app(self) -> Path:
        """The owner's project."""
        return self.root / "app"

    @property
    def home(self) -> Path:
        """The sessions' ``HOME``."""
        return self.root / "home"

    @property
    def config(self) -> Path:
        """Claude Code's configuration directory inside the throwaway home."""
        return self.home / ".claude"

    @property
    def plugins(self) -> Path:
        """The plugin registry and the companion plugins' copies; read-only inside."""
        return self.config / "plugins"

    @property
    def gh(self) -> Path:
        """``gh``'s configuration directory: empty, so not logged in."""
        return self.home / ".config" / "gh"

    @property
    def tmp(self) -> Path:
        """The run's temp root: ``TMPDIR``, and ``/tmp`` inside the sandbox."""
        return self.root / "tmp"

    @property
    def bin(self) -> Path:
        """The toolchain links and the session launcher."""
        return self.root / "bin"

    @property
    def pinned(self) -> Path:
        """The plugin under test: a clone of the checkout's HEAD, read-only inside."""
        return self.root / "plugin"

    @property
    def owner(self) -> Path:
        """The owner model's working directory."""
        return self.root / "owner"

    @property
    def appcheck(self) -> Path:
        """Where the app check checks the default branch out."""
        return self.root / "appcheck"

    @property
    def logs(self) -> Path:
        """Each paste's and app check's output."""
        return self.root / "logs"

    @property
    def runtime(self) -> Path:
        """``XDG_RUNTIME_DIR`` of the Claude Code processes: a short directory of the run's own.

        Why: Claude Code puts its messaging socket there, and when the path is too long for a
        socket it falls back to the ``/tmp/cc-socks-<uid>`` that the operator's sessions share.
        A run directory is often too deep, so this one sits at the top of the temp location.
        """
        digest = hashlib.sha256(str(self.root).encode()).hexdigest()[:12]
        return Path(tempfile.gettempdir()) / f"first-hour-{digest}"

    @property
    def shell(self) -> Path:
        """The shell prefix: Claude Code starts every command through it, in the sandbox."""
        return self.bin / "claude-shell"

    @property
    def writable(self) -> tuple[Path, ...]:
        """What a session may write: the project, the home and the temp root."""
        return (self.app, self.home, self.tmp)

    def scope(self) -> policy.Scope:
        """The product session's reach, for the policy."""
        return policy.Scope(
            root=self.root,
            cwd=self.app,
            home=self.home,
            tmp=self.tmp,
            writable=self.writable,
            readonly=(self.plugins,),
        )


def environment(layout: Layout, inherited: Mapping[str, str]) -> dict[str, str]:
    """The whole environment of every process of the run, built from nothing.

    Only locale and terminal settings are kept from the launching process: no ``CLAUDE_CODE_*``
    or ``CLAUDECODE`` of the session that started the harness, no token, no agent socket.
    """
    env = {k: v for k in KEEP if (v := inherited.get(k))}
    env.update(
        {
            "PATH": f"{layout.bin}:{SYSTEM_PATH}",
            "HOME": str(layout.home),
            "SHELL": "/bin/sh",
            "TMPDIR": str(layout.tmp),
            "XDG_CONFIG_HOME": str(layout.home / ".config"),
            "XDG_CACHE_HOME": str(layout.home / ".cache"),
            "XDG_DATA_HOME": str(layout.home / ".local" / "share"),
            "XDG_STATE_HOME": str(layout.home / ".local" / "state"),
            "XDG_RUNTIME_DIR": str(layout.runtime),
            "RATCHET_PLUGIN": str(layout.pinned),
            "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0",
            "DISABLE_AUTOUPDATER": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
            "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
            "CLAUDE_CODE_DISABLE_WEB_FETCH": "1",
            **session_env(layout),
        }
    )
    return env


def session_env(layout: Layout) -> dict[str, str]:
    """What keeps commands in the sandbox and ``gh`` and ``git`` logged out.

    It is also the session's ``--settings`` ``env``: Claude Code applies a settings value over the
    one it inherits, and flag settings over the project's, so a project file the session writes
    cannot take the shell prefix away.
    """
    return {
        "CLAUDE_CODE_SHELL_PREFIX": str(layout.shell),
        "GH_CONFIG_DIR": str(layout.gh),
        "GH_TOKEN": "",
        "GITHUB_TOKEN": "",
        "GH_ENTERPRISE_TOKEN": "",
        "GLAB_CONFIG_DIR": str(layout.home / ".config" / "glab-cli"),
        "GITLAB_TOKEN": "",
        "GIT_CONFIG_GLOBAL": str(layout.home / ".gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_MERGE_AUTOEDIT": "no",
        "SSH_AUTH_SOCK": "",
    }


def toolchain(found: Mapping[str, str | None]) -> dict[str, Path]:
    """Each toolchain program found on the operator's ``PATH``, by its real file."""
    return {name: Path(path).resolve() for name, path in found.items() if path}


def argv(
    layout: Layout,
    command: Sequence[str],
    *,
    tools: Iterable[Path] = (),
    writable: Sequence[Path] | None = None,
) -> list[str]:
    """``command`` under ``bwrap``: the run's view of the machine, and a fresh process namespace.

    The order matters: later mounts sit on top of earlier ones. The whole system is read-only;
    every home, the per-user runtime directory and ``/tmp`` are replaced; the run is read-only
    but for ``writable``; the plugin registry stays read-only inside the writable home.
    """
    trees = layout.writable if writable is None else tuple(writable)
    args = ["bwrap", "--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc"]
    args += ["--tmpfs", "/home", "--tmpfs", "/run/user"]
    args += ["--bind", str(layout.tmp), "/tmp", "--bind", str(layout.tmp), "/var/tmp"]
    args += ["--ro-bind", str(layout.root), str(layout.root)]
    for tree in trees:
        args += ["--bind", str(tree), str(tree)]
    args += ["--ro-bind", str(layout.plugins), str(layout.plugins)]
    for tool in sorted(set(tools)):
        args += ["--ro-bind", str(tool), str(tool)]
    args += ["--remount-ro", "/home"]
    args += ["--unshare-pid", "--unshare-ipc", "--die-with-parent", "--new-session"]
    return [*args, "--", *command]


def shell_prefix(layout: Layout, tools: Mapping[str, Path]) -> str:
    """The script Claude Code runs each command line through: it runs the line in the sandbox.

    Claude Code calls it with the whole command line as its one argument, for Bash calls and
    hooks alike. The credentials go before ``bwrap`` starts, so that no process in the sandbox,
    ``bwrap``'s own included, was started with them.
    """
    unset = [w for name in CREDENTIALS for w in ("-u", name)]
    words = ["env", *unset, *argv(layout, ["/bin/bash", "-c"], tools=tools.values())]
    quoted = " ".join(_quote(w) for w in words)
    return f'#!/bin/sh\nexec {quoted} "$1"\n'


def _quote(word: str) -> str:
    return "'" + word.replace("'", "'\"'\"'") + "'"


@dataclass(frozen=True)
class Plugin:
    """An installed plugin: its registry key, version, commit, folder and marketplace source."""

    key: str
    version: str
    commit: str | None
    path: Path
    source: Mapping[str, object] = field(default_factory=dict)

    @property
    def name(self) -> str:
        """The plugin's name, as skills are namespaced."""
        return self.key.split("@", 1)[0]

    @property
    def marketplace(self) -> str:
        """The marketplace it came from."""
        return self.key.split("@", 1)[1]


def installed(
    registry: Mapping[str, object], markets: Mapping[str, object], names: Iterable[str]
) -> list[Plugin]:
    """The named plugins in ``installed_plugins.json``, each by its first install.

    Each carries its marketplace's source from ``known_marketplaces.json``.
    """
    plugins = registry.get("plugins")
    wanted = set(names)
    found = []
    for key, entries in (plugins if isinstance(plugins, dict) else {}).items():
        if key.split("@", 1)[0] not in wanted or not isinstance(entries, list) or not entries:
            continue
        entry = entries[0]
        market = markets.get(key.split("@", 1)[-1])
        source = market.get("source") if isinstance(market, dict) else None
        found.append(
            Plugin(
                key=key,
                version=str(entry.get("version")),
                commit=entry.get("gitCommitSha"),
                path=Path(str(entry.get("installPath"))),
                source=source if isinstance(source, dict) else {},
            )
        )
    return sorted(found, key=lambda p: p.key)


def copied(layout: Layout, plugin: Plugin) -> Path:
    """Where a plugin sits in the run.

    The plugin under test is the pinned clone; a companion is copied to the cache path a skill
    expects under ``~/.claude``.
    """
    if layout.root in plugin.path.parents:
        return plugin.path
    return layout.plugins / "cache" / plugin.marketplace / plugin.name / plugin.version


def prepare(layout: Layout, tools: Mapping[str, Path], plugins: Sequence[Plugin]) -> None:
    """Make the throwaway home: git and gh logged out, the plugins copied in and registered.

    The registry and the settings name the copies so that ``claude plugin list`` and the init
    skill's cache lookup find what a fresh owner's install has: the four plugins and nothing
    else. The sessions load the copies with ``--plugin-dir``; they never read these settings.
    """
    for path in (
        layout.app,
        layout.tmp,
        layout.bin,
        layout.gh,
        layout.plugins,
        layout.owner,
        layout.logs,
    ):
        path.mkdir(parents=True, exist_ok=True)
    layout.appcheck.mkdir(exist_ok=True)
    layout.runtime.mkdir(mode=0o700, exist_ok=True)
    (layout.home / ".gitconfig").write_text(GITCONFIG, encoding="utf-8")
    layout.shell.write_text(shell_prefix(layout, tools), encoding="utf-8")
    layout.shell.chmod(0o755)
    for name, path in tools.items():
        link = layout.bin / name
        if not link.is_symlink():
            link.symlink_to(path)
    entries: dict[str, object] = {}
    markets: dict[str, object] = {}
    for plugin in plugins:
        target = copied(layout, plugin)
        if not target.exists():
            shutil.copytree(plugin.path, target, symlinks=True)
        entry: dict[str, object] = {
            "scope": "user",
            "installPath": str(target),
            "version": plugin.version,
            "installedAt": INSTALLED_AT,
            "lastUpdated": INSTALLED_AT,
        }
        if plugin.commit:
            entry["gitCommitSha"] = plugin.commit
        entries[plugin.key] = [entry]
        markets[plugin.marketplace] = _marketplace(layout, plugin)
    registry = {"version": 2, "plugins": entries}
    (layout.plugins / "installed_plugins.json").write_text(json.dumps(registry, indent=2) + "\n")
    (layout.plugins / "known_marketplaces.json").write_text(json.dumps(markets, indent=2) + "\n")
    settings = {"enabledPlugins": dict.fromkeys(entries, True)}
    (layout.config / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")


def _marketplace(layout: Layout, plugin: Plugin) -> dict[str, object]:
    """A marketplace that lists only this plugin; its source is the real one, never fetched."""
    where = layout.plugins / "marketplaces" / plugin.marketplace
    manifest = {
        "name": plugin.marketplace,
        "owner": {"name": plugin.marketplace},
        "plugins": [{"name": plugin.name, "source": "./", "version": plugin.version}],
    }
    (where / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (where / ".claude-plugin" / "marketplace.json").write_text(json.dumps(manifest, indent=2))
    return {
        "source": dict(plugin.source),
        "installLocation": str(where),
        "lastUpdated": INSTALLED_AT,
    }


def settings(layout: Layout) -> dict[str, object]:
    """The product session's own settings (``--settings``): they win over every file."""
    return {
        "env": session_env(layout),
        "disableClaudeAiConnectors": True,
        "enableAllProjectMcpServers": False,
        "autoMemoryEnabled": False,
        "permissions": {"deny": list(policy.DENIED_TOOLS)},
    }


MARGIN_MIN = 10.0


def model_token(credentials: Mapping[str, object], *, now: float, minutes: float) -> str:
    """The operator's current model access token, if it outlives the run.

    Only the access token goes to the run, never the refresh token: a session that cannot renew
    its sign-in cannot rotate the operator's out from under their own sessions either. So the
    run must end before the token does.
    """
    oauth = credentials.get("claudeAiOauth")
    token = oauth.get("accessToken") if isinstance(oauth, dict) else None
    expires = oauth.get("expiresAt") if isinstance(oauth, dict) else None
    if not isinstance(token, str) or not token or not isinstance(expires, (int, float)):
        msg = "no Claude sign-in found: sign in with `claude` first (a subscription login)"
        raise SystemExit(msg)
    left = (expires / 1000 - now) / 60
    if left < minutes + MARGIN_MIN:
        msg = (
            f"the Claude sign-in expires in {max(left, 0):.0f} minutes, before a run of"
            f" {minutes:g} minutes ends: start any Claude Code session to renew it, or lower"
            " --minutes"
        )
        raise SystemExit(msg)
    return token
