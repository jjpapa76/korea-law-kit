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


def _canonicalize_name(cand):
    """법령명을 띄어쓰기가 있는 정식 명칭으로 정규화한다."""
    if not cand:
        return cand
    cand = str(cand).strip()
    squashed = "".join(cand.split())
    # 1. 현행 법령에서 정식명 찾기
    cur = client.call("law", query=cand, search=1, display=10)
    if cur.ok:
        for item in cur.partial:
            t = text(item.get("법령명한글"))
            if t and "".join(t.split()) == squashed:
                return t
    # 2. 연혁 법령에서 정식명 찾기
    ef = client.call("eflaw", query=cand, search=1, display=10)
    if ef.ok:
        for item in ef.partial:
            t = text(item.get("법령명한글"))
            if t and "".join(t.split()) == squashed:
                return t
    return cand


def _find_same_id_candidate(name, last, ef_items=None):
    """동일 법령ID를 가진 판들 중 시행일자 순으로 바로 다음 이름을 직접_후보로,
    그 ID의 현행 이름이 다르면 현행_이름으로 추출한다."""
    lid = str(last.get("ID") or "")
    if not lid:
        return None
    name_squashed = "".join(str(name).split())

    # 1. 동일 ID 기본정보 조회
    id_body = client.call("eflaw", service=True, ID=lid)
    id_curr_title = None
    id_prev_title = None
    if id_body.ok and id_body.partial and isinstance(id_body.partial[0], dict):
        basic = id_body.partial[0].get("기본정보", {})
        id_curr_title = text(basic.get("법령명_한글"))
        id_prev_title = text(basic.get("이전법령명"))
        if id_curr_title:
            id_curr_title = _canonicalize_name(id_curr_title)
        if id_prev_title:
            id_prev_title = _canonicalize_name(id_prev_title)

    # 2. 동일 ID 판 목록 수집 (상한 5쪽)
    items = list(ef_items or [])
    if not items:
        ef_list = client.call_all("eflaw", query=name, search=1, page_size=100, max_pages=5)
        if not ef_list.ok or not ef_list.complete:
            return {
                "complete": False,
                "why": "같은 법령ID 연혁을 끝까지 받지 못했다"
            }
        items.extend(ef_list.partial)
    if id_curr_title and "".join(id_curr_title.split()) != name_squashed:
        ef_list2 = client.call("eflaw", query=id_curr_title, search=1, display=100)
        if ef_list2.ok:
            items.extend(ef_list2.partial)
    if id_prev_title and "".join(id_prev_title.split()) != name_squashed:
        ef_list3 = client.call("eflaw", query=id_prev_title, search=1, display=100)
        if ef_list3.ok:
            items.extend(ef_list3.partial)

    # 3. 동일 ID인 판만 모으기
    same_id_versions = []
    seen_keys = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        if str(it.get("법령ID") or "") == lid:
            t = text(it.get("법령명한글"))
            if not t:
                continue
            efYd = text(it.get("시행일자") or "")
            promYd = text(it.get("공포일자") or "")
            key = (efYd, promYd, "".join(t.split()))
            if key not in seen_keys:
                seen_keys.add(key)
                same_id_versions.append({
                    "법령명": t,
                    "시행일자": efYd,
                    "공포일자": promYd,
                })

    # 시행일자, 공포일자 오름차순 정렬
    same_id_versions.sort(key=lambda r: (r["시행일자"], r["공포일자"]))

    # 4. 입력 이름의 마지막 판 시행일자 이후 가장 먼저 나오는 다른 이름 찾기
    last_efyd = str(last.get("시행일자") or "")
    last_promyd = str(last.get("공포일자") or "")

    direct_cand = None
    for r in same_id_versions:
        r_squashed = "".join(r["법령명"].split())
        if r_squashed != name_squashed:
            if (r["시행일자"], r["공포일자"]) >= (last_efyd, last_promyd):
                direct_cand = _canonicalize_name(r["법령명"])
                break

    if not direct_cand and id_curr_title and "".join(id_curr_title.split()) != name_squashed:
        direct_cand = id_curr_title

    if not direct_cand:
        return None

    cand_obj = {
        "직접_후보": direct_cand,
        "근거": "같은 법령ID 제명변경"
    }

    # ID의 현행 이름이 직접_후보와 다르면 "현행_이름" 따로 넣기
    # latest_title 은 동일 ID 판들 중 가장 최근 판의 이름이거나 id_curr_title
    latest_title = None
    if same_id_versions:
        candidate_latest = same_id_versions[-1]["법령명"]
        if "".join(candidate_latest.split()) != name_squashed:
            latest_title = candidate_latest
    if not latest_title and id_curr_title and "".join(id_curr_title.split()) != name_squashed:
        latest_title = id_curr_title

    if latest_title:
        latest_title = _canonicalize_name(latest_title)
        if ("".join(latest_title.split()) != name_squashed and
                "".join(latest_title.split()) != "".join(direct_cand.split())):
            cand_obj["현행_이름"] = latest_title

    return cand_obj


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
    if not dict.get(old, "complete", True):
        # 연혁을 끝까지 못 봤으면 rows[0] 이 마지막 판이라는 보장이 없다.
        return {"name": name, "status": "미확인", "complete": False,
                "why": "연혁을 끝까지 보지 못했다 - %s" % old.why(),
                "candidates": []}
    if not rows:
        return {"name": name, "status": "미확인", "why": "연혁을 못 찾았다",
                "candidates": []}
    last = rows[0]
    last_kind = last.get("제개정") or ""
    name_squashed = "".join(str(name).split())

    candidates = []

    # 1. 마지막 판의 제개정구분이 폐지·타법폐지일 때만 "폐지시킨 법"(그 판 부칙의 법령명)을 후보로 한다.
    # 타법개정 판의 부칙 법령명은 그 법을 고친 다른 법일 뿐이니 절대 후보로 쓰지 않는다.
    if "폐지" in last_kind:
        # MST 로 부를 때는 efYd(시행일자)가 필수다. 빠뜨리면 HTML 이 오고,
        # 그 실패가 "후보 0개" 로 보였다(도시계획법 실측 2026-09-24).
        body = client.call("eflaw", service=True, MST=last["MST"],
                           efYd=last.get("시행일자", ""))
        if not body.ok or not body.complete:
            return {"name": name, "status": "구법", "why": why,
                    "last_version": last, "candidates": [],
                    "complete": False,
                    "note": "마지막 판 본문을 못 받아 후보를 못 뽑았다 - %s"
                            % (body.error or "끝까지 받지 못했다")}
        import json as _json
        import re
        blob = _json.dumps(body.partial, ensure_ascii=False)
        for pattern in (r"부칙\(([^)]{2,40}?(?:법|법률))\)",
                        r"「([^」]{4,40}?(?:법|법률))」"):
            for match in re.finditer(pattern, blob):
                title = match.group(1).strip()
                if "".join(title.split()) != name_squashed:
                    canon = _canonicalize_name(title)
                    cand_obj = {"직접_후보": canon, "근거": "폐지 부칙"}
                    # 폐지시킨 법이 현행인지 확인하고 구법이면 현행 이름/다음 이름 추적
                    is_alv, _ = is_current(canon)
                    if is_alv is False:
                        cand_v = versions(canon)
                        cand_rows = cand_v.partial("versions") or []
                        if cand_rows and cand_rows[0].get("ID"):
                            sub_cand = _find_same_id_candidate(canon, cand_rows[0])
                            if sub_cand and sub_cand.get("직접_후보"):
                                cand_obj["현행_이름"] = sub_cand["직접_후보"]
                    if not any(c.get("직접_후보") == cand_obj["직접_후보"] for c in candidates):
                        candidates.append(cand_obj)

    if not candidates and last.get("ID"):
        same_id_cand = _find_same_id_candidate(name, last)
        if same_id_cand and same_id_cand.get("complete") is False:
            return {"name": name, "status": "구법",
                    "why": same_id_cand.get("why") or "같은 법령ID 연혁을 끝까지 받지 못했다",
                    "last_version": last, "candidates": [], "complete": False,
                    "note": "같은 법령ID 연혁을 끝까지 받지 못했다 - 근거 없는 후보 금지"}
        if same_id_cand and same_id_cand.get("직접_후보"):
            candidates.append(same_id_cand)

    if not candidates:
        # 근거를 못 찾으면 complete:false + why "승계 근거를 찾지 못했다"
        return {"name": name, "status": "구법", "why": "승계 근거를 찾지 못했다",
                "last_version": last, "candidates": [], "complete": False,
                "note": "승계 근거를 찾지 못했다 - 거짓 후보 금지"}

    return {"name": name, "status": "구법", "why": why,
            "last_version": last,
            "candidates": candidates[:10],
            "complete": True,
            "note": ("후보다. 단정이 아니다 - 법제처 API 에 승계 관계를 "
                     "직접 주는 칸이 없다. 사람이 확인해야 한다")}


def compare(name, display=20):
    """신구법 대조 목록. 무엇이 바뀌었는지 볼 때."""
    result = client.call(TARGET_DIFF, query=name, search=1, display=display)
    return Answer({"name": name, "ok": result.ok,
                   "complete": result.complete,
                   "note": result.why_incomplete(),
                   "items": result.partial})
