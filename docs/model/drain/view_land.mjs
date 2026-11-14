import * as c from "./view_common.mjs";

// Drawn from DrainLanding. A kill of another command is one arrow, whichever it is.
export const title = "A kill of land after merge.py, and what finishes what it left";
const KILL = {
  AKillBeforeTheNoteStopsDrainPyLandA: "a kill stops land before its note",
  AKillBeforeTheDiscardStopsDrainPyLandA: "a kill stops land before its discard",
  AKillAfterTheDiscardStopsDrainPyLandA: "a kill stops land after its discard",
};
export const state = c.leftByLand;
export const step = (action) =>
  KILL[action] ?? (action.startsWith("AKill") ? "a kill of another command" : c.command(action));
