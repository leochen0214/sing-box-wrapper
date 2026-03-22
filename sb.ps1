# sb.ps1 - sing-box 模式切换启动器 (Windows)
# 用法：sb [-c config_file] [mixed|tun|stop|status|log|cdn|check|update]
#
#   mixed  (默认) - 绕过大陆IP，系统代理 127.0.0.1:<port>
#   tun            - 绕过大陆IP，TUN 全局接管（需要管理员权限）

param(
    [string]$ConfigFile,
    [Parameter(Position = 0)]
    [string]$Command = "mixed",
    [Parameter(Position = 1)]
    [string]$Arg2,
    [Parameter(Position = 2)]
    [string]$Arg3
)

$ErrorActionPreference = "Stop"

# --- 路径常量 ---
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseConfig = if ($ConfigFile) {
    if ([System.IO.Path]::IsPathRooted($ConfigFile)) { $ConfigFile }
    else { Join-Path $PWD $ConfigFile }
} else {
    Join-Path $Dir "config.json"
}
$RunConfig = Join-Path $Dir ".config-run.json"
$PidFile = Join-Path $Dir ".sing-box.pid"
$ModeFile = Join-Path $Dir ".current-mode"
$CdnIpsFile = Join-Path $Dir "cdn-ips.txt"
$CorpBypassFile = Join-Path $Dir "corp-bypass-domains.txt"

if ($ConfigFile) { Write-Host "配置文件: $(Split-Path -Leaf $BaseConfig)" }

# --- 从配置读取代理端口 ---
$configJson = Get-Content $BaseConfig -Raw | ConvertFrom-Json
$ProxyPort = ($configJson.inbounds | Where-Object { $_.type -eq "mixed" }).listen_port
Write-Host "代理端口: $ProxyPort"

# --- 检测默认网络接口 ---
$DefaultIface = try {
    $route = Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($route) { (Get-NetAdapter -InterfaceIndex $route.InterfaceIndex -ErrorAction SilentlyContinue).Name }
} catch { $null }
if ($DefaultIface) { Write-Host "默认接口: $DefaultIface" }

# --- WinINet 代理刷新（P/Invoke）---
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class WinINet {
    [DllImport("wininet.dll", SetLastError = true)]
    private static extern bool InternetSetOption(IntPtr hInternet, int dwOption, IntPtr lpBuffer, int dwBufferLength);
    private const int INTERNET_OPTION_SETTINGS_CHANGED = 39;
    private const int INTERNET_OPTION_REFRESH = 37;
    public static void Refresh() {
        InternetSetOption(IntPtr.Zero, INTERNET_OPTION_SETTINGS_CHANGED, IntPtr.Zero, 0);
        InternetSetOption(IntPtr.Zero, INTERNET_OPTION_REFRESH, IntPtr.Zero, 0);
    }
}
"@ -ErrorAction SilentlyContinue

# --- 系统代理 ---
$RegPath = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings"

function Enable-SystemProxy {
    Set-ItemProperty -Path $RegPath -Name ProxyEnable -Value 1
    Set-ItemProperty -Path $RegPath -Name ProxyServer -Value "127.0.0.1:$ProxyPort"
    # 代理绕过域名
    $bypass = "localhost;127.*;10.*;172.16.*;172.17.*;172.18.*;172.19.*;172.20.*;172.21.*;172.22.*;172.23.*;172.24.*;172.25.*;172.26.*;172.27.*;172.28.*;172.29.*;172.30.*;172.31.*;192.168.*;<local>"
    if (Test-Path $CorpBypassFile) {
        $extra = Get-Content $CorpBypassFile | Where-Object { $_ -and $_ -notmatch '^\s*#' } | ForEach-Object { $_.Trim() }
        if ($extra) { $bypass = "$bypass;$($extra -join ';')" }
    }
    Set-ItemProperty -Path $RegPath -Name ProxyOverride -Value $bypass
    [WinINet]::Refresh()
    Write-Host "系统代理已开启 (127.0.0.1:$ProxyPort)"
}

function Disable-SystemProxy {
    Set-ItemProperty -Path $RegPath -Name ProxyEnable -Value 0
    Remove-ItemProperty -Path $RegPath -Name ProxyServer -ErrorAction SilentlyContinue
    Remove-ItemProperty -Path $RegPath -Name ProxyOverride -ErrorAction SilentlyContinue
    [WinINet]::Refresh()
    Write-Host "系统代理已关闭"
}

# --- 进程管理 ---
function Stop-SingBox {
    if (Test-Path $PidFile) {
        $pid = [int](Get-Content $PidFile -Raw).Trim()
        $proc = Get-Process -Id $pid -ErrorAction SilentlyContinue
        if ($proc) {
            Write-Host "停止 sing-box (PID $pid)..."
            Stop-Process -Id $pid -Force -ErrorAction SilentlyContinue
            # 等待退出
            for ($i = 0; $i -lt 10; $i++) {
                if (-not (Get-Process -Id $pid -ErrorAction SilentlyContinue)) { break }
                Start-Sleep -Milliseconds 300
            }
        }
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
    # 兜底清理
    Get-Process -Name "sing-box" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}

function Show-Status {
    if ((Test-Path $PidFile) -and (Get-Process -Id ([int](Get-Content $PidFile -Raw).Trim()) -ErrorAction SilentlyContinue)) {
        $mode = if (Test-Path $ModeFile) { (Get-Content $ModeFile -Raw).Trim() } else { "unknown" }
        $pid = (Get-Content $PidFile -Raw).Trim()
        Write-Host "sing-box 运行中 (PID $pid, 模式: $mode)"
    } else {
        Write-Host "sing-box 未运行"
        Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    }
}

# --- JSON 辅助 ---
function Read-Config {
    Get-Content $BaseConfig -Raw | ConvertFrom-Json
}

function Write-Config($config) {
    $config | ConvertTo-Json -Depth 20 | Set-Content $BaseConfig -Encoding UTF8
}

function Get-CdnCurrentIp {
    $c = Read-Config
    ($c.outbounds | Where-Object { $_.tag -eq "proxy-cdn" }).server
}

function Set-CdnIp($ip) {
    $c = Read-Config
    ($c.outbounds | Where-Object { $_.tag -eq "proxy-cdn" }).server = $ip
    Write-Config $c
}

# --- 检测系统 DNS ---
function Get-SystemDns {
    try {
        $dns = Get-DnsClientServerAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.ServerAddresses.Count -gt 0 } |
            Select-Object -First 1
        if ($dns) { return $dns.ServerAddresses[0] }
    } catch {}
    return "223.5.5.5"
}

# --- 管理员权限检查 ---
function Test-Admin {
    ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ==================== 命令分发 ====================

switch ($Command) {
    "stop" {
        if (Test-Path $ModeFile) {
            $m = (Get-Content $ModeFile -Raw).Trim()
            if ($m -eq "mixed") { Disable-SystemProxy }
        }
        Stop-SingBox
        Remove-Item $ModeFile -Force -ErrorAction SilentlyContinue
        Write-Host "已停止"
        exit 0
    }

    "status" {
        Show-Status
        exit 0
    }

    "log" {
        $level = $Arg2
        $c = Read-Config
        if ($level -notin @("info", "warn", "error", "debug")) {
            Write-Host "当前日志级别: $($c.log.level)"
            Write-Host "用法: sb log [info|warn|error|debug]"
            exit 0
        }
        $c.log.level = $level
        Write-Config $c
        Write-Host "日志级别已改为: $level"
        # 运行中则自动重启
        if ((Test-Path $PidFile) -and (Get-Process -Id ([int](Get-Content $PidFile -Raw).Trim()) -ErrorAction SilentlyContinue) -and (Test-Path $ModeFile)) {
            $mode = (Get-Content $ModeFile -Raw).Trim()
            Write-Host "重启 $mode 模式以生效..."
            $args = @($mode)
            if ($ConfigFile) { $args = @("-c", $ConfigFile) + $args }
            & $MyInvocation.MyCommand.Path @args
        } else {
            Write-Host "sing-box 未运行，下次启动时生效"
        }
        exit 0
    }

    "cdn" {
        $action = $Arg2
        $cdnCurrentIp = Get-CdnCurrentIp

        switch ($action) {
            "ip" {
                Write-Host "当前 CDN IP: $cdnCurrentIp"
                Write-Host "获取 Cloudflare IP 列表..."
                # 获取 CF IPs
                $cfIps = try {
                    $raw = (Invoke-WebRequest -Uri "https://www.cloudflare.com/ips-v4" -TimeoutSec 5 -UseBasicParsing).Content
                    $cidrs = $raw -split "`n" | Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+/\d+$' }
                    $cidrs | ForEach-Object {
                        $parts = $_ -split '/'
                        $octets = $parts[0] -split '\.'
                        # network + 1
                        "$($octets[0]).$($octets[1]).$($octets[2]).$([int]$octets[3] + 1)"
                    }
                } catch {
                    @("104.16.0.1", "141.101.114.1", "162.159.0.1", "172.67.0.1", "103.21.244.1",
                      "108.162.192.1", "173.245.48.1", "188.114.96.1", "190.93.240.1", "198.41.128.1")
                }
                # 确保当前 IP 在列表中
                if ($cdnCurrentIp -notin $cfIps) { $cfIps = @($cdnCurrentIp) + $cfIps }
                Write-Host "测试 $($cfIps.Count) 个 IP 延迟..."

                # 并行测试
                $results = $cfIps | ForEach-Object -Parallel {
                    $ip = $_
                    try {
                        $sw = [System.Diagnostics.Stopwatch]::StartNew()
                        $resp = curl.exe -s -o NUL -w "%{http_code}" --resolve "nongliba.cc:443:$ip" `
                            --noproxy '*' --max-time 5 `
                            -H "Upgrade: websocket" -H "Connection: Upgrade" `
                            -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" `
                            -H "Sec-WebSocket-Version: 13" `
                            "https://nongliba.cc/ws" 2>$null
                        $sw.Stop()
                        $code = if ($resp) { $resp.Trim() } else { "000" }
                        $ms = [int]$sw.ElapsedMilliseconds
                        [PSCustomObject]@{ IP = $ip; Code = $code; Ms = $ms }
                    } catch {
                        [PSCustomObject]@{ IP = $ip; Code = "000"; Ms = 99999 }
                    }
                } -ThrottleLimit 10

                # 输出结果并保存
                $available = @()
                foreach ($r in $results | Sort-Object Ms) {
                    if ($r.Code -ne "000") {
                        $mark = if ($r.IP -eq $cdnCurrentIp) { " <-" } else { "" }
                        Write-Host ("  {0,-18} OK  {1}ms{2}" -f $r.IP, $r.Ms, $mark)
                        $available += $r
                    } else {
                        Write-Host ("  {0,-18} X" -f $r.IP)
                    }
                }

                if ($available.Count -eq 0) {
                    Write-Host "所有 IP 均不可用"
                    exit 1
                }

                # 保存到文件
                $available | ForEach-Object { "$($_.IP) $($_.Ms)" } | Set-Content $CdnIpsFile -Encoding UTF8
                Write-Host "已保存 $($available.Count) 个可用 IP -> cdn-ips.txt"

                $best = $available[0]
                $curEntry = $available | Where-Object { $_.IP -eq $cdnCurrentIp }
                if (-not $curEntry) {
                    Set-CdnIp $best.IP
                    Write-Host "当前 $cdnCurrentIp 不可用！已切换: -> $($best.IP) ($($best.Ms)ms)"
                } elseif ($best.IP -eq $cdnCurrentIp) {
                    Write-Host "当前 $cdnCurrentIp 已是最优 ($($curEntry.Ms)ms)"
                } else {
                    Write-Host "当前: $cdnCurrentIp ($($curEntry.Ms)ms)  最优: $($best.IP) ($($best.Ms)ms)"
                }
                exit 0
            }

            "list" {
                if (-not (Test-Path $CdnIpsFile) -or (Get-Item $CdnIpsFile).Length -eq 0) {
                    Write-Host "无保存的 IP，先运行: sb cdn ip"
                    exit 1
                }
                Write-Host "当前: $cdnCurrentIp"
                Write-Host "可用 IP（按延迟排序）:"
                $i = 1
                Get-Content $CdnIpsFile | ForEach-Object {
                    $parts = $_ -split '\s+'
                    $mark = if ($parts[0] -eq $cdnCurrentIp) { " <-" } else { "" }
                    Write-Host ("  {0}) {1,-18} {2}ms{3}" -f $i, $parts[0], $parts[1], $mark)
                    $i++
                }
                exit 0
            }

            "set" {
                $target = $Arg3
                if (-not $target) {
                    Write-Host "用法: sb cdn set <IP 或序号>"
                    exit 1
                }
                # 序号转 IP
                if ($target -match '^\d+$' -and [int]$target -le 99 -and (Test-Path $CdnIpsFile)) {
                    $line = (Get-Content $CdnIpsFile)[[int]$target - 1]
                    if ($line) { $target = ($line -split '\s+')[0] }
                }
                if ($target -eq $cdnCurrentIp) {
                    Write-Host "当前已是 $target"
                } else {
                    Set-CdnIp $target
                    Write-Host "已更新: $cdnCurrentIp -> $target"
                }
                exit 0
            }

            "on" {
                $c = Read-Config
                $c | Add-Member -NotePropertyName "_cdn_enabled" -NotePropertyValue $true -Force
                Write-Config $c
                Write-Host "CDN 中转已开启"
                if ((Test-Path $PidFile) -and (Get-Process -Id ([int](Get-Content $PidFile -Raw).Trim()) -ErrorAction SilentlyContinue) -and (Test-Path $ModeFile)) {
                    $mode = (Get-Content $ModeFile -Raw).Trim()
                    Write-Host "重启 $mode 模式以生效..."
                    $restartArgs = @($mode)
                    if ($ConfigFile) { $restartArgs = @("-c", $ConfigFile) + $restartArgs }
                    & $MyInvocation.MyCommand.Path @restartArgs
                } else {
                    Write-Host "下次启动时生效"
                }
                exit 0
            }

            "off" {
                $c = Read-Config
                $c.PSObject.Properties.Remove("_cdn_enabled")
                Write-Config $c
                Write-Host "CDN 中转已关闭"
                if ((Test-Path $PidFile) -and (Get-Process -Id ([int](Get-Content $PidFile -Raw).Trim()) -ErrorAction SilentlyContinue) -and (Test-Path $ModeFile)) {
                    $mode = (Get-Content $ModeFile -Raw).Trim()
                    Write-Host "重启 $mode 模式以生效..."
                    $restartArgs = @($mode)
                    if ($ConfigFile) { $restartArgs = @("-c", $ConfigFile) + $restartArgs }
                    & $MyInvocation.MyCommand.Path @restartArgs
                } else {
                    Write-Host "下次启动时生效"
                }
                exit 0
            }

            default {
                $c = Read-Config
                $cdnState = if ($c._cdn_enabled) { "on" } else { "off" }
                Write-Host "CDN 中转: $cdnState  IP: $cdnCurrentIp"
                Write-Host "用法: sb cdn [on|off|ip|list|set]"
                Write-Host "  on/off    CDN 中转开关"
                Write-Host "  ip        测试所有 Cloudflare IP 并保存可用列表"
                Write-Host "  list      查看已保存的可用 IP"
                Write-Host "  set <N>   切换到第 N 个 IP（序号来自 list）"
                exit 0
            }
        }
    }

    "check" {
        $targets = if ($Arg2) {
            @($Arg2)
        } else {
            $c = Read-Config
            $c.outbounds | Where-Object {
                $_.server -and $_.server -match '^\d' -and $_.tag -ne 'proxy-cdn'
            } | Select-Object -ExpandProperty server -Unique
        }

        if (-not $targets) {
            Write-Host "未找到 VPS IP，请指定: sb check <IP>"
            exit 1
        }

        foreach ($ip in $targets) {
            Write-Host "检测 $ip ..."
            $ports = @(443, 8443, 22)
            $blockedCount = 0
            foreach ($port in $ports) {
                try {
                    $tcp = New-Object System.Net.Sockets.TcpClient
                    $sw = [System.Diagnostics.Stopwatch]::StartNew()
                    $task = $tcp.ConnectAsync($ip, $port)
                    if ($task.Wait(5000)) {
                        $sw.Stop()
                        Write-Host ("  :{0,-5}  OK {1}ms" -f $port, $sw.ElapsedMilliseconds)
                    } else {
                        Write-Host ("  :{0,-5}  X  超时" -f $port)
                        $blockedCount++
                    }
                    $tcp.Close()
                } catch {
                    Write-Host ("  :{0,-5}  X  {1}" -f $port, $_.Exception.InnerException.Message)
                    $blockedCount++
                }
            }
            Write-Host ""
            if ($blockedCount -eq $ports.Count) {
                Write-Host "结论: $ip 大概率被封（所有端口不可达）"
            } elseif ($blockedCount -gt 0) {
                Write-Host "结论: $ip 部分端口不可达，可能被针对性封端口"
            } else {
                Write-Host "结论: $ip 未被封，如果代理不通则是协议被 DPI 识别"
            }
        }
        exit 0
    }

    "update" {
        $curlProxy = ""
        if ((Test-Path $PidFile) -and (Get-Process -Id ([int](Get-Content $PidFile -Raw).Trim()) -ErrorAction SilentlyContinue)) {
            $curlProxy = "-x http://127.0.0.1:$ProxyPort"
            Write-Host "更新规则集（通过代理）..."
        } else {
            Write-Host "更新规则集（直连，sing-box 未运行）..."
        }
        $rulesetDir = Join-Path $Dir "ruleset"
        if (-not (Test-Path $rulesetDir)) { New-Item -Path $rulesetDir -ItemType Directory | Out-Null }
        $geositeBase = "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set"
        $geoipBase = "https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set"
        $allFiles = @("geosite-private", "geosite-cn", "geosite-geolocation-cn", "geosite-category-ads-all", "geoip-cn")
        $tmpDir = Join-Path $env:TEMP "sb-update-$(Get-Random)"
        New-Item -Path $tmpDir -ItemType Directory | Out-Null
        $failed = 0

        foreach ($f in $allFiles) {
            Write-Host -NoNewline ("  {0,-35}" -f $f)
            $base = if ($f -like "geoip-*") { $geoipBase } else { $geositeBase }
            $url = "$base/$f.srs"
            $outFile = Join-Path $tmpDir "$f.srs"
            $curlArgs = @("-fLo", $outFile, $url)
            if ($curlProxy) { $curlArgs = @($curlProxy -split ' ') + $curlArgs }
            $result = & curl.exe @curlArgs 2>$null
            if ($LASTEXITCODE -eq 0) {
                Write-Host "OK"
            } else {
                Write-Host "失败"
                $failed++
            }
        }

        if ($failed -eq 0) {
            Get-ChildItem "$tmpDir\*.srs" | Move-Item -Destination $rulesetDir -Force
            Write-Host "全部更新完成"
        } else {
            Write-Host "部分文件更新失败，请检查网络（已有文件未被修改）"
        }
        Remove-Item $tmpDir -Recurse -Force -ErrorAction SilentlyContinue
        exit 0
    }

    { $_ -in @("mixed", "tun") } {
        # 继续往下执行启动流程
    }

    default {
        Write-Host "用法: sb [-c config_file] [mixed|tun|stop|status|log|cdn|check|update]"
        Write-Host ""
        Write-Host "  -c file        指定配置文件（默认 config.json）"
        Write-Host "  mixed  (默认) 绕过大陆IP，系统代理 127.0.0.1:$ProxyPort"
        Write-Host "  tun            绕过大陆IP，TUN 全局接管（需要管理员）"
        Write-Host "  stop           停止 sing-box"
        Write-Host "  status         查看运行状态"
        Write-Host "  log [level]    切换日志级别（info/warn/error/debug）"
        Write-Host "  cdn [on|off|ip|list|set] CDN 中转 / 优选 IP"
        Write-Host "  check [IP]     检测 IP 是否被封"
        Write-Host "  update         更新规则集（geosite/geoip）"
        exit 1
    }
}

# ==================== 启动流程 ====================

# TUN 模式需要管理员权限
if ($Command -eq "tun" -and -not (Test-Admin)) {
    Write-Host "TUN 模式需要管理员权限，正在提升..."
    $argList = "-ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`""
    if ($ConfigFile) { $argList += " -ConfigFile `"$ConfigFile`"" }
    $argList += " tun"
    Start-Process powershell -ArgumentList $argList -Verb RunAs
    exit 0
}

# 如果已在运行，先停掉
if ((Test-Path $PidFile) -and (Get-Process -Id ([int](Get-Content $PidFile -Raw).Trim()) -ErrorAction SilentlyContinue)) {
    $oldMode = if (Test-Path $ModeFile) { (Get-Content $ModeFile -Raw).Trim() } else { "unknown" }
    Write-Host "切换: $oldMode -> $Command"
    if ($oldMode -eq "mixed") { Disable-SystemProxy }
    Stop-SingBox
    Start-Sleep -Milliseconds 500
}

# --- 生成运行时配置 ---
$config = Get-Content $BaseConfig -Raw | ConvertFrom-Json
$sysDns = Get-SystemDns
$cdnEnabled = $false
if ($config.PSObject.Properties.Name -contains "_cdn_enabled") {
    $cdnEnabled = $config._cdn_enabled
    $config.PSObject.Properties.Remove("_cdn_enabled")
}

if ($Command -eq "mixed") {
    # 移除 TUN inbound
    $config.inbounds = @($config.inbounds | Where-Object { $_.type -ne "tun" })
    # 替换 local DNS
    if ($sysDns) {
        $config.dns.servers | Where-Object { $_.tag -eq "local" } | ForEach-Object { $_.server = $sysDns }
    }
    # 重排路由规则：域名规则在前 → resolve → IP 规则在后
    $domainRules = @()
    $ipRules = @()
    foreach ($r in $config.route.rules) {
        $isIpRule = $r.ip_is_private -or $r.ip_cidr -or
            ($r.rule_set -and ($r.rule_set | Where-Object { $_ -match 'geoip' }))
        if ($isIpRule) { $ipRules += $r } else { $domainRules += $r }
    }
    $resolveRule = [PSCustomObject]@{ action = "resolve"; strategy = "prefer_ipv4" }
    $config.route.rules = @($domainRules) + @($resolveRule) + @($ipRules)
    # 显式设置默认接口
    if ($DefaultIface) {
        $config.route.PSObject.Properties.Remove("auto_detect_interface")
        $config.route | Add-Member -NotePropertyName "default_interface" -NotePropertyValue $DefaultIface -Force
    }
}

# CDN 中转
if ($cdnEnabled) {
    # DNS detour: proxy → proxy-cdn
    $config.dns.servers | ForEach-Object {
        if ($_.detour -eq "proxy") { $_.detour = "proxy-cdn" }
    }
    # 路由规则: outbound proxy → proxy-cdn
    $config.route.rules | ForEach-Object {
        if ($_.outbound -eq "proxy") { $_.outbound = "proxy-cdn" }
    }
    # 路由 final
    if (-not $config.route.final -or $config.route.final -eq "proxy") {
        $config.route | Add-Member -NotePropertyName "final" -NotePropertyValue "proxy-cdn" -Force
    }
}

$config | ConvertTo-Json -Depth 20 | Set-Content $RunConfig -Encoding UTF8

# --- 启动 sing-box ---
Write-Host "启动 sing-box ($Command 模式)..."
$Command | Set-Content $ModeFile -Encoding UTF8

$proc = Start-Process -FilePath "sing-box" -ArgumentList "run", "-c", $RunConfig, "-D", $Dir `
    -PassThru -NoNewWindow -RedirectStandardError (Join-Path $Dir ".sing-box.log")
$proc.Id | Set-Content $PidFile -Encoding UTF8

Start-Sleep -Seconds 1

if (Get-Process -Id $proc.Id -ErrorAction SilentlyContinue) {
    Write-Host "sing-box 已启动 (PID $($proc.Id))"
    if ($Command -eq "mixed") { Enable-SystemProxy }
} else {
    Write-Host "启动失败，查看 .sing-box.log 排查"
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Remove-Item $ModeFile -Force -ErrorAction SilentlyContinue
    exit 1
}

# 前台等待，Ctrl+C 退出时自动清理
try {
    $proc.WaitForExit()
} finally {
    if (Test-Path $ModeFile) {
        $m = (Get-Content $ModeFile -Raw).Trim()
        if ($m -eq "mixed") { Disable-SystemProxy }
    }
    Stop-SingBox
    Remove-Item $ModeFile -Force -ErrorAction SilentlyContinue
    Write-Host "已停止"
}
