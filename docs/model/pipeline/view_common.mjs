// The owner's words for one pipeline run, shared by the charts of docs/model/pipeline.md.

export const FIRST_BUILD = "A first build";
export const REJUDGE = "Judging a rebased branch again";

const AGENT = {
  preflight: "The preflight: is the ticket ready?",
  spec: "The spec: tests that fail, and the size",
  "ticket-check": "The ticket check: does the spec keep every promise?",
  "spec-amend": "The spec is amended to the gaps",
  "ticket-recheck": "The ticket check, once more",
  implement: "The build",
  critique: "The critic attacks the change",
  fix: "A fix round",
  review: "The review",
  reconcile: "The reconcile: finish the rebase, check green",
  "review-again": "The review, once more",
};
export const agent = (v) => AGENT[v.waits];

const STOP = {
  "no ticket was passed": "Stopped: no ticket, or the ticket is not ready",
  "the preflight is red": "Stopped: no ticket, or the ticket is not ready",
  "a question for the owner": "Stopped for you: a question, or a guard path to change",
  "a guard path the owner must change": "Stopped for you: a question, or a guard path to change",
  "too large": "Stopped too large: split the ticket",
};

// How a run ended, or the agent it waits on.
export const where = (v) => {
  if (v.outcome === "running") return v.waits === "none" ? "Not started" : agent(v);
  if (v.outcome === "approve") return "Approved";
  if (v.outcome === "request_changes") return "Changes requested";
  if (v.outcome === "blocked") return STOP[v.stop];
  return v.resume ? "Stopped on an error: resume the run" : "Stopped on an error: an input is missing";
};

// The agent's answer that moved the run between two of those.
export const answer = (from, to) => {
  const now = where(to);
  if (now === "Stopped on an error: resume the run") return "the agent dies";
  if (now === "Stopped on an error: an input is missing") return "it is ready but names no slug";
  if (now.startsWith("Stopped for you")) return "it asks you, or needs a guard path changed";
  if (now.startsWith("Stopped too large"))
    return from.waits.startsWith("spec")
      ? "it measures or estimates the change over a ceiling"
      : "it measures the change over a ceiling";
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
