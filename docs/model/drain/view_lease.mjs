import * as c from "./view_common.mjs";

// Drawn from DrainLeaseShown. The two drains are alike, so a step is named by its drain's part:
// the lease's holder, or the other drain.
export const title = "The drain lease: who holds it, and when the next drain may take it";
const MODE = { separate: "Each drain in a session of its own", one: "One process runs both drains" };
export const state = (v) => {
  if (v.processes === "undecided") return "Not started";
  const holder = v.holder;
  if (holder === "none") return [MODE[v.processes], "No lease"];
  if (v[`draining${holder}`]) return [MODE[v.processes], "Its holder drains"];
  if (v[`alive${holder}`]) return [MODE[v.processes], "Its holder stopped, its session open"];
  return [MODE[v.processes], "Its holder is gone"];
};
const WORDS = {
  DrainPyCleanAll: "clean --all",
  DrainPyCleanAllDrainWithItsToken: "clean --all",
  DrainPyCleanAllTakeOver: "clean --all --take-over",
  DrainPyReport: "report",
  DrainPyReportDrainWithItsToken: "report",
  StopsItsSessionStillOpen: "stop",
};
export const step = (action, from) => {
  if (action === "EachDrainRunsInASessionOfItsOwn") return "each drain starts in a session of its own";
  if (action === "OneLongLivedProcessRunsBothDrains") return "one process starts both drains";
  const taken = c.byDrain(action);
  if (taken === null) return c.leaseNamed(action);
  if (taken.words === "its session ends")
    return from.processes === "one" ? "the process ends" : "the holder's session ends";
  const who =
    from.holder === "none" ? "a drain" : taken.drain === from.holder ? "the holder" : "the other drain";
  const word = Object.entries(WORDS).find(([key]) => action.endsWith(key))?.[1] ?? taken.words;
  return word === "stop" ? `${who} stops` : `${who}'s ${word}`;
};
