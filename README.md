# sing-box macOS 客户端

直接使用 sing-box CLI 连接 VPS 上的 VLESS-Reality 代理，不依赖 v2rayN。

## 目录结构

```
~/.config/sing-box/
├── sb                           # macOS 启动脚本
├── sb.ps1                       # Windows PowerShell 版
├── sb.bat                       # Windows CMD 包装
│
├── config.template.json         # 配置模板（${VAR} 占位符，入库）
├── .env.example                 # 环境变量模板（入库）
├── .env                         # 当前 VPS 凭据（.gitignore）
├── .env.dmit                    # DMIT VPS 凭据（.gitignore）
├── .env.vps                     # 旧 VPS 凭据（.gitignore）
├── config.json                  # 由 sb init 生成（.gitignore）
│
├── proxy-domains.txt            # 强制代理域名（mixed/tun/claude 共用，入库）
├── direct-domains.txt           # 强制直连域名（入库）
├── corp-bypass-domains.txt      # 代理绕过域名（.gitignore，本地特定）
├── cdn-ips.txt                  # CDN 优选 IP 缓存（.gitignore）
│
├── export.json                  # iPhone 配置（sb export 生成，.gitignore）
├── sub.json                     # 订阅节点（sb sub 生成，.gitignore）
│
├── .config-run.json             # 运行时配置（sb 自动生成）
├── .sing-box.pid                # PID 文件（sb 自动管理）
├── .current-mode                # 当前模式（sb 自动管理）
├── cache.db                     # sing-box 缓存（自动生成）
│
├── README.md                    # macOS 使用说明（入库）
├── README_win.md                # Windows 使用说明（入库）
│
└── ruleset/                     # 规则集（入库，sb update 更新）
    ├── geosite-private.srs
    ├── geosite-cn.srs
    ├── geosite-geolocation-cn.srs
    ├── geoip-cn.srs
    └── geosite-category-ads-all.srs
```

### 配置生成流程

```
config.template.json + .env[.name] → sb init [name] → config.json → sb mixed/tun
```

敏感信息（UUID、密码、密钥、IP）在 `.env` 中，模板在 `config.template.json` 中，两者通过 `sb init` 合并生成 `config.json`。切换 VPS 只需 `sb init <name>`。

## 快速使用

```bash
# 添加到 PATH（只需执行一次）
ln -s ~/.config/sing-box/sb /usr/local/bin/sb

# 首次使用：从 VPS 自动提取凭据
echo "VPS_IP=1.2.3.4" > .env.dmit   # 填写 VPS IP
sb import dmit --ssh dmit                  # SSH 到 VPS，自动提取所有凭据
sb init dmit                               # 从模板 + .env.dmit 生成 config.json

# 或手动填写 .env
cp .env.example .env          # 手动填入所有凭据
sb init                       # 生成 config.json

# 切换 VPS（多 VPS 场景）
sb init dmit                  # 用 .env.dmit 重新生成 config.json
sb init vps                   # 用 .env.vps 重新生成 config.json

# 启动代理
sb                            # 默认 mixed 模式，系统代理 127.0.0.1:10887
sb tun                        # TUN 全局模式（需要 root）
sb -c config.json mixed       # 指定配置文件

# 运行中直接切换（另开终端，自动停旧启新）
sb tun                        # mixed → tun
sb mixed                      # tun → mixed

# 停止 / 状态
sb stop
sb status

# 切换节点（selector 手动 / urltest 自动）
sb select                     # 列出所有节点
sb select proxy-cdn           # 切到 CDN 节点
sb select auto                # 切回自动选最快

# CDN 中继（IP 被封时的备用线路）
sb cdn ip                     # 测试优选 Cloudflare IP
sb cdn on && sb               # 开启 CDN 模式（等价于 sb select proxy-cdn）
sb cdn off && sb              # 恢复直连

# iPhone sing-box (SFI)
sb export dmit                # 生成 iPhone 配置（自有 VPS 节点）
sb serve dmit                 # 导出并起临时 HTTP 服务，iPhone 拉取 Remote Profile
sb export --sub <订阅URL>     # 生成 iPhone 配置（机场订阅节点）

# 订阅转换
sb sub <订阅URL>                     # 只输出节点到 sub.json
sb sub <订阅URL> cloudflare          # 生成完整可运行的 config-cloudflare.json
sb -c config-cloudflare.json select  # 查看/切换节点
sb -c config-cloudflare.json mixed   # 用订阅配置运行

# 其他
sb log info                   # 切换日志级别（运行中自动重启）
sb check                      # 检测 VPS IP 是否被封
sb update                     # 更新 geosite/geoip 规则集

# Ctrl+C 也会自动停止并清理
```

## 两种模式

| | mixed（默认） | tun |
|---|---|---|
| 命令 | `sb` 或 `sb mixed` | `sb tun` |
| 接入方式 | SOCKS5/HTTP 代理 | 虚拟网卡全局接管 |
| 监听 | `127.0.0.1:10887` | 系统级，无需配置 |
| 应用需要 | 手动设置代理或用 `curl -x socks5://...` | 所有应用自动走代理 |
| 路由 | CN 直连，其余走代理 | CN 直连，其余走代理 |
| root | 不需要 | 需要（自动 sudo） |
| 适合场景 | 仅浏览器/特定应用需要代理 | 全局翻墙 |

两种模式的路由规则相同（绕过大陆IP），区别只在于流量如何进入 sing-box。

## 三种代理协议

| 协议 | 客户端 tag | 连接方式 | 特点 |
|------|-----------|---------|------|
| ShadowTLS v3 + SS | proxy-shadowtls | 直连 VPS:443 | 伪装 TLS 握手，抗检测最强 |
| VLESS Reality | proxy-reality | 直连 VPS:443 | XTLS Vision，性能最好 |
| VLESS WebSocket | proxy-cdn | Cloudflare CDN:443 | CDN 中继，IP 被封时备用 |

三种协议都在 `config.json` 中，但**默认只使用前两种**：

- **默认**（`sb`）：`proxy` = urltest，自动在 shadowtls 和 reality 之间选延迟最低的
- **CDN 模式**（`sb cdn on`）：所有流量切换到 `proxy-cdn`，走 Cloudflare CDN 中转

CDN 模式是**备用线路**——当 VPS IP 被封、直连不通时才需要开启。正常情况下直连的延迟和性能更好。

```bash
# 正常使用（直连，自动选优）
sb

# VPS IP 被封时，切到 CDN
sb check                      # 先检测是否被封
sb cdn ip                     # 测试优选 Cloudflare IP
sb cdn on && sb               # 开启 CDN 模式

# IP 解封后恢复直连
sb cdn off && sb
```

## 验证

```bash
# mixed 模式
curl -x socks5://127.0.0.1:10887 https://ifconfig.me

# tun 模式
curl https://ifconfig.me

# 两种模式都应返回 VPS IP

# 验证大陆直连（应返回国内 IP，不是 VPS IP）
curl -x socks5://127.0.0.1:10887 https://myip.ipip.net
```

DNS 泄漏检查：浏览器访问 https://browserleaks.com/dns，确认 DNS 服务器为 8.8.8.8 而非国内 DNS。

## 配置说明

### 配置生成

`config.json` 由 `sb init` 从模板生成，不要手动编辑：

```
sb import <name>  →  .env.<name>（从 VPS 自动提取凭据）
                         ↓
config.template.json + .env.<name>  →  sb init <name>  →  config.json
```

- **新增 VPS**：`echo "VPS_IP=x.x.x.x" > .env.new && sb import new --ssh <alias>`
- **修改 VPS 凭据**：编辑 `.env.<name>`，重新 `sb init <name>`
- **修改协议结构**：编辑 `config.template.json`，重新 `sb init`
- **切换 VPS**：`sb init dmit` / `sb init vps`

`sb import` 通过 SSH 连接 VPS，从 s-ui 数据库自动提取 UUID、密码、公钥、SNI 等所有凭据，避免手动复制出错。

### config.json 结构

config.json 同时包含 tun 和 mixed 两个入站，由 `sb` 脚本在启动时按模式过滤，生成 `.config-run.json` 给 sing-box 使用。**不需要手动编辑 config.json 来切换模式。**

### DNS 分流

| DNS 服务器 | 用途 |
|---|---|
| `tcp://8.8.8.8`（remote） | 默认，走代理出站 |
| `223.5.5.5`（local） | CN 域名，走直连 |

- 广告域名（匹配 geosite-category-ads-all）DNS 返回空应答，直接拦截
- CN 域名（匹配 geosite-cn / geosite-geolocation-cn）用国内 DNS 解析
- 其余用 Google DNS 经代理解析，避免 DNS 污染

### 路由规则

流量判定顺序：

1. DNS 协议 → dns_out（内部处理）
2. UDP 443 → block（阻止 QUIC，强制 TCP 走代理）
3. geosite-category-ads-all → block（广告拦截）
4. 私有 IP → direct
5. geosite-private → direct
6. **强制代理域名** → proxy（如 `googlevideo.com`）
7. geoip-cn → direct
8. geosite-cn → direct
9. **强制直连域名** → direct（如 `gsuus.com`）
10. 其余 → proxy

### 自定义域名路由

通过编辑域名文件自定义路由（每行一个域名，支持 `#` 注释）：

- `proxy-domains.txt` — 强制代理的域名（插入到 geoip-cn 规则之前，claude 模式也复用此文件）
- `direct-domains.txt` — 强制直连的域名（插入到 geosite-cn 规则之后）

```bash
# 国外域名被直连导致不通 → 加到 proxy-domains.txt
echo "googlevideo.com" >> proxy-domains.txt

# 国内域名被代理导致不通 → 加到 direct-domains.txt
echo "gsuus.com" >> direct-domains.txt
```

修改后重启即生效（无需 `sb init`）：
```bash
sb mixed   # 或 sb tun
```

## iPhone sing-box (SFI)

### 导出配置

```bash
sb export dmit                # 自有 VPS 节点 → export-dmit.json
sb export --sub <订阅URL>     # 机场订阅节点 → export.json
```

导出会自动处理 SFI 兼容性：
- 移除 mixed 入站和 tun 防环路配置
- DNS/路由格式降级为 1.11 兼容（`type`+`server` → `address`）
- 规则集从本地文件改为远程 URL
- `urltest` 改为 `selector`（支持手动切换节点）
- 注入 `proxy-domains.txt` / `direct-domains.txt` 域名规则

### 传输到 iPhone

**方式一：临时 HTTP 服务（推荐）**
```bash
sb export dmit                # 先生成 export-dmit.json
sb serve dmit                 # 起临时服务并显示 URL
# iPhone SFI: Profiles → New Profile → Remote → 粘贴 URL → 保存
# 拉取完成后 Ctrl+C 关闭
```

> **推荐用 iPhone 开热点，Mac 连上后再 `sb serve`。**
>
> 原因：很多 WiFi（尤其公司/酒店/部分家用路由器）开启了 AP Isolation（客户端隔离），Mac 和 iPhone 虽然同网段但无法互通，ARP 表会看到 iPhone 是 `(incomplete)`。iPhone 热点不会做隔离，最省事。
>
> 拉取前记得先关闭 iPhone 上 sing-box 的 Enabled 开关，否则 tun 会拦截局域网访问。

**方式二：AirDrop**
- Finder 中 AirDrop `export-dmit.json` 到 iPhone，选择用 sing-box 打开

## 订阅转换

支持三种订阅格式（自动识别）：
- **Clash YAML** — `proxies:` 开头
- **V2Ray/SS base64** — `ss://`/`vmess://`/`vless://`/`trojan://` URI 列表
- **sing-box JSON** — 机场根据 User-Agent 返回的完整配置

```bash
# 仅转换节点（输出到 sub.json）
sb sub <订阅URL>

# 生成完整可运行的配置（用于 Mac）
sb sub <订阅URL> cloudflare
# → sub.json + config-cloudflare.json

# 运行
sb -c config-cloudflare.json mixed      # 系统代理模式
sb -c config-cloudflare.json tun        # TUN 全局模式

# 生成 iPhone 配置
sb export --sub <订阅URL>                # → export.json
```

`sb sub <url> <name>` 生成的配置特点：
- `proxy` 是 `selector` 类型，默认第一个节点，支持手动切换
- 所有节点 IP 自动填入 tun `route_exclude_address`，防止路由环路
- 保留 `config.template.json` 的 DNS/路由规则和域名分流

## 节点选择 (sb select)

适用于**自有 VPS 配置**（`config.json` / `config-<profile>.json`）和**订阅配置**（`config-<name>.json`）。

```bash
# 列出所有节点（显示当前模式 selector/urltest 和选中的节点）
sb select
sb -c config-cloudflare.json select

# 手动选择节点（selector 模式）
sb select proxy-cdn                 # 按 tag
sb select 3                         # 按序号
sb -c config-cloudflare.json select "JP"

# 自动选最快（urltest 模式）
sb select auto

# 切换后需要重启生效
sb stop && sb
```

**工作原理：**
- `sb select <tag|idx>` → 配置里 `proxy` 变为 `selector` 类型，`default` 设为指定节点
- `sb select auto` → 配置里 `proxy` 变为 `urltest` 类型，自动选延迟最低的节点

**自有 VPS 配置的 proxy 列表：**
- `proxy-shadowtls` — ShadowTLS v3 + Shadowsocks（默认）
- `proxy-reality` — VLESS Reality
- `proxy-cdn` — VLESS over Cloudflare WebSocket

> 选 `proxy-cdn` 等价于 `sb cdn on`。

### Tun 模式 route_exclude_address

tun 入站中 `route_exclude_address` 包含 VPS IP，是 tun 模式正常工作的前提。

原因：macOS 上 sing-box 的 `auto_detect_interface` 对自身出站不生效。不排除 VPS IP 会导致代理发往 VPS 的连接被 tun 捕获送回 sing-box，形成路由环路，所有网络超时。

**切换 VPS 时不需要手动更新**：`config.template.json` 中使用 `${VPS_IP}/32` 占位符，`sb init` 会自动替换。

## sb 脚本工作原理

1. `sb init [name]`：从 `config.template.json` + `.env[.name]` 生成 `config.json`
2. 读取 `config.json`，根据模式过滤 inbounds（mixed 移除 tun，tun 保留全部）
3. 如果 shadowtls 密码为空，自动移除 shadowtls 相关 outbound
4. 写入 `.config-run.json`
5. 如果已有 sing-box 在运行（通过 `.sing-box.pid` 检测），先停掉
6. 以对应权限启动 sing-box（tun 用 sudo，mixed 不用）
7. 前台等待进程，Ctrl+C 时自动清理

## 命令一览

```bash
sb import <name> [--ssh alias]  # 从 VPS 提取 s-ui 凭据到 .env.<name>
sb init [name]                  # 从模板 + .env 生成 config.json
sb [mixed]                      # 系统代理模式（默认）
sb tun                          # TUN 全局模式（sudo）
sb claude                       # 仅代理 proxy-domains.txt 中的域名（公司网络用）
sb stop                         # 停止
sb status                       # 查看状态
sb log [level]                  # 日志级别（info/warn/error/debug）
sb cdn [on|off|ip|list|set]     # CDN 中继管理
sb export [name]                # 生成 iPhone sing-box 配置
sb export [name] --sub <url>    # 生成 iPhone 配置（使用机场订阅节点）
sb serve [name]                 # 起临时 HTTP 服务供 iPhone 拉取
sb sub <url> [name]             # 订阅转换（有 name 则生成 config-<name>.json）
sb select [tag|idx|auto]        # 列出/切换节点（auto 切回 urltest 自动模式）
sb check [IP]                   # 检测 VPS IP 是否被封
sb update                       # 更新 geosite/geoip 规则集
sb -c file.json mixed           # 指定配置文件
```

## 更新规则集

```bash
sb update              # 自动下载，运行中会通过本地代理下载
sb stop && sb          # 更新后重启生效
```

## 故障排查

| 现象 | 原因 | 解决 |
|---|---|---|
| mixed 模式 curl 超时 | sing-box 未运行或端口错 | `sb status` 确认运行中，`lsof -i :10887` 确认监听 |
| tun 模式全部超时 | route_exclude_address 缺失或 IP 不对 | 检查 config.json 中 tun 入站的 `route_exclude_address` 是否为当前 VPS IP |
| `sb tun` 提示权限不足 | 未输入 sudo 密码 | 脚本会自动请求 sudo，输入密码即可 |
| TLS 握手失败 | Reality 公钥或 SNI 不匹配 | 核对 config.json 中的 `public_key` 和 `server_name` |
| `sb` 报 sing-box not found | 未安装或不在 PATH | `brew install sing-box`，确认 `/opt/homebrew/bin/sing-box` 存在 |
| 切换模式后旧进程残留 | PID 文件与实际进程不一致 | `sb stop` 会兜底 pkill，或手动 `sudo killall sing-box` |

## 依赖

- sing-box: `brew install sing-box`（需包含 with_utls, with_quic, with_gvisor tags）
- python3: macOS 自带，sb 脚本用来处理 JSON 配置和模板替换
- Windows 版见 [README_win.md](README_win.md)
