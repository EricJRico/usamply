<#
.SYNOPSIS
  End-to-end: record a Unity Editor workload with usamply, with named C# (Mono JIT) frames.

.DESCRIPTION
  Unity's Mono doesn't list already-compiled methods when a trace starts, but it lists every
  live method when the trace stops (end rundown, Method/DCStopVerbose). The mono-fix usamply
  build uses that list for the whole recording, so no reload or restart is needed.
  -Reload forces a domain reload under the recorder instead (fallback for older builds).

.EXAMPLE
  profile-run.ps1 -ProjectPath C:\Projects\MyGame -Out C:\tmp\run1.json -Seconds 60 `
      -Workload "my_bench --scenario 1 --frames 600"
#>
param(
    [Parameter(Mandatory)][string]$ProjectPath,
    [Parameter(Mandatory)][string]$Out,
    [int]$Seconds = 60,
    [string]$Workload = '',             # one string of args after `unity command`; empty = just idle in Play mode
    [switch]$Reload,                    # force a domain reload while recording (fallback; not needed with end-rundown support)
    [switch]$KeepEtl,
    [string[]]$SymbolDir = @(),
    [string]$Usamply = $env:USAMPLY_EXE
)

$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$common = @('--project-path', $ProjectPath, '--result-only', '--no-banner', '--caller', 'plugin', '--skill', 'profiling-unity-with-usamply')

function Get-EditorStatus {
    try { unity command editor_status @common 2>$null | ConvertFrom-Json } catch { $null }
}
function Wait-Editor([scriptblock]$Done, [int]$TimeoutSec, [string]$What) {
    $t = [Diagnostics.Stopwatch]::StartNew()
    while ($t.Elapsed.TotalSeconds -lt $TimeoutSec) {
        $s = Get-EditorStatus
        if ($s -and (& $Done $s)) { return $s }
        Start-Sleep -Milliseconds 500
    }
    throw "Timed out after $TimeoutSec s waiting for: $What"
}

# 1. Edit mode, so the reload and the Play-mode entry both happen while recording.
$s = Get-EditorStatus
if (-not $s) { throw "No connected Editor for $ProjectPath (check 'unity status')." }
if ($s.playMode -ne 'stopped') {
    unity command editor_stop @common | Out-Null
    Wait-Editor { param($s) $s.playMode -eq 'stopped' } 60 'Play mode to stop' | Out-Null
}

# 2. Start the recorder detached and wait until it is actually sampling (after UAC).
$log = [IO.Path]::ChangeExtension($Out, '.log')
Remove-Item $Out, $log -ErrorAction SilentlyContinue
$recArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "$here\record.ps1",
             '-ProjectPath', $ProjectPath, '-Seconds', $Seconds, '-Out', $Out)
if ($Usamply) { $recArgs += @('-Usamply', $Usamply) }
if ($KeepEtl) { $recArgs += '-KeepEtl' }
if ($SymbolDir) { $recArgs += @('-SymbolDir', ($SymbolDir -join ',')) }
Write-Host "Starting recorder ($Seconds s). Accept the UAC prompt."
$rec = Start-Process powershell -ArgumentList $recArgs -WindowStyle Hidden -PassThru
$t = [Diagnostics.Stopwatch]::StartNew()
while (-not ((Test-Path $log) -and (Select-String -Path $log -Pattern 'Profiling process with pid' -Quiet))) {
    if ($rec.HasExited) { Get-Content $log -Tail 20 -ErrorAction SilentlyContinue; throw "Recorder exited before sampling started." }
    if ($t.Elapsed.TotalSeconds -gt 120) { throw "Recorder not sampling after 120 s (UAC prompt not accepted?)." }
    Start-Sleep -Milliseconds 500
}
$startedAt = Get-Date
Write-Host "Recorder sampling."

# 3. Force a domain reload so all C# is re-JITted under the recorder.
if ($Reload) {
    $cs = Join-Path $env:TEMP 'usamply-reload.cs'
    Set-Content -Path $cs -Encoding UTF8 -Value 'UnityEditor.EditorUtility.RequestScriptReload(); return "reload requested";'
    unity command eval_file @common --file $cs | Out-Null
    Start-Sleep -Seconds 2
    Wait-Editor { param($s) $s.status -eq 'ready' -and -not $s.domainReloadInProgress -and -not $s.compiling } 120 'domain reload' | Out-Null
    Write-Host "Domain reload done after $([int]((Get-Date) - $startedAt).TotalSeconds) s."
}

# 4. Play mode, then the workload.
unity command editor_play @common | Out-Null
Wait-Editor { param($s) $s.playMode -eq 'playing' } 60 'Play mode' | Out-Null
Start-Sleep -Seconds 1
if ($Workload) {
    $wl = $Workload -split '\s+' | Where-Object { $_ }
    Write-Host "Workload: unity command $Workload"
    unity command @wl @common
}

# 5. Let the recorder finish.
$rec.WaitForExit()
$used = [int]((Get-Date) - $startedAt).TotalSeconds
if (-not (Test-Path $Out)) { Get-Content $log -Tail 20; throw "No profile written (see $log)." }
$rundown = Select-String -Path $log -Pattern 'Method/DCStopVerbose: (\d+)' | Select-Object -Last 1
$n = if ($rundown) { $rundown.Matches[0].Groups[1].Value } else { 0 }
Write-Host "Done: $Out  (recorded ~$used s, end-rundown method events: $n)"
if (-not $rundown) { Write-Warning "No end-rundown events: C# frames will be unnamed. Is this the mono-fix usamply build? Try -Reload." }
