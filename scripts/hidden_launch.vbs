' hidden_launch.vbs -- silent launcher for Windows Scheduled Tasks.
'
' Why: a task whose Action is powershell.exe / pythonw.exe / cmd.exe starts a
' console-subsystem program directly, and Task Scheduler gives you no way to
' suppress the console window. Wrapping the action in wscript.exe with
' Run(..., 0, False) does suppress it.
'
' Install:
'   1. edit the Run line below to your command
'   2. save this file (ANSI/GBK or UTF-16 -- avoid UTF-8 without BOM if the
'      path contains non-ASCII characters)
'   3. point the task at it:
'        schtasks /change /tn "<TaskName>" /tr "wscript.exe //B //Nologo C:\path\hidden_launch.vbs"
'      (drop the inner quotes when the path has no spaces -- nested quotes get
'       mis-parsed and /RU SYSTEM can leak into the arguments)
'
' Verify:
'   schtasks /query /tn "<TaskName>" /fo LIST /v      ' read back the action
'   then run it once and confirm no visible window appears.
'
' Note: //B (batch mode) and //Nologo keep wscript.exe itself quiet.

Option Explicit

Dim sh
Set sh = CreateObject("WScript.Shell")

'                        command line                                  window  wait
'                        --------------------------------------------  ------  -----
sh.Run """C:\path\to\pythonw.exe"" ""C:\path\to\script.py"" --arg",      0,    False

' window: 0 = hidden, 1 = normal, 2 = minimized, 3 = maximized
' wait:   False = fire and forget (recommended for tasks), True = block

Set sh = Nothing
