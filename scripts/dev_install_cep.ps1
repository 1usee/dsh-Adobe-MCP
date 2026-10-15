<#
.SYNOPSIS
    把 cep-extension/ 部署到用户级 CEP 扩展目录（说明书 §4.2 的手动退路）。

.DESCRIPTION
    与 src/dsh_adobe_mcp/bridge/deployer.py 的逻辑一致，但完全不依赖 Python 环境，
    因此在"DSh 文件沙箱禁止 MCP 服务器写入 %APPDATA%"或 Python 环境损坏时仍可使用。

    部署策略：
      1. 校验 CSXS/manifest.xml 的必需元素（含 --enable-nodejs 与 --mixed-context）；
      2. 比较源与目标的清单哈希（文件名 + 大小 + 修改时间），一致则跳过；
      3. 不一致时复制到临时目录 → 校验完整性 → 备份旧版本 → 原子替换；
      4. 失败时自动还原上一版本；保留最近 2 个备份。

.PARAMETER Force
    忽略哈希比较，强制重新部署。

.PARAMETER SetPlayerDebugMode
    同时设置 HKCU\Software\Adobe\CSXS.<n>\PlayerDebugMode = "1"。
    这是 Adobe 官方给扩展开发者用来加载**未签名**扩展的标准设置，只影响扩展签名校验，
    与宿主软件的授权无关（说明书 §1.3）。

.EXAMPLE
    pwsh -File scripts\dev_install_cep.ps1
    pwsh -File scripts\dev_install_cep.ps1 -Force -SetPlayerDebugMode
#>
[CmdletBinding()]
param(
    [string]$SourceDir,
    [string]$ExtensionsDir,
    [string]$ExtensionId = 'com.dsh.adobe.bridge',
    [switch]$Force,
    [switch]$SetPlayerDebugMode,
    [switch]$Rollback
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Get-RepoRoot {
    return (Split-Path -Parent $PSScriptRoot)
}

if (-not $SourceDir) { $SourceDir = Join-Path (Get-RepoRoot) 'cep-extension' }
if (-not $ExtensionsDir) { $ExtensionsDir = Join-Path $env:APPDATA 'Adobe\CEP\extensions' }
$SourceDir = (Resolve-Path -LiteralPath $SourceDir).Path
$TargetDir = Join-Path $ExtensionsDir $ExtensionId
$BackupRoot = Join-Path $env:LOCALAPPDATA "dsh-adobe-mcp\cep-backup"

function Write-Step([string]$Message) { Write-Host "[dsh-adobe-mcp] $Message" }
function Write-Fail([string]$Message) { Write-Host "[dsh-adobe-mcp] 失败：$Message" -ForegroundColor Red; exit 1 }

# ---------------------------------------------------------------- manifest 校验
function Test-Manifest {
    param([string]$ManifestPath)

    if (-not (Test-Path -LiteralPath $ManifestPath)) {
        Write-Fail "manifest 不存在：$ManifestPath"
    }
    try {
        [xml]$manifest = Get-Content -LiteralPath $ManifestPath -Raw -Encoding UTF8
    } catch {
        Write-Fail "manifest 不是合法 XML：$($_.Exception.Message)"
    }

    $problems = New-Object System.Collections.Generic.List[string]
    $root = $manifest.ExtensionManifest
    if (-not $root) { $problems.Add('根元素应为 ExtensionManifest') } 
    if (-not $manifest.ExtensionManifest.ExtensionBundleId) { $problems.Add('缺少 ExtensionBundleId') }
    if (-not $manifest.ExtensionManifest.ExtensionBundleVersion) { $problems.Add('缺少 ExtensionBundleVersion') }

    $hostNames = @($manifest.SelectNodes('//Host') | ForEach-Object { $_.Name })
    if ($hostNames -notcontains 'PPRO') { $problems.Add('HostList 中没有 PPRO 宿主') }

    $csxs = @($manifest.SelectNodes('//Runtime') | Where-Object { $_.Name -eq 'CSXS' })
    if ($csxs.Count -eq 0) { $problems.Add('RequiredRuntime 中没有声明 CSXS 运行时') }

    if (-not $manifest.ExtensionManifest.MainPath) { $problems.Add('缺少 MainPath') }

    $parameters = @($manifest.SelectNodes('//CEFCommandLine/Parameter') | ForEach-Object { $_.'#text' })
    if ($parameters -notcontains '--enable-nodejs') { $problems.Add('CEFCommandLine 缺少 --enable-nodejs') }
    if ($parameters -notcontains '--mixed-context') {
        $problems.Add("CEFCommandLine 缺少 --mixed-context（这是面板里 'require is not defined' 的最常见根因）")
    }

    $uiType = @($manifest.SelectNodes('//UI/Type') | ForEach-Object { $_.'#text' })
    if ($uiType -notcontains 'Panel') { $problems.Add('UI/Type 应为 Panel') }

    $geometrySize = @($manifest.SelectNodes('//Geometry/Size'))
    if ($geometrySize.Count -eq 0) { $problems.Add('缺少 Geometry/Size 初始尺寸（非阻断）') -as [void] }

    if ($problems.Count -gt 0) {
        foreach ($problem in $problems) { Write-Host "  - $problem" -ForegroundColor Yellow }
        $blocking = $problems | Where-Object { $_ -notmatch '非阻断' }
        if ($blocking) { Write-Fail 'manifest 必需元素缺失，已拒绝部署' }
    }
    Write-Step "manifest 校验通过（BundleId=$($manifest.ExtensionManifest.ExtensionBundleId)）"
}

# ---------------------------------------------------------------- 清单哈希
function Get-ManifestFingerprint {
    param([string]$Root)

    if (-not (Test-Path -LiteralPath $Root)) { return '' }
    $lines = New-Object System.Collections.Generic.List[string]
    Get-ChildItem -LiteralPath $Root -Recurse -File -Force |
        Where-Object { $_.FullName -notmatch '\\(\.git|__pycache__)\\' -and $_.Name -notin @('.debug') } |
        Sort-Object FullName |
        ForEach-Object {
            $relative = $_.FullName.Substring($Root.Length).TrimStart('\')
            $lines.Add("$relative|$($_.Length)|$($_.LastWriteTimeUtc.Ticks)")
        }
    $joined = ($lines -join "`n")
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($joined)
    return ([System.BitConverter]::ToString($sha.ComputeHash($bytes)) -replace '-', '').ToLowerInvariant()
}

# ---------------------------------------------------------------- 主流程
Write-Step "源目录：    $SourceDir"
Write-Step "扩展目录：  $ExtensionsDir"
Write-Step "目标目录：  $TargetDir"

Test-Manifest -ManifestPath (Join-Path $SourceDir 'CSXS\manifest.xml')

if (-not (Test-Path -LiteralPath $ExtensionsDir)) {
    New-Item -ItemType Directory -Path $ExtensionsDir -Force | Out-Null
}

# 可写性探测（给出比"权限不足"更有用的提示）
$probe = Join-Path $ExtensionsDir ".write-probe-$PID"
try {
    Set-Content -LiteralPath $probe -Value '' -Encoding UTF8
    Remove-Item -LiteralPath $probe -Force
} catch {
    Write-Fail "扩展目录不可写：$ExtensionsDir（$($_.Exception.Message)）。请以当前用户身份重试，避免使用提权/只读挂载。"
}

if ($Rollback) {
    $backups = @(Get-ChildItem -LiteralPath $BackupRoot -Directory -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending)
    if ($backups.Count -eq 0) { Write-Fail "没有可用的备份目录：$BackupRoot" }
    $latest = $backups[0].FullName
    if (Test-Path -LiteralPath $TargetDir) { Remove-Item -LiteralPath $TargetDir -Recurse -Force }
    Move-Item -LiteralPath $latest -Destination $TargetDir
    Write-Step "已回滚到备份：$latest"
    exit 0
}

$sourcePrint = Get-ManifestFingerprint -Root $SourceDir
$targetPrint = Get-ManifestFingerprint -Root $TargetDir

if (-not $Force -and $sourcePrint -and $sourcePrint -eq $targetPrint) {
    Write-Step '源与目标的清单哈希一致，无需重新部署（需要强制时加 -Force）'
} else {
    if (-not $targetPrint) { Write-Step '目标目录不存在，执行首次安装' }
    else { Write-Step '源与目标不一致，整体替换' }

    $staging = Join-Path $ExtensionsDir ".$ExtensionId.staging-$PID"
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    Copy-Item -LiteralPath $SourceDir -Destination $staging -Recurse -Force

    $stagedPrint = Get-ManifestFingerprint -Root $staging
    if ($stagedPrint -ne $sourcePrint) {
        Remove-Item -LiteralPath $staging -Recurse -Force
        Write-Fail '临时目录的清单哈希与源不一致（复制不完整），已中止，目标目录未改动'
    }

    $backupPath = $null
    if (Test-Path -LiteralPath $TargetDir) {
        $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
        $backupPath = Join-Path $BackupRoot "$ExtensionId-$stamp"
        New-Item -ItemType Directory -Path $BackupRoot -Force | Out-Null
        Move-Item -LiteralPath $TargetDir -Destination $backupPath
    }

    try {
        Move-Item -LiteralPath $staging -Destination $TargetDir
    } catch {
        if ($backupPath -and (Test-Path -LiteralPath $backupPath)) {
            if (Test-Path -LiteralPath $TargetDir) { Remove-Item -LiteralPath $TargetDir -Recurse -Force }
            Move-Item -LiteralPath $backupPath -Destination $TargetDir
            Write-Step '替换失败，已自动还原上一版本'
        }
        Write-Fail "部署失败：$($_.Exception.Message)"
    }

    # 只保留最近 2 个备份
    $keep = @(Get-ChildItem -LiteralPath $BackupRoot -Directory -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -Skip 2)
    foreach ($stale in $keep) { Remove-Item -LiteralPath $stale.FullName -Recurse -Force }

    Write-Step "部署完成（$((Get-ChildItem -LiteralPath $TargetDir -Recurse -File).Count) 个文件）"
}

if ($SetPlayerDebugMode) {
    foreach ($version in 9, 10, 11, 12) {
        $key = "HKCU:\Software\Adobe\CSXS.$version"
        try {
            New-Item -Path $key -Force | Out-Null
            New-ItemProperty -Path $key -Name 'PlayerDebugMode' -Value '1' -PropertyType String -Force | Out-Null
            Write-Step "已设置 $key\PlayerDebugMode = 1"
        } catch {
            Write-Host "[dsh-adobe-mcp] 警告：无法设置 $key（$($_.Exception.Message)）" -ForegroundColor Yellow
        }
    }
    Write-Step 'PlayerDebugMode 只为加载未签名扩展；本项目不读取、不判断、不干预任何授权状态。'
}

Write-Step '完成后需要**重启 Premiere Pro** 才会加载新面板。'
exit 0
