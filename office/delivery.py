"""Trusted delivery of verified UI bytes; the model never receives Git authority."""
import hashlib
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path, PurePosixPath

from .development import ASSETS, REQUIRED, _safe


def _digest(data):
    return hashlib.sha256(data).hexdigest()


class DeliveryError(RuntimeError):
    """A failed delivery carrying the durable progress needed for a safe retry."""
    def __init__(self, message, receipt):
        super().__init__(message)
        self.receipt = dict(receipt, error=str(message))
        self.stage = self.receipt.get('stage', 'preflight')


class GitDelivery:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.lock = threading.RLock()

    def _git(self, *args, input=None, index=None, allowed=(0,)):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith('GIT_')}
        env.update(GIT_TERMINAL_PROMPT='0', GIT_OPTIONAL_LOCKS='0')
        if index is not None:
            env['GIT_INDEX_FILE'] = str(index)
        result = subprocess.run(['git', '-c', 'core.fsmonitor=false', '-c', 'credential.interactive=never', *args],
                                cwd=self.root, env=env, input=input, capture_output=True, timeout=45,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if result.returncode not in allowed:
            error = result.stderr.decode('utf-8', errors='replace').strip()
            raise ValueError('Git ' + args[0] + ' 실패: ' + error[:1000])
        return result.stdout

    def _text(self, *args, **kwargs):
        return self._git(*args, **kwargs).decode('utf-8').strip()

    def _path(self, name):
        if not isinstance(name, str):
            raise ValueError('UI 파일 이름이 올바르지 않습니다.')
        path = PurePosixPath(name)
        if ('\\' in name or path.as_posix() != name or path.is_absolute()
                or any(p.startswith('.') or ':' in p or p.endswith((' ', '.')) for p in path.parts)
                or not (name in REQUIRED or (name.startswith('brand/') and path.suffix.lower() in ASSETS))):
            raise ValueError('허용되지 않은 UI 파일: ' + str(name))
        return _safe(self.root / 'static' / name, self.root / 'static')

    def _current(self, name):
        path = self._path(name)
        if path.exists() and not path.is_file():
            raise ValueError('파일 경로가 디렉터리입니다: ' + name)
        return _digest(path.read_bytes()) if path.exists() else None

    def _upstream(self, branch, required, expected):
        remote = self._text('config', '--get', 'branch.' + branch + '.remote', allowed=(0, 1))
        merge = self._text('config', '--get', 'branch.' + branch + '.merge', allowed=(0, 1))
        if not remote and not merge and not required and expected is None:
            return None, None
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', remote) or merge != 'refs/heads/' + branch:
            raise ValueError('같은 이름의 기존 원격 브랜치가 upstream으로 설정되어야 합니다.')
        urls = self._text('remote', 'get-url', '--all', remote).splitlines()
        pushes = self._text('remote', 'get-url', '--push', '--all', remote).splitlines()
        if len(urls) != 1 or pushes != urls or (expected is not None and urls[0] != expected):
            raise ValueError('원격 저장소 주소가 승인된 대상과 다릅니다.')
        return remote, urls[0]

    def _remote_tip(self, remote, branch):
        ref = 'refs/heads/' + branch
        lines = self._text('ls-remote', '--exit-code', '--refs', remote, ref).splitlines()
        pairs = [line.split('\t') for line in lines]
        if len(pairs) != 1 or len(pairs[0]) != 2 or pairs[0][1] != ref:
            raise ValueError('기존 원격 브랜치의 커밋을 확인할 수 없습니다.')
        return pairs[0][0]

    def _validate_previous(self, previous, receipt, head):
        if not previous:
            return
        for key in ('root', 'files', 'baseline', 'branch'):
            if previous.get(key) != receipt.get(key):
                raise ValueError('이전 전달 기록과 현재 대상이 다릅니다: ' + key)
        for key in ('remote', 'remote_url'):
            if previous.get(key) is not None and previous[key] != receipt.get(key):
                raise ValueError('이전 전달 이후 원격 대상이 변경되었습니다.')
        base = previous.get('base_commit')
        commit = previous.get('commit')
        if not commit and previous.get('pending_commit') == head:
            commit = head
        if head != (commit or base):
            raise ValueError('이전 전달 이후 현재 브랜치가 변경되었습니다.')
        receipt.update(base_commit=base, commit=commit, applied_paths=list(previous.get('applied_paths', [])),
                       planned_paths=list(previous.get('planned_paths', [])),
                       committed_paths=list(previous.get('committed_paths', [])),
                       committed_blobs=dict(previous.get('committed_blobs', {})),
                       applied_to_live=bool(previous.get('applied_to_live')),
                       stage='committed' if commit else previous.get('stage', 'preflight'))
        if commit:
            parents = self._text('rev-list', '--parents', '-n', '1', commit).split()
            changed = set(self._git('diff-tree', '--no-commit-id', '--name-only', '--no-renames', '-r', '-z', commit).decode('utf-8').split('\0')) - {''}
            if parents != [commit, base] or changed != set(receipt['committed_paths']):
                raise ValueError('이전 커밋이 이 UI 전달만 포함하는지 확인할 수 없습니다.')
            for name, digest in receipt['files'].items():
                path = 'static/' + name
                if path not in changed:
                    continue
                exists = bool(self._git('ls-tree', commit, '--', path))
                if (digest is None and exists) or (digest is not None and (not exists or self._text('rev-parse', commit + ':' + path) != receipt['committed_blobs'].get(path))):
                    raise ValueError('이전 전달 커밋의 UI 내용이 달라졌습니다.')

    def _clean(self, receipt, allow_applied=False):
        if self._git('diff', '--cached', '--name-only', '-z'):
            raise ValueError('먼저 기존 스테이징 변경을 정리해야 합니다. 기존 변경은 보존했습니다.')
        dirty = set(self._git('diff', '--name-only', '--no-renames', '-z').decode('utf-8').split('\0')) - {''}
        allowed = set(receipt['applied_paths']) | set(receipt['planned_paths']) if allow_applied else set()
        if dirty - allowed:
            raise ValueError('다른 미커밋 변경이 있어 전달을 멈췄습니다. 기존 변경은 보존했습니다.')
        for path in dirty:
            if not path.startswith('static/') or self._current(path[7:]) != receipt['files'].get(path[7:]):
                raise ValueError('이전 적용 파일이 다시 수정되어 전달을 멈췄습니다.')
        tracked = set(self._git('ls-files', '-z').decode('utf-8').split('\0')) - {''}
        for name in receipt['files']:
            path = 'static/' + name
            if path not in tracked and self._path(name).exists() and path not in allowed:
                raise ValueError('추적되지 않은 기존 파일과 충돌합니다: ' + path)

    def _verify_live(self, receipt):
        if any(self._current(name) != digest for name, digest in receipt['files'].items()):
            receipt['applied_to_live'] = False
            raise ValueError('전달 중 운영 파일이 다시 변경되어 적용 상태를 확인할 수 없습니다.')

    def _commit(self, receipt, files, message, index_path, progress):
        descriptor, temp = tempfile.mkstemp(prefix='office-delivery-', dir=index_path.parent)
        os.close(descriptor)
        temp = Path(temp)
        temp.unlink()
        try:
            self._git('read-tree', receipt['base_commit'], index=temp)
            for name, digest in receipt['files'].items():
                # Keep unchanged Git blobs, including Git's original line-ending normalization.
                if receipt['baseline'].get(name) == digest:
                    continue
                path = 'static/' + name
                if digest is None:
                    self._git('update-index', '--force-remove', '--', path, index=temp)
                    continue
                entry = self._text('ls-tree', receipt['base_commit'], '--', path)
                mode = entry.split(' ', 1)[0] if entry else '100644'
                if mode not in ('100644', '100755'):
                    raise ValueError('일반 파일만 전달할 수 있습니다: ' + path)
                blob = self._text('hash-object', '-w', '--path=' + path, '--stdin', input=files[name])
                receipt['committed_blobs'][path] = blob
                self._git('update-index', '--add', '--cacheinfo', mode + ',' + blob + ',' + path, index=temp)
            tree = self._text('write-tree', index=temp)
            receipt['verified_tree'] = tree
            if tree == self._text('rev-parse', receipt['base_commit'] + '^{tree}'):
                # Refresh only the equivalent index tree under our existing lock;
                # normalized line-ending changes otherwise leave stale stat data.
                os.replace(temp, index_path)
                receipt['stage'] = 'unchanged'
                return
            commit = self._text('commit-tree', tree, '-p', receipt['base_commit'], input=(message.strip() + '\n').encode('utf-8'))
            paths = self._git('diff-tree', '--no-commit-id', '--name-only', '--no-renames', '-r', '-z', commit).decode('utf-8').split('\0')
            receipt.update(pending_commit=commit, committed_paths=[path for path in paths if path], stage='commit_prepared')
            progress()
            self._git('update-ref', 'refs/heads/' + receipt['branch'], commit, receipt['base_commit'])
            receipt.update(commit=commit, stage='committed')
            # We hold Git's real index lock throughout; no existing staged work is replaced.
            os.replace(temp, index_path)
            progress()
        finally:
            if temp.exists():
                temp.unlink()

    def deliver(self, files, baseline, operation, message, expected_remote_url=None, previous=None, on_progress=None):
        """Apply, commit, or push immutable verified bytes, returning a retry receipt.

        A push may contain only the commit created by this delivery. If the exact
        UI is already committed and present remotely, verify that fact without a
        new commit or push. Persist failure receipts for safe delivery retries.
        """
        receipt = {'root': str(self.root), 'operation': operation, 'applied_to_live': False,
                   'commit': None, 'pushed': False, 'remote': None, 'remote_url': None,
                   'already_synced': False, 'remote_verified': False, 'verified_commit': None,
                   'branch': None, 'stage': 'preflight', 'applied_paths': [], 'planned_paths': [], 'committed_paths': [], 'committed_blobs': {}}
        def progress():
            if on_progress:
                import copy
                on_progress(copy.deepcopy(receipt))
        with self.lock:
            index_lock = None
            lock_owned = False
            try:
                if operation not in ('apply', 'commit', 'push'):
                    raise ValueError('허용되지 않은 전달 작업입니다.')
                if not isinstance(files, dict) or not isinstance(baseline, dict) or not set(REQUIRED) <= set(files):
                    raise ValueError('검증된 UI 파일 전체와 기준 해시가 필요합니다.')
                if len(files) > 512 or len(baseline) > 512 or not isinstance(message, str) or not message.strip():
                    raise ValueError('전달 파일 수 또는 커밋 메시지가 올바르지 않습니다.')
                hashes = {}
                for name in set(files) | set(baseline):
                    self._path(name)
                    if name in baseline and not re.fullmatch(r'[a-f0-9]{64}', str(baseline[name])):
                        raise ValueError('기준 파일 해시가 올바르지 않습니다.')
                    if name in files and (not isinstance(files[name], bytes) or len(files[name]) > 10_000_000):
                        raise ValueError('검증된 파일 바이트가 필요합니다.')
                    hashes[name] = _digest(files[name]) if name in files else None
                if Path(self._text('rev-parse', '--show-toplevel')).resolve() != self.root:
                    raise ValueError('등록된 저장소 루트에서만 전달할 수 있습니다.')
                branch = self._text('symbolic-ref', '--quiet', '--short', 'HEAD')
                head = self._text('rev-parse', 'HEAD')
                for state in ('MERGE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD', 'rebase-merge', 'rebase-apply'):
                    if (self.root / self._text('rev-parse', '--git-path', state)).exists():
                        raise ValueError('진행 중인 Git 병합 또는 재배치를 먼저 마쳐야 합니다.')
                remote, url = self._upstream(branch, operation == 'push', expected_remote_url)
                receipt.update(branch=branch, base_commit=head, remote=remote, remote_url=url, files=hashes, baseline=dict(baseline))
                self._validate_previous(previous, receipt, head)
                index_path = (self.root / self._text('rev-parse', '--git-path', 'index')).resolve()
                index_lock = Path(str(index_path) + '.lock')
                descriptor = os.open(index_lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(descriptor)
                lock_owned = True
                if previous and previous.get('pending_commit') == head and self._git('diff', '--cached', '--name-only', '-z'):
                    # A crash between update-ref and index replacement leaves exactly the old tree.
                    if self._git('diff', '--cached', '--name-only', '-z', receipt['base_commit']):
                        raise ValueError('중단 이후 인덱스에 다른 변경이 있어 자동 복구하지 않습니다.')
                    descriptor, recovery = tempfile.mkstemp(prefix='office-delivery-recovery-', dir=index_path.parent)
                    os.close(descriptor)
                    os.unlink(recovery)
                    try:
                        self._git('read-tree', head, index=recovery)
                        os.replace(recovery, index_path)
                    finally:
                        if os.path.exists(recovery):
                            os.unlink(recovery)
                self._clean(receipt, bool(previous))
                if operation == 'push':
                    tip = self._remote_tip(remote, branch)
                    if tip not in (receipt['base_commit'], receipt.get('commit')):
                        raise ValueError('원격과 로컬 기준이 다르거나 다른 미푸시 커밋이 있습니다. 자동으로 합치거나 푸시하지 않습니다.')
                for name, digest in hashes.items():
                    current = self._current(name)
                    if current != baseline.get(name) and current != digest:
                        raise ValueError('미리보기 생성 이후 운영 파일이 변경되었습니다: ' + name)
                receipt['planned_paths'] = sorted(set(receipt['planned_paths']) | {'static/' + name for name, digest in hashes.items() if self._current(name) != digest})
                receipt['stage'] = 'committed' if receipt['commit'] else 'applying'
                progress()
                for name, digest in hashes.items():
                    path = self._path(name)
                    current = self._current(name)
                    if current == digest:
                        continue
                    if current != baseline.get(name):
                        raise ValueError('적용 도중 운영 파일이 변경되었습니다: ' + name)
                    if digest is None:
                        path.unlink()
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        descriptor, temporary = tempfile.mkstemp(prefix='.office-delivery-', dir=path.parent)
                        try:
                            with os.fdopen(descriptor, 'wb') as handle:
                                handle.write(files[name])
                            os.replace(temporary, path)
                        finally:
                            if os.path.exists(temporary):
                                os.unlink(temporary)
                    receipt['applied_paths'] = sorted(set(receipt['applied_paths']) | {'static/' + name})
                receipt.update(applied_to_live=True, stage='committed' if receipt['commit'] else 'applied')
                progress()
                if operation != 'apply' and receipt['commit'] is None:
                    self._clean(receipt, True)
                    self._verify_live(receipt)
                    self._commit(receipt, files, message, index_path, progress)
                if operation == 'push':
                    if not receipt['commit']:
                        self._clean(receipt)
                        self._verify_live(receipt)
                        if (receipt['stage'] != 'unchanged'
                                or self._text('rev-parse', 'HEAD') != head
                                or self._text('symbolic-ref', '--quiet', '--short', 'HEAD') != branch
                                or receipt.get('verified_tree') != self._text('rev-parse', head + '^{tree}')):
                            raise ValueError('기존 커밋과 검증된 UI가 같은지 확인할 수 없습니다.')
                        if self._upstream(branch, True, expected_remote_url) != (remote, url):
                            raise ValueError('확인 도중 원격 대상이 변경되었습니다.')
                        if self._remote_tip(remote, branch) != head:
                            raise ValueError('원격에 같은 커밋이 없어 기존 HEAD를 임의로 푸시하지 않습니다.')
                        self._clean(receipt)
                        self._verify_live(receipt)
                        if self._text('rev-parse', 'HEAD') != head:
                            raise ValueError('원격 확인 도중 현재 커밋이 변경되었습니다.')
                        receipt.update(already_synced=True, remote_verified=True,
                                       verified_commit=head, stage='already_synced')
                    else:
                        if self._text('rev-parse', 'HEAD') != receipt['commit']:
                            raise ValueError('푸시 직전 현재 커밋이 변경되었습니다.')
                        tip = self._remote_tip(remote, branch)
                        if tip not in (receipt['base_commit'], receipt['commit']):
                            raise ValueError('푸시 직전 원격 브랜치가 변경되었습니다.')
                        receipt['stage'] = 'pushing'
                        progress()
                        if tip != receipt['commit']:
                            self._git('push', '--porcelain', remote, receipt['commit'] + ':refs/heads/' + branch)
                        if self._remote_tip(remote, branch) != receipt['commit']:
                            raise ValueError('원격 저장소에서 전달 커밋을 확인하지 못했습니다.')
                        receipt.update(pushed=True, remote_verified=True,
                                       verified_commit=receipt['commit'], stage='pushed')
                    progress()
                self._verify_live(receipt)
                return receipt
            except Exception as exc:
                if previous and receipt['stage'] == 'preflight':
                    # A temporary preflight failure must not erase an already
                    # created commit needed for the next safe retry.
                    receipt = dict(previous, operation=operation)
                raise DeliveryError(str(exc), receipt) from exc
            finally:
                if lock_owned and index_lock is not None and index_lock.exists():
                    index_lock.unlink()
