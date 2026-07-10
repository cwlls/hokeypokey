"""Root conftest.py — applies compatibility shims before any imports.

The shims live in ``hokeypokey._compat`` (also imported by the package
``__init__``); importing it here guarantees they are installed before any
test module imports pgpy directly.
"""

from __future__ import annotations

import hokeypokey._compat  # noqa: F401  — imported for side effects
