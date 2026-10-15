<#
.SYNOPSIS
    验证 MCP 接入：用真实 stdio 传输启动服务器，完成握手并列出工具（说明书 M0 验收）。

.DESCRIPTION
    本脚本对应验收标准第 1 条："DSH 能通过 stdio 接入该 MCP 服务器，工具可被正常发现与调用"。
    它启动的是**出厂形态**（`python -m dsh_adobe_mcp`），用 JSON-RPC 发 initialize /
    tools/list，然后断言工具数量与关键工具是否存在。

    为什么用文件而不是管道：本项目的开发环境禁止对子进程使用管道 stdio（WinError 5），
    因此请求与响应都走临时文件重定向。脚本退出码即结论（0 = 通过）。

.PARAMETER Groups
    启用的工具组（逗号分隔），用于验证分段暴露。缺省用配置默认值（45 个工具）。

.PARAMETER Python
    Python 解释器路径（缺省用本环境已选定的 dsh-adobe-mcp 环境）。

.EXAMPLE
    pwsh -File scripts\verify_mcp_stdio.ps1
    pwsh -File scripts\verify_mcp_stdio.ps1 -Groups core      # 期望 14 个工具
#>
[CmdletBinding()]
param(
    [string]$Groups,
    [string]$Python = 'D:\Conda_envs\dsh-adobe-mcp\python.exe'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$work = Join-Path $repoRoot '.tmp2\mcp-verify'
New-Item -ItemType Directory -Path $work -Force | Out-Null

$requestFile = Join-Path $work 'request.jsonl'
$outputFile = Join-Path $work 'response.jsonl'
$errorFile = Join-Path $work 'stderr.txt'

# 先清掉上一次的响应，避免读到陈旧文件得出错误结论
Remove-Item -LiteralPath $outputFile, $errorFile -Force -ErrorAction SilentlyContinue

$requests = @(
    '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"verify-mcp-stdio","version":"1"}}}'
    '{"jsonrpc":"2.0","method":"notifications/initialized"}'
    '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
)
Set-Content -LiteralPath $requestFile -Value $requests -Encoding utf8

$env:PYTHONPATH = Join-Path $repoRoot 'src'
$env:DSH_ADOBE_MCP__SERVER__LOG_DIR = Join-Path $work 'logs'
$env:DSH_ADOBE_MCP__SAFETY__CHECKPOINT_DIR = Join-Path $work 'checkpoints'
$env:DSH_ADOBE_MCP__JOBS__STATE_DIR = Join-Path $work 'jobs'
$env:DSH_ADOBE_MCP__BRIDGE__HANDSHAKE_DIR = Join-Path $work 'handshake'
$env:DSH_ADOBE_MCP__HOSTS__PREMIERE__LAUNCH_ON_DEMAND = 'false'
if ($Groups) { $env:DSH_ADOBE_MCP__TOOLS__ENABLED_GROUPS = $Groups }

Get-Content -LiteralPath $requestFile | & $Python -m dsh_adobe_mcp 2> $errorFile | Set-Content -LiteralPath $outputFile -Encoding utf8
$exitCode = $LASTEXITCODE

if ($exitCode -ne 0) {
    Write-Host "[FAIL] 服务器进程退出码 $exitCode" -ForegroundColor Red
    Get-Content -LiteralPath $errorFile -ErrorAction SilentlyContinue | Select-Object -Last 20
    exit 1
}

$checker = Join-Path $PSScriptRoot 'check_mcp_handshake.py'
$checkArgs = @($checker, $outputFile)
if ($Groups) { $checkArgs += @('--groups', $Groups) }
& $Python @checkArgs
$checkExit = $LASTEXITCODE

if ($checkExit -eq 0) {
    Write-Host '[ OK ] MCP 接入验证通过（握手 + 工具发现）' -ForegroundColor Green
} else {
    Write-Host '[FAIL] MCP 接入验证未通过' -ForegroundColor Red
    Get-Content -LiteralPath $errorFile -ErrorAction SilentlyContinue | Select-Object -Last 10
}
exit $checkExit
