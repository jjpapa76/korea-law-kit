# -*- coding: utf-8 -*-
"""법이 **언제 어떻게 바뀌었는지**, 그리고 **옛 이름**을 따라간다.

왜 필요한가: 오래된 보고서는 지금 없는 법을 인용한다. "도시계획법
제○조" 를 현행법에서 찾으면 0건이 나오고, 그러면 "틀린 인용" 으로
찍히기 쉽다. 그런데 도시계획법은 틀린 게 아니라 **2003년에 국토의
계획 및 이용에 관한 법률로 바뀐** 것이다. 이걸 구분 못 하면 멀쩡한
문서를 고치게 만든다. 실제로 그 사고를 낸 적이 있다.

**정정(2026-09-22 실측):** `lsHistory` 는 JSON·XML 을 주지 않는다.
어떤 조합으로 불러도 HTML 뷰어 페이지가 돌아온다(ID·MST·query, JSON·XML
여섯 조합 전부 확인). 이전 조사에서 "lsHistory 로 연혁 추적 가능" 이라고
보고된 것은 사실이 아니다. 그 일은 **`eflaw`(시행일 법령)** 가 한다 -
폐지·제명변경된 판까지 들어 있어 구법명이 여기서 나온다.
"""
from . import client
from .shape import Answer, text

#: 시행일 법령. 폐지분까지 있다.
TARGET = "eflaw"
#: 신구법 대조
TARGET_DIFF = "oldAndNew"

#: JSON·XML 을 주지 않는 것으로 실측된 target. 부르면 HTML 이 온다.
HTML_ONLY = ("lsHistory",)


def versions(name, max_pages=40, ttl=client.CACHE_TTL):
    """그 이름을 가진 법의 **모든 시행일 판**을 시간 역순으로.

    끝까지 못 받으면 `complete=False` 로 말한다. 122판짜리 법이 있어서
    한 페이지로 끝난다고 가정하면 안 된다(도시계획법 실측 122건).
    """
    result = client.call_all(TARGET, query=name, search=1,
                             page_size=100, max_pages=max_pages, ttl=ttl)
    rows = []
    for item in result.partial:
        if not isinstance(item, dict):
            continue
        title = text(item.get("법령명한글"))
        if "".join(title.split()) != "".join(str(name).split()):
            continue                          # 부분 일치는 다른 법이다
        rows.append({
            "법령명": title,
            "ID": text(item.get("법령ID")),
            "MST": text(item.get("법령일련번호")),
            "시행일자": text(item.get("시행일자")),
            "공포일자": text(item.get("공포일자")),
            "공포번호": text(item.get("공포번호")),
            "제개정": text(item.get("제개정구분명")),
            "상태": text(item.get("현행연혁코드")),
        })
    rows.sort(key=lambda r: r["시행일자"], reverse=True)
    return Answer({"name": name, "ok": result.ok,
                   "complete": result.complete,
                   "truncated": result.truncated,
                   "note": result.why_incomplete(), "versions": rows,
                   "total": result.total, "cached": result.cached})


def is_current(name):
    """지금도 살아 있는 이름인가. (살아있음, 사유)

    셋 중 하나다:
        True,  "현행"      - 현행 법령에 그 이름이 있다
        False, "구법"      - 연혁에만 있다. 이름이 바뀌었거나 폐지됐다
        None,  "미확인"    - 조회를 못 했다. **없다고 말하면 안 된다**
    """
    current = client.call("law", query=name, search=1, display=100)
    if not current.ok:
        return None, "미확인: %s" % current.error
    wanted = "".join(str(name).split())
    for item in current.partial:
        if "".join(text(item.get("법령명한글")).split()) == wanted:
            return True, "현행"
    old = versions(name)
    if not old["ok"]:
        return None, "미확인: 연혁 조회 실패"
    rows = old.partial("versions") or []
    if not rows and not old["complete"]:
        # 연혁을 끝까지 못 봤는데 "어디에도 없다" 고 하면, 멀쩡한 구법
        # 인용이 '틀린 인용' 으로 찍힌다. 모르면 모른다고 한다.
        return None, "미확인: 연혁을 끝까지 보지 못했다 - " + old["note"]
    if rows:
        last = rows[0]
        return False, ("구법 - 마지막 시행 %s (%s)"
                       % (last["시행일자"], last["제개정"]))
    return False, "현행·연혁 어디에도 없다"


def successor(name):
    """구법의 **뒤를 이은 법**을 찾아 본다.

    솔직히 말해 둔다: 법제처 API 는 "A법이 B법으로 바뀌었다" 를 한 칸으로
    주지 않는다. 그래서 여기서 하는 것은 **후보 제시**이고 단정이 아니다.
    구법의 마지막 판 본문에서 폐지·대체 문구를 읽어 후보를 뽑는다.
    못 찾으면 후보 없이 "미확인" 이라고 말한다 - 지어내지 않는다.
    """
    alive, why = is_current(name)
    if alive is None:
        return {"name": name, "status": "미확인", "why": why, "candidates": []}
    if alive:
        return {"name": name, "status": "현행", "why": why, "candidates": []}
    old = versions(name)
    rows = old.partial("versions") or []
    if not rows:
        return {"name": name, "status": "미확인", "why": "연혁을 못 찾았다",
                "candidates": []}
    last = rows[0]
    body = client.call("eflaw", service=True, MST=last["MST"])
    hints = []
    if body.ok:
        import json as _json
        blob = _json.dumps(body.partial, ensure_ascii=False)
        import re
        for match in re.finditer(r"「([^」]{4,40}?(?:법|법률))」", blob):
            title = match.group(1)
            if title != name and title not in hints:
                hints.append(title)
    return {"name": name, "status": "구법", "why": why,
            "last_version": last,
            "candidates": hints[:10],
            "note": ("후보다. 단정이 아니다 - 법제처 API 에 승계 관계를 "
                     "직접 주는 칸이 없다. 사람이 확인해야 한다")}


def compare(name, display=20):
    """신구법 대조 목록. 무엇이 바뀌었는지 볼 때."""
    result = client.call(TARGET_DIFF, query=name, search=1, display=display)
    return Answer({"name": name, "ok": result.ok,
                   "complete": result.complete,
                   "note": result.why_incomplete(),
                   "items": result.partial})
