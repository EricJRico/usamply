<#
.SYNOPSIS
  Record a CPU profile of a running Unity Editor (or Player) with usamply.

.EXAMPLE
  record.ps1 -ProjectPath C:\Projects\MyGame -Seconds 30 -Out C:\tmp\run1.json
  record.ps1 -ProcessId 12345 -Seconds 20 -Out C:\tmp\player.json
#>
param(
    [string]$ProjectPath,
    [int]$ProcessId,
    [int]$Seconds = 30,
    [Parameter(Mandatory)][string]$Out,
    [string]$Usamply = $env:USAMPLY_EXE,
    [switch]$KeepEtl,
    [string[]]$SymbolDir = @()   # extra PDB folders, e.g. a player's *_BurstDebugInformation_DoNotShip
)

$ErrorActionPreference = 'Stop'

function Resolve-RealDir([string]$dir) {
    # Follow a junction/symlink anywhere up the path (the skill is usually installed as a
    # junction into ~/.claude/skills pointing back into the usamply repo).
    $parts = @()
    $cur = $dir
    while ($cur) {
        $item = Get-Item -LiteralPath $cur -Force -ErrorAction SilentlyContinue
        if ($item -and $item.LinkType -and $item.Target) {
            $target = @($item.Target)[0]
            return (Join-Path $target ($parts -join '\')).TrimEnd('\')
        }
        $parts = ,(Split-Path $cur -Leaf) + $parts
        $cur = Split-Path $cur -Parent
    }
    return $dir
}

if (-not $Usamply) {
    # USAMPLY_EXE, then this repo's release build, then PATH.
    $repoBuild = Join-Path (Resolve-RealDir $PSScriptRoot) '..\..\..\target\release\usamply.exe'
    foreach ($c in @($repoBuild, (Get-Command usamply -ErrorAction SilentlyContinue).Source)) {
        if ($c -and (Test-Path $c)) { $Usamply = (Resolve-Path $c).Path; break }
    }
}
if (-not $Usamply -or -not (Test-Path $Usamply)) { throw "usamply.exe not found. Build it (cargo build --release -p usamply), set USAMPLY_EXE, or pass -Usamply." }

if (-not $ProcessId) {
    if (-not $ProjectPath) { throw "Pass -ProjectPath (Editor) or -ProcessId (Player)." }
    $status = unity status --format json --no-banner | ConvertFrom-Json
    $want = (Resolve-Path $ProjectPath).Path.TrimEnd('\')
    $inst = $status.data.instances | Where-Object { $_.project.TrimEnd('\') -ieq $want } | Select-Object -First 1
    if (-not $inst) { throw "No connected Editor for $want. Open the project, then check 'unity status'." }
    $ProcessId = $inst.pid
}

New-Item -ItemType Directory -Force (Split-Path $Out) | Out-Null
$log = [IO.Path]::ChangeExtension($Out, '.log')
$env:RUST_LOG = 'info'

Write-Host "Recording pid $ProcessId for $Seconds s -> $Out (a UAC prompt will appear; accept it)"
# Run through cmd so usamply's stderr logging isn't turned into PowerShell errors.
# Symbol sources. usamply only looks at the PDB paths baked into each DLL (Unity's point at
# its build machine, Burst's at a deleted Temp folder) and has no default symbol server, so:
#  - Editor folder: ships Unity_x64.pdb, mono-2.0-bdwgc.pdb
#  - Library/BurstCache/JIT: Burst DLLs with their PDBs
#  - Microsoft + Unity symbol servers for everything else
$dirs = @($SymbolDir)
$exe = (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue).Path
if ($exe) { $dirs += Split-Path $exe }
if ($ProjectPath) { $dirs += Join-Path (Resolve-Path $ProjectPath).Path 'Library\BurstCache\JIT' }
$extra = @('--windows-symbol-server', 'https://msdl.microsoft.com/download/symbols',
           '--windows-symbol-server', 'https://symbolserver.unity3d.com/')
foreach ($d in $dirs) { if ($d -and (Test-Path $d)) { $extra += @('--symbol-dir', "`"$d`"") } }
if ($KeepEtl) { $extra += '--keep-etl' }
cmd /c "`"$Usamply`" record -p $ProcessId -d $Seconds --save-only --presymbolicate $($extra -join ' ') -o `"$Out`" > `"$log`" 2>&1"
$code = $LASTEXITCODE

if (-not (Test-Path $Out)) { Get-Content $log -Tail 20; throw "usamply exited $code without writing $Out (see $log)" }
Get-Content $log | Select-String 'DotNETRuntime events|event breakdown' | ForEach-Object { $_.Line }
Write-Host "Done: $Out"
