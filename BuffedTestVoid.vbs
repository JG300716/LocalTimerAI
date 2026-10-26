' Optional example: keep this file beside LocalFlow.exe, or set LOCALFLOW_HOME.
Option Explicit

Dim WshShell, fileSystem, strDesktopPath, strLocalFlowPath, strLocalFlowFolder
Set WshShell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")

strDesktopPath = WshShell.ExpandEnvironmentStrings("%USERPROFILE%") & "\Desktop"
strLocalFlowFolder = WshShell.ExpandEnvironmentStrings("%LOCALFLOW_HOME%")
If strLocalFlowFolder = "%LOCALFLOW_HOME%" Or Len(strLocalFlowFolder) = 0 Then
    strLocalFlowFolder = fileSystem.GetParentFolderName(WScript.ScriptFullName)
End If
strLocalFlowPath = fileSystem.BuildPath(strLocalFlowFolder, "LocalFlow.exe")

' LocalFlow uses a single-instance mutex. Repeated runs do not start duplicates.
If fileSystem.FileExists(strLocalFlowPath) Then
    WshShell.Run Chr(34) & strLocalFlowPath & Chr(34) & " --no-browser", 1, False
Else
    MsgBox "Nie znaleziono LocalFlow: " & strLocalFlowPath, 48, "LocalFlow"
End If

' The optional TestVoid desktop shortcut is not distributed with LocalFlow.
If fileSystem.FileExists(strDesktopPath & "\TestVoid.exe.lnk") Then
    WshShell.Run Chr(34) & strDesktopPath & "\TestVoid.exe.lnk" & Chr(34), 1, False
End If

Set fileSystem = Nothing
Set WshShell = Nothing
