# =====================================================================
# tests/l3/panel/verify.ps1
# ---------------------------------------------------------------------
# L3 面板（宿主内代理层 Node 侧）五组验证的一键入口。
#
# 依次执行：
#   V1  tests/l3/panel/run.js           面板 Node 侧自测（153 项）
#   V2  cep-extension/js/vendor/ws/selftest.js   mini-ws 自测（17 项）
#   V3  每个 .js 的 --check             语法检查
#   V4  CSXS/manifest.xml               合法 XML + 设计说明书 §4.3 必需元素自查
#   V5  index.html                      引用的每个脚本路径真实存在
#
# 运行：
#   pwsh -File tests\l3\panel\verify.ps1
#
# 为什么必须重定向到文件（本机实测）：
#   本机没有 node.exe，用 DSH 自带的 Electron 以 ELECTRON_RUN_AS_NODE=1 当 Node 用。
#   该二进制在「把子进程 stdout 捕获进 PowerShell 变量」时会被提前终止（约 100ms，
#   $LASTEXITCODE 为空），长时间运行的脚本会只输出前几行。重定向到文件则正常。
#   又因为它是 GUI 子系统程序，-PassThru 的 ExitCode 也可能为空，所以面板自测
#   另外把 exit_code 写进 tests/l3/panel/last-run.json 供独立核对。
# =====================================================================

$ErrorActionPreference = "Continue"

$exe = "F:\DeepseekHarness\DeepSeek Harness.exe"
$py  = "D:\Conda_envs\dsh-adobe-mcp\python.exe"
$env:ELECTRON_RUN_AS_NODE = "1"

# 仓库根：本文件在 tests/l3/panel/ 下，往上三级
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")).Path
Set-Location $root

$tmp = Join-Path ([System.IO.Path]::GetTempPath()) ("dsh-l3-verify-" + $PID)
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$log = Join-Path $tmp "verify.log"

function Write-Log([string]$line) {
  Add-Content -Path $log -Value $line -Encoding utf8
  Write-Host $line
}

Write-Log "root = $root"
Write-Log "exe  = $exe"
Write-Log "log  = $log"
Write-Log ""

# ---------------------------------------------------------------- V1
Write-Log "################ V1  tests/l3/panel/run.js ################"
$v1out = Join-Path $tmp "v1.out"
$v1err = Join-Path $tmp "v1.err"
Remove-Item (Join-Path $root "tests\l3\panel\last-run.json") -ErrorAction SilentlyContinue
Start-Process -FilePath $exe -ArgumentList "tests\l3\panel\run.js" -NoNewWindow -Wait `
  -RedirectStandardOutput $v1out -RedirectStandardError $v1err | Out-Null
if (Test-Path $v1out) { Get-Content $v1out -Encoding utf8 | ForEach-Object { Write-Log $_ } }
if ((Test-Path $v1err) -and (Get-Item $v1err).Length -gt 0) {
  Write-Log "--- v1 stderr ---"
  Get-Content $v1err -Encoding utf8 | ForEach-Object { Write-Log $_ }
}
$v1bad = 0
$resFile = Join-Path $root "tests\l3\panel\last-run.json"
if (Test-Path $resFile) {
  $j = Get-Content $resFile -Raw -Encoding utf8 | ConvertFrom-Json
  Write-Log ""
  Write-Log ("last-run.json: exit_code={0} failures={1} total={2}" -f $j.exit_code, $j.failures, $j.total)
  if ($j.exit_code -ne 0) { $v1bad = 1 }
} else {
  Write-Log "MISSING tests/l3/panel/last-run.json"
  $v1bad = 1
}

# ---------------------------------------------------------------- V2
Write-Log ""
Write-Log "################ V2  cep-extension/js/vendor/ws/selftest.js ################"
$v2out = Join-Path $tmp "v2.out"
$v2err = Join-Path $tmp "v2.err"
Start-Process -FilePath $exe -ArgumentList "cep-extension\js\vendor\ws\selftest.js" -NoNewWindow -Wait `
  -RedirectStandardOutput $v2out -RedirectStandardError $v2err | Out-Null
$v2text = ""
if (Test-Path $v2out) {
  $v2text = (Get-Content $v2out -Encoding utf8) -join "`n"
  $v2text -split "`n" | ForEach-Object { Write-Log $_ }
}
$v2bad = if ($v2text -match "SELFTEST PASSED") { 0 } else { 1 }

# ---------------------------------------------------------------- V3
Write-Log ""
Write-Log "################ V3  --check（每个 .js） ################"
$v3bad = 0
$files = Get-ChildItem -Recurse -File -Include *.js cep-extension, tests\l3\panel | Sort-Object FullName
foreach ($f in $files) {
  $rel = $f.FullName.Replace($root + "\", "")
  # 必须用 | Out-String 触发同步读取，否则 GUI 子系统程序不会设置 $LASTEXITCODE。
  # --check 通过时 stdout 为空；失败时打印 SyntaxError 且 exit=1。
  $o = (& $exe --check $rel 2>&1 | Out-String).Trim()
  $code = $LASTEXITCODE
  if ($o.Length -eq 0 -and $code -eq 0) {
    Write-Log "OK    $rel (exit=0, no output)"
  } else {
    $v3bad++
    Write-Log "FAIL  $rel (exit=$code) :: $o"
  }
}
Write-Log "V3 bad=$v3bad"

# ---------------------------------------------------------------- V4
Write-Log ""
Write-Log "################ V4  manifest.xml + §4.3 必需元素自查 ################"
$v4bad = 0
$pyCheck = @'
import sys, xml.etree.ElementTree as ET
p = "cep-extension/CSXS/manifest.xml"
try:
    tree = ET.parse(p)
except Exception as exc:
    print("XML PARSE FAILED:", exc); sys.exit(1)
print("ET.parse OK:", p)
root = tree.getroot()
checks = []
def add(n, ok, d=""): checks.append((n, ok, d))
def text_of(node):
    """元素值既可能在文本里（CEP 标准写法），也可能在属性里（旧写法）。"""
    if node is None:
        return None
    if node.text and node.text.strip():
        return node.text.strip()
    return node.get("Value") or node.get("Name")
add("root 元素为 ExtensionManifest", root.tag == "ExtensionManifest", root.tag)
bid = root.get("ExtensionBundleId")
add("ExtensionBundleId == com.dsh.adobe.bridge", bid == "com.dsh.adobe.bridge", repr(bid))
ver = root.get("ExtensionBundleVersion")
add("ExtensionBundleVersion 语义化版本", bool(ver) and ver.count(".") == 2, repr(ver))
hosts = [(h.get("Name"), h.get("Version")) for h in root.iter("Host")]
add("HostList 含 PPRO", any(n == "PPRO" for n, _ in hosts), str(hosts))
add("Host 版本范围覆盖目标版本", any(n == "PPRO" and v and v.startswith("[") for n, v in hosts), str(hosts))
rts = [(text_of(r), r.get("Version")) for r in root.iter("RequiredRuntime")]
add("RequiredRuntime 含 CSXS", any(n == "CSXS" for n, _ in rts), str(rts))
add("RequiredRuntime 版本非空", bool(rts) and all(v for _, v in rts), str(rts))
mp = [text_of(e) for e in root.iter("MainPath")]
add("MainPath 指向 index.html", any((t or "").lower().endswith((".html", ".htm")) for t in mp), str(mp))
params = [(e.text or "").strip() for e in root.iter("Parameter")]
add("CEFCommandLine 含 --enable-nodejs", "--enable-nodejs" in params, str(params))
add("CEFCommandLine 含 --mixed-context", "--mixed-context" in params, str(params))
types = [(e.text or "").strip() for e in root.iter("Type")]
add("UI/Type == Panel", "Panel" in types, str(types))
geom = list(root.iter("Geometry"))
add("Geometry 存在", len(geom) > 0, "count=%d" % len(geom))
if geom:
    g = geom[0]; w = g.find("Size/Width"); h = g.find("Size/Height")
    add("Geometry 含初始 Width/Height", w is not None and h is not None and bool(w.text) and bool(h.text),
        "w=%s h=%s" % (w.text if w is not None else None, h.text if h is not None else None))
add("MainPath 在 DispatchInfo/Resources 下（CEP 标准位置）",
    root.find(".//DispatchInfo/Resources/MainPath") is not None)
add("CEFCommandLine 在 DispatchInfo/Resources 下（CEP 标准位置）",
    root.find(".//DispatchInfo/Resources/CEFCommandLine") is not None)
print("")
print("section 4.3 required-element self-check:")
bad = 0
for n, ok, d in checks:
    print("  %s %s%s" % ("OK  " if ok else "MISS", n, ("  [" + d + "]") if d else ""))
    if not ok: bad += 1
print("")
print("SELF-CHECK %s (%d/%d)" % ("PASSED" if bad == 0 else "FAILED", len(checks) - bad, len(checks)))
sys.exit(0 if bad == 0 else 1)
'@
$pyFile = Join-Path $tmp "manifest_check.py"
Set-Content -Path $pyFile -Value $pyCheck -Encoding utf8
$r4 = (& $py $pyFile 2>&1) -join "`n"
$r4 -split "`n" | ForEach-Object { Write-Log $_ }
if ($r4 -notmatch "SELF-CHECK PASSED") { $v4bad = 1 }
Write-Log "V4 bad=$v4bad"

# ---------------------------------------------------------------- V5
Write-Log ""
Write-Log "################ V5  index.html 引用脚本存在性 ################"
$v5bad = 0
$html = Get-Content (Join-Path $root "cep-extension\index.html") -Raw -Encoding utf8
$refs = [regex]::Matches($html, 'src\s*=\s*"([^"]+)"') | ForEach-Object { $_.Groups[1].Value }
$htmlDir = Join-Path $root "cep-extension"
foreach ($ref in $refs) {
  $full = Join-Path $htmlDir ($ref -replace '/', '\')
  if (Test-Path $full) {
    Write-Log ("  OK   {0}  -> {1} ({2} bytes)" -f $ref, $full, (Get-Item $full).Length)
  } else {
    Write-Log ("  MISS {0}  -> {1}" -f $ref, $full)
    $v5bad++
  }
}
Write-Log "V5 missing=$v5bad"

# ---------------------------------------------------------------- 汇总
$total = $v1bad + $v2bad + $v3bad + $v4bad + $v5bad
Write-Log ""
Write-Log "=================================================================="
Write-Log ("SUMMARY  V1={0} V2={1} V3={2} V4={3} V5={4}  ->  {5}" -f `
  $(if ($v1bad) { "FAIL" } else { "PASS" }), `
  $(if ($v2bad) { "FAIL" } else { "PASS" }), `
  $(if ($v3bad) { "FAIL" } else { "PASS" }), `
  $(if ($v4bad) { "FAIL" } else { "PASS" }), `
  $(if ($v5bad) { "FAIL" } else { "PASS" }), `
  $(if ($total -eq 0) { "ALL PASSED" } else { "FAILURES=$total" }))
Write-Log "完整日志: $log"

exit $total
