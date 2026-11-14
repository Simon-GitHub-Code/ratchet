import * as c from "./view_common.mjs";

// The critic and its fix rounds are one state here; view_critic.mjs draws the rounds.
const CRITIC = "The critic and its fix rounds";
const LOOP = new Set(["critique", "fix"]);

export const title = "A first build: each stage, and each way it ends";

export const state = (v) => {
  if (v.rejudge) return [c.REJUDGE];
  if (LOOP.has(v.waits)) return [c.FIRST_BUILD, CRITIC];
  const where = c.where(v);
  return v.outcome === "running" && v.waits !== "none" ? [c.FIRST_BUILD, where] : [where];
};

export const step = c.drawn(state, (_action, from, to) => {
  if (from.waits === "none") return to.rejudge ? "it judges a rebased branch again" : c.start(to);
  if (!LOOP.has(from.waits) || to.outcome === "error") return c.answer(from, to);
  if (to.waits === "review") return "the critic finds nothing, or two rounds are spent";
  return "a fix round measures the change over a ceiling";
});
