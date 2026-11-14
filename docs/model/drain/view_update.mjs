import * as c from "./view_common.mjs";

// Drawn from DrainLandingShown. A kill of another command is one arrow, whichever it is.
export const title = "A kill of update, and what finishes what it left";
export const state = c.leftByUpdate;
export const step = (action) => {
  if (action === "AKillMidRebaseStopsDrainPyUpdateA") return "a kill stops update mid-rebase";
  if (action === "AKillAfterTheRebaseStopsDrainPyUpdateA") return "a kill stops update after the rebase";
  return action.startsWith("AKill") ? "a kill of another command" : c.command(action);
};
