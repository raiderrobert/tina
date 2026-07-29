"""Tina — an autonomous factory.

Orchestration only: select a work item, claim it, call an agent once with a
one-shot prompt, read the outcome the agent wrote, verify it, record it.
"""

from importlib.metadata import PackageNotFoundError, version

#: `project.name` in pyproject.toml. The import package is `tina`, the
#: distribution is `tina-cli`; tests/test_version.py pins the two together so a
#: rename cannot silently drop `__version__` to the fallback.
DIST_NAME = "tina-cli"

#: Used when the distribution is not installed — running straight from a
#: checkout, for example. Importing `tina` must never raise.
FALLBACK_VERSION = "0.0.0+unknown"

try:
    __version__ = version(DIST_NAME)
except PackageNotFoundError:
    __version__ = FALLBACK_VERSION

__all__ = ["DIST_NAME", "FALLBACK_VERSION", "__version__"]
