"""no_popup_patch -- make it impossible for any child process to flash a console.

Monkey-patches ``subprocess.Popen.__init__`` on Windows so the flag is applied
once, globally, instead of relying on every call site to remember it.

Design rules (learned the hard way):
  * OR the flag in -- never overwrite. Callers that pass DETACHED_PROCESS or
    CREATE_NEW_PROCESS_GROUP must keep those semantics.
  * If ``startupinfo`` was supplied (PTY / interactive / console apps that must
    own a window) pass everything through untouched.
  * Swallow every exception. A cosmetic plugin must never break real work.
  * Idempotent -- importing it twice is harmless.

Load it as an agent startup hook / plugin so it is active from process start.
"""
from __future__ import annotations

import sys

CREATE_NO_WINDOW = 0x08000000


def install() -> bool:
    """Install the patch. Returns True if (already) active."""
    if sys.platform != "win32":
        return False

    import subprocess

    popen = subprocess.Popen
    if getattr(popen, "_no_popup_patched", False):
        return True

    original_init = popen.__init__

    def _patched_init(self, *args, **kwargs):
        try:
            if kwargs.get("startupinfo") is None:
                kwargs["creationflags"] = (
                    int(kwargs.get("creationflags") or 0) | CREATE_NO_WINDOW
                )
        except Exception:
            pass
        return original_init(self, *args, **kwargs)

    try:
        popen.__init__ = _patched_init
        popen._no_popup_patched = True
        return True
    except Exception:
        return False


install()


# ---------------------------------------------------------------------------
# Manual helpers for code paths that bypass subprocess (e.g. os.spawn*, ctypes
# CreateProcessW, PowerShell from Task Scheduler).
# ---------------------------------------------------------------------------

def popen_kwargs(**kwargs) -> dict:
    """Return kwargs with CREATE_NO_WINDOW safely merged in."""
    if sys.platform == "win32":
        kwargs["creationflags"] = int(kwargs.get("creationflags") or 0) | CREATE_NO_WINDOW
    return kwargs


if __name__ == "__main__":
    import subprocess
    r = subprocess.run(
        [sys.executable, "-c", "print('child ran with no console window')"],
        capture_output=True, text=True, **popen_kwargs(),
    )
    print("patched:", getattr(subprocess.Popen, "_no_popup_patched", False))
    print("child stdout:", r.stdout.strip())
