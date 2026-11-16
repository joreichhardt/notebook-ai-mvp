import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('provision', Path(__file__).parents[1] / 'provision_secrets.py')
assert spec is not None and spec.loader is not None
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
SECRET = '00000000-0000-0000-0000-000000000001'
PROJECT = '00000000-0000-0000-0000-000000000002'


class ProvisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.token = self.root / 'token'
        self.token.write_text('fixture-token')
        self.token.chmod(0o600)
        self.target = self.root / 'release'
        self.manifest = {'server_url': 'https://example.invalid', 'project_id': PROJECT,
                         'secrets': {'database_url': SECRET}}

    def result(self, args, **kwargs):
        self.assertNotIn('fixture-token', args)
        self.assertNotIn('UNRELATED_SECRET', kwargs['env'])
        if '--version' in args:
            self.assertNotIn('BWS_ACCESS_TOKEN', kwargs['env'])
            return subprocess.CompletedProcess(args, 0, b'bws 2.1.0\n')
        self.assertEqual(kwargs['env']['BWS_ACCESS_TOKEN'], 'fixture-token')
        self.assertEqual(args[-3:], ['secret', 'get', SECRET])
        return subprocess.CompletedProcess(args, 0, json.dumps(
            {'id': SECRET, 'projectId': PROJECT, 'value': 'fixture-value'}).encode())

    def test_provisions_private_files_without_inheriting_environment(self):
        with patch.dict(os.environ, {'UNRELATED_SECRET': 'never-forward'}), patch.object(m.subprocess, 'run', side_effect=self.result):
            m.provision(self.manifest, self.token, self.target)
        self.assertEqual((self.target / 'database_url').read_text(), 'fixture-value')
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.target / 'database_url').stat().st_mode & 0o777, 0o600)
        self.assertNotIn('BWS_ACCESS_TOKEN', os.environ)

    def test_failed_fetch_does_not_publish_partial_release(self):
        self.manifest['secrets']['second'] = SECRET
        count = 0
        def result(args, **kwargs):
            nonlocal count
            count += 1
            if count == 3:
                return subprocess.CompletedProcess(args, 1, b'', b'sensitive-error')
            return self.result(args, **kwargs)
        with patch.object(m.subprocess, 'run', side_effect=result), self.assertRaises(m.ProvisionError):
            m.provision(self.manifest, self.token, self.target)
        self.assertFalse(self.target.exists())
        self.assertEqual(list(self.root.glob('.secrets-*')), [])

    def test_wrong_project_is_rejected(self):
        def result(args, **kwargs):
            r = self.result(args, **kwargs)
            if '--version' not in args:
                r.stdout = r.stdout.replace(PROJECT.encode(), SECRET.encode())
            return r
        with patch.object(m.subprocess, 'run', side_effect=result), self.assertRaises(m.ProvisionError):
            m.provision(self.manifest, self.token, self.target)
        self.assertFalse(self.target.exists())

    def test_existing_release_preserved(self):
        self.target.mkdir()
        (self.target / 'old').write_text('old')
        with self.assertRaises(m.ProvisionError):
            m.provision(self.manifest, self.token, self.target)
        self.assertEqual((self.target / 'old').read_text(), 'old')

    def test_symlink_or_public_token_rejected(self):
        link = self.root / 'link'
        link.symlink_to(self.token)
        with self.assertRaises(OSError):
            m.provision(self.manifest, link, self.target)
        self.token.chmod(0o644)
        with self.assertRaises(m.ProvisionError):
            m.provision(self.manifest, self.token, self.target)

    def test_path_traversal_rejected(self):
        self.manifest['secrets'] = {'../escape': SECRET}
        with self.assertRaises(m.ProvisionError):
            m.provision(self.manifest, self.token, self.target)

    def test_unexpected_bws_version_blocks_fetch(self):
        with patch.object(m.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, b'bws 0.0.0')) as run, self.assertRaises(m.ProvisionError):
            m.provision(self.manifest, self.token, self.target)
        self.assertEqual(run.call_count, 1)
        self.assertFalse(self.target.exists())

    def test_plain_http_rejected_before_reading_token(self):
        self.manifest['server_url'] = 'http://example.invalid'
        with self.assertRaises(m.ProvisionError):
            m.provision(self.manifest, self.root / 'absent', self.target)

    def test_cli_errors_do_not_reveal_underlying_message(self):
        with patch('sys.argv', ['provision', '--manifest', str(self.root / 'absent'),
                               '--token-file', str(self.token), '--destination', str(self.target)]), \
             patch('sys.stderr') as stderr:
            self.assertEqual(m.main(), 1)
        message = ''.join(call.args[0] for call in stderr.write.call_args_list)
        self.assertEqual(message, 'Secret provisioning failed; no new release published.\n')
