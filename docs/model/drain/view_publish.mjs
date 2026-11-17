import * as c from "./view_common.mjs";

export const title = "A role's commit on the branch, and the publish check of ADR 0060";
export const state = (v) => {
  if (v.mergedA) return "Merged";
  if (!v.publishedA) return v.passedA ? "No role has committed, the check passed" : "No role has committed";
  if (v.statusA === "held") return "Held, a role's commit on it";
  if (v.treeA === "none") return "A role committed, its tree gone";
  if (v.branchA === "judged") return "A role's commit judged, unchecked";
  return "A role committed, the run returned";
};
export const step = (action) => c.command(action);
