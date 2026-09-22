# -*- coding: utf-8 -*-
"""법제처 JSON 의 **모양이 일정하지 않은 것**을 한 가지 모양으로 편다.

법제처 응답은 같은 자리에 값 하나가 올 수도 있고 값 여러 개가 올 수도
있다. 그것도 리스트 하나가 아니라 **평행 리스트 여러 개**로 온다:

    {"위임법령제목": ["건설산업기본법", "전기공사업법"],
     "위임구분":    ["인용법령",     "인용법령"]}

이걸 그대로 쓰면 첫 항목만 보고 나머지를 조용히 버리게 된다. 실측에서
그런 자리가 한 법령에 167곳 있었다. 여기서 한 번 펴 두면 위에서는
항상 "딕셔너리의 리스트" 하나만 다루면 된다.
"""


def as_list(value):
    """무엇이 오든 리스트로. None 은 빈 리스트."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def rows(record):
    """평행 리스트를 레코드 여러 개로 편다.

    길이가 서로 다르면 **가장 긴 것**에 맞추고 짧은 쪽은 마지막 값을
    되쓰지 않고 비운다 - 없는 값을 지어내면 그게 곧 거짓 보증이다.
    """
    if not isinstance(record, dict):
        return []
    width = 1
    for value in record.values():
        if isinstance(value, list):
            width = max(width, len(value))
    if width == 1:
        return [dict(record)]
    out = []
    for index in range(width):
        row = {}
        for key, value in record.items():
            if isinstance(value, list):
                row[key] = value[index] if index < len(value) else None
            else:
                row[key] = value
            # 스칼라는 모든 행에 그대로 붙인다 - 공통 항목이라는 뜻이다
        out.append(row)
    return out


def flatten(records):
    """레코드 목록 전체를 편다."""
    out = []
    for record in as_list(records):
        out.extend(rows(record))
    return out


def text(value, default=""):
    """값을 사람이 읽는 문자열로. 리스트면 이어 붙인다."""
    if value is None:
        return default
    if isinstance(value, list):
        return " / ".join(text(v) for v in value if v is not None) or default
    return str(value).strip() or default


#: 알맹이가 들어 있는 칸 이름들. 여기에 손을 대려면 complete 여야 한다.
PAYLOAD_KEYS = ("items", "articles", "rows", "versions", "groups", "laws",
                "terms")


class Answer(dict):
    """딕셔너리로 돌려주는 답. **불완전하면 알맹이를 못 꺼낸다.**

    `Result` 에는 문을 달았는데 딕셔너리는 그냥 열려 있으면, 부르는 쪽은
    자연히 열린 문으로 들어간다 - `found["articles"]` 한 줄이면 실패가
    "0건" 이 된다. 그래서 같은 문을 여기에도 단다.

    받은 만큼이라도 보려면 `partial("articles")` 를 쓴다. 그때는
    **모르고 쓰는 것이 아니라 알고 쓰는 것**이 된다.
    """

    def __getitem__(self, key):
        if key in PAYLOAD_KEYS and not dict.get(self, "complete", True):
            from .client import Incomplete
            raise Incomplete(
                "%s: %s - 그래도 보려면 partial(%r) 을 쓰라"
                % (key, dict.get(self, "note") or "끝까지 받지 못했다", key))
        return dict.__getitem__(self, key)

    def partial(self, key="items", default=None):
        """검사 없이 꺼낸다. 알고 쓰는 사람을 위한 문."""
        return dict.get(self, key, default)

    def why(self):
        return "" if dict.get(self, "complete", True) else (
            dict.get(self, "note") or "끝까지 받지 못했다")
