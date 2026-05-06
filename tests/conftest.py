from __future__ import annotations

import os
import tempfile
from pathlib import Path


_TEST_TEMP_ROOT = Path(__file__).resolve().parents[1] / ".pytest-tmp"
_ORIGINAL_MKDIR = os.mkdir
_TEST_TEMP_ROOT.mkdir(parents=True, exist_ok=True)


def _windows_safe_mkdir(path, mode=0o777, *, dir_fd=None):
    # Python 3.13 can create inaccessible 0o700 temp dirs in this Windows harness.
    if mode == 0o700:
        mode = 0o777
    if dir_fd is None:
        return _ORIGINAL_MKDIR(path, mode)
    return _ORIGINAL_MKDIR(path, mode, dir_fd=dir_fd)


def pytest_configure(config) -> None:
    temp_root = str(_TEST_TEMP_ROOT)
    tempfile.tempdir = temp_root
    for key in ("TMP", "TEMP", "TMPDIR"):
        os.environ[key] = temp_root

    if os.name != "nt":
        return

    os.mkdir = _windows_safe_mkdir
    config.add_cleanup(lambda: setattr(os, "mkdir", _ORIGINAL_MKDIR))

    try:
        import _pytest.pathlib as pytest_pathlib
        import _pytest.tmpdir as pytest_tmpdir
    except Exception:
        return

    def _skip_cleanup_dead_symlinks(_root: Path) -> None:
        return None

    pytest_pathlib.cleanup_dead_symlinks = _skip_cleanup_dead_symlinks
    pytest_tmpdir.cleanup_dead_symlinks = _skip_cleanup_dead_symlinks
