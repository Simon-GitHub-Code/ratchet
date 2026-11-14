import * as c from "./view_common.mjs";

// Drawn from DrainLease, where the tool draws only the shortest run to each broken rule. Each run
// begins with drain 1, as the tests give it.
export const title = "Where two drains break a rule";
export const state = (v) => {
  if (v.processes === "undecided") return "Not started";
  const mode = v.processes === "one" ? "One process" : "Sessions of their own";
  const lease = v.holder === "none" ? "no lease" : `drain ${v.holder} holds the lease`;
  const stopped = v.holder !== "none" && !v[`draining${v.holder}`] ? " and has stopped" : "";
  const open = v.tree !== "none" ? `, drain ${v.tree}'s tree open${v.running ? " with a program running" : ""}` : "";
  const merging = v.merge === "none" ? "merge.py runs" : "land waits to close";
  const tree = v.landing !== "none" ? `, drain ${v.landing}'s ${merging}${v.tree === "none" ? ", its tree gone" : ""}` : open;
  const held = v.held ? ", the ticket held" : "";
  return `${mode}, ${lease}${stopped}${tree}${held}`;
};
export const step = c.leaseNamed;
