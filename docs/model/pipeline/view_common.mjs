// The owner's words for one pipeline run, shared by the charts of docs/model/pipeline.md.

// Why: Mermaid 11 reads a second colon in a state's words as an error, so no label holds one.
export const FIRST_BUILD = "A first build";
export const REJUDGE = "Judging a rebased branch again";

const AGENT = {
  preflight: "The preflight checks the ticket is ready",
  spec: "The spec writes tests that fail, and measures the size",
  "ticket-check": "The ticket check reads the spec for every promise",
  "spec-amend": "The spec is amended to the gaps",
  "ticket-recheck": "The ticket check, once more",
  implement: "The build",
  critique: "The critic attacks the change",
  fix: "A fix round",
  review: "The review",
  reconcile: "The reconcile finishes the rebase and turns the check green",
  "review-again": "The review, once more",
};
// Why: before the project is depended-on the review hands its blockers to one fix pass, and a
// second review judges what the pass left.
export const PASS = "The fix pass on the review's blockers";
export const SECOND_REVIEW = "The review, after the fix pass";
export const agent = (v) => {
  if (v.critic !== "waits") return AGENT[v.waits];
  if (v.waits === "fix") return PASS;
  return v.waits === "review" && v.rounds > 0 ? SECOND_REVIEW : AGENT[v.waits];
};

const STOP = {
  "no ticket was passed": "Stopped, no ticket or the ticket is not ready",
  "the preflight is red": "Stopped, no ticket or the ticket is not ready",
  "a question for the owner": "Stopped for you, a question or a guard path to change",
  "a guard path the owner must change": "Stopped for you, a question or a guard path to change",
  "a departure from the spec the owner decides": "Stopped for you, a question or a guard path to change",
  "a question for the owner after a role that builds or judges":
    "Stopped for you, a question or a guard path to change",
  "too large": "Stopped too large, split the ticket",
};

const CARRIED =
  "a critic finding still owed after the fix round it was given, parked with no review";

const DIED = "Stopped on an error, resume the run";
const MISSING = "Stopped on an error, an input is missing";

// How a run ended, or the agent it waits on.
export const where = (v) => {
  if (v.outcome === "running") return v.waits === "none" ? "Not started" : agent(v);
  if (v.outcome === "approve") return "Approved";
  if (v.outcome === "request_changes") return "Changes requested";
  if (v.outcome === "blocked") return STOP[v.stop];
  return v.resume ? DIED : MISSING;
};

// The agent's answer that moved the run between two of those.
export const answer = (from, to) => {
  const now = where(to);
  if (now === DIED) return "the agent dies";
  if (now === MISSING) return "it is ready but names no slug";
  if (to.stop === "a departure from the spec the owner decides")
    return "it departs from a criterion, or from an assumption that is yours";
  if (to.stop === "a question for the owner after a role that builds or judges")
    return from.waits === "critique"
      ? "it finds a case no record states, which is yours"
      : "it finds a case that is yours, or leaves you a finding no quote holds";
  if (now.startsWith("Stopped for you")) return "it asks you, or needs a guard path changed";
  if (now.startsWith("Stopped too large"))
    return from.waits.startsWith("spec")
      ? "it measures or estimates the change over a ceiling"
      : "it measures the change over a ceiling";
  if (to.stop === CARRIED) return "it finds again the major it was fixed for, and no review runs";
  if (now === "Approved") return "it approves, and nothing stands against the change";
  if (now === "Changes requested") return "it requests changes, or something stands against the change";
  const ANSWER = {
    "preflight spec": "the ticket is ready",
    "preflight none": "the ticket is not ready",
    "spec ticket-check": "it asks nothing and fits",
    "spec-amend ticket-recheck": "it asks nothing and fits",
    "ticket-check implement": "it finds no gap",
    "ticket-check spec-amend": "it finds a gap",
    "ticket-recheck implement": "it finds no gap, or one the run keeps as an assumption",
    "implement critique": "the change is built [the project is depended-on]",
    "implement review": "the change is built [nobody depends on the project yet]",
    "critique fix": "it finds a major it does not see fixed [fewer than two rounds run]",
    "critique review": "it finds nothing, or two rounds are spent",
    "fix critique": "the round is done",
    "review fix": "it finds a blocker in the change [nobody depends on the project yet]",
    "fix review": "the pass is done",
    "reconcile review-again": "the rebase is finished",
  };
  return ANSWER[`${from.waits} ${to.waits}`];
};

const START = {
  preflight: "the feature skill starts a build",
  reconcile: "it judges again a branch whose rebase conflicted or whose check went red",
  "review-again": "it judges again a branch that rebased cleanly",
};

export const start = (to) => {
  if (to.outcome === "running") return START[to.waits];
  return to.rejudge
    ? "it judges a branch again without the first run's slug or spec path"
    : "it starts with no ticket";
};

// The chart does not draw a step that stays in one of its states, so that step needs no words.
export const drawn = (state, words) => (action, from, to) =>
  JSON.stringify(state(from)) === JSON.stringify(state(to)) ? "stays" : words(action, from, to);

// The model's own names for its steps, as the tests give the runs: each TLA+ action is the name
// with each word capitalised.
export const named = (action) =>
  action
    .match(/[A-Z][a-z]*|[0-9]+/g)
    .map((word) => word.toLowerCase())
    .join(" ");
