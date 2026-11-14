---
status: accepted
---
# The binding declares how dependencies are read

ADR 0011 made a change to a manifest or a lockfile that only adds or raises dependencies the spec's decision, and told the two apart by reading lockfiles of five formats and manifests of three. Each format was a parser in `ratchet_guards/dependencies.py`, and a format it did not read was never dependency-only. This decision keeps every property ADR 0011 gave and moves the reading to the project. It supersedes ADR 0011 in part: its decision on which changes are dependency-only, its refusals and its residue stand, and the parsers and the lockfile names are gone.

## Decision

**The binding's `## Dependencies` section names the manifests and the lockfiles as globs, the registries the project accepts that its lockfiles do not use yet, and an entry point**, `just dependencies <dir>`, that the adopting agent writes for its stack. The entry point reads the files under `<dir>`, each at its own path, as data. It does not run the project, install or reach the network. It prints records, one per line, tab separated, in a format the plugin defines in `ratchet_guards/dependencies.py` and the template repeats:

- `declared`: file, table, name, spec, origin. One dependency a manifest asks for, with whether its spec fetches it from a registry by name and version range.
- `locked`: file, scope, name, version, order, origin, source, integrity, sides. One resolved version, with `order` as integers that sort the versions of a name as its registry does, where it comes from, a value that identifies its contents, and whether several versions of the name sit beside each other.
- `rest`: file, digest. A digest of everything in the file that can change what installs, less the dependency tables, the resolved packages and the package's own version.

**The judgement compares the output at the merge base and at the head, and keeps ADR 0011's rules.** A manifest change is dependency-only when its `rest` is equal, no entry that was there changed or went, and each new entry comes from a registry by name and version range. A lockfile change is dependency-only when its `rest` is equal, no version changed in place (the same version with other contents or a different source), every new version comes from a registry that the lockfile already used or the binding names, and no name's highest version falls; where versions sit beside each other, only the highest is held. A snapshot under `quality/snapshots/` is judged as the file it copies. The text diff is used once: each new version or spec the output reports must appear in a line the change added, so an entry point that reports what the file does not show is refused.

**Anything the contract cannot judge is not dependency-only.** That is a missing section, `none` for the entry point, an entry point that fails, runs over a minute, prints more than eight megabytes or prints a line that is not a record, a registry or direct version with no integrity, an `order` that is not integers, a file created or deleted, and a snapshot that differs from the file it copies.

**The merge base's entry point reads both sides.** `guard-diff` and the merge judge copy the merge base's tree, run the recipe from it on the base files and on the head files, and compare. A change that rewrites the recipe, or the binding section that names it, cannot change how its own files are read. The guard hook that runs before every tool call does not call it, and never did: it asks on an edit of the manifest by path, as before. The cost is one archive of the tree and two runs of a recipe, only when a changed path matches a declared glob.

**`change_size.py` takes its exempt lockfiles from the merge base's section** rather than a list of names.

**The upgrade owes the section.** The files and the entry point are the stack's, so the upgrade cannot derive them from the tree. It adds the template's section for an agent to fill, and records that the dependency-only judgement and the lockfile exemption wait for it: until then every dependency change goes to the owner, which is the safe side.

## Weakened or not

Two guarantees of ADR 0011 now rest on the agent's entry point and not on the plugin's parser: that a `rest` digest covers everything but dependencies (the parser used to compare the whole parsed file), and that `order` is right. A wrong entry point can make the judgement too lenient, and nothing in the plugin can tell. The merge base's copy is the one that runs, so a branch cannot make it lenient for itself, and the entry point is a guard path the owner reads at adoption. A lockfile that records no contents for a registry version could be judged under the parsers only where they knew the format; it is now refused everywhere, which is stricter than the formats that carried an integrity and the same as the formats the parsers did not read.

## Refutation criterion

This is wrong if a loosening of a gate reaches a merge as a dependency-only change. The open attempts are two. Write an entry point that prints a `rest` digest of the manifest's dependency tables only, change a gate setting, and see whether the change is called dependency-only; the template's `rest` text must make that an error an agent would not write, and if the first ten adoptions write it, the plugin needs to compute `rest` itself from the line diff. Swap a gate tool's artifact in a lockfile at the same version and see whether `integrity` catches it, as it did under the parsers. It is also wrong if guard-diff's note on a pipeline run becomes unreadable to the pipeline: the note keeps ADR 0011's three forms, `dependencies added:`, `lockfile:` and `not dependency-only:`.

## Considered options

- **Keep the parsers, add a plugin-side registry of formats.** Rejected by ADR 0040.
- **Have the entry point decide dependency-only itself and print a verdict.** Rejected: the security properties (a swap at the same version, a rollback, a new source) are the plugin's to hold, and a verdict from a recipe the agent writes would put them in the agent's hands. The recipe only reports facts, and the plugin compares them.
- **Run the head's recipe.** Rejected: a change could then rewrite what judges it.
- **Call the entry point from the guard hook.** Rejected: the hook runs before every tool call, must stay fast, and has no business executing project code. The judgement belongs to `guard-diff` and the merge judge, which run once per change.
- **Compare the text diff alone.** Rejected: a diff cannot say what a hash or a range does to resolution.
