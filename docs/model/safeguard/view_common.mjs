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
};
export const named = (action) => STEP[action];

// The kind on the default branch, and what the gate says of a change that adds an instance.
export const kind = (v) => {
  if (v.row === "later" && v.allowance >= 0 && v.instances > v.allowance)
    return "Owed, and grown past its arrival unbound";
  if (v.table === "blind") return "The Triggers row points elsewhere: the gate sees no kind";
  if (v.row === "absent") return "The row is dropped: nothing is owed";
  if (v.row === "bound") return "Bound: a new instance is green";
  if (v.instances > 0) return "Arrived, and owed: a new instance is red";
  if (v.held) return "Gone back to none: a return is red";
  return "No kind yet: the first change brings it free";
};

const ROW = {
  "later bound": "binds the row",
  "bound later": "puts the bound row back to later",
  "absent later": "puts the dropped row back",
};

// What a green change did to the default branch, whichever branch it came from. A change to the
// binding is on a guard path, so it lands only when you merge it.
export const change = (from, to) => {
  const bound = [];
  if (to.row === "absent" && from.row !== "absent") bound.push("drops the row");
  else if (from.row !== to.row) bound.push(ROW[`${from.row} ${to.row}`]);
  if (from.table !== to.table) bound.push("points the Triggers row elsewhere");
  if (from.event !== to.event) bound.push("declares the event");
  if (from.event_row !== to.event_row) bound.push("binds the event's row");
  if (bound.length) return `you merge a change that ${bound.join(" and ")}`;
  if (to.instances > from.instances) return "a change adds instances";
  if (to.instances === 0 && from.instances > 0) return "a change removes every instance";
  return to.instances < from.instances ? "a change removes some instances" : "a change lands";
};
