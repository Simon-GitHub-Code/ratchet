import * as c from "./view_common.mjs";

export const title = "A rebased branch judged again: reconcile if it must, then review";

export const state = (v) => {
  if (!v.rejudge) return [v.waits === "none" && v.outcome === "running" ? "Not started" : c.FIRST_BUILD];
  return v.outcome === "running" ? [c.REJUDGE, c.where(v)] : [c.where(v)];
};

export const step = c.drawn(state, (_action, from, to) => {
  if (from.waits !== "none") return c.answer(from, to);
  return to.rejudge ? c.start(to) : "the feature skill starts a build";
});
