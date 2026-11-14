// The owner's words for the drain's model, shared by the charts of docs/model/drain.md.

// The model's own names for the steps of the main graph, which its tests use too.
const MAIN = {
  DrainPyOpenA: "drain.py open a",
  DrainPyOpenB: "drain.py open b",
  DrainPyOpenC: "drain.py open c",
  TheAgentLeavesWorkUncommittedInC: "the agent leaves work uncommitted in c",
  TheAgentCommitsA: "the agent commits a",
  TheAgentCommitsTheVerdictRecordOfA: "the agent commits the verdict record of a",
  DrainPyCloseOutcomeParkedA: "drain.py close --outcome parked a",
  DrainPyCloseOutcomeParkedC: "drain.py close --outcome parked c",
  DrainPyUpdateA: "drain.py update a",
  DrainPyUpdateC: "drain.py update c",
  TheOwnerEditsTicketA: "the owner edits ticket a",
  TheOwnerEditsTicketC: "the owner edits ticket c",
  DrainPyLandA: "drain.py land a",
  DrainPyLandC: "drain.py land c",
  GitLeavesAStaleIndexLockInTheTreeOfC: "git leaves a stale index lock in the tree of c",
  TheOwnerRemovesTheStaleIndexLockOfC: "the owner removes the stale index lock of c",
  AKillMidRebaseStopsDrainPyUpdateA: "a kill mid-rebase stops drain.py update a",
  AKillAfterTheRebaseStopsDrainPyUpdateA: "a kill after the rebase stops drain.py update a",
  AKillBeforeTheDiscardStopsDrainPyCloseOutcomeParkedC:
    "a kill before the discard stops drain.py close --outcome parked c",
  AKillAfterTheStashStopsDrainPyCloseOutcomeParkedC:
    "a kill after the stash stops drain.py close --outcome parked c",
  AKillBeforeTheNoteStopsDrainPyLandA: "a kill before the note stops drain.py land a",
  AKillBeforeTheDiscardStopsDrainPyLandA: "a kill before the discard stops drain.py land a",
  AKillAfterTheDiscardStopsDrainPyLandA: "a kill after the discard stops drain.py land a",
  AKillBeforeTheFastForwardStopsMergePyInDrainPyLandA:
    "a kill before the fast-forward stops merge.py in drain.py land a",
  AKillMidFastForwardStopsMergePyInDrainPyLandA:
    "a kill mid fast-forward stops merge.py in drain.py land a",
  DrainPyClean: "drain.py clean",
  DrainPyCleanAll: "drain.py clean --all",
  DrainPyReport: "drain.py report",
};

// The model's own names for a drain's steps on the lease graph, without the drain's number.
const LEASE = {
  DrainPyCleanAll: "drain.py clean --all",
  DrainPyCleanAllDrainWithItsToken: "drain.py clean --all --drain with its token",
  DrainPyCleanAllTakeOver: "drain.py clean --all --take-over",
  DrainPyOpenA: "drain.py open a",
  ItsAgentLeavesADraftInItsTree: "its agent leaves a draft in its tree",
  ItsAgentStartsAProgramBuiltInItsTree: "its agent starts a program built in its tree",
  DrainPyReport: "drain.py report",
  DrainPyReportDrainWithItsToken: "drain.py report --drain with its token",
  DrainPyClean: "drain.py clean",
  DrainPyLandAUpToTheMergeMergePyLands: "drain.py land a, up to the merge merge.py lands",
  DrainPyLandATheCloseAfterTheMerge: "drain.py land a, the close after the merge",
  AKillStopsItsLandAfterTheMergeBeforeTheClose:
    "a kill stops its land after the merge, before the close",
  StopsItsSessionStillOpen: "stops, its session still open",
  ItsSessionEnds: "its session ends",
};
const LEASE_WHOLE = {
  EachDrainRunsInASessionOfItsOwn: "each drain runs in a session of its own",
  OneLongLivedProcessRunsBothDrains: "one long-lived process runs both drains",
  TheProgramBuiltInTheTreeEnds: "the program built in the tree ends",
};

export const named = (action) => MAIN[action];

// A lease step: the drain that takes it, as a number, and its words without the number.
export const byDrain = (action) => {
  const found = /^Drain([12])(\w+)$/.exec(action);
  return found ? { drain: found[1], words: LEASE[found[2]] } : null;
};
// Why: Mermaid 11 reads a second colon in an arrow's label as an error, so a comma follows the drain.
export const leaseNamed = (action) => {
  const step = byDrain(action);
  return step ? `drain ${step.drain}, ${step.words}` : LEASE_WHOLE[action];
};

// What a chart calls a command of the drain, or a step of the agent, the owner or git.
const COMMAND = {
  DrainPyOpen: "open",
  DrainPyCloseOutcomeParked: "close as parked",
  DrainPyUpdate: "update",
  DrainPyLand: "land",
  DrainPyClean: "clean",
  DrainPyCleanAll: "clean --all",
  DrainPyReport: "report",
  TheAgentCommits: "the agent commits",
  TheAgentCommitsTheVerdictRecordOf: "the agent commits the verdict record",
  TheAgentLeavesWorkUncommittedIn: "the agent leaves work uncommitted",
  TheOwnerEditsTicket: "the owner edits the ticket",
  GitLeavesAStaleIndexLockInTheTreeOf: "git leaves a stale index lock",
  TheOwnerRemovesTheStaleIndexLockOf: "the owner removes the lock",
};
export const command = (action) => COMMAND[action] ?? COMMAND[action.replace(/[ABC]$/, "")];

// What a kill of update, land or the merge.py it runs leaves of ticket a. A fast-forward cut
// off half-way leaves merge.py's scratch tree too, and is named for the cut.
export const leftByUpdate = (v) => {
  if (v.treeA === "rebasing") return "A rebase cut off, the tree detached";
  if (v.rebasingA && v.branchA === "stale") return "Rebased, the old verdict record still last";
  return "No rebase left over";
};
export const leftByLand = (v) => {
  if (v.mergedA && v.noteA !== "merged") return "Merged, and no note says so";
  if (v.discardingA && v.treeA !== "none") return "The note written, the tree still there";
  if (v.discardingA) return "The tree gone, the note still marked";
  return "Nothing of a landing left over";
};
export const leftByMerge = (v) => {
  if (v.half !== "none") return "A fast-forward cut off half-way";
  if (v.scratch) return "The scratch tree of merge.py left";
  return "Nothing of merge.py left over";
};

// One ticket's life, as the plan and the note call ticket a.
export const life = (v) => {
  if (v.mergedA) return v.freshA ? "Merged, the next report prints it" : "Merged and reported";
  if (v.statusA === "held") return "Held until its text changes";
  if (v.statusA === "buildable") return "Buildable";
  if (v.branchA === "bare") return "Opened, nothing committed";
  if (v.branchA !== "judged") return "Committed, no verdict record last";
  return v.currentA ? "Judged on the tip" : "Judged on an older tip";
};
