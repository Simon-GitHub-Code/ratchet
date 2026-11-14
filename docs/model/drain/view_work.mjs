import * as c from "./view_common.mjs";

// Drawn from DrainWork, whole: ticket c's tree, where the agent leaves work uncommitted.
export const title = "Uncommitted work in a ticket's tree, and a kill of close";
export const state = (v) => {
  if (v.treeC !== "none" && v.discardingC) return "Parked, its tree not yet removed";
  if (v.treeC === "locked") return "Work uncommitted behind a stale lock";
  if (v.treeC === "dirty") return "Work uncommitted in its tree";
  if (v.treeC === "clean") return "A clean tree";
  return "No tree";
};
const KILL = {
  AKillBeforeTheDiscardStopsDrainPyCloseOutcomeParkedC: "a kill stops close before its discard",
  AKillAfterTheStashStopsDrainPyCloseOutcomeParkedC: "a kill stops close after the stash",
};
export const step = (action) => KILL[action] ?? c.command(action);
