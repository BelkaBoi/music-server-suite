Option Explicit
Dim shell, fso, log, i, code, ms, t0, detail, healthy, repair, stream
Dim http, flclashRunning, wmi, procs

Set shell = CreateObject("WScript.Shell")
Dim url
url = shell.ExpandEnvironmentStrings("%MUSICSERVER_PUBLIC_URL%")
If url = "%MUSICSERVER_PUBLIC_URL%" Or url = "" Then url = "https://music.example.com"
Set fso = CreateObject("Scripting.FileSystemObject")
Dim scriptDir
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
log = scriptDir & "\cloudflared-health.log"
Const UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"

Function Probe()
    t0 = Timer
    code = "000"
    On Error Resume Next
    Set http = CreateObject("WinHttp.WinHttpRequest.5.1")
    http.SetTimeouts 3000, 3000, 10000, 10000
    http.Open "GET", url & "/ping", False
    http.SetRequestHeader "User-Agent", UA
    http.Send
    If Err.Number = 0 Then
        code = CStr(http.Status)
    Else
        code = "ERR"
    End If
    Err.Clear
    On Error GoTo 0
    ms = Int((Timer - t0) * 1000)
    If ms < 0 Then ms = 0
    Probe = code
End Function

' --- 3 probes with per-probe code+latency ---
healthy = 0
detail = ""
For i = 1 To 3
    code = Probe()
    detail = detail & " #" & i & "=" & code & "/" & ms & "ms"
    If code = "200" Then healthy = healthy + 1
    If i < 3 Then WScript.Sleep 3000
Next

Set stream = fso.OpenTextFile(log, 8, True)
If healthy = 3 Then
    stream.WriteLine Now & " healthy 3/3" & detail
    stream.Close
    WScript.Quit 0
End If
stream.WriteLine Now & " unhealthy " & healthy & "/3" & detail
stream.Close

' --- repair 1: FlClash proxy core (tunnel dies when it dies) ---
repair = ""
Set wmi = GetObject("winmgmts:\\.\root\cimv2")
Set procs = wmi.ExecQuery("SELECT Name FROM Win32_Process WHERE Name='FlClashCore.exe'")
flclashRunning = (procs.Count > 0)
If Not flclashRunning Then
    shell.Run """C:\Program Files\FlClash\FlClash.exe""", 0, False
    repair = " flclash-restarted"
    WScript.Sleep 20000
End If

' --- repair 2: queue a connector restart (never blocks) ---
shell.Run "powershell -NoProfile -Command Restart-Service cloudflared", 0, False
WScript.Sleep 15000

' --- verify recovery with the same instrumented probes ---
healthy = 0
detail = ""
For i = 1 To 3
    code = Probe()
    detail = detail & " #" & i & "=" & code & "/" & ms & "ms"
    If code = "200" Then healthy = healthy + 1
    If i < 3 Then WScript.Sleep 3000
Next
Set stream = fso.OpenTextFile(log, 8, True)
stream.WriteLine Now & " after repair " & healthy & "/3" & detail & repair
stream.Close

' --- repair 3: if still unhealthy, rotate VPN node once and try again ---
If healthy < 3 Then
    Dim rotOut, rotated
    rotated = False
    On Error Resume Next
    Set rotOut = shell.Exec("powershell -NoProfile -ExecutionPolicy Bypass -File """ & scriptDir & "\rotate-vpn-node.ps1""")
    Do While rotOut.Status = 0
        WScript.Sleep 500
    Loop
    If Err.Number = 0 Then
        rotated = True
        repair = repair & " rotated:" & Trim(rotOut.StdOut.ReadAll())
    End If
    Err.Clear
    On Error GoTo 0

    If rotated Then
        shell.Run "powershell -NoProfile -Command Restart-Service cloudflared", 0, False
        WScript.Sleep 15000
        healthy = 0
        detail = ""
        For i = 1 To 3
            code = Probe()
            detail = detail & " #" & i & "=" & code & "/" & ms & "ms"
            If code = "200" Then healthy = healthy + 1
            If i < 3 Then WScript.Sleep 3000
        Next
        Set stream = fso.OpenTextFile(log, 8, True)
        stream.WriteLine Now & " after rotate " & healthy & "/3" & detail
        stream.Close
    End If
End If

If healthy = 3 Then WScript.Quit 0
WScript.Quit 1
