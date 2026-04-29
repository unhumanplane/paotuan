param(
    [Parameter(Mandatory = $true)]
    [int]$PrNumber,

    [switch]$Merge,
    [ValidateSet("squash", "merge", "rebase")]
    [string]$MergeMethod = "squash",

    [switch]$DeployAfterMerge,
    [string]$DeployConfig = ".deploy/nas-deploy.json",
    [switch]$SkipChecks
)

$ErrorActionPreference = "Stop"

function Fail([string]$Message) {
    Write-Error $Message
    exit 1
}

function Run([string]$File, [string[]]$Args) {
    Write-Host ">> $File $($Args -join ' ')"
    & $File @Args
    if ($LASTEXITCODE -ne 0) {
        Fail "Command failed with exit code ${LASTEXITCODE}: ${File}"
    }
}

function Require-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Fail "Required command not found: $Name"
    }
}

Require-Command git
Require-Command gh
Require-Command python

$repoRoot = (& git rev-parse --show-toplevel 2>$null)
if ($LASTEXITCODE -ne 0 -or -not $repoRoot) {
    Fail "Run this script inside a Git repository."
}
Set-Location $repoRoot.Trim()

$dirty = (& git status --porcelain --untracked-files=normal)
if ($dirty) {
    Fail "Working tree is not clean. Commit or stash local changes before checking a PR."
}

Run "gh" @("auth", "status")

$prJson = (& gh pr view $PrNumber --json number,title,author,baseRefName,headRefName,isDraft,mergeStateStatus,url)
if ($LASTEXITCODE -ne 0) {
    Fail "Could not load PR #$PrNumber"
}
$pr = $prJson | ConvertFrom-Json

Write-Host "PR #$($pr.number): $($pr.title)"
Write-Host "Author: $($pr.author.login)"
Write-Host "Base: $($pr.baseRefName)  Head: $($pr.headRefName)"
Write-Host "State: $($pr.mergeStateStatus)"
Write-Host $pr.url

if ($pr.isDraft) {
    Fail "PR is still a draft."
}
if ($pr.baseRefName -ne "main") {
    Fail "This helper only handles PRs targeting main."
}

Run "git" @("checkout", "main")
Run "git" @("pull", "--ff-only", "origin", "main")
Run "gh" @("pr", "checkout", "$PrNumber")

if (-not $SkipChecks) {
    Run "python" @("-m", "compileall", "-q", "astrbot_plugin_auto_trpg_dm", "tests")
    & python -c "import pytest" 2>$null
    if ($LASTEXITCODE -eq 0) {
        Run "python" @("-m", "pytest", "-q")
    } else {
        Write-Warning "pytest is not installed; compileall passed, pytest skipped."
    }
}

if (-not $Merge) {
    Write-Host "Checks finished. Review the diff, then rerun with -Merge when ready."
    Write-Host "Useful commands:"
    Write-Host "  git diff main...HEAD"
    Write-Host "  powershell -ExecutionPolicy Bypass -File scripts/handle-pr.ps1 -PrNumber $PrNumber -Merge"
    exit 0
}

Run "git" @("checkout", "main")
Run "git" @("pull", "--ff-only", "origin", "main")

$mergeFlag = "--squash"
if ($MergeMethod -eq "merge") { $mergeFlag = "--merge" }
if ($MergeMethod -eq "rebase") { $mergeFlag = "--rebase" }

Run "gh" @("pr", "merge", "$PrNumber", $mergeFlag, "--delete-branch")
Run "git" @("pull", "--ff-only", "origin", "main")

if ($DeployAfterMerge) {
    Run "powershell" @("-ExecutionPolicy", "Bypass", "-File", "scripts/deploy-nas.ps1", "-ConfigPath", $DeployConfig, "-Pull")
}

Write-Host "PR #$PrNumber handled successfully."
