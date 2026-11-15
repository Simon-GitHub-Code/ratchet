import * as c from "./view_common.mjs";

// Drawn from DrainLanding: a role's commit on ticket a's branch, and what the drain does with it.
export const title = "A role's commit on the branch, and where ADR 0060's publish check sits";
export const state = (v) => {
  if (v.mergedA) return "Merged";
  if (!v.publishedA) return "No role has committed";
  if (v.branchA === "draft") return "A role committed, the run returned";
  return "A role's commit judged";
};
export const step = (action) => (action.startsWith("AKill") ? "a kill of a command" : c.command(action));
