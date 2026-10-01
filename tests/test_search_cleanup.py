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
