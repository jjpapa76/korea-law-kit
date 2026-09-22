# -*- coding: utf-8 -*-
"""**낱말 하나로 조문을 바로 찾는다.** 이 꾸러미에서 제일 값어치 있는 길이다.

보통은 이렇게 한다: 관련 있을 법한 법을 고른다 → 그 법 본문을 통째로
받는다 → 조문을 훑는다. 법이 열 개면 열 번이다. 실측 5분 걸렸다.

`lstrmRltJo` 는 그걸 한 번에 끝낸다. "도시혁신구역" 을 넣으면 그 용어가
**실제로 쓰인 조문**을 법령명·조번호·본문까지 붙여서 돌려준다.
실측 1.2초, 조문 35개 · 법령 13개. 200배다.

주의할 점 하나: 이건 **법령정보지식베이스에 등재된 용어**에만 쓴다.
없는 낱말을 넣으면 0건이 나오는데, 그건 "그런 조문이 없다"가 아니라
"그 낱말이 용어로 등재돼 있지 않다"는 뜻이다. 둘을 섞으면 안 된다 -
`found=False` 와 `articles=[]` 를 따로 두는 이유다.
"""
from . import client
from .shape import Answer, as_list, text

TARGET = "lstrmRltJo"


def _articles_of(term_record):
    """한 용어에 걸린 조문들.

    같은 조문이 여러 번 온다. 그 용어가 그 조문 안에서 **쓰인 횟수**만큼
    행이 생기고, 본문은 첫 행에만 붙어 있다. 그대로 두면 빈 줄이 다섯 개씩
    보인다. 합치되 **버리지는 않는다** - 횟수를 `출현횟수` 로 남긴다.
    몇 번 쓰였는지는 그 조문이 그 용어의 중심인지 아닌지를 가르는 단서다.
    """
    rows = []
    index = {}
    for link in as_list(term_record.get("연계법령")):
        if not isinstance(link, dict):
            continue
        number = text(link.get("조번호"))
        branch = text(link.get("조가지번호"))
        label = "제%s조" % number.lstrip("0") if number.strip("0") else ""
        if label and branch.strip("0"):
            label += "의%s" % branch.lstrip("0")
        key = (text(link.get("법령명")), number, branch)
        body = text(link.get("조문내용"))
        if key in index:
            seen = index[key]
            seen["출현횟수"] += 1
            if body and not seen["조문내용"]:
                seen["조문내용"] = body       # 본문은 첫 행에만 오기도 한다
            continue
        row = {
            "법령명": key[0],
            "조": label,
            "조번호": number,
            "조가지번호": branch,
            "조문내용": body,
            "용어구분": text(link.get("용어구분")),
            "출현횟수": 1,
        }
        index[key] = row
        rows.append(row)
    return rows


def articles(term, ttl=client.CACHE_TTL):
    """용어 하나로 조문을 찾는다.

    돌려주는 것:
        found     그 용어가 지식베이스에 있었는가
        complete  끝까지 받았는가 (False 면 "없다" 고 말하면 안 된다)
        terms     맞은 용어들(동음이의어가 있다)
        articles  조문 목록
        laws      나온 법령 이름들
    """
    result = client.call(TARGET, service=True, query=term, ttl=ttl)
    if not result.ok:
        return Answer({"term": term, "found": False, "complete": False,
                       "error": result.error, "terms": [], "articles": [],
                       "laws": [],
                       "note": "조회 자체가 실패했다: %s" % result.error})
    terms, rows = [], []
    for record in result.partial:
        if not isinstance(record, dict):
            continue
        name = text(record.get("법령용어명"))
        found = _articles_of(record)
        terms.append({"용어": name, "비고": text(record.get("비고")),
                      "조문수": len(found)})
        rows.extend(found)
    laws = list(dict.fromkeys(r["법령명"] for r in rows if r["법령명"]))
    note = ""
    if not terms:
        note = ("'%s' 는 법령용어로 등재돼 있지 않다. "
                "관련 조문이 없다는 뜻이 아니다 - 본문검색을 따로 해야 한다"
                % term)
    return Answer({"term": term, "found": bool(terms),
                   "complete": result.complete, "error": "",
                   "terms": terms, "articles": rows, "laws": laws,
                   "note": note, "cached": result.cached})


def by_law(term, law_name):
    """한 법 안에서만 본다. 법령명이 부분 일치해도 잡는다."""
    result = articles(term)
    key = "".join(str(law_name).split())
    kept = [r for r in (result.partial("articles") or [])
            if key in "".join(r["법령명"].split())]
    dict.__setitem__(result, "articles", kept)
    dict.__setitem__(result, "laws",
                     list(dict.fromkeys(r["법령명"] for r in kept)))
    return result
