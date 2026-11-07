"""Harness-enforced guards on the quality gates of the Ratchet method; standard library only.

``manifest`` reads ``guards/manifest.toml`` and adds the plugin's own locked paths; ``shell``
lexes Bash; ``git``, ``gh`` and ``writes`` hold the rules; ``bash`` applies them to a command
line; ``hook`` is the PreToolUse hook behind ``hooks/guard.py``; ``diff`` lists changed guard
paths for ``just guard-diff`` and CI. Standard library only, because the hook runs on the
system ``python3`` outside any project virtualenv.
"""
