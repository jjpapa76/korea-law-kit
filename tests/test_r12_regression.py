# -*- coding: utf-8 -*-
"""R12 결함 반례 및 회귀 방지 시험 (가짜 응답 / 키 지우기 기반).

망에 나가지 않는다 (monkeypatch 로 가짜 응답 주입 및 키 지우기).
테스트 항목:
    1. 결함 1: 현행법(건축법, 민법, 형법, 근로기준법) 및 구법(도시재개발법, 풍수해대책법)에서
       없는 조문 조회 시 complete: true + "조문 없음" 반환 (폐지 판으로 오판하지 않음).
    2. 결함 2: 환경보전법 제1조 연혁 eflaw 페이징(상한 5쪽)으로 정상 조회 및 조문 반환,
       5쪽 넘어도 끝을 못 봤을 때 예외 없이 complete: false + why 반환.
    3. 회귀: 도시계획법 제1조, 도시재개발법 제1조, 건축법 제1조 정상 조회.
"""
import copy
import json
import os
import pytest
import law_kit as kit
from law_kit import articles, laws
from law_kit.client import Result
from law_kit.shape import Answer

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

def _load_fixture(filename):
    path = os.path.join(FIXTURES_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- 결함 1 시험
@pytest.mark.parametrize("law_name, jo", [
    ("건축법", "제999조"),
    ("민법", "제5000조"),
    ("형법", "제888조"),
    ("근로기준법", "제777조"),
])
def test_current_law_missing_jo_never_claims_repealed(monkeypatch, law_name, jo):
    """현행법에 없는 조문을 조회했을 때 '폐지 판'으로 오판하지 않고 complete: true, '조문 없음'을 반환한다."""
    monkeypatch.delenv("LAW_API_OC", raising=False)
    monkeypatch.delenv("NATIONAL_LAW_API_OC", raising=False)

    monkeypatch.setattr(kit.laws, "find", lambda name, **k: [
        {"법령명": law_name, "MST": "12345", "현행": "현행", "찾은방법": "exact", "시행일자": "20260101"}
    ])

    def fake_call(target, service=False, **params):
        if params.get("JO"):
            # 요청한 조문은 없음
            return Result("law", ok=True, complete=True, items=[
                {"기본정보": {"법령명_한글": law_name, "시행일자": "20260101"}}
            ])
        else:
            # JO 없이 전체 조회 시에는 본문 조문들이 있음 (본문 있는 현행법)
            return Result("law", ok=True, complete=True, items=[
                {"기본정보": {"법령명_한글": law_name, "시행일자": "20260101"},
                 "조문": {"조문단위": [{"조문여부": "조문", "조문번호": "1", "조문가지번호": "0", "조문내용": "제1조 (목적)"}]}}
            ])

    monkeypatch.setattr(kit.client, "call", fake_call)

    res = articles.get_article(law_name, jo)
    assert res["complete"] is True
    assert res["status"] == "조문 없음"
    assert "폐지 판" not in str(res.get("why", ""))
    assert jo in res.get("why", "")


@pytest.mark.parametrize("law_name, jo, mst", [
    ("도시재개발법", "제999조", "55073"),
    ("풍수해대책법", "제999조", "4237"),
])
def test_historic_law_missing_jo_on_substantive_version_never_claims_repealed(monkeypatch, law_name, jo, mst):
    """본문 있는 구법에서 없는 조문을 조회했을 때 폐지 판으로 오판하지 않고 complete: true, '조문 없음'을 반환한다."""
    monkeypatch.delenv("LAW_API_OC", raising=False)
    monkeypatch.delenv("NATIONAL_LAW_API_OC", raising=False)

    monkeypatch.setattr(kit.laws, "find", lambda name, **k: [
        {"법령명": law_name, "MST": mst, "현행": "연혁", "찾은방법": "historic", "시행일자": "20030101"}
    ])
    monkeypatch.setattr(kit.history, "versions", lambda name, **k: Answer({
        "name": name, "ok": True, "complete": True,
        "versions": [
            {"법령명": law_name, "MST": mst, "시행일자": "20030101", "제개정": "타법개정", "상태": "연혁"}
        ]
    }))

    def fake_call(target, service=False, **params):
        if params.get("JO"):
            # 해당 조는 없음
            return Result("eflaw", ok=True, complete=True, items=[
                {"기본정보": {"법령명_한글": law_name, "시행일자": "20030101", "제개정구분": "타법개정"}}
            ])
        else:
            # JO 없이 전체 조회 시에는 다른 조문들이 있음 (본문 있는 구법)
            return Result("eflaw", ok=True, complete=True, items=[
                {"기본정보": {"법령명_한글": law_name, "시행일자": "20030101", "제개정구분": "타법개정"},
                 "조문": {"조문단위": [{"조문여부": "조문", "조문번호": "1", "조문가지번호": "0", "조문내용": "제1조"}]}}
            ])

    monkeypatch.setattr(kit.client, "call", fake_call)

    res = articles.get_article(law_name, jo)
    assert res["complete"] is True
    assert res["status"] == "조문 없음"
    assert "폐지 판" not in str(res.get("why", ""))
    assert jo in res.get("why", "")


# ---------------------------------------------------------------- 결함 2 시험
def test_historic_law_find_pages_up_to_5_pages(monkeypatch):
    """laws.find 가 연혁 법령 검색 시 1쪽에서 끝나지 않고 필요한 만큼(상한 5쪽) 쪽을 넘겨 검색한다."""
    monkeypatch.delenv("LAW_API_OC", raising=False)
    monkeypatch.delenv("NATIONAL_LAW_API_OC", raising=False)

    pages_called = []

    def fake_call(target, service=False, **params):
        if target == "law":
            return Result("law", ok=True, complete=True, items=[], total=0)
        if target == "eflaw":
            page = params.get("page", 1)
            pages_called.append(page)
            if page < 3:
                other_items = [{"법령명한글": "대기환경보전법 시행규칙", "법령ID": "0001", "법령일련번호": "100"}]
                return Result("eflaw", ok=True, complete=False, items=other_items, total=500)
            elif page == 3:
                matched = [{"법령명한글": "환경보전법", "법령약칭명": "", "법령ID": "001775",
                            "법령일련번호": "7660", "법령구분명": "법률", "소관부처명": "",
                            "시행일자": "19910202", "공포일자": "19900801", "현행연혁코드": "연혁"}]
                return Result("eflaw", ok=True, complete=False, items=matched, total=500)
        return Result(target, ok=True, complete=True, items=[], total=0)

    monkeypatch.setattr(kit.client, "call", fake_call)
    monkeypatch.setattr(kit.client, "call_all", lambda *a, **k: Result("lsAbrv", ok=True, complete=True, items=[], total=0))

    hits = laws.find("환경보전법")
    assert len(hits) == 1
    assert hits[0]["법령명"] == "환경보전법"
    assert hits[0]["MST"] == "7660"
    assert pages_called == [1, 2, 3]  # 3쪽에서 찾고 조기 종료


def test_historic_law_find_exceeding_5_pages_never_raises_in_get_article(monkeypatch):
    """연혁 법령 검색에서 5쪽을 넘겨도 끝을 못 보고 못 찾았을 때 Incomplete 예외로 크래시나지 않고 complete: false를 반환한다."""
    monkeypatch.delenv("LAW_API_OC", raising=False)
    monkeypatch.delenv("NATIONAL_LAW_API_OC", raising=False)

    def fake_call(target, service=False, **params):
        if target == "law":
            return Result("law", ok=True, complete=True, items=[], total=0)
        if target == "eflaw":
            other_items = [{"법령명한글": "다른법", "법령ID": "999", "법령일련번호": "999"}]
            return Result("eflaw", ok=True, complete=False, items=other_items, total=1000)
        return Result(target, ok=True, complete=True, items=[], total=0)

    monkeypatch.setattr(kit.client, "call", fake_call)
    monkeypatch.setattr(kit.client, "call_all", lambda *a, **k: Result("lsAbrv", ok=True, complete=True, items=[], total=0))

    res = articles.get_article("끝없는구법", "제1조")
    assert res["complete"] is False
    assert "조회가 온전하지 않았다" in res.get("why", "") or "찾을 수 없습니다" in res.get("why", "")


# ---------------------------------------------------------------- 회귀 시험
def test_regression_urban_planning_act_article_1(monkeypatch):
    """회귀: 도시계획법 제1조가 정상 조회된다 (실제 픽스처 사용)."""
    monkeypatch.delenv("LAW_API_OC", raising=False)
    monkeypatch.delenv("NATIONAL_LAW_API_OC", raising=False)

    urban_hist_payload = _load_fixture("urban_plan_act_art1_hist.json")

    monkeypatch.setattr(kit.laws, "find", lambda name, **k: [
        {"법령명": "도시계획법", "MST": "8776", "현행": "연혁", "찾은방법": "historic", "시행일자": "20000701"}
    ])
    monkeypatch.setattr(kit.history, "versions", lambda name, **k: Answer({
        "name": name, "ok": True, "complete": True,
        "versions": [
            {"법령명": "도시계획법", "MST": "8776", "시행일자": "20000701", "제개정": "전부개정", "상태": "연혁"}
        ]
    }))
    monkeypatch.setattr(kit.client, "call", lambda *a, **k: Result(
        "eflaw", ok=True, complete=True, items=urban_hist_payload
    ))

    res = articles.get_article("도시계획법", "제1조")
    assert res["complete"] is True
    assert res["조번호"] == "제1조"
    assert "도시계획의 수립 및 집행에 관하여" in res["조문내용"]


def test_regression_urban_redevelopment_act_article_1(monkeypatch):
    """회귀: 도시재개발법 제1조(폐지 직전 판 MST 55073)가 정상 조회된다."""
    monkeypatch.delenv("LAW_API_OC", raising=False)
    monkeypatch.delenv("NATIONAL_LAW_API_OC", raising=False)

    hist_template = _load_fixture("urban_plan_act_art1_hist.json")
    redevelop_payload = copy.deepcopy(hist_template)
    redevelop_payload[0]["기본정보"]["법령명_한글"] = "도시재개발법"
    redevelop_payload[0]["기본정보"]["시행일자"] = "20030101"
    redevelop_payload[0]["기본정보"]["제개정구분"] = "타법개정"
    redevelop_payload[0]["조문"]["조문단위"][1]["조문내용"] = "제1조 (목적) 이 법은 도시의 계획적인 재개발에 관하여 필요한 사항을 규정함을 목적으로 한다."

    monkeypatch.setattr(kit.laws, "find", lambda name, **k: [
        {"법령명": "도시재개발법", "MST": "58819", "현행": "연혁", "찾은방법": "historic", "시행일자": "20030701"}
    ])
    monkeypatch.setattr(kit.history, "versions", lambda name, **k: Answer({
        "name": name, "ok": True, "complete": True,
        "versions": [
            {"법령명": "도시재개발법", "MST": "58819", "시행일자": "20030701", "제개정": "타법폐지", "상태": "연혁"},
            {"법령명": "도시재개발법", "MST": "55073", "시행일자": "20030101", "제개정": "타법개정", "상태": "연혁"}
        ]
    }))
    monkeypatch.setattr(kit.client, "call", lambda *a, **k: Result(
        "eflaw", ok=True, complete=True, items=redevelop_payload
    ))

    res = articles.get_article("도시재개발법", "제1조")
    assert res["complete"] is True
    assert res["MST"] == "55073"
    assert res["조번호"] == "제1조"
    assert "도시의 계획적인 재개발" in res["조문내용"]


def test_regression_building_act_article_1(monkeypatch):
    """회귀: 건축법 제1조가 정상 조회된다 (실제 픽스처 사용)."""
    monkeypatch.delenv("LAW_API_OC", raising=False)
    monkeypatch.delenv("NATIONAL_LAW_API_OC", raising=False)

    building_payload = _load_fixture("building_act_art1.json")

    monkeypatch.setattr(kit.laws, "find", lambda name, **k: [
        {"법령명": "건축법", "MST": "273437", "현행": "현행", "찾은방법": "exact", "시행일자": "20240101"}
    ])
    monkeypatch.setattr(kit.client, "call", lambda *a, **k: Result(
        "law", ok=True, complete=True, items=building_payload
    ))

    res = articles.get_article("건축법", "제1조")
    assert res["complete"] is True
    assert res["조번호"] == "제1조"
    assert "건축물의 대지" in res["조문내용"]
