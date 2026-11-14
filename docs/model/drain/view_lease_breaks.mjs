import * as c from "./view_common.mjs";

// Drawn from DrainLease, where the tool draws only the shortest run to each broken rule. Each run
// begins with drain 1, as the tests give it.
export const title = "Where two drains break a rule";
export const state = (v) => {
  if (v.processes === "undecided") return "Not started";
  const mode = v.processes === "one" ? "One process" : "Sessions of their own";
  const lease = v.holder === "none" ? "no lease" : `drain ${v.holder} holds the lease`;
  const stopped = v.holder !== "none" && !v[`draining${v.holder}`] ? " and has stopped" : "";
  const tree =
    v.landing !== "none"
      ? `, drain ${v.landing}'s land waits on merge.py`
      : v.tree !== "none"
        ? `, drain ${v.tree}'s tree open${v.running ? " with a program running" : ""}`
        : "";
  return `${mode}, ${lease}${stopped}${tree}`;
};
export const step = c.leaseNamed;
