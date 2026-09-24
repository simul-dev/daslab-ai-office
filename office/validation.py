"""Code Reviewer: inspect execution and saved evidence, never infer truth from 'done'."""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


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
    problems = document.get("confirmed_problems")
    check("확인된 문제와 출처", isinstance(problems, list) and bool(problems)
          and all(isinstance(item, dict) and _text(item.get("statement")) and _text(item.get("source")) for item in problems),
          "confirmed_problems의 각 statement와 source를 검사했습니다. 출처의 진실성 판단은 대표 검토 대상입니다.")
    mvp = document.get("mvp")
    check("단일 MVP와 선정 이유", isinstance(mvp, dict) and _text(mvp.get("name")) and _text(mvp.get("rationale")),
          "mvp 객체의 name과 rationale을 검사했습니다.")
    for field in TEXT_LIST_FIELDS:
        value = document.get(field)
        check(field, isinstance(value, list) and bool(value) and all(_text(item) for item in value),
              f"{field}는 비어 있지 않은 문자열 목록이어야 합니다.")

    evidence = document.get("evidence")
    valid_evidence = isinstance(evidence, list) and bool(evidence) and all(
        isinstance(item, dict) and all(_text(item.get(field)) for field in ("criterion", "artifact_section", "explanation"))
        for item in evidence
    )
    evidence_criteria = [item["criterion"] for item in evidence] if valid_evidence else []
    valid_criteria = isinstance(criteria, list) and bool(criteria) and all(_text(item) for item in criteria)
    check("완료 기준별 근거 대응", valid_criteria and valid_evidence and len(evidence_criteria) == len(criteria)
          and set(evidence_criteria) == set(criteria),
          "대표가 등록한 완료 기준 원문과 evidence.criterion이 중복·누락 없이 일치하는지 검사했습니다.")
    check("근거 위치", valid_evidence and all(_section_exists(document, item["artifact_section"]) for item in evidence),
          "각 artifact_section이 result.json에 실제 존재하는 비어 있지 않은 필드인지 검사했습니다.")

    passed = all(item["passed"] for item in checks)
    return {"passed": passed, "checks": checks, "artifacts": artifacts, "review_required": True,
            "scope": "실행 완료, 파일·해시, 구조 및 완료 기준 대응을 검사했습니다. 내용의 진실성·사업성·고객 업무 검증 완료 여부는 대표 검토가 필요합니다.",
            "source": "reviewer:code", "created_at": checked_at, "project_id": project_id,
            "verification_status": "basic_checks_passed_human_review_required" if passed else "failed"}
