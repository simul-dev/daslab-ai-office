"""Human reporting views; process completion is not evidence of goal achievement."""
from datetime import datetime, timezone


def progress_metrics(document, criteria):
    """Count actual deliverables while retaining every original acceptance criterion."""
    if not isinstance(document, dict) or not isinstance(criteria, list) or not criteria or any(
            not isinstance(item, str) or not item.strip() for item in criteria) or len(set(criteria)) != len(criteria):
        return None
    evidence = document.get("evidence")
    if not isinstance(evidence, list) or len(evidence) != len(criteria) or any(
            not isinstance(item, dict) or not isinstance(item.get("criterion"), str)
            or item.get("status") not in ("met", "unmet", "unknown") for item in evidence):
        return None
    by_criterion = {item["criterion"]: item["status"] for item in evidence}
    if len(by_criterion) != len(evidence) or set(by_criterion) != set(criteria):
        return None
    milestones = document.get("milestones", [])
    if not isinstance(milestones, list):
        return None
    states = list(by_criterion.values())
    if milestones:
        if any(not isinstance(item, dict) or any(not isinstance(item.get(key), str) or not item[key].strip()
               for key in ("criterion", "deliverable", "artifact_section", "explanation"))
               or item.get("status") not in ("met", "unmet", "unknown") for item in milestones):
            return None
        names = [item["deliverable"].strip().casefold() for item in milestones]
        if len(set(names)) != len(names) or {item["criterion"] for item in milestones} != set(criteria):
            return None
        for criterion, status in by_criterion.items():
            children = [item["status"] for item in milestones if item["criterion"] == criterion]
            aggregated = "unknown" if "unknown" in children else "met" if all(item == "met" for item in children) else "unmet"
            if status != aggregated:
                return None
        states = [item["status"] for item in milestones]
    count = states.count("met")
    return {"count": count, "total": len(states), "percent": None if "unknown" in states else count * 100 // len(states),
            "milestones": bool(milestones), "all_met": all(value == "met" for value in by_criterion.values()) and count == len(states)}


def assessment(document, criteria, verified=False):
    unknown = {"status": "unknown", "progress_percent": None,
               "basis": "목표 달성에 대한 확인 가능한 보고가 아직 없습니다."}
    if not isinstance(document, dict) or not verified:
        return unknown
    if not isinstance(document.get("analysis"), dict) or not isinstance(document.get("report"), list) or not document["report"]:
        return unknown
    outcome = document.get("outcome")
    if not isinstance(outcome, dict) or outcome.get("status") not in ("achieved", "partial", "blocked"):
        return unknown
    metrics = progress_metrics(document, criteria)
    if metrics is None:
        return unknown
    percent = metrics["percent"]
    valid_percent = "progress_percent" in outcome and (outcome["progress_percent"] is None if percent is None
                    else type(outcome["progress_percent"]) is int and outcome["progress_percent"] == percent)
    if not valid_percent or (outcome["status"] == "achieved") != metrics["all_met"]:
        return unknown
    unit = "AI가 나눈 요청 결과" if metrics["milestones"] else "AI 보고 기준"
    return {"status": outcome["status"], "progress_percent": percent,
            "basis": f"{unit} {metrics['total']}개 중 {metrics['count']}개 완료. " + (
                "확인되지 않은 항목이 있어 달성률은 미정입니다." if percent is None else "항목 수 기준이며 실제 업무량이나 독립 검증을 뜻하지 않습니다.")}


def elapsed_seconds(runs):
    if not runs:
        return None
    total = 0.0
    for run in runs:
        if run.get("verification_status") == "interrupted":
            return None
        try:
            started = datetime.fromisoformat(run["started_at"])
            finished = datetime.fromisoformat(run["finished_at"]) if run.get("finished_at") else datetime.now(timezone.utc)
            total += max(0, (finished - started).total_seconds())
        except (KeyError, TypeError, ValueError):
            return None
    return round(total, 1)


def report_view(document, outcome):
    if not isinstance(document, dict):
        return None
    sections = []
    if isinstance(document.get("report"), list):
        sections = [{"title": item["title"], "content": item["content"]}
                    for item in document["report"] if isinstance(item, dict)
                    and isinstance(item.get("title"), str) and isinstance(item.get("content"), str)]
    else:
        # Existing reports remain readable without exposing raw JSON to the owner.
        problems = document.get("confirmed_problems")
        if isinstance(problems, list):
            content = "\n".join("• " + str(item.get("statement", "")) + " (" + str(item.get("source", "")) + ")"
                                for item in problems if isinstance(item, dict))
            if content:
                sections.append({"title": "확인된 배경", "content": content})
        mvp = document.get("mvp")
        if isinstance(mvp, dict):
            sections.append({"title": str(mvp.get("name") or "제안"), "content": str(mvp.get("rationale", ""))})
        for field, title in (("assumptions", "확인이 필요한 가정"), ("required_inputs", "필요한 자료"),
                             ("expected_outputs", "기대 결과"), ("validation_plan", "다음 검증 방법"),
                             ("completion_criteria", "검증 기준")):
            values = document.get(field)
            if isinstance(values, list):
                sections.append({"title": title, "content": "\n".join("• " + v for v in values if isinstance(v, str))})
    lists = lambda field: [v for v in document.get(field, []) if isinstance(v, str)] if isinstance(document.get(field), list) else []
    raw_milestones = document.get("milestones", [])
    raw_milestones = raw_milestones if isinstance(raw_milestones, list) else []
    milestones = [{key: item[key] for key in ("deliverable", "status", "explanation")} for item in raw_milestones
                  if isinstance(item, dict) and all(isinstance(item.get(key), str) for key in ("deliverable", "status", "explanation"))]
    return {"summary": document.get("summary", ""), "sections": sections,
            "accomplishments": lists("accomplishments"), "remaining": lists("remaining") or lists("next_tasks"),
            "limitations": lists("limitations"), "outcome": outcome, "milestones": milestones,
            "assessment_source": "ai_self_assessment" if "outcome" in document else "legacy_report"}


def overview(task, runs, document=None):
    latest = runs[-1] if runs else {}
    verified = bool((latest.get("verification") or {}).get("passed"))
    outcome = assessment(document, task["acceptance_criteria"], verified)
    if task["status"] in ("queued", "running"):
        outcome = {"status": "running" if task["status"] == "running" else "pending", "progress_percent": None,
                   "basis": "실행 중에는 완료 근거가 보고되기 전까지 달성률을 표시하지 않습니다." if task["status"] == "running" else "실행을 기다리고 있습니다."}
    elif task["status"] in ("failed", "cancelled"):
        outcome = {"status": "blocked" if task["status"] == "failed" else "unknown", "progress_percent": None,
                   "basis": task.get("error") or "실행이 취소되어 목표 달성 여부를 확인하지 못했습니다."}
    analysis = (document or {}).get("analysis")
    analysis = analysis if isinstance(analysis, dict) and verified else task.get("mission_analysis", {})
    complexity = analysis.get("complexity")
    complexity = "standard" if complexity == "moderate" else complexity
    if complexity not in ("simple", "standard", "complex"):
        complexity = None
    summary = (document or {}).get("summary")
    if not isinstance(summary, str) or not summary.strip() or task["status"] in ("queued", "running", "failed", "cancelled"):
        summary = task.get("error") or task.get("start_error") or {
            "queued": "미션을 받았습니다. 실행을 기다리고 있습니다.", "running": "목표에 맞는 결과를 만들고 있습니다.",
            "cancelled": "실행이 취소되었습니다.", "failed": "실행을 마치지 못했습니다."}.get(task["status"], "저장된 결과 보고를 확인할 수 있습니다.")
    return {"outcome": outcome["status"], "progress_percent": outcome["progress_percent"], "progress_basis": outcome["basis"],
            "summary": summary, "duration_seconds": elapsed_seconds(runs), "complexity": complexity,
            "priority": analysis.get("priority", task.get("priority", "normal")),
            "process": analysis.get("process", task.get("pm_spec", {}).get("steps", [])),
            "assessment_source": "ai_self_assessment" if outcome["status"] in ("achieved", "partial", "blocked") and verified else None}
