# -*- coding: utf-8 -*-
"""주제 하나로 **법·조례·행정규칙·판례·해석례**를 한꺼번에 훑는다.

한 축만 보면 반드시 틀린다. 법률만 보면 지자체가 실제로 적용하는 조례를
놓치고, 조문만 보면 법원이 그 조문을 어떻게 읽는지 모른다. 그래서
기본 축을 여덟 개로 잡는다.

여기서도 원칙은 같다: **못 물은 축과 물었는데 없는 축을 구분한다.**
`axis["complete"]` 가 False 인 축을 놓고 "없다" 고 말하면 안 된다.
"""
import re

from . import client
from .shape import Answer, text


def clean_query(query):
    """따옴표, 괄호 등 검색 문법 기호를 걷어 내고 공백을 정리한다."""
    if not query:
        return ""
    cleaned = re.sub(r'["\'“”‘’`´()[\]{}<>（）【】「」『』《》]', ' ', str(query))
    return " ".join(cleaned.split())


def has_article_number(query):
    """질의어에 '제84조' 처럼 조번호가 섞여 있는지 확인한다."""
    if not query:
        return False
    return bool(re.search(r'제\s*\d+\s*조', str(query)))


#: (target, 사람이 읽는 이름, 이름검색 지원 여부)
AXES = (
    ("law", "법령", True),
    ("ordin", "자치법규(조례·규칙)", True),
    ("admrul", "행정규칙(훈령·예규·고시)", True),
    ("prec", "판례", False),
    ("detc", "헌재결정례", False),
    ("expc", "법령해석례", False),
    ("decc", "행정심판례", False),
    ("baiPvcs", "감사원 사전컨설팅 의견서", False),
)

#: 축마다 제목 칸 이름이 다르다. 순서대로 있는 것을 쓴다.
_TITLE_KEYS = ("법령명한글", "자치법규명", "행정규칙명", "사건명", "안건명",
               "제목", "민원인", "사건번호", "안건번호")


def _title(item):
    for key in _TITLE_KEYS:
        value = text(item.get(key))
        if value:
            return value
    # 못 찾으면 **비워 둔다.** 아무 칸이나 집어 제목인 척하지 않는다.
    return ""


def one(target, query, display=20, search_mode=None, ttl=client.CACHE_TTL,
        **extra):
    """축 하나."""
    params = dict(extra)
    if search_mode is not None:
        params["search"] = search_mode
    result = client.call(target, query=query, display=display, ttl=ttl,
                         **params)
    rows = []
    for item in result.partial:
        if not isinstance(item, dict):
            continue
        rows.append({"제목": _title(item), "raw": item})
    # **display 상한에 걸린 것은 완전한 것이 아니다.**
    # 서버는 "총 340건 중 20건" 을 순순히 준다. 그 20건만 보고 complete
    # 라고 말하면, 부르는 쪽은 340건을 다 본 줄 안다. 실측에서 '법령 축
    # 20건 확인' 이 그렇게 나왔다 - 실제로는 앞 20건이었다.
    try:
        expected = int(result.total or 0)
    except (TypeError, ValueError):
        expected = 0
    complete, note = result.complete, result.why_incomplete()
    if complete and expected > len(rows):
        complete = False
        note = ("총 %d건 중 앞 %d건만 봤다 (display=%s). "
                "전수가 필요하면 all_pages() 를 쓰라" % (expected, len(rows), display))
    elif complete and not expected and len(rows) >= int(display or 0) > 0:
        # 총건수를 안 준 축이 있다(판례 등). 그때 display 를 정확히 채워
        # 돌아왔다면 **뒤가 더 있는지 알 수 없다.** 모르는 것을 완전하다고
        # 말하지 않는다.
        complete = False
        note = ("총건수를 주지 않는 축이다. display=%s 를 가득 채워 왔으니 "
                "뒤가 더 있는지 알 수 없다. 전수가 필요하면 all_pages() 를 쓰라"
                % display)
    return Answer({"target": target, "ok": result.ok, "complete": complete,
                   "total": result.total, "count": len(rows), "items": rows,
                   "note": note, "cached": result.cached})


def all_pages(target, query, max_pages=40, search_mode=None, ttl=client.CACHE_TTL,
        **extra):
    """한 축을 **끝까지**. 전수라고 말하려면 이걸 써야 한다."""
    params = dict(extra)
    if search_mode is not None:
        params["search"] = search_mode
    result = client.call_all(target, query=query, page_size=100,
                             max_pages=max_pages, ttl=ttl, **params)
    rows = [{"제목": _title(i), "raw": i}
            for i in result.partial if isinstance(i, dict)]
    return Answer({"target": target, "ok": result.ok,
                   "complete": result.complete, "total": result.total,
                   "count": len(rows), "items": rows,
                   "note": result.why_incomplete(), "cached": result.cached})


def across(query, axes=AXES, display=20, name_first=True, body_fallback=True,
           exhaustive=False):
    """여덟 축 전부. 이름검색이 0건이면 본문검색으로 한 번 더.

    본문검색으로 넘어간 축은 `scope` 에 그렇게 적는다 - 이름으로 맞은
    것과 본문 어딘가에 낱말이 있었던 것은 신뢰도가 다르다.
    """
    out = {}
    fetch = (lambda t, m=None: all_pages(t, query, search_mode=m)) if exhaustive else (
        lambda t, m=None: one(t, query, display=display, search_mode=m))
    for target, label, supports_name in axes:
        scope = "본문"
        if name_first and supports_name:
            found = fetch(target, 1)
            scope = "이름"
            if found["ok"] and found["count"] == 0 and body_fallback:
                found = fetch(target, 2)
                scope = "본문"
        else:
            found = fetch(target)
        dict.__setitem__(found, "label", label)
        dict.__setitem__(found, "scope", scope)
        out[target] = found
    return {"query": query, "axes": out,
            "incomplete": [t for t, a in out.items() if not a["complete"]]}


def format_report(result):
    """사람이 읽는 표. **모르는 축을 0건으로 보이게 하지 않는다.**"""
    lines = ["주제: %s" % result["query"], ""]
    lines.append("%-24s %-6s %6s  %s" % ("축", "범위", "건수", "상태"))
    lines.append("-" * 66)
    for target, axis in result["axes"].items():
        state = "확인" if axis["complete"] else ("미확인 - " + axis["note"])
        lines.append("%-24s %-6s %6d  %s"
                     % (axis["label"][:22], axis["scope"], axis["count"], state))
    if result["incomplete"]:
        lines.append("")
        lines.append("!! %d개 축은 끝까지 못 봤다. 이 축에 대해 "
                     "'없다' 고 말하면 안 된다." % len(result["incomplete"]))
    return "\n".join(lines)
