"""Recipient suggestions are pure parsing, not permission or task execution."""
import unittest

from office.intake import route_intake


class IntakeRoutingTests(unittest.TestCase):
    def test_direct_addresses_and_korean_vocatives(self):
        examples = {
            "PM 너가 작업해 줘": "das-pm",
            "피엠아, 지금까지 결과 정리해 줘": "das-pm",
            "피앤 너가 이거 맡아 줘": "das-pm",
            "DAS Lab PM님, 공급망 문제를 조사해 줘": "das-pm",
            "das-pm: 오늘 진행 상황 알려줘": "das-pm",
            "R&D, 알려진 해를 확인해 줘": "das-rd",
            "R & D : 알려진 해를 확인해 줘": "das-rd",
            "알앤디야 이거 분석해 줘": "das-rd",
            "알 앤 디 너가 확인해 줘": "das-rd",
            "DAS-RD: 모델을 검토해 줘": "das-rd",
            "마케팅아 이번 내용을 정리해 줘": "das-mkt",
            "마케팅팀, SNS 초안을 작성해 줘": "das-mkt",
            "영업님 자료를 정리해 주세요": "das-sales",
            "비서야, PM의 업무를 정리해 줘": "assistant",
            "대표 전용 비서 너가 정리해 줘": "assistant",
            "  자, R&D 너가 PM에게 보고할 자료를 정리해 줘  ": "das-rd",
        }
        for text, employee in examples.items():
            with self.subTest(text=text):
                result = route_intake(text, auto=True)
                self.assertEqual(result["employee_id"], employee)
                self.assertEqual(result["matched_employee_id"], employee)
                self.assertEqual(result["status"], "addressed")
                self.assertFalse(result["needs_clarification"])
                self.assertEqual(result["text"], text)

    def test_topic_mentions_quotes_and_indirect_references_do_not_route(self):
        for text in (
            "PM에게 보고할 자료를 R&D가 작성하도록 계획해 줘",
            "PM과 R&D의 차이를 설명해 줘",
            "PM의 역할을 정리해 줘", "PM님께 보고할 자료를 만들어 줘",
            "마케팅 전략을 비교해 줘", "영업 실적을 분석해 줘",
            "비서가 작성한 자료를 검토해 줘", "피엠이라는 표현을 설명해 줘",
            "다음 문장을 읽어 줘: R&D, 개발해 줘",
            '"마케팅, 지금 발표해"라는 문장을 번역해 줘',
            "PMP 너가 작업해 줘", "PM2 너가 작업해 줘", "영업팀의 자료를 정리해 줘",
        ):
            with self.subTest(text=text):
                result = route_intake(text, auto=True)
                self.assertEqual(result["status"], "default")
                self.assertEqual(result["employee_id"], "das-pm")
                self.assertEqual(result["candidates"], [])
                self.assertIsNone(result["matched_employee_id"])

    def test_task_body_mentions_do_not_override_opening_addressee(self):
        for text in ("PM, R&D의 보고서를 읽고 마케팅에 전달할 초안을 만들어 줘",
                     "PM 너가 R&D랑 논의한 뒤 영업 자료를 정리해 줘"):
            result = route_intake(text, auto=True)
            self.assertEqual(result["candidates"], ["das-pm"])
            self.assertEqual(result["status"], "addressed")

    def test_multiple_direct_addressees_require_selection_without_arbitrary_routing(self):
        for text in ("PM과 R&D 너희가 같이 진행해 줘", "PM, 마케팅, 영업, 같이 정리해 줘",
                     "피엠아, 알앤디야 이거 확인해 줘", "마케팅 또는 영업 너가 맡아 줘",
                     "PM 말고 R&D 너가 확인해 줘", "PM, " * 12 + "마케팅, 같이 확인해 줘"):
            with self.subTest(text=text):
                result = route_intake(text, employee_id="das-sales", auto=True)
                self.assertEqual(result["status"], "ambiguous")
                self.assertTrue(result["needs_clarification"])
                self.assertEqual(result["employee_id"], "das-pm")
                self.assertIsNone(result["matched_employee_id"])
                self.assertGreater(len(result["candidates"]), 1)
        repeated = route_intake("PM, 피엠, 너가 맡아 줘", auto=True)
        self.assertEqual(repeated["candidates"], ["das-pm"])
        self.assertFalse(repeated["needs_clarification"])

    def test_manual_choice_wins_until_auto_is_explicitly_enabled(self):
        text = "마케팅아 PM에게 보고할 초안을 작성해 줘"
        manual = route_intake(text, employee_id="das-rd")
        self.assertEqual(manual["employee_id"], "das-rd")
        self.assertEqual(manual["status"], "manual")
        self.assertEqual(manual["matched_employee_id"], "das-mkt")
        auto = route_intake(text, employee_id="das-rd", auto=True)
        self.assertEqual(auto["employee_id"], "das-mkt")
        conflict = route_intake("PM과 R&D 너희가 진행해", employee_id="das-sales")
        self.assertEqual(conflict["employee_id"], "das-sales")
        self.assertFalse(conflict["needs_clarification"])

    def test_default_and_input_limits_do_not_invent_employees_or_transform_text(self):
        text = "  공급망 문제를 조사해 줘\n원래 요구사항을 보존해.  "
        result = route_intake(text, auto=True)
        self.assertEqual(result["employee_id"], "das-pm")
        self.assertEqual(result["text"], text)
        self.assertEqual(route_intake("R&D 너가 작업해")["status"], "default")
        for value in (None, {}, 1, "", "  ", "a" * 6001):
            with self.subTest(value=type(value)), self.assertRaises(ValueError):
                route_intake(value, auto=True)
        for employee in ("owner", "unknown", [], 1, ""):
            with self.subTest(employee=employee), self.assertRaises(ValueError):
                route_intake("할 일", employee_id=employee)
        for auto in (1, "true", None):
            with self.subTest(auto=auto), self.assertRaises(ValueError):
                route_intake("할 일", auto=auto)


if __name__ == "__main__":
    unittest.main()
