"""Explicit, hash-pinned repair of bounded report classification/reference mistakes.

The supervisor must inspect every proposed future action. This helper never
decides whether an unfinished deliverable is optional and never changes claims,
assessment states, execution counts, or the original failed run artifacts.
"""
import copy
import json
import re
import uuid
from datetime import datetime, timezone

from .development import _hash, _safe
from .providers import CodeReviewer
from .worker import render_report


ARTIFACTS = ("result.json", "report.md", "events.jsonl")


def _read(path, boundary, limit=8_000_000):
    path = _safe(path, boundary)
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError("보고서 복구 파일이 없거나 크기 제한을 초과했습니다.")
    raw = path.read_bytes()
    if len(raw) > limit:
        raise ValueError("보고서 복구 파일 크기 제한을 초과했습니다.")
    return raw


def _only_failures(verification, names):
    if not isinstance(verification, dict) or verification.get("passed") is not False:
        return False
    checks = verification.get("checks")
    if not isinstance(checks, list) or not checks or any(
            not isinstance(check, dict) or type(check.get("passed")) is not bool for check in checks):
        return False
    failed = [check for check in checks if not check["passed"]]
    return len(failed) == len(names) and {check.get("name") for check in failed} == names


def _pinned_hashes(verification):
    artifacts = verification.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) != len(ARTIFACTS):
        raise ValueError("원본 보고서의 검증 해시가 없습니다.")
    hashes = {}
    for item in artifacts:
        if (not isinstance(item, dict) or item.get("name") not in ARTIFACTS
                or not isinstance(item.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", item["sha256"])
                or item["name"] in hashes):
            raise ValueError("원본 보고서의 검증 해시가 올바르지 않습니다.")
        hashes[item["name"]] = item["sha256"]
    return hashes


def _validate_request(mission_id, review_note):
    if not isinstance(mission_id, str) or not re.fullmatch(r"[a-f0-9]{32}", mission_id):
        raise ValueError("복구할 업무 ID가 올바르지 않습니다.")
    if not isinstance(review_note, str) or not review_note.strip() or len(review_note) > 4000:
        raise ValueError("구조 수정의 이유와 확인 내용을 검토 기록으로 남겨야 합니다.")


def _load_original(engine, mission_id, failed_names):
    # The public caller holds engine.changed and engine.db throughout.
    if engine.closed:
        raise ValueError("서비스가 종료 중입니다.")
    mission = engine._get("missions", mission_id)
    if (mission.get("status") != "failed" or mission.get("execution_mode") not in ("research", "analysis")
            or (engine._active and engine._active.get("mission_id") == mission_id)):
        raise ValueError("실행이 멈춘 조사·분석 보고서의 형식 오류만 복구할 수 있습니다.")
    attempts = engine._attempts(mission_id)
    if not attempts:
        raise ValueError("복구할 실행 기록이 없습니다.")
    attempt = attempts[-1]
    if (attempt.get("status") != "failed" or attempt.get("execution_mode") not in ("research", "analysis")
            or attempt.get("execution_mode") != mission["execution_mode"] or attempt.get("report_correction")
            or not isinstance(attempt.get("id"), str) or not re.fullmatch(r"[a-f0-9]{32}", attempt["id"])
            or not _only_failures(attempt.get("verification"), failed_names)):
        raise ValueError("최신 실행에서 허용한 구조 오류가 유일한 실패일 때만 복구할 수 있습니다.")
    folder = _safe(engine.data_dir / "organization-runs" / attempt["id"], engine.data_dir)
    expected_hashes = _pinned_hashes(attempt["verification"])
    originals = {name: _read(folder / name, folder) for name in ARTIFACTS}
    if {name: _hash(raw) for name, raw in originals.items()} != expected_hashes:
        raise ValueError("원본 보고서 파일이 최초 검증 이후 변경되어 복구하지 않았습니다.")
    execution_raw = _read(folder / "execution.json", folder, 1_000_000)
    execution = json.loads(execution_raw)
    if (not isinstance(execution, dict) or execution.get("completed") is not True
            or type(execution.get("exit_code")) is not int or execution["exit_code"] != 0 or execution.get("error")):
        raise ValueError("정상 종료한 실행의 보고서만 복구할 수 있습니다.")
    if mission["execution_mode"] == "research":
        for receipt in (execution.get("web_search") or {}, attempt.get("web_search") or {}):
            if (receipt.get("enabled") is not True or receipt.get("observed") is not True
                    or type(receipt.get("completed_count")) is not int or receipt["completed_count"] < 1):
                raise ValueError("실제 웹 검색 완료 기록이 없는 조사는 완료 보고로 복구할 수 없습니다.")
    document = json.loads(originals["result.json"].decode("utf-8-sig"))
    if not isinstance(document, dict):
        raise ValueError("원본 보고서 구조가 올바르지 않습니다.")
    reviewer = CodeReviewer()
    original_review = reviewer.review(folder, execution, mission["acceptance_criteria"], "daslab")
    if not _only_failures(original_review, failed_names) or _pinned_hashes(original_review) != expected_hashes:
        raise ValueError("현재 엄격 검사에서도 같은 구조 오류임을 확인하지 못했습니다.")
    return mission, attempt, folder, expected_hashes, originals, execution_raw, execution, document


def _write_correction(engine, original, corrected, review_note, change, scope):
    mission, attempt, folder, expected_hashes, originals, execution_raw, execution, _ = original
    mission_id = mission["id"]
    reviewer = CodeReviewer()
    correction_id = uuid.uuid4().hex
    corrections = _safe(folder / "report-corrections", folder)
    target = _safe(corrections / correction_id, folder)
    target.mkdir(parents=True, exist_ok=False)
    (target / "result.json").write_text(json.dumps(corrected, ensure_ascii=False, indent=2), encoding="utf-8")
    (target / "report.md").write_text(render_report(corrected, attempt.get("duration_seconds")), encoding="utf-8")
    (target / "events.jsonl").write_bytes(originals["events.jsonl"])
    (target / "execution.json").write_bytes(execution_raw)
    verification = reviewer.review(target, execution, mission["acceptance_criteria"], "daslab")
    (target / "verification.json").write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding="utf-8")
    if verification.get("passed") is not True:
        raise ValueError("수정 사본이 엄격 보고서 검사를 통과하지 못했습니다. 기존 상태와 원본을 보존했습니다.")
    corrected_hashes = _pinned_hashes(verification)
    if {name: _hash(_read(target / name, target)) for name in ARTIFACTS} != corrected_hashes:
        raise ValueError("수정 사본이 검사 후 변경되어 반영하지 않았습니다.")
    if ({name: _hash(_read(folder / name, folder)) for name in ARTIFACTS} != expected_hashes
            or _read(folder / "execution.json", folder, 1_000_000) != execution_raw):
        raise ValueError("복구 도중 원본이 변경되어 반영하지 않았습니다.")
    stamp = datetime.now(timezone.utc).isoformat()
    provenance = {"id": correction_id, "path": str(target), "operator": "supervisor_review", "created_at": stamp,
                  "note": review_note.strip(), **change,
                  "original_artifacts": expected_hashes, "corrected_artifacts": corrected_hashes,
                  "original_execution_sha256": _hash(execution_raw), "verification": verification,
                  "scope": scope}
    (target / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
    attempt["report_correction"] = provenance
    status = "blocked" if corrected["outcome"]["status"] == "blocked" else "review"
    mission.update(status=status, result=corrected, verification="structural_only", error=None,
                   updated_at=stamp, report_correction=provenance)
    engine._save("attempts", attempt)
    engine._save("missions", mission)
    engine._event("report.corrected", "검토한 보고서의 구조 오류를 수정했습니다. 원본 실행 실패와 보고서는 보존했습니다.", mission["employee_id"], mission_id)
    return {"mission_id": mission_id, "attempt_id": attempt["id"], "status": status,
            "report_correction": provenance, "verification": verification}


def recover_future_actions(engine, mission_id, expected_remaining, review_note):
    """Move an explicitly reviewed future-step list into next_actions once."""
    _validate_request(mission_id, review_note)
    if (not isinstance(expected_remaining, list) or not 1 <= len(expected_remaining) <= 100
            or any(not isinstance(item, str) or not item.strip() or len(item) > 4000 for item in expected_remaining)
            or sum(map(len, expected_remaining)) > 16000):
        raise ValueError("직접 검토한 후속 업무 원문 목록이 필요합니다.")
    with engine.changed, engine.db:
        original = _load_original(engine, mission_id, {"미완료 내용"})
        document = original[-1]
        if ((document.get("outcome") or {}).get("status") != "achieved"
                or document.get("remaining") != expected_remaining
                or document.get("next_actions") not in (None, [], expected_remaining)):
            raise ValueError("원본의 달성 상태와 검토한 후속 업무 원문 목록이 일치하지 않습니다.")
        corrected = copy.deepcopy(document)
        corrected["remaining"] = []
        corrected["next_actions"] = list(expected_remaining)
        return _write_correction(engine, original, corrected, review_note,
                                 {"kind": "future_actions", "moved_future_actions": list(expected_remaining)},
                                 "후속 업무의 필드 분류만 수정했습니다. 본문·근거·달성 주장은 그대로이며 사업 성과의 독립 검증이 아닙니다.")


def recover_report_references(engine, mission_id, expected_milestones, replacements, review_note):
    """Repair reviewed milestone criterion/path references, preserving all claims."""
    _validate_request(mission_id, review_note)
    if (not isinstance(expected_milestones, list) or not 1 <= len(expected_milestones) <= 50
            or any(not isinstance(item, dict) for item in expected_milestones)
            or not isinstance(replacements, list) or len(replacements) != len(expected_milestones)
            or any(not isinstance(item, dict) or set(item) != {"criterion", "artifact_section"} for item in replacements)):
        raise ValueError("검토한 세부 결과 원문과 기준·근거 위치만 포함한 수정 목록이 필요합니다.")
    with engine.changed, engine.db:
        original = _load_original(engine, mission_id, {"세부 결과와 원문 기준 대응", "세부 결과 근거 위치", "달성 상태와 진행률"})
        mission, document = original[0], original[-1]
        criteria = mission.get("acceptance_criteria")
        if (not isinstance(criteria, list) or len(criteria) != 1 or not isinstance(criteria[0], str)
                or not criteria[0].strip() or document.get("milestones") != expected_milestones):
            raise ValueError("단일 원문 기준과 검토한 세부 결과 목록이 정확히 일치해야 합니다.")
        corrected = copy.deepcopy(document)
        for index, replacement in enumerate(replacements):
            old = document["milestones"][index]
            snippet = old.get("criterion")
            reference = replacement.get("artifact_section")
            if (not isinstance(snippet, str) or not snippet.strip() or snippet not in criteria[0]
                    or replacement.get("criterion") != criteria[0] or not isinstance(reference, str)):
                raise ValueError("축약한 기준을 기존 단일 원문 기준으로 복원하는 수정만 허용합니다.")
            match = re.fullmatch(r"report\[(\d+)\]\.content", reference)
            report = document.get("report")
            if (not match or not isinstance(report, list) or int(match[1]) >= len(report)
                    or not isinstance(report[int(match[1])], dict)
                    or not isinstance(report[int(match[1])].get("content"), str)
                    or not report[int(match[1])]["content"].strip()):
                raise ValueError("실제 보고 본문의 단일 content 위치만 지정할 수 있습니다.")
            corrected["milestones"][index].update(replacement)
        return _write_correction(engine, original, corrected, review_note,
                                 {"kind": "milestone_references", "original_milestones": copy.deepcopy(expected_milestones),
                                  "replacements": copy.deepcopy(replacements)},
                                 "세부 결과의 원문 기준과 본문 위치만 수정했습니다. 상태·달성 판단·진행률·남은 일·본문은 변경하지 않았으며 사업 성과의 독립 검증이 아닙니다.")
