# sing-box macOS 客户端

一个 sing-box CLI 包装脚本，支持两种使用场景：

- **场景 A：连接自建 VPS**（VLESS Reality / ShadowTLS）
- **场景 B：使用机场订阅**（Clash / V2Ray / SS / sing-box 格式）

也能一键生成 iPhone sing-box (SFI) 配置。Windows 版见 [README_win.md](README_win.md)。

---

## 安装

```bash
# 依赖
brew install sing-box       # 需包含 with_utls, with_quic, with_gvisor tags

# 添加到 PATH（只需执行一次）
ln -s ~/.config/sing-box/sb /usr/local/bin/sb
```

根据你的场景，跳到对应章节：

- [场景 A：自建 VPS](#场景-a自建-vps)
- [场景 B：机场订阅](#场景-b机场订阅)

---

## 场景 A：自建 VPS

### A.1 获取凭据到 .env

**方式一（推荐）：SSH 自动提取**

前提：本机 `~/.ssh/config` 里已配置 VPS 的 SSH alias，可以 `ssh dmit` 免密连接。

```bash
sb import dmit --ssh dmit    # 从 VPS 的 s-ui 数据库自动提取凭据到 .env.dmit
```

**方式二：手动填写**

```bash
cp .env.example .env.dmit    # 复制模板
vim .env.dmit                # 手动填入 VPS_IP/UUID/密码/公钥等
```

### A.2 生成 config.json

```bash
sb init dmit                 # → config-dmit.json
```

`config.template.json` 是模板（入库），`.env.dmit` 是凭据（.gitignore），两者合并生成 `config-dmit.json`。

### A.3 启动代理

```bash
sb -c config-dmit.json           # mixed 模式，默认，系统代理 127.0.0.1:10887
sb -c config-dmit.json tun       # tun 模式，全局接管（自动 sudo）
```

两种模式对比见 [mixed vs tun](#mixed-vs-tun)。

运行中直接 `sb -c config-dmit.json tun`/`mixed` 可以平滑切换，Ctrl+C 停止并清理。

> **省略 `-c`**：如果只用一个 VPS，把 `config-dmit.json` 改名成 `config.json` 即可 `sb` 直接启动。

### A.4 切换代理节点

自建 VPS 的 `proxy` selector 内置三种出站：

| tag | 协议 | 连接方式 | 特点 |
|------|-----------|---------|------|
| `proxy-shadowtls` | ShadowTLS v3 + Shadowsocks | 直连 VPS:443 | 伪装 TLS 握手，抗检测 |
| `proxy-reality` | VLESS Reality | 直连 VPS:443 | XTLS Vision，性能最好 |
| `proxy-cdn` | VLESS over Cloudflare WebSocket | CDN 中继 | VPS IP 被封时的备用 |

```bash
sb select                    # 列出所有节点，* 标记当前
sb select proxy-reality      # 按 tag 切换
sb select 2                  # 按序号切换
sb select auto               # 切回 urltest 自动选最快

sb stop && sb                # 切换后重启生效
```

### A.5 多 VPS 切换

每个 VPS 一个 `.env.<name>`，每次 `sb init <name>` 生成对应 `config-<name>.json`：

```bash
sb init dmit                 # → config-dmit.json
sb init vps                  # → config-vps.json
sb -c config-dmit.json       # 启动 dmit
sb -c config-vps.json        # 切到 vps
```

### A.6 导出到 iPhone sing-box (SFI)

```bash
sb export dmit               # → export-dmit.json（已做 SFI 1.11 兼容转换）
sb serve dmit                # 起临时 HTTP 服务，显示拉取 URL
```

iPhone：Profiles → New Profile → Type: **Remote** → 粘贴 URL → 保存 → Mac 上 Ctrl+C 关闭服务。

> **推荐用 iPhone 开热点，Mac 连上后再 `sb serve`。**
>
> 原因：很多 WiFi（尤其公司/酒店/部分家用路由器）开启了 AP Isolation（客户端隔离），Mac 和 iPhone 虽然同网段但无法互通。iPhone 热点不会做隔离。
>
> 拉取前先关闭 iPhone 上 sing-box 的 Enabled 开关，否则 tun 会拦截局域网访问。

或者 AirDrop `export-dmit.json` 到 iPhone，选择用 sing-box 打开。

### A.7 VPS IP 被封时

```bash
sb check                     # 检测当前 VPS IP 是否被封
sb cdn ip                    # 测试 Cloudflare IP 延迟
sb select proxy-cdn          # 切到 CDN 节点
sb stop && sb
```

`sb select proxy-cdn` 等价于旧版的 `sb cdn on`。

---

## 场景 B：机场订阅

### B.1 生成配置

```bash
sb sub "https://机场订阅URL" cloudflare
# → sub.json + config-cloudflare.json
```

支持自动识别三种订阅格式：
- **Clash YAML** (`proxies:` 开头)
- **V2Ray/SS base64** (`ss://`/`vmess://`/`vless://`/`trojan://` URI 列表)
- **sing-box JSON**（机场根据 User-Agent 返回的完整配置）

生成的 `config-cloudflare.json` 以 `config.template.json` 为骨架，`proxy` 是 `selector` 类型包含所有订阅节点。

### B.2 启动代理

```bash
sb -c config-cloudflare.json           # mixed 模式
sb -c config-cloudflare.json tun       # tun 模式
```

### B.3 切换节点

```bash
sb -c config-cloudflare.json select          # 列出所有节点
sb -c config-cloudflare.json select "JP"     # 按 tag 切换
sb -c config-cloudflare.json select 12       # 按序号切换
sb -c config-cloudflare.json select auto     # 切回自动选最快

sb stop && sb -c config-cloudflare.json
```

### B.4 导出到 iPhone

```bash
sb export --sub "https://机场订阅URL"    # → export.json（仅含机场节点）
sb serve                                 # 起临时 HTTP 服务供 iPhone 拉取
```

同场景 A.6，推荐用 iPhone 热点。

---

## 通用功能

### mixed vs tun

| | mixed（默认） | tun |
|---|---|---|
| 命令 | `sb` 或 `sb mixed` | `sb tun` |
| 接入方式 | SOCKS5/HTTP 代理 | 虚拟网卡全局接管 |
| 监听 | `127.0.0.1:10887` | 系统级，无需配置 |
| 应用需要 | 手动设置代理或 `curl -x socks5://...` | 所有应用自动走代理 |
| root | 不需要 | 需要（自动 sudo） |
| 适合 | 仅浏览器/特定应用 | 全局翻墙 |

两种模式路由规则相同，区别只在流量进入方式。

### 自定义域名路由

编辑两个文本文件即可（每行一个域名，支持 `#` 注释），修改后**重启 sb 即生效**，无需 `sb init`：

- `proxy-domains.txt` — 强制走代理的域名（插入到 geoip-cn 规则之前）
- `direct-domains.txt` — 强制直连的域名（插入到 geosite-cn 规则之后）

```bash
echo "googlevideo.com" >> proxy-domains.txt   # 国外域名被直连导致不通
echo "gsuus.com" >> direct-domains.txt        # 国内域名被代理导致不通
sb stop && sb                                  # 重启生效
```

### 验证是否生效

```bash
# mixed 模式
curl -x socks5://127.0.0.1:10887 https://ifconfig.me    # 应返回代理 IP
curl -x socks5://127.0.0.1:10887 https://myip.ipip.net  # 应返回国内 IP（CN 直连）

# tun 模式
curl https://ifconfig.me
```

DNS 泄漏检查：浏览器访问 <https://browserleaks.com/dns>，确认 DNS 服务器为 `8.8.8.8` 而非国内 DNS。

---

## 参考

### 命令一览

```bash
# VPS 配置
sb import <name> [--ssh alias]    # 从 VPS 提取凭据到 .env.<name>
sb init [name]                    # 从模板 + .env 生成 config.json

# 订阅配置
sb sub <url> [name]               # 订阅转换（有 name 则生成 config-<name>.json）

# 启动 / 停止
sb [mixed]                        # 系统代理模式（默认）
sb tun                            # TUN 全局模式（sudo）
sb claude                         # 仅 proxy-domains.txt 中的域名走代理
sb stop                           # 停止
sb status                         # 查看状态
sb -c file.json mixed             # 指定配置文件

# 节点选择
sb select [tag|idx|auto]          # 列出/切换节点

# iPhone 导出
sb export [name]                  # 生成 iPhone sing-box 配置
sb export [name] --sub <url>      # 生成 iPhone 配置（使用机场节点）
sb serve [name]                   # 起临时 HTTP 服务供 iPhone 拉取

# 维护
sb check [IP]                     # 检测 IP 是否被封
sb cdn [on|off|ip|list|set]       # CDN 中继管理（旧接口，建议用 sb select proxy-cdn）
sb log [level]                    # 日志级别（info/warn/error/debug）
sb update                         # 更新 geosite/geoip 规则集
```

### 目录结构

```
~/.config/sing-box/
├── sb                          # macOS 启动脚本（入库）
├── sb.ps1 / sb.bat             # Windows 版
│
├── config.template.json        # 配置模板，${VAR} 占位符（入库）
├── .env.example                # 环境变量模板（入库）
├── .env.<name>                 # VPS 凭据（.gitignore）
├── config-<name>.json          # sb init / sb sub 生成（.gitignore）
│
├── proxy-domains.txt           # 强制代理域名（入库）
├── direct-domains.txt          # 强制直连域名（入库）
├── corp-bypass-domains.txt     # 代理绕过域名（.gitignore，本地特定）
├── cdn-ips.txt                 # CDN 优选 IP 缓存（.gitignore）
│
├── export.json / export-<name>.json  # iPhone 配置（.gitignore）
├── sub.json                    # sb sub 输出的节点列表（.gitignore）
│
├── .config-run.json            # 运行时配置（sb 自动生成）
├── .sing-box.pid               # PID 文件
├── .current-mode               # 当前模式
├── cache.db                    # sing-box 缓存
│
└── ruleset/                    # 规则集（入库，sb update 更新）
    ├── geosite-private.srs
    ├── geosite-cn.srs
    ├── geosite-geolocation-cn.srs
    ├── geoip-cn.srs
    └── geosite-category-ads-all.srs
```

### DNS 分流与路由规则

**DNS：**

| DNS 服务器 | 用途 |
|---|---|
| `tcp://8.8.8.8`（remote） | 默认，走代理出站 |
| `223.5.5.5`（local） | CN 域名，走直连 |

- 广告域名（geosite-category-ads-all）返回空应答
- CN 域名（geosite-cn / geosite-geolocation-cn）用国内 DNS
- 其余用 Google DNS 经代理解析，避免 DNS 污染

**路由规则**（从上到下匹配）：

1. DNS 协议 → dns_out
2. UDP 443 → block（阻止 QUIC，强制 TCP 走代理）
3. geosite-category-ads-all → block
4. 私有 IP / geosite-private → direct
5. `proxy-domains.txt` 中的域名 → proxy
6. geoip-cn / geosite-cn → direct
7. `direct-domains.txt` 中的域名 → direct
8. 其余 → proxy

### Tun 模式 route_exclude_address

tun 入站中 `route_exclude_address` 包含 VPS IP 是 tun 模式正常工作的前提。

原因：macOS 上 sing-box 的 `auto_detect_interface` 对自身出站不生效。不排除 VPS IP 会导致代理发往 VPS 的连接被 tun 捕获送回 sing-box，形成路由环路。

- **自建 VPS 配置**：`config.template.json` 中使用 `${VPS_IP}/32` 占位符，`sb init` 自动替换
- **订阅配置**：`sb sub` 自动收集所有节点 IP 填入此列表

### 故障排查

| 现象 | 原因 | 解决 |
|---|---|---|
| mixed 模式 curl 超时 | sing-box 未运行或端口错 | `sb status`，`lsof -i :10887` |
| tun 模式全部超时 | `route_exclude_address` 缺失或 IP 不对 | 检查 config.json 中 tun 入站的 `route_exclude_address` |
| `sb tun` 权限不足 | 未输入 sudo 密码 | 脚本会自动请求 sudo，输入密码即可 |
| TLS 握手失败 | Reality 公钥或 SNI 不匹配 | 核对 `public_key` 和 `server_name` |
| iPhone 拉取 serve 超时 | WiFi AP Isolation 或 SFI tun 拦截 | 用 iPhone 热点 + 拉取前关闭 Enabled |
| sing-box not found | 未安装或不在 PATH | `brew install sing-box` |
| 切换模式后旧进程残留 | PID 文件与实际不一致 | `sb stop` 会兜底 pkill |

---

## 附录：sb 脚本工作原理

1. `sb init <name>`：从 `config.template.json` + `.env.<name>` 生成 `config-<name>.json`
2. `sb -c <file> mixed/tun`：
   - 读取指定配置，根据模式过滤 inbounds（mixed 移除 tun，tun 保留全部）
   - 如果 shadowtls 密码为空，自动移除 shadowtls 相关 outbound
   - 写入 `.config-run.json`
   - 停掉已有 sing-box（通过 `.sing-box.pid`）
   - 启动 sing-box（tun 用 sudo），前台等待，Ctrl+C 清理
