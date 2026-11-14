import * as c from "./view_common.mjs";

// Drawn from DrainLandingShown. A kill of another command is one arrow, whichever it is.
export const title = "A kill of the merge.py that land runs, and what puts it back";
const KILL = {
  AKillBeforeTheFastForwardStopsMergePyInDrainPyLandA: "a kill stops merge.py before the fast-forward",
  AKillMidFastForwardStopsMergePyInDrainPyLandA: "a kill stops merge.py mid fast-forward",
};
export const state = c.leftByMerge;
export const step = (action) =>
  KILL[action] ?? (action.startsWith("AKill") ? "a kill of another command" : c.command(action));
