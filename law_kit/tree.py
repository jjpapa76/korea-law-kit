# -*- coding: utf-8 -*-
"""법률 하나에서 **아래로 뻗은 것들**을 한 번에 본다.

법률만 읽고 판단하면 절반을 놓친다. 실제 기준은 시행령·시행규칙·조례에
있고, 법률 본문은 "대통령령으로 정한다" 로 끝나는 경우가 대부분이다.
그 화살표를 사람이 손으로 따라가면 하나도 빠짐없이 하기가 어렵다.

`lsDelegated` 가 그 화살표를 전부 준다. 주차장법 실측 6.2초에
인용법령 33 · 시행령 28 · 시행규칙 26 · 위임자치법규 21 · 위임행정규칙 1.

응답 모양이 고약하다. 한 자리에 값이 하나 올 때도 있고 **평행 리스트**로
여러 개가 올 때도 있다. `shape.rows` 로 펴서 항상 같은 모양으로 만든다 -
안 그러면 첫 항목만 보고 나머지를 조용히 버린다.
"""
from . import client, laws
from .shape import Answer, as_list, flatten, text

TARGET = "lsDelegated"

#: 위임구분 값들. 무엇이 나올지 미리 못 박지 않는다 - 서버가 새 값을 주면
#: 그대로 보여 준다. 이건 보기 좋게 묶을 때만 쓴다.
GROUP_ORDER = ("시행령", "시행규칙", "위임행정규칙", "위임자치법규", "인용법령",
               "자체·미지정")


def _rows_of(record):
    """위임 블록 하나를 평평한 행들로.

    **위임구분마다 칸 이름이 다르다.** 시행령·시행규칙·인용법령은
    `위임법령제목`/`위임법령조문정보` 를 쓰는데, 조례는 `위임자치법규제목`
    이 `위임자치법규조문정보` **안에** 들어 있고, 행정규칙은 또
    `위임행정규칙…` 이다. 앞의 이름만 보고 읽으면 조례가 전부 이름 없이
    떨어진다 - 주차장법 실측에서 4,840건이 그렇게 사라졌다.

    그래서 이름을 박지 않는다. `…조문정보` 로 끝나는 칸을 찾아 들어가고,
    `…제목` · `…일련번호` 로 끝나는 칸을 이름과 번호로 읽는다. 법제처가
    새 구분을 추가해도 그대로 잡힌다.
    """

    def pick(source, suffix, skip=()):
        for key, value in source.items():
            if key.endswith(suffix) and not any(key.endswith(s) for s in skip):
                return text(value)
        return ""

    rows = []
    article = record.get("조정보") if isinstance(record, dict) else None
    article = article if isinstance(article, dict) else {}
    host_no = text(article.get("조문번호"))
    host_title = text(article.get("조문제목"))

    for block in as_list(record.get("위임정보")):
        if not isinstance(block, dict):
            continue
        detail_key = next((k for k in block if k.endswith("조문정보")), "")
        details = flatten(block.get(detail_key)) if detail_key else []
        outer = flatten({k: v for k, v in block.items() if k != detail_key})
        kind = text(block.get("위임구분"))
        if not kind:
            # 구분이 비어 있는 블록이 있다. 실물을 열어 보니 **대상 법령이
            # 아예 없는** 것들이었다 - 그 법이 자기 조문을 가리키거나
            # ("제19조의6"), 법 이름 없이 "국토교통부령으로 정하는" 이라고만
            # 한 경우다. 이름을 지어 붙이지 않고 그렇다고 적는다.
            kind = "자체·미지정"
        width = max(len(details), len(outer), 1)
        for index in range(width):
            near = details[index] if index < len(details) else {}
            far = outer[index] if index < len(outer) else (outer[0] if outer else {})
            rows.append({
                "상위조번호": host_no,
                "상위조제목": host_title,
                "위임구분": text(far.get("위임구분")) or kind,
                # 조문제목은 법령 이름이 아니다. 그걸 이름 칸에 넣으면
                # "주차전용건축물의 주차면적비율" 이 법 이름으로 보인다.
                "대상법령": (pick(near, "제목", skip=("조문제목",))
                            or pick(far, "제목", skip=("조문제목",))),
                "대상MST": pick(near, "일련번호") or pick(far, "일련번호"),
                "대상조번호": pick(near, "조문번호"),
                "대상조제목": text(near.get(
                    next((k for k in near if k.endswith("조문제목")), ""))),
                "조항호목": text(near.get("조항호목")),
                "근거문구": text(near.get("라인텍스트")),
                "링크텍스트": text(near.get("링크텍스트")),
            })
    return rows


def delegated(law, ttl=client.CACHE_TTL):
    """법률 하나의 위임 관계를 전부 가져온다.

    law 는 법령명이어도 되고 법령ID 여도 된다. 이름이면 여기서 해석한다.
    """
    key = str(law).strip()
    resolved = None
    if not key.isdigit():
        resolved = laws.resolve(key)
        if resolved is None:
            return Answer({"law": key, "ok": False, "complete": False,
                    "error": "법을 찾지 못했다",
                    "note": "'%s' 라는 이름의 법을 찾지 못했다. "
                                   "laws.find() 로 후보를 확인하라" % key,
                           "rows": [], "groups": {}, "resolved": None})
        key = resolved["ID"]
    result = client.call(TARGET, service=True, ID=key, ttl=ttl)
    if not result.ok:
        return Answer({"law": law, "ok": False, "complete": False,
                       "error": result.error, "note": result.why_incomplete(),
                       "rows": [], "groups": {}, "resolved": resolved})
    rows, info = [], {}
    for record in result.partial:
        if not isinstance(record, dict):
            continue
        meta = record.get("법령정보")
        if isinstance(meta, dict) and not info:
            info = {"법령명": text(meta.get("법령명")),
                    "법령ID": text(meta.get("법령ID")),
                    "MST": text(meta.get("법령일련번호")),
                    "시행일자": text(meta.get("시행일자")),
                    "소관부처": text(meta.get("소관부처"))}
        for block in as_list(record.get("위임조문정보")):
            rows.extend(_rows_of(block))
    groups = {}
    for row in rows:
        groups.setdefault(row["위임구분"] or "미분류", []).append(row)
    # complete 를 True 로 박아 두면 아래 조회가 잘려도 위에서는 알 길이
    # 없다. 받은 그대로 올려 보낸다.
    return Answer({"law": law, "ok": result.ok, "complete": result.complete,
                   "error": result.error, "resolved": resolved,
                   "법령정보": info, "rows": rows, "groups": groups,
                   "note": result.why_incomplete(), "cached": result.cached})


def summary(result):
    """위임구분별 건수. 한 줄로 보고 싶을 때."""
    counts = {k: len(v) for k, v in (result.get("groups") or {}).items()}
    ordered = [(k, counts[k]) for k in GROUP_ORDER if k in counts]
    ordered += sorted((kv for kv in counts.items()
                       if kv[0] not in GROUP_ORDER), key=lambda kv: -kv[1])
    return ordered


def subordinate_laws(result):
    """아래로 붙은 **법령 이름들**만. 중복 없이.

    인용법령은 뺀다 - 그건 아래가 아니라 옆이다.
    """
    names = []
    for row in (result.get("rows") or []):
        if row["위임구분"] == "인용법령":
            continue
        if row["대상법령"] and row["대상법령"] not in names:
            names.append(row["대상법령"])
    return names
