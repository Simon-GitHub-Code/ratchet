import * as c from "./view_common.mjs";
export const title = "The formal model: behind is offered beside the work, a round and its ticket come first";
export const state = (v) => {
  if (v.defect) return "A defect ticket: the safeguard's row waits on it";
  if (v.upkeep === "ticket") {
    return v.advice === "formal-ticket" ? "A round's ticket: the route names it" : "A round's ticket: unfinished work comes first";
  }
  if (v.upkeep === "unstated") return "A round's ticket states no conjecture: the route names its conjecture";
  if (v.upkeep === "waiting") {
    return v.advice === "formal-round" ? "A round waits for your merge" : "A round waits: unfinished work comes first";
  }
  if (v.upkeep === "behind") {
    return v.advice === "formal-upkeep" ? "Behind: the route offers a round beside the work" : "Behind: unfinished work comes first";
  }
  return "In sync, or no model";
};
export const step = (action, from, to) =>
  state(from) === state(to) ? "the work moves on" : c.step_(action);
