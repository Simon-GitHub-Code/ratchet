import * as c from "./view_common.mjs";

const HOLDS = ["blocked-run", "answer", "land-guard", "change-ticket"];

export const title = "The Flag: line: asked before deployment, stated before any build";
export const state = (v) => {
  if (!v.missing) return "No Flag: line is missing";
  if (v.advice === "conjecture") return "Missing: the route names the ticket's conjecture";
  if (v.advice === "conjecture-spec") return "Missing: the route names the spec's conjecture";
  if (HOLDS.includes(v.advice)) return "Missing: a held ticket is released first";
  return "Missing: other work comes first";
};
export const step = (action, from, to) =>
  state(from) === state(to) ? "the work moves on" : c.step_(action);
