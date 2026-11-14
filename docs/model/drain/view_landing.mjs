import * as c from "./view_common.mjs";

// Drawn from DrainLeaseShown. land holds the drain's lock before merge.py runs and again for its
// close, and not while merge.py runs, so the other drain's commands can run in between.
export const title = "A landing in flight, which the other drain leaves alone";
export const state = (v) => {
  const inFlight = v.landing !== "none";
  if (v.merge === "none") return "Not landed";
  if (v.merge === "unsaid")
    return inFlight ? "Merged by merge.py, its land not yet closed" : "Merged, and no note says so";
  if (v.merge === "said")
    return inFlight ? "Recorded by the other drain, the land not yet closed" : "Recorded, not yet printed";
  return inFlight ? "Printed by the other drain, the land not yet closed" : "Merged and printed";
};
const WORDS = [
  ["DrainPyCleanAllTakeOver", "clean or clean --all"],
  ["DrainPyCleanAllDrainWithItsToken", "clean or clean --all"],
  ["DrainPyCleanAll", "clean or clean --all"],
  ["DrainPyClean", "clean or clean --all"],
  ["DrainPyReportDrainWithItsToken", "report"],
  ["DrainPyReport", "report"],
  ["DrainPyLandAUpToTheMergeMergePyLands", "land runs merge.py, which merges it"],
  ["DrainPyLandATheCloseAfterTheMerge", "land closes"],
  ["AKillStopsItsLandAfterTheMergeBeforeTheClose", "killed"],
  ["ItsSessionEnds", "killed"],
];
export const step = (action, from) => {
  const taken = c.byDrain(action);
  if (taken === null) return c.leaseNamed(action);
  const word = WORDS.find(([key]) => action.endsWith(key))?.[1] ?? taken.words;
  if (word === "killed") return "a kill stops the land, or its session ends";
  if (from.landing === "none") return `a drain's ${word}`;
  if (taken.drain === from.landing) return `its drain's ${word}`;
  const other = `the other drain's ${word}`;
  return from.processes === "one" ? `${other} [under one process]` : other;
};
