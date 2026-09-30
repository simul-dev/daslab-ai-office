"""Temporary PM workflow UI fixture: synthetic decisions, real browser and local Git.

Run with the optional browser dependencies installed:
    .venv/Scripts/python.exe tests/pm_fixture_server.py --port 8773 --exercise-revision

The optional revision scenario deliberately creates mobile overflow on the first
worker pass and removes that fixture-only defect on the second. Browser receipts
are always produced by the real BrowserVerifier. No model, GitHub remote, or
existing project data is used. Stop the server to discard its temporary files.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))
sys.path.insert(0, str(SOURCE_ROOT / 'tests'))

import server as server_module
from office.organization import OrganizationEngine
from office.planner import CodePlanner
from office.providers import CodeReviewer
from office.service import Office
from office.worker import render_report
from test_organization_development import FakeDevWorker


DEFECT = '\n/* PM_FIXTURE_OVERFLOW_START */\nhtml { min-width: 1200px; }\n/* PM_FIXTURE_OVERFLOW_END */\n'
CRITERIA = ['검증 전용 작업본에 CSS 변경을 남기고 실제 브라우저의 기본 화면 검사를 통과한다.']
API_ENVIRONMENT = ('OPENAI_API_KEY', 'CODEX_API_KEY', 'ANTHROPIC_API_KEY',
                   'ANTHROPIC_AUTH_TOKEN', 'AZURE_OPENAI_API_KEY')


def git(root, *args):
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    return subprocess.run(['git', '-C', str(root), *args], check=True, shell=False,
                          capture_output=True, text=True, timeout=20, env=env).stdout.strip()


class PMFixtureWorker(FakeDevWorker):
    def __init__(self, exercise_revision=False):
        super().__init__()
        self.exercise_revision = exercise_revision
        self.development_calls = 0
        self.decision_calls = 0

    def probe(self, force=False):
        return {'available': True, 'auth_mode': 'chatgpt', 'version': 'pm-fixture-no-ai',
                'message': '검증 전용: 합성 PM·R&D 응답 / 실제 브라우저 검사 / 임시 로컬 Git'}

    def execute(self, run_dir, prompt, timeout_seconds, cancel_event, on_event=None, workspace_dir=None):
        if cancel_event.is_set():
            return {'completed': False, 'exit_code': -1, 'error': '검증 전용 작업 취소'}
        self.development_calls += 1
        execution = super().execute(run_dir, prompt, timeout_seconds, cancel_event, on_event, workspace_dir)
        css = workspace_dir / 'static/office.css'
        source = css.read_text(encoding='utf-8').replace(DEFECT, '')
        if self.exercise_revision and self.development_calls == 1:
            source += DEFECT
        css.write_text(source, encoding='utf-8')
        path = run_dir / 'result.json'
        document = json.loads(path.read_text(encoding='utf-8'))
        document.update(
            summary='[검증 전용 · 합성 R&D 응답] 임시 작업본에 CSS 변경을 남겼습니다. 실제 브라우저 검사는 다음 서버 단계에서 수행합니다.',
            report=[{'title': '검증 전용 합성 작업 보고',
                     'content': '실제 사업 업무나 AI 개발 결과가 아닙니다. 테스트 실행기가 임시 CSS 파일을 변경했습니다. '
                                '화면 검사와 로컬 Git 결과는 서버가 별도로 실행하여 기록합니다.'}],
            accomplishments=['테스트용 분리 작업본의 CSS 파일 변경'],
            limitations=['PM·R&D 응답은 합성입니다. 구독·API·GitHub·기존 업무 데이터는 사용하지 않습니다.'],
            remaining=['서버의 실제 브라우저 확인과 PM 검수, 요청한 범위의 임시 로컬 Git 반영'],
        )
        # The worker cannot claim the browser checks that have not run yet.
        document['outcome'] = {'status': 'partial', 'progress_percent': 0,
                               'basis': 'CSS 파일 변경만 수행했습니다. 완료 기준의 브라우저 검사는 다음 단계입니다.'}
        for evidence in document['evidence']:
            evidence.update(status='unmet', artifact_section='remaining',
                            explanation='서버의 실제 브라우저 검사 결과가 아직 없습니다.')
        path.write_text(json.dumps(document, ensure_ascii=False), encoding='utf-8')
        (run_dir / 'report.md').write_text(render_report(document, 0), encoding='utf-8')
        execution['provider'] = 'pm_fixture_no_ai'
        return execution

    def execute_decision(self, run_dir, context, timeout_seconds, cancel_event, on_event=None):
        started = time.monotonic()
        self.decision_calls += 1
        if cancel_event.is_set():
            return {'completed': False, 'exit_code': -1, 'error': '검증 전용 PM 판단 취소'}
        if context['stage'] == 'plan':
            decision = {
                'action': 'delegate',
                'summary': '[검증 전용 · 합성 PM 판단] R&D에 임시 화면 CSS 변경을 배정합니다.',
                'instruction': '검증 전용 조직 화면의 CSS 변경을 만들어 주세요. 실제 사업 개발이라고 보고하지 말고 서버가 브라우저 검사를 하도록 결과를 넘겨 주세요.',
                'criteria': CRITERIA,
                'reason': '실제 엔진의 배정·작업·브라우저 검사·검수·로컬 Git 연결을 시험합니다.',
            }
        else:
            receipt = context.get('browser_verification') or {}
            passed = receipt.get('status') == 'passed'
            flow = context['mission'].get('workflow') or {}
            can_revise = flow.get('round', 0) < context.get('max_revisions', 0)
            issues = [item.get('name', '') + ': ' + str(item.get('detail', ''))
                      for item in receipt.get('checks', []) if item.get('status') == 'failed']
            issues += receipt.get('errors', [])
            decision = {
                'action': 'accept' if passed else ('revise' if can_revise else 'blocked'),
                'summary': '[검증 전용 · 합성 PM 판단] ' + ('실제 브라우저 기본 검사를 통과했습니다. 요청한 임시 Git 반영으로 이어갑니다.'
                                                         if passed else '실제 브라우저 검사 실패를 확인했습니다. ' + ('R&D에 수정을 맡깁니다.' if can_revise else '수정 한도에 도달했습니다.')),
                'instruction': ('검사한 동일 작업본을 대표 지시 범위에서 임시 로컬 Git에 반영하세요.' if passed
                                else '검증 전용 CSS의 PM_FIXTURE_OVERFLOW 구간을 제거하고 화면 검사를 다시 받으세요. 실패 내용: ' + '; '.join(issues)[:3000]),
                'criteria': CRITERIA,
                'reason': ('서버가 생성한 실제 브라우저 검사 receipt.status=passed를 확인했습니다. 미적용 Git 작업을 완료됐다고 주장하지 않습니다.'
                           if passed else '브라우저 검사를 통과하지 않았으므로 반영을 승인하지 않습니다. ' + receipt.get('summary', '검사 결과 없음')),
            }
        (run_dir / 'result.json').write_text(json.dumps(decision, ensure_ascii=False), encoding='utf-8')
        (run_dir / 'events.jsonl').write_text('{"type":"turn.completed","fixture":true}\n', encoding='utf-8')
        if on_event:
            on_event({'type': 'turn.completed'})
        return {'provider': 'pm_fixture_no_ai', 'completed': True, 'exit_code': 0,
                'error': None, 'duration_seconds': round(time.monotonic() - started, 3)}


def prepare_repository(temporary):
    root, remote = temporary / 'office', temporary / 'origin.git'
    root.mkdir()
    for folder in ('config', 'knowledge'):
        shutil.copytree(SOURCE_ROOT / folder, root / folder)
    # UI source and branding are copied; downloaded voice model caches are not needed.
    shutil.copytree(SOURCE_ROOT / 'static', root / 'static',
                    ignore=shutil.ignore_patterns('vendor', 'models', 'fixtures', 'assets-status.json'))
    html = root / 'static/office.html'
    source = html.read_text(encoding='utf-8').replace('<title>', '<title>[검증 전용] ', 1)
    source = source.replace('<body>', '<body><div id="pm-fixture-banner" role="status">검증 전용 · PM/R&amp;D 응답은 합성 · 실제 브라우저 검사 · 임시 로컬 Git · 실제 업무 데이터 없음</div>', 1)
    html.write_text(source, encoding='utf-8')
    css = root / 'static/office.css'
    with css.open('a', encoding='utf-8') as stream:
        stream.write('\n#pm-fixture-banner{padding:10px 20px;background:#694814;color:#fff;font:600 13px/1.5 sans-serif;text-align:center;overflow-wrap:anywhere}\n')
    policy_path = root / 'config/development.json'
    policy = json.loads(policy_path.read_text(encoding='utf-8'))
    policy['delivery']['remote_url'] = remote.as_posix()
    policy['managed_pm'] = {'enabled': True, 'max_revisions': 2}
    policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding='utf-8')
    (root / '.gitignore').write_text('data/\n', encoding='utf-8')
    hooks = temporary / 'empty-hooks'
    hooks.mkdir()
    git(temporary, 'init', '--bare', '--initial-branch=main', str(remote))
    git(root, 'init', '--initial-branch=main')
    for key, value in (('user.name', 'DAS PM UI Fixture'), ('user.email', 'fixture@example.invalid'),
                       ('core.autocrlf', 'false'), ('core.hooksPath', str(hooks)),
                       ('commit.gpgsign', 'false'), ('push.gpgsign', 'false')):
        git(root, 'config', key, value)
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'Seed temporary PM workflow UI fixture')
    git(root, 'remote', 'add', 'origin', remote.as_posix())
    git(root, 'push', '-u', 'origin', 'main')
    return root, remote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8773)
    parser.add_argument('--exercise-revision', action='store_true',
                        help='Introduce real mobile overflow once so PM must request revision')
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('port must be between 1024 and 65535')
    with tempfile.TemporaryDirectory(prefix='das-pm-ui-fixture-') as folder:
        root, remote = prepare_repository(Path(folder))
        worker = PMFixtureWorker(args.exercise_revision)
        with patch.dict(os.environ, {key: '' for key in API_ENVIRONMENT}):
            office = Office(root, root / 'data', (CodePlanner(), worker, CodeReviewer()), start_scheduler=False)
            organization = OrganizationEngine(root, root / 'data', worker=worker)
            server_module.ROOT = root
            http = ThreadingHTTPServer(('127.0.0.1', args.port), server_module.handler_for(office, organization))
            http.daemon_threads = True
            print(f'FAKE PM/RD / REAL BROWSER / TEMP LOCAL GIT / NO AI: http://127.0.0.1:{args.port}/', flush=True)
            print(f'Temporary repository: {root}', flush=True)
            print(f'Temporary bare remote: {remote}', flush=True)
            print(f'Exercise real browser failure and revision: {args.exercise_revision}', flush=True)
            print('UI request: 디자인을 개선하고 화면과 동작을 확인한 뒤 반영하고 커밋·푸시까지 끝내 줘.', flush=True)
            try:
                http.serve_forever(poll_interval=.1)
            except KeyboardInterrupt:
                pass
            finally:
                organization.close()
                office.close()
                http.server_close()


if __name__ == '__main__':
    main()
