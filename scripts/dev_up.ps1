#Requires -Version 5.1
<#
  scripts/dev_up.ps1 —— DevMate 一键启动（电脑开机后，第一件事就是跑它）

  它按顺序做 5 步，每步都打印结果；任何一步真失败就停下说清原因：
    0 体检     ：.env / run_api.py / .venv 齐不齐（缺了就告诉你先跑 uv sync）
    1 Docker   ：引擎没跑就拉起 Docker Desktop，最多等 150 秒
    2 容器     ：找 Postgres / Redis 容器（按端口找，不硬编容器名），停了就 docker start
    3 等端口   ：TCP 真连上才算数（容器 Up 不等于能连）
    4 起进程   ：新开 API 窗口 + arq worker 窗口（可选 飞书 / 前端 / 可观测）
    5 健康检查 ：轮询 /readyz，绿了才是真能用

  用法（在项目根目录跑）：
    .\scripts\dev_up.ps1              # 默认：Docker + 容器 + API + worker
    .\scripts\dev_up.ps1 -DryRun      # 只看它打算干什么，一个副作用都不产生
    .\scripts\dev_up.ps1 -NoWorker    # 只要 API，不要后台队列
    .\scripts\dev_up.ps1 -Feishu      # 顺带起飞书长连接
    .\scripts\dev_up.ps1 -Web         # 顺带起前端静态服务（8010）
    .\scripts\dev_up.ps1 -Recreate    # 找不到容器时按 README 参数新建
    .\scripts\dev_up.ps1 -Obs         # 顺带起 Prometheus + Grafana

  停止：各窗口 Ctrl+C 即可；要让 Postgres/Redis 也停，见脚本最后一行提示。
#>
[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$Recreate,
    [switch]$NoWorker,
    [switch]$Feishu,
    [switch]$Web,
    [switch]$Obs
)

# ===== PARAMS：可调项全集中在这，改数值不用碰下面逻辑 =====
$ApiPort     = 8000                                   # FastAPI 端口
$WorkerPort  = 8002                                   # worker 自己的指标端口
$WebPort     = 8010                                   # 前端静态服务端口
$PgPort      = 5432                                   # Postgres
$RedisPort   = 6379                                   # Redis
$DockerWaitS = 150                                    # 等 Docker 引擎就绪上限
$InfraWaitS  = 45                                     # 等单个端口可连上限
$ApiWaitS    = 150                                    # 等 /readyz 变绿上限
$DockerExe   = 'C:\Program Files\Docker\Docker\Docker Desktop.exe'
$PgName      = 'devmate-postgres'                         # -Recreate 时用的名字
$RedisName   = 'devmate-redis'
$PgImage     = 'pgvector/pgvector:pg17'
$RedisImage  = 'redis:7-alpine'
$PgUser      = 'devmate'                              # -Recreate 建库时用；必须和 database_url 默认值一致
$PgPass      = 'devmate'
$PgDb        = 'devmate'
# =========================================================

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (-not $root) { $root = (Get-Location).Path }
Set-Location -LiteralPath $root

$py = Join-Path $root '.venv\Scripts\python.exe'
$shell = if (Get-Command pwsh -ErrorAction SilentlyContinue) { (Get-Command pwsh).Source } else { (Get-Command powershell).Source }

function Head($n, $t) { Write-Host ("[{0}] {1}" -f $n, $t) -ForegroundColor Cyan }
function Ok($t)   { Write-Host ("      OK  {0}" -f $t) -ForegroundColor Green }
function Warn($t) { Write-Host ("      !!  {0}" -f $t) -ForegroundColor Yellow }
function Bad($t)  { Write-Host ("      XX  {0}" -f $t)  -ForegroundColor Red }
function Plan($t) { Write-Host ("      ..  {0}" -f $t)  -ForegroundColor DarkGray }

# 只看端口有没有人监听：docker 建的容器会把端口映射到本机
function Test-PortListening([int]$p) {
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $p -ErrorAction SilentlyContinue)
}

# TCP 真握手，比"有没有人监听"更可靠（容器起了但还在初始化时连不上）
function Wait-TcpPort([int]$p, [int]$timeoutSec) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        $c = New-Object Net.Sockets.TcpClient
        try {
            $task = $c.ConnectAsync('127.0.0.1', $p)
            if ($task.Wait(1000) -and $c.Connected) { $c.Close(); return $true }
        } catch { } finally { $c.Close() }
        Start-Sleep -Milliseconds 700
    }
    return $false
}

# 调原生命令（docker 这类）。为什么必须包一层：
# PowerShell 5.1 在 $ErrorActionPreference='Stop' 下，原生命令只要往 stderr 写一个字，
# 就会抛 RemoteException 终止异常（实测：2>&1 和 2>$null 两种写法都抛，裸调用反而不抛）。
# "引擎还没起"是预期内的事，不该把脚本炸死，所以这里临时降成 Continue 并自己看退出码。
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

function Get-EngineVersion {
    $r = Invoke-Native -Exe 'docker' -Params @('version', '--format', '{{.Server.Version}}')
    if ($r.Code -ne 0) { return $null }
    $v = ($r.Out -split "`n" | Where-Object { "$_".Trim() } | Select-Object -Last 1)
    if ([string]::IsNullOrWhiteSpace([string]$v)) { return $null }
    return ([string]$v).Trim()
}

# 扫容器：返回 Name/Status/Image/Ports 的对象数组（-All 含停止的）
function Get-Containers([switch]$All) {
    $fmt = '{{.Names}}|{{.Status}}|{{.Image}}|{{.Ports}}'
    $argv = if ($All) { @('ps', '-a', '--format', $fmt) } else { @('ps', '--format', $fmt) }
    $r = Invoke-Native -Exe 'docker' -Params $argv
    if ($r.Code -ne 0) { return @() }
    $out = @()
    foreach ($line in @($r.Out -split "`n")) {
        $s = ([string]$line).Trim()
        if (-not $s) { continue }
        $c = $s -split '\|'
        if ($c.Count -lt 4) { continue }
        $out += [pscustomobject]@{ Name = $c[0]; Status = $c[1]; Image = $c[2]; Ports = $c[3] }
    }
    return $out
}

# 先按"谁映射了这个端口"找，找不到再按镜像名/容器名猜
function Find-ByPort($rows, [int]$port) {
    foreach ($r in $rows) { if ($r.Ports -match (":" + $port + "->")) { return $r } }
    return $null
}
function Find-ByGuess($rows, [string]$pattern) {
    foreach ($r in $rows) { if ($r.Image -match $pattern -or $r.Name -match $pattern) { return $r } }
    return $null
}

Write-Host ''
Write-Host ' =============== DevMate 一键启动 ===============' -ForegroundColor White
if ($DryRun) { Warn 'DryRun：下面全是计划，我不动你的机器' }

# ---------- 0 体检 ----------
Head 0 '体检：文件和配置齐不齐'
$need = @('.env', 'run_api.py', 'api\app.py', 'tasks\worker.py', 'channels\feishu_ws.py', 'web\index.html')
foreach ($f in $need) {
    if (-not (Test-Path -LiteralPath (Join-Path $root $f))) { Bad "缺文件：$f（项目不完整，脚本停在这）"; exit 1 }
}
if (-not (Test-Path -LiteralPath $py)) { Bad '没有 .venv\Scripts\python.exe —— 先跑一次：uv sync'; exit 1 }
Ok ('Python 就绪：' + ((& $py -V 2>&1) -join ' ') + '（venv 直用，绕开 uv 缓存权限报错）')
if (Select-String -LiteralPath (Join-Path $root '.env') -Pattern '^DEVMATE_API_KEY=\S+' -Quiet) {
    Ok '.env 里有模型密钥（agent 调模型才不报错）'
} else {
    Warn '.env 的 DEVMATE_API_KEY 像空的：服务能起，但一调模型就炸'
}
if (-not (Select-String -LiteralPath (Join-Path $root '.env') -Pattern '^DEVMATE_DATABASE_URL=\S+' -Quiet)) {
    Plan '提醒：DEVMATE_POSTGRES_URL 是无效键，生效的是 database_url 的默认值 devmate/devmate/devmate@localhost:5432'
}

# ---------- 1 Docker 引擎 ----------
Head 1 'Docker 引擎（Postgres、Redis、沙箱容器都在里面）'
$ver = Get-EngineVersion
if ($ver) {
    Ok "已在跑：Docker $ver"
} elseif ($DryRun) {
    Plan "本应拉起 $DockerExe 并最多等 $DockerWaitS 秒"
} else {
    if (-not (Test-Path -LiteralPath $DockerExe)) {
        Bad "找不到 Docker Desktop：$DockerExe（装在别处就改脚本顶部 `$DockerExe）"
        exit 1
    }
    Warn '引擎没跑，正在拉起 Docker Desktop…（可能要等 1 分钟，弹授权窗就点掉）'
    Start-Process -FilePath $DockerExe | Out-Null
    $deadline = (Get-Date).AddSeconds($DockerWaitS)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 5
        $ver = Get-EngineVersion
        if ($ver) { break }
    }
    if (-not $ver) { Bad "等了 $DockerWaitS 秒引擎还没起来（常见：WSL2 没装好 / Docker Desktop 等你点确认）"; exit 1 }
    Ok "引擎就绪：Docker $ver"
}

# ---------- 2 + 3 容器与端口 ----------
Head 2 '基础设施容器（Postgres = 记忆库，Redis = 队列 / 限流）'
if ($DryRun) {
    Plan '本应：docker ps -a 按端口 5432 / 6379 定位容器 → 停了就 docker start → 都没有且带 -Recreate 才新建'
    Head 3 "端口连通性（DryRun 计划：TCP 握手 127.0.0.1:$PgPort 和 :$RedisPort，各最多等 $InfraWaitS 秒）"
} else {
    $rows = Get-Containers -All
    $pg = Find-ByPort $rows $PgPort;  if (-not $pg) { $pg = Find-ByGuess $rows 'postgres|pgvector' }
    $rd = Find-ByPort $rows $RedisPort; if (-not $rd) { $rd = Find-ByGuess $rows 'redis' }

    function Ensure-Up($c, [string]$label) {
        if (-not $c) { return $null }
        if ($c.Status -match '^Up') { Ok "$label 已在跑：$($c.Name)"; return $c }
        Warn "$label 容器在但停了：$($c.Name) → docker start"
        $r = Invoke-Native -Exe 'docker' -Params @('start', $c.Name)
        if ($r.Code -ne 0) { Bad "docker start $($c.Name) 失败：$($r.Out)"; exit 1 }
        Ok "$label 已拉起：$($c.Name)"
        return $c
    }
    $pg = Ensure-Up $pg 'Postgres'
    $rd = Ensure-Up $rd 'Redis'

    if ((-not $pg -or -not $rd) -and $Recreate) {
        if (-not $pg) {
            Warn "没找到 Postgres 容器，新建 $PgName（用户=$PgUser 库=$PgDb，与 database_url 默认值对齐）"
            $r = Invoke-Native -Exe 'docker' -Params @(
                'run', '-d', '--name', $PgName, '-p', "${PgPort}:5432",
                '-e', "POSTGRES_USER=$PgUser", '-e', "POSTGRES_PASSWORD=$PgPass", '-e', "POSTGRES_DB=$PgDb", $PgImage
            )
            if ($r.Code -ne 0) { Bad "新建 $PgName 失败：$($r.Out)（本地有没有镜像 $PgImage？）"; exit 1 }
            Ok "已新建 $PgName"
        }
        if (-not $rd) {
            Warn "没找到 Redis 容器，新建 $RedisName"
            $r = Invoke-Native -Exe 'docker' -Params @('run', '-d', '--name', $RedisName, '-p', "${RedisPort}:6379", $RedisImage)
            if ($r.Code -ne 0) { Bad "新建 $RedisName 失败：$($r.Out)"; exit 1 }
            Ok "已新建 $RedisName"
        }
    } elseif (-not $pg -or -not $rd) {
        Bad '缺 Postgres 或 Redis 容器。要么加 -Recreate 让脚本建，要么自己跑 README 的准备章节那两条 docker run。'
        exit 1
    }

    Head 3 '端口连通性（容器 Up 不等于能连上）'
    if (-not (Wait-TcpPort $PgPort $InfraWaitS)) { Bad "$PgPort 连不上 —— Postgres 还在初始化或端口没映射，稍后重跑"; exit 1 }
    Ok "Postgres 可连：127.0.0.1:$PgPort"
    if (-not (Wait-TcpPort $RedisPort $InfraWaitS)) { Bad "$RedisPort 连不上 —— arq 队列和限流都会失败"; exit 1 }
    Ok "Redis 可连：127.0.0.1:$RedisPort"
    if (-not (Get-Containers | Where-Object { $_.Image -match 'sandbox' -or $_.Name -match 'sbx' })) {
        Plan '没有在跑的沙箱容器属正常：沙箱容器是按需起、用完毁'
    }

    if ($Obs) {
        $obsRows = Get-Containers -All
        $p2 = $obsRows | Where-Object { $_.Name -match 'prometheus' }
        $g2 = $obsRows | Where-Object { $_.Name -match 'grafana' }
        if ($p2) { Ensure-Up $p2 'Prometheus' | Out-Null } else { Warn '没有 devmate-prometheus 容器，跳过（建法见 README 可观测章节）' }
        if ($g2) { Ensure-Up $g2 'Grafana' | Out-Null } else { Warn '没有 devmate-grafana 容器，跳过' }
    }
}

# ---------- 4 起进程（各开一个窗口，方便看日志 / Ctrl+C）----------
Head 4 '起进程'
function Open-Window([string]$title, [string]$cmdline, [int]$port, [bool]$checkPort) {
    if ($checkPort -and $port -gt 0 -and (Test-PortListening $port)) {
        Warn "$title 已经在端口 $port 上跑着，不重复启动"
        return $false
    }
    if ($DryRun) { Plan "本应新开窗口跑：$cmdline"; return $true }
    $inner = "Set-Location -LiteralPath '$root'; `$env:PYTHONUTF8='1'; if (`$Host.Name -eq 'ConsoleHost') { `$Host.UI.RawUI.WindowTitle = '$title' }; $cmdline"
    Start-Process -FilePath $shell -ArgumentList @('-NoExit', '-Command', $inner) | Out-Null
    Ok "$title 窗口已开"
    return $true
}

$startedApi = Open-Window 'DevMate-API' "$py -X utf8 run_api.py" $ApiPort $true
if (-not $NoWorker) { Open-Window 'DevMate-Worker' "$py -X utf8 -m arq tasks.worker.WorkerSettings" $WorkerPort $true | Out-Null }
if ($Feishu) { Open-Window 'DevMate-Feishu' "$py -X utf8 -m channels.feishu_ws" 0 $false | Out-Null }
if ($Web)    { Open-Window 'DevMate-Web'    "$py -X utf8 -m http.server $WebPort --bind 127.0.0.1" $WebPort $true | Out-Null }

# ---------- 5 健康检查 ----------
if ($DryRun) {
    Head 5 'DryRun 结束：以上全是计划。去掉 -DryRun 就是真跑'
    Write-Host ''
    return
}
Head 5 "健康检查（最多等 $ApiWaitS 秒）"
$ok = $false
$body = ''
$deadline = (Get-Date).AddSeconds($ApiWaitS)
while ((Get-Date) -lt $deadline) {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$ApiPort/readyz" -UseBasicParsing -TimeoutSec 3
        if ($r.StatusCode -eq 200) { $ok = $true; $body = $r.Content.Trim(); break }
    } catch { Start-Sleep -Seconds 2 }
}
if ($ok) { Ok "/readyz 200 -> $body（Postgres 连接池已就绪，可以开始用了）" }
else {
    Bad '/readyz 没通。看 API 窗口最后几行报错，八成是这三种：.env 密钥/模型名不对、Postgres 连不上、依赖没装全'
    exit 1
}

# ---------- 收尾：平时怎么用 ----------
Write-Host ''
Write-Host ' ================= 平时怎么用 =================' -ForegroundColor White
Write-Host ("  1) 看接口文档   http://127.0.0.1:{0}/docs" -f $ApiPort)
Write-Host   '  2) 终端提问     .\.venv\Scripts\python.exe -X utf8 -m channels.cli --session mywork "用一句话解释什么是反向代理"'
Write-Host ("  3) 网页提问     浏览器打开 web\index.html（后端地址填 http://localhost:{0}）" -f $ApiPort)
if ($Web) { Write-Host ("      或已开静态服务：http://127.0.0.1:{0}/web/index.html" -f $WebPort) }
if ($Feishu) { Write-Host '  4) 飞书         长连接窗口保持开着，去群里 @机器人 或直接发消息' }
Write-Host '  续聊规则  同一 session / thread_id 才接得上上下文；换 session = 换一个新会话'
Write-Host '  跑队列   异步任务走 POST /jobs（需要 worker 窗口在跑），拿 job_id 去 GET /jobs/{job_id} 查'
Write-Host ''
Write-Host '  收工：API / worker / 飞书 各窗口按 Ctrl+C；容器可留着，要一起停就：' -ForegroundColor DarkGray
Write-Host ("          docker stop {0} {1}" -f $PgName, $RedisName) -ForegroundColor DarkGray
Write-Host '  提醒：会调模型的端点（/issues /jobs /webhook/generic）都要 X-API-Key；CORS 白名单见 DEVMATE_CORS_ALLOW_ORIGINS。' -ForegroundColor DarkGray
