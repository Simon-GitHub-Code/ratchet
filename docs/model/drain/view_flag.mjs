import * as c from "./view_common.mjs";

// Drawn from DrainTicketLife: ticket a, the one ticket with no Flag: line, across the upgrade
// that ships ADR 0061, and b, which waits on it.
export const title = "The upgrade, a ticket with no Flag line, and the ticket that waits on it";
export const state = (v) => {
  if (v.mergedA || v.branchA !== "none" && v.branchA !== "bare") return "Built, so the preflight reads it no more";
  if (v.statusA === "held") return "Held until its text changes";
  if (v.statusA === "refused") return "Refused for its Flag line, b refused with it";
  if (v.treeA !== "none") return "Opened";
  return v.flag === "landed" ? "Buildable, its line landed" : "Buildable, no line asked";
};
export const step = (action) => {
  if (action === "AnUpgradeMakesThePreflightAskTheFlagLineBeforeDeployment") return "an upgrade asks the Flag line";
  if (action === "TheOwnerLandsTheFlagLineOfA") return "the owner lands its Flag line";
  return c.command(action);
};
