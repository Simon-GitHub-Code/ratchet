import * as c from "./view_common.mjs";

// Drawn from DrainLeaseShown. land holds the drain's lock for its checks and again for its close,
// and not while merge.py runs, so every other command can run in between. A command on the
// ticket is refused there and changes nothing, so it stays in one state and is not drawn.
export const title = "A landing in flight, before its merge lands and after";
export const state = (v) => {
  const inFlight = v.landing !== "none";
  if (v.merge === "none") return inFlight ? "Its land runs merge.py" : "Not landed";
  if (v.merge === "unsaid") return inFlight ? "Merged, its land not yet closed" : "Merged, and no note says so";
  // Why: rule 9h says no other command records a live landing's merge; a chart that drew it
  // would show these two states.
  if (v.merge === "said") return inFlight ? "Recorded by another command" : "Recorded, not yet printed";
  return inFlight ? "Printed by another command" : "Merged and printed";
};
const WORDS = [
  ["DrainPyCleanAllTakeOver", "clean or clean --all"],
  ["DrainPyCleanAllDrainWithItsToken", "clean or clean --all"],
  ["DrainPyCleanAll", "clean or clean --all"],
  ["DrainPyClean", "clean or clean --all"],
  ["DrainPyReportDrainWithItsToken", "report"],
  ["DrainPyReport", "report"],
  ["DrainPyLandAUpToTheMergePyItRuns", "land"],
  ["ItsMergePyLandsTheMerge", "merge.py merges it"],
  ["DrainPyLandATheCloseAfterTheMerge", "land closes"],
  ["AKillStopsItsLandAndItsMergePyBeforeTheMerge", "killed"],
  ["AKillStopsItsLandAfterTheMergeBeforeTheClose", "killed"],
  ["ItsSessionEnds", "killed"],
];
export const step = (action, from) => {
  const taken = c.byDrain(action);
  if (taken === null) return c.leaseNamed(action);
  const word = WORDS.find(([key]) => action.endsWith(key))?.[1] ?? taken.words;
  if (word === "killed") return "a kill stops the land, or its session ends";
  if (from.landing === "none") return `a drain's ${word}`;
  return taken.drain === from.landing ? `its ${word}` : `the other drain's ${word}`;
};
