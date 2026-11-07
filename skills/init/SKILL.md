---
name: init
description: Lay down the bare bones any repository needs before agents build in it: an AGENTS.md that names the three plugins composed here and what each owns, with CLAUDE.md as a symlink to it.
disable-model-invocation: true
---

# Initialise a repository

The bare bones. `/ratchet:adopt` builds the gates on top later, when the owner is ready. The template is `${CLAUDE_SKILL_DIR}/templates/AGENTS.md`.

## Steps

1. **Check the ground.** The directory is a git repository and `claude plugin list` shows `mattpocock-skills`, `pstack` and `ratchet`. Done when all hold or you have told the owner which is missing.

2. **Write `AGENTS.md`.** Copy the template to the repository root. If an `AGENTS.md` already exists, insert the template's opening stanza (the three plugins and their questions) above its first heading and keep the rest. If a `CLAUDE.md` with content exists and no `AGENTS.md`, move that content under the stanza. Done when `AGENTS.md` opens with the three-plugin stanza.

3. **Link `CLAUDE.md`.** `ln -s AGENTS.md CLAUDE.md` at the root, replacing a plain `CLAUDE.md` whose content step 2 moved. Done when `CLAUDE.md` is a symlink to `AGENTS.md`.

4. **Fill "What this is".** One interactive question dialog for the one-paragraph purpose. The sections below it keep their placeholders for `/ratchet:adopt`. Done when that section has no angle-bracket placeholder.

5. **Run the other plugins' setup once.** `/setup-matt-pocock-skills` asks for the issue tracker, triage labels and docs location; `/setup-pstack` writes the model sheet. Skip each that has already run. Done when both have run or the owner declined.

6. **Hand over.** Say what each plugin now does in this repository, in three lines, and that `/ratchet:adopt` binds the gates when the owner wants them.
