import * as c from "./view_common.mjs";
export const title = "The architecture: asked at adoption, owed after an upgrade, never instead of the work";
export const state = (v) => {
  if (["init", "tracker", "adopt"].includes(v.advice)) return "Not adopted yet";
  if (v.architecture === "unasked") return "Never asked: an older plugin adopted";
  if (v.architecture === "recorded") return "Recorded";
  if (v.advice === "architecture") return "Owed: the route names it";
  if (v.advice === "fill") return "Owed: the rows to fill come first";
  if (v.advice === "safeguard") return "Owed: a safeguard comes first";
  return "Owed: unfinished work comes first";
};
export const step = (action, from, to) =>
  state(from) === state(to) ? "the work moves on" : c.step_(action);
