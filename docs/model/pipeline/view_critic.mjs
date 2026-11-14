import * as c from "./view_common.mjs";

const ROUNDS = "The critic and its fix rounds";
const AFTER = new Set(["Critique", "Fix", "Review"]);
const CRITIC = [
  "The critic attacks the change",
  "The critic checks again after round 1",
  "The critic checks again after round 2",
];

export const title = "The critic and its fix rounds: at most two, then the review";

export const state = (v) => {
  if (v.rejudge || v.critic !== "runs") return ["Before the critic, or a run with none"];
  if (v.waits === "critique") return [ROUNDS, CRITIC[v.rounds]];
  if (v.waits === "fix") return [ROUNDS, `Fix round ${v.rounds + 1}`];
  if (v.waits === "review" || (v.outcome !== "running" && AFTER.has(v.phase)))
    return [c.where(v)];
  return ["Before the critic, or a run with none"];
};

export const step = c.drawn(state, (_action, from, to) => {
  if (from.waits === "critique" && to.waits === "fix")
    return "it finds a major it does not see fixed";
  if (from.waits === "critique" && to.waits === "review")
    return from.rounds < 2 ? "it finds nothing" : "it finds nothing, or a major is still owed";
  if (from.waits === "fix" && to.waits === "critique") return "the round is done";
  return c.answer(from, to);
});
