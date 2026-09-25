# Killing the Blue Flash: Eliminating Console Popups in Headless Windows Agents

A postmortem of a bug that survived **six fix attempts over two months**, and the
layered model that finally killed it.

## Symptom

An autonomous agent on Windows spawns background subprocesses all day: keep-alive
scripts, watchdogs, health probes, scheduled tasks. Any child that links against
the console subsystem (`cmd.exe`, `powershell.exe`, `python.exe`, `ssh.exe`)
allocates a **console host window for a few hundred milliseconds**. For a hidden
background process there is no owner to hide it, so it lands on the desktop as a
**blue console box that flashes and vanishes**.

Ours fired every 5 minutes, four windows at a time, for weeks — stealing focus
and interrupting the user's typing.

## Why the first five fixes failed

### 1. Fixed at the wrong layer
We scanned 4,995 `.py` files and found 436 with "bare subprocess calls". Almost
none were relevant — mostly upstream library code and Linux-only paths. The real
triggers were **four scheduled tasks** and **one registry setting**. We were
debugging the script layer while the bug lived in the OS layer.

### 2. Fixed by convention, not by mechanism
*"Always pass `creationflags=CREATE_NO_WINDOW`"* is a rule every caller must
remember. Humans forget it. LLMs forget it. New code never knew it. **Any fix
that depends on the caller will leak** — and ours did, repeatedly.

### 3. Wrong detection criteria
We counted `conhost.exe` processes and treated any growth as proof of a popup.
Wrong in both directions:
- Processes started **with** `CREATE_NO_WINDOW` still get a conhost → count rises
  with **no** visible window (false positive).
- A window hidden by `-WindowStyle Hidden` still exists in the window list →
  count is flat while the user **does** see the flash (false negative).

**The only valid signal is a newly created window whose `IsWindowVisible` is
`True`.** Not "a conhost exists". Not "the task ran successfully".

### 4. The monitor was itself a popup source
Our first monitor polled state by spawning `powershell.exe` every N seconds.
Every poll created the exact console window it was hunting. The instrument was
generating the noise it was measuring.

### 5. We trusted hiding flags that don't work
- `powershell -WindowStyle Hidden` does **not** suppress console allocation — it
  hides the window *after* Windows creates it. The flash still happens.
- MSYS2 / git-bash `ssh.exe` ignores hidden window styles entirely (console
  subsystem program) — every spawn flashes.

## The fix: four layers, outermost first

### L0 — System-wide console delegation (do this first)

Windows 11 can delegate console hosting to Windows Terminal, which **overrides**
children's `CREATE_NO_WINDOW` and opens a WT tab instead. Force the classic host:

```powershell
$k = 'HKCU:\Console\%%Startup'
if (-not (Test-Path $k)) { New-Item -Path $k -Force | Out-Null }
Set-ItemProperty $k DelegationConsole  '{00000000-0000-0000-0000-000000000000}'
Set-ItemProperty $k DelegationTerminal '{00000000-0000-0000-0000-000000000000}'
```

After this, `WindowsTerminal.exe` should have **zero** instances. One change
silences an entire class of flashes.

### L1 — Scheduled tasks: wrap every action in a VBS shim

A task whose action is `powershell.exe ...` or `pythonw.exe ...` starts a console
program directly, and the task service offers no way to suppress the window.
Wrap it — see `scripts/hidden_launch.vbs`:

```vbs
Set sh = CreateObject("WScript.Shell")
sh.Run """C:\path\to\pythonw.exe"" ""C:\path\to\script.py"" --arg", 0, False
'                                                     ^  ^
'                                      0 = hidden window, False = don't wait
```

```powershell
schtasks /change /tn "<TaskName>" /tr "wscript.exe //B //Nologo C:\path\launch_silent.vbs"
```

`//B` (batch) and `//Nologo` matter — without them `wscript.exe` can be noisy
itself. Audit existing tasks with `scripts/audit_scheduled_tasks.ps1`.

Two traps we hit:
- **Nested quotes in `/tr`** get mis-parsed; `/RU SYSTEM` can end up inside the
  arguments. Don't quote paths that contain no spaces.
- `schtasks /change` **requires admin**, and an interactive admin prompt wants the
  account password (not a PIN). Prefer running the task as `/RU SYSTEM`.

### L2 — Process creation: patch once, globally

L0 and L1 cover tasks; the agent process still spawns children itself. Enforce the
flag in **one place** rather than at 400 call sites —
`scripts/no_popup_patch.py` monkey-patches `subprocess.Popen.__init__` on Windows:

```python
_CREATE_NO_WINDOW = 0x08000000
_orig = subprocess.Popen.__init__

def _patched(self, *args, **kwargs):
    if kwargs.get("startupinfo") is None:          # never touch PTY/interactive
        kwargs["creationflags"] = (
            int(kwargs.get("creationflags") or 0) | _CREATE_NO_WINDOW
        )
    return _orig(self, *args, **kwargs)

subprocess.Popen.__init__ = _patched
```

Notes: OR the flag in rather than overwriting, so `DETACHED_PROCESS` and
`CREATE_NEW_PROCESS_GROUP` keep working. Load it as an agent startup
hook/plugin so it is always active. This is the difference between *"we fixed the
offenders"* and *"offenders can no longer exist"*.

### L3 — SSH is its own special case

SSH popups are usually not about window flags:

1. **Use the OS OpenSSH client** (`C:\Windows\System32\OpenSSH\ssh.exe`), not the
   MSYS/git-bash build. The MSYS build is a console-subsystem program and always
   flashes; the system build honors hidden styles.
2. **Set `BatchMode yes` in `~/.ssh/config`.** Without it, a failed
   authentication *must* prompt — and on Windows ssh borrows `git-askpass.exe`
   from Git for Windows, so the user sees a **"Git password"** dialog that has
   nothing to do with Git.
3. **Get usernames right.** Our worst case: an SSH health probe used machine A's
   username against machine B, and the host had no `~/.ssh/config` entry. Auth
   failed → password prompt → new window on the desktop. With `BatchMode yes`,
   the identical mistake fails fast and **silently**.

## Verification: objective, not "I read the code"

**A. Before/after visible-window diff.** Snapshot visible console-class windows,
trigger the suspect, snapshot again. Ten seconds, no ambiguity:

```powershell
Get-Process | Where-Object { $_.MainWindowHandle -ne 0 } |
  ForEach-Object { "{0} | {1}" -f $_.ProcessName, $_.MainWindowTitle }
```

**B. Watch with zero subprocesses.** `scripts/visible_window_watch.py` uses
`ctypes` + `EnumWindows` only, so it cannot pollute its own measurement. It logs a
hit only when a window is **new** and `IsWindowVisible == True`. Run it for 15
minutes — zero hits is the acceptance test.

Never accept "the process list contains pythonw/conhost" as evidence. Background
python processes are normal; only a visible new window counts.

## Checklist

- [ ] **L0** console delegation forced to classic conhost; `WindowsTerminal.exe` count = 0
- [ ] **L1** every non-Microsoft scheduled task action wrapped (`wscript.exe //B //Nologo ...`)
- [ ] **L2** global `subprocess.Popen` patch loaded at agent startup
- [ ] **L3** OS OpenSSH + `BatchMode yes` + a config block for every host
- [ ] Detection uses `IsWindowVisible`, never conhost counts
- [ ] The watcher itself spawns no processes
- [ ] A 15-minute clean watch is the acceptance test

## Outcome

Flashes went from several per hour to rare. The residue traces to third-party
executables outside our control.

## The transferable lesson

When a symptom returns after repeated fixes, stop fixing instances and ask
**which layer can make the whole class impossible**. Two mechanisms did almost all
the work: forcing the console delegate at the OS level, and patching process
creation globally instead of by convention. Everything else was cleanup.

---

*Field notes from an autonomous multi-agent system on Windows 11 (agents on
WSL2 + native, orchestrated over SSH).*
