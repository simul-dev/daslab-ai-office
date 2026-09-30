"""Operator capture of old, unverified prototype bytes for a later repair only."""
import copy
import re
from datetime import datetime, timezone

from .prototypes import FILES


def capture_prototype_repair_source(engine, mission_id, attempt_id, expected_artifacts,
                                   expected_baseline_sha256):
    """Preserve an explicit current snapshot without rewriting failed-run evidence.

    These pins are supplied after operator inspection. They do not establish that
    the files are the bytes present when the old employee execution terminated.
    No code, browser, employee or original report is executed or reclassified.
    """
    for value in (mission_id, attempt_id):
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{32}", value):
            raise ValueError("업무와 실행 ID를 정확히 지정해야 합니다.")
    if (not isinstance(expected_artifacts, dict) or set(expected_artifacts) != set(FILES)
            or any(not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value)
                   for value in expected_artifacts.values())
            or not isinstance(expected_baseline_sha256, str)
            or not re.fullmatch(r"[a-f0-9]{64}", expected_baseline_sha256)):
        raise ValueError("운영자가 확인한 6개 파일과 기준 파일의 SHA-256 해시가 필요합니다.")
    with engine.changed, engine.db:
        if engine.closed or engine._active:
            raise ValueError("모든 실행이 멈춘 상태에서만 재작업 자료를 고정할 수 있습니다.")
        mission = engine._get("missions", mission_id)
        if (mission.get("execution_mode") != "prototype" or mission.get("status") not in ("failed", "deferred")
                or mission.get("pending_action")):
            raise ValueError("실패하거나 보류된 프로젝트 개발 업무만 자료를 고정할 수 있습니다.")
        engine._require_prototyper(mission["employee_id"])
        project_id, profile = mission.get("prototype_project_id"), mission.get("prototype_profile")
        if (not isinstance(project_id, str) or not re.fullmatch(r"[a-f0-9]{32}", project_id)
                or mission.get("parent_mission_id") != project_id):
            raise ValueError("프로젝트 개발 업무의 상위 미션이 일치하지 않습니다.")
        parent = engine._get("missions", project_id)
        flow = parent.get("workflow") or {}
        if (parent.get("status") not in ("paused", "blocked") or parent.get("pending_action")
                or flow.get("kind") != "business" or flow.get("profile") != profile
                or mission_id not in flow.get("child_ids", [])):
            raise ValueError("같은 프로젝트의 PM이 일시 정지 또는 막힘 상태여야 합니다.")
        attempts = engine._attempts(mission_id)
        if not attempts or attempts[-1].get("id") != attempt_id:
            raise ValueError("이 업무의 최신 실패 실행을 정확히 지정해야 합니다.")
        attempt = attempts[-1]
        if (attempt.get("mission_id") != mission_id or attempt.get("execution_mode") != "prototype"
                or attempt.get("status") not in ("failed", "deferred") or not attempt.get("ended_at")):
            raise ValueError("종료된 최신 개발 실패 실행만 자료를 고정할 수 있습니다.")
        original = attempt.get("prototype") or {}
        recheck = attempt.get("prototype_reverification") or {}
        if original.get("ready") or original.get("repairable") or recheck.get("passed"):
            raise ValueError("이미 검증되거나 실행 종료 때 고정된 작업본은 다시 고정하지 않습니다.")
        folder = engine.data_dir / "organization-runs" / attempt_id
        identity = {"project_id": project_id, "profile": profile}
        snapshot = engine.prototypes.repair_snapshot(folder, **identity,
                                                     expected_baseline_sha256=expected_baseline_sha256)
        if snapshot["repair_artifacts"] != expected_artifacts:
            raise ValueError("현재 작업 파일이 운영자가 확인한 해시와 다릅니다.")
        prior = attempt.get("prototype_repair_capture")
        if prior:
            if (prior.get("attempt_id") != attempt_id or prior.get("project_id") != project_id
                    or prior.get("profile") != profile or prior.get("repair_artifacts") != expected_artifacts
                    or prior.get("repair_baseline_sha256") != expected_baseline_sha256):
                raise ValueError("첫 자료 고정과 다른 해시로 기존 기록을 바꿀 수 없습니다.")
            return {"captured": True, "reused": True, "mission_id": mission_id,
                    "attempt_id": attempt_id, "prototype": copy.deepcopy(prior)}
        stamp = datetime.now(timezone.utc).isoformat()
        capture = {**snapshot, "attempt_id": attempt_id, "ready": False, "artifacts": {},
                   "error": original.get("error") or attempt.get("error") or mission.get("error"),
                   "model_verified": False, "browser_verified": False, "applied_to_live": False,
                   "captured_after_failure": True, "captured_at": stamp, "operator": "supervisor_review",
                   "original_byte_identity_verified": False,
                   "note": "실패 후 운영자가 현재 파일을 확인해 재작업용으로 고정했습니다. 원래 실행 종료 시점과 같은 바이트인지, 모델·화면이 동작하는지는 검증하지 않았습니다."}
        if engine.prototypes.repair_snapshot(folder, **identity,
                                            expected_baseline_sha256=expected_baseline_sha256) != snapshot:
            raise ValueError("자료 고정 도중 작업 파일이 변경됐습니다.")
        attempt["prototype_repair_capture"] = copy.deepcopy(capture)
        mission.update(prototype=copy.deepcopy(capture), updated_at=stamp)
        engine._save("attempts", attempt)
        engine._save("missions", mission)
        engine._event("prototype.repair_captured", "실패 후 확인한 현재 파일을 재작업용으로 고정했습니다. 원래 실패·미검증 기록은 유지합니다.",
                      mission["employee_id"], mission_id)
        return {"captured": True, "reused": False, "mission_id": mission_id,
                "attempt_id": attempt_id, "prototype": capture}
