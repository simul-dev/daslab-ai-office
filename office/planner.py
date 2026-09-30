from datetime import datetime, timezone
import re


def mission_brief(payload, projects):
    """Make a usable internal brief without making the owner fill out a form."""
    mission = payload.get("mission")
    if not isinstance(mission, str) or not mission.strip() or len(mission) > 6000:
        raise ValueError("미션을 1~6000자로 적어 주세요.")
    inputs = payload.get("inputs", "")
    if not isinstance(inputs, str) or len(inputs) > 30000:
        raise ValueError("참고 자료는 30000자 이내로 적어 주세요.")
    mission, inputs = mission.strip(), inputs.strip()
    text = mission.lower()
    project_id = payload.get("project_id")
    if project_id is None:
        groups = {"franchise": ("프랜차이즈", "출점", "점주", "가맹", "인테리어", "견적"),
                  "ventures": ("신사업", "미디어", "콘텐츠", "유튜브", "사업 아이디어")}
        scores = {key: sum(word in text for word in words) for key, words in groups.items()}
        project_id = max(scores, key=scores.get) if any(scores.values()) else "daslab"
        if project_id not in {p["id"] for p in projects}:
            project_id = projects[0]["id"]
    if project_id not in {p["id"] for p in projects}:
        raise ValueError("알 수 없는 사업 분류입니다.")
    priority = "high" if any(word in text for word in ("긴급", "오늘", "당장", "asap", "장애", "마감")) else (
        "low" if any(word in text for word in ("시간 될 때", "나중에", "급하지", "여유 있을 때")) else "normal")
    if any(word in text for word in ("설계", "전략", "시뮬레이션", "아키텍처", "종합", "사업계획")) or len(mission) > 1000:
        complexity = "complex"
    elif any(word in text for word in ("비교", "분석", "제안", "계획", "코드", "개발", "기획")) or len(mission) > 240:
        complexity = "standard"
    else:
        complexity = "simple"
    steps = ["요청 의도와 자료를 파악하고 필요한 가정을 정리한다."]
    # Do not give progress credit for reporting, admitting uncertainty or planning.
    # The original mission is the deliverable; all its requirements must be met.
    request = mission if len(mission) <= 1600 else mission[:1600] + "… (goal의 미션 원문 전체 요구사항 포함)"
    criteria = ["요청한 실제 결과를 완성한다: " + request]
    if any(word in text for word in ("비교", "선정", "추천", "mvp", "우선순위")):
        steps.append("선택지를 같은 기준으로 비교하고 우선안을 정한다.")
    elif any(word in text for word in ("요약", "정리")):
        steps.append("핵심 내용을 추려 읽기 쉬운 요약을 만든다.")
    elif any(word in text for word in ("코드", "개발", "구현")):
        steps.append("요구사항에 맞는 코드 제안과 확인 방법을 작성한다.")
    else:
        steps.append("목표에 맞는 결과와 실행 가능한 제안을 작성한다.")
    steps.append("완료 기준별 달성 여부를 확인하고 결과와 남은 일을 보고한다.")
    title = re.split(r"[\n.!?。]", mission, maxsplit=1)[0].strip() or mission
    title = title if len(title) <= 72 else title[:69].rstrip() + "…"
    return {"mission": mission, "title": title, "goal": mission, "project_id": project_id,
            "inputs": inputs or "추가 참고 자료가 제공되지 않았습니다. 미션에 포함된 정보로 진행하고 가정은 구분하세요.",
            "acceptance_criteria": criteria, "priority": priority,
            "mission_analysis": {"complexity": complexity, "priority": priority, "process": steps,
                                 "source": "initial_rules", "rationale": "요청의 긴급 표현과 작업 유형을 기준으로 정리했습니다. 실행 담당이 업무 내용을 다시 판단합니다."}}


class CodePlanner:
    provider = "code"

    def plan(self, task: dict, context: dict) -> dict:
        return {
            "provider": self.provider,
            "goal": task["goal"],
            "inputs": task["inputs"],
            "assigned_to": "worker",
            "steps": task.get("mission_analysis", {}).get("process") or ["입력 배경과 가정을 구분한다.", "목표에 맞는 산출물을 작성한다.",
                      "각 완료 기준에 해당하는 산출물 위치와 근거를 제출한다."],
            "mission_analysis": task.get("mission_analysis", {}),
            "acceptance_criteria": task["acceptance_criteria"],
            "constraints": ["근거 없는 수치·인터뷰 결과 금지", "외부 발송·구매·운영 배포 금지",
                            "도구 실행이나 추가 AI 작업자 호출 없이 제공된 자료로 문서·코드 제안 작성",
                            "고객 업무 검증은 미실시이며 계획과 실행 사실을 구분",
                            "제공된 사실과 추정·미확인 내용을 구분하고 판단 이유와 한계를 설명한다.",
                            "실제 실행과 제안·계획을 구분한다. 정직한 보고, 가정 구분, 계획 작성 자체에는 미션 달성 진척을 부여하지 않는다.",
                            "목표 달성 여부와 남은 일, 필요한 경우에만 대표의 결정을 보고한다.",
                            "미션의 실제 요구를 임의로 축소하지 않는다. 실행 권한·자료가 없어 미완료인 항목은 partial 또는 blocked로 보고한다.",
                            "사소한 표현·형식은 스스로 결정하고 손실을 막기 위해 꼭 필요한 결정만 요청한다.",
                            "미션의 우선순위·난이도와 처리 순서를 다시 판단해 analysis에 보고한다."],
            "context": context,
            "source": "task:" + task["id"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "project_id": task["project_id"],
            "verification_status": "planned",
        }
