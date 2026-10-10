' Launcher for xiaobai_gui.ps1 - runs the WinForms GUI with NO console window.
' Keep this file ASCII-only: VBScript is read as ANSI, non-ASCII bytes break it.

Option Explicit
Dim sh, fso, base, script, cmd
Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

base   = fso.GetParentFolderName(WScript.ScriptFullName)
script = base & "\xiaobai_gui.ps1"

If Not fso.FileExists(script) Then
    MsgBox "Cannot find xiaobai_gui.ps1:" & vbCrLf & script, 16, "Xiaobai Config Tool"
    WScript.Quit 1
End If

cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & script & """"
sh.Run cmd, 0, False
