import * as c from "./view_common.mjs";

export const title = "A landing in flight, before its merge lands and after";
export const state = (v) => {
  const inFlight = v.landing !== "none";
  if (v.merge === "none") {
    if (v.orphan !== "none") return "Its land killed, its merge.py runs on";
    return inFlight ? "Its land runs merge.py" : "Not landed";
  }
  if (v.merge === "unsaid") return inFlight ? "Merged, its land not yet closed" : "Merged, and no note says so";
  if (v.merge === "said") return inFlight ? "Recorded by another command" : "Recorded, not yet printed";
  return inFlight ? "Printed by another command" : "Merged and printed";
};
const WORDS = [
  ["DrainPyCleanAllTakeOver", "records"],
  ["DrainPyCleanAllDrainWithItsToken", "records"],
  ["DrainPyCleanAll", "records"],
  ["DrainPyClean", "records"],
  ["DrainPyReportDrainWithItsToken", "report"],
  ["DrainPyReport", "report"],
  ["DrainPyUpdateA", "records"],
  ["DrainPyCloseOutcomeParkedA", "records"],
  ["DrainPyLandAUpToTheMergePyItRuns", "land"],
  ["ItsMergePyLandsTheMerge", "merge.py merges it"],
  ["DrainPyLandATheCloseAfterTheMerge", "land closes"],
  ["AKillStopsItsLandAndItsMergePyBeforeTheMerge", "killed"],
  ["AKillStopsItsLandAloneBeforeItsMergePyProbesIt", "killed"],
  ["ItsMergePyMeetsTheMergeLockOfAMergeWhoseLandIsGoneAndBothStop", "killed"],
  ["AKillStopsItsLandAfterTheMergeBeforeTheClose", "killed"],
  ["ItsSessionEnds", "killed"],
  ["AKillStopsItsLandAloneOnceItsMergePyHasProbedItTheMergePyRunningOn", "alone"],
  ["TheMergePyItsKilledLandStartedLandsTheMerge", "orphan merges"],
  ["TheMergePyItsKilledLandStartedEndsAndLandsNothing", "orphan ends"],
];
export const step = (action, from) => {
  const taken = c.byDrain(action);
  if (taken === null) return c.leaseNamed(action);
  const word = WORDS.find(([key]) => action.endsWith(key))?.[1] ?? taken.words;
  if (word === "records") return "a drain's clean, clean --all, update or close as parked";
  if (word === "killed") return "a kill stops the land, or its session ends";
  if (word === "alone") return "a kill stops the land alone past the probe";
  if (word === "orphan merges") return "its merge.py merges it";
  if (word === "orphan ends") return "its merge.py is killed";
  if (from.landing === "none") return `a drain's ${word}`;
  return taken.drain === from.landing ? `its ${word}` : `the other drain's ${word}`;
};
