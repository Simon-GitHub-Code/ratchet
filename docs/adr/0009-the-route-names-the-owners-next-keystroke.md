---
status: accepted
---
# The route names the owner's next keystroke; user-invoked skills are never imitated

Three of the four plugins mark most of their skills `disable-model-invocation: true`: all of pstack but `setup-pstack`, and 23 of mattpocock-skills' 38, among them `setup-matt-pocock-skills`, `to-spec`, `to-tickets`, `triage`, `wayfinder`, `interrogate`, `architect` and `poteto-mode`. The harness refuses such a skill to an agent, in the main session and in every subagent, and says not to reproduce it another way; mattpocock-skills' own invocation rule says the same, while pstack's adapter lets a pstack skill read another pstack skill's file. The method's pages told agents to load several of these, the pipeline's critic loaded `interrogate`, and `/ratchet:init` followed `setup-matt-pocock-skills` from its file. We decided that a user-invoked skill is the owner's keystroke and appears in the method only as the slash command the owner types; that the method's job at every stage is to name that keystroke, which `scripts/route.py` decides from the checkout and the tracker, never from a guess, and which `/ratchet:next`, every ratchet hand-over and the session hook's last line print; that init stops before the tracker setup and names it; that the pipeline keeps its four roles on model-invoked skills only, with the critic working the method's rule of evidence and `interrogate` left to the owner; that the method keeps two build routes, `/ratchet:feature` when the owner steps away and `/pstack:poteto-mode` when they stay, the gates and guards holding under either; and that `/ratchet:conjecture` drafts the one section the method adds to a ticket, which a ticket may also inherit from its parent spec. Decided 2026-11-07.

## Refutation criterion

The decision is wrong if, in the first ten features built on the route, the owner has to work out the next command themselves more than once, or `/ratchet:next` names a command the state of the checkout does not call for; or if a pstack or mattpocock-skills release makes the refused skills callable again, which would leave the route's keystrokes as needless ceremony. Check after the tenth feature and at each plugin upgrade.

## Considered options

- Keep reading the refused skills' files and following them: rejected, the harness message and mattpocock-skills' rule both forbid it, and a skill followed from its file loses the author's updates and the harness's accounting of what ran.
- Make ratchet gates-only and let poteto-mode be the one front door: rejected, the pipeline's sight lines (an adversary that did not write the code, a reviewer that did not see the critic) are what makes an owner-absent build criticisable, and poteto-mode has no equivalent when the owner is away.
- A route the model infers from the method pages: rejected, that is the state before this decision, and it drifted; a script that reads the files cannot.
- Mirror the agent-ready ask into the generated permission rules: rejected in ADR 0008 and unchanged here.
