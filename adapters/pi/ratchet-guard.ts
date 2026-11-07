// Ratchet guard for pi: translate a tool call into the guard's PreToolUse JSON and act on its decision.
// The rules live in hooks/guard.py; this file only marshals. See docs/harness/pi.md.
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PLUGIN_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const GUARD = path.join(PLUGIN_ROOT, "hooks", "guard.py");
const SESSION = path.join(PLUGIN_ROOT, "hooks", "session.py");
const TOOLS: Record<string, string> = { bash: "Bash", edit: "Edit", write: "Write" };

type Decision = { permissionDecision?: string; permissionDecisionReason?: string };

function run(script: string, payload: object): { ok: boolean; stdout: string; stderr: string } {
  const result = spawnSync("python3", [script], { input: JSON.stringify(payload), encoding: "utf8" });
  return { ok: result.status === 0, stdout: result.stdout ?? "", stderr: result.stderr ?? "" };
}

function decide(toolName: string, input: Record<string, unknown>, cwd: string): Decision | null {
  const tool = TOOLS[toolName];
  if (!tool) return null;
  const toolInput = tool === "Bash" ? { command: input.command } : { file_path: input.path };
  const payload = { hook_event_name: "PreToolUse", tool_name: tool, tool_input: toolInput, cwd };
  const result = run(GUARD, payload);
  if (!result.ok && !result.stdout.trim()) {
    // Why: a guard that cannot run fails closed, as the Claude Code shim does.
    return { permissionDecision: "deny", permissionDecisionReason: `Ratchet guard could not run: ${result.stderr.trim()}` };
  }
  if (!result.stdout.trim()) return null;
  try {
    return (JSON.parse(result.stdout).hookSpecificOutput as Decision) ?? null;
  } catch {
    return { permissionDecision: "deny", permissionDecisionReason: "Ratchet guard returned an unreadable reply" };
  }
}

export default function (pi: any) {
  pi.on("tool_call", async (event: any, ctx: any) => {
    const decision = decide(event.toolName, event.input ?? {}, ctx.cwd ?? process.cwd());
    if (!decision) return;
    const reason = decision.permissionDecisionReason ?? "Ratchet guard refused this call";
    if (decision.permissionDecision === "deny") return { block: true, reason };
    if (decision.permissionDecision === "ask") {
      if (ctx.hasUI && ctx.ui?.confirm) {
        const ok = await ctx.ui.confirm("Ratchet guard", reason);
        return ok ? undefined : { block: true, reason: `The owner declined: ${reason}` };
      }
      return { block: true, reason: `${reason} No one can approve this in a session without a UI.` };
    }
    return;
  });

  pi.on("before_agent_start", async (event: any, ctx: any) => {
    const result = run(SESSION, { hook_event_name: "SessionStart", cwd: ctx.cwd ?? process.cwd() });
    const context = result.stdout.trim();
    if (!result.ok || !context) return;
    const current = typeof event.systemPrompt === "string" ? event.systemPrompt : "";
    return { systemPrompt: `${current}\n\n${context}` };
  });
}
