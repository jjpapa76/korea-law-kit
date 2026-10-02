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

    def fake_across(query, display=20):
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
        lambda query, display=20: {
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
    items = prec_axis.partial("items")
    assert items[0]["raw"]["사건번호"] == "2012다89399"

    # 2. 재검색 후에도 0건인 케이스 -> 안내 문구 확인
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
    assert "0건 - 낱말을 줄여 다시 물어라" in prec_zero.get("안내", "")
    assert "모든 낱말이 들어간 결과가 없다는 뜻이다. 없다고 단정하지 마라" in prec_zero.get("안내", "")


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

