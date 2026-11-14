import * as c from "./view_common.mjs";

// Drawn from DrainLeaseShown. land holds the drain's lock for its checks and again for its close,
// and not while merge.py runs, so every other command can run in between. A command on the
// ticket is refused there and changes nothing, so it stays in one state and is not drawn. A
// merge.py whose land was killed before its probe can land nothing, so it is drawn in the next
// chart and not here.
export const title = "A landing in flight, before its merge lands and after";
export const state = (v) => {
  const inFlight = v.landing !== "none";
  if (v.merge === "none") {
    if (v.orphan !== "none") return "Its land killed, its merge.py runs on";
    return inFlight ? "Its land runs merge.py" : "Not landed";
  }
  if (v.merge === "unsaid") return inFlight ? "Merged, its land not yet closed" : "Merged, and no note says so";
  // Why: rule 9h says no other command records a live landing's merge; a chart that drew it
  // would show these two states.
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
  // Why: clean, clean --all, update and close as parked each record a merge no note says, and
  // with a live landing each leaves it alone, so the chart names them once.
  if (word === "records") return "a drain's clean, clean --all, update or close as parked";
  if (word === "killed") return "a kill stops the land, or its session ends";
  if (word === "alone") return "a kill stops the land alone past the probe";
  if (word === "orphan merges") return "its merge.py merges it";
  if (word === "orphan ends") return "its merge.py is killed";
  if (from.landing === "none") return `a drain's ${word}`;
  return taken.drain === from.landing ? `its ${word}` : `the other drain's ${word}`;
};
