# sing-box Windows 客户端

使用 sing-box CLI + PowerShell 脚本连接 VPS 代理，功能与 macOS 版 `sb` 脚本对等。

## 目录结构

```
sing-box/
├── config.json          # 主配置（与 macOS 版通用，不要手动删改）
├── sb.ps1               # PowerShell 启动脚本
├── sb.bat               # CMD 包装（可在 cmd 中直接 sb mixed）
├── cdn-ips.txt          # CDN 优选 IP 缓存（sb cdn ip 生成）
├── corp-bypass-domains.txt  # 代理绕过域名（可选）
├── cache.db             # sing-box 缓存（自动生成）
├── .config-run.json     # 运行时配置（自动生成，勿编辑）
├── .sing-box.pid        # PID 文件（自动管理）
├── .sing-box.log        # 错误日志（启动失败时查看）
├── .current-mode        # 当前模式（自动管理）
└── ruleset/             # 规则集
    ├── geosite-private.srs
    ├── geosite-cn.srs
    ├── geosite-geolocation-cn.srs
    ├── geoip-cn.srs
    └── geosite-category-ads-all.srs
```

## 安装

### 1. 安装 sing-box

从 [sing-box releases](https://github.com/SagerNet/sing-box/releases) 下载 Windows amd64 版本，解压后将 `sing-box.exe` 放入 PATH 目录（如 `C:\Users\<用户>\bin\`）。

验证：

```powershell
sing-box version
```

### 2. 部署脚本和配置

将以下文件复制到 Windows 上的 sing-box 配置目录（如 `C:\Users\<用户>\.config\sing-box\`）：

- `config.json`（与 macOS 版通用）
- `sb.ps1`
- `sb.bat`
- `ruleset/` 目录

### 3. 添加到 PATH（可选）

将 sing-box 配置目录加入系统 PATH，即可在任意位置直接使用 `sb` 命令：

```
系统属性 → 环境变量 → Path → 新建 → C:\Users\<用户>\.config\sing-box
```

或在 PowerShell 中临时添加：

```powershell
$env:Path += ";$HOME\.config\sing-box"
```

## 快速使用

```powershell
# 绕过大陆IP（默认，系统代理 127.0.0.1:10887）
sb mixed
# 或
sb

# TUN 模式（全局接管，自动弹出 UAC 提权）
sb tun

# 停止
sb stop

# 查看状态
sb status

# Ctrl+C 也会自动停止并清理代理设置
```

在 CMD 中使用 `sb.bat`：

```cmd
sb mixed
sb stop
```

在 PowerShell 中使用：

```powershell
.\sb.ps1 mixed
# 或加入 PATH 后直接
sb mixed
```

## 两种模式

| | mixed（默认） | tun |
|---|---|---|
| 命令 | `sb` 或 `sb mixed` | `sb tun` |
| 接入方式 | HTTP 系统代理 | 虚拟网卡全局接管 |
| 监听 | `127.0.0.1:10887` | 系统级，所有应用自动走代理 |
| 系统代理 | 自动开启/关闭（Registry） | 不设置 |
| 管理员权限 | 不需要 | 需要（自动 UAC 提权） |
| 适合场景 | 浏览器/特定应用代理 | 全局翻墙 |

## 命令一览

```powershell
sb                          # 默认 mixed 模式启动
sb mixed                    # 同上
sb tun                      # TUN 全局模式（自动提权）
sb stop                     # 停止 sing-box + 关闭系统代理
sb status                   # 查看运行状态
sb log [info|warn|error|debug]  # 切换日志级别（运行中自动重启）
sb cdn                      # 查看 CDN 中转状态
sb cdn on                   # 开启 CDN 中转
sb cdn off                  # 关闭 CDN 中转
sb cdn ip                   # 测试所有 Cloudflare IP 并选最优
sb cdn list                 # 查看已缓存的可用 IP
sb cdn set <IP|N>           # 切换到指定 IP 或序号
sb check [IP]               # 检测 VPS IP 是否被封
sb update                   # 更新 geosite/geoip 规则集
sb -c config.json.xxx mixed # 指定配置文件启动
```

## CDN 中继

IP 被封时通过 Cloudflare CDN 中转连接 VPS。

```powershell
# 1. 测试并选优 Cloudflare IP
sb cdn ip

# 2. 开启 CDN 模式
sb cdn on

# 3. 启动
sb mixed

# 恢复直连
sb cdn off
sb mixed
```

CDN 模式会将所有 `proxy` 出站替换为 `proxy-cdn`（vless-ws 通过 Cloudflare），对 DNS 和路由规则统一生效。

## 系统代理说明

### mixed 模式自动管理

启动时自动设置 Windows 系统代理（Registry `Internet Settings`），停止时自动关闭：

- `ProxyEnable` = 1/0
- `ProxyServer` = `127.0.0.1:<端口>`
- `ProxyOverride` = 内网地址 + `corp-bypass-domains.txt` 中的域名

使用 WinINet API 通知系统刷新，浏览器（Chrome/Edge）立即生效。

### 代理绕过

内置绕过：`localhost`、`127.*`、`10.*`、`172.16-31.*`、`192.168.*`

额外绕过：创建 `corp-bypass-domains.txt`，每行一个域名：

```
*.corp.example.com
internal.mycompany.com
```

## 验证

```powershell
# mixed 模式（系统代理已设置，浏览器直接访问）
curl.exe https://ifconfig.me

# 验证大陆直连（应返回国内 IP）
curl.exe https://myip.ipip.net

# DNS 泄漏检查
# 浏览器访问 https://browserleaks.com/dns
# 确认 DNS 服务器为 8.8.8.8 而非国内 DNS
```

## 与 macOS 版的差异

| 差异点 | macOS | Windows |
|--------|-------|---------|
| 系统代理 | `networksetup` 命令 | Registry + WinINet P/Invoke |
| TUN 提权 | `sudo` | UAC 自动提升 |
| JSON 处理 | 内嵌 python3 | PowerShell 原生 ConvertFrom-Json |
| DNS 检测 | `scutil --dns` | `Get-DnsClientServerAddress` |
| 接口检测 | `route -n get default` | `Get-NetRoute` + `Get-NetAdapter` |
| 错误日志 | 终端直接输出 stderr | 重定向到 `.sing-box.log` |
| claude 模式 | 支持（PAC 代理自动检测） | 不支持 |
| 并行 IP 测试 | bash `&` + `wait` | `ForEach-Object -Parallel` |

## 故障排查

| 现象 | 原因 | 解决 |
|---|---|---|
| `sb` 无法识别 | 未加入 PATH | 将 sb.bat 所在目录加入 PATH，或用完整路径 `.\sb.bat` |
| 执行策略限制 | PowerShell 禁止运行脚本 | `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned` |
| 系统代理未生效 | Registry 更新但浏览器未刷新 | 关闭重开浏览器，或检查是否有代理插件覆盖系统设置 |
| TUN 启动失败 | 未安装 WinTun 驱动 | 从 [wintun.net](https://www.wintun.net/) 下载 `wintun.dll` 放到 sing-box.exe 同目录 |
| TUN 提权后窗口闪退 | UAC 拒绝或路径有空格 | 以管理员身份手动打开 PowerShell 再运行 `sb tun` |
| 启动后全部超时 | `route_exclude_address` 缺失 | 检查 config.json tun 入站的 `route_exclude_address` 是否包含 VPS IP |
| CDN IP 测试全部失败 | curl.exe 不存在 | Windows 10 1803+ 自带 curl.exe，低版本需手动安装 |
| 规则集更新失败 | 网络问题 | 先启动代理再 `sb update`（会自动通过本地代理下载） |

## 依赖

- **sing-box**: [GitHub Releases](https://github.com/SagerNet/sing-box/releases)（Windows amd64）
- **curl.exe**: Windows 10 1803+ 自带
- **PowerShell 7+**: 推荐（`ForEach-Object -Parallel` 需要 7.0+）。Windows PowerShell 5.1 基本功能可用，但 CDN IP 并行测试需降级为串行
- **WinTun**: TUN 模式需要（[wintun.net](https://www.wintun.net/)），将 `wintun.dll` 放到 sing-box.exe 同目录
