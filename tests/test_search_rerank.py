# -*- coding: utf-8 -*-
"""과업 1(법령 본문검색 재정렬) 쪽수 넘김 계약 및 실물 검증 시험."""
import itertools
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import law_kit as kit
from law_kit import mcp_server
from law_kit.client import Result

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def _load_fixture(filename):
    path = os.path.join(FIXTURES_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _get_sorted_fixture(fixture_items, query="건축물 용도변경 허가", word_items=None):
    """참조용 정렬 목록 생성."""
    tokens = kit.search.clean_query(query).split()
    words = [w for w in tokens if len(w) >= 2][:3]
    w_items = word_items or []

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

    all_raws = [{"제목": kit.search._title(r), "raw": r} for r in w_items + fixture_items]
    w_rows = [{"제목": kit.search._title(r), "raw": r} for r in w_items]
    body_rows = [{"제목": kit.search._title(r), "raw": r} for r in fixture_items]

    group1 = []
    group2 = []
    group3 = []

    for item in all_raws:
        if kit.search._is_exact_or_word_plus_law(item["raw"], words):
            if not _is_duplicate(item["raw"], item["제목"]):
                _mark_seen(item["raw"], item["제목"])
                group1.append(item)

    for item in w_rows:
        if not _is_duplicate(item["raw"], item["제목"]):
            _mark_seen(item["raw"], item["제목"])
            group2.append(item)

    for item in body_rows:
        if not _is_duplicate(item["raw"], item["제목"]):
            _mark_seen(item["raw"], item["제목"])
            group3.append(item)

    def sort_group(grp):
        def sort_key(entry):
            idx, r = entry
            raw = r.get("raw") or {}
            mc = kit.search._law_match_count(raw, tokens) if tokens else 0
            kr = kit.search._law_kind_rank(raw)
            return (-mc, -kr, idx)
        indexed = list(enumerate(grp))
        indexed.sort(key=sort_key)
        return [r for _, r in indexed]

    return sort_group(group1) + sort_group(group2) + sort_group(group3)


def test_fixture_rerank_order_and_total_preservation(monkeypatch):
    """시험: fixture(실물 응답)로 재정렬 순서, total 보존, 100건 초과 시 complete:false 검증."""
    fixture_items = _load_fixture("search_bldg_usage_change_100.json")
    assert len(fixture_items) == 100

    def fake_call(target, query=None, display=20, search=None, **params):
        if target == "law" and str(search) == "2":
            return Result("law", ok=True, complete=True, items=fixture_items, total=633)
        return Result(target, ok=True, complete=True, items=[], total=0)

    monkeypatch.setattr(kit.client, "call", fake_call)

    # 1. search.one 단독 호출 검증
    res_one = kit.search.one("law", "건축물 용도변경 허가", display=20, search_mode=2)
    # (1) total 보존
    assert res_one["total"] == 633
    # (2) 100건 초과 시 complete: false 와 why
    assert res_one["complete"] is False
    assert res_one["note"] == "총 633건 중 앞 100건 안에서만 정렬했다"
    assert res_one["why"] == "총 633건 중 앞 100건 안에서만 정렬했다"
    assert res_one.why() == "총 633건 중 앞 100건 안에서만 정렬했다"
    # (3) 정렬 표시
    assert res_one["정렬"] == "낱말 법령명 일치 → 본문검색 앞 100건(법제처는 관련도 정렬 미지원)"
    # (4) 요청한 개수만 담음
    items = res_one.partial("items")
    assert len(items) == 20
    # (5) 재정렬 순서: 상위 5개 안에 건축법 포함 (건축법이 1위로 승격)
    top5_titles = [it["제목"] for it in items[:5]]
    assert "건축법" in top5_titles
    assert top5_titles[0] == "건축법"


def test_pagination_combinations_matrix(monkeypatch):
    """시험(가짜 응답): offset 0/20/37/80/99/100, display 20/100, total 50/100/633/없음 조합.
    이어 붙인 쪽이 정렬 목록과 같은지, next_offset 산술, complete 값 검증.
    """
    full_fixture = _load_fixture("search_bldg_usage_change_100.json")
    offsets = [0, 20, 37, 80, 99, 100]
    displays = [20, 100]
    totals = [50, 100, 633, None]

    for total in totals:
        # total이 50이면 50건만 있는 fixture 사용
        if total == 50:
            fixture_items = full_fixture[:50]
        else:
            fixture_items = full_fixture

        expected_sorted = _get_sorted_fixture(fixture_items)
        sorted_len = len(expected_sorted)

        def make_fake_call(t_val, items_list):
            def fake_call(target, query=None, display=100, search=None, **params):
                if target == "law" and str(search) == "2":
                    return Result("law", ok=True, complete=True, items=items_list, total=t_val)
                return Result(target, ok=True, complete=True, items=[], total=0)
            return fake_call

        monkeypatch.setattr(kit.client, "call", make_fake_call(total, fixture_items))

        for display in displays:
            for offset in offsets:
                res = kit.search.one("law", "건축물 용도변경 허가", display=display, offset=offset, search_mode=2)

                # total 원래 값 보존 확인
                assert res["total"] == total

                # 다. offset >= 100 이면 재정렬하지 않고 complete:false + why
                if offset >= 100:
                    assert res["complete"] is False
                    assert res["why"] == "재정렬은 앞 100건까지다. 범위를 좁혀 다시 물어라"
                    assert res["count"] == 0
                    assert res.partial("items") == []
                    assert res.get("next_offset") is None
                    continue

                # 나. 응답 = 정렬 목록[offset : offset+display]
                items = res.partial("items")
                expected_slice = expected_sorted[offset : offset + display]
                assert len(items) == len(expected_slice)
                assert res["count"] == len(expected_slice)
                for it_res, it_exp in zip(items, expected_slice):
                    assert it_res["제목"] == it_exp["제목"]

                # next_offset 산술: offset+display < 정렬 목록 길이면 offset+display, 아니면 None
                expected_next = (offset + display) if (offset + display < sorted_len) else None
                assert res.get("next_offset") == expected_next

                # 라. total>100 이거나 total을 모르는데 100건을 꽉 채워 받았으면 항상 complete:false
                is_unknown_total = (total is None)
                if total == 633:
                    assert res["complete"] is False
                    assert res["why"] == "총 633건 중 앞 100건 안에서만 정렬했다"
                elif is_unknown_total and sorted_len >= 100:
                    assert res["complete"] is False
                    assert res["why"] == "총 알 수 없음 중 앞 100건 안에서만 정렬했다"
                elif total == 100:
                    if offset + len(items) >= 100:
                        assert res["complete"] is True
                        assert res["why"] == ""
                    else:
                        assert res["complete"] is False
                        assert "총 100건 중 앞" in res["why"]
                elif total == 50:
                    if offset + len(items) >= 50:
                        assert res["complete"] is True
                        assert res["why"] == ""
                    else:
                        assert res["complete"] is False
                        assert "총 50건 중 앞" in res["why"]


def test_pagination_concatenation_no_duplicates(monkeypatch):
    """마. 중복 없음: 여러 쪽을 이어 붙이면 정렬 목록과 순서·내용이 같아야 한다."""
    fixture_items = _load_fixture("search_bldg_usage_change_100.json")
    expected_sorted = _get_sorted_fixture(fixture_items)

    def fake_call(target, query=None, display=100, search=None, **params):
        if target == "law" and str(search) == "2":
            return Result("law", ok=True, complete=True, items=fixture_items, total=633)
        return Result(target, ok=True, complete=True, items=[], total=0)

    monkeypatch.setattr(kit.client, "call", fake_call)

    # 1. 고정 20건씩 5쪽 호출하여 이어 붙이기
    collected_fixed = []
    for off in [0, 20, 40, 60, 80]:
        res = kit.search.one("law", "건축물 용도변경 허가", display=20, offset=off, search_mode=2)
        collected_fixed.extend(res.partial("items"))

    assert len(collected_fixed) == 100
    for i in range(100):
        assert collected_fixed[i]["제목"] == expected_sorted[i]["제목"]

    # 2. next_offset 산술을 따라가며 이어 붙이기 (예: display=37)
    collected_paging = []
    curr_offset = 0
    while curr_offset is not None:
        res = kit.search.one("law", "건축물 용도변경 허가", display=37, offset=curr_offset, search_mode=2)
        items = res.partial("items")
        collected_paging.extend(items)
        curr_offset = res.get("next_offset")

    assert len(collected_paging) == 100
    for i in range(100):
        assert collected_paging[i]["제목"] == expected_sorted[i]["제목"]


def test_fixture_parking_order_and_dedup(monkeypatch):
    """시험: fixture(실물 응답, 키 지우기)로 낱말별 법령명 검색 순위(1/2/3)와 중복 제거 검증."""
    fix_data = _load_fixture("search_parking_fixture.json")

    def fake_call(target, query=None, display=20, search=None, **params):
        if target == "law":
            s = str(search)
            if s == "2" and query == "주차장 설치 기준":
                return Result("law", ok=True, complete=True, items=fix_data["body_100"], total=fix_data["total"])
            if s == "1":
                if query == "주차장":
                    return Result("law", ok=True, complete=True, items=fix_data["word_주차장"], total=len(fix_data["word_주차장"]))
                elif query == "설치":
                    return Result("law", ok=True, complete=True, items=fix_data["word_설치"], total=len(fix_data["word_설치"]))
                elif query == "기준":
                    return Result("law", ok=True, complete=True, items=fix_data["word_기준"], total=len(fix_data["word_기준"]))
        return Result(target, ok=True, complete=True, items=[], total=0)

    monkeypatch.setattr(kit.client, "call", fake_call)

    # 1. search.one 호출 검증
    res = kit.search.one("law", "주차장 설치 기준", display=20, offset=0, search_mode=2)
    items = res.partial("items")
    # (1) total 보존 및 complete: false
    assert str(res["total"]) == str(fix_data["total"])
    assert res["complete"] is False
    assert "앞 100건 안에서만 정렬했다" in res["why"]
    # (2) 정렬 표시
    assert res["정렬"] == "낱말 법령명 일치 → 본문검색 앞 100건(법제처는 관련도 정렬 미지원)"
    # (3) 순위 (1): 법령명이 '낱말+법'인 본령 '주차장법'이 최상위(1위)
    assert items[0]["제목"] == "주차장법"
    assert "주차장법" in [it["제목"] for it in items[:3]]

    # (4) 전 쪽수 순회 시 중복 없음 검증 (같은 법령ID 또는 MST는 1회만)
    all_paged = []
    curr_off = 0
    while curr_off is not None and curr_off < 100:
        p_res = kit.search.one("law", "주차장 설치 기준", display=20, offset=curr_off, search_mode=2)
        all_paged.extend(p_res.partial("items"))
        curr_off = p_res.get("next_offset")

    seen_ids = set()
    for it in all_paged:
        raw = it["raw"]
        lid = str(raw.get("법령ID") or "").strip()
        mst = str(raw.get("법령일련번호") or "").strip()
        k = (lid, mst) if (lid or mst) else it["제목"]
        assert k not in seen_ids, f"중복 법령 발견: {it['제목']} {k}"
        seen_ids.add(k)


def test_law_search_dispatch_rerank_bldg_and_urban():
    """실물 확인: law_search('건축물 용도변경 허가') 상위 5개에 건축법이 드는지, law_search('도시혁신구역')에 국토계획법 포함되는지."""
    # 1. 건축물 용도변경 허가
    out_bldg = mcp_server.dispatch_tool("law_search", {"query": "건축물 용도변경 허가", "display": 20})
    law_axis = out_bldg["axes"]["law"]
    items_bldg = law_axis.partial("items")
    top5_bldg = [it["제목"] for it in items_bldg[:5]]
    print("\n[실물 확인] 건축물 용도변경 허가 상위 5개:", top5_bldg)
    assert "건축법" in top5_bldg
    assert law_axis["정렬"] == "낱말 법령명 일치 → 본문검색 앞 100건(법제처는 관련도 정렬 미지원)"
    assert law_axis["complete"] is False
    assert "앞 100건 안에서만 정렬했다" in law_axis["why"]

    # 2. 도시혁신구역
    out_urban = mcp_server.dispatch_tool("law_search", {"query": "도시혁신구역", "display": 20})
    urban_axis = out_urban["axes"]["law"]
    items_urban = urban_axis.partial("items")
    top20_urban = [it["제목"] for it in items_urban]
    print("[실물 확인] 도시혁신구역 상위 20개 중 국토계획법 포함 여부:", "국토의 계획 및 이용에 관한 법률" in top20_urban)
    assert "국토의 계획 및 이용에 관한 법률" in top20_urban


def test_law_search_parking_act_in_top3():
    """실물 확인: law_search('주차장 설치 기준') 상위 3개에 주차장법이 드는지."""
    out_park = mcp_server.dispatch_tool("law_search", {"query": "주차장 설치 기준", "display": 20})
    law_axis = out_park["axes"]["law"]
    items_park = law_axis.partial("items")
    top3_park = [it["제목"] for it in items_park[:3]]
    print("\n[실물 확인] 주차장 설치 기준 상위 3개:", top3_park)
    assert "주차장법" in top3_park
    assert law_axis["정렬"] == "낱말 법령명 일치 → 본문검색 앞 100건(법제처는 관련도 정렬 미지원)"


def test_live_law_search_offset_no_overlap():
    """실물 확인: 각 쿼리 offset 0 과 20 이 겹치지 않는지 (건축물 용도변경 허가, 주차장 설치 기준, 도시혁신구역)."""
    queries = ["건축물 용도변경 허가", "주차장 설치 기준", "도시혁신구역"]
    for q in queries:
        out0 = kit.search.law_search(q, display=20, offset=0)
        out20 = kit.search.law_search(q, display=20, offset=20)

        items0 = out0["axes"]["law"].partial("items")
        items20 = out20["axes"]["law"].partial("items")
        assert len(items0) == 20
        assert len(items20) == 20

        ids0 = [it["raw"].get("법령일련번호") or it["raw"].get("법령ID") or it["제목"] for it in items0]
        ids20 = [it["raw"].get("법령일련번호") or it["raw"].get("법령ID") or it["제목"] for it in items20]
        overlap_ids = set(ids0) & set(ids20)
        assert len(overlap_ids) == 0, f"'{q}' offset 0과 20 사이에 겹치는 항목이 있습니다: {overlap_ids}"
        print(f"\n[실물 확인 완료] '{q}' offset 0 (20건)과 offset 20 (20건) 사이 겹침: 0건")
