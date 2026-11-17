import * as c from "./view_common.mjs";
export const title = "What the route names first: unfinished work in the checkout";
const CHECKOUT = ["finish-records", "land-records", "commit", "switch", "merge", "merge-records"];
export const state = (v) => {
  const here = c.checkout(v);
  if (!CHECKOUT.includes(v.advice) || v.real_advice === v.advice) return here;
  return v.parked ? `${here}, yet the route passes it by` : `${here}, yet the route names a commit`;
};
export const step = (action, from, to) =>
  state(from) === state(to) ? "the work moves on" : c.step_(action);
