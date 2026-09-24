from datetime import datetime, timezone


class CodePlanner:
    provider = "code"

    def plan(self, task: dict, context: dict) -> dict:
        return {
            "provider": self.provider,
            "goal": task["goal"],
            "inputs": task["inputs"],
            "assigned_to": "worker",
            "steps": ["입력 배경과 가정을 구분한다.", "목표에 맞는 산출물을 작성한다.",
                      "각 완료 기준에 해당하는 산출물 위치와 근거를 제출한다."],
            "acceptance_criteria": task["acceptance_criteria"],
            "constraints": ["근거 없는 수치·인터뷰 결과 금지", "외부 발송·구매·운영 배포 금지",
                            "도구 실행이나 추가 AI 작업자 호출 없이 제공된 자료로 문서·코드 제안 작성",
                            "고객 업무 검증은 미실시이며 계획과 실행 사실을 구분"],
            "context": context,
            "source": "task:" + task["id"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "project_id": task["project_id"],
            "verification_status": "planned",
        }
