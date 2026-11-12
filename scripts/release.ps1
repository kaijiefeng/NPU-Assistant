<#
.SYNOPSIS
    发布 NPU-Assistant：校验、改版本号、跑测试、提交、打 tag、推送。

.DESCRIPTION
    tag 推送到 GitHub 后，.github/workflows/release.yml 会自动读取
    CHANGELOG.md 的对应章节并创建 GitHub Release。

    前置条件：
      1. 当前分支工作区干净；
      2. CHANGELOG.md 已写好 "## vX.Y.Z" 发布章节并已提交；
      3. backend/app/config.py 的 APP_VERSION 可以落后于目标版本（脚本会改）。

.EXAMPLE
    .\scripts\release.ps1 -Version 0.2.1 -Branch release

.EXAMPLE
    .\scripts\release.ps1 -Version 0.2.1 -DryRun
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Version,

    [string]$Branch,

    [switch]$SkipTests,
    [switch]$SkipPush,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

$Version = $Version.Trim().TrimStart('v')
if ($Version -notmatch '^\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$') {
    throw "版本号必须形如 0.2.1 或 0.2.1-rc1，当前为：$Version"
}
$tag = "v$Version"

$configPath = Join-Path $repo 'backend/app/config.py'
$changelogPath = Join-Path $repo 'CHANGELOG.md'
$notesScript = Join-Path $repo 'scripts/release_notes.py'

$pythonCandidates = @(
    (Join-Path $repo '.venv/Scripts/python.exe'),
    (Join-Path $repo '.venv/bin/python')
)
$python = $pythonCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $python) { $python = 'python' }

# Windows PowerShell 5.1 会把原生命令的 stderr 包成 NativeCommandError；调用方一旦
# 重定向输出（2>&1 / Tee / 写日志），$ErrorActionPreference='Stop' 会让 git 正常的
# 进度信息（如 push 的 "To ..."）变成终止错误。这里只在执行 git 期间放宽，成败仍按
# $LASTEXITCODE 判定。
function Invoke-Native {
    param([Parameter(Mandatory = $true)][scriptblock]$Command)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $Command
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    return @{ Output = $output; ExitCode = $exitCode }
}

function Invoke-Mutation {
    param(
        [Parameter(Mandatory = $true)][string]$Description,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )
    if ($DryRun) {
        Write-Host "[dry-run] $Description" -ForegroundColor DarkYellow
        return
    }
    $result = Invoke-Native -Command $Action
    if ($result.ExitCode -ne 0) {
        throw "$Description 失败（exit code $($result.ExitCode)）"
    }
}

# python / pytest 也会往 stderr 写内容（警告、错误提示）；同样在重定向场景下会被
# 5.1 当成终止错误，因此统一走这里：保留实时输出，只按退出码判定。
# 注意输出必须交给 Out-Host，不能留在函数管道里，否则会和返回的退出码一起变成
# Object[]，把「成功（退出码 0）」误判成失败。
function Invoke-External {
    param([Parameter(Mandatory = $true)][scriptblock]$Command)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Command 2>&1 | Out-Host
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
    return $exitCode
}

function Get-GitValue {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $result = Invoke-Native -Command { git @Arguments }
    if ($result.ExitCode -ne 0) {
        throw "git $($Arguments -join ' ') 失败（exit code $($result.ExitCode)）：$($result.Output)"
    }
    return (($result.Output) | Out-String).Trim()
}

function Invoke-GitProbe {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $result = Invoke-Native -Command { git @Arguments 2>&1 }
    return @{
        Output   = (($result.Output) | Out-String).Trim()
        ExitCode = $result.ExitCode
    }
}

Write-Host "==> 发布 $tag" -ForegroundColor Cyan

# 1. 基本检查 ---------------------------------------------------------------
$dirty = Get-GitValue -Arguments @('status', '--porcelain')
if ($dirty) {
    if (-not $DryRun) {
        throw "工作区不干净，请先提交或暂存改动：`n$dirty"
    }
    Write-Warning "工作区有未提交改动；dry-run 不会提交或打 tag，继续检查。"
}

$currentBranch = Get-GitValue -Arguments @('rev-parse', '--abbrev-ref', 'HEAD')
if ($Branch -and $currentBranch -ne $Branch) {
    throw "当前分支是 $currentBranch，不是指定的 $Branch"
}
if ($currentBranch -notin @('release', 'master')) {
    Write-Warning "当前分支是 $currentBranch；正式发布建议在 release 分支进行。"
}

# 先与远端同步：远端已前进时尽早失败，避免本地 commit/tag 之后才在 push 处失败。
$fetchProbe = Invoke-GitProbe -Arguments @('fetch', '--quiet', 'origin')
if ($fetchProbe.ExitCode -ne 0) {
    throw "无法访问 origin（git fetch 退出码 $($fetchProbe.ExitCode)）：$($fetchProbe.Output)"
}
$upstreamProbe = Invoke-GitProbe -Arguments @('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}')
if ($upstreamProbe.ExitCode -eq 0 -and $upstreamProbe.Output) {
    $behindProbe = Invoke-GitProbe -Arguments @('rev-list', '--count', "HEAD..$($upstreamProbe.Output)")
    if ($behindProbe.ExitCode -eq 0 -and [int]$behindProbe.Output -gt 0) {
        throw "本地分支落后 $($upstreamProbe.Output) $($behindProbe.Output) 个提交，请先 git pull --ff-only"
    }
}

$localTagProbe = Invoke-GitProbe -Arguments @('rev-parse', '-q', '--verify', "refs/tags/$tag")
if ($localTagProbe.ExitCode -eq 0 -and $localTagProbe.Output) {
    throw "本地已存在 tag $tag；如需重发，请在 Actions 页面手动运行 Release 工作流。"
}
$remoteProbe = Invoke-GitProbe -Arguments @('ls-remote', '--tags', 'origin', "refs/tags/$tag")
if ($remoteProbe.ExitCode -ne 0) {
    throw "无法访问 origin（git ls-remote 退出码 $($remoteProbe.ExitCode)）：$($remoteProbe.Output)"
}
if ($remoteProbe.Output) {
    throw "远程已存在 tag $tag；如需补发 release，请在 Actions 页面手动运行 Release 工作流。"
}

# 2. 发布说明检查（tag 推送后工作流会读取该章节） -------------------------
$notesCode = Invoke-External -Command { & $python $notesScript --version $tag --check }
if ($notesCode -ne 0) {
    throw "CHANGELOG.md 缺少 $tag 的发布章节。请先补写并提交 '## $tag — YYYY-MM-DD（主题）'。"
}

# 3. 更新 APP_VERSION -------------------------------------------------------
$configText = [System.IO.File]::ReadAllText($configPath)
$versionPattern = '(?m)^APP_VERSION\s*=\s*"[^"]*"'
$newConfigText = [System.Text.RegularExpressions.Regex]::Replace(
    $configText,
    $versionPattern,
    "APP_VERSION = `"$Version`""
)
if ($newConfigText -ne $configText) {
    if ($DryRun) {
        Write-Host "[dry-run] 将 backend/app/config.py 的 APP_VERSION 改为 $Version" -ForegroundColor DarkYellow
    }
    else {
        [System.IO.File]::WriteAllText(
            $configPath,
            $newConfigText,
            [System.Text.UTF8Encoding]::new($false)
        )
        Write-Host "已更新 APP_VERSION -> $Version"
    }
}
else {
    Write-Host "APP_VERSION 已是 $Version，跳过修改"
}

# 4. 测试 -------------------------------------------------------------------
if (-not $SkipTests) {
    Write-Host "==> 运行后端全量测试" -ForegroundColor Cyan
    Push-Location (Join-Path $repo 'backend')
    try {
        $testCode = Invoke-External -Command { & $python -m pytest -q }
        if ($testCode -ne 0) {
            throw "测试未通过，已中止发布（可用 -SkipTests 跳过，不推荐）"
        }
    }
    finally {
        Pop-Location
    }
}
else {
    Write-Warning "已跳过测试"
}

# 5. 提交、打 tag、推送 -----------------------------------------------------
Invoke-Mutation -Description "git add backend/app/config.py" -Action {
    git add backend/app/config.py
}

$staged = Get-GitValue -Arguments @('diff', '--cached', '--name-only')
if ($staged) {
    Invoke-Mutation -Description "git commit -m 'chore: release $tag'" -Action {
        git commit -m "chore: release $tag"
    }
}
else {
    Write-Host "没有需要提交的版本号改动"
}

Invoke-Mutation -Description "git tag -a $tag -m 'release $tag'" -Action {
    git tag -a $tag -m "release $tag"
}

if ($SkipPush) {
    Write-Warning "已跳过推送；手动执行 'git push origin HEAD && git push origin $tag' 后才会触发 Release 工作流。"
}
else {
    Invoke-Mutation -Description "git push --atomic origin HEAD $tag" -Action {
        git push --atomic --quiet origin HEAD $tag
    }
}

# 6. 结果提示 ---------------------------------------------------------------
$origin = Get-GitValue -Arguments @('remote', 'get-url', 'origin')
$slug = $null
if ($origin -match '^git@github\.com:(?<slug>[^/]+/[^/]+?)(?:\.git)?$') {
    $slug = $Matches['slug']
}
elseif ($origin -match '^https?://github\.com/(?<slug>[^/]+/[^/]+?)(?:\.git)?$') {
    $slug = $Matches['slug']
}

Write-Host ""
Write-Host "==> 完成" -ForegroundColor Green
if ($slug) {
    Write-Host "Release: https://github.com/$slug/releases/tag/$tag"
    Write-Host "Actions: https://github.com/$slug/actions/workflows/release.yml"
    Write-Host "提示：workflow 需要仓库 Settings → Actions → Workflow permissions 允许 Read and write。"
}
else {
    Write-Host "origin 不是 GitHub（$origin），跳过 Release 页面链接提示。"
}
if ($DryRun) {
    Write-Host "当前为 dry-run，未修改任何文件、未创建 tag、未推送。" -ForegroundColor DarkYellow
}
