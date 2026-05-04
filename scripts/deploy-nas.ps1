param(
    [string]$ConfigPath = ".deploy/nas-deploy.json",
    [switch]$Init,
    [switch]$DryRun,
    [switch]$SkipChecks,
    [switch]$SkipReload,
    [switch]$SkipRestart,
    [switch]$Pull,
    [int]$ReloadTimeoutSeconds = 0,
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

function Run-SshScript(
    [string[]]$SshArgumentList,
    [string[]]$ScpArgumentList,
    [string]$RemoteTarget,
    [string]$RemoteScriptPath,
    [string]$Script
) {
    $normalizedScript = $Script -replace "`r`n", "`n" -replace "`r", "`n"
    $localScriptPath = Join-Path ([System.IO.Path]::GetTempPath()) ([System.IO.Path]::GetFileName($RemoteScriptPath))
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($localScriptPath, $normalizedScript, $utf8NoBom)

    try {
        Run "scp" ($ScpArgumentList + @($localScriptPath, "${RemoteTarget}:$RemoteScriptPath"))
        $remoteRunner = "sh $RemoteScriptPath; rc=`$?; rm -f $RemoteScriptPath; exit `$rc"
        Run "ssh" ($SshArgumentList + @($RemoteTarget, $remoteRunner))
    } finally {
        Remove-Item -LiteralPath $localScriptPath -Force -ErrorAction SilentlyContinue
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

function Join-ApiUrl([string]$BaseUrl, [string]$Path) {
    return $BaseUrl.TrimEnd("/") + "/" + $Path.TrimStart("/")
}

function ConvertTo-Md5([string]$Value) {
    $md5 = [System.Security.Cryptography.MD5]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
        $hash = $md5.ComputeHash($bytes)
        return -join ($hash | ForEach-Object { $_.ToString("x2") })
    } finally {
        $md5.Dispose()
    }
}

function Invoke-AstrBotJson([string]$Method, [string]$Url, $Payload, [string]$Token, [int]$TimeoutSeconds) {
    $headers = @{}
    if ($Token) {
        $headers["Authorization"] = "Bearer $Token"
    }
    $body = $null
    if ($null -ne $Payload) {
        $body = ($Payload | ConvertTo-Json -Compress)
    }
    return Invoke-RestMethod -Method $Method -Uri $Url -Headers $headers -ContentType "application/json" -Body $body -TimeoutSec $TimeoutSeconds
}

function Get-AstrBotDashboardToken($Config, [string]$DashboardUrl, [int]$TimeoutSeconds) {
    $configuredToken = Config-Value $Config "dashboardToken"
    if ($configuredToken) {
        return $configuredToken
    }

    $username = Config-Value $Config "dashboardUsername"
    $passwordMd5 = Config-Value $Config "dashboardPasswordMd5"
    $password = Config-Value $Config "dashboardPassword"
    if (-not $username -or (-not $passwordMd5 -and -not $password)) {
        return ""
    }
    if (-not $passwordMd5) {
        if ($password -match "^[0-9a-fA-F]{32}$") {
            $passwordMd5 = $password
        } else {
            $passwordMd5 = ConvertTo-Md5 $password
        }
    }

    $login = Invoke-AstrBotJson "POST" (Join-ApiUrl $DashboardUrl "/api/auth/login") @{
        username = $username
        password = $passwordMd5
    } "" $TimeoutSeconds
    if ($login.status -eq "error") {
        Fail "AstrBot dashboard login failed: $($login.message)"
    }
    return "$($login.data.token)"
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
    Write-Host "Edit it with your NAS host, user, paths, and optional AstrBot dashboard reload settings. It is ignored by Git."
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
$dashboardUrl = Config-Value $config "dashboardUrl"
$registeredPluginName = Config-Value $config "registeredPluginName" "auto_trpg_dm"
$failedPluginDirName = Config-Value $config "failedPluginDirName"
$remotePluginLog = Config-Value $config "remotePluginLog" "/volume1/docker/astrbot/data/plugin_data/astrbot_plugin_auto_trpg_dm/logs/auto_trpg_dm.log"
$configuredReloadTimeoutSeconds = [int](Config-Value $config "reloadTimeoutSeconds" "45")
$keepBackups = [int](Config-Value $config "keepBackups" "10")
$reloadTimeout = if ($ReloadTimeoutSeconds -gt 0) {
    $ReloadTimeoutSeconds
} elseif ($RestartTimeoutSeconds -gt 0) {
    $RestartTimeoutSeconds
} else {
    $configuredReloadTimeoutSeconds
}
$skipReloadEffective = $SkipReload -or $SkipRestart

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
if ($reloadTimeout -lt 1) {
    Fail "reloadTimeoutSeconds must be greater than zero."
}
if (-not $failedPluginDirName) {
    $failedPluginDirName = ($remotePluginDir -split "/")[-1]
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
$remoteDeployScript = "/tmp/paotuan-$commit-$timestamp-deploy.sh"
$remoteVerifyScriptPath = "/tmp/paotuan-$commit-$timestamp-verify.sh"
$remoteReloadVerifyScriptPath = "/tmp/paotuan-$commit-$timestamp-reload-verify.sh"
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
    find "`$remote_dir" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
    mv "`$remote_dir" "`$old"
fi

if ! mv "`$stage/astrbot_plugin_auto_trpg_dm" "`$remote_dir"; then
    if [ -d "`$old" ]; then
        mv "`$old" "`$remote_dir"
    fi
    exit 23
fi

rm -rf "`$stage"
if [ -d "`$old" ]; then
    find "`$old" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
    if ! rm -rf "`$old"; then
        echo "warning: previous plugin directory could not be fully removed: `$old" >&2
    fi
fi
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
Run-SshScript -SshArgumentList $sshArgs -ScpArgumentList $scpArgs -RemoteTarget $remote -RemoteScriptPath $remoteDeployScript -Script $remoteScript

$remoteVerifyScript = @"
set -eu
remote_dir=$qRemotePluginDir
echo "Remote plugin metadata:"
grep '^version:' "`$remote_dir/metadata.yaml"
"@

Run-SshScript -SshArgumentList $sshArgs -ScpArgumentList $scpArgs -RemoteTarget $remote -RemoteScriptPath $remoteVerifyScriptPath -Script $remoteVerifyScript

if (-not $skipReloadEffective -and $dashboardUrl) {
    $token = Get-AstrBotDashboardToken $config $dashboardUrl $reloadTimeout
    if (-not $token) {
        Write-Warning "AstrBot hot reload skipped: configure dashboardToken or dashboardUsername plus dashboardPasswordMd5/dashboardPassword."
    } else {
        Write-Host "AstrBot hot reload: $registeredPluginName via $dashboardUrl"
        $remoteLogOffset = "0"
        if ($remotePluginLog) {
            $qRemotePluginLogForSize = Remote-Quote $remotePluginLog
            $sizeOutput = (& ssh @sshArgs $remote "test -f $qRemotePluginLogForSize && wc -c < $qRemotePluginLogForSize || echo 0")
            if ($LASTEXITCODE -eq 0 -and $sizeOutput) {
                $remoteLogOffset = "$(@($sizeOutput)[-1])".Trim()
            }
            if (-not ($remoteLogOffset -match '^\d+$')) {
                $remoteLogOffset = "0"
            }
        }
        $reload = Invoke-AstrBotJson "POST" (Join-ApiUrl $dashboardUrl "/api/plugin/reload") @{
            name = $registeredPluginName
        } $token $reloadTimeout
        if ($reload.status -eq "error") {
            Write-Warning "Normal plugin reload failed: $($reload.message). Trying failed-plugin reload for $failedPluginDirName."
            $reload = Invoke-AstrBotJson "POST" (Join-ApiUrl $dashboardUrl "/api/plugin/reload-failed") @{
                dir_name = $failedPluginDirName
            } $token $reloadTimeout
            if ($reload.status -eq "error") {
                Fail "AstrBot failed-plugin reload failed: $($reload.message)"
            }
        }

        $expectedVersion = ""
        $metadataPath = Join-Path $repoRoot "astrbot_plugin_auto_trpg_dm/metadata.yaml"
        $versionLine = Select-String -Path $metadataPath -Pattern '^version:\s*(.+)$' | Select-Object -First 1
        if ($versionLine) {
            $expectedVersion = ($versionLine.Matches[0].Groups[1].Value.Trim() -replace '^v', '')
        }
        if ($expectedVersion) {
            $qRemotePluginLog = Remote-Quote $remotePluginLog
            $qExpectedVersion = Remote-Quote $expectedVersion
            $qRemoteLogOffset = Remote-Quote $remoteLogOffset
            $remoteReloadVerifyScript = @"
set -eu
log_path=$qRemotePluginLog
expected=$qExpectedVersion
offset=$qRemoteLogOffset
if [ ! -f "`$log_path" ]; then
    echo "Plugin log not found: `$log_path" >&2
    exit 31
fi
if [ "`$offset" -gt 0 ]; then
    new_log=`$(tail -c +`$((offset + 1)) "`$log_path" 2>/dev/null || true)
else
    new_log=`$(tail -n 300 "`$log_path")
fi
if printf '%s\n' "`$new_log" | grep -F "plugin_initialized version=`$expected" >/dev/null; then
    echo "Plugin log confirmed hot reload: plugin_initialized version=`$expected"
else
    echo "Plugin log did not confirm hot reload version `$expected" >&2
    printf '%s\n' "`$new_log" | tail -n 80 >&2
    exit 32
fi
"@
            Run-SshScript -SshArgumentList $sshArgs -ScpArgumentList $scpArgs -RemoteTarget $remote -RemoteScriptPath $remoteReloadVerifyScriptPath -Script $remoteReloadVerifyScript
        }
    }
} elseif ($skipReloadEffective) {
    Write-Host "AstrBot hot reload skipped."
} else {
    Write-Warning "AstrBot hot reload not configured: set dashboardUrl and dashboard credentials/token in $ConfigPath."
}

Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
Write-Host "NAS deploy finished: $commit"
