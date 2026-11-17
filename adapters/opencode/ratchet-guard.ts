import { spawnSync } from "node:child_process";

const PLUGIN_ROOT = "__RATCHET_PLUGIN_ROOT__";
const GUARD = `${PLUGIN_ROOT}/hooks/guard.py`;
const TOOLS: Record<string, string> = { bash: "Bash", edit: "Edit", write: "Write" };

type Decision = { permissionDecision?: string; permissionDecisionReason?: string };

export const RatchetGuard = async ({ directory }: { directory: string }) => ({
  "tool.execute.before": async (input: { tool: string }, output: { args: Record<string, unknown> }) => {
    const tool = TOOLS[input.tool];
    if (!tool) return;
    const toolInput = tool === "Bash" ? { command: output.args.command } : { file_path: output.args.filePath };
    const payload = { hook_event_name: "PreToolUse", tool_name: tool, tool_input: toolInput, cwd: directory };
    const result = spawnSync("python3", ["-E", "-s", GUARD], { input: JSON.stringify(payload), encoding: "utf8" });
    const stdout = (result.stdout ?? "").trim();
    if (result.status !== 0 && !stdout) {
      throw new Error(`Ratchet guard could not run: ${(result.stderr ?? "").trim()}`);
    }
    if (!stdout) return;
    let decision: Decision;
    try {
      decision = JSON.parse(stdout).hookSpecificOutput ?? {};
    } catch {
      throw new Error("Ratchet guard returned an unreadable reply");
    }
    const reason = decision.permissionDecisionReason ?? "Ratchet guard refused this call";
    if (decision.permissionDecision === "deny") throw new Error(reason);
    if (decision.permissionDecision === "ask") {
      throw new Error(`${reason} OpenCode cannot hand a call to the owner; run it yourself in your own shell.`);
    }
  },
});
