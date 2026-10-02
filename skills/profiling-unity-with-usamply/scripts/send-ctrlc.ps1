# Send Ctrl+C to a console process (graceful stop for a usamply recording).
# Usage: powershell -NoProfile -File send-ctrlc.ps1 -ProcessId <pid>
param([Parameter(Mandatory)][int]$ProcessId)

Add-Type -Namespace W -Name K -MemberDefinition @'
[DllImport("kernel32.dll")] public static extern bool FreeConsole();
[DllImport("kernel32.dll")] public static extern bool AttachConsole(uint pid);
[DllImport("kernel32.dll")] public static extern bool SetConsoleCtrlHandler(System.IntPtr h, bool add);
[DllImport("kernel32.dll")] public static extern bool GenerateConsoleCtrlEvent(uint ev, uint group);
'@

[W.K]::FreeConsole() | Out-Null
if (-not [W.K]::AttachConsole($ProcessId)) { throw "AttachConsole($ProcessId) failed" }
[W.K]::SetConsoleCtrlHandler([IntPtr]::Zero, $true) | Out-Null   # don't kill ourselves
[W.K]::GenerateConsoleCtrlEvent(0, 0) | Out-Null
Start-Sleep -Milliseconds 500
[W.K]::FreeConsole() | Out-Null
