"""Compatibility shims applied at import time.

Imported for its side effects by ``hokeypokey/__init__.py`` (and by the root
test ``conftest.py``), so the shims are in place before any third-party
dependency is imported — both at runtime and under pytest.
"""

from __future__ import annotations

import sys
import types


def _install_imghdr_shim() -> None:
    """Provide a stub ``imghdr`` module when the real one is unavailable.

    pgpy 0.6.0 imports the stdlib ``imghdr`` module, which was removed in
    Python 3.13.  pgpy only uses ``imghdr.what()`` to classify image UID
    attributes, so a stub that always returns ``None`` is sufficient for
    serving public keys.  On Python <= 3.12 the real module is used.
    """
    try:
        import imghdr  # noqa: F401  — real module available; nothing to do
    except ModuleNotFoundError:
        shim = types.ModuleType("imghdr")
        shim.what = lambda *args, **kwargs: None  # type: ignore[attr-defined]
        sys.modules["imghdr"] = shim


_install_imghdr_shim()
