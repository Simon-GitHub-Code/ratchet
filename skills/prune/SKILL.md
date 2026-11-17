---
name: prune
description: Delete the comments a project owes and cut what its pages repeat of the code, with nothing lost, judged by a review agent.
disable-model-invocation: true
---

# Prune what the code now says

Delete what the code already says, and keep everything it does not. The route names this skill with one subject. `comments` works off the comments `quality/owed.toml` owes. `pages` cuts the text of the pages the binding lets a pass cut. Load the `ratchet:method` skill first.

The owner is asked nothing. Pruning is reversible, and git holds every cut. A review agent reads every removal, and its verdict lets the merge judge pass the change. A decision record a comment batch writes is the only part the owner merges.

## Steps for `comments`

1. **Take one batch.** Start on the default branch with a clean tree, and make the branch `chore/prune-comments-<n>`, where `<n>` is the next free number. List the owed comments: `python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode "${CLAUDE_PLUGIN_ROOT}/scripts/owed.py" --comments --root .`. Take them file by file, in the order listed, until the batch would turn `just change-size` red.

   Done when you hold a list of files and the owed comments in each.

2. **Settle each comment.** Read it with the code around it, then do the first of these that fits:
   - **It narrates what the code does:** delete it.
   - **It narrates what a test checks:** delete it, and rename the test when its name does not say it.
   - **It claims a constraint:** encode the constraint first, then delete the comment. A behaviour becomes a test named for it, a shape becomes a type, a rule for the whole repository becomes a lint rule, and a reason for a choice becomes a decision record. A test you add goes red when you break the constraint by hand. See it red, then put the code back.
   - **The failure output of a test would not say what broke:** add an assertion message that says it, in a few words. Add none where the output already says it.

   Done when every comment in the batch is deleted, and each claimed constraint has its test, type, rule or record.

3. **Drop the ledger entries.** Remove each settled comment's entry from `quality/owed.toml` in the same change. `just comments` is green with the entries gone.

4. **Split off the records.** Move every decision record the batch wrote onto its own branch, `chore/prune-records-<n>`, as a change of its own. Its merge is the owner's. The code changes merge without them.

5. **Have it reviewed.** Start a fresh agent as the reviewer. Give it the diff and these instructions only: "For each removed comment, find the test, type, rule or record that now states what it said, or state that it only narrated the code. Approve only when nothing is lost that no file now states. List each removal you cannot place." Fix what it lists, and ask again.

6. **Open the change.** Commit with the subject `refactor(prune): <the files, in a few words>`. Then record the review's approval as `python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode "${CLAUDE_PLUGIN_ROOT}/scripts/merge_ready.py" record --root .` with `{"slug": "prune-comments", "verdict": "approve", "unresolved": [], "openQuestions": []}` on its input, and commit the record alone, last. Open it as [feature step 5](../feature/SKILL.md) opens a change, or with no forge run `just merge <branch>`. The merge judge passes it on the review's verdict.

## Steps for `pages`

1. **Find what may be cut.** Start on the default branch with a clean tree, and make the branch `chore/prune-pages-<n>`. The pages you may cut are those of a kind marked `pass`, in the plugin's catalogue or the binding's `## Documents`. Start with the pages that cite a path changed since the page's own last change: `python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode "${CLAUDE_PLUGIN_ROOT}/scripts/documents.py" stale --root .`.

2. **Cut what the code says.** Cut a sentence or a section only when a tracked file now states it, and the reader would find it there. Keep, every time:
   - each heading the kind keeps, such as the brief's `## Purpose` and `## In the owner's words`;
   - an instruction: how to install, run, deploy or reach someone;
   - a reason;
   - anything in a guard path.
   For each cut, write one line in the change description: the page, the cut words in short, and the path and symbol that now state them.

3. **Check the citations.** Run `python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode "${CLAUDE_PLUGIN_ROOT}/scripts/documents.py" cited --root . <the description file>`. It is red when a cited path is not in the tree, or does not hold the symbol as a whole word. Fix each line it names.

4. **Have it reviewed.** Start a fresh agent as the reviewer. Give it the diff and the change description only, with these instructions: "For each cut passage, open the path and symbol its line names, and check that they say what the passage said. Approve only when nothing is lost that no file now states. List each cut you cannot place." Fix what it lists, and ask again.

5. **Open the change.** Commit with the subject `docs(prune): <the pages, in a few words>`. That subject marks the pass for the route. Then record the review's approval as `python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode "${CLAUDE_PLUGIN_ROOT}/scripts/merge_ready.py" record --root .` with `{"slug": "prune-pages", "verdict": "approve", "unresolved": [], "openQuestions": []}` on its input, and commit the record alone, last. Open it as for `comments`. When the pass cuts nothing, open no change. Run `python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode "${CLAUDE_PLUGIN_ROOT}/scripts/documents.py" passed --root .`, so the route counts from here.

## Hand over

Say in plain words what was deleted or cut, what now states each constraint, and that a decision record waits for the owner's merge when a comment batch wrote one. The last lines are the route: run `python3 -E -s -B -X pycache_prefix=/nonexistent/ratchet-no-bytecode "${CLAUDE_PLUGIN_ROOT}/scripts/route.py" --root .` in one Bash call and print its lines as they are.
