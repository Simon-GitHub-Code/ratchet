import * as c from "./view_common.mjs";

// Drawn from DrainLeaseShown. land holds the drain's lock for its checks and again for its close,
// and not while merge.py runs, so every other command can run in between, before the merge
// lands and after.
export const title = "A landing in flight, before its merge lands and after";
export const state = (v) => {
  const inFlight = v.landing !== "none";
  if (v.merge === "none") {
    if (inFlight) return v.held ? "merge.py runs, the ticket held meanwhile" : "merge.py runs";
    return v.held ? "Held, not landed" : "Not landed";
  }
  if (v.merge === "unsaid") {
    if (inFlight)
      return v.held ? "Merged over a hold, its land not yet closed" : "Merged by merge.py, its land not yet closed";
    return v.held ? "Merged over a hold, and no note says so" : "Merged, and no note says so";
  }
  if (v.merge === "said")
    return inFlight ? "Recorded by another command, the land not yet closed" : "Recorded, not yet printed";
  return inFlight ? "Printed by another command, the land not yet closed" : "Merged and printed";
};
const WORDS = [
  ["DrainPyCleanAllTakeOver", "clean or clean --all"],
  ["DrainPyCleanAllDrainWithItsToken", "clean or clean --all"],
  ["DrainPyCleanAll", "clean or clean --all"],
  ["DrainPyClean", "clean or clean --all"],
  ["DrainPyReportDrainWithItsToken", "report"],
  ["DrainPyReport", "report"],
  ["DrainPyUpdateA", "update"],
  ["DrainPyCloseOutcomeParkedA", "close as parked"],
  ["DrainPyLandAUpToTheMergePyItRuns", "land runs merge.py"],
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
  if (taken.drain === from.landing) return `its drain's ${word}`;
  const other = `the other drain's ${word}`;
  return from.processes === "one" ? `${other} [under one process]` : other;
};
