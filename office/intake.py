"""Conservative employee addressing for text/voice drafts; never execute work.

Only an opening direct address affects automatic selection. The owner's exact
text remains intact, and choosing an employee does not grant any tool authority.
"""
import re


EMPLOYEE_IDS = frozenset({"assistant", "das-pm", "das-rd", "das-mkt", "das-sales"})
DEFAULT_EMPLOYEE_ID = "das-pm"
_ALIASES = (
    ("das-pm", r"(?:DAS[ \t-]*(?:Lab[ \t-]*)?)?PM|피[ \t]*엠|피[ \t]*앤"),
    ("das-rd", r"(?:DAS[ \t-]*(?:Lab[ \t-]*)?)?R[ \t]*&[ \t]*D|DAS[ \t-]*RD|알[ \t]*앤[ \t]*디"),
    ("das-mkt", r"(?:DAS[ \t-]*(?:Lab[ \t-]*)?)?마케팅(?:[ \t]*팀)?|DAS[ \t-]*MKT"),
    ("das-sales", r"(?:DAS[ \t-]*(?:Lab[ \t-]*)?)?영업(?:[ \t]*팀)?|DAS[ \t-]*SALES"),
    ("assistant", r"(?:대표[ \t]*(?:전용[ \t]*)?)?비서|ASSISTANT"),
)
_PATTERNS = [(employee, re.compile("(?:" + pattern + ")", re.I)) for employee, pattern in _ALIASES]
_PREAMBLE = re.compile(r"(?:(?:저기|자|음|어|그럼|그러면|있잖아|안녕)(?:\s*[,，]\s*|\s+))*")
_CONNECTOR = re.compile(r"\s*(?:[,，/&]|그리고\s+|또는\s+|혹은\s+|및\s+|말고\s+|아니고\s+|이랑|하고|과|와|랑)\s*")
_SECOND_PERSON = re.compile(r"\s*(?:너가|네가|니가|너는|당신이|당신은|너희(?:가|는)?|여러분|다들)(?=\s|[,，:：!！]|$)")
_DIRECT_START = re.compile(r"\s+(?:이거(?:를|부터|는)?|이것(?:을|부터|은)?|이번|오늘|지금|먼저|좀|직접|다음|이\s+작업|이\s+일|확인해|작업해|진행해|도와줘|맡아줘|부탁해)(?=\s|[,，:：!！]|$)")


def _token(text, position):
    for employee, pattern in _PATTERNS:
        match = pattern.match(text, position)
        if not match:
            continue
        end = match.end()
        honorific = re.match(r"[ \t]*(?:님|아|야)", text[end:])
        vocative = bool(honorific)
        if honorific:
            end += honorific.end()
        return employee, end, vocative
    return None


def _direct_tail(text, end, vocative):
    tail = text[end:]
    if not tail:
        return True
    if re.match(r"\s*[,，:：!！]", tail):
        return True
    if vocative and tail[0].isspace():
        return True
    return bool(_SECOND_PERSON.match(tail) or _DIRECT_START.match(tail))


def _addressed_employees(text):
    start = len(text) - len(text.lstrip())
    start = _PREAMBLE.match(text, start).end()
    first = _token(text, start)
    if not first:
        return []
    employee, end, vocative = first
    candidates = [employee]
    # Keep the last valid direct-address prefix. A later staff mention such as
    # "PM, R&D의 보고서를 읽어줘" is the task's object, not another addressee.
    accepted = candidates[:] if _direct_tail(text, end, vocative) else []
    while True:
        connector = _CONNECTOR.match(text, end)
        if not connector:
            break
        following = _token(text, connector.end())
        if not following:
            break
        employee, end, vocative = following
        candidates.append(employee)
        if _direct_tail(text, end, vocative):
            accepted = candidates[:]
    return list(dict.fromkeys(accepted))


def route_intake(text, *, employee_id=None, auto=False):
    """Suggest a recipient while preserving manual choice and the raw request.

    A caller must explicitly enable ``auto`` to use spoken/written addressing.
    Multiple addressees require a selection; the PM fallback must not be treated
    as permission to submit an ambiguous draft automatically.
    """
    if not isinstance(text, str) or not text.strip() or len(text) > 6000:
        raise ValueError("전달할 내용을 1~6000자로 입력하세요.")
    if type(auto) is not bool:
        raise ValueError("자동 직원 선택 여부는 참·거짓이어야 합니다.")
    if employee_id is not None and (not isinstance(employee_id, str) or employee_id not in EMPLOYEE_IDS):
        raise ValueError("등록된 직원을 선택하세요.")
    candidates = _addressed_employees(text)
    matched = candidates[0] if len(candidates) == 1 else None
    ambiguous = auto and len(candidates) > 1
    if not auto and employee_id is not None:
        selected, status = employee_id, "manual"
    elif auto and matched:
        selected, status = matched, "addressed"
    else:
        selected = DEFAULT_EMPLOYEE_ID
        status = "ambiguous" if ambiguous else "default"
    return {"text": text, "employee_id": selected, "matched_employee_id": matched,
            "candidates": candidates, "status": status, "needs_clarification": ambiguous}
