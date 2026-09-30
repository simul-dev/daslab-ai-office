"""Disposable project UI: request input -> draft -> accept, with no model calls.

Run: python tests/project_fixture_server.py --port 8783
Stop with Ctrl+C to close services and clean the temporary directory.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch


SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))

import server as server_module
from office.organization import OrganizationEngine
from office.planner import CodePlanner
from office.providers import CodeReviewer
from office.service import Office
from office.worker import render_report
from tests.test_project_decisions import decision
from tests.test_projects import ProjectWorker


API_ENVIRONMENT = ("OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY",
                   "ANTHROPIC_AUTH_TOKEN", "AZURE_OPENAI_API_KEY")


class ProjectUIWorker(ProjectWorker):
    def __init__(self):
        super().__init__()
        request, draft, accept = (decision(action) for action in ("request_input", "draft", "accept"))
        for item in (request, draft, accept):
            item.update(problem="검증용 자료를 받아 내부 요약에 반영한다.",
                        selected_method="대표의 테스트 답변을 제공 자료로 사용해 내부 요약을 작성하고 연결을 확인한다.",
                        instruction="검증용 답변을 보존한 내부 요약을 작성한다. 실제 AI 업무나 사업 성과로 표현하지 않는다.",
                        criteria=["검증용 답변이 내부 요약에 정확히 포함된다."],
                        remaining=[] if item["action"] == "accept" else ["테스트 자료 접수와 내부 요약 검수"],
                        reason="합성 PM·직원으로 자료 질문·답변·자동 배정·검수의 실제 UI 연결만 확인한다.")
            item["metrics"] = [{"name": "테스트 답변 반영", "baseline": "답변 없음", "target": "답변 1건 반영",
                                "measurement": "저장된 대표 답변과 직원 보고서 본문 대조",
                                "result": "테스트 답변과 요약의 연결 확인" if item["action"] == "accept" else "",
                                "status": "measured" if item["action"] == "accept" else "planned"}]
        request.update(summary="[검증 전용] 내부 요약에 사용할 짧은 테스트 자료 한 가지가 필요합니다.",
                       input_requests=[{"id": "sample_data", "question": "내부 요약에 넣을 검증용 자료를 한두 문장으로 입력해 주세요.",
                                        "needed_for": "입력 답변이 PM에서 담당 직원에게 전달되는지 확인합니다.",
                                        "alternatives": "실제 회사 자료 대신 예시 수치나 짧은 테스트 문장을 입력하면 됩니다. 민감한 정보는 필요하지 않습니다."}])
        draft["summary"] = "[검증 전용] 받은 테스트 자료로 R&D에게 내부 요약을 배정합니다."
        accept["summary"] = "[검증 전용] 테스트 답변 → 내부 요약 → PM 검수 연결을 완료했습니다. 실제 AI·사업 실행은 아닙니다."
        self.decisions = [request, draft, accept]

    def probe(self, force=False):
        return {"available": True, "message": "검증 전용 합성 PM·직원 · 모델·구독·API 호출 없음"}

    def execute(self, run_dir, prompt, timeout_seconds, cancel_event, on_event=None, research=False):
        if research:
            raise AssertionError("This UI fixture performs no research or network call")
        if cancel_event.wait(1):
            return {"completed": False, "exit_code": -1, "error": "UI fixture cancelled"}
        execution = super().execute(run_dir, prompt, timeout_seconds, cancel_event, on_event, research=False)
        context = json.loads((run_dir / "input.json").read_text(encoding="utf-8"))
        answers = context["owner_goal"]["owner_answers"]
        text = answers[-1]["answers"]["sample_data"]
        result_path = run_dir / "result.json"
        document = json.loads(result_path.read_text(encoding="utf-8"))
        document.update(summary="[검증 전용] 전달받은 테스트 자료를 내부 요약에 반영했습니다.",
                        report=[{"title": "접수한 검증용 자료", "content": text},
                                {"title": "내부 요약 연결", "content": "위 문장은 실제 UI에서 입력한 테스트 답변입니다. 합성 직원이 입력을 그대로 반영했으며 외부 조사나 실제 모델 호출은 없었습니다."}],
                        limitations=["임시 DB·합성 직원의 화면 흐름 검사이며 실제 AI·고객 효과·사업 성과가 아닙니다."])
        result_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        (run_dir / "report.md").write_text(render_report(document), encoding="utf-8")
        return execution


def prepare(temporary):
    root = temporary / "office"
    root.mkdir()
    for folder in ("config", "knowledge"):
        shutil.copytree(SOURCE_ROOT / folder, root / folder)
    shutil.copytree(SOURCE_ROOT / "static", root / "static",
                    ignore=shutil.ignore_patterns("vendor", "models", "fixtures", "assets-status.json"))
    for name in ("development.json", "standing.json", "research.json", "prototypes.json"):
        path = root / "config" / name
        if path.is_file():
            policy = json.loads(path.read_text(encoding="utf-8"))
            policy["enabled"] = False
            path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
    html = root / "static/office.html"
    html.write_text(html.read_text(encoding="utf-8").replace("<title>", "<title>[검증 전용] ", 1).replace(
        "<body>", '<body><div id="project-fixture-banner" role="status">검증 전용 · PM·직원은 합성 · 임시 DB · 모델 호출 없음 · 운영 업무와 무관</div>', 1), encoding="utf-8")
    with (root / "static/office.css").open("a", encoding="utf-8") as stream:
        stream.write("\n#project-fixture-banner{padding:10px 20px;background:#624719;color:#fff;font:600 13px/1.5 sans-serif;text-align:center;overflow-wrap:anywhere}\n")
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8783)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be between 1024 and 65535")
    with tempfile.TemporaryDirectory(prefix="das-project-ui-fixture-") as temporary:
        root = prepare(Path(temporary))
        worker = ProjectUIWorker()
        with patch.dict(os.environ, {key: "" for key in API_ENVIRONMENT}):
            office = Office(root, root / "data", (CodePlanner(), worker, CodeReviewer()), start_scheduler=False)
            organization = OrganizationEngine(root, root / "data", worker=worker)
            server_module.ROOT = root
            http = None
            try:
                http = ThreadingHTTPServer(("127.0.0.1", args.port), server_module.handler_for(office, organization))
                http.daemon_threads = True
                response = organization.submit({"request_id": "project-ui-fixture-input", "employee_id": "das-pm",
                                                "execution_mode": "project", "project_profile": "research-deliverable-v1",
                                                "context": {"project_id": "daslab-growth"}, "text": "검증용 자료로 내부 요약을 작성해 주세요."})
                mission_id = response["mission"]["id"]
                deadline = time.monotonic() + 8
                while time.monotonic() < deadline:
                    detail = organization.detail(mission_id)
                    if detail["mission"]["status"] == "blocked":
                        break
                    organization.wait_revision(detail["revision"], .05)
                else:
                    raise RuntimeError("Fixture did not reach the expected input question")
                if detail["mission"]["workflow"]["stage"] != "awaiting_input":
                    raise RuntimeError("Fixture stopped for a reason other than the input question")
                print(json.dumps({"url": f"http://127.0.0.1:{args.port}/#organization", "mission_id": mission_id,
                                  "temporary_root": str(root), "status": "awaiting_input", "question_id": "sample_data",
                                  "ai_calls": 0}, ensure_ascii=False), flush=True)
                http.serve_forever(poll_interval=.1)
            except KeyboardInterrupt:
                pass
            finally:
                organization.close()
                office.close()
                if http is not None:
                    http.server_close()


if __name__ == "__main__":
    main()
