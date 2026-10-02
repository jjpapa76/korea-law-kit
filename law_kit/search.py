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


def _get_law_name_core(name):
    """법령명에서 시행규칙, 시행령, 특별조치법, 특별법, 법률, 법 등 접미사를 뗀 어간."""
    name = str(name).strip()
    for suf in ("시행규칙", "시행령", "특별조치법", "특별법", "법률", "법"):
        if name.endswith(suf):
            return name[:-len(suf)].strip()
    return name


def _law_kind_rank(item):
    """법률 > 시행령 > 시행규칙 > 기타 순위 점수 (3 > 2 > 1 > 0)."""
    kind = str(item.get("법령구분명") or "")
    name = str(item.get("법령명한글") or item.get("법령명") or _title(item))
    if "시행규칙" in name or "부령" in kind:
        return 1
    if "시행령" in name or "대통령령" in kind:
        return 2
    if kind == "법률" or name.endswith("법") or name.endswith("법률") or "특별법" in name or "특별조치법" in name:
        return 3
    return 0


def _is_exact_or_word_plus_law(raw, words):
    """(1) 법령명이 '낱말+법' 이거나 낱말과 정확히 같은 본령인지 확인."""
    if _law_kind_rank(raw) != 3:
        return False
    name = str(raw.get("법령명한글") or raw.get("법령명") or _title(raw)).strip()
    core = _get_law_name_core(name)
    for w in words:
        if not w:
            continue
        if name == w:
            return True
        if name == w + "법":
            return True
        if len(w) >= 3 and name == (w[:-1] + "법"):
            return True
        if core and core == w:
            return True
        if len(w) >= 3 and core and core == w[:-1]:
            return True
    return False


def _law_match_count(item, tokens):
    """검색어 토큰이 법령명(또는 어간)에 들어간 횟수 계산."""
    name = str(item.get("법령명한글") or item.get("법령명") or _title(item))
    core = _get_law_name_core(name)
    count = 0
    for t in tokens:
        if not t:
            continue
        if t in name:
            count += 1
        elif len(core) >= 2 and core in t:
            count += 1
        elif len(t) >= 3 and t[:-1] in name:
            count += 1
    return count


def one(target, query, display=20, search_mode=None, ttl=client.CACHE_TTL,
        offset=0, **extra):
    """축 하나."""
    params = dict(extra)
    if "offset" in params:
        offset = params.pop("offset")
    if search_mode is not None:
        params["search"] = search_mode

    try:
        offset_val = int(offset or 0)
    except (TypeError, ValueError):
        offset_val = 0

    try:
        req_display = int(display or 20)
    except (TypeError, ValueError):
        req_display = 20

    is_law_body = (target == "law" and (search_mode in (2, "2") or params.get("search") in (2, "2")))
    call_params = dict(params)
    if is_law_body:
        call_params.pop("offset", None)
        call_params["page"] = 1
        call_display = 100
    else:
        call_display = display

    result = client.call(target, query=query, display=call_display, ttl=ttl,
                         **call_params)
    rows = []
    for item in result.partial:
        if not isinstance(item, dict):
            continue
        rows.append({"제목": _title(item), "raw": item})

    try:
        expected = int(result.total or 0)
    except (TypeError, ValueError):
        expected = 0

    if is_law_body:
        # 다. offset >= 100 이면 재정렬하지 않고 도구 오류가 아니라 complete:false + why "재정렬은 앞 100건까지다. 범위를 좁혀 다시 물어라" (항목 0).
        if offset_val >= 100:
            why_msg = "재정렬은 앞 100건까지다. 범위를 좁혀 다시 물어라"
            ans_data = {
                "target": target,
                "ok": result.ok,
                "complete": False,
                "total": result.total,
                "count": 0,
                "items": [],
                "note": why_msg,
                "why": why_msg,
                "cached": result.cached,
                "정렬": "낱말 법령명 일치 → 본문검색 앞 100건(법제처는 관련도 정렬 미지원)",
            }
            return Answer(ans_data)

        # 가. 법령 축 본문검색을 할 때, 검색어 낱말 중 2글자 이상 앞 3개까지 각각 법령명 검색(search=1, display 20)을 한 번씩 더 해서, 법령명이 그 낱말을 포함하는 법령을 모은다.
        tokens = clean_query(query).split()
        words = [w for w in tokens if len(w) >= 2][:3]

        word_rows = []
        for w in words:
            word_params = dict(call_params)
            word_params.pop("search", None)
            word_params.pop("display", None)
            word_res = client.call("law", query=w, display=20, search=1, ttl=ttl, **word_params)
            for it in word_res.partial:
                if not isinstance(it, dict):
                    continue
                t_name = str(it.get("법령명한글") or it.get("법령명") or _title(it)).strip()
                if w in t_name:
                    word_rows.append({"제목": _title(it), "raw": it})

        # 나. 순위: (1) 법령명이 "낱말+법" 이거나 낱말과 정확히 같은 본령 (2) 낱말별 법령명 검색에서 나온 것 (3) 본문검색 100건. 각 묶음 안에서는 지금의 일치순 → 법률 > 시행령 > 시행규칙 → 원래 순서. 같은 법령(법령ID 또는 MST)은 한 번만.
        seen_lids = set()
        seen_msts = set()
        seen_titles = set()

        def _is_duplicate(raw, title):
            lid = str(raw.get("법령ID") or "").strip()
            mst = str(raw.get("법령일련번호") or "").strip()
            t = str(title).strip()
            if lid and lid in seen_lids:
                return True
            if mst and mst in seen_msts:
                return True
            if not lid and not mst and t in seen_titles:
                return True
            return False

        def _mark_seen(raw, title):
            lid = str(raw.get("법령ID") or "").strip()
            mst = str(raw.get("법령일련번호") or "").strip()
            t = str(title).strip()
            if lid:
                seen_lids.add(lid)
            if mst:
                seen_msts.add(mst)
            seen_titles.add(t)

        group1 = []
        group2 = []
        group3 = []

        # (1) 묶음: word_rows + rows 중 조건 만족하는 것
        for item in word_rows + rows:
            if _is_exact_or_word_plus_law(item["raw"], words):
                if not _is_duplicate(item["raw"], item["제목"]):
                    _mark_seen(item["raw"], item["제목"])
                    group1.append(item)

        # (2) 묶음: word_rows 중 아직 안 들어간 것
        for item in word_rows:
            if not _is_duplicate(item["raw"], item["제목"]):
                _mark_seen(item["raw"], item["제목"])
                group2.append(item)

        # (3) 묶음: rows 중 아직 안 들어간 것
        for item in rows:
            if not _is_duplicate(item["raw"], item["제목"]):
                _mark_seen(item["raw"], item["제목"])
                group3.append(item)

        def sort_group(grp):
            def sort_key(entry):
                idx, r = entry
                raw = r.get("raw") or {}
                mc = _law_match_count(raw, tokens) if tokens else 0
                kr = _law_kind_rank(raw)
                return (-mc, -kr, idx)
            indexed = list(enumerate(grp))
            indexed.sort(key=sort_key)
            return [r for _, r in indexed]

        sorted_rows = sort_group(group1) + sort_group(group2) + sort_group(group3)
        sorted_len = len(sorted_rows)

        # 나. 응답 = 정렬 목록[offset : offset+display]. offset+display < 정렬 목록 길이면 next_offset = offset+display.
        paged_rows = sorted_rows[offset_val : offset_val + req_display]
        paged_count = len(paged_rows)

        if offset_val + req_display < sorted_len:
            next_offset = offset_val + req_display
        else:
            next_offset = None

        # 라. total>100 이거나, total 을 모르는데 100건을 꽉 채워 받았으면 항상 complete:false + why "총 N건(또는 알 수 없음) 중 앞 100건 안에서만 정렬했다". total 은 법제처 원래 값.
        is_unknown_total = (result.total is None or str(result.total).strip() == "" or expected == 0)
        is_cap_100 = (expected > 100) or (is_unknown_total and sorted_len >= 100)

        if is_cap_100:
            complete = False
            total_label = ("%d건" % expected) if expected > 0 else "알 수 없음"
            why = "총 %s 중 앞 100건 안에서만 정렬했다" % total_label
            note = why
        elif expected > 0:
            if expected > (offset_val + paged_count):
                complete = False
                note = ("총 %d건 중 앞 %d건만 봤다 (display=%s). "
                        "전수가 필요하면 all_pages() 를 쓰라" % (expected, offset_val + paged_count, req_display))
                why = note
            elif not result.complete:
                complete = False
                why = result.why_incomplete()
                note = why
            else:
                complete = True
                note = ""
                why = ""
        else:
            # total을 모르는데 100건 미만으로 받은 경우 (sorted_len < 100)
            if (offset_val + paged_count) < sorted_len:
                complete = False
                note = ("총 %d건 중 앞 %d건만 봤다 (display=%s). "
                        "전수가 필요하면 all_pages() 를 쓰라" % (sorted_len, offset_val + paged_count, req_display))
                why = note
            elif not result.complete:
                complete = False
                why = result.why_incomplete()
                note = why
            else:
                complete = True
                note = ""
                why = ""

        ans_data = {
            "target": target,
            "ok": result.ok,
            "complete": complete,
            "total": result.total,
            "count": paged_count,
            "items": paged_rows,
            "note": note,
            "why": why,
            "cached": result.cached,
            "정렬": "낱말 법령명 일치 → 본문검색 앞 100건(법제처는 관련도 정렬 미지원)",
        }
        if next_offset is not None:
            ans_data["next_offset"] = next_offset
        return Answer(ans_data)

    # **display 상한에 걸린 것은 완전한 것이 아니다.**
    # 서버는 "총 340건 중 20건" 을 순순히 준다. 그 20건만 보고 complete
    # 라고 말하면, 부르는 쪽은 340건을 다 본 줄 안다. 실측에서 '법령 축
    # 20건 확인' 이 그렇게 나왔다 - 실제로는 앞 20건이었다.
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
           exhaustive=False, offset=0, **extra):
    """여덟 축 전부. 이름검색이 0건이면 본문검색으로 한 번 더.

    본문검색으로 넘어간 축은 `scope` 에 그렇게 적는다 - 이름으로 맞은
    것과 본문 어딘가에 낱말이 있었던 것은 신뢰도가 다르다.
    """
    if "offset" in extra:
        offset = extra.pop("offset")
    try:
        offset_val = int(offset or 0)
    except (TypeError, ValueError):
        offset_val = 0

    out = {}
    fetch = (lambda t, m=None: all_pages(t, query, search_mode=m, **extra)) if exhaustive else (
        lambda t, m=None: one(t, query, display=display, search_mode=m, offset=offset_val, **extra))
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


def law_search(query, display=20, offset=0, **extra):
    """법령 축 본문검색 및 8개 축 검색 편의 함수."""
    return across(query, display=display, offset=offset, **extra)

