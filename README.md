# sing-box macOS 客户端

直接使用 sing-box CLI 连接 VPS 上的 VLESS-Reality 代理，不依赖 v2rayN。

## 目录结构

```
~/.config/sing-box/
├── config.json          # 主配置（tun + mixed 两种入站，不要手动删改）
├── sb                   # 启动脚本（按模式启动/切换/停止）
├── README.md
├── cache.db             # sing-box 缓存（自动生成）
├── .config-run.json     # 运行时配置（sb 自动生成，勿手动编辑）
├── .sing-box.pid        # PID 文件（sb 自动管理）
├── .current-mode        # 当前模式记录（sb 自动管理）
└── ruleset/             # 规则集
    ├── geosite-private.srs
    ├── geosite-cn.srs
    ├── geosite-geolocation-cn.srs
    ├── geoip-cn.srs
    └── geosite-category-ads-all.srs
```

## 快速使用

```bash
# 添加到 PATH（只需执行一次）
ln -s ~/.config/sing-box/sb /usr/local/bin/sb

# 绕过大陆IP（默认，系统代理 127.0.0.1:10887）
sb

# TUN 模式（全局接管，需要 root）
sb tun

# 运行中直接切换（另开终端，自动停旧启新）
sb tun      # mixed → tun
sb mixed    # tun → mixed

# 停止
sb stop

# 查看状态
sb status

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

## 验证

```bash
# mixed 模式
curl -x socks5://127.0.0.1:10887 https://ifconfig.me

# tun 模式
curl https://ifconfig.me

# 两种模式都应返回 VPS IP: 104.194.80.201

# 验证大陆直连（应返回国内 IP，不是 VPS IP）
curl -x socks5://127.0.0.1:10887 https://myip.ipip.net
```

DNS 泄漏检查：浏览器访问 https://browserleaks.com/dns，确认 DNS 服务器为 8.8.8.8 而非国内 DNS。

## 配置说明

### config.json 结构

config.json 同时包含 tun 和 mixed 两个入站，由 `sb` 脚本在启动时按模式过滤，生成 `.config-run.json` 给 sing-box 使用。**不需要手动编辑 config.json 来切换模式。**

如需修改代理参数（换服务器、换 UUID 等），编辑 config.json 后重新 `sb` 即可生效。

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
6. geoip-cn → direct
7. geosite-cn → direct
8. 其余 → proxy

### Tun 模式 route_exclude_address

tun 入站中 `route_exclude_address: ["104.194.80.201/32"]` 是 tun 模式正常工作的前提。

原因：macOS 上 sing-box 的 `auto_detect_interface` 对自身出站不生效。不排除 VPS IP 会导致代理发往 VPS 的连接被 tun 捕获送回 sing-box，形成路由环路，所有网络超时。

**如果换了 VPS IP，必须同步更新两处**：outbounds 中的 `server` 和 tun 入站中的 `route_exclude_address`。

## sb 脚本工作原理

1. 读取 `config.json`
2. 根据模式过滤 inbounds（mixed 模式移除 tun 入站，tun 模式保留全部）
3. 写入 `.config-run.json`
4. 如果已有 sing-box 在运行（通过 `.sing-box.pid` 检测），先停掉
5. 以对应权限启动 sing-box（tun 用 sudo，mixed 不用）
6. 前台等待进程，Ctrl+C 时自动清理

## 更新规则集

规则集文件不会自动更新。定期手动更新：

```bash
cd ~/.config/sing-box/ruleset
curl -fLO https://github.com/SagerNet/sing-geosite/releases/latest/download/geosite-private.srs
curl -fLO https://github.com/SagerNet/sing-geosite/releases/latest/download/geosite-cn.srs
curl -fLO https://github.com/SagerNet/sing-geosite/releases/latest/download/geosite-geolocation-cn.srs
curl -fLO https://github.com/SagerNet/sing-geosite/releases/latest/download/geosite-category-ads-all.srs
curl -fLO https://github.com/SagerNet/sing-geoip/releases/latest/download/geoip-cn.srs
```

更新后重启 sing-box（`sb stop && sb`）生效。

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

- sing-box: `brew install sing-box`（需包含 with_utls, with_reality_server, with_quic, with_gvisor tags）
- python3: macOS 自带，sb 脚本用来过滤 JSON 配置
