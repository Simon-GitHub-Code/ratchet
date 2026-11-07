"""Hypothesis profiles: ``default`` is deterministic; ``ci`` runs more examples.

Select with ``HYPOTHESIS_PROFILE=ci uv run pytest``. Neither has a per-example deadline: a
wall-clock limit fails a deterministic test for the host's load, not for a fault in the code.
"""

import os

from hypothesis import settings

settings.register_profile(
    "default", derandomize=True, print_blob=True, max_examples=200, deadline=None
)
settings.register_profile("ci", derandomize=True, print_blob=True, max_examples=2000, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))
