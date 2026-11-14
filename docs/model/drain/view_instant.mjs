import * as c from "./view_common.mjs";

// Drawn from DrainLeaseShown. A kill of land alone: before its merge.py probes the land's private
// lock, or after. Before, no lock is held and the merge.py can only stop; after, the merge lock
// keeps it seen until it ends. Once the ticket is merged this chart says no more.
export const title = "A land killed alone, before its merge.py probes it and after";
export const state = (v) => {
  if (v.merge !== "none") return "Merged";
  if (v.orphan !== "none") return "Killed past the probe, its merge.py runs on";
  if (v.landing !== "none")
    return v.doomed ? "A land runs merge.py, the killed land's merge.py starting" : "Its land runs merge.py";
  return v.doomed ? "Killed before the probe, its merge.py starting" : "Not landed";
};
const WORDS = [
  ["DrainPyLandAUpToTheMergePyItRuns", "land"],
  ["ItsMergePyLandsTheMerge", "its merge.py merges it"],
  ["AKillStopsItsLandAndItsMergePyBeforeTheMerge", "a kill stops the land and its merge.py, or its session ends"],
  ["ItsSessionEnds", "a kill stops the land and its merge.py, or its session ends"],
  ["AKillStopsItsLandAloneBeforeItsMergePyProbesIt", "a kill stops the land alone before the probe"],
  ["AKillStopsItsLandAloneOnceItsMergePyHasProbedItTheMergePyRunningOn", "a kill stops the land alone past the probe"],
  ["ItsMergePyMeetsTheMergeLockOfAMergeWhoseLandIsGoneAndBothStop", "its merge.py meets the lock of the killed land's merge.py, and both stop"],
  ["TheMergePyItsKilledLandStartedLandsTheMerge", "its merge.py merges it"],
  ["TheMergePyItsKilledLandStartedEndsAndLandsNothing", "its merge.py is killed"],
];
export const step = (action) => {
  if (action === "TheMergePyOfALandKilledBeforeItsProbeFindsTheLandGoneAndStops")
    return "the killed land's merge.py finds it gone and stops";
  const taken = c.byDrain(action);
  if (taken === null) return c.leaseNamed(action);
  const word = WORDS.find(([key]) => action.endsWith(key))?.[1];
  if (word === undefined) return taken.words;
  return word === "land" ? "a drain's land" : word;
};
