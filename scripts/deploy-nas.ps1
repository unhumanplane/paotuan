param(
    [string]$ConfigPath = ".deploy/nas-deploy.json",
    [switch]$Init,
    [switch]$DryRun,
    [switch]$SkipChecks,
    [switch]$SkipRestart,
    [switch]$Pull,
    [int]$RestartTimeoutSeconds = 0
)

$ErrorActionPreference = "Stop"

function Fail([string]$Message) {
    Write-Error $Message
    exit 1
}

function Run([string]$File, [string[]]$ArgumentList) {
    Write-Host ">> $File $($ArgumentList -join ' ')"
    & $File @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        Fail "Command failed with exit code ${LASTEXITCODE}: ${File}"
    }
}

function Require-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Fail "Required command not found: $Name"
    }
}

function Repo-Root {
    $root = (& git rev-parse --show-toplevel 2>$null)
    if ($LASTEXITCODE -ne 0 -or -not $root) {
        Fail "Run this script inside a Git repository."
    }
    return $root.Trim()
}

function Resolve-RepoPath([string]$Root, [string]$Path) {
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return (Join-Path $Root $Path)
}

function Remote-Quote([string]$Value) {
    if ($Value -match "[`r`n']") {
        Fail "Remote values must not contain quotes or newlines: $Value"
    }
    return "'" + $Value + "'"
}

function Config-Value($Config, [string]$Name, [string]$Default = "") {
    if ($Config.PSObject.Properties.Name -contains $Name) {
        $value = $Config.$Name
        if ($null -ne $value -and "$value" -ne "") {
            return "$value"
        }
    }
    return $Default
}

Require-Command git
Require-Command ssh
Require-Command scp
Require-Command python

$repoRoot = Repo-Root
Set-Location $repoRoot

$examplePath = Join-Path $repoRoot "scripts/nas-deploy.example.json"
$configFullPath = Resolve-RepoPath $repoRoot $ConfigPath

if ($Init) {
    if (Test-Path $configFullPath) {
        Fail "Config already exists: $configFullPath"
    }
    $configDir = Split-Path -Parent $configFullPath
    if ($configDir -and -not (Test-Path $configDir)) {
        New-Item -ItemType Directory -Force -Path $configDir | Out-Null
    }
    Copy-Item $examplePath $configFullPath
    Write-Host "Created local config: $configFullPath"
    Write-Host "Edit it with your NAS host, user, paths, and restart command. It is ignored by Git."
    exit 0
}

if (-not (Test-Path $configFullPath)) {
    Fail "Missing deploy config: $configFullPath. Run: powershell -ExecutionPolicy Bypass -File scripts/deploy-nas.ps1 -Init"
}

$config = Get-Content -Raw -Encoding UTF8 $configFullPath | ConvertFrom-Json

$hostName = Config-Value $config "host"
$userName = Config-Value $config "user"
$port = [int](Config-Value $config "port" "22")
$identityFile = Resolve-RepoPath $repoRoot (Config-Value $config "identityFile")
$remotePluginDir = Config-Value $config "remotePluginDir"
$remoteBackupDir = Config-Value $config "remoteBackupDir"
$restartCommand = Config-Value $config "restartCommand"
$configuredRestartTimeoutSeconds = [int](Config-Value $config "restartTimeoutSeconds" "120")
$keepBackups = [int](Config-Value $config "keepBackups" "10")
$restartTimeout = if ($RestartTimeoutSeconds -gt 0) {
    $RestartTimeoutSeconds
} else {
    $configuredRestartTimeoutSeconds
}

if (-not $hostName) { Fail "Config field is required: host" }
if (-not $userName) { Fail "Config field is required: user" }
if (-not (Test-Path $identityFile)) { Fail "SSH identity file not found: $identityFile" }
if (-not $remotePluginDir) { Fail "Config field is required: remotePluginDir" }
if (-not $remoteBackupDir) { Fail "Config field is required: remoteBackupDir" }
if ($remotePluginDir -notmatch "^/") {
    Fail "remotePluginDir must be an absolute Linux path on the NAS."
}
if ($remotePluginDir -match "/$") {
    Fail "remotePluginDir must not end with a trailing slash."
}
if ($restartTimeout -lt 1) {
    Fail "restartTimeoutSeconds must be greater than zero."
}

$sshOptions = @()
if ($config.PSObject.Properties.Name -contains "sshOptions" -and $config.sshOptions) {
    $sshOptions += @($config.sshOptions)
}

$dirty = (& git status --porcelain --untracked-files=normal)
if ($dirty) {
    Fail "Working tree is not clean. Commit or stash changes before deploying so NAS receives an auditable commit."
}

if ($Pull) {
    Run "git" @("fetch", "origin", "main")
    Run "git" @("pull", "--ff-only", "origin", "main")
}

if (-not $SkipChecks) {
    Run "python" @("-m", "compileall", "-q", "astrbot_plugin_auto_trpg_dm", "tests")
    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & python -c "import importlib.util, sys; sys.exit(0 if importlib.util.find_spec('pytest') else 1)" *> $null
    $pytestAvailable = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $previousErrorActionPreference
    if ($pytestAvailable) {
        Run "python" @("-m", "pytest", "-q")
    } else {
        Write-Warning "pytest is not installed; compileall passed, pytest skipped."
    }
}

$commit = (& git rev-parse --short=12 HEAD).Trim()
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$archive = Join-Path ([System.IO.Path]::GetTempPath()) "paotuan-$commit-$timestamp.tar"
$remoteArchive = "/tmp/paotuan-$commit-$timestamp.tar"
$remote = "$userName@$hostName"

Run "git" @("archive", "--format=tar", "--output", $archive, "HEAD", "astrbot_plugin_auto_trpg_dm")

$sshArgs = @("-p", "$port", "-i", $identityFile) + $sshOptions
$scpArgs = @("-O", "-P", "$port", "-i", $identityFile) + $sshOptions

$qRemotePluginDir = Remote-Quote $remotePluginDir
$qRemoteBackupDir = Remote-Quote $remoteBackupDir
$qRemoteArchive = Remote-Quote $remoteArchive
$qCommit = Remote-Quote $commit
$qTimestamp = Remote-Quote $timestamp
$keep = [Math]::Max(1, $keepBackups)

$remoteScript = @"
set -eu
remote_dir=$qRemotePluginDir
backup_dir=$qRemoteBackupDir
archive=$qRemoteArchive
commit=$qCommit
stamp=$qTimestamp
keep=$keep
parent=`$(dirname "`$remote_dir")
name=`$(basename "`$remote_dir")
stage="`$parent/.`$name.deploy.`$stamp"
old="`$parent/.`$name.previous.`$stamp"

mkdir -p "`$parent" "`$backup_dir"
rm -rf "`$stage"
mkdir -p "`$stage"
tar -xf "`$archive" -C "`$stage"
test -f "`$stage/astrbot_plugin_auto_trpg_dm/main.py"
test -f "`$stage/astrbot_plugin_auto_trpg_dm/metadata.yaml"

if [ -d "`$remote_dir" ]; then
    tar -czf "`$backup_dir/`$name.`$stamp.`$commit.tgz" -C "`$parent" "`$name"
    mv "`$remote_dir" "`$old"
fi

if ! mv "`$stage/astrbot_plugin_auto_trpg_dm" "`$remote_dir"; then
    if [ -d "`$old" ]; then
        mv "`$old" "`$remote_dir"
    fi
    exit 23
fi

rm -rf "`$stage" "`$old"
rm -f "`$archive"

count=0
for file in `$(ls -1t "`$backup_dir"/"`$name".*.tgz 2>/dev/null || true); do
    count=`$((count + 1))
    if [ "`$count" -gt "`$keep" ]; then
        rm -f "`$file"
    fi
done

echo "deployed `$name at commit `$commit"
"@

Write-Host "Deploy target: ${remote}:$remotePluginDir"
Write-Host "Commit: $commit"

if ($DryRun) {
    Write-Host "Dry run only. Archive prepared at: $archive"
    Write-Host "Remote archive would be: $remoteArchive"
    exit 0
}

Run "scp" ($scpArgs + @($archive, "${remote}:$remoteArchive"))
Run "ssh" ($sshArgs + @($remote, $remoteScript))

$remoteVerifyScript = @"
set -eu
remote_dir=$qRemotePluginDir
echo "Remote plugin metadata:"
grep '^version:' "`$remote_dir/metadata.yaml"
"@

Run "ssh" ($sshArgs + @($remote, $remoteVerifyScript))

if ($restartCommand -and -not $SkipRestart) {
    $qRestartCommand = Remote-Quote $restartCommand
    $remoteRestartScript = @"
set -eu
restart_command=$qRestartCommand
timeout_seconds=$restartTimeout
echo "Restart command timeout: `$timeout_seconds seconds"
if command -v timeout >/dev/null 2>&1; then
    set +e
    timeout -s KILL "`$timeout_seconds" sh -lc "`$restart_command"
    rc=`$?
    set -e
    if [ "`$rc" -eq 124 ] || [ "`$rc" -eq 137 ]; then
        echo "Restart command timed out after `$timeout_seconds seconds." >&2
        exit "`$rc"
    fi
    exit "`$rc"
fi

echo "Warning: remote timeout command not found; running restart command without timeout." >&2
sh -lc "`$restart_command"
"@

    Write-Host "Restart command: $restartCommand"
    Run "ssh" ($sshArgs + @($remote, $remoteRestartScript))
} elseif ($SkipRestart) {
    Write-Host "Restart skipped."
} else {
    Write-Host "No restartCommand configured."
}

Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
Write-Host "NAS deploy finished: $commit"
