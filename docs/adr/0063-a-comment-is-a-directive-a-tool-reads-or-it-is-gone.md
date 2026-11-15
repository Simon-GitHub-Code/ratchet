---
status: accepted
---
# A comment is a directive a tool reads, or it is gone

Amends ADR 0037: the ledger also grows in the change that first fills the lister, and along a rename, not only when the pin moves. Amends ADR 0028: documentation coverage unbinds outside a library, the one exception to "nothing unbinds".

The owner wants no prose comment in a project on the method. A comment drifts from the code beside it. A reason worth keeping belongs in a test, a type, a lint rule or a decision record, where a gate or a reviewer reads it. The plugin names no language (ADR 0042), so it cannot find a comment itself. The binding already lists the directives each bound tool reads, one extended regular expression per kind, and the `suppressions` gate counts the lines they match (`skills/adopt/templates/docs/process/binding.md:212-224`, ADR 0042, ADR 0050). The dependencies contract is the precedent for a reader the project writes and a script the plugin ships that judges its records (`hooks/ratchet_guards/dependencies.py:8-16`, `:35-36`, ADR 0044).

## Decision

**The rule.** A comment is allowed only when a tool reads it, which is a line that matches a kind of the binding's `## Suppressions` section. A doc comment is allowed only in a file of the published interface, and only while the merge base's binding declares the type `library` (`binding.md:77`). A reason for a directive stays on the directive's own line. Every other comment is a finding.

**One list of directive forms.** The Suppressions kinds table gains a third rule, `kept`. A `kept` kind is a directive that silences no gate, such as the interpreter line at the top of a script, a build constraint or a one-line licence identifier. `rises` and `never` keep their meaning. A comment is a directive when its source line matches any kind's pattern. It is searched with the engine the suppressions count uses, extended patterns through git's own search (`scripts/markers.py:32-35`, `scripts/suppressions.py:142`), so the two gates cannot disagree about what a pattern matches. `suppressions.RULES` gains `kept` (`scripts/suppressions.py:42`). Until then the parser refuses a `kept` row (`:95`). `comments.py` reads every kind, whatever its rule. The version note on a pinned workflow line, which the pins gate requires (`scripts/pins.py:4`, `:48`, `:70-71`), is a directive by the plugin's own reading of that format.

**The lister.** The binding's new `## Comments` section names an entry point, `comments-list <file>...`, written by the agent that binds the stack. It reads the files as data and runs nothing. It prints one record per line a comment occupies, tab separated: path, line, kind (`comment` or `doc`) and the text without its delimiters. It exits non-zero on a file it cannot read. A table maps globs to `listed`, or to `none: <reason>` for code nobody writes by hand, such as generated code, vendored code or a fixture that reproduces outside input. Every tracked text file that is not a document matches a row. A record whose text is not on the line it names is refused, so a lister cannot report what the file does not show. The lister is a guard path, so changing it is the owner's merge.

**The published interface.** The binding's `## Architecture` section (`binding.md:21-31`) gains `- Published interface: <globs>`, or `none: not a library`. It is chosen with the architecture, since which surfaces are promises is the question `life:depended-on` reopens (`skills/method/application-types.toml:68`). It is in the binding, a guard path, so widening it is the owner's.

**The gate.** `scripts/comments.py`, entry point `comments`, tier 0, trigger `always`, question `no prose comments`. Its entry point runs the lister over the files changed since the merge base and pipes the records into the script. The script runs nothing itself. It reads the records on standard input and takes the changed files as arguments (`scripts/comments.py:179-193`). Empty input is green, so the lister's exit must reach the gate. The adoption template's recipe shell sets `pipefail` (`skills/adopt/templates/justfile:10`), so a lister that fails turns the entry point red. The dependencies contract treats a reader that fails the same way, as a run it cannot judge (`hooks/ratchet_guards/dependencies.py:37-38`). Each finding is keyed by the rule `comments`, its path and its text, the ledger's rule, path and problem (`scripts/owed.py:92-102`). An owed entry excuses one equal finding (ADR 0037), and every other finding is red. A comment moved within its file keeps its key. One reworded, copied or added is red. An entry whose comment is gone is red until it is dropped.

**What exists is owed.** An adoption records every comment it finds, as a change from no pin may (`scripts/owed.py:214-227`, ADR 0037). In a repository already adopted, the upgrade adds the section, and filling it is a fill-first item (`owed.py:73`, ADR 0050). Two exceptions join `owed.grown`, which today lets the ledger grow only in a change that moves the pin (`owed.py:242-246`). The change that first fills the lister may add `comments` entries carrying the pin as `since`, because the first reading is free as the first instance of a kind is (ADR 0030). An entry may follow a rename git reports, with the same rule and text. Otherwise the ledger only shrinks.

**Documentation coverage is asked of a library alone**, over the published interface. Today the question applies to every type (`application-types.toml:260-262`). It gains `applies-to = ["library"]`. A row a non-library repository bound to a tool becomes `none: not a library` in the upgrade, which the owner merges. This is the one exception to "nothing unbinds" (`skills/method/tiers.md:167`, ADR 0028). The question now asks for what the rule forbids.

**The bite.** The question requires two proofs, `prose-comment` (a prose comment in a listed file) and `doc-comment` (a doc comment outside the published interface). The conformance gate also requires a bite that plants into the globs of each `listed` row, so each file kind's lister is shown to find a comment. The plugin's own bite proof covers the judging, beside the suppressions cases (`scripts/bite.py:502-505`). A directive line is green. A published doc comment of a library is green. A doc comment outside the published interface is red. A lister that reports text absent from its line is red. A bite cannot remove `library` on its branch, because the merge base's binding judges, so the doc comment outside the published interface stands for that case.

## Consequences

The clean-up of what is owed is ADR 0065's.

The plugin holds itself to the rule. Its own scripts, hooks and tests lose their prose comments and docstrings as an adopted project's code does, and its pages follow ADR 0066.

The lister is a row an agent writes. It can miss a form of comment inside a file kind it covers, and the per-row bite proves one form only. That is weaker than a parser the plugin owned, which no stack-neutral plugin can have. It is said on the row, as ADR 0050 says it for its own rows.

The judge has two further limits. A lister may report less text than a comment holds, since the judge checks only that the line shows the text, and a short text makes a rewording invisible. The guard path and the bite proof hold the lister against that. A comment with no text cannot be owed, since the ledger refuses an empty field (`owed.py:129`), so adoption deletes a bare marker.

One owed entry per comment means thousands of entries in an old project. Measured on the judge's first build with 5,000 `comments` entries, `owed` took about 2.5 s, `conformance` 3.3 s and the merge judge 1.9 s, against 0.1 to 0.5 s with none. The cost is parsing the ledger four or five times in `owed`, three in `conformance` and twice in the judge. Nothing grows with the square of the entries. Each command reads the ledger once, before the ledger exceptions above are built, and the entries stay in `quality/owed.toml`.

## Refutation criterion

This is wrong if, across the first ten adoptions after the release, any of these happens:

- Prose reappears where the gate cannot see it: string constants nothing reads, identifiers stretched into sentences, reasons moved into pages the prune pass then cuts.
- An incident's cause is a constraint that a deleted comment stated and nobody encoded.
- A lister is green while a form of comment its file kind has goes unreported.

Look at the ledger's size over time, the dead-code gate's findings and the incident records.

## Considered options

- **A catalogue question only, answered by a tool the adopting agent chooses.** Rejected. Few tools forbid every comment but a list. The list of directive forms would fork from the Suppressions table. The owed and library rules would be rewritten per project, and the plugin could prove none of them.
- **A pattern per file kind in the binding, as the Triggers and Image pins sections hold.** Rejected. A pattern cannot tell a comment marker inside a string literal, and in some languages a doc comment is a string literal.
- **A count per file that only falls.** Rejected. Rewording an old comment into a new one keeps the count.
- **The lines a change adds, judged against the merge base, with no ledger.** Rejected. Moving a function within its file turns its old comments red, and the backlog is invisible.
- **One owed entry per file.** Rejected. It excuses every new comment in a file that still owes one, which ADR 0037 rejected for the same reason.
- **Doc comments on every public symbol.** Rejected. It asks for prose on every symbol, and that prose drifts as any comment does.
- **The clean-up through a companion plugin's comment reviewer.** Rejected. Its keep list allows doc comments that define any public contract and comments about behaviour forced from outside (pstack 0.9.137, `agents/comment-sicko.md:17`, `:19`). It waits for approval before it encodes a constraint (`skills/no-comments/SKILL.md:34`).

## Settled by the owner, 2026-11-15

- **Licence notices.** A one-line licence identifier is a `kept` directive. A project whose licence needs a full header lists that header's lines as a `kept` kind in its binding, which the owner merges. Exempting full headers by default was declined: it leaves a door for prose.

## Defaults accepted by the owner, 2026-11-15

Each item was a default taken overnight. The owner accepted every one as written on 2026-11-15.

- **1. Documentation coverage for libraries only.** Default taken overnight: the question applies to `library` alone, and the upgrade unbinds it elsewhere. Recommendation: accept. A non-library has no published interface, so coverage there asks for the comments this record forbids. Cost: the one exception to "nothing unbinds", in an upgrade the owner merges.
- **2. Markdown files are outside the comment rule.** Default taken overnight: a document is judged by its kind (ADR 0064), not by this gate, and the lister's table needs no row for it. Recommendation: accept. Cost: a comment marker inside a document is never a finding.
