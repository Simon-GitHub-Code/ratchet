import * as c from "./view_common.mjs";

export const title = "The drain lease: who holds it, and when the next drain may take it";
const MODE = { separate: "Each drain in a session of its own", one: "One process runs both drains" };
const HOLDER = {
  none: "No lease",
  draining: "Its holder drains",
  stopped: "Its holder stopped, its session open",
  gone: "Its holder is gone",
};
export const state = (v) => {
  if (v.processes === "undecided") return "Not started";
  return [MODE[v.processes], HOLDER[v.holderIs]];
};
const WORDS = {
  DrainPyCleanAll: "clean --all",
  DrainPyCleanAllDrainWithItsToken: "clean --all",
  DrainPyCleanAllTakeOver: "clean --all --take-over",
  DrainPyReport: "report",
  DrainPyReportDrainWithItsToken: "report",
  StopsItsSessionStillOpen: "stop",
};
export const step = (action, from, to) => {
  if (action === "EachDrainRunsInASessionOfItsOwn") return "each drain starts in a session of its own";
  if (action === "OneLongLivedProcessRunsBothDrains") return "one process starts both drains";
  const taken = c.byDrain(action);
  if (taken === null) return c.leaseNamed(action);
  if (taken.words === "its session ends")
    return from.processes === "one" ? "the process ends" : "the holder's session ends";
  const who =
    from.holder === "none" ? "a drain" : taken.drain === from.holder ? "the holder" : "the other drain";
  const found = Object.entries(WORDS).find(([key]) => action.endsWith(key))?.[1] ?? taken.words;
  const alive = from.processes === "separate" && ["draining", "stopped"].includes(from.holderIs);
  const word = found === "clean --all --take-over" && !(alive && who === "the other drain") ? "clean --all" : found;
  if (word === "stop") return `${who} stops`;
  if (word === "report" && to.holder !== "none") return `${who}'s report while a program runs`;
  return `${who}'s ${word}`;
};
