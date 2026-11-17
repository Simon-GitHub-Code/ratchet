import * as c from "./view_common.mjs";
export const title = "The architecture review: when it is owed, and what settles it";
export const state = c.review;
export const step = (_action, from, to) => {
  if (from.review === to.review) return c.change(from, to);
  return to.start === "asked before the kind"
    ? "you merge a change that asks for the architecture review, before the kind ever arrived"
    : "you merge a change that asks for the architecture review, once the kind was held";
};
