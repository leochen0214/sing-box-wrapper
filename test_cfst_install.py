"""Offline installer tests: python3 -m unittest -v test_cfst_install."""
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile


REPO = Path(__file__).resolve().parent


def binary(version):
    return ('#!/bin/sh\n[ "$1" = "-h" ] || exit 1\n'
            f'echo "CloudflareSpeedTest {version}"\n').encode()


class CfstInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sb-cfst-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shutil.copy2(REPO / 'sb', self.root / 'sb')
        commands = self.root / 'bin'
        commands.mkdir()
        uname = commands / 'uname'
        uname.write_text('#!/bin/sh\nif [ "$1" = "-s" ]; then echo "$CFST_OS"; else echo "$CFST_ARCH"; fi\n')
        uname.chmod(0o755)
        curl = commands / 'curl'
        curl.write_text(
            f'#!{sys.executable}\nimport os,pathlib,shutil,sys\n'
            'pathlib.Path(os.environ["CFST_CURL_LOG"]).write_text(sys.argv[-1])\n'
            'if os.environ.get("CFST_CURL_FAIL"): sys.exit(22)\n'
            'shutil.copyfile(os.environ["CFST_ARCHIVE"],sys.argv[sys.argv.index("-o")+1])\n')
        curl.chmod(0o755)
        self.archive = self.root / 'release.zip'
        self.env = dict(os.environ, PATH=str(commands) + os.pathsep + os.environ['PATH'],
                        CFST_OS='Darwin', CFST_ARCH='arm64', CFST_ARCHIVE=str(self.archive),
                        CFST_CURL_LOG=str(self.root / 'curl.log'))
        self.target = self.root / 'cfst'
        self.target.write_bytes(binary('v2.3.4'))
        self.target.chmod(0o755)
        for name in ('ip.txt', 'ipv6.txt', 'cdn-ips.txt', 'config.json', '.config-run.json', '.current-mode'):
            (self.root / name).write_text('existing ' + name)
        # 不可解析的配置也不应影响安装维护命令。
        self.make_archive()

    def make_archive(self, version='v2.3.5', linux=False, data=None):
        files = {'cfst': binary(version) if data is None else data, 'ip.txt': b'new default IPs'}
        if linux:
            with tarfile.open(self.archive, 'w:gz') as archive:
                for name, content in files.items():
                    member = tarfile.TarInfo(name)
                    member.size = len(content)
                    member.mode = 0o755 if name == 'cfst' else 0o644
                    archive.addfile(member, io.BytesIO(content))
        else:
            with zipfile.ZipFile(self.archive, 'w') as archive:
                for name, content in files.items():
                    archive.writestr(name, content)

    def run_cdn(self, *args):
        result = subprocess.run(['bash', str(self.root / 'sb'), 'cdn', *args],
                                cwd=self.root, env=self.env, capture_output=True,
                                text=True, timeout=15)
        self.assertEqual(list(self.root.glob('.cfst-install.*')), [])
        return result

    def test_update_latest_preserves_data_and_proxy_state(self):
        before = {p.name: p.read_bytes() for p in self.root.iterdir()
                  if p.is_file() and p.name not in ('sb', 'cfst', 'release.zip')}
        result = self.run_cdn('update')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.target.read_bytes(), binary('v2.3.5'))
        self.assertTrue(os.access(self.target, os.X_OK))
        self.assertTrue((self.root / 'curl.log').read_text().endswith('/latest/download/cfst_darwin_arm64.zip'))
        for name, content in before.items():
            self.assertEqual((self.root / name).read_bytes(), content)

    def test_install_existing_does_not_download(self):
        result = self.run_cdn('install')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('sb cdn update', result.stdout)
        self.assertFalse((self.root / 'curl.log').exists())

    def test_first_install_without_config(self):
        self.target.unlink()
        (self.root / 'config.json').unlink()
        result = self.run_cdn('install')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.target.read_bytes(), binary('v2.3.5'))

    def test_version_is_offline_and_does_not_require_config(self):
        (self.root / 'config.json').unlink()
        result = self.run_cdn('version')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('v2.3.4', result.stdout)
        self.assertFalse((self.root / 'curl.log').exists())

    def test_pinned_version_can_downgrade(self):
        self.make_archive('v2.3.3')
        result = self.run_cdn('update', 'v2.3.3')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.target.read_bytes(), binary('v2.3.3'))
        self.assertIn('/download/v2.3.3/', (self.root / 'curl.log').read_text())

    def test_same_version_does_not_replace_binary(self):
        self.make_archive('v2.3.4')
        before = self.target.stat().st_mtime_ns
        result = self.run_cdn('update')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.target.stat().st_mtime_ns, before)

    def test_download_extraction_and_validation_failures_preserve_old_binary(self):
        for failure in ('download', 'archive', 'binary', 'version'):
            with self.subTest(failure=failure):
                self.env.pop('CFST_CURL_FAIL', None)
                self.make_archive()
                if failure == 'download':
                    self.env['CFST_CURL_FAIL'] = '1'
                elif failure == 'archive':
                    self.archive.write_bytes(b'invalid archive')
                elif failure == 'binary':
                    self.make_archive(data=b'#!/bin/sh\nexit 1\n')
                result = self.run_cdn('update', *(['v2.3.3'] if failure == 'version' else []))
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.target.read_bytes(), binary('v2.3.4'))

    def test_linux_amd64_uses_tar_archive(self):
        self.env.update(CFST_OS='Linux', CFST_ARCH='x86_64')
        self.make_archive(linux=True)
        result = self.run_cdn('update')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.target.read_bytes(), binary('v2.3.5'))
        self.assertTrue((self.root / 'curl.log').read_text().endswith('cfst_linux_amd64.tar.gz'))

    def test_bad_version_or_architecture_fails_before_download(self):
        result = self.run_cdn('update', '../../wrong')
        self.assertNotEqual(result.returncode, 0)
        self.env['CFST_ARCH'] = 'unsupported'
        result = self.run_cdn('update')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / 'curl.log').exists())


if __name__ == '__main__':
    unittest.main()
