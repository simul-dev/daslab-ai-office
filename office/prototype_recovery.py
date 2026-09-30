"""Explicit postprocessing recheck of an immutable, completed prototype run.

No employee is rerun and generated tests are never executed on the host. Original
attempt fields and artifacts remain intact; new evidence has its own directory.
"""
import copy
import json
import re
import uuid
from datetime import datetime, timezone

from .development import _hash, _safe
from .network_checks import NetworkVerifier
from .prototypes import DEFAULT_PROFILE, NETWORK_PROFILE
from .providers import CodeReviewer
from .report_recovery import ARTIFACTS, _pinned_hashes, _read


def _write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _load(engine, mission_id):
    if engine.closed or engine._active:
        raise ValueError("실행 중이거나 종료 중에는 데모 후처리를 재검수할 수 없습니다.")
    mission = engine._get("missions", mission_id)
    if (mission.get("status") not in ("failed", "blocked") or mission.get("execution_mode") != "prototype"
            or mission.get("pending_action")):
        raise ValueError("멈춘 데모 업무의 후처리만 재검수할 수 있습니다.")
    engine._require_prototyper(mission["employee_id"])
    attempts = engine._attempts(mission_id)
    if not attempts:
        raise ValueError("재검수할 실행 기록이 없습니다.")
    attempt = attempts[-1]
    allowed = ("failed",) if mission["status"] == "failed" else ("failed", "blocked", "completed")
    if (attempt.get("status") not in allowed or attempt.get("execution_mode") != "prototype"
            or attempt.get("mission_id") != mission_id
            or not isinstance(attempt.get("id"), str) or not re.fullmatch(r"[a-f0-9]{32}", attempt["id"])
            or (attempt.get("verification") or {}).get("passed") is not True
            or (attempt.get("prototype_reverification") or {}).get("passed") is True):
        raise ValueError("보고서 검사를 통과한 최신 데모 실행의 실패만 재검수할 수 있습니다.")
    folder = _safe(engine.data_dir / "organization-runs" / attempt["id"], engine.data_dir)
    pinned = _pinned_hashes(attempt["verification"])
    originals = {name: _read(folder / name, folder) for name in ARTIFACTS}
    if {name: _hash(raw) for name, raw in originals.items()} != pinned:
        raise ValueError("원본 보고서가 최초 검증 이후 변경됐습니다.")
    execution_raw = _read(folder / "execution.json", folder, 1_000_000)
    execution = json.loads(execution_raw)
    if (not isinstance(execution, dict) or execution.get("completed") is not True
            or type(execution.get("exit_code")) is not int or execution["exit_code"] != 0 or execution.get("error")):
        raise ValueError("정상 종료한 직원 실행만 후처리할 수 있습니다.")
    review = CodeReviewer().review(folder, execution, mission["acceptance_criteria"], "daslab")
    if review.get("passed") is not True or _pinned_hashes(review) != pinned:
        raise ValueError("원본 보고서의 엄격 검사가 통과하지 못했습니다.")
    document = json.loads(originals["result.json"].decode("utf-8-sig"))
    return mission, attempt, folder, pinned, execution_raw, document, review


def _identity(engine, mission, folder):
    identity = engine.prototypes.identity(folder)
    expected = {"project_id": mission.get("prototype_project_id", "daslab-growth"),
                "profile": mission.get("prototype_profile", DEFAULT_PROFILE)}
    if identity != expected:
        raise ValueError("데모 작업본의 프로젝트·프로필이 배정 기록과 다릅니다.")
    if identity["profile"] == NETWORK_PROFILE or mission.get("parent_mission_id"):
        parent_id = mission.get("parent_mission_id")
        if (not isinstance(parent_id, str) or not re.fullmatch(r"[a-f0-9]{32}", parent_id)
                or parent_id != identity["project_id"]):
            raise ValueError("프로젝트 개발 업무의 상위 미션이 일치하지 않습니다.")
        parent = engine._get("missions", parent_id)
        flow = parent.get("workflow") or {}
        if (parent.get("status") not in ("paused", "blocked") or parent.get("pending_action")
                or flow.get("kind") != "business" or flow.get("profile") != identity["profile"]
                or mission["id"] not in flow.get("child_ids", [])):
            raise ValueError("같은 프로젝트의 PM이 멈춘 상태에서만 자식 데모를 재검수할 수 있습니다.")
    return identity


def _source_pin(engine, attempt, folder, identity, source_hashes, baseline_raw):
    original = attempt.get("prototype") or {}
    basis = None
    if original.get("artifacts"):
        if original["artifacts"] != source_hashes:
            raise ValueError("기존 데모 검증 이후 작업 파일이 변경됐습니다.")
        basis = "original_attempt"
    for name, pin in (("original_repair_snapshot", original),
                      ("captured_repair_snapshot", attempt.get("prototype_repair_capture") or {})):
        if not pin.get("repairable"):
            continue
        if (pin.get("repairable") is not True or pin.get("ready") is not False
                or pin.get("attempt_id") != attempt["id"]
                or {key: pin.get(key) for key in identity} != identity
                or pin.get("repair_artifacts") != source_hashes
                or pin.get("repair_baseline_sha256") != _hash(baseline_raw)):
            raise ValueError("고정된 재작업 자료의 파일·기준 해시 또는 계보가 다릅니다.")
        snapshot = engine.prototypes.repair_snapshot(folder, **identity,
                                                     expected_baseline_sha256=pin["repair_baseline_sha256"])
        if snapshot["repair_artifacts"] != source_hashes:
            raise ValueError("고정된 재작업 자료가 확인 도중 변경됐습니다.")
        basis = basis or name
    if identity["profile"] == NETWORK_PROFILE and basis is None:
        raise ValueError("프로젝트 데모의 실행 종료 또는 운영자 고정 파일 해시가 필요합니다.")
    return basis


def recover_prototype(engine, mission_id, review_note, require_policy_comparison=True):
    """Recheck a stopped prototype without rewriting reports or resetting runs.

    A passed recheck is a reviewable synthetic demo, not business acceptance.
    The caller owns subsequent standing-work reconciliation and preview routing.
    """
    if not isinstance(mission_id, str) or not re.fullmatch(r"[a-f0-9]{32}", mission_id):
        raise ValueError("재검수할 업무 ID가 올바르지 않습니다.")
    if not isinstance(review_note, str) or not review_note.strip() or len(review_note) > 4000:
        raise ValueError("재검수 이유와 확인 내용을 4000자 이내로 남겨야 합니다.")
    if type(require_policy_comparison) is not bool:
        raise ValueError("정책 비교 검사 여부는 명시적인 참·거짓이어야 합니다.")
    with engine.changed, engine.db:
        mission, attempt, folder, report_hashes, execution_raw, document, review = _load(engine, mission_id)
        identity = _identity(engine, mission, folder)
        network = identity["profile"] == NETWORK_PROFILE
        source_hashes = {name: _hash(raw) for name, raw in engine.prototypes._files(folder / "workspace").items()}
        baseline_raw = _read(folder / "prototype-baseline.json", folder, 1_000_000)
        pin_basis = _source_pin(engine, attempt, folder, identity, source_hashes, baseline_raw)
        prior = attempt.get("prototype_reverification")
        if prior and (not isinstance(prior, dict) or prior.get("source_artifacts") != source_hashes
                      or prior.get("execution_sha256") != _hash(execution_raw)
                      or prior.get("baseline_sha256") != _hash(baseline_raw)
                      or prior.get("report_artifacts") != report_hashes):
            raise ValueError("첫 재검수 이후 원본 작업 파일 또는 실행·기준 기록이 변경됐습니다.")
        recheck_id = uuid.uuid4().hex
        target = _safe(folder / "prototype-rechecks" / recheck_id, folder)
        target.mkdir(parents=True, exist_ok=False)
        provenance = {"id": recheck_id, "path": str(target), "operator": "supervisor_review",
                      **identity,
                      "created_at": datetime.now(timezone.utc).isoformat(), "note": review_note.strip(),
                      "require_policy_comparison": require_policy_comparison and not network,
                      "requested_policy_comparison": require_policy_comparison,
                      "original_attempt_status": attempt["status"], "original_attempt_error": attempt.get("error"),
                      "original_prototype": copy.deepcopy(attempt.get("prototype")),
                      "report_artifacts": report_hashes, "execution_sha256": _hash(execution_raw),
                      "baseline_sha256": _hash(baseline_raw), "source_artifacts": source_hashes,
                      "source_pin_basis": pin_basis or ("previous_recheck" if prior else "recheck_start_snapshot"),
                      "scope": "직원 보고 원문을 보존한 합성 모델·화면 재검수입니다. 산업적 유효성·고객 효과·3D 품질·실서비스 반영을 검증하지 않았습니다."}
        _write(target / "input.json", provenance)
        receipt, browser, error = {}, {}, None
        try:
            receipt = engine.prototypes.finish(folder)
            if (receipt.get("ready") is not True or receipt.get("artifacts") != source_hashes
                    or {key: receipt.get(key) for key in identity} != identity):
                raise ValueError("데모 정적 검사 또는 작업 파일 해시가 일치하지 않습니다: " + str(receipt.get("error")))
            if not receipt.get("changed_files"):
                raise ValueError("직원이 수정하지 않은 기본 데모는 개발 결과로 인정하지 않습니다.")
            if network and not set(receipt["changed_files"]) & {"model.js", "app.js", "index.html", "style.css"}:
                raise ValueError("선정 문제를 실제 모델·화면 소스에 적용해야 합니다. 문서 변경만으로 개발을 완료 처리하지 않습니다.")
            url = engine.prototypes.open_preview(folder, expected_artifacts=source_hashes)
            verifier = NetworkVerifier() if network else engine.prototype_verifier
            browser = verifier.verify(url, source_hashes, target)
            _write(target / "prototype-browser.json", browser)
            checks = browser.get("checks")
            if (browser.get("status") != "passed" or browser.get("artifacts") != source_hashes
                    or not isinstance(checks, list) or not checks
                    or any(not isinstance(c, dict) or c.get("status") != "passed" for c in checks)
                    or browser.get("errors") or browser.get("business_acceptance") is not False):
                raise ValueError("독립 모델·화면 검사를 통과하지 못했습니다.")
            if network and (browser.get("profile") != NETWORK_PROFILE or browser.get("kind") != "supply_network_gis_checks"):
                raise ValueError("등록된 공급망 모델·GIS 검증 프로필이 아닙니다.")
            policy = browser.get("policy_comparison") or {}
            if require_policy_comparison and not network and (policy.get("present") is not True or policy.get("status") != "passed"):
                raise ValueError("필수 정책 비교 검사를 통과하지 못했습니다.")
            checked = engine.prototypes.finish(folder)
            if (checked.get("ready") is not True or checked.get("artifacts") != source_hashes
                    or {key: checked.get(key) for key in identity} != identity):
                raise ValueError("데모 작업 파일이 재검수 도중 변경됐습니다.")
            if ({name: _hash(_read(folder / name, folder)) for name in ARTIFACTS} != report_hashes
                    or _read(folder / "execution.json", folder, 1_000_000) != execution_raw
                    or _read(folder / "prototype-baseline.json", folder, 1_000_000) != baseline_raw):
                raise ValueError("원본 보고서 또는 실행·기준 기록이 재검수 도중 변경됐습니다.")
        except Exception as exc:
            error = str(exc)[:1000] or type(exc).__name__
        passed = error is None
        receipt.update(attempt_id=attempt["id"], browser=browser, browser_verified=passed,
                       model_verified=passed, applied_to_live=False, recheck_id=recheck_id)
        if passed:
            receipt["preview_url"] = f"/prototypes/{attempt['id']}/"
        else:
            receipt.update(ready=False, error=error)
            receipt.pop("preview_url", None)
        provenance.update(passed=passed, error=error, prototype=receipt, verification=review,
                          completed_at=datetime.now(timezone.utc).isoformat())
        _write(target / "provenance.json", provenance)
        attempt.setdefault("prototype_rechecks", []).append({"id": recheck_id, "path": str(target), "passed": passed})
        attempt["prototype_reverification"] = copy.deepcopy(provenance)
        status = "review" if passed and document["outcome"]["status"] != "blocked" else "blocked"
        mission.update(status=status, prototype=receipt, prototype_reverification=copy.deepcopy(provenance),
                       updated_at=provenance["completed_at"], error=error)
        if passed:
            mission.update(result=document, verification="structural_only")
        engine._save("attempts", attempt)
        engine._save("missions", mission)
        engine._event("prototype.reverified", "기존 데모의 후처리를 재검수했습니다. 원본 실행 이력과 보고서는 보존했습니다.",
                      mission["employee_id"], mission_id)
        return {"mission_id": mission_id, "attempt_id": attempt["id"], "status": status,
                "passed": passed, "prototype_reverification": provenance, "prototype": receipt}
