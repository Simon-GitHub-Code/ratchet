export const title = "A unit two tickets share: the last to land deletes it";

export const state = (v) => {
  if (!v.unit) return "Gone from the default branch, its lines in the brief";
  if (v.landed === 0) return "On the default branch, neither ticket landed";
  if (!v.deletes) return "One ticket landed, the other's branch keeps the unit";
  if (v.short) return "Deleted on the last ticket's branch, the owner's new line not on it";
  return "Deleted on the last ticket's branch, its lines in the brief there";
};

export const step = (action, from, to) => {
  if (action.startsWith("DrainPyLand") || action.startsWith("AKillBeforeTheNote")) {
    return "a ticket lands";
  }
  if (action.startsWith("DrainPyUpdate")) {
    return from.short ? "update takes the edit and runs expire" : "update runs expire";
  }
  if (action.startsWith("DocumentsPyExpire")) return "expire, on a branch built after the landing";
  if (action === "TheOwnerLandsAnAnswersLineOnATicketOfTheUnit") {
    return "the owner lands an Answers line";
  }
  return null;
};
