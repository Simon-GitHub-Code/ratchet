import * as c from "./view_common.mjs";
export const title = "Fills, safeguards and reviews: named before the next feature, never instead of it";
export const state = (v) => {
  if (v.fills) {
    return v.advice === "fill" ? "Rows to fill: the route names it" : "Rows to fill: unfinished work comes first";
  }
  if (v.reviews) {
    return v.advice === "review" ? "Review owed: the route names it" : "Review owed: unfinished work comes first";
  }
  if (!v.owes) return "None owed";
  return v.advice === "safeguard" ? "Owed: the route names it" : "Owed: unfinished work comes first";
};
export const step = (action, from, to) =>
  state(from) === state(to) ? "the work moves on" : c.step_(action);
