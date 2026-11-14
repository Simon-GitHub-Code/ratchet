// The owner's words for the safeguard's model, shared by the charts of docs/model/safeguard.md.

// The model's own names for its steps, which its tests use too.
const STEP = {
  ForkAddOneInstance: "fork: add one instance",
  ForkAddSeveralInstances: "fork: add several instances",
  ForkMoveAnInstance: "fork: move an instance",
  ForkRemoveSomeInstances: "fork: remove some instances",
  ForkRemoveEveryInstance: "fork: remove every instance",
  ForkBindTheRow: "fork: bind the row",
  LandTheBranch: "land the branch",
  AbandonTheBranch: "abandon the branch",
  AnotherBranchLandsAddOneInstance: "another branch lands: add one instance",
  AnotherBranchLandsAddSeveralInstances: "another branch lands: add several instances",
  AnotherBranchLandsMoveAnInstance: "another branch lands: move an instance",
  AnotherBranchLandsRemoveSomeInstances: "another branch lands: remove some instances",
  AnotherBranchLandsRemoveEveryInstance: "another branch lands: remove every instance",
  AnotherBranchLandsBindTheRow: "another branch lands: bind the row",
  AnotherBranchLandsPutTheBoundRowBackToLater:
    "another branch lands: put the bound row back to later",
  AnotherBranchLandsDropTheRow: "another branch lands: drop the row",
  AnotherBranchLandsPutTheDroppedRowBackAsLater:
    "another branch lands: put the dropped row back as later",
  AnotherBranchLandsPointTheTriggersRowElsewhere:
    "another branch lands: point the Triggers row elsewhere",
  AnotherBranchLandsDeclareTheEvent: "another branch lands: declare the event",
  AnotherBranchLandsBindTheEventSRow: "another branch lands: bind the event's row",
  AnotherBranchLandsAskForTheArchitectureReview:
    "another branch lands: ask for the architecture review",
  AnotherBranchLandsLandTheArchitectureReview:
    "another branch lands: land the architecture review",
  AnotherBranchLandsAddOneInstanceWithItsReview:
    "another branch lands: add one instance with its review",
};
export const named = (action) => STEP[action];

// The kind on the default branch, and what the gate says of a change that adds an instance.
export const kind = (v) => {
  if (v.row === "later" && v.allowance >= 0 && v.instances > v.allowance)
    return "Owed, and grown past its arrival unbound";
  if (v.table === "blind") return "The Triggers row points elsewhere: the gate sees no kind";
  if (v.row === "absent") return "The row is dropped: the row owes nothing";
  if (v.row === "bound") return "Bound: a new instance is green unless the review is owed";
  if (v.instances > 0) return "Arrived, and owed: a new instance is red";
  if (v.held) return "Gone back to none: a return is red";
  return "No kind yet: the first change brings it free";
};

const ROW = {
  "later bound": "binds the row",
  "bound later": "puts the bound row back to later",
  "absent later": "puts the dropped row back",
};

// The architecture review on the default branch. A review that lands, and a kind held when the
// binding first asked, are one state in the model: the gate reads neither again.
export const review = (v) => {
  if (v.review === "absent") return "Not asked: no review is owed";
  if (v.start !== "asked before the kind") return "Settled: the rows alone judge the kind";
  if (v.table === "blind")
    return "Asked, and the Triggers row points elsewhere: the gate sees no kind";
  if (v.instances > 0) return "Owed: another instance is red until the review lands";
  if (v.held) return "Asked, and the kind gone back to none: a return is red";
  return "Asked before the kind: its arrival is free";
};

// Whether a change landed the review's decision record: the review settled, its row unchanged.
const recorded = (from, to) => from.review === to.review && from.start !== to.start;

// What a green change did to the default branch, whichever branch it came from. A change to the
// binding or a decision record is on a guard path, so it lands only when you merge it.
export const change = (from, to) => {
  const bound = [];
  if (recorded(from, to))
    bound.push(
      to.instances > from.instances
        ? "adds an instance with its review"
        : "lands the architecture review",
    );
  if (to.row === "absent" && from.row !== "absent") bound.push("drops the row");
  else if (from.row !== to.row) bound.push(ROW[`${from.row} ${to.row}`]);
  if (from.table !== to.table) bound.push("points the Triggers row elsewhere");
  if (from.event !== to.event) bound.push("declares the event");
  if (from.event_row !== to.event_row) bound.push("binds the event's row");
  if (from.review !== to.review) bound.push("asks for the architecture review");
  if (bound.length) return `you merge a change that ${bound.join(" and ")}`;
  if (to.instances > from.instances) return "a change adds instances";
  if (to.instances === 0 && from.instances > 0) return "a change removes every instance";
  return to.instances < from.instances ? "a change removes some instances" : "a change lands";
};
