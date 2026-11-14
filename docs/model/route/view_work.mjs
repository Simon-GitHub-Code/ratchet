import * as c from "./view_common.mjs";

// The drain's four holds share one release step, and the build of one ticket and the drain of
// several are one step on this chart; the page names each apart.
const COARSE = {
  ...c.NEXT,
  answer: "Release the ticket the drain parked",
  "land-guard": "Release the ticket the drain parked",
  "change-ticket": "Release the ticket the drain parked",
  safeguard: "Release the ticket the drain parked",
  build: "Build the agent-ready tickets",
  drain: "Build the agent-ready tickets",
};

export const title = "What the route names once nothing is unfinished";
export const state = (v) =>
  v.real_advice === v.advice
    ? COARSE[v.beneath]
    : `${COARSE[v.advice]}, yet the route names: ${COARSE[v.real_advice]}`;
export const step = c.step_;
