#!/usr/bin/env pwsh
<#
.SYNOPSIS
    生成测试素材（彩条短片）。需要本机有 ffmpeg。

.DESCRIPTION
    仓库不提交二进制素材，集成测试与 scripts/smoke.py 需要时用本脚本生成。
    详见 tests/fixtures/README.md。

.EXAMPLE
    pwsh -File tests\fixtures\make_fixtures.ps1
#>
[CmdletBinding()]
param(
    [string]$OutputDir
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
if (-not $OutputDir) { $OutputDir = Join-Path $repoRoot 'tests\fixtures\media' }
New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null

$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if (-not $ffmpeg) {
    Write-Host "[fixtures] 找不到 ffmpeg。请先安装 ffmpeg（winget install Gyan.FFmpeg 或 conda install -c conda-forge ffmpeg），" -ForegroundColor Yellow
    Write-Host "[fixtures] 或者手工准备两个 mp4 放到 $OutputDir。集成测试会因此跳过。" -ForegroundColor Yellow
    exit 1
}

function New-BarsClip {
    param([string]$Path, [int]$Width, [int]$Height, [int]$Fps, [int]$Seconds)

    if (Test-Path -LiteralPath $Path) {
        Write-Host "[fixtures] 已存在，跳过：$Path"
        return
    }
    Write-Host "[fixtures] 生成 $Path（${Width}x${Height}@${Fps}, ${Seconds}s）"
    & $ffmpeg.Source -hide_banner -loglevel error -y `
        -f lavfi -i "smptebars=size=${Width}x${Height}:rate=${Fps}:duration=${Seconds}" `
        -f lavfi -i "sine=frequency=1000:duration=${Seconds}" `
        -c:v libx264 -pix_fmt yuv420p -preset veryfast -crf 28 `
        -c:a aac -b:a 96k -shortest $Path
    if ($LASTEXITCODE -ne 0) { throw "ffmpeg 生成失败：$Path" }
}

New-BarsClip -Path (Join-Path $OutputDir 'bars_5s_1080p25.mp4') -Width 1920 -Height 1080 -Fps 25 -Seconds 5
New-BarsClip -Path (Join-Path $OutputDir 'bars_2s_720p25.mp4') -Width 1280 -Height 720 -Fps 25 -Seconds 2

Get-ChildItem -LiteralPath $OutputDir -File |
    Select-Object Name, @{ Name = 'SizeKB'; Expression = { [math]::Round($_.Length / 1KB, 1) } } |
    Format-Table -AutoSize

Write-Host '[fixtures] 完成。'
