"""Run with: python3 -m unittest -v test_env_sync

Uses temporary configs and sing-box check; never starts a proxy or changes system settings.
"""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import lib


REPO = Path(__file__).resolve().parent
OLD_IP = '192.0.2.10'
NEW_IP = '198.51.100.20'


def sample_config():
    return {
        'log': {'level': 'warn'},
        'dns': {'servers': [{'tag': 'local', 'type': 'udp', 'server': '223.5.5.5'}]},
        'inbounds': [
            {'type': 'mixed', 'listen': '127.0.0.1', 'listen_port': 10887},
            {'type': 'tun', 'address': ['172.18.0.1/30'],
             'route_exclude_address': [f'{OLD_IP}/32', '10.0.0.0/8', '203.0.113.1/32']},
        ],
        'outbounds': [
            {'type': 'selector', 'tag': 'proxy',
             'outbounds': ['proxy-reality', 'proxy-cdn'], 'default': 'proxy-cdn'},
            {'type': 'vless', 'tag': 'proxy-reality', 'server': OLD_IP,
             'server_port': 443, 'uuid': '00000000-0000-4000-8000-000000000001'},
            {'type': 'shadowtls', 'tag': 'shadowtls-out', 'server': OLD_IP,
             'server_port': 443, 'version': 3, 'password': 'test-only',
             'tls': {'enabled': True, 'server_name': 'example.com'}},
            {'type': 'vless', 'tag': 'proxy-cdn', 'server': '203.0.113.1',
             'server_port': 443, 'uuid': '00000000-0000-4000-8000-000000000002'},
            {'type': 'direct', 'tag': 'direct'},
        ],
        'route': {'final': 'proxy', 'rules': [
            {'domain_suffix': ['example.org'], 'outbound': 'direct'},
        ]},
    }


@unittest.skipUnless(shutil.which('sing-box'), 'sing-box is required for config validation')
class EnvSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sb-env-sync-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.base = self.root / 'config.json'
        self.original = sample_config()
        self.base.write_text(json.dumps(self.original))
        (self.root / '.env').write_text(f'VPS_IP={NEW_IP}\nCDN_IP=203.0.113.99\n')
        (self.root / 'proxy-domains.txt').write_text('example.net\n')
        self.register_vps()

    def register_vps(self, path=None, env_file='.env', bindings=None):
        lib._register_profile(str(path or self.base), {
            'source': 'vps', 'env': env_file,
            'ip_outbounds': bindings or {'proxy-reality': 'vless', 'shadowtls-out': 'shadowtls'},
        })

    def sync(self, path=None):
        with contextlib.redirect_stderr(io.StringIO()):
            return lib._load_synced_config(str(path or self.base))

    def test_ip_change_preserves_settings_and_cdn_and_is_idempotent(self):
        result = self.sync()
        expected = copy.deepcopy(self.original)
        for ob in expected['outbounds']:
            if ob['tag'] in ('proxy-reality', 'shadowtls-out'):
                ob['server'] = NEW_IP
        expected['inbounds'][1]['route_exclude_address'] = [
            '10.0.0.0/8', '203.0.113.1/32', f'{NEW_IP}/32',
        ]
        self.assertEqual(result, expected)
        self.assertEqual(json.loads(self.base.read_text()), expected)
        modified = self.base.stat().st_mtime_ns
        self.sync()
        self.assertEqual(self.base.stat().st_mtime_ns, modified)

    def test_profile_inherits_ip_but_can_override_other_settings(self):
        profile = self.root / 'config-dmit.json'
        profile.write_text(self.base.read_text())
        (self.root / '.env.dmit').write_text('CDN_IP=203.0.113.77\n')
        self.register_vps(profile, '.env.dmit')
        result = self.sync(profile)
        self.assertEqual(result['outbounds'][1]['server'], NEW_IP)
        self.assertEqual(lib._read_env(str(self.root / '.env.dmit'))['CDN_IP'], '203.0.113.77')
        (self.root / '.env').write_text('VPS_IP=198.51.100.21\n')
        self.assertEqual(self.sync(profile)['outbounds'][1]['server'], '198.51.100.21')

    def test_other_vps_keeps_explicit_ip(self):
        profile = self.root / 'config-vps.json'
        profile.write_text(self.base.read_text())
        (self.root / '.env.vps').write_text(f'VPS_IP={OLD_IP}\n')
        self.register_vps(profile, '.env.vps')
        self.assertEqual(self.sync(profile)['outbounds'][1]['server'], OLD_IP)

    def test_subscription_is_unchanged_even_if_tag_matches_self_hosted(self):
        backup = self.root / 'config-backup.json'
        backup.write_text(self.base.read_text())
        lib._register_profile(str(backup), {'source': 'subscription'})
        original_bytes = backup.read_bytes()
        (self.root / '.env').write_text('VPS_IP=invalid\n')
        self.assertEqual(self.sync(backup), self.original)
        self.assertEqual(backup.read_bytes(), original_bytes)
        (self.root / '.env.backup').write_text(f'VPS_IP={NEW_IP}\n')
        self.assertEqual(self.sync(backup), self.original)
        self.assertEqual(backup.read_bytes(), original_bytes)

    def test_unregistered_external_config_is_unchanged(self):
        external = self.root / 'config-external.json'
        external.write_text(self.base.read_text())
        (self.root / '.env.external').write_text(f'VPS_IP={NEW_IP}\n')
        self.assertEqual(self.sync(external), self.original)

    def test_registered_profile_missing_env_is_an_error(self):
        profile = self.root / 'config-dmit.json'
        profile.write_text(self.base.read_text())
        self.register_vps(profile, '.env.dmit')
        with self.assertRaises(FileNotFoundError):
            self.sync(profile)
        self.assertEqual(json.loads(profile.read_text()), self.original)

    def test_missing_duplicate_or_changed_binding_is_an_error(self):
        for change in ('missing', 'duplicate', 'type'):
            with self.subTest(change=change):
                config = copy.deepcopy(self.original)
                if change == 'missing':
                    config['outbounds'][1]['tag'] = 'renamed'
                elif change == 'duplicate':
                    config['outbounds'].append(copy.deepcopy(config['outbounds'][1]))
                else:
                    config['outbounds'][1]['type'] = 'trojan'
                self.base.write_text(json.dumps(config))
                with self.assertRaisesRegex(ValueError, '出站 proxy-reality'):
                    self.sync()
                self.assertEqual(json.loads(self.base.read_text()), config)

    def test_invalid_or_missing_ip_leaves_config_untouched(self):
        before = self.base.read_bytes()
        for value in ('invalid', '', '999.1.2.3'):
            with self.subTest(value=value):
                (self.root / '.env').write_text(f'VPS_IP={value}\n')
                with self.assertRaisesRegex(ValueError, 'VPS_IP'):
                    self.sync()
                self.assertEqual(self.base.read_bytes(), before)
        (self.root / '.env').unlink()
        with self.assertRaises(FileNotFoundError):
            self.sync()
        self.assertEqual(self.base.read_bytes(), before)

    def test_failed_singbox_check_preserves_existing_config(self):
        invalid = copy.deepcopy(self.original)
        invalid['log']['level'] = 'invalid-level'
        self.base.write_text(json.dumps(invalid))
        before = self.base.read_bytes()
        with self.assertRaisesRegex(ValueError, '配置校验失败'):
            self.sync()
        self.assertEqual(self.base.read_bytes(), before)
        self.assertEqual(list(self.root.glob('.sb-config-*')), [])

    def test_ipv6_replaces_only_old_host_routes(self):
        (self.root / '.env').write_text('VPS_IP=2001:db8::20\n')
        result = self.sync()
        self.assertEqual(result['outbounds'][1]['server'], '2001:db8::20')
        self.assertIn('2001:db8::20/128', result['inbounds'][1]['route_exclude_address'])
        self.assertNotIn(f'{OLD_IP}/32', result['inbounds'][1]['route_exclude_address'])

    def test_init_uses_inherited_ip_and_rejects_bad_ip_without_overwriting(self):
        template = self.root / 'config.template.json'
        content = json.dumps(self.original).replace(OLD_IP, '${VPS_IP}')
        template.write_text(content)
        env = self.root / '.env.dmit'
        env.write_text('SS_CLIENT_PASSWORD=test-only\n')
        out = self.root / 'config-dmit.json'
        lib.cmd_init_config(str(env), str(template), str(out))
        self.assertEqual(json.loads(out.read_text())['outbounds'][1]['server'], NEW_IP)
        _, registry = lib._read_profiles(str(out))
        self.assertEqual(registry['profiles'][out.name], {
            'source': 'vps', 'env': '.env.dmit',
            'ip_outbounds': {'proxy-reality': 'vless', 'shadowtls-out': 'shadowtls'},
        })
        before = out.read_bytes()
        env.write_text('VPS_IP=invalid\n')
        with self.assertRaisesRegex(ValueError, 'VPS_IP'):
            lib.cmd_init_config(str(env), str(template), str(out))
        self.assertEqual(out.read_bytes(), before)

    def test_init_binds_template_variables_with_arbitrary_outbound_names(self):
        template = self.root / 'custom.template.json'
        template.write_text(json.dumps(self.original).replace(OLD_IP, '${VPS_IP}').replace('proxy-reality', 'my-vps'))
        env = self.root / '.env.custom'
        env.write_text('SS_CLIENT_PASSWORD=test-only\n')
        out = self.root / 'arbitrary-name.json'
        lib.cmd_init_config(str(env), str(template), str(out))
        (self.root / '.env').write_text('VPS_IP=198.51.100.21\n')
        self.assertEqual(self.sync(out)['outbounds'][1]['server'], '198.51.100.21')
        _, registry = lib._read_profiles(str(out))
        self.assertIn('my-vps', registry['profiles'][out.name]['ip_outbounds'])

    def test_simplified_init_registers_renamed_proxy_binding(self):
        template = self.root / 'config.template.json'
        template.write_text(json.dumps(self.original).replace(OLD_IP, '${VPS_IP}'))
        lib.cmd_init_config(str(self.root / '.env'), str(template), str(self.base))
        _, registry = lib._read_profiles(str(self.base))
        self.assertEqual(registry['profiles'][self.base.name]['ip_outbounds'], {'proxy': 'vless'})
        (self.root / '.env').write_text('VPS_IP=198.51.100.21\n')
        result = self.sync()
        proxy = next(ob for ob in result['outbounds'] if ob['tag'] == 'proxy')
        self.assertEqual(proxy['server'], '198.51.100.21')

    def test_init_does_not_invent_binding_from_a_familiar_node_name(self):
        template = self.root / 'config.template.json'
        template.write_text(json.dumps(self.original))  # 没有 ${VPS_IP} 字段
        before = self.base.read_bytes()
        with self.assertRaisesRegex(ValueError, '没有有效的 VPS_IP 出站绑定'):
            lib.cmd_init_config(str(self.root / '.env'), str(template), str(self.base))
        self.assertEqual(self.base.read_bytes(), before)

    def test_sub_overwrites_old_vps_registration_even_with_same_node_tag(self):
        (self.root / 'config.template.json').write_text(json.dumps(self.original))
        profile = self.root / 'config-backup.json'
        profile.write_text(self.base.read_text())
        (self.root / '.env.backup').write_text(f'VPS_IP={NEW_IP}\n')
        self.register_vps(profile, '.env.backup')
        source = self.root / 'source.json'
        source.write_text(json.dumps({'outbounds': [self.original['outbounds'][1]]}))
        with contextlib.redirect_stdout(io.StringIO()):
            lib.cmd_sub_parse(source.as_uri(), '', str(self.root), 'backup')
        before = profile.read_bytes()
        self.assertEqual(self.sync(profile)['outbounds'][1]['server'], OLD_IP)
        self.assertEqual(profile.read_bytes(), before)
        _, registry = lib._read_profiles(str(profile))
        self.assertEqual(registry['profiles'][profile.name], {'source': 'subscription'})
        self.assertEqual(registry['profiles'][self.base.name]['source'], 'vps')

    def test_failed_generation_restores_old_config_and_registration(self):
        invalid = copy.deepcopy(self.original)
        invalid['log']['level'] = 'invalid-level'
        before = self.base.read_bytes()
        _, registry = lib._read_profiles(str(self.base))
        with self.assertRaisesRegex(ValueError, '配置校验失败'):
            lib._write_generated_config(invalid, str(self.base), {'source': 'subscription'}, str(self.root))
        self.assertEqual(self.base.read_bytes(), before)
        self.assertEqual(lib._read_profiles(str(self.base))[1], registry)

    def test_interrupted_registration_blocks_sync_instead_of_using_stale_source(self):
        original_register = lib._register_profile
        def fail_final_registration(path, profile):
            if profile == {'source': 'subscription'}:
                raise OSError('simulated registry write failure')
            original_register(path, profile)
        with patch.object(lib, '_register_profile', side_effect=fail_final_registration):
            with self.assertRaisesRegex(OSError, 'registry write failure'):
                lib._write_generated_config(self.original, str(self.base), {'source': 'subscription'}, str(self.root))
        before = self.base.read_bytes()
        with self.assertRaisesRegex(ValueError, '配置生成尚未完成'):
            self.sync()
        self.assertEqual(self.base.read_bytes(), before)

    def test_invalid_registry_never_falls_back_to_node_name_inference(self):
        for content in ('broken JSON', '{"version": 2, "profiles": {}}',
                        '{"version": 1, "profiles": {"config.json": {"source": "unknown"}}}'):
            with self.subTest(content=content):
                (self.root / '.sb-profiles.json').write_text(content)
                before = self.base.read_bytes()
                with self.assertRaises(ValueError):
                    self.sync()
                self.assertEqual(self.base.read_bytes(), before)

    def test_external_config_validates_rulesets_from_runtime_directory(self):
        runtime = self.root / 'runtime'
        (runtime / 'ruleset').mkdir(parents=True)
        (runtime / 'proxy-domains.txt').write_text('example.net\n')
        (runtime / 'ruleset' / 'custom.json').write_text(json.dumps({
            'version': 1, 'rules': [{'domain_suffix': ['example.org']}],
        }))
        config = copy.deepcopy(self.original)
        config['route']['rule_set'] = [{
            'tag': 'custom', 'type': 'local', 'format': 'source', 'path': 'ruleset/custom.json',
        }]
        self.base.write_text(json.dumps(config))
        run = runtime / 'run.json'
        with patch.object(lib, '_detect_sys_dns', return_value=None), \
             contextlib.redirect_stderr(io.StringIO()):
            lib.cmd_build_run_config(str(self.base), str(run), str(runtime), 'mixed', '')
        self.assertEqual(json.loads(run.read_text())['outbounds'][1]['server'], NEW_IP)

    def test_check_and_export_use_updated_ip(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            lib.cmd_check_vps_ips(str(self.base))
        self.assertEqual(output.getvalue().strip(), NEW_IP)
        (self.root / '.env').write_text('VPS_IP=198.51.100.21\n')
        exported = self.root / 'export.json'
        with contextlib.redirect_stderr(io.StringIO()):
            lib.cmd_export_ios(str(self.base), str(exported), str(self.root), '')
        self.assertEqual(json.loads(exported.read_text())['outbounds'][1]['server'], '198.51.100.21')

    def test_all_runtime_modes_use_updated_ip(self):
        for mode in ('mixed', 'tun', 'claude'):
            with self.subTest(mode=mode):
                run = self.root / f'run-{mode}.json'
                with patch.object(lib, '_detect_sys_dns', return_value=None), \
                     patch.object(lib.sys, 'platform', 'linux'), \
                     contextlib.redirect_stderr(io.StringIO()):
                    lib.cmd_build_run_config(str(self.base), str(run), str(self.root), mode, '')
                self.assertEqual(json.loads(run.read_text())['outbounds'][1]['server'], NEW_IP)

    def shell_fixture(self):
        for name in ('sb', 'lib.py'):
            shutil.copy2(REPO / name, self.root / name)
        commands = self.root / 'bin'
        commands.mkdir()
        scripts = {
            'uname': 'echo Linux',
            'ip': 'echo "default via 192.0.2.1 dev en0"',
            'ps': 'echo ps >> "$GUARD_MARKER"; exit 1',
            'pgrep': 'echo pgrep >> "$GUARD_MARKER"; exit 1',
            'networksetup': 'echo networksetup >> "$GUARD_MARKER"; exit 1',
        }
        for name, script in scripts.items():
            command = commands / name
            command.write_text('#!/bin/sh\n' + script + '\n')
            command.chmod(0o755)
        env = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ['PATH'],
                   GUARD_MARKER=str(self.root / 'guard'))
        return commands, env

    def test_shell_preflight_failure_never_reaches_stop_or_system_proxy(self):
        _, env = self.shell_fixture()
        (self.root / '.sing-box.pid').write_text('999999')
        (self.root / '.current-mode').write_text('mixed')
        run = self.root / '.config-run.json'
        run.write_text('existing runtime')
        for failure in ('env', 'runtime'):
            with self.subTest(failure=failure):
                config = copy.deepcopy(self.original)
                if failure == 'runtime':
                    config['log']['level'] = 'invalid-level'
                self.base.write_text(json.dumps(config))
                (self.root / '.env').write_text(
                    'VPS_IP=invalid\n' if failure == 'env' else f'VPS_IP={OLD_IP}\n')
                result = subprocess.run(
                    ['bash', str(self.root / 'sb'), 'mixed'], cwd=self.root,
                    env=env, capture_output=True, text=True, timeout=15,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Error:', result.stderr)
                self.assertFalse((self.root / 'guard').exists(), result.stdout + result.stderr)
                self.assertEqual(run.read_text(), 'existing runtime')
                self.assertEqual((self.root / '.sing-box.pid').read_text(), '999999')

    def test_import_preserves_profile_ip_inheritance(self):
        commands, env = self.shell_fixture()
        ssh = commands / 'ssh'
        ssh.write_text('#!/bin/sh\ncat >/dev/null\nprintf "VLESS_UUID=test-only\\nCDN_DOMAIN=example.com\\n"\n')
        ssh.chmod(0o755)
        profile = self.root / '.env.dmit'
        profile.write_text('CDN_IP=203.0.113.77\n')
        result = subprocess.run(
            ['bash', str(self.root / 'sb'), 'import', 'dmit', '--ssh', 'dmit'],
            cwd=self.root, env=env, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn('\nVPS_IP=', '\n' + profile.read_text())
        self.assertEqual(lib._read_env(str(profile))['VPS_IP'], NEW_IP)

    def test_import_without_default_env_saves_ssh_ip(self):
        commands, env = self.shell_fixture()
        (self.root / '.env').unlink()
        profile = self.root / '.env.dmit'
        profile.write_text('CDN_IP=203.0.113.77\n')
        ssh = commands / 'ssh'
        ssh.write_text(
            '#!/bin/sh\nif [ "$1" = "-G" ]; then\n'
            'echo "hostname 203.0.113.50"\nelse\ncat >/dev/null\n'
            'printf "VLESS_UUID=test-only\\nCDN_DOMAIN=example.com\\n"\nfi\n')
        ssh.chmod(0o755)
        result = subprocess.run(
            ['bash', str(self.root / 'sb'), 'import', 'dmit', '--ssh', 'dmit'],
            cwd=self.root, env=env, capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('VPS_IP=203.0.113.50', profile.read_text())


if __name__ == '__main__':
    unittest.main()
