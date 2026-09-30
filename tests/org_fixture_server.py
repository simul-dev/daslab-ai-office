"""Ephemeral UI fixture: fake execution only, never production data or an LLM.

Run: python tests/org_fixture_server.py --port 8766
Terminate the process to discard its temporary database and reports.
"""
import argparse
import json
import sys
import tempfile
import time
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from office.organization import OrganizationEngine
from office.planner import CodePlanner
from office.providers import CodeReviewer
from office.service import Office
from office.worker import render_report
from server import handler_for


class FixtureWorker:
    def __init__(self, delay):
        self.delay = delay

    def probe(self, force=False):
        return {"available": True, "auth_mode": "chatgpt", "version": "fixture-no-model",
                "message": "화면 검증용 가짜 실행기 · 실제 AI 및 구독 사용 없음"}

    def execute(self, run_dir, prompt, timeout_seconds, cancel_event, **kwargs):
        started = time.monotonic()
        if cancel_event.wait(self.delay):
            return {"provider": "fake_fixture", "completed": False, "exit_code": -1,
                    "error": "화면 검증용 실행이 중지되었습니다.",
                    "duration_seconds": round(time.monotonic() - started, 2)}
        source = json.loads((run_dir / "input.json").read_text(encoding="utf-8"))
        criteria = source["mission"]["acceptance_criteria"]
        document = {
            "summary": "화면 검증용 합성 보고서입니다. 실제 업무는 수행하지 않았습니다.",
            "analysis": {"priority": "normal", "complexity": "simple",
                         "rationale": "임시 HTTP 화면 검증", "process": ["가짜 실행 대기", "합성 보고서 반환"]},
            "outcome": {"status": "blocked", "progress_percent": None,
                        "basis": "실제 업무를 실행하지 않는 테스트 환경이므로 목표 달성을 산정하지 않습니다."},
            "report": [{"title": "화면 검증 결과", "content":
                        "담당 배정, 상태 전환, 개입, 결과 표시를 확인하기 위한 합성 자료입니다. "
                        "실제 데모 개발, 외부 조사, 마케팅 게시, 매출 성과는 수행하거나 확인하지 않았습니다."}],
            "accomplishments": [],
            "remaining": ["실제 실행 제공자 연결 및 업무 수행은 이 화면 시험의 범위 밖입니다."],
            "limitations": ["가짜 Worker 결과이며 실제 AI 응답이 아닙니다."],
            "milestones": [],
            "evidence": [{"criterion": criterion, "status": "unknown", "artifact_section": "remaining",
                          "explanation": "화면 검증용 가짜 실행기여서 실제 목표 충족을 확인하지 않았습니다."}
                         for criterion in criteria],
        }
        (run_dir / "result.json").write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        (run_dir / "report.md").write_text(render_report(document, time.monotonic() - started), encoding="utf-8")
        (run_dir / "events.jsonl").write_text('{"type":"turn.completed","fixture":true}\n', encoding="utf-8")
        return {"provider": "fake_fixture", "completed": True, "exit_code": 0, "error": None,
                "duration_seconds": round(time.monotonic() - started, 2)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--delay", type=float, default=12)
    args = parser.parse_args()
    if not 0 <= args.delay <= 120:
        parser.error("delay must be between 0 and 120 seconds")
    with tempfile.TemporaryDirectory(prefix="das-org-ui-fixture-") as temporary:
        data_dir = Path(temporary)
        worker = FixtureWorker(args.delay)
        office = Office(ROOT, data_dir, (CodePlanner(), worker, CodeReviewer()), start_scheduler=False)
        organization = OrganizationEngine(ROOT, data_dir, worker=worker)
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(office, organization))
        server.daemon_threads = True
        print(f"FAKE WORKER / TEMPORARY DATA / NO AI: http://127.0.0.1:{args.port}/", flush=True)
        print(f"Temporary data: {data_dir}", flush=True)
        try:
            server.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            pass
        finally:
            organization.close()
            office.close()
            server.server_close()


if __name__ == "__main__":
    main()
