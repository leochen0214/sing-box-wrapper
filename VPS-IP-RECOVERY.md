# 备用订阅与 VPS 换 IP 操作

2026-11-01 检查环境：本机 sing-box 1.12.22；当前主配置为 config.json，运行模式为 claude。

## 已准备的备用配置

订阅服务：`https://nongliba.cc.cd/sub`，使用用户保存的 token。本文不保存 token。
已拉取并转换为 `config-backup.json` 和 `sub.json`，过滤提示节点后共 32 个 VLESS 节点，
节点 server 均不同于当前自建 VPS 的地址。已开启 URLTest 自动测速，每 3 分钟测一次。
配置通过本机 `sing-box check`；临时独立端口实测 Google 返回 204、Cloudflare 返回 200。
临时测试进程已关闭，当前主配置未切换。

这说明客户端连接地址不依赖当前 VPS IP；订阅服务背后的部署依赖没有核实。
CDN 节点仍依赖它们各自的源站，不能把订阅地址可访问等同于全部节点可用。

### 故障时直接启用缓存

在任意目录执行，沿用当前公司网络模式：

```bash
sb -c ~/.config/sing-box-wrapper/config-backup.json claude
```

普通系统代理模式：

```bash
sb -c ~/.config/sing-box-wrapper/config-backup.json mixed
```

需要全局 TUN 时将 `mixed` 改为 `tun`，不必给 sb 加 sudo 前缀。
这些启动命令会停止现有实例并使用备用配置启动；在终端前台运行，Ctrl+C 停止。
不需要在故障时重新下载订阅。

恢复自建 VPS：

```bash
sb claude
```

### 更新备用订阅

在网络正常、或已运行备用配置后执行，用完整订阅 URL 替换占位文本：

```bash
cd ~/.config/sing-box-wrapper
sb sub '完整订阅URL（含 token）' backup
sb -c config-backup.json select auto
sing-box check -c config-backup.json
sb -c config-backup.json claude
```

`sub` 会重新生成配置，默认选第一个节点，所以更新后需重新执行 `select auto`。
当前 sb 没有定时更新订阅，也没有在主配置失效时自动切到备用配置。
当 sb 正在运行时，`sb sub` 会通过当前代理下载；当前代理已坏时，先切到缓存备用配置。

## 同一台 VPS 只更换公网 IP

当前 IP 为 `64.186.227.78`，下面的 `新IP` 应替换为服务商分配的新地址。

1. 更新 Cloudflare 中指向旧 VPS 的源站 A 记录；如果 IPv6 也更换，更新相关 AAAA 记录。
   覆盖 `nongliba.cc`、`www` 等实际使用的记录；使用 CNAME 的名称应检查其目标。
   `proxy-cdn` 仍依赖 Cloudflare 能连接新的源站。
2. 修改 `~/.ssh/config` 中 `Host dmit` 的 `HostName` 为新 IP，确认 `ssh dmit` 能登录。
3. 修改 `/Users/chenlong35/python/nongli/.env`：

   ```dotenv
   DEPLOY_HOST=root@新IP
   ```

   端口有变化时同步修改 SSH_PORT。DOMAIN、DEPLOY_PATH 在域名和目录没变化时保持原值。
   现有 nginx 模板监听通配地址、转发至 127.0.0.1，无须因公网 IP 更换逐个修改模板。

4. 更新 S-UI 中发布的直连节点地址：

   ```bash
   cd /Users/chenlong35/python/nongli
   npm run deploy:sui
   ```

   已检查该脚本：它从 DEPLOY_HOST 提取 IP，更新 `inbounds.addrs`、`clients.links`
   中的 IPv4 地址，保留域名和端口，有变化时重启 S-UI。
   `npm run sui:on` 只开启反代，不能代替这个步骤。
   同机仅换 IP 时一般无需重新部署网页或重新申请相同域名的证书。

5. 修改本机 sb 的 `.env`，将 VPS_IP 更新为新 IP：

   ```dotenv
   VPS_IP=新IP
   ```

   `.env.dmit` 已移除重复的 VPS_IP，会继承 `.env`，无需再改一处。
   默认配置、dmit 配置及备用订阅已登记到 `.sb-profiles.json`，sb 根据登记的来源和字段绑定同步，
   不根据节点名称猜测；这个文件由 sb 自动维护。
   直接启动即可同步配置并切回主连接：

   ```bash
   cd ~/.config/sing-box-wrapper
   sb claude
   ```

   启动时会同步当前配置的直连地址和 TUN 的 route_exclude_address，保留 CDN 优选地址、
   节点选择和其他设置；新配置校验通过后才停止旧实例，失败时保留现有连接。
   `sb -c config-dmit.json claude`、`sb check` 和 `sb export dmit` 同样会读取最新 IP。
   旧 `.env.vps` 属于另一个历史 profile，仅仍在使用时才需单独处理。

6. 验证网站、代理及使用该 VPS 的其他设备。iPhone/OpenWrt 中写死 IP 的配置也需更新；
   远程订阅需重新拉取，导入的静态文件需重新导出/导入。

如果服务端凭据也改变，在修改本机 VPS_IP 后运行以下命令，重新导入并生成完整配置：

```bash
sb import --ssh dmit
sb import dmit --ssh dmit
sb init
sb init dmit
```

注意：`.env` 是本机 sb 的 VPS_IP 来源，SSH alias 用于连接；仅修改 SSH HostName
不会修改 `.env` 中的 VPS_IP。命名 profile 的 import 会保留既有的 IP 继承关系。

## 重装或迁移到另一台服务器

这与同机换 IP 不同。先恢复 S-UI 数据库和凭据、网站文件、nginx/SNI/CDN 配置及证书，
确保服务能启动，再更新 DNS、客户端和订阅。仓库中的 `docs/s-ui.db` 是否最新需另行确认，
不能直接把 `npm run sui:recover` 当成同机换 IP 的处理方式。

## 参考

- [sing-box URLTest](https://sing-box.sagernet.org/configuration/outbound/urltest/)
- [Cloudflare DNS 记录管理](https://developers.cloudflare.com/dns/manage-dns-records/how-to/create-dns-records/)
