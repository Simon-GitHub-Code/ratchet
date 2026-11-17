import * as c from "./view_common.mjs";

const COARSE = {
  ...c.NEXT,
  answer: "Release the ticket the drain parked",
  "land-guard": "Release the ticket the drain parked",
  "change-ticket": "Release the ticket the drain parked",
  safeguard: "Release the ticket the drain parked",
  release: "Release the ticket the drain parked",
  build: "Build the agent-ready tickets",
  drain: "Build the agent-ready tickets",
};

export const title = "What the route names once nothing is unfinished";
export const state = (v) => {
  if (!(v.advice in COARSE) || COARSE[v.real_advice] === COARSE[v.advice]) return COARSE[v.beneath];
  if (v.advice === "adopt") return "Run /ratchet:adopt, yet the route names what follows it";
  return `${COARSE[v.advice]}, yet the route names: ${COARSE[v.real_advice]}`;
};
export const step = c.step_;
