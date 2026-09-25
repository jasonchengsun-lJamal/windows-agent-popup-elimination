"""visible_window_watch -- catch sub-second console flashes without being part of
the problem.

The instrument must not pollute its own measurement. This watcher therefore:

  * spawns **zero** subprocesses (pure ctypes + Win32 calls, no powershell, no
    wmic, no tasklist),
  * logs a hit only when a window is **new** AND ``IsWindowVisible == True``,
  * resolves pid -> process name and parent chain through Win32 APIs, not by
    shelling out,
  * runs from a VBS shim so starting it cannot flash either.

Usage (from a hidden launcher, not from a visible console):
    wscript.exe //B //Nologo watch.vbs           # see watch.vbs in this repo
or directly, if you already have a silent console:
    pythonw.exe visible_window_watch.py C:\\logs\\flash.log 20

A hit is evidence of exactly one popup. Zero hits over the watch window (15
minutes covers the usual 5-minute task cycle) is the acceptance test.

    python visible_window_watch.py flash.log 20
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
psapi = ctypes.WinDLL("psapi", use_last_error=True)

WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

# Window classes that mean "a console window appeared on screen".
CONSOLE_CLASSES = {
    "ConsoleWindowClass",        # classic conhost
    "CASCADIA_HOSTING_WINDOW_CLASS",  # Windows Terminal
}

user32.EnumWindows.argtypes = [WNDENUMPROC, wt.LPARAM]
user32.IsWindowVisible.argtypes = [wt.HWND]
user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
user32.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _title(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def _pid_of(hwnd: int) -> int:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _proc_name(pid: int) -> str:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return "?"
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wt.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return "?"
    finally:
        kernel32.CloseHandle(h)


def _parent_of(pid: int) -> int:
    """Parent pid via NtQueryInformationProcess (no shelling out)."""
    class PROCESS_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("Reserved1", ctypes.c_void_p),
            ("PebBaseAddress", ctypes.c_void_p),
            ("Reserved2", ctypes.c_void_p * 2),
            ("UniqueProcessId", ctypes.c_void_p),
            ("InheritedFromUniqueProcessId", ctypes.c_void_p),
        ]

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return 0
    try:
        ntdll = ctypes.WinDLL("ntdll")
        info = PROCESS_BASIC_INFORMATION()
        ntdll.NtQueryInformationProcess(
            h, 0, ctypes.byref(info), ctypes.sizeof(info), None
        )
        return int(info.InheritedFromUniqueProcessId or 0)
    except Exception:
        return 0
    finally:
        kernel32.CloseHandle(h)


def _chain(pid: int, depth: int = 4) -> str:
    names, seen = [], 0
    while pid and seen < depth:
        names.append(f"{_proc_name(pid)}({pid})")
        pid = _parent_of(pid)
        seen += 1
    return " <- ".join(names)


def snapshot() -> dict:
    """All currently *visible* console-class windows -> {hwnd: (class, title, pid)}."""
    found: dict[int, tuple] = {}

    def _cb(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = _class_name(hwnd)
        if cls in CONSOLE_CLASSES:
            found[int(hwnd)] = (cls, _title(hwnd), _pid_of(hwnd))
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return found


def watch(log_path: str, minutes: float = 20.0, interval: float = 0.12) -> None:
    log = open(log_path, "a", encoding="utf-8", buffering=1)
    started = time.time()
    known = set(snapshot())
    log.write(
        f"# watch start {time.strftime('%Y-%m-%d %H:%M:%S')} "
        f"| baseline visible console windows = {len(known)}\n"
    )

    hits = 0
    while time.time() - started < minutes * 60:
        for hwnd, (cls, title, pid) in snapshot().items():
            if hwnd in known:
                continue
            known.add(hwnd)
            hits += 1
            log.write(
                f"{time.strftime('%H:%M:%S')} FLASH#{hits} class={cls} "
                f"title={title!r} pid={pid} chain={_chain(pid)}\n"
            )
        time.sleep(interval)

    log.write(f"# watch end | flashes={hits} over {minutes:g} min\n")
    log.close()
    print(f"flashes={hits}")


if __name__ == "__main__":
    log_file = sys.argv[1] if len(sys.argv) > 1 else "flash.log"
    mins = float(sys.argv[2]) if len(sys.argv) > 2 else 20.0
    watch(log_file, mins)
