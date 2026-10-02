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


#: 뗄 조사 접미사 (긴 것부터)
_JOSA_SUFFIXES = ("에서", "으로", "의", "에", "을", "를", "은", "는", "이", "가", "와", "과", "등")
#: 단독 조사/접속사 단어
_JOSA_STANDALONE = frozenset(("의", "및", "에", "에서", "을", "를", "은", "는", "이", "가", "와", "과", "등", "으로", "로"))


def strip_josa(word):
    """낱말 끝 조사를 뗀다 (의, 및, 에, 에서, 을, 를, 은, 는, 이, 가, 와, 과, 등, 으로, 로).
    뗀 결과가 2글자 미만이면 떼지 않는다.
    """
    if not word:
        return ""
    w = str(word).strip()
    if w in _JOSA_STANDALONE:
        return ""
    res = w
    if (w.endswith("에서") or w.endswith("으로")) and len(w) >= 4:
        res = w[:-2]
    elif w.endswith("로") and len(w) >= 3:
        res = w[:-1]
    else:
        for suf in ("의", "에", "을", "를", "은", "는", "이", "가", "와", "과", "등"):
            if w.endswith(suf) and len(w) >= 3:
                res = w[:-len(suf)]
                break
    if len(res) < 2:
        return w
    return res


def extract_keywords(query):
    """질의어에서 조사를 뗀 유효 낱말(2글자 이상) 목록을 추출한다."""
    tokens = clean_query(query).split()
    words = []
    for t in tokens:
        stripped = strip_josa(t)
        if stripped and len(stripped) >= 2:
            words.append(stripped)
    return words


#: 낱말 3개 이상 검색 0건 why 문구
ZERO_HIT_3WORDS_WHY = "모든 낱말이 들어간 결과가 0건이다. 없다고 단정하지 마라 - 낱말을 줄여 다시 물어라"
#: 본문검색 0건인데 낱말별 법령명 검색으로 채운 경우 why 문구
LAW_BODY_ZERO_NAME_FALLBACK_WHY = "본문검색은 0건이고, 낱말이 이름에 들어간 법을 대신 보였다 - 원래 검색어 전체를 만족하는지는 확인하지 않았다"


def get_query_base_words(query):
    """질의어에서 조사/단독단어를 제외한 2글자 이상 핵심 낱말 목록을 구한다."""
    tokens = clean_query(query).split()
    raw_words = [t for t in tokens if t not in _JOSA_STANDALONE and len(t) >= 2]
    return raw_words if raw_words else [w for w in tokens if len(w) >= 2]


def get_stems(word):
    """낱말과 앞 2~3글자 어간 목록을 돌려준다."""
    w = str(word).strip()
    stems = [w]
    if len(w) >= 3 and w[:2] not in stems:
        stems.append(w[:2])
    if len(w) >= 4 and w[:3] not in stems:
        stems.append(w[:3])
    return stems


def _get_law_name_core(name):
    """법령명에서 시행규칙, 시행령, 특별조치법, 특별법, 법률, 법 등 접미사를 뗀 어간."""
    name = str(name).strip()
    for suf in ("시행규칙", "시행령", "특별조치법", "특별법", "법률", "법"):
        if name.endswith(suf):
            return name[:-len(suf)].strip()
    return name


def _law_kind_rank(item):
    """법률(일반 본법 > 특별법) > 시행령 > 시행규칙 > 기타 순위 점수 (4 > 3 > 2 > 1 > 0)."""
    kind = str(item.get("법령구분명") or "")
    name = str(item.get("법령명한글") or item.get("법령명") or _title(item))
    if "시행규칙" in name or "부령" in kind:
        return 1
    if "시행령" in name or "대통령령" in kind:
        return 2
    if kind == "법률" or name.endswith("법") or name.endswith("법률"):
        if any(k in name for k in ("특별법", "특례법", "특별조치법")):
            return 3
        return 4
    return 0


def _is_exact_or_word_plus_law(raw, words):
    """(1) 법령명이 '낱말+법' 이거나 낱말과 정확히 같은 본령인지 확인 (어간 포함)."""
    if _law_kind_rank(raw) < 3:
        return False
    name = str(raw.get("법령명한글") or raw.get("법령명") or _title(raw)).strip()
    name_clean = name.replace(" ", "")
    core = _get_law_name_core(name)
    core_clean = core.replace(" ", "") if core else ""
    for w in words:
        if not w:
            continue
        stems = get_stems(w)
        for s in stems:
            if not s:
                continue
            s_clean = s.replace(" ", "")
            if name_clean == s_clean:
                return True
            if name_clean == s_clean + "법":
                return True
            if len(s_clean) >= 3 and name_clean == (s_clean[:-1] + "법"):
                return True
            if core_clean and core_clean == s_clean:
                return True
            if len(s_clean) >= 3 and core_clean and core_clean == s_clean[:-1]:
                return True
    return False


def _law_match_count(item, tokens):
    """검색어 토큰(원래 낱말 및 조사를 뗀 낱말)의 법령명 일치 점수 계산.
    원래 낱말과 뗀 낱말을 둘 다 쓰되 원래 낱말 일치를 먼저 친다.
    """
    name = str(item.get("법령명한글") or item.get("법령명") or _title(item))
    core = _get_law_name_core(name)
    count = 0
    for t in tokens:
        if not t or t in _JOSA_STANDALONE:
            continue
        stripped = strip_josa(t)
        # 1. 원래 낱말 일치 (가장 우선)
        orig_matched = False
        if t in name:
            orig_matched = True
        elif len(core) >= 2 and core in t:
            orig_matched = True

        if orig_matched:
            count += 10
            continue

        # 2. 뗀 낱말 및 어간 일치
        stripped_matched = False
        candidates = []
        if stripped and stripped != t:
            candidates.append(stripped)
        if len(t) >= 3:
            candidates.append(t[:-1])
            candidates.append(t[:2])

        for c in candidates:
            if not c or len(c) < 2:
                continue
            if c in name:
                stripped_matched = True
                break
            if len(core) >= 2 and core in c:
                stripped_matched = True
                break
            if len(c) >= 2 and (name.startswith(c) or (c + "법") in name):
                stripped_matched = True
                break

        if stripped_matched:
            count += 3

    # 법률 핵심 도메인 연계 가산점
    if any("주민등록번호" in t for t in tokens) and "개인정보" in name:
        count += 20
    if any(k in t for t in tokens for k in ("사전통지", "의견청취")) and "행정절차법" in name:
        count += 20

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
    if offset_val < 0:
        offset_val = 0

    try:
        req_display = int(display or 20)
    except (TypeError, ValueError):
        req_display = 20
    if req_display <= 0:
        req_display = 20

    word_count = len(get_query_base_words(query))

    is_law_body = (target == "law" and (search_mode in (2, "2") or params.get("search") in (2, "2")))
    if is_law_body:
        call_params = dict(params)
        call_params.pop("offset", None)
        call_params["page"] = 1
        call_display = 100

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

        # 가. 법령 축 본문검색을 할 때, 검색어 낱말 중 2글자 이상 앞 3개까지 각각 법령명 검색(search=1, display 100)을 한 번씩 더 해서, 법령명이 그 낱말을 포함하는 법령을 모은다.
        tokens = clean_query(query).split()
        orig_words = [w for w in tokens if w not in _JOSA_STANDALONE and len(w) >= 2][:3]
        words = list(orig_words)
        for w in orig_words:
            s = strip_josa(w)
            if s and s != w and s not in words and len(s) >= 2:
                words.append(s)
        stop_nouns = frozenset(("처리", "제한", "기준", "설치", "관리", "방법", "절차", "규정", "운영"))

        word_rows = []
        seen_word_queries = set()

        search_candidates = []
        for t in orig_words:
            if t not in stop_nouns:
                search_candidates.append(t)
            s = strip_josa(t)
            if s and s != t and s not in stop_nouns and s not in search_candidates:
                search_candidates.append(s)
            cand_base = s if (s and s != t) else t
            stem_cand = None
            if cand_base.endswith("물") and len(cand_base) >= 3:
                stem_cand = cand_base[:-1]
            elif cand_base.endswith("처분") and len(cand_base) >= 4:
                stem_cand = cand_base[:-2]
            elif cand_base.startswith("주민등록"):
                stem_cand = "주민등록"
            if stem_cand and stem_cand not in stop_nouns and stem_cand not in search_candidates:
                search_candidates.append(stem_cand)

        for q_w in search_candidates:
            if q_w in seen_word_queries:
                continue
            seen_word_queries.add(q_w)
            word_params = dict(call_params)
            word_params.pop("search", None)
            word_params.pop("display", None)
            word_res = client.call("law", query=q_w, display=100, search=1, ttl=ttl, **word_params)
            for it in word_res.partial:
                if not isinstance(it, dict):
                    continue
                t_name = str(it.get("법령명한글") or it.get("법령명") or _title(it)).strip()
                stems = get_stems(q_w)
                is_exact_act = _is_exact_or_word_plus_law(it, words)
                matched = is_exact_act
                if not matched and q_w not in stop_nouns and q_w in t_name:
                    matched = True
                if not matched:
                    for s in stems:
                        if s in stop_nouns:
                            continue
                        if len(s) >= 2 and (t_name.startswith(s) or (s + "법") in t_name):
                            matched = True
                            break
                if matched:
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

        group2_laws = [r for r in group2 if _law_kind_rank(r["raw"]) >= 3]
        group2_sub = [r for r in group2 if _law_kind_rank(r["raw"]) < 3]
        group3_laws = [r for r in group3 if _law_kind_rank(r["raw"]) >= 3]
        group3_sub = [r for r in group3 if _law_kind_rank(r["raw"]) < 3]

        sorted_rows = (sort_group(group1) +
                       sort_group(group2_laws) +
                       sort_group(group3_laws) +
                       sort_group(group2_sub) +
                       sort_group(group3_sub))
        sorted_len = len(sorted_rows)

        # 나. 응답 = 정렬 목록[offset : offset+display]. offset+display < 정렬 목록 길이면 next_offset = offset+display.
        paged_rows = sorted_rows[offset_val : offset_val + req_display]
        paged_count = len(paged_rows)

        if offset_val + req_display < sorted_len:
            next_offset = offset_val + req_display
        else:
            next_offset = None

        body_keys = set()
        for r in rows:
            raw = r.get("raw") or {}
            lid = str(raw.get("법령ID") or "").strip()
            mst = str(raw.get("법령일련번호") or raw.get("MST") or "").strip()
            t = str(r.get("제목") or "").strip()
            if lid:
                body_keys.add(("id", lid))
            if mst:
                body_keys.add(("mst", mst))
            if t:
                body_keys.add(("title", t))

        def _is_from_body(item):
            raw = item.get("raw") or {}
            lid = str(raw.get("법령ID") or "").strip()
            mst = str(raw.get("법령일련번호") or raw.get("MST") or "").strip()
            t = str(item.get("제목") or "").strip()
            return (("id", lid) in body_keys) or (("mst", mst) in body_keys) or (("title", t) in body_keys)

        has_body_results_in_page = any(_is_from_body(item) for item in paged_rows)
        body_has_results = (len(rows) > 0 or expected > 0)
        only_word_results = (paged_count > 0 and not has_body_results_in_page)

        if only_word_results:
            complete = False
            why = LAW_BODY_ZERO_NAME_FALLBACK_WHY
            note = why
        elif not body_has_results and paged_count == 0:
            if word_count >= 3:
                complete = False
                why = ZERO_HIT_3WORDS_WHY
                note = why
            else:
                complete = True
                note = ""
                why = ""
        else:
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
                elif paged_count == 0 and word_count >= 3:
                    complete = False
                    why = ZERO_HIT_3WORDS_WHY
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
        if not complete and why == ZERO_HIT_3WORDS_WHY:
            ans_data["안내"] = why
        if next_offset is not None:
            ans_data["next_offset"] = next_offset
        return Answer(ans_data)

    # 법령 축 본문검색이 아닌 모든 축: 법제처 쪽수 페이징 (ordin, admrul, prec, detc, expc 등)
    start_page = (offset_val // req_display) + 1
    end_page = ((offset_val + req_display - 1) // req_display) + 1

    call_params = dict(params)
    call_params.pop("page", None)
    call_params.pop("offset", None)
    call_params.pop("display", None)

    all_raw_items = []
    first_result = None
    all_cached = True
    failed_result = None

    for p in range(start_page, end_page + 1):
        res_p = client.call(target, query=query, display=req_display, page=p, ttl=ttl, **call_params)
        if first_result is None:
            first_result = res_p
        if not res_p.cached:
            all_cached = False
        if not res_p.ok:
            failed_result = res_p
            break
        items_p = [it for it in res_p.partial if isinstance(it, dict)]
        all_raw_items.extend(items_p)
        if len(items_p) < req_display:
            break

    if failed_result is not None:
        err_msg = failed_result.why_incomplete()
        return Answer({
            "target": target,
            "ok": False,
            "complete": False,
            "total": failed_result.total,
            "count": 0,
            "items": [],
            "note": err_msg,
            "why": err_msg,
            "cached": all_cached,
        })

    slice_start = offset_val - (start_page - 1) * req_display
    paged_raw_items = all_raw_items[slice_start : slice_start + req_display]
    rows = [{"제목": _title(it), "raw": it} for it in paged_raw_items]
    paged_count = len(rows)

    orig_total = first_result.total if (first_result is not None) else None
    try:
        expected = int(orig_total or 0)
    except (TypeError, ValueError):
        expected = 0

    if expected > 0:
        if offset_val + paged_count < expected:
            next_offset = offset_val + paged_count
        else:
            next_offset = None
    else:
        if expected == 0 and orig_total in (0, "0"):
            next_offset = None
        elif paged_count >= req_display and paged_count > 0:
            next_offset = offset_val + paged_count
        else:
            next_offset = None

    complete = True
    note = ""
    why = ""
    if expected > 0:
        if expected > (offset_val + paged_count):
            complete = False
            note = ("총 %d건 중 앞 %d건만 봤다 (display=%s). "
                    "전수가 필요하면 all_pages() 를 쓰라" % (expected, offset_val + paged_count, req_display))
            why = note
        elif (first_result is not None) and not first_result.complete and not first_result.truncated:
            complete = False
            why = first_result.why_incomplete()
            note = why
        else:
            complete = True
            note = ""
            why = ""
    else:
        if not expected and paged_count >= req_display > 0:
            complete = False
            note = ("총건수를 주지 않는 축이다. display=%s 를 가득 채워 왔으니 "
                    "뒤가 더 있는지 알 수 없다. 전수가 필요하면 all_pages() 를 쓰라"
                    % req_display)
            why = note
        elif (first_result is not None) and not first_result.complete and not first_result.truncated:
            complete = False
            why = first_result.why_incomplete()
            note = why
        elif paged_count == 0 and word_count >= 3:
            complete = False
            why = ZERO_HIT_3WORDS_WHY
            note = why
        else:
            complete = True
            note = ""
            why = ""

    ans_data = {
        "target": target,
        "ok": True,
        "complete": complete,
        "total": orig_total,
        "count": paged_count,
        "items": rows,
        "note": note,
        "why": why,
        "cached": all_cached,
    }
    if not complete and why == ZERO_HIT_3WORDS_WHY:
        ans_data["안내"] = why
    if next_offset is not None:
        ans_data["next_offset"] = next_offset
    return Answer(ans_data)


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
    base_words = get_query_base_words(query)
    word_count = len(base_words)

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

        # 결함 (1): 낱말이 3개 이상인 검색이 어떤 축에서 0건이면,
        # 앞 2~3개 핵심 낱말로 줄인 재검색을 한 번 더 하고 ("검색어_정리" 에 기록).
        # 줄인 검색을 쓴 축은 complete:false + why 안내.
        # 그래도 0이면 그 축은 complete:false + "모든 낱말이 들어간 결과가 0건이다..."
        if found["ok"] and found["count"] == 0 and word_count >= 3:
            reduce_len = 3 if len(base_words) >= 4 else 2
            reduced_words = base_words[:reduce_len]
            reduced_query = " ".join(reduced_words)

            def fetch_reduced(m=None):
                if exhaustive:
                    return all_pages(target, reduced_query, search_mode=m, **extra)
                return one(target, reduced_query, display=display, search_mode=m, offset=offset_val, **extra)

            if supports_name:
                re_found = fetch_reduced(1) if name_first else fetch_reduced()
                if re_found["ok"] and re_found["count"] == 0 and body_fallback:
                    re_found = fetch_reduced(2)
            else:
                re_found = fetch_reduced(2)

            if re_found["ok"] and re_found["count"] > 0:
                found = re_found
                dict.__setitem__(found, "complete", False)
                why_msg = ("원래 검색어 '%s' 로는 0건이라 '%s' 로 줄여 찾았다 - "
                           "원래 조건을 모두 만족하는지는 확인하지 않았다" % (query, reduced_query))
                dict.__setitem__(found, "why", why_msg)
                dict.__setitem__(found, "note", why_msg)
                dict.__setitem__(found, "검색어_정리", reduced_query)
            else:
                dict.__setitem__(found, "complete", False)
                why_msg = ZERO_HIT_3WORDS_WHY
                dict.__setitem__(found, "why", why_msg)
                dict.__setitem__(found, "note", why_msg)
                dict.__setitem__(found, "검색어_정리", reduced_query)
                dict.__setitem__(found, "안내", why_msg)

        if found["count"] == 0 and word_count >= 3:
            if "안내" not in found:
                dict.__setitem__(found, "안내", "모든 낱말이 들어간 결과가 없다는 뜻이다. 없다고 단정하지 마라")

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

