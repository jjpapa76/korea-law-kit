# -*- coding: utf-8 -*-
"""과업 1(덩어리 1건 상한 축소 및 안내)과 과업 2(검색어 정리 및 조문 안내) 시험."""
import io
import json
import os
import re
import sys

import pytest

import law_kit as kit
from law_kit import mcp_server
from law_kit.client import Result


@pytest.fixture(autouse=True)
def restore_client_state():
    """시험 종료 후 CURRENT_CLIENT 상태를 원래대로 복원한다."""
    orig_name = mcp_server.CURRENT_CLIENT.get("name")
    orig_ver = mcp_server.CURRENT_CLIENT.get("version")
    try:
        yield
    finally:
        mcp_server.CURRENT_CLIENT["name"] = orig_name
        mcp_server.CURRENT_CLIENT["version"] = orig_ver


def test_law_call_single_large_item_senpi_client(monkeypatch):
    """과업 (1) 덩어리 1건: senpi-mcp-client 상한 내 축소, 1건 유지, why 뺀 수, 안내(JO) 확인."""
    mcp_server.CURRENT_CLIENT["name"] = "senpi-mcp-client"
    max_chars, max_bytes = mcp_server.get_client_limits("senpi-mcp-client")
    assert (max_chars, max_bytes) == (15000, 40000)

    # 항목 1개짜리 거대한 dict (안에 '조문단위' 목록 300개, 각 원소에 긴 문자열)
    huge_articles = [
        {
            "조문번호": str(i),
            "조문내용": "제%d조(목적) 이 법은 테스트를 위한 조문입니다. 내용이 길어집니다. " % i * 5,
        }
        for i in range(1, 301)
    ]
    huge_item = {
        "기본정보": "대한민국 법률 제1호",
        "조문단위": huge_articles,
    }

    mock_res = Result("law", ok=True, items=[huge_item], total=1, complete=True)

    # kit.client.call 가짜로 대체
    monkeypatch.setattr(kit.client, "call", lambda target, service=False, **params: mock_res)

    # law_call target='law' service=True params={'MST':'1'} (JO 없음)
    dispatched = mcp_server.dispatch_tool(
        "law_call",
        {"target": "law", "service": True, "params": {"MST": "1"}},
    )

    serialized = mcp_server.serialize_response(dispatched)
    serialized_bytes = serialized.encode("utf-8")

    # 1. 클라이언트 상한 (15000자, 40000바이트) 안인지
    assert len(serialized) <= max_chars
    assert len(serialized_bytes) <= max_bytes

    parsed = json.loads(serialized)

    # 2. 항목이 0개가 아니며 (1개 남음)
    assert parsed.get("count") == 1
    partial_data = parsed.get("partial")
    assert isinstance(partial_data, dict)
    items = partial_data.get("items")
    assert isinstance(items, list)
    assert len(items) == 1

    # 3. why 에 뺀 수가 들어 있는지
    why_text = parsed.get("why") or ""
    assert "why" in parsed and why_text
    remaining_articles = len(items[0].get("조문단위", []))
    omitted_count = 300 - remaining_articles
    assert omitted_count > 0
    assert str(omitted_count) in why_text
    assert "조문단위" in why_text
    assert "뺐다" in why_text

    # 4. 머리에 '안내' 로 'JO' 가 들어간 문장이 있는지
    assert "안내" in parsed
    assert "JO" in parsed["안내"]
    # 머리 쪽에 위치하는지 키 순서 확인
    keys = list(parsed.keys())
    assert keys.index("안내") < keys.index("partial")


def test_law_call_with_jo_no_guide(monkeypatch):
    """과업 (1) JO를 준 호출에는 '안내'가 없는지 확인."""
    mock_res_jo = Result(
        "law",
        ok=True,
        items=[{"기본정보": "법", "조문단위": [{"조문번호": "1", "조문내용": "내용"}]}],
        total=1,
        complete=True,
    )
    monkeypatch.setattr(kit.client, "call", lambda target, service=False, **params: mock_res_jo)

    dispatched_jo = mcp_server.dispatch_tool(
        "law_call",
        {"target": "law", "service": True, "params": {"MST": "1", "JO": "000100"}},
    )
    # dispatched 결과에 '안내' 가 없어야 함
    if isinstance(dispatched_jo, dict):
        assert "안내" not in dispatched_jo

    serialized_jo = mcp_server.serialize_response(dispatched_jo)
    parsed_jo = json.loads(serialized_jo)
    assert "안내" not in parsed_jo


def test_law_call_small_item_complete_true(monkeypatch):
    """과업 (1) 결과가 상한 안에 다 들어가는 작은 1건이면 complete가 true인지 (안내만 있고 불완전 아님)."""
    small_item = {
        "기본정보": "작은 법",
        "조문단위": [{"조문번호": "1", "조문내용": "간단한 조문 내용"}],
    }
    mock_res_small = Result("law", ok=True, items=[small_item], total=1, complete=True)
    monkeypatch.setattr(kit.client, "call", lambda target, service=False, **params: mock_res_small)

    dispatched_small = mcp_server.dispatch_tool(
        "law_call",
        {"target": "law", "service": True, "params": {"MST": "1"}},
    )
    serialized_small = mcp_server.serialize_response(dispatched_small)
    parsed_small = json.loads(serialized_small)

    # 안내만 있고 complete 는 True, why는 없음
    assert parsed_small.get("complete") is True
    assert "안내" in parsed_small
    assert "JO" in parsed_small["안내"]
    assert parsed_small.get("why") is None


def test_law_search_query_cleanup_and_article_guide(monkeypatch):
    """과업 (2) 검색어 정리: 따옴표 제거 질의 전달, 응답 머리 '검색어_정리' 및 '안내'(law_article) 확인."""
    passed_calls = []

    def fake_across(query, display=20, *args, **kwargs):
        passed_calls.append((query, display))
        return {
            "axes": {
                "law": {
                    "label": "법령",
                    "items": [{"법령명한글": "국토의 계획 및 이용에 관한 법률", "MST": "1"}],
                    "count": 1,
                    "total": 1,
                    "complete": True,
                }
            },
            "incomplete": [],
        }

    monkeypatch.setattr(kit.search, "across", fake_across)

    query_input = '"국토의 계획 및 이용에 관한 법률" "제84조"'
    dispatched = mcp_server.dispatch_tool("law_search", {"query": query_input})

    # 1. search.across 에 실제로 넘어간 질의에 따옴표가 없는지
    assert len(passed_calls) == 1
    passed_query, _ = passed_calls[0]
    assert '"' not in passed_query
    assert "'" not in passed_query
    assert passed_query == "국토의 계획 및 이용에 관한 법률 제84조"

    # 2. 응답 머리에 '검색어_정리'와 '안내'(law_article 언급)가 있는지
    serialized = mcp_server.serialize_response(dispatched)
    parsed = json.loads(serialized)

    assert "검색어_정리" in parsed
    assert parsed["검색어_정리"] == "국토의 계획 및 이용에 관한 법률 제84조"

    assert "안내" in parsed
    assert "law_article" in parsed["안내"]

    # 머리에 위치하는지 확인 (axes 앞에 배치)
    keys = list(parsed.keys())
    assert keys.index("검색어_정리") < keys.index("axes")
    assert keys.index("안내") < keys.index("axes")


def test_law_search_plain_query_no_cleanup(monkeypatch):
    """과업 (2) 따옴표 없는 평범한 질의에는 '검색어_정리'가 없는지 확인."""
    monkeypatch.setattr(
        kit.search,
        "across",
        lambda query, display=20, *args, **kwargs: {
            "axes": {
                "law": {
                    "label": "법령",
                    "items": [{"법령명한글": "건축법", "MST": "10"}],
                    "count": 1,
                    "total": 1,
                    "complete": True,
                }
            },
            "incomplete": [],
        },
    )

    plain_query = "건축법"
    dispatched = mcp_server.dispatch_tool("law_search", {"query": plain_query})
    serialized = mcp_server.serialize_response(dispatched)
    parsed = json.loads(serialized)

    assert "검색어_정리" not in dispatched
    assert "검색어_정리" not in parsed
    assert "안내" not in parsed


def test_strip_josa_and_extract_keywords():
    """조사 떼기 (의 및 에 에서 을 를 은 는 이 가 와 과 등) 및 키워드 추출 검증."""
    assert kit.search.strip_josa("건축물의") == "건축물"
    assert kit.search.strip_josa("처리의") == "처리"
    assert kit.search.strip_josa("행정처분의") == "행정처분"
    assert kit.search.strip_josa("및") == ""
    assert kit.search.strip_josa("도로에서") == "도로"
    assert kit.search.strip_josa("기준으로") == "기준"
    assert kit.search.strip_josa("의견청취") == "의견청취"

    keywords = kit.search.extract_keywords("건축물의 대지 및 도로")
    assert "건축물" in keywords
    assert "대지" in keywords
    assert "도로" in keywords
    assert "및" not in keywords


def test_case_search_zero_retry_and_guide(monkeypatch):
    """시험: fixture(키 지우기)로 3단어 이상 0건 시 재검색 경로, 검색어_정리 및 축별 안내 검증."""
    calls = []

    def fake_call(target, query=None, display=20, search=None, **params):
        calls.append((target, query, search))
        if query == "통상임금 정기성 일률성 고정성 전원합의체":
            # 첫 질의는 0건 반환 (법제처 AND 매칭 실패 모사)
            return Result(target, ok=True, complete=True, items=[], total=0)
        elif query == "통상임금 정기성 일률성":
            # 줄인 검색어는 성공 결과 반환
            mock_case = {
                "판례일련번호": "1",
                "사건명": "퇴직금",
                "사건번호": "2012다89399",
                "판결유형": "전원합의체 판결",
                "법원명": "대법원",
            }
            return Result(target, ok=True, complete=True, items=[mock_case], total=1)
        return Result(target, ok=True, complete=True, items=[], total=0)

    monkeypatch.setattr(kit.client, "call", fake_call)

    # 1. 재검색 성공 케이스
    out = kit.search.across(
        "통상임금 정기성 일률성 고정성 전원합의체",
        axes=[("prec", "판례", False)],
        display=20,
    )
    prec_axis = out["axes"]["prec"]
    assert prec_axis["count"] == 1
    assert prec_axis.get("검색어_정리") == "통상임금 정기성 일률성"
    assert prec_axis["complete"] is False
    expected_why = ("원래 검색어 '통상임금 정기성 일률성 고정성 전원합의체' 로는 0건이라 "
                    "'통상임금 정기성 일률성' 로 줄여 찾았다 - 원래 조건을 모두 만족하는지는 확인하지 않았다")
    assert prec_axis["why"] == expected_why
    assert "prec" in out["incomplete"]
    items = prec_axis.partial("items")
    assert items[0]["raw"]["사건번호"] == "2012다89399"

    # MCP 도구 및 최상위 complete, 축별 머리 요약 일치 검증
    dispatched = mcp_server.dispatch_tool("law_search", {"query": "통상임금 정기성 일률성 고정성 전원합의체"})
    assert dispatched["축별"]["판례"]["complete"] is False
    serialized = mcp_server.serialize_response(dispatched)
    parsed = json.loads(serialized)
    assert parsed["complete"] is False
    if "축별" in parsed and parsed["축별"]:
        assert parsed["축별"]["판례"]["complete"] is False

    # 2. 재검색 후에도 0건인 케이스 -> complete:false 및 why 확인
    def fake_call_always_zero(target, query=None, display=20, search=None, **params):
        return Result(target, ok=True, complete=True, items=[], total=0)

    monkeypatch.setattr(kit.client, "call", fake_call_always_zero)

    out_zero = kit.search.across(
        "존재하지 않는 가상의 판례 낱말들",
        axes=[("prec", "판례", False)],
        display=20,
    )
    prec_zero = out_zero["axes"]["prec"]
    assert prec_zero["count"] == 0
    assert "검색어_정리" in prec_zero
    assert prec_zero["complete"] is False
    expected_zero_why = "모든 낱말이 들어간 결과가 0건이다. 없다고 단정하지 마라 - 낱말을 줄여 다시 물어라"
    assert prec_zero["why"] == expected_zero_why
    assert "prec" in out_zero["incomplete"]

    # MCP 도구 및 최상위 complete, 축별 머리 요약 일치 검증
    dispatched_zero = mcp_server.dispatch_tool("law_search", {"query": "존재하지 않는 가상의 판례 낱말들"})
    assert dispatched_zero["축별"]["판례"]["complete"] is False
    serialized_zero = mcp_server.serialize_response(dispatched_zero)
    parsed_zero = json.loads(serialized_zero)
    assert parsed_zero["complete"] is False
    if "축별" in parsed_zero and parsed_zero["축별"]:
        assert parsed_zero["축별"]["판례"]["complete"] is False


def test_statute_search_ranking_fixture(monkeypatch):
    """시험: fixture(키 지우기)로 건축물의 대지 및 도로 -> 건축법 1위 및 주민등록번호 처리의 제한 -> 개인정보 보호법 상위 검증."""
    def fake_call(target, query=None, display=20, search=None, **params):
        s = str(search) if search is not None else ""
        if target == "law":
            if s == "2" and query == "건축물의 대지 및 도로":
                return Result("law", ok=True, complete=True, items=[
                    {"법령명한글": "초고층 및 지하연계 복합건축물 재난관리에 관한 특별법", "법령구분명": "법률"},
                    {"법령명한글": "건축법", "법령구분명": "법률"},
                ], total=2)
            if s == "1" and query in ("건축", "건축법"):
                return Result("law", ok=True, complete=True, items=[
                    {"법령명한글": "건축법", "법령구분명": "법률"},
                    {"법령명한글": "건축법 시행령", "법령구분명": "대통령령"},
                ], total=2)
            if s == "2" and query == "주민등록번호 처리의 제한":
                return Result("law", ok=True, complete=True, items=[
                    {"법령명한글": "평창올림픽 지원 등에 관한 특별법", "법령구분명": "법률"},
                    {"법령명한글": "가족관계의 등록 등에 관한 법률", "법령구분명": "법률"},
                    {"법령명한글": "개인정보 보호법", "법령구분명": "법률"},
                ], total=3)
            if s == "1" and query in ("주민등록", "주민등록법"):
                return Result("law", ok=True, complete=True, items=[
                    {"법령명한글": "주민등록법", "법령구분명": "법률"},
                ], total=1)
            if s == "2" and query == "행정처분의 사전통지 및 의견청취":
                return Result("law", ok=True, complete=True, items=[
                    {"법령명한글": "가축전염병 예방법 시행령", "법령구분명": "대통령령"},
                    {"법령명한글": "국토의 계획 및 이용에 관한 법률 시행령", "법령구분명": "대통령령"},
                ], total=2)
            if s == "1" and query in ("행정", "행정처분", "행정절차법"):
                return Result("law", ok=True, complete=True, items=[
                    {"법령명한글": "행정절차법", "법령구분명": "법률"},
                    {"법령명한글": "행정기본법", "법령구분명": "법률"},
                ], total=2)
        return Result(target, ok=True, complete=True, items=[], total=0)

    monkeypatch.setattr(kit.client, "call", fake_call)

    # 1. 건축물의 대지 및 도로 -> 건축법 1위
    res1 = kit.search.one("law", "건축물의 대지 및 도로", display=20, search_mode=2)
    items1 = res1.partial("items")
    assert items1[0]["제목"] == "건축법"

    # 2. 주민등록번호 처리의 제한 -> 개인정보 보호법 상위 5개 포함
    res2 = kit.search.one("law", "주민등록번호 처리의 제한", display=20, search_mode=2)
    items2 = res2.partial("items")
    top_titles = [it["제목"] for it in items2[:5]]
    assert "개인정보 보호법" in top_titles

    # 3. 행정처분의 사전통지 및 의견청취 -> 행정절차법 상위 5개 포함
    res3 = kit.search.one("law", "행정처분의 사전통지 및 의견청취", display=20, search_mode=2)
    items3 = res3.partial("items")
    top_titles3 = [it["제목"] for it in items3[:5]]
    assert "행정절차법" in top_titles3


def test_strip_josa_rules_and_minimum_length():
    """조사 떼기 규칙 검증: 뗀 결과가 2글자 미만이면 떼지 않고, 원문 보존."""
    # 뗀 결과가 2글자 미만이면 떼지 않음
    assert kit.search.strip_josa("차로") == "차로"
    assert kit.search.strip_josa("비가") == "비가"
    assert kit.search.strip_josa("해로") == "해로"

    # 조사가 붙은 단어는 정상 분리
    assert kit.search.strip_josa("건축물의") == "건축물"
    assert kit.search.strip_josa("도로에서") == "도로"
    assert kit.search.strip_josa("기준으로") == "기준"


def test_children_playground_safety_act_fixture(monkeypatch):
    """시험: fixture로 '어린이 놀이시설 안전관리' 검색 시 원문 검색어 보존 및 어린이놀이시설 안전관리법 상위 5개 포함 검증."""
    queries_called = []

    def fake_call(target, query=None, display=20, search=None, **params):
        s = str(search) if search is not None else ""
        queries_called.append((target, query, s))
        if target == "law":
            if s == "2" and query == "어린이 놀이시설 안전관리":
                return Result("law", ok=True, complete=True, items=[
                    {"법령명한글": "도시공원 및 녹지 등에 관한 법률", "법령구분명": "법률"},
                    {"법령명한글": "어린이놀이시설 안전관리법", "법령구분명": "법률"},
                    {"법령명한글": "재난 및 안전관리 기본법", "법령구분명": "법률"},
                ], total=3)
            if s == "1" and query == "어린이":
                return Result("law", ok=True, complete=True, items=[
                    {"법령명한글": "어린이놀이시설 안전관리법", "법령구분명": "법률"},
                    {"법령명한글": "어린이놀이시설 안전관리법 시행령", "법령구분명": "대통령령"},
                    {"법령명한글": "어린이식생활안전관리특별법", "법령구분명": "법률"},
                ], total=3)
            if s == "1" and query == "놀이시설":
                return Result("law", ok=True, complete=True, items=[
                    {"법령명한글": "어린이놀이시설 안전관리법", "법령구분명": "법률"},
                ], total=1)
        return Result(target, ok=True, complete=True, items=[], total=0)

    monkeypatch.setattr(kit.client, "call", fake_call)

    # 1. 법제처 본문검색에 전송된 쿼리가 원문 '어린이 놀이시설 안전관리' 그대로인지 확인
    res = kit.search.one("law", "어린이 놀이시설 안전관리", display=20, search_mode=2)
    assert any(q == "어린이 놀이시설 안전관리" and s == "2" for _, q, s in queries_called)

    # 2. 낱말별 법령명 검색(search=1)에서 '어린'이 아닌 원래 낱말 '어린이'로 검색되었는지 확인
    assert any(q == "어린이" and s == "1" for _, q, s in queries_called)

    # 3. 상위 5개에 '어린이놀이시설 안전관리법' 포함 확인
    items = res.partial("items")
    top_titles = [it["제목"] for it in items[:5]]
    assert "어린이놀이시설 안전관리법" in top_titles
    assert items[0]["제목"] == "어린이놀이시설 안전관리법"


def test_parking_act_regression_fixture(monkeypatch):
    """시험: fixture로 '주차장 설치 기준' -> 주차장법 1위 회귀 검증."""
    def fake_call(target, query=None, display=20, search=None, **params):
        s = str(search) if search is not None else ""
        if target == "law":
            if s == "2" and query == "주차장 설치 기준":
                return Result("law", ok=True, complete=True, items=[
                    {"법령명한글": "주차장법 시행령", "법령구분명": "대통령령"},
                    {"법령명한글": "주차장법", "법령구분명": "법률"},
                ], total=2)
            if s == "1" and query == "주차장":
                return Result("law", ok=True, complete=True, items=[
                    {"법령명한글": "주차장법", "법령구분명": "법률"},
                    {"법령명한글": "주차장법 시행규칙", "법령구분명": "부령"},
                ], total=2)
        return Result(target, ok=True, complete=True, items=[], total=0)

    monkeypatch.setattr(kit.client, "call", fake_call)

    res = kit.search.one("law", "주차장 설치 기준", display=20, search_mode=2)
    items = res.partial("items")
    assert items[0]["제목"] == "주차장법"


def test_non_law_axes_paging_and_offset_fixtures(monkeypatch):
    """시험: 가짜 응답으로 축별 offset 0/20/37, display 20 에서 쪽 번호·자르기·next_offset 산술, 이어 붙였을 때 중복 0."""
    total_mock_count = 100
    mock_db = [{"자치법규명": "조례_%03d" % i,
                "행정규칙명": "훈령_%03d" % i,
                "사건명": "판례_%03d" % i,
                "안건명": "해석례_%03d" % i,
                "제목": "결정례_%03d" % i,
                "일련번호": i} for i in range(total_mock_count)]

    called_pages = []

    def fake_call(target, query=None, display=20, page=1, **params):
        p = int(page or 1)
        d = int(display or 20)
        called_pages.append((target, p, d))
        start_idx = (p - 1) * d
        end_idx = p * d
        page_items = mock_db[start_idx:end_idx]
        return Result(target, ok=True, complete=True, items=page_items, total=total_mock_count)

    monkeypatch.setattr(kit.client, "call", fake_call)

    for target in ("ordin", "admrul", "prec", "detc", "expc"):
        # 1. offset = 0, display = 20
        called_pages.clear()
        res0 = kit.search.one(target, "검색어", display=20, offset=0)
        items0 = res0.partial("items")
        assert len(items0) == 20
        assert res0.get("next_offset") == 20
        assert res0.get("total") == 100
        # 쪽 번호 1만 호출됨
        assert called_pages == [(target, 1, 20)]
        ids0 = [it["raw"]["일련번호"] for it in items0]
        assert ids0 == list(range(0, 20))

        # 2. offset = 20, display = 20
        called_pages.clear()
        res20 = kit.search.one(target, "검색어", display=20, offset=20)
        items20 = res20.partial("items")
        assert len(items20) == 20
        assert res20.get("next_offset") == 40
        assert res20.get("total") == 100
        # 쪽 번호 2만 호출됨
        assert called_pages == [(target, 2, 20)]
        ids20 = [it["raw"]["일련번호"] for it in items20]
        assert ids20 == list(range(20, 40))

        # 이어 붙였을 때 중복 0 확인
        combined_ids = ids0 + ids20
        assert len(combined_ids) == 40
        assert len(set(combined_ids)) == 40
        assert len(set(ids0) & set(ids20)) == 0

        # 3. offset = 37, display = 20 (쪽 번호 2, 3 호출 및 잘라내기 검증)
        called_pages.clear()
        res37 = kit.search.one(target, "검색어", display=20, offset=37)
        items37 = res37.partial("items")
        assert len(items37) == 20
        assert res37.get("next_offset") == 57
        assert res37.get("total") == 100
        # 쪽 번호 2와 3이 호출됨
        assert called_pages == [(target, 2, 20), (target, 3, 20)]
        ids37 = [it["raw"]["일련번호"] for it in items37]
        # 인덱스 37부터 56까지 정확히 잘렸는지 검증
        assert ids37 == list(range(37, 57))


def test_mcp_server_law_search_offset_dispatch(monkeypatch):
    """MCP law_search 도구에서 offset 인자가 across 에 정확히 전달되는지 검증."""
    received_offset = []

    def fake_across(query, display=20, offset=0, **extra):
        received_offset.append((query, display, offset))
        return {
            "query": query,
            "axes": {
                "ordin": kit.shape.Answer({"target": "ordin", "label": "자치법규", "complete": False, "total": 100, "count": 20, "next_offset": offset + 20, "items": []})
            },
            "incomplete": ["ordin"]
        }

    monkeypatch.setattr(kit.search, "across", fake_across)
    out = mcp_server.dispatch_tool("law_search", {"query": "주차장", "display": 20, "offset": 20})
    assert received_offset == [("주차장", 20, 20)]
    assert out["axes"]["ordin"]["next_offset"] == 40


def test_one_zero_hit_three_or_more_words_fixture(monkeypatch):
    """결함 (1) 시험: search.one 에서 낱말 3개 이상 0건 시 complete:false + why 안내 확인."""
    def fake_call(target, query=None, **kwargs):
        return Result(target, ok=True, complete=True, items=[], total=0)

    monkeypatch.setattr(kit.client, "call", fake_call)

    # 1. 판례 축 (prec) 3단어 이상 0건
    q = "취득세 중과세 대상인 고급주택에 해당하는지 여부의 판단 기준"
    res_prec = kit.search.one("prec", q)
    assert res_prec["count"] == 0
    assert res_prec["complete"] is False
    expected_why = "모든 낱말이 들어간 결과가 0건이다. 없다고 단정하지 마라 - 낱말을 줄여 다시 물어라"
    assert res_prec["why"] == expected_why
    assert res_prec["note"] == expected_why
    assert res_prec.get("안내") == expected_why

    # 2. 법령 축 (law, search_mode=2) 3단어 이상 0건 (본문 및 법령명 둘 다 0건)
    res_law = kit.search.one("law", q, search_mode=2)
    assert res_law["count"] == 0
    assert res_law["complete"] is False
    assert res_law["why"] == expected_why
    assert res_law["note"] == expected_why
    assert res_law.get("안내") == expected_why


def test_one_zero_hit_one_or_two_words_remains_complete_true(monkeypatch):
    """결함 (1) 시험: search.one 에서 낱말 1~2개 0건은 complete:true, why '' 유지 확인."""
    def fake_call(target, query=None, **kwargs):
        return Result(target, ok=True, complete=True, items=[], total=0)

    monkeypatch.setattr(kit.client, "call", fake_call)

    # 1. 낱말 1개
    res_1 = kit.search.one("prec", "취득세")
    assert res_1["count"] == 0
    assert res_1["complete"] is True
    assert res_1["why"] == ""
    assert res_1["note"] == ""

    # 2. 낱말 2개
    res_2 = kit.search.one("prec", "고급주택 판단")
    assert res_2["count"] == 0
    assert res_2["complete"] is True
    assert res_2["why"] == ""
    assert res_2["note"] == ""

    # 3. 법령 축 낱말 1개
    res_law_1 = kit.search.one("law", "취득세", search_mode=2)
    assert res_law_1["count"] == 0
    assert res_law_1["complete"] is True
    assert res_law_1["why"] == ""

    # 4. 법령 축 낱말 2개
    res_law_2 = kit.search.one("law", "고급주택 판단", search_mode=2)
    assert res_law_2["count"] == 0
    assert res_law_2["complete"] is True
    assert res_law_2["why"] == ""


def test_across_law_body_zero_name_fallback_fixture(monkeypatch):
    """결함 (2) 시험: search.across 법령 축 본문검색 0건인데 낱말별 법령명 검색으로 채운 결과 complete:false + why 확인."""
    def fake_call(target, query=None, search=None, **kwargs):
        s = str(search) if search is not None else ""
        if target == "law":
            if s == "1" and query == "BIM으로 설계된 건축물의 건축허가":
                return Result("law", ok=True, complete=True, items=[], total=0)
            if s == "2" and query == "BIM으로 설계된 건축물의 건축허가":
                # 본문검색 0건
                return Result("law", ok=True, complete=True, items=[], total=0)
            if s == "1" and query in ("건축물", "건축"):
                return Result("law", ok=True, complete=True, items=[
                    {"법령명한글": "건축법", "법령구분명": "법률", "법령일련번호": "100"},
                    {"법령명한글": "건축법 시행령", "법령구분명": "대통령령", "법령일련번호": "101"},
                ], total=2)
        return Result(target, ok=True, complete=True, items=[], total=0)

    monkeypatch.setattr(kit.client, "call", fake_call)

    # 1. search.one ("law", search_mode=2) 단독 호출 확인
    res_one = kit.search.one("law", "BIM으로 설계된 건축물의 건축허가", search_mode=2)
    assert res_one["count"] == 2
    assert res_one["complete"] is False
    expected_why = "본문검색은 0건이고, 낱말이 이름에 들어간 법을 대신 보였다 - 원래 검색어 전체를 만족하는지는 확인하지 않았다"
    assert res_one["why"] == expected_why
    assert res_one["note"] == expected_why
    items_one = res_one.partial("items")
    assert items_one[0]["제목"] == "건축법"

    # 2. search.across 호출 확인
    res_across = kit.search.across(
        "BIM으로 설계된 건축물의 건축허가",
        axes=[("law", "법령", True)],
        display=20,
    )
    law_axis = res_across["axes"]["law"]
    assert law_axis["count"] == 2
    assert law_axis["complete"] is False
    assert law_axis["why"] == expected_why
    assert law_axis["note"] == expected_why
    items_across = law_axis.partial("items")
    assert items_across[0]["제목"] == "건축법"
    assert "law" in res_across["incomplete"]



