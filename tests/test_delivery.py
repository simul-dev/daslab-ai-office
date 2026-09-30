"""Real Git delivery tests against disposable local repositories, never GitHub."""
import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from office.delivery import DeliveryError, GitDelivery


class GitDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project'
        self.root.mkdir()
        self.remote = Path(self.temp.name) / 'remote.git'
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Delivery test')
        self.git('config', 'user.email', 'delivery@example.invalid')
        self.git('config', 'core.autocrlf', 'false')
        self.git('init', '--bare', str(self.remote))
        (self.root / 'static').mkdir()
        self.original = {'office.html': b'<html><body>Original</body></html>\n',
                         'office.css': b'body {color: black}\n', 'office.js': b'"use strict";\n'}
        for name, data in self.original.items():
            (self.root / 'static' / name).write_bytes(data)
        (self.root / 'README.md').write_text('Existing project\n', encoding='utf-8')
        self.git('add', '.')
        self.git('commit', '-m', 'Initial')
        self.git('remote', 'add', 'origin', str(self.remote))
        self.git('push', '-u', 'origin', 'main')
        self.base = self.git('rev-parse', 'HEAD').strip()
        self.files = dict(self.original, **{'office.css': b'body {color: navy}\n'})
        self.baseline = {name: hashlib.sha256(data).hexdigest() for name, data in self.original.items()}
        self.delivery = GitDelivery(self.root)

    def git(self, *args):
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith('GIT_')}
        result = subprocess.run(['git', *args], cwd=self.root, env=env, capture_output=True, check=True)
        return result.stdout.decode('utf-8')

    def deliver(self, operation='push', **kwargs):
        return self.delivery.deliver(self.files, self.baseline, operation, 'Apply reviewed UI',
                                     expected_remote_url=str(self.remote) if operation == 'push' else None, **kwargs)

    def test_push_changes_only_preview_files_and_verifies_remote(self):
        receipt = self.deliver()
        self.assertTrue(receipt['pushed'])
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertEqual(self.git('diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD').strip(), 'static/office.css')
        self.assertIn(receipt['commit'], self.git('ls-remote', 'origin', 'refs/heads/main'))
        self.assertEqual((self.root / 'static/office.css').read_bytes(), self.files['office.css'])

    def test_apply_then_commit_then_push_uses_one_owned_commit(self):
        applied = self.deliver('apply')
        self.assertIsNone(applied['commit'])
        committed = self.deliver('commit', previous=applied)
        pushed = self.deliver('push', previous=committed)
        self.assertEqual(committed['commit'], pushed['commit'])
        self.assertEqual(self.git('rev-list', '--count', self.base + '..HEAD').strip(), '1')

    def test_existing_staged_and_unstaged_work_is_preserved(self):
        for staged in (False, True):
            with self.subTest(staged=staged):
                path = self.root / 'README.md'
                path.write_text('User work\n', encoding='utf-8')
                if staged:
                    self.git('add', 'README.md')
                before = self.git('status', '--porcelain')
                with self.assertRaises(DeliveryError):
                    self.deliver()
                self.assertEqual(self.git('status', '--porcelain'), before)
                self.assertEqual((self.root / 'static/office.css').read_bytes(), self.original['office.css'])
                self.assertEqual(self.git('rev-parse', 'HEAD').strip(), self.base)

    def test_unrelated_unpushed_commit_is_not_published(self):
        (self.root / 'README.md').write_text('Private unfinished work\n', encoding='utf-8')
        self.git('add', 'README.md')
        self.git('commit', '-m', 'Unrelated work')
        with self.assertRaises(DeliveryError):
            self.deliver()
        self.assertIn(self.base, self.git('ls-remote', 'origin', 'refs/heads/main'))

    def test_push_failure_retains_commit_and_retry_does_not_duplicate(self):
        original_git = self.delivery._git

        def offline(*args, **kwargs):
            if args[0] == 'push':
                raise ValueError('Simulated transport failure')
            return original_git(*args, **kwargs)

        with patch.object(self.delivery, '_git', side_effect=offline):
            with self.assertRaises(DeliveryError) as caught:
                self.deliver()
        receipt = caught.exception.receipt
        self.assertTrue(receipt['commit'])
        self.assertFalse(receipt['pushed'])
        resumed = self.deliver(previous=receipt)
        self.assertEqual(resumed['commit'], receipt['commit'])
        self.assertTrue(resumed['pushed'])

    def test_baseline_conflict_and_remote_change_block_before_write(self):
        old = dict(self.baseline)
        self.baseline['office.css'] = '0' * 64
        with self.assertRaises(DeliveryError):
            self.deliver()
        self.baseline = old
        self.git('remote', 'set-url', 'origin', str(self.remote) + '-wrong')
        with self.assertRaises(DeliveryError):
            self.deliver()
        self.assertEqual((self.root / 'static/office.css').read_bytes(), self.original['office.css'])

    def test_rejects_out_of_scope_paths_and_untracked_collisions(self):
        for path in ('../server.py', 'brand/.hidden.svg', 'brand/logo:stream.svg'):
            with self.subTest(path=path):
                self.files[path] = b'bad'
                with self.assertRaises(DeliveryError):
                    self.deliver('apply')
                del self.files[path]
        (self.root / 'static/brand').mkdir()
        (self.root / 'static/brand/logo.svg').write_bytes(b'User asset')
        self.files['brand/logo.svg'] = b'<svg/>'
        with self.assertRaises(DeliveryError):
            self.deliver('apply')
        self.assertEqual((self.root / 'static/brand/logo.svg').read_bytes(), b'User asset')

    def test_crlf_files_remain_clean_and_retry_preserves_git_blobs(self):
        self.git('config', 'core.autocrlf', 'true')
        for name, data in self.original.items():
            converted = data.replace(b'\n', b'\r\n')
            (self.root / 'static' / name).write_bytes(converted)
            self.baseline[name] = hashlib.sha256(converted).hexdigest()
            self.files[name] = converted
        self.files['office.css'] = b'body {color: navy}\r\n'
        receipt = self.deliver('commit')
        self.assertEqual(self.git('status', '--porcelain'), '')
        self.assertEqual(self.git('diff-tree', '--no-commit-id', '--name-only', '-r', 'HEAD').strip(), 'static/office.css')
        self.assertTrue(self.deliver('push', previous=receipt)['pushed'])

    def test_late_file_change_is_not_reported_as_success(self):
        def mutate(receipt):
            if receipt['stage'] == 'applied':
                (self.root / 'static/office.css').write_bytes(self.original['office.css'])
        with self.assertRaises(DeliveryError) as caught:
            self.deliver('apply', on_progress=mutate)
        self.assertFalse(caught.exception.receipt['applied_to_live'])

    def test_noop_verifies_existing_remote_without_commit_or_push(self):
        self.files = dict(self.original)
        with patch.object(self.delivery, '_git', wraps=self.delivery._git) as git:
            receipt = self.deliver()
        commands = [call.args[0] for call in git.call_args_list]
        self.assertFalse({'push', 'commit-tree', 'update-ref'} & set(commands))
        self.assertTrue(receipt['already_synced'])
        self.assertTrue(receipt['remote_verified'])
        self.assertTrue(receipt['applied_to_live'])
        self.assertFalse(receipt['pushed'])
        self.assertIsNone(receipt['commit'])
        self.assertEqual(receipt['verified_commit'], self.base)
        self.assertEqual(receipt['verified_tree'], self.git('rev-parse', 'HEAD^{tree}').strip())
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), self.base)
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_noop_normalized_bytes_match_existing_remote(self):
        self.git('config', 'core.autocrlf', 'true')
        self.files = {name: data.replace(b'\n', b'\r\n') for name, data in self.original.items()}
        with patch.object(self.delivery, '_git', wraps=self.delivery._git) as git:
            receipt = self.deliver()
        self.assertFalse({'push', 'commit-tree', 'update-ref'} & {call.args[0] for call in git.call_args_list})
        self.assertTrue(receipt['already_synced'])
        self.assertEqual(receipt['verified_commit'], self.base)
        self.assertEqual(self.git('status', '--porcelain'), '')
        for name, data in self.files.items():
            self.assertEqual((self.root / 'static' / name).read_bytes(), data)

    def test_noop_with_unpushed_existing_head_does_not_publish_it(self):
        self.files = dict(self.original)
        (self.root / 'README.md').write_text('Private unfinished work\n', encoding='utf-8')
        self.git('add', 'README.md')
        self.git('commit', '-m', 'Unrelated work')
        head = self.git('rev-parse', 'HEAD').strip()
        with patch.object(self.delivery, '_git', wraps=self.delivery._git) as git:
            with self.assertRaises(DeliveryError) as caught:
                self.deliver()
        self.assertFalse({'push', 'commit-tree', 'update-ref'} & {call.args[0] for call in git.call_args_list})
        self.assertFalse(caught.exception.receipt['already_synced'])
        self.assertEqual(self.git('rev-parse', 'HEAD').strip(), head)
        self.assertIn(self.base, self.git('ls-remote', 'origin', 'refs/heads/main'))

    def test_noop_rechecks_remote_and_never_pushes_a_changed_target(self):
        self.files = dict(self.original)
        with patch.object(self.delivery, '_remote_tip', side_effect=[self.base, 'f' * 40]), \
                patch.object(self.delivery, '_git', wraps=self.delivery._git) as git:
            with self.assertRaises(DeliveryError) as caught:
                self.deliver()
        self.assertFalse({'push', 'commit-tree', 'update-ref'} & {call.args[0] for call in git.call_args_list})
        self.assertFalse(caught.exception.receipt['already_synced'])
        self.assertFalse(caught.exception.receipt['remote_verified'])

    def test_noop_rechecks_live_bytes_after_remote_verification(self):
        self.files = dict(self.original)
        calls = 0
        original_tip = self.delivery._remote_tip

        def changed_while_reading_remote(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                (self.root / 'static/office.css').write_bytes(b'body {color: red}\n')
            return original_tip(*args)

        with patch.object(self.delivery, '_remote_tip', side_effect=changed_while_reading_remote), \
                patch.object(self.delivery, '_git', wraps=self.delivery._git) as git:
            with self.assertRaises(DeliveryError) as caught:
                self.deliver()
        self.assertFalse({'push', 'commit-tree', 'update-ref'} & {call.args[0] for call in git.call_args_list})
        self.assertFalse(caught.exception.receipt['already_synced'])
        self.assertEqual((self.root / 'static/office.css').read_bytes(), b'body {color: red}\n')

    def test_interruption_after_ref_update_recovers_only_own_pending_commit(self):
        original_git = self.delivery._git

        def interrupt_after_ref(*args, **kwargs):
            result = original_git(*args, **kwargs)
            if args[0] == 'update-ref':
                raise ValueError('Simulated interruption after ref update')
            return result

        with patch.object(self.delivery, '_git', side_effect=interrupt_after_ref):
            with self.assertRaises(DeliveryError) as caught:
                self.deliver()
        receipt = caught.exception.receipt
        self.assertEqual(receipt['pending_commit'], self.git('rev-parse', 'HEAD').strip())
        self.assertIsNone(receipt['commit'])
        resumed = self.deliver(previous=receipt)
        self.assertEqual(resumed['commit'], receipt['pending_commit'])
        self.assertTrue(resumed['pushed'])
        self.assertEqual(self.git('status', '--porcelain'), '')


if __name__ == '__main__':
    unittest.main()
