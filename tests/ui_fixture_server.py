"""Temporary UI fixtures, never a real AI worker or a production data directory.

Run: python tests/ui_fixture_server.py
Open http://127.0.0.1:8766 with the supported browser tools. Inspect the three
seeded outcomes, submit a mission, observe running -> achieved, and request a
revision. New missions take 20 seconds by default; cancel remains responsive.
All files are temporary and removed when this process exits normally.
"""
import argparse
import json
import sys
import tempfile
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from office.planner import CodePlanner
from office.providers import CodeReviewer
from office.service import Office
from office.worker import render_report
from server import handler_for


class FixtureWorker:
    """Deterministic test double: no subprocess, AI, credentials, or network."""
    provider = "ui_fixture_no_ai"

    def __init__(self, delay_seconds=20):
        self.delay_seconds = delay_seconds

    def probe(self):
        return {"provider": self.provider, "available": True, "auth_mode": "화면 검증 전용",
                "message": "가짜 작업자입니다. 실제 AI 호출·계정 사용량·외부 작업은 없습니다."}

    def execute(self, folder, prompt, timeout_seconds, cancel_event):
        if cancel_event.wait(self.delay_seconds):
            return {"exit_code": -1, "completed": False, "cancelled": True}
        task = json.loads((folder / "input.json").read_text(encoding="utf-8"))
        mission = task["goal"]
        criteria = task["acceptance_criteria"]
        blocked = "막힘 예시" in mission
        partial = "부분 달성 예시" in mission
        status = "blocked" if blocked else "partial" if partial else "achieved"
        states = ["unknown" if blocked else "unmet" if partial else "met"] * len(criteria)
        deliverables = ["공통 비교 항목 정리", "견적 조건 차이 확인 항목 정리", "동일 조건 비교 순서 제안", "최종 업체 추천"]
        milestones = []
        if partial:
            for index, criterion in enumerate(criteria):
                for position, deliverable in enumerate(deliverables):
                    milestone_state = "met" if position < 3 else "unmet"
                    milestones.append({"criterion": criterion,
                                       "deliverable": deliverable + (f" ({index + 1})" if len(criteria) > 1 else ""),
                                       "status": milestone_state,
                                       "artifact_section": "report[1].content" if milestone_state == "met" else "remaining",
                                       "explanation": "요청한 실제 산출물의 검증용 충족 여부입니다."})
        percent = (sum(item["status"] == "met" for item in milestones) * 100 // len(milestones)
                   if milestones else None if blocked else states.count("met") * 100 // len(states))
        summaries = {
            "achieved": "[화면 검증용 예시] 견적 비교표의 핵심 항목과 확인 순서를 한 페이지로 정리했습니다.",
            "partial": "[화면 검증용 예시] 비교 항목은 정리했지만 최종 업체 선정에는 빠진 견적 자료가 필요합니다.",
            "blocked": "[화면 검증용 예시] 원본 견적서를 받지 못해 금액 비교와 달성률을 확인할 수 없습니다.",
        }
        body = ("이 내용은 화면과 동작 확인을 위한 고정 예시이며 실제 AI 분석 결과가 아닙니다.\n\n"
                "견적 비교표에는 공사 범위, 자재 사양, 수량, 단가, 총액, 제외 항목을 같은 순서로 배치합니다. "
                "우선 제외 항목과 자재 사양의 차이를 확인하고, 조건이 같아진 뒤 총액을 비교합니다.")
        remaining = (["실제 비교에 사용할 원본 견적서가 필요합니다."] if blocked else
                     ["누락된 업체의 자재 사양과 제외 항목을 확인한 뒤 최종 선택을 정리합니다."] if partial else [])
        document = {
            "summary": summaries[status],
            "analysis": {"priority": "high" if blocked else "normal", "complexity": "moderate",
                         "rationale": "화면 검증을 위해 고정한 우선순위와 난이도입니다.",
                         "process": ["요청한 결과 파악", "비교 항목 정리", "남은 내용과 달성 여부 보고"]},
            "outcome": {"status": status, "progress_percent": percent,
                        "basis": "화면 검증용 완료 기준 판정입니다. 실제 업무 수행 성과가 아닙니다."},
            "report": [{"title": "보고 요약", "content": summaries[status]},
                       {"title": "견적 비교 방법", "content": body}],
            "accomplishments": [] if blocked else deliverables[:3] if partial else ["비교표에 필요한 여섯 항목을 정리했습니다.", "검토할 순서를 정했습니다."],
            "remaining": remaining,
            "milestones": milestones,
            "limitations": ["고정된 화면 검증 데이터입니다. 실제 AI 실행이나 외부 검증은 수행하지 않았습니다."],
            "evidence": [{"criterion": criterion, "status": state,
                          "artifact_section": "report[1].content" if state == "met" else "remaining",
                          "explanation": "검증용 보고 본문과 남은 일에 대응하는 고정 예시입니다."}
                         for criterion, state in zip(criteria, states)],
        }
        (folder / "prompt.txt").write_text(prompt, encoding="utf-8")
        (folder / "result.json").write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        (folder / "report.md").write_text(render_report(document, self.delay_seconds), encoding="utf-8")
        (folder / "events.jsonl").write_text('{"type":"turn.completed","source":"ui_fixture_no_ai"}\n', encoding="utf-8")
        return {"exit_code": 0, "completed": True, "source": self.provider}


class FixtureOffice(Office):
    def create(self, payload):
        if isinstance(payload, dict):
            payload = dict(payload)
            for key in ("mission", "title"):
                if isinstance(payload.get(key), str) and not payload[key].startswith("[화면 검증]"):
                    payload[key] = "[화면 검증] " + payload[key]
        return super().create(payload)


def main():
    parser = argparse.ArgumentParser(description="임시 화면 검증 서버. 실제 AI 호출 없음.")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--delay", type=float, default=20, help="새 미션의 가짜 처리 시간(초)")
    args = parser.parse_args()
    if args.delay < 0:
        parser.error("--delay must not be negative")
    with tempfile.TemporaryDirectory(prefix="daslab-ui-fixture-") as temporary:
        worker = FixtureWorker(0.05)
        office = FixtureOffice(ROOT, Path(temporary), (CodePlanner(), worker, CodeReviewer()))
        office.config.update(worker_provider=worker.provider, daily_runs=100, max_attempts=20)
        server = None
        try:
            for mission in ("달성 예시: 견적 비교 방법을 간단히 정리해 줘",
                            "부분 달성 예시: 견적서를 비교하고 업체를 추천해 줘",
                            "막힘 예시: 원본 견적서의 누락 비용을 확인해 줘"):
                result = office.submit_mission({"mission": mission})
                active = office.active.get(result["task"]["id"])
                if active:
                    active[1].join(timeout=5)
                detail = office.detail(result["task"]["id"])
                if detail["task"]["status"] not in ("completed", "review"):
                    raise RuntimeError("화면 검증 예시를 준비하지 못했습니다.")
            worker.delay_seconds = args.delay
            server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_for(office))
            server.daemon_threads = True
            print(f"UI FIXTURE READY http://127.0.0.1:{args.port} | NO AI | temporary data only | submit delay {args.delay}s", flush=True)
            server.serve_forever(poll_interval=0.1)
        except KeyboardInterrupt:
            pass
        finally:
            if server:
                server.server_close()
            office.close()


if __name__ == "__main__":
    main()
