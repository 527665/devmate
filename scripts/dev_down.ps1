#Requires -Version 5.1
<#
  scripts/dev_down.ps1 —— DevMate 一键收工（和 dev_up.ps1 配对）

  干什么：
    1 找进程：按命令行关键字认领本项目起的 python 进程（API / worker / 飞书 / 前端静态服务）
    2 关窗口：连 dev_up 开的那几个标题为 DevMate-* 的终端窗口一起关，不留空壳
    3 -Infra  ：顺手 docker stop Postgres / Redis 容器（默认不停，留着下次起得快）
    4 -Sandbox：清掉残留的沙箱容器 dm-sbx*（跑过 verify_agent_in_docker 忘了毁时用它）

  用法：
    .\scripts\dev_down.ps1              # 只停应用进程
    .\scripts\dev_down.ps1 -DryRun      # 先看它打算停谁
    .\scripts\dev_down.ps1 -Infra       # 连容器一起停
    .\scripts\dev_down.ps1 -Sandbox     # 清残留沙箱容器
    .\scripts\dev_down.ps1 -Infra -Sandbox
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Infra,
    [switch]$Sandbox
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }
Set-Location -LiteralPath $root

# ===== PARAMS =====
$ApiPort    = 8000
$WebPort    = 8010
$PgPort     = 5432
$RedisPort  = 6379
# 认领本项目进程的关键字（只认这几个，绝不会误杀你别的 python）
$JobKeys = @('run_api.py', 'tasks.worker.WorkerSettings', 'channels.feishu_ws', "http.server $WebPort")
# =================

function Head($t) { Write-Host "  $t" -ForegroundColor Cyan }
function Ok($t)   { Write-Host ("      OK  {0}" -f $t) -ForegroundColor Green }
function Warn($t) { Write-Host ("      !!  {0}" -f $t) -ForegroundColor Yellow }
function Bad($t)  { Write-Host ("      XX  {0}" -f $t)  -ForegroundColor Red }
function Plan($t) { Write-Host ("      ..  {0}" -f $t)  -ForegroundColor DarkGray }

function Invoke-Native {
    param([string]$Exe, [string[]]$Params)
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $raw = & $Exe @Params 2>&1
        $code = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $saved
    }
    $lines = @()
    foreach ($item in @($raw)) {
        if ($item -is [System.Management.Automation.ErrorRecord]) { $lines += $item.Exception.Message }
        else { $lines += [string]$item }
    }
    return [pscustomobject]@{ Code = $code; Out = ($lines -join "`n") }
}

function Test-Engine {
    $r = Invoke-Native -Exe 'docker' -Params @('version', '--format', '{{.Server.Version}}')
    if ($r.Code -ne 0) { return $null }
    return ($r.Out -split "`n" | Where-Object { "$_".Trim() } | Select-Object -Last 1)
}

function Get-PortOwner([int]$port) {
    return @(Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue |
        Select-Object -Expand OwningProcess -Unique)
}

Write-Host ''
Write-Host ' =============== DevMate 收工 ===============' -ForegroundColor White
if ($DryRun) { Warn 'DryRun：只报打算停谁，不动手' }

# ---------- 1 + 2 停本项目进程与它的窗口 ----------
Head '第 1 步：本项目起的进程'
$targets = @()
# 认领规则（防误杀，三条硬线）：
$venvRe = [regex]::Escape($root) + '\\\.venv\\'
$shellSig = 'Set-Location -LiteralPath ''' + [regex]::Escape($root) + ''''

# 祖先链黑名单：dev_down 是被 Codex 的 shell 叫起来的，它自己就是"运行本项目的命令行"，
# 不排除的话会顺藤摸瓜把辅助进程自己也杀了。$PID 只是叶子，父链必须一并排除。
$ancestors = @($PID)
$anc = $PID
for ($i = 0; $i -lt 10; $i++) {
    $ap = Get-CimInstance Win32_Process -Filter "ProcessId=$anc" -ErrorAction SilentlyContinue
    if (-not $ap -or -not $ap.ParentProcessId) { break }
    $anc = [int]$ap.ParentProcessId
    $ancestors += $anc
}

foreach ($proc in @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pwsh.exe' OR Name='powershell.exe'" -ErrorAction SilentlyContinue)) {
    $cl = [string]$proc.CommandLine
    if (-not $cl) { continue }
    if ($cl -match 'dev_down\.ps1') { continue }
    if ($ancestors -contains [int]$proc.ProcessId) { continue }
    # 干活的 python：看命令行 argv[0]，不看 ExecutablePath。
    # 实测：.venv\Scripts\python.exe 是 uv 转发壳，真解释器是它的子进程（ExecutablePath 指向全局
    # Python313），但子进程命令行仍原样继承那个 venv 路径 —— 所以按命令行匹配能一次抓齐父子俩。
    if ($proc.Name -eq 'python.exe' -and $cl -match $venvRe) {
        foreach ($k in $JobKeys) { if ($cl -match [regex]::Escape($k)) { $targets += $proc; break } }
    } elseif (($proc.Name -eq 'pwsh.exe' -or $proc.Name -eq 'powershell.exe') -and $cl -match '-NoExit' -and $cl -match $shellSig) {
        $targets += $proc                                        # dev_up 开的壳窗口（标题 DevMate-*）
    }
}
# 按 PID 去重，但保留整行对象 —— Select-Object -Unique ProcessId 会把 CommandLine/Name 丢掉，
# 后面 "$($t.CommandLine)" 就全是空的，分不清停的是谁。
$seen = @{}
$unique = @()
foreach ($t in $targets) {
    $keyt = [int]$t.ProcessId
    if ($seen.ContainsKey($keyt)) { continue }
    $seen[$keyt] = $true
    $unique += $t
}
$targets = @($unique)

if ($targets.Count -eq 0) {
    Ok '没找到本项目的进程（要么已经停了，要么从没起过）'
} else {
    foreach ($t in $targets) {
        $what = if ("$($t.CommandLine)" -match 'run_api') { 'API' }
                elseif ("$($t.CommandLine)" -match 'WorkerSettings') { 'worker' }
                elseif ("$($t.CommandLine)" -match 'feishu_ws') { '飞书长连接' }
                elseif ("$($t.CommandLine)" -match 'http.server') { '前端静态服务' }
                else { '壳窗口' }
        if ($DryRun) { Plan "本应停 PID $($t.ProcessId)（$($t.Name) -> $what）"; continue }
        Stop-Process -Id $t.ProcessId -Force -ErrorAction SilentlyContinue
        Ok "已停 PID $($t.ProcessId) -> $what"
    }
}

if (-not $DryRun) {
    Start-Sleep -Seconds 2
    foreach ($p in @(@{ n = 'API'; v = $ApiPort }, @{ n = '前端'; v = $WebPort })) {
        $o = Get-PortOwner $p.v
        if ($o.Count -gt 0) { Warn "$($p.n) 端口 $($p.v) 还有进程占用（PID $($o -join ',')），可能是你自己另开的" }
        else { Ok "$($p.n) 端口 $($p.v) 已释放" }
    }
}

# ---------- 3 容器 ----------
if ($Infra) {
    Head '第 2 步：基础设施容器（-Infra）'
    $ver = Test-Engine
    if (-not $ver) { Warn 'Docker 引擎连不上，跳过容器（要停容器请先开 Docker Desktop）' }
    else {
        $fmt = '{{.Names}}|{{.Ports}}'
        $r = Invoke-Native -Exe 'docker' -Params @('ps', '--format', $fmt)
        $running = @()
        foreach ($line in @($r.Out -split "`n")) {
            $s = ([string]$line).Trim(); if (-not $s) { continue }
            $c = $s -split '\|'
            if ($c.Count -lt 2) { continue }
            if ($c[1] -match (":" + $PgPort + "->") -or $c[1] -match (":" + $RedisPort + "->")) { $running += $c[0] }
        }
        if ($running.Count -eq 0) { Ok '没有在跑的 Postgres / Redis 容器' }
        foreach ($n in $running) {
            if ($DryRun) { Plan "本应 docker stop $n"; continue }
            $s = Invoke-Native -Exe 'docker' -Params @('stop', $n)
            if ($s.Code -eq 0) { Ok "已停容器 $n" } else { Bad "停容器 $n 失败：$($s.Out)" }
        }
        Plan '下次要恢复：docker start 这些名字，或直接跑 .\scripts\dev_up.ps1'
    }
}

# ---------- 4 残留沙箱容器 ----------
if ($Sandbox) {
    Head '第 3 步：残留沙箱容器（-Sandbox）'
    $ver = Test-Engine
    if (-not $ver) { Warn 'Docker 引擎连不上，跳过沙箱清理' }
    else {
        $r = Invoke-Native -Exe 'docker' -Params @('ps', '-a', '--format', '{{.Names}}')
        $sbx = @(($r.Out -split "`n") | Where-Object { "$_".Trim() -match 'dm-sbx|sbx-' } | ForEach-Object { "$_".Trim() })
        if ($sbx.Count -eq 0) { Ok '没有残留沙箱容器（干净）' }
        foreach ($n in $sbx) {
            if ($DryRun) { Plan "本应 docker rm -f $n"; continue }
            $s = Invoke-Native -Exe 'docker' -Params @('rm', '-f', $n)
            if ($s.Code -eq 0) { Ok "已销毁沙箱 $n" } else { Warn "销毁 $n 失败：$($s.Out)" }
        }
    }
}

Write-Host ''
if (-not $Infra -and -not $Sandbox) {
    Write-Host '  Postgres / Redis 容器我没动（下次起得快）。要一起停：.\scripts\dev_down.ps1 -Infra' -ForegroundColor DarkGray
}
Write-Host '  数据都在 Postgres 卷里，停进程/停容器都不会丢上下文。' -ForegroundColor DarkGray
