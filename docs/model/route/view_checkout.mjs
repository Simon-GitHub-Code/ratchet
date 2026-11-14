import * as c from "./view_common.mjs";
export const title = "What the route names first: unfinished work in the checkout";
export const state = (v) => c.checkout(v);
export const step = (action, from, to) =>
  state(from) === state(to) ? "the work moves on" : c.step_(action);
