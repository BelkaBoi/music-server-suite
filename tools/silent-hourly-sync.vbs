' Silent launcher for the hourly Spotify sync (hidden window, waits for exit code).
Set shell = CreateObject("WScript.Shell")
Dim fso, syncCmd
Set fso = CreateObject("Scripting.FileSystemObject")
syncCmd = fso.BuildPath(fso.GetParentFolderName(WScript.ScriptFullName), "hourly-sync.cmd")
shell.Run Chr(34) & syncCmd & Chr(34), 0, True
