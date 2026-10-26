Option Explicit
Dim localFlowShell, localFlowFiles, localFlowFolder, localFlowCommand
Set localFlowShell = CreateObject("WScript.Shell")
Set localFlowFiles = CreateObject("Scripting.FileSystemObject")
localFlowFolder = localFlowFiles.GetParentFolderName(WScript.ScriptFullName)
localFlowCommand = Quote(localFlowShell.ExpandEnvironmentStrings("%ComSpec%")) & " /d /s /c " & Quote(Quote(localFlowFolder & "\Start.cmd") & " --no-browser")
' 0 hides the console. False leaves the tracker running after this script exits.
localFlowShell.Run localFlowCommand, 0, False

Function Quote(value)
    Quote = Chr(34) & value & Chr(34)
End Function
