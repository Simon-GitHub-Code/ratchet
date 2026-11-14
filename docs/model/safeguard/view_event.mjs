import * as c from "./view_common.mjs";
export const title = "The life event: declared, it owes its row and is never red";
export const state = (v) => {
  if (v.event === "undeclared") return "No event declared";
  return v.event_row === "bound" ? "Its row is bound" : "Declared: its row is owed";
};
export const step = (_action, from, to) => c.change(from, to);
