import * as c from "./view_common.mjs";

// The drain's four holds share one release step, and the build of one ticket and the drain of
// several are one step on this chart; the page names each apart. So do the steps the real route
// names where an adoption stopped after its pin.
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
export const state = (v) => {
  if (v.real_advice === v.advice) return COARSE[v.beneath];
  if (v.advice === "adopt") return "Run /ratchet:adopt, yet the route names what follows it";
  return `${COARSE[v.advice]}, yet the route names: ${COARSE[v.real_advice]}`;
};
export const step = c.step_;
