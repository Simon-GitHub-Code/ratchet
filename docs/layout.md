# What is in the plugin

A map of the repository. [The README](../README.md) says what the method is and how to use it; [gates.md](gates.md) lists every shipped gate.

| Path | What it is |
| --- | --- |
| `skills/method/SKILL.md` | the founding document: the words, the eleven commitments, how work runs |
| `skills/method/*.md` | the reference behind each commitment: the merge rule, tiers as questions, the ratchet, guards and disputes, the pipeline, records, verifiable shape |
| `skills/method/application-types.toml` | the six application types and the questions each is asked; `scripts/application_types.py <type>...` prints the rows a set of types leaves out |
| `skills/init/` | `/ratchet:init`: the bare bones for any repository: `git init` where needed, an `AGENTS.md` naming the four plugins with `CLAUDE.md` linked to it, the pstack and impeccable one-time setups, the first commit and the remote; the mattpocock-skills setup is the owner's command after it, and a rerun of init creates the triage labels that setup recorded |
| `skills/adopt/` | `/ratchet:adopt`: the steps and the stack-neutral templates that bind the method to a repository's runner, forge and stack |
| `skills/fill/` | `/ratchet:fill`: writes the rows an upgrade left owed that an agent can write from the repository alone, with nothing asked of the owner; no feature or drain starts before it is done |
| `skills/architecture/` | `/ratchet:architecture`: the architecture an adopted repository owes, or a revisit of it: whole options from the code, one round of questions, the decision record and the binding's `## Architecture` section; and its review when an arrival or a life event reopens it: the decision holds, or the owner is asked once how to change it |
| `skills/feature/` | `/ratchet:feature <ticket>`: the feature preflight on an agent-ready ticket, the branch, then the pipeline. With no ticket: every agent-ready ticket at once, each in a work tree of its own, merged one at a time, one report |
| `skills/safeguard/` | `/ratchet:safeguard`: binds a row the project now owes. An agent chooses the tool, writes the recipe and the fault, and binds the row in a change of its own |
| `skills/conjecture/` | `/ratchet:conjecture <ticket or spec>`: the method's two additions to a ticket (or to the spec it came from), its conjecture and how it is operated, drafted from the conversation that produced it |
| `skills/next/` | `/ratchet:next`: the command the owner types next, decided from the checkout and the tracker |
| `scripts/route.py` | the route engine behind `/ratchet:next`, every hand-over and the session hook's last line |
| `scripts/drain.py` | the queue behind `/ratchet:feature` with no ticket: the plan, the work trees it makes and removes, the rebase, the local merge (through `scripts/merge.py`) and the report |
| `scripts/questions.py` | one line per answer the owner gives a skill, and the proposal of a standing answer once the recommended option was taken three times running |
| `scripts/criteria_digest.py` | the digest of a ticket's or spec's criteria that its conjecture's `- Criteria:` line records, from which `/ratchet:conjecture` tells whether the criteria changed since |
| `workflows/pipeline.js` | the feature pipeline (preflight, spec, ticket check, implement, review; one fix pass on a review blocker until the project is depended on, the critic and its fix loop after) the feature skill runs, and the reconcile and second review of a change brought up to date behind another merge |
| `scripts/feature_preflight.py` | what must be true of a ticket and the checkout before the pipeline's first agent spawns |
| `scripts/publish_check.py` | whether a pipeline role committed, pushed or opened a pull request, judged after the run and before the orchestrator's first commit (ADR 0060), and whether a blocker or major of the run's result quotes what its record does not hold (ADR 0072) |
| `scripts/quotes.py` | which quotes of a finding's `breaks` its record holds word for word, run by a role over its findings and by the publish check over the result (ADR 0072) |
| `scripts/release.py` | the release of a version: the tag `vV` on main's commit plus `.claude-plugin/commit`, and the changelog section its page carries; run by `.github/workflows/release.yml` (ADR 0068) |
| `scripts/release_check.py` | whether a version tag is main's commit plus its commit file, run after the tag is pushed and by anyone later (ADR 0068) |
| `scripts/` | the gates that ship, listed in [gates.md](gates.md) |
| `hooks/` | the PreToolUse guard hook, the SessionStart and SubagentStart hook that puts the stance in every agent's context on an adopted checkout and names a bare one, and their `ratchet_guards` package (`guard-diff` lives here too) |
| `scripts/adapter.py` | wires the guard into Codex or OpenCode when the harness does not load the plugin's hooks |
| `adapters/` | the pi extension and the OpenCode plugin that translate a tool call into the guard's JSON |
| `docs/harness/` | one page per harness: install commands, how the guard is wired, the names mapped, what was verified |
| `docs/adr/` | the plugin's own decisions; 0004 is the harness decision, made for five and since extended to Antigravity, 0017 the one that removed the presets |
| `docs/research/` | the sources behind the stance |
| `docs/gates.md` | every shipped gate script, its tier and the question it answers |
| `docs/model/` | one page per modelled state machine (the route, the ratchet, the safeguards, a pipeline run, the drain and its merge), each held to the code by tests |
| `docs/acceptance.md` | the files an acceptance tester writes and the acceptance harness reads |
| `evals/` | the measurements: whether the stance changes how an agent works, and what the first hour asks of a solo builder |
