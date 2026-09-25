# audit_scheduled_tasks.ps1 -- read-only audit: which tasks can flash a console?
#
# Pure ASCII on purpose. PowerShell 5.1 mis-decodes UTF-8-without-BOM scripts,
# which silently breaks string handling (a "ghost" class of bug).
#
# This script changes nothing. Run it before and after applying fixes.

Write-Output "=== 1. Non-Microsoft scheduled tasks: action + wrapping ==="
Write-Output "    RISK = action starts a console program with no wscript shim"
Write-Output ""

Get-ScheduledTask |
  Where-Object { $_.TaskPath -notlike '\Microsoft\*' } |
  ForEach-Object {
    $t = $_
    foreach ($a in $t.Actions) {
      if (-not $a.Execute) { continue }
      $exe = $a.Execute
      if ($exe -match 'wscript\.exe') {
        $flag = 'OK  '
      } elseif ($exe -match 'powershell|pwsh|cmd|python|\.bat') {
        $flag = 'RISK'
      } else {
        $flag = 'ok  '
      }
      Write-Output ("{0} | {1,-8} | {2} | {3} | {4}" -f $flag, $t.State, $t.TaskName, $exe, $a.Arguments)
    }
  }

Write-Output ""
Write-Output "=== 2. Console delegation (HKCU:\Console\%%Startup) ==="
$k = 'HKCU:\Console\%%Startup'
if (Test-Path $k) {
  $p = Get-ItemProperty $k
  Write-Output ("DelegationConsole : {0}" -f $p.DelegationConsole)
  Write-Output ("DelegationTerminal: {0}" -f $p.DelegationTerminal)
  if ("$($p.DelegationConsole)" -match '^\{0{8}-0{4}-0{4}-0{4}-0{12}\}$') {
    Write-Output "VERDICT: classic conhost forced (good)"
  } else {
    Write-Output "VERDICT: a delegate is stored -- Windows Terminal may adopt console children"
  }
} else {
  Write-Output "key missing -> default behaviour; Windows Terminal may take over"
}

Write-Output ""
Write-Output "=== 3. Windows Terminal processes (expect 0) ==="
$wt = @(Get-Process WindowsTerminal -ErrorAction SilentlyContinue)
Write-Output ("WindowsTerminal count = {0}" -f $wt.Count)

Write-Output ""
Write-Output "=== 4. Currently visible console-class windows (expect none) ==="
Add-Type -TypeDefinition @"
using System;
using System.Text;
using System.Runtime.InteropServices;
public class WinEnum {
  public delegate bool EnumProc(IntPtr hWnd, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc cb, IntPtr lParam);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr hWnd, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder s, int n);
}
"@

$script:hits = 0
$cb = [WinEnum+EnumProc]{
  param($h, $l)
  if ([WinEnum]::IsWindowVisible($h)) {
    $cls = New-Object System.Text.StringBuilder 256
    [WinEnum]::GetClassName($h, $cls, 256) | Out-Null
    $c = $cls.ToString()
    if ($c -eq 'ConsoleWindowClass' -or $c -eq 'CASCADIA_HOSTING_WINDOW_CLASS') {
      $txt = New-Object System.Text.StringBuilder 512
      [WinEnum]::GetWindowText($h, $txt, 512) | Out-Null
      Write-Output ("VISIBLE | {0} | {1}" -f $c, $txt.ToString())
      $script:hits++
    }
  }
  return $true
}
[WinEnum]::EnumWindows($cb, [IntPtr]::Zero) | Out-Null
if ($script:hits -eq 0) { Write-Output "none - clean" }

Write-Output ""
Write-Output "=== 5. Reminder ==="
Write-Output "Do NOT use 'conhost.exe process count' as popup evidence."
Write-Output "CREATE_NO_WINDOW children still spawn a conhost; only a VISIBLE window counts."
