"""Hokeypokey — a read-only HKP/HKPS keyserver that federates GPG keys from pluggable sources."""

from __future__ import annotations

# Compatibility shims (e.g. the pgpy/imghdr shim for Python 3.13+) must be
# installed before any module that imports pgpy.
import hokeypokey._compat  # noqa: F401  — imported for side effects

__version__ = "0.1.0"
