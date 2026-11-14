---
status: accepted
---
# The last stack names leave the plugin for the binding

ADR 0042 moved three gates' tool lists into the binding and left a residue that `hooks/tests/test_stack_names.py` holds and only shrinks. Seven entries remained that were not the guard hook: the prompt examples of the pipeline, the signs of a life event, the converters of two mutation tools' reports, the exclusion directives of the coverage reader, the container build files of the pins gate and the extension list of the prose gate. A name in the text an agent reads is a default, so each is a place a model binds toward a tool the project did not choose.

## Decision

**The pipeline's examples are in concepts.** The two prompt examples name no language and no package manager.

**The signs of a life event are the binding's `## Life event signs` table.** One row per event whose signs the stack decides (`deployed`, `irreplaceable-data`; the catalogue marks them `in-binding`): the globs of the files that mark it in this project, or `none: <reason>`. The catalogue keeps the signs of `depended-on`, which are files a maintainer writes for strangers and no stack's. The route and the Life events question read the table; a sign only proposes, so the work tree's table is read. A missing section or row is a `conformance` finding and proposes nothing, which fails closed: no event is declared for the owner.

**The mutation gate reads one format.** One JSON line per mutant, `--tool NAME --mutants FILE`, as its docstring defines (file, mutator, replacement, status, start, end, unit). The readers of two tools' reports, the register identity of one of them and their scope rules are gone. The project's mutation recipe, written by its agent for its tool, converts the report; `scripts/bite.py` plants the neutral lines. `conformance` is red on a recipe that runs the script without `--mutants` or `--changed`. An old register entry whose identity has no position is already red and names the mutants it matched.

**The coverage gate's exclusions are the project's Suppressions kinds.** The patterns of the kinds that the Suppressions section lists for `coverage, absolute and ratcheted` are searched for in the files of an absolute group. The merge base's section judges; the work tree's is read where the base has none. `none: <reason>` there means the tool reads no directive.

**The pins gate reads the container files the binding names.** The `## Image pins` section holds a table of file globs and one regular expression each (group 1 the image reference, an optional group 2 a stage name). Each reference must carry a digest. Workflow files, which are the forge's, stay in the plugin. The merge base's section judges.

**The prose gate scans every tracked text file.** A file that is not a document is read for text after a comment marker, and a binary file is skipped. There is no list of extensions. This is stricter than before, never looser; a line that must use an avoided word carries the existing allow marker.

**The guard hook and `guards.md` keep their lists.** Their interpreter and command lists are a security boundary. A project that could write its own row could loosen it, so they stay in the plugin, and `RESIDUE` says so.

**An upgrade owes each section.** The files are the stack's, so nothing is derived from the tree: each is a `conformance` finding, owed through the ledger (ADR 0037), never a sudden red.

## Weakened or not

Three guarantees are held by a row an agent writes and not by the plugin, and each is weaker until the row exists. The pins gate reads no container file, the coverage gate searches no exclusion on an absolute path, and the route proposes no `deployed` or `irreplaceable-data` event. Each says so on every run, and `conformance` names the row with what it stops (ADR 0042's precedent for the suppressions gate). The first two are guarantees a change could break unseen in the window, which the owed ledger bounds and does not remove. Red until filled was rejected, as ADR 0037 rejects it for every new check.

A wrong row can also be weaker than the list it replaced: a pins pattern that misses a line, a Suppressions kind that omits a directive. `conformance` checks the table's shape and that each pattern compiles, not its coverage.

## Refutation criterion

This is wrong if an adopter's agent, given the sections with no examples, writes rows that miss files the old lists caught: a container file that names an image unpinned and green, or a coverage exclusion on an absolute path that the Suppressions kinds do not match. The first ten adoptions show it in their `pins` and `coverage` runs. It is wrong if the prose gate's wider scan turns a repository red on generated text that no one writes, in which case the scan needs the binding's list of files.

## Considered options

- **Keep the converters and the lists as residue.** Rejected: they steer every adopter toward the named tools.
- **Ship the lists as data the binding overrides.** Rejected by ADR 0042: a default is still an example.
- **A binding row for the prose gate's source files.** Rejected: a row could only narrow what is scanned, and a second list of extensions in the binding is the first one moved.
- **Red while a section is missing.** Rejected: every adopted repository would stop at once (ADR 0037).
- **Move the guard hook's lists into the binding.** Rejected: they are a boundary a project must not loosen.

Decided 2026-11-14.
