import * as c from "./view_common.mjs";

const HOLDS = ["blocked-run", "answer", "land-guard", "change-ticket"];

export const title = "The Flag: line: asked before deployment, stated before any build";
export const state = (v) => {
  if (!v.missing) return "No Flag: line is missing";
  if (v.advice === "conjecture") {
    return v.real_advice === v.advice
      ? "Missing: the route names the conjecture"
      : `Missing: the route names the conjecture, yet the real route names: ${c.NEXT[v.real_advice]}`;
  }
  if (HOLDS.includes(v.advice)) return "Missing: a held ticket is released first";
  return "Missing: other work comes first";
};
export const step = (action, from, to) =>
  state(from) === state(to) ? "the work moves on" : c.step_(action);
