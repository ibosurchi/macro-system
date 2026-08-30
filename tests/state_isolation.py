"""Test-only isolation of ApexMacro's durable state.

The existing test suite imports ``apex.production_core`` and, in the course of
exercising real code paths, can reach a live calendar fetch which persists its
result to ``forex_factory_schedule_state.json`` at the repository root. A test
run must never rewrite a production state file.

This module fixes that at the **test boundary only**. Production persistence
behaviour is not modified in any way: ``_load_persistent_state`` and
``_save_persistent_state`` keep their exact semantics, and the Supabase layer is
untouched. All that changes, and only inside a test process, is *where the
module-level path constants point*.

The redirection is generic rather than a hand-maintained list: every
module-level ``str`` attribute of ``production_core`` that resolves to a file
directly inside ``PROJECT_ROOT`` is rebound into a private temporary directory.
That way a state file added to the project in future is isolated automatically
instead of silently escaping the net.

Existing files are copied into the temporary directory first, so tests that
read real fixture content still see it; they simply cannot write back.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile

_ACTIVE: dict[str, object] = {}


def _is_project_root_file(value: object, root: str) -> bool:
    if not isinstance(value, str) or not value:
        return False
    if os.path.isdir(value):
        return False
    try:
        parent = os.path.dirname(os.path.abspath(value))
    except (OSError, ValueError):
        return False
    return os.path.normcase(parent) == os.path.normcase(root)


def isolate_durable_state() -> str:
    """Redirect every production state path into a temporary directory.

    Idempotent: calling it from several test modules in one process is safe and
    reuses the same temporary directory. Returns that directory.
    """
    if _ACTIVE.get("dir"):
        return str(_ACTIVE["dir"])

    from apex import production_core as core

    root = os.path.abspath(str(core.PROJECT_ROOT))
    tmp = tempfile.mkdtemp(prefix="apexmacro_test_state_")

    redirected: dict[str, str] = {}
    for name in dir(core):
        if name.startswith("__"):
            continue
        try:
            value = getattr(core, name)
        except Exception:
            continue
        if not _is_project_root_file(value, root):
            continue

        basename = os.path.basename(value)
        target = os.path.join(tmp, basename)
        # Preserve readable fixture content so existing assertions are unaffected.
        if os.path.exists(value):
            try:
                shutil.copy2(value, target)
            except OSError:
                pass
        setattr(core, name, target)
        redirected[name] = target

    # Belt and braces: no test run should reach a remote persistence backend
    # even if credentials happen to be present in the environment.
    original_supabase_enabled = core._supabase_enabled
    core._supabase_enabled = lambda: False

    _ACTIVE.update(
        {
            "dir": tmp,
            "redirected": redirected,
            "core": core,
            "supabase_enabled": original_supabase_enabled,
        }
    )
    atexit.register(cleanup_durable_state)
    return tmp


def redirected_paths() -> dict[str, str]:
    """Which constants were redirected, for assertions."""
    return dict(_ACTIVE.get("redirected") or {})


def cleanup_durable_state() -> None:
    """Remove the temporary directory. Safe to call more than once."""
    tmp = _ACTIVE.pop("dir", None)
    core = _ACTIVE.pop("core", None)
    original = _ACTIVE.pop("supabase_enabled", None)
    _ACTIVE.pop("redirected", None)
    if core is not None and original is not None:
        core._supabase_enabled = original
    if tmp:
        shutil.rmtree(str(tmp), ignore_errors=True)
