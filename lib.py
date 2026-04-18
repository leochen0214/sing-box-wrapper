#!/usr/bin/env python3
"""sb 的 Python 工具库：每个子命令对应一个 verb，由 sb 脚本调用。

用法: python3 lib.py <verb> [args...]
"""
from __future__ import annotations

import argparse
import base64
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request


# ───────────────────────────── init ─────────────────────────────

def cmd_init_config(env_file: str, tpl_file: str, out_file: str) -> None:
    env: dict[str, str] = {}
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            key, _, value = line.partition('=')
            env[key.strip()] = value.strip()

    # CDN_UUID fallback 到 VLESS_UUID
    if 'CDN_UUID' not in env or not env['CDN_UUID']:
        env['CDN_UUID'] = env.get('VLESS_UUID', '')

    with open(tpl_file) as f:
        content = f.read()

    def replace_var(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in env:
            print(f"  Warning: ${{{key}}} 未在 .env 中定义", file=sys.stderr)
            return m.group(0)
        return env[key]

    content = re.sub(r'\$\{(\w+)\}', replace_var, content)
    config = json.loads(content)

    # 如果 shadowtls 密码为空，移除相关 outbound 并简化 proxy
    ss_pw = env.get('SS_CLIENT_PASSWORD', '')
    if not ss_pw:
        remove_tags = {'proxy', 'proxy-shadowtls', 'shadowtls-out', 'proxy-reality'}
        kept = [ob for ob in config['outbounds'] if ob.get('tag') not in remove_tags]
        reality = next((ob for ob in config['outbounds'] if ob.get('tag') == 'proxy-reality'), None)
        if reality:
            reality['tag'] = 'proxy'
            kept.insert(0, reality)
        config['outbounds'] = kept

    with open(out_file, 'w') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


# ──────────────────────── import (远程脚本) ────────────────────────
# 在 VPS 上执行，通过 ssh stdin 传入。保持为普通字符串常量，避免 heredoc。

REMOTE_IMPORT_SCRIPT = r'''import sqlite3, json, sys
db = "/usr/local/s-ui/db/s-ui.db"
conn = sqlite3.connect(db)
result = {}
row = conn.execute("SELECT config FROM clients LIMIT 1").fetchone()
if row and row[0]:
    client = json.loads(row[0])
    result["VLESS_UUID"] = client.get("vless", {}).get("uuid", "")
    result["SHADOWTLS_PASSWORD"] = client.get("shadowtls", {}).get("password", "")
    ss_client = client.get("shadowsocks16", {}).get("password", "")
    if ss_client:
        result["SS_CLIENT_PASSWORD"] = f"{ss_client}:{ss_client}"
for row in conn.execute("SELECT tag, out_json, options FROM inbounds"):
    tag, out_json_raw, options_raw = row
    out_json = json.loads(out_json_raw) if out_json_raw else {}
    options = json.loads(options_raw) if options_raw else {}
    if tag == "vless-reality":
        tls = out_json.get("tls", {})
        reality = tls.get("reality", {})
        result["REALITY_PUBLIC_KEY"] = reality.get("public_key", "")
        result["REALITY_SHORT_ID"] = reality.get("short_id", "")
        result["REALITY_SNI"] = tls.get("server_name", "")
    elif tag == "shadowtls-in":
        result["SHADOWTLS_SNI"] = options.get("handshake", {}).get("server", "")
    elif tag == "vless-ws-in":
        result["CDN_DOMAIN"] = out_json.get("tls", {}).get("server_name", "")
conn.close()
result.setdefault("SHADOWTLS_SNI", "")
result.setdefault("CDN_DOMAIN", "")
for k, v in result.items():
    print(f"{k}={v}")
'''


def cmd_remote_import_script() -> None:
    sys.stdout.write(REMOTE_IMPORT_SCRIPT)


# ───────────────────────────── log ─────────────────────────────

def cmd_log_get(base_config: str) -> None:
    with open(base_config) as f:
        print(json.load(f)['log']['level'])


def cmd_log_set(base_config: str, level: str) -> None:
    with open(base_config) as f:
        c = json.load(f)
    c['log']['level'] = level
    with open(base_config, 'w') as f:
        json.dump(c, f, indent=2, ensure_ascii=False)


# ───────────────────────────── cdn ─────────────────────────────

def cmd_cdn_current(base_config: str) -> None:
    """打印当前 proxy-cdn 的 IP 和 Host 域名，空格分隔。"""
    with open(base_config) as f:
        c = json.load(f)
    for ob in c['outbounds']:
        if ob.get('tag') == 'proxy-cdn':
            ip = ob.get('server', '')
            domain = ob.get('transport', {}).get('headers', {}).get('Host', '')
            print(ip, domain)
            return


def cmd_cdn_set_ip(base_config: str, ip: str) -> None:
    with open(base_config) as f:
        c = json.load(f)
    for ob in c['outbounds']:
        if ob.get('tag') == 'proxy-cdn':
            ob['server'] = ip
            break
    with open(base_config, 'w') as f:
        json.dump(c, f, indent=2, ensure_ascii=False)


def cmd_cdn_toggle(base_config: str, action: str) -> None:
    with open(base_config) as f:
        c = json.load(f)
    if action == 'on':
        c['_cdn_enabled'] = True
    else:
        c.pop('_cdn_enabled', None)
    with open(base_config, 'w') as f:
        json.dump(c, f, indent=2, ensure_ascii=False)


def cmd_cdn_status(base_config: str) -> None:
    """打印 on/off。"""
    with open(base_config) as f:
        c = json.load(f)
    print('on' if c.get('_cdn_enabled') else 'off')


CDN_FALLBACK_IPS = (
    '104.16.0.1 141.101.114.1 162.159.0.1 172.67.0.1 103.21.244.1 '
    '108.162.192.1 173.245.48.1 188.114.96.1 190.93.240.1 198.41.128.1'
)


def cmd_cdn_fetch_ips() -> None:
    """从 Cloudflare 拉取 IP 段并输出每段首个可用 IP；失败时输出内置列表。"""
    ips: list[str] = []
    try:
        req = urllib.request.Request(
            'https://www.cloudflare.com/ips-v4',
            headers={'User-Agent': 'sing-box'}
        )
        raw = urllib.request.urlopen(req, timeout=5).read().decode()
        for line in raw.splitlines():
            line = line.strip()
            if not re.match(r'^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+/[0-9]+$', line):
                continue
            try:
                net = ipaddress.ip_network(line)
                ips.append(str(net.network_address + 1))
            except ValueError:
                continue
    except Exception:
        pass
    print(' '.join(ips) if ips else CDN_FALLBACK_IPS)


def cmd_cdn_ms(seconds: str) -> None:
    """秒 → 毫秒（curl %{time_connect} 解析）。"""
    try:
        v = float(seconds or 0)
        print(int(v * 1000) if v > 0 else 0)
    except Exception:
        print(0)


# ──────────────────────────── check ────────────────────────────

def cmd_check_vps_ips(base_config: str) -> None:
    """输出配置中出现的 VPS IP，每行一个（跳过 proxy-cdn）。"""
    with open(base_config) as f:
        c = json.load(f)
    seen: set[str] = set()
    for ob in c.get('outbounds', []):
        ip = ob.get('server', '')
        if ip and ip[0].isdigit() and ip not in seen:
            if ob.get('tag', '') != 'proxy-cdn':
                seen.add(ip)
                print(ip)


def cmd_check_ports(ip: str) -> None:
    ports = [443, 8443, 22]
    blocked_count = 0
    for port in ports:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5)
        start = time.time()
        try:
            s.connect((ip, port))
            ms = (time.time() - start) * 1000
            print(f'  :{port:<5}  ✓ {ms:.0f}ms')
            s.close()
        except socket.timeout:
            print(f'  :{port:<5}  ✗ 超时')
            blocked_count += 1
        except ConnectionRefusedError:
            print(f'  :{port:<5}  ✗ 拒绝连接（端口未开放）')
        except Exception as e:
            print(f'  :{port:<5}  ✗ {e}')
            blocked_count += 1

    print()
    if blocked_count == len(ports):
        print(f'结论: {ip} 大概率被封（所有端口不可达）')
    elif blocked_count > 0:
        print(f'结论: {ip} 部分端口不可达，可能被针对性封端口')
    else:
        print(f'结论: {ip} 未被封，如果代理不通则是协议被 DPI 识别')


# ──────────────────────────── update ────────────────────────────

RULESET_FILES = [
    'geosite-private',
    'geosite-cn',
    'geosite-geolocation-cn',
    'geosite-category-ads-all',
    'geoip-cn',
]


def cmd_update_ruleset_list() -> None:
    print(' '.join(RULESET_FILES))


def cmd_count_rules(tmp_json: str) -> None:
    """统计 sing-box rule-set decompile 输出的规则条数。"""
    try:
        with open(tmp_json) as f:
            rules = json.load(f)['rules']
        total = sum(
            len(v) for r in rules for k, v in r.items() if isinstance(v, list)
        )
        print(total)
    except Exception:
        print('?')


# ──────────────────────── build-run-config ────────────────────────

def cmd_build_run_config(
    base_config: str,
    run_config: str,
    dir_path: str,
    cmd: str,
    default_iface: str,
) -> None:
    with open(base_config) as f:
        config = json.load(f)

    # 读取域名文件注入自定义路由规则（编辑后重启即生效，无需 sb init）
    def read_domains(filename: str) -> list[str]:
        filepath = os.path.join(dir_path, filename)
        if not os.path.isfile(filepath):
            return []
        with open(filepath) as df:
            return [l.strip() for l in df if l.strip() and not l.strip().startswith('#')]

    proxy_domains = read_domains('proxy-domains.txt')
    direct_domains = read_domains('direct-domains.txt')
    rules = config.get('route', {}).get('rules', [])
    if proxy_domains:
        idx = next((i for i, r in enumerate(rules) if 'geoip-cn' in r.get('rule_set', [])), len(rules))
        rules.insert(idx, {'outbound': 'proxy', 'domain_suffix': proxy_domains})
    if direct_domains:
        idx = next((i for i, r in enumerate(rules) if 'geosite-cn' in r.get('rule_set', [])), len(rules) - 1)
        rules.insert(idx + 1, {'outbound': 'direct', 'domain_suffix': direct_domains})

    # 检测系统 DNS（公司 DHCP 分配的 DNS，用于解析内网域名）
    dns_out = subprocess.check_output(['scutil', '--dns'], text=True)
    sys_dns = None
    for line in dns_out.splitlines():
        if 'nameserver[0]' in line:
            sys_dns = line.split(':')[-1].strip()
            break

    default_iface_val = default_iface or None

    # CDN 中转标志（模式处理后统一替换）
    cdn_enabled = config.pop('_cdn_enabled', False)

    if cmd == 'mixed':
        config['inbounds'] = [ib for ib in config['inbounds'] if ib['type'] != 'tun']
        if sys_dns:
            for s in config['dns']['servers']:
                if s['tag'] == 'local':
                    s['server'] = sys_dns
        # 重排路由规则：域名规则在前 → resolve → IP 规则在后
        # resolve 会将域名替换为 IP，之后域名规则不再匹配，所以必须先检查域名规则
        old_rules = config['route']['rules']
        domain_rules = []
        ip_rules = []
        for r in old_rules:
            if r.get('ip_is_private') or r.get('ip_cidr') or any('geoip' in rs for rs in r.get('rule_set', [])):
                ip_rules.append(r)
            else:
                domain_rules.append(r)
        config['route']['rules'] = domain_rules + [{'action': 'resolve', 'strategy': 'prefer_ipv4'}] + ip_rules
        if default_iface_val:
            config['route'].pop('auto_detect_interface', None)
            config['route']['default_interface'] = default_iface_val
    elif cmd == 'claude':
        with open(os.path.join(dir_path, 'proxy-domains.txt')) as df:
            domains = [line.strip() for line in df if line.strip() and not line.startswith('#')]
        config['inbounds'] = [ib for ib in config['inbounds'] if ib['type'] != 'tun']
        # 检测公司 PAC 代理（从 PAC 文件提取 PROXY host:port）
        corp_proxy = None
        try:
            pac_url = subprocess.check_output(
                ['networksetup', '-getautoproxyurl', 'Wi-Fi'], text=True)
            pac_match = re.search(r'URL:\s*(http\S+)', pac_url)
            if pac_match:
                pac_content = urllib.request.urlopen(pac_match.group(1), timeout=3).read().decode()
                proxy_match = re.search(r'PROXY\s+([\w.-]+):(\d+)', pac_content)
                if proxy_match:
                    corp_proxy = (proxy_match.group(1), int(proxy_match.group(2)))
        except Exception:
            pass
        sys_dns = sys_dns or '223.5.5.5'
        config['dns'] = {
            'servers': [
                {'tag': 'remote', 'type': 'tcp', 'server': '8.8.8.8', 'detour': 'proxy'},
                {'tag': 'local', 'type': 'udp', 'server': sys_dns}
            ],
            'rules': [
                {'domain_suffix': domains, 'server': 'remote'}
            ],
            'final': 'local',
            'strategy': 'prefer_ipv4'
        }
        route_final = 'direct'
        if corp_proxy:
            config['outbounds'].append({
                'type': 'http', 'tag': 'corp-proxy',
                'server': corp_proxy[0], 'server_port': corp_proxy[1]
            })
            route_final = 'corp-proxy'
        route_iface = (
            {'default_interface': default_iface_val}
            if default_iface_val else {'auto_detect_interface': True}
        )
        config['route'] = {
            **route_iface,
            'default_domain_resolver': 'local',
            'rules': [
                {'action': 'sniff'},
                {'protocol': 'dns', 'action': 'hijack-dns'},
            ] + [
                {'outbound': 'proxy', 'domain_suffix': domains},
                {'action': 'resolve', 'strategy': 'prefer_ipv4'},
                {'outbound': 'direct', 'ip_is_private': True}
            ],
            'final': route_final
        }

    # CDN 中转：替换所有 proxy → proxy-cdn（在模式处理之后，确保覆盖 claude 模式重建的 DNS）
    if cdn_enabled:
        for s in config.get('dns', {}).get('servers', []):
            if s.get('detour') == 'proxy':
                s['detour'] = 'proxy-cdn'
        for r in config.get('route', {}).get('rules', []):
            if r.get('outbound') == 'proxy':
                r['outbound'] = 'proxy-cdn'
        route_final = config.get('route', {}).get('final')
        if route_final is None or route_final == 'proxy':
            config.setdefault('route', {})['final'] = 'proxy-cdn'

    with open(run_config, 'w') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


# ──────────────────────────── export ────────────────────────────

GEOSITE_BASE = 'https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set'
GEOIP_BASE = 'https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set'


def cmd_export_ios(base: str, out: str, dir_path: str, sub_url: str) -> None:
    with open(base) as f:
        config = json.load(f)

    # 移除 mixed 入站（iPhone 不需要）
    config['inbounds'] = [ib for ib in config['inbounds'] if ib['type'] != 'mixed']

    # 移除 tun 的 route_exclude_address（SFI 不需要防环路）
    for ib in config['inbounds']:
        if ib['type'] == 'tun':
            ib.pop('route_exclude_address', None)

    # urltest → selector（允许手动切换代理节点），并加入 proxy-cdn
    for ob in config['outbounds']:
        if ob.get('type') == 'urltest':
            ob['type'] = 'selector'
            for key in ['url', 'interval', 'tolerance', 'interrupt_exist_connections']:
                ob.pop(key, None)
            if any(o.get('tag') == 'proxy-cdn' for o in config['outbounds']):
                if 'proxy-cdn' not in ob.get('outbounds', []):
                    ob['outbounds'].append('proxy-cdn')

    # 合并订阅节点（仅在 --sub 时）
    sub_file = os.path.join(dir_path, 'sub.json')
    if sub_url and os.path.isfile(sub_file):
        with open(sub_file) as f:
            sub_nodes = json.load(f)
        if sub_nodes:
            sub_tags = [n['tag'] for n in sub_nodes]
            config['outbounds'] = [
                ob for ob in config['outbounds']
                if ob.get('type') in ('selector', 'direct') or ob.get('tag') in sub_tags
            ] + [n for n in sub_nodes if n['tag'] not in {ob.get('tag') for ob in config['outbounds']}]
            for ob in config['outbounds']:
                if ob.get('type') == 'selector':
                    ob['outbounds'] = sub_tags

    def read_domains(filename: str) -> list[str]:
        filepath = os.path.join(dir_path, filename)
        if not os.path.isfile(filepath):
            return []
        with open(filepath) as f:
            return [l.strip() for l in f if l.strip() and not l.strip().startswith('#')]

    proxy_domains = read_domains('proxy-domains.txt')
    direct_domains = read_domains('direct-domains.txt')
    rules = config.get('route', {}).get('rules', [])
    if proxy_domains:
        idx = next((i for i, r in enumerate(rules) if 'geoip-cn' in r.get('rule_set', [])), len(rules))
        rules.insert(idx, {'outbound': 'proxy', 'domain_suffix': proxy_domains})
    if direct_domains:
        idx = next((i for i, r in enumerate(rules) if 'geosite-cn' in r.get('rule_set', [])), len(rules) - 1)
        rules.insert(idx + 1, {'outbound': 'direct', 'domain_suffix': direct_domains})

    # 移除 1.12+ 字段（SFI 1.11 不支持）
    config.get('route', {}).pop('default_domain_resolver', None)

    # DNS servers: 新版格式(1.12+) → 旧版格式（SFI 兼容）
    # type+server → address (如 type:tcp + server:8.8.8.8 → address:tcp://8.8.8.8)
    for s in config.get('dns', {}).get('servers', []):
        srv_type = s.pop('type', 'udp')
        server = s.pop('server', '')
        s['address'] = server if srv_type == 'udp' else f'{srv_type}://{server}'

    # DNS rules: action:reject → server:block
    dns_rules = config.get('dns', {}).get('rules', [])
    need_block = False
    for r in dns_rules:
        if r.get('action') == 'reject':
            r.pop('action')
            r['server'] = 'block'
            need_block = True
    if need_block:
        config['dns']['servers'].append({'tag': 'block', 'address': 'rcode://success'})

    # rule_set: local → remote（iPhone 没有本地 .srs 文件）
    for rs in config.get('route', {}).get('rule_set', []):
        if rs.get('type') == 'local':
            tag = rs['tag']
            rs['type'] = 'remote'
            rs['url'] = (GEOIP_BASE if tag.startswith('geoip') else GEOSITE_BASE) + '/' + tag + '.srs'
            rs.pop('path', None)

    with open(out, 'w') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


# ──────────────────────────── sub-parse ────────────────────────────

def _parse_ss(uri: str) -> dict:
    """ss://base64(method:password)@host:port#name 或 SIP002"""
    uri = uri[5:]
    tag = ''
    if '#' in uri:
        uri, tag = uri.rsplit('#', 1)
        tag = urllib.parse.unquote(tag)
    if '@' in uri:
        userinfo, hostport = uri.rsplit('@', 1)
        try:
            userinfo = base64.b64decode(userinfo + '==').decode()
        except Exception:
            pass
        method, password = userinfo.split(':', 1)
        host, port = hostport.rsplit(':', 1)
    else:
        decoded = base64.b64decode(uri + '==').decode()
        userinfo, hostport = decoded.rsplit('@', 1)
        method, password = userinfo.split(':', 1)
        host, port = hostport.rsplit(':', 1)
    return {'type': 'shadowsocks', 'tag': tag or f'ss-{host}',
            'server': host, 'server_port': int(port),
            'method': method, 'password': password}


def _parse_vmess(uri: str) -> dict:
    """vmess://base64(json)"""
    data = json.loads(base64.b64decode(uri[8:] + '==').decode())
    ob = {'type': 'vmess', 'tag': data.get('ps', f"vmess-{data['add']}"),
          'server': data['add'], 'server_port': int(data['port']),
          'uuid': data['id'], 'security': data.get('scy', 'auto'),
          'alter_id': int(data.get('aid', 0))}
    if data.get('tls') == 'tls':
        ob['tls'] = {'enabled': True, 'server_name': data.get('sni', data.get('host', ''))}
    net = data.get('net', 'tcp')
    if net == 'ws':
        ob['transport'] = {'type': 'ws', 'path': data.get('path', '/'),
                           'headers': {'Host': data.get('host', '')}}
    elif net == 'grpc':
        ob['transport'] = {'type': 'grpc', 'service_name': data.get('path', '')}
    elif net == 'h2':
        ob['transport'] = {'type': 'http', 'host': [data.get('host', '')],
                           'path': data.get('path', '/')}
    return ob


def _parse_vless(uri: str) -> dict:
    """vless://uuid@host:port?params#name"""
    parsed = urllib.parse.urlparse(uri)
    params = dict(urllib.parse.parse_qsl(parsed.query))
    tag = urllib.parse.unquote(parsed.fragment) or f'vless-{parsed.hostname}'
    ob = {'type': 'vless', 'tag': tag,
          'server': parsed.hostname, 'server_port': int(parsed.port),
          'uuid': parsed.username}
    if params.get('flow'):
        ob['flow'] = params['flow']
    security = params.get('security', '')
    if security in ('tls', 'reality'):
        tls = {'enabled': True, 'server_name': params.get('sni', parsed.hostname)}
        if params.get('fp'):
            tls['utls'] = {'enabled': True, 'fingerprint': params['fp']}
        if security == 'reality':
            tls['reality'] = {'enabled': True,
                              'public_key': params.get('pbk', ''),
                              'short_id': params.get('sid', '')}
        ob['tls'] = tls
    net = params.get('type', 'tcp')
    if net == 'ws':
        ob['transport'] = {'type': 'ws', 'path': params.get('path', '/'),
                           'headers': {'Host': params.get('host', parsed.hostname)}}
    elif net == 'grpc':
        ob['transport'] = {'type': 'grpc', 'service_name': params.get('serviceName', '')}
    return ob


def _parse_trojan(uri: str) -> dict:
    """trojan://password@host:port?params#name"""
    parsed = urllib.parse.urlparse(uri)
    params = dict(urllib.parse.parse_qsl(parsed.query))
    tag = urllib.parse.unquote(parsed.fragment) or f'trojan-{parsed.hostname}'
    ob = {'type': 'trojan', 'tag': tag,
          'server': parsed.hostname, 'server_port': int(parsed.port),
          'password': urllib.parse.unquote(parsed.username)}
    tls = {'enabled': True, 'server_name': params.get('sni', parsed.hostname)}
    if params.get('fp'):
        tls['utls'] = {'enabled': True, 'fingerprint': params['fp']}
    ob['tls'] = tls
    net = params.get('type', 'tcp')
    if net == 'ws':
        ob['transport'] = {'type': 'ws', 'path': params.get('path', '/'),
                           'headers': {'Host': params.get('host', parsed.hostname)}}
    elif net == 'grpc':
        ob['transport'] = {'type': 'grpc', 'service_name': params.get('serviceName', '')}
    return ob


def _parse_clash_proxy(p: dict) -> dict | None:
    """Clash YAML proxy → sing-box outbound"""
    ptype = p.get('type', '')
    base = {'tag': p.get('name', ''), 'server': p.get('server', ''),
            'server_port': int(p.get('port', 0))}
    if ptype == 'ss':
        return {**base, 'type': 'shadowsocks',
                'method': p.get('cipher', ''), 'password': str(p.get('password', ''))}
    elif ptype == 'vmess':
        ob = {**base, 'type': 'vmess', 'uuid': p.get('uuid', ''),
              'security': p.get('cipher', 'auto'), 'alter_id': int(p.get('alterId', 0))}
        if p.get('tls'):
            ob['tls'] = {'enabled': True, 'server_name': p.get('servername', p.get('sni', ''))}
        net = p.get('network', 'tcp')
        if net == 'ws':
            ws_opts = p.get('ws-opts', {})
            ob['transport'] = {'type': 'ws', 'path': ws_opts.get('path', '/'),
                               'headers': ws_opts.get('headers', {})}
        elif net == 'grpc':
            grpc_opts = p.get('grpc-opts', {})
            ob['transport'] = {'type': 'grpc', 'service_name': grpc_opts.get('grpc-service-name', '')}
        return ob
    elif ptype == 'vless':
        ob = {**base, 'type': 'vless', 'uuid': p.get('uuid', '')}
        if p.get('flow'):
            ob['flow'] = p['flow']
        if p.get('tls'):
            tls = {'enabled': True, 'server_name': p.get('servername', p.get('sni', ''))}
            if p.get('client-fingerprint'):
                tls['utls'] = {'enabled': True, 'fingerprint': p['client-fingerprint']}
            reality_opts = p.get('reality-opts', {})
            if reality_opts:
                tls['reality'] = {'enabled': True,
                                  'public_key': reality_opts.get('public-key', ''),
                                  'short_id': reality_opts.get('short-id', '')}
            ob['tls'] = tls
        net = p.get('network', 'tcp')
        if net == 'ws':
            ws_opts = p.get('ws-opts', {})
            ob['transport'] = {'type': 'ws', 'path': ws_opts.get('path', '/'),
                               'headers': ws_opts.get('headers', {})}
        elif net == 'grpc':
            grpc_opts = p.get('grpc-opts', {})
            ob['transport'] = {'type': 'grpc', 'service_name': grpc_opts.get('grpc-service-name', '')}
        return ob
    elif ptype == 'trojan':
        ob = {**base, 'type': 'trojan', 'password': str(p.get('password', ''))}
        tls = {'enabled': True, 'server_name': p.get('sni', p.get('server', ''))}
        if p.get('client-fingerprint'):
            tls['utls'] = {'enabled': True, 'fingerprint': p['client-fingerprint']}
        ob['tls'] = tls
        net = p.get('network', 'tcp')
        if net == 'ws':
            ws_opts = p.get('ws-opts', {})
            ob['transport'] = {'type': 'ws', 'path': ws_opts.get('path', '/'),
                               'headers': ws_opts.get('headers', {})}
        return ob
    return None


def _is_fake_node(ob: dict) -> bool:
    server = ob.get('server', '')
    if ob.get('server_port', 0) <= 0:
        return True
    if not server.isascii():
        return True
    if server in ('127.0.0.1', 'localhost', '0.0.0.0'):
        return True
    server_lower = server.lower()
    if any(kw in server_lower for kw in ['telegram', 'channel', 'join.my', 'unlock', 't.me']):
        return True
    tag = ob.get('tag', '')
    if tag and not any(c.isascii() and c.isalpha() for c in tag):
        return True
    return False


def cmd_sub_parse(sub_url: str, curl_proxy: str, dir_path: str, sub_name: str) -> None:
    if curl_proxy:
        proxy_host = curl_proxy.split('/')[-1]
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({'http': f'http://{proxy_host}', 'https': f'http://{proxy_host}'}))
    else:
        opener = urllib.request.build_opener()
    opener.addheaders = [('User-Agent', 'sing-box')]
    raw = opener.open(sub_url, timeout=15).read()

    outbounds: list[dict] = []
    text = raw.decode('utf-8', errors='ignore').lstrip()

    if text.startswith('{'):
        try:
            data = json.loads(text)
            sb_obs = data.get('outbounds', [])
            skip_types = {'direct', 'block', 'dns', 'selector', 'urltest'}
            for ob in sb_obs:
                if ob.get('type') not in skip_types:
                    outbounds.append(ob)
        except Exception as e:
            print(f'  sing-box JSON 解析失败: {e}', file=sys.stderr)
    elif 'proxies:' in text:
        import yaml
        data = yaml.safe_load(text)
        proxies = data.get('proxies', [])
        for p in proxies:
            try:
                ob = _parse_clash_proxy(p)
                if ob:
                    outbounds.append(ob)
            except Exception as e:
                print(f'  跳过 {p.get("name","?")}: {e}', file=sys.stderr)
    else:
        try:
            decoded = base64.b64decode(raw + b'==').decode('utf-8', errors='ignore')
            lines = [l.strip() for l in decoded.splitlines() if l.strip()]
        except Exception:
            lines = [l.strip() for l in text.splitlines() if l.strip()]
        for line in lines:
            try:
                if line.startswith('ss://'):
                    outbounds.append(_parse_ss(line))
                elif line.startswith('vmess://'):
                    outbounds.append(_parse_vmess(line))
                elif line.startswith('vless://'):
                    outbounds.append(_parse_vless(line))
                elif line.startswith('trojan://'):
                    outbounds.append(_parse_trojan(line))
            except Exception as e:
                print(f'  跳过: {line[:30]}... ({e})', file=sys.stderr)

    outbounds = [ob for ob in outbounds if not _is_fake_node(ob)]

    if not outbounds:
        print('Error: 未解析到任何节点', file=sys.stderr)
        sys.exit(1)

    out_file = os.path.join(dir_path, 'sub.json')
    with open(out_file, 'w') as f:
        json.dump(outbounds, f, indent=2, ensure_ascii=False)

    print(f'转换完成: {len(outbounds)} 个节点 → sub.json')
    print('')
    types: dict[str, int] = {}
    for ob in outbounds:
        t = ob['type']
        types[t] = types.get(t, 0) + 1
    print('协议统计:', ', '.join(f'{t}:{n}' for t, n in types.items()))
    print('')
    for i, ob in enumerate(outbounds):
        print(f'  {i+1:2d}. [{ob["type"]:12s}] {ob["tag"]}')

    if sub_name:
        tpl_file = os.path.join(dir_path, 'config.template.json')
        if not os.path.isfile(tpl_file):
            print(f'\nError: 模板 {tpl_file} 不存在', file=sys.stderr)
            sys.exit(1)
        with open(tpl_file) as f:
            config = json.load(f)

        tags = [ob['tag'] for ob in outbounds]
        config['outbounds'] = [
            {'type': 'selector', 'tag': 'proxy', 'outbounds': tags, 'default': tags[0]},
            *outbounds,
            {'type': 'direct', 'tag': 'direct'},
        ]

        # 收集节点 IP（IPv4/IPv6），填入 tun 的 route_exclude_address 防环路
        node_ips: set[str] = set()
        for ob in outbounds:
            server = ob.get('server', '')
            if re.match(r'^\d+\.\d+\.\d+\.\d+$', server):
                node_ips.add(f'{server}/32')
            elif ':' in server and not server.replace(':', '').replace('.', '').strip('abcdef0123456789'):
                node_ips.add(f'{server}/128')
        for ib in config.get('inbounds', []):
            if ib.get('type') == 'tun':
                ib['route_exclude_address'] = sorted(node_ips) if node_ips else []

        out_cfg = os.path.join(dir_path, f'config-{sub_name}.json')
        with open(out_cfg, 'w') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print(f'\n完整配置已生成: config-{sub_name}.json ({len(node_ips)} 个节点 IP 加入 route_exclude_address)')
        print(f'  运行: sb -c config-{sub_name}.json mixed')
        print(f'  切换节点: sb -c config-{sub_name}.json select <tag>')


# ──────────────────────────── select ────────────────────────────

def cmd_select_node(base_config: str, target: str) -> None:
    with open(base_config) as f:
        config = json.load(f)
    proxy = next((ob for ob in config.get('outbounds', [])
                  if ob.get('tag') == 'proxy' and ob.get('type') in ('selector', 'urltest')), None)
    if not proxy:
        print('Error: 配置中没有 selector/urltest 类型的 proxy outbound', file=sys.stderr)
        sys.exit(1)

    cur_type = proxy['type']
    if not target:
        if cur_type == 'urltest':
            current = '(auto - urltest 自动选最快)'
        else:
            current = proxy.get('default', '(未设置)')
        print(f'当前模式: {cur_type}')
        print(f'当前节点: {current}\n')
        print(f'可选节点 ({len(proxy["outbounds"])}):')
        for i, tag in enumerate(proxy['outbounds']):
            mark = ' *' if cur_type == 'selector' and tag == current else '  '
            print(f'{mark}{i+1:3d}. {tag}')
        print('\n  sb select auto         # 切换到自动选最快 (urltest)')
        print('  sb select <tag|序号>   # 手动选择节点')
        sys.exit(0)

    if target == 'auto':
        if cur_type == 'urltest':
            print('已是 auto 模式，无需切换')
            sys.exit(0)
        proxy['type'] = 'urltest'
        proxy.pop('default', None)
        proxy['url'] = 'https://www.gstatic.com/generate_204'
        proxy['interval'] = '3m'
        proxy['tolerance'] = 100
        proxy['interrupt_exist_connections'] = True
        with open(base_config, 'w') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print('已切换: selector → urltest (auto)')
        print('重启生效: sb stop && sb mixed  (或 tun)')
        sys.exit(0)

    if target.isdigit():
        idx = int(target) - 1
        if idx < 0 or idx >= len(proxy['outbounds']):
            print(f'Error: 序号 {target} 超出范围 (1-{len(proxy["outbounds"])})', file=sys.stderr)
            sys.exit(1)
        target = proxy['outbounds'][idx]
    elif target not in proxy['outbounds']:
        print(f'Error: 节点 "{target}" 不存在', file=sys.stderr)
        print('使用 sb select 查看可选节点', file=sys.stderr)
        sys.exit(1)

    if cur_type == 'urltest':
        proxy['type'] = 'selector'
        for key in ['url', 'interval', 'tolerance', 'interrupt_exist_connections']:
            proxy.pop(key, None)
        print(f'已切换: urltest → selector, default={target}')
    else:
        old = proxy.get('default', '(未设置)')
        proxy['default'] = target
        print(f'已切换: {old} → {target}')

    proxy['default'] = target
    with open(base_config, 'w') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    print('重启生效: sb stop && sb mixed  (或 tun)')


# ─────────────────────────── dispatcher ───────────────────────────

def main() -> None:
    p = argparse.ArgumentParser(prog='lib.py', description='sb 的 Python 工具库')
    sub = p.add_subparsers(dest='verb', required=True)

    # init
    s = sub.add_parser('init-config')
    s.add_argument('env_file')
    s.add_argument('template')
    s.add_argument('out_file')

    sub.add_parser('remote-import-script')

    # log
    s = sub.add_parser('log-get'); s.add_argument('base_config')
    s = sub.add_parser('log-set'); s.add_argument('base_config'); s.add_argument('level')

    # cdn
    s = sub.add_parser('cdn-current'); s.add_argument('base_config')
    s = sub.add_parser('cdn-set-ip'); s.add_argument('base_config'); s.add_argument('ip')
    s = sub.add_parser('cdn-toggle'); s.add_argument('base_config'); s.add_argument('action', choices=['on', 'off'])
    s = sub.add_parser('cdn-status'); s.add_argument('base_config')
    sub.add_parser('cdn-fetch-ips')
    s = sub.add_parser('cdn-ms'); s.add_argument('seconds')

    # check
    s = sub.add_parser('check-vps-ips'); s.add_argument('base_config')
    s = sub.add_parser('check-ports'); s.add_argument('ip')

    # update
    sub.add_parser('update-ruleset-list')
    s = sub.add_parser('count-rules'); s.add_argument('tmp_json')

    # build-run-config
    s = sub.add_parser('build-run-config')
    s.add_argument('base_config')
    s.add_argument('run_config')
    s.add_argument('dir_path')
    s.add_argument('cmd')
    s.add_argument('default_iface')

    # export
    s = sub.add_parser('export-ios')
    s.add_argument('base')
    s.add_argument('out')
    s.add_argument('dir_path')
    s.add_argument('sub_url', nargs='?', default='')

    # sub-parse
    s = sub.add_parser('sub-parse')
    s.add_argument('sub_url')
    s.add_argument('curl_proxy')
    s.add_argument('dir_path')
    s.add_argument('sub_name', nargs='?', default='')

    # select-node
    s = sub.add_parser('select-node')
    s.add_argument('base_config')
    s.add_argument('target', nargs='?', default='')

    args = p.parse_args()
    verb = args.verb.replace('-', '_')
    func = globals().get(f'cmd_{verb}')
    if func is None:
        print(f'未知 verb: {args.verb}', file=sys.stderr)
        sys.exit(2)

    kwargs = {k: v for k, v in vars(args).items() if k != 'verb'}
    func(**kwargs)


if __name__ == '__main__':
    main()
