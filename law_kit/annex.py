# -*- coding: utf-8 -*-
"""**별표·서식.** 숫자가 실제로 들어 있는 곳이다.

법령 본문에는 기준이 없는 경우가 많다. "별표 1 에 따른다" 로 끝난다.
용도지역별 건폐율·용적률, 부담금 요율, 이격거리 - 전부 별표에 있다.
그래서 본문만 읽고 수치를 말하면 근거 없이 말하는 것이 된다.

여기서 하는 일은 둘이다: 별표 **목록**을 찾고, 그 **파일**(HWP/PDF)을
받는다. 파일을 받고 나서는 `kordoc`/`anydoc` 으로 Markdown 으로 바꿔서
읽는다 - 첨부를 모델 컨텍스트에 통째로 넣지 않는다.
"""
import os
import re

from . import client
from .shape import Answer, text

TARGET = "licbyl"

#: 별표명으로 찾을지(1) 관련 법령명으로 찾을지(2). 기본은 법령명이다 -
#: 사람이 아는 것은 보통 법 이름이지 별표 이름이 아니다.
BY_TITLE = 1
BY_LAW = 2

#: 하위법령으로 인정하는 꼬리말. 이것 말고는 남의 법이다.
SUBORDINATE = ("시행령", "시행규칙")


def _entry(item):
    return {
        "별표명": text(item.get("별표명")),
        "별표번호": text(item.get("별표번호")),
        "별표종류": text(item.get("별표종류")),
        "법령명": text(item.get("관련법령명")),
        "법령ID": text(item.get("관련법령ID")),
        "MST": text(item.get("관련법령일련번호")),
        "별표일련번호": text(item.get("별표일련번호")),
        "소관부처": text(item.get("소관부처명")),
        "시행": text(item.get("공포일자")),
        "hwp": text(item.get("별표서식 파일링크") or item.get("별표서식파일링크")),
        "pdf": text(item.get("별표서식 PDF파일링크")
                    or item.get("별표서식PDF파일링크")),
        "상세": text(item.get("별표법령 상세링크") or item.get("별표법령상세링크")),
    }


def search(query, by=BY_LAW, max_pages=20, ttl=client.CACHE_TTL):
    """별표를 찾는다. 기본은 법령명으로."""
    result = client.call_all(TARGET, query=query, search=by,
                             page_size=100, max_pages=max_pages, ttl=ttl)
    rows = [_entry(i) for i in result.partial if isinstance(i, dict)]
    return Answer({"query": query, "ok": result.ok, "complete": result.complete,
            "truncated": result.truncated, "total": result.total,
            "note": result.why_incomplete(), "items": rows,
            "cached": result.cached})


def of_law(law_name, include_subordinate=True, **kwargs):
    """한 법의 별표. **기본으로 시행령·시행규칙까지 포함한다.**

    별표는 법률 본문보다 시행규칙에 훨씬 많이 붙는다. 건축법을 실측하면
    "건축법" 이름으로 정확히 걸리는 별표는 0건이고, 99건이 전부
    "건축법 시행령"·"건축법 시행규칙" 에 있다. 정확 일치만 남기면
    "건축법에는 별표가 없다" 는 틀린 결론이 나온다.

    `include_subordinate=False` 로 그 법 자체만 볼 수 있다.
    각 행의 `수준` 이 본법인지 하위법령인지 말해 준다.
    """
    found = search(law_name, by=BY_LAW, **kwargs)
    # 걸러 내는 일은 알맹이를 꺼내야 하므로 partial 로 연다.
    # 불완전하다는 사실은 그대로 들고 나간다.
    wanted = "".join(str(law_name).split())
    kept = []
    for row in found.partial("items"):
        squashed = "".join(row["법령명"].split())
        if squashed == wanted:
            row["수준"] = "본법"
        elif (include_subordinate and squashed.startswith(wanted)
              and squashed[len(wanted):].startswith(SUBORDINATE)):
            # 앞글자만 같다고 하위법령이 아니다. "건축법대장법" 은 건축법의
            # 시행규칙이 아니라 남의 법이다. 뒤에 붙은 말이 '시행령'·'시행규칙'
            # 일 때만 하위로 본다.
            row["수준"] = "하위법령"
        else:
            continue
        kept.append(row)
    dict.__setitem__(found, "items", kept)
    return found


_BAD = re.compile(r'[\/:*?"<>|]')


def fetch(entry, out_dir, prefer="hwp"):
    """별표 파일 하나를 내려받는다. 어디에 무엇이 떨어졌는지 돌려준다.

    링크가 없으면 **없다고 말한다.** 다른 형식으로 몰래 바꿔 받지 않는다 -
    받은 것이 무엇인지 부르는 쪽이 알아야 한다.
    """
    order = [prefer] + [k for k in ("hwp", "pdf") if k != prefer]
    link = kind = ""
    for key in order:
        if entry.get(key):
            link, kind = entry[key], key
            break
    if not link:
        return {"ok": False, "error": "내려받을 링크가 없다",
                "별표명": entry.get("별표명", "")}
    name = _BAD.sub("_", "%s_%s" % (entry.get("법령명", "법령"),
                                    entry.get("별표명", "별표")))[:120]
    path = os.path.join(out_dir, "%s.%s" % (name, kind))
    try:
        info = client.download(link, path)
    except Exception as exc:
        return {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc),
                "별표명": entry.get("별표명", "")}
    info.update({"ok": True, "format": kind, "별표명": entry.get("별표명", ""),
                 "note": "읽으려면 kordoc(hwp) 또는 anydoc(pdf) 으로 변환하라"})
    return info
