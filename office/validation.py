"""Code Reviewer: inspect execution and saved evidence, never infer truth from 'done'."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .outcomes import progress_metrics


TEXT_LIST_FIELDS = (
    "assumptions", "required_inputs", "expected_outputs", "validation_plan",
    "completion_criteria", "next_tasks", "limitations",
)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _section_exists(document, reference):
    """Allow schema fields, dot/index paths, and comma-separated field references."""
    if not _text(reference):
        return False
    references = reference.split(",")
    for raw in references:
        section = raw.strip()
        if not re.fullmatch(r"[a-z_]+(?:\.[a-z_]+|\[\d+\])*", section):
            return False
        value = document
        for segment in filter(None, re.split(r"[.\[\]]", section)):
            if isinstance(value, dict) and segment in value:
                value = value[segment]
            elif isinstance(value, list) and segment.isdigit() and int(segment) < len(value):
                value = value[int(segment)]
            else:
                return False
        if not value:
            return False
    return True


def _mission_checks(document, criteria, check):
    """Check a self-assessment's consistency without presenting it as proven truth."""
    analysis = document.get("analysis")
    analysis = analysis if isinstance(analysis, dict) else {}
    check("업무 판단", analysis.get("priority") in ("high", "normal", "low")
          and analysis.get("complexity") in ("simple", "moderate", "complex")
          and _text(analysis.get("rationale")) and isinstance(analysis.get("process"), list)
          and bool(analysis["process"]) and all(_text(step) for step in analysis["process"]),
          "우선순위, 난이도, 판단 이유와 처리 순서를 확인했습니다.")
    report = document.get("report")
    check("보고 본문", isinstance(report, list) and bool(report) and all(
        isinstance(section, dict) and _text(section.get("title")) and _text(section.get("content")) for section in report),
        "읽을 수 있는 제목과 본문이 있는지 확인했습니다.")
    for key in ("accomplishments", "remaining", "limitations"):
        value = document.get(key)
        check(key, isinstance(value, list) and all(_text(item) for item in value),
              "해당 사항이 있는 항목만 문자열 목록으로 기록해야 합니다.")

    evidence = document.get("evidence")
    valid_evidence = isinstance(evidence, list) and bool(evidence) and all(
        isinstance(item, dict) and all(_text(item.get(field)) for field in ("criterion", "artifact_section", "explanation"))
        and item.get("status") in ("met", "unmet", "unknown") for item in evidence)
    evidence_criteria = [item["criterion"] for item in evidence] if valid_evidence else []
    valid_criteria = isinstance(criteria, list) and bool(criteria) and all(_text(item) for item in criteria)
    check("완료 기준별 판단", valid_criteria and valid_evidence and len(evidence_criteria) == len(criteria)
          and len(set(evidence_criteria)) == len(evidence_criteria) and set(evidence_criteria) == set(criteria),
          "미션에서 정한 완료 기준마다 충족·미충족·확인 불가 판단이 누락과 중복 없이 있는지 확인했습니다.")

    def substantive_reference(item):
        roots = [re.split(r"[.\[]", ref.strip())[0] for ref in item["artifact_section"].split(",")]
        allowed = {"report", "accomplishments"} if item["status"] == "met" else {"report", "accomplishments", "remaining", "limitations"}
        return all(root in allowed for root in roots) and _section_exists(document, item["artifact_section"])

    check("판단 근거 위치", valid_evidence and all(substantive_reference(item) for item in evidence),
          "충족 판단이 실제 보고 본문 또는 완료한 일의 비어 있지 않은 위치를 가리키는지 확인했습니다.")
    metrics = progress_metrics(document, criteria)
    milestones = document.get("milestones", [])
    check("세부 결과와 원문 기준 대응", metrics is not None,
          "세부 결과를 나눈 경우 원문 기준 모두에 대응하고 결과명이 중복되지 않으며 하위 상태와 원문 기준 판단이 일치해야 합니다.")
    check("세부 결과 근거 위치", metrics is not None and all(substantive_reference(item) for item in milestones),
          "각 세부 결과의 완료 또는 미완료 근거가 보고서의 실제 내용을 가리키는지 확인했습니다.")
    outcome = document.get("outcome")
    outcome = outcome if isinstance(outcome, dict) else {}
    status, progress = outcome.get("status"), outcome.get("progress_percent")
    all_met = metrics is not None and metrics["all_met"]
    expected_progress = metrics["percent"] if metrics is not None else None
    valid_progress = "progress_percent" in outcome and (progress is None if expected_progress is None
                      else type(progress) is int and progress == expected_progress)
    check("달성 상태와 진행률", metrics is not None and _text(outcome.get("basis")) and valid_progress
          and ((status == "achieved" and all_met) or (status in ("partial", "blocked") and not all_met)),
          "모든 원문 기준과 세부 결과 충족만 달성입니다. 진행률은 세부 결과 완료 비율(없으면 기준 비율)이며 확인 불가 항목이 있으면 산정하지 않습니다.")
    check("미완료 내용", status == "achieved" and document.get("remaining") == []
          or status in ("partial", "blocked") and isinstance(document.get("remaining"), list)
          and bool(document["remaining"]) and all(_text(item) for item in document["remaining"]),
          "미달성 보고에는 남은 일이 있어야 하며 달성 보고에는 미완료 일이 남아 있으면 안 됩니다.")
    return outcome


def _legacy_checks(document, criteria, check):
    """Keep saved reports from the previous MVP-specific contract readable."""
    problems = document.get("confirmed_problems")
    check("확인된 문제와 출처", isinstance(problems, list) and bool(problems)
          and all(isinstance(item, dict) and _text(item.get("statement")) and _text(item.get("source")) for item in problems),
          "이전 보고서의 문제와 출처 필드를 검사했습니다. 출처의 진실성을 독립 검증한 것은 아닙니다.")
    mvp = document.get("mvp")
    check("단일 MVP와 선정 이유", isinstance(mvp, dict) and _text(mvp.get("name")) and _text(mvp.get("rationale")),
          "이전 보고서의 MVP 이름과 선정 이유를 검사했습니다.")
    for field in TEXT_LIST_FIELDS:
        value = document.get(field)
        check(field, isinstance(value, list) and bool(value) and all(_text(item) for item in value),
              f"{field}는 비어 있지 않은 문자열 목록이어야 합니다.")
    evidence = document.get("evidence")
    valid_evidence = isinstance(evidence, list) and bool(evidence) and all(
        isinstance(item, dict) and all(_text(item.get(field)) for field in ("criterion", "artifact_section", "explanation"))
        for item in evidence)
    evidence_criteria = [item["criterion"] for item in evidence] if valid_evidence else []
    valid_criteria = isinstance(criteria, list) and bool(criteria) and all(_text(item) for item in criteria)
    check("완료 기준별 근거 대응", valid_criteria and valid_evidence and len(evidence_criteria) == len(criteria)
          and len(set(evidence_criteria)) == len(evidence_criteria) and set(evidence_criteria) == set(criteria),
          "등록된 완료 기준 원문과 제출 근거가 중복·누락 없이 일치하는지 검사했습니다.")
    check("근거 위치", valid_evidence and all(_section_exists(document, item["artifact_section"]) for item in evidence),
          "각 artifact_section이 실제 존재하는 비어 있지 않은 필드인지 검사했습니다.")


def validate_run(run_dir: Path, execution: dict, criteria: list[str], project_id: str) -> dict:
    checked_at = datetime.now(timezone.utc).isoformat()
    checks, artifacts = [], []

    def check(name, passed, evidence):
        checks.append({"name": name, "passed": bool(passed), "evidence": evidence})

    files = {}
    for name in ("result.json", "report.md", "events.jsonl"):
        try:
            path = run_dir / name
            if path.is_symlink() or not path.is_file():
                raise OSError("Missing regular artifact")
            raw = path.read_bytes()
            files[name] = raw.decode("utf-8-sig")
            digest = hashlib.sha256(raw).hexdigest()
            check(name + " 파일", bool(files[name].strip()), f"{len(raw)} bytes; SHA-256 {digest}")
            artifacts.append({"name": name, "size": len(raw), "sha256": digest,
                              "source": "file:" + name, "created_at": checked_at,
                              "project_id": project_id, "verification_status": "hashed"})
        except (OSError, UnicodeError):
            check(name + " 파일", False, "파일이 없거나 UTF-8 파일로 읽을 수 없습니다.")

    exit_code = execution.get("exit_code")
    check("실행 종료", type(exit_code) is int and exit_code == 0 and execution.get("completed") is True
          and not execution.get("error"),
          f"exit_code={exit_code if type(exit_code) is int else 'unknown'}; completed={execution.get('completed') is True}")
    events = []
    try:
        events = [json.loads(line) for line in files.get("events.jsonl", "").splitlines() if line.strip()]
        valid_events = bool(events) and all(isinstance(event, dict) and _text(event.get("type")) for event in events)
    except (ValueError, TypeError):
        valid_events = False
    event_types = {event.get("type") for event in events if isinstance(event, dict) and isinstance(event.get("type"), str)}
    check("CLI 완료 이벤트", valid_events and "turn.completed" in event_types
          and not ({"turn.failed", "error"} & event_types),
          "events.jsonl의 turn.completed와 실패 이벤트 유무를 직접 검사했습니다.")

    try:
        document = json.loads(files.get("result.json", ""))
        valid_document = isinstance(document, dict)
    except (ValueError, TypeError):
        document, valid_document = {}, False
    if not valid_document:
        document = {}
    check("구조화 결과", valid_document, "result.json이 JSON 객체인지 검사했습니다.")
    check("요약", _text(document.get("summary")), "summary는 비어 있지 않은 문자열이어야 합니다.")
    mission_report = any(field in document for field in ("analysis", "outcome", "report"))
    outcome = _mission_checks(document, criteria, check) if mission_report else None
    if not mission_report:
        _legacy_checks(document, criteria, check)
    passed = all(item["passed"] for item in checks)
    return {"passed": passed, "checks": checks, "artifacts": artifacts,
            "review_required": not mission_report,
            "outcome": outcome if passed else None,
            "assessment_source": "ai_self_assessment" if mission_report else "legacy_report",
            "scope": "실행 기록, 파일·해시, 보고서 구조, 완료 기준별 근거 및 진행률 일관성을 검사했습니다. 달성 판단은 AI의 자체 평가이며 내용의 진실성이나 외부 실행을 독립 검증하지 않습니다.",
            "source": "reviewer:code", "created_at": checked_at, "project_id": project_id,
            "verification_status": ("report_checks_passed" if mission_report else "basic_checks_passed_human_review_required") if passed else "failed"}
