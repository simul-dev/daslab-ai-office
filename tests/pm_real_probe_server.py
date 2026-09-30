"""Manual subscription PM probe in an isolated repository and local bare remote.

Run with the optional browser dependencies available:
    .venv/Scripts/python.exe tests/pm_real_probe_server.py --port 8774

No mission is submitted automatically. UI submissions use the real CodexWorker
and existing subscription authentication, and consume real subscription usage.
Only fresh temporary UI files, databases and a local Git remote are used.
The temporary directory is deliberately retained after exit for evidence review.
"""
import argparse
import sys
import tempfile
from http.server import ThreadingHTTPServer
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))
sys.path.insert(0, str(SOURCE_ROOT / 'tests'))

import server as server_module
from office.organization import OrganizationEngine
from office.planner import CodePlanner
from office.providers import CodeReviewer
from office.service import Office
from office.worker import CodexWorker
from pm_fixture_server import git, prepare_repository


FIXTURE_BANNER = '검증 전용 · PM/R&amp;D 응답은 합성 · 실제 브라우저 검사 · 임시 로컬 Git · 실제 업무 데이터 없음'
PROBE_BANNER = '실제 구독 실행 검증 · 임시 작업본 · 로컬 원격 · 운영 데이터 미사용'


def prepare_probe_repository(temporary):
    root, remote = prepare_repository(temporary)
    html = root / 'static/office.html'
    source = html.read_text(encoding='utf-8')
    if FIXTURE_BANNER not in source:
        raise ValueError('Temporary fixture banner was not found; probe setup stopped')
    source = source.replace(FIXTURE_BANNER, PROBE_BANNER, 1)
    source = source.replace('<title>[검증 전용] ', '<title>[실제 구독 실행 검증] ', 1)
    html.write_text(source, encoding='utf-8')
    git(root, 'config', 'user.name', 'DAS PM Subscription Probe')
    git(root, 'config', 'user.email', 'subscription-probe@example.invalid')
    git(root, 'add', 'static/office.html')
    git(root, 'commit', '-m', 'Label isolated real subscription probe')
    git(root, 'push', 'origin', 'main')
    return root, remote


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8774)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('port must be between 1024 and 65535')

    # Keep the DB, run receipts and local Git history until evidence is saved.
    temporary = Path(tempfile.mkdtemp(prefix='das-pm-real-probe-'))
    print(f'Preserved evidence directory: {temporary}', flush=True)
    root, remote = prepare_probe_repository(temporary)
    print(f'Temporary repository: {root}', flush=True)
    print(f'Temporary bare remote: {remote}', flush=True)
    office = organization = http = None
    try:
        worker = CodexWorker()
        office = Office(root, root / 'data', (CodePlanner(), worker, CodeReviewer()), start_scheduler=False)
        organization = OrganizationEngine(root, root / 'data', worker=worker)
        server_module.ROOT = root
        http = ThreadingHTTPServer(('127.0.0.1', args.port), server_module.handler_for(office, organization))
        http.daemon_threads = True
        print(f'REAL SUBSCRIPTION / TEMP UI / LOCAL GIT: http://127.0.0.1:{args.port}/', flush=True)
        print('No automatic submission. UI requests use real subscription execution; production data is not loaded.', flush=True)
        print('Authentication and API-key environment checks are unchanged. Evidence is retained after exit.', flush=True)
        http.serve_forever(poll_interval=.1)
    except KeyboardInterrupt:
        pass
    finally:
        if organization is not None:
            organization.close()
        if office is not None:
            office.close()
        if http is not None:
            http.server_close()
        print(f'Evidence retained: {temporary}', flush=True)


if __name__ == '__main__':
    main()
