# Stack presets

One directory per stack the method has been bound to before. Adopt reads `## Stack` from `PRODUCT.md` (or recognises the manifests in the tree), picks the matching preset, merges its files into the templates it has just copied, and confirms the resulting binding with the owner in one dialog. A stack with no preset goes through the binding interview as before; a binding that worked becomes the next preset.

Every preset has the same files, each a fragment adopt merges into the template of the same name:

| File | Merged into | How |
| --- | --- | --- |
| `justfile` | `justfile` | each recipe here replaces the template recipe of the same name (its `bind:` body); recipes the preset does not name keep their stub and stay on the owner's list |
| `binding.md` | `docs/process/binding.md` | the Runner paragraph, each Gates row's Tool cell by question, the Absolute paths, Limits and Tool versions sections |
| `manifest.toml` | `guards/manifest.toml` | each group's `paths` are appended to the template group of the same name, replacing its `# bind:` comment |
| `thresholds.toml` | `quality/thresholds.toml` | appended; the coverage groups with their `paths`, the `core` group absolute |
| `ci.yml` | `.github/workflows/ci.yml` and `deep.yml` | the toolchain steps replace the `# bind: toolchain setup` comment in every job; the cache `path:` and `key:` lines replace the template's |
| `tools/` | the repository root | tool configuration stanzas, appended to the file of the same name when it exists (`pyproject.toml`, `package.json`) or copied when it does not |
| `fitness/` | the fitness directory the manifest names | the fitness-test scaffold: layers, limits, the ADR constants, a self-sabotage check |

The shipped gates (`markers`, `suppressions`, `prose`, `snapshots`, `commits`, `change-size`, `ratchet-lowered`, `settings-check`, `guard-diff`, `conformance`, `budget`, the tool-neutral half of `pins`, the shipped half of `bite`) are bound in the template already and no preset repeats them. Every action a preset's `ci.yml` names is pinned to a commit with its version in a comment, so `just pins` is green on the merged result.
