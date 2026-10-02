# -*- coding: utf-8 -*-
"""과업 2건에 대한 단위 시험 (r17).

1. 구법 항목의 "현행아님": True 및 history.successor 기반 "현행_법령명_후보" 표시, 머리 안내.
   - successor 실패 시 후보 없이 안내만, complete 유지.
   - 현행 법령 검색 결과는 변경 없음.
   - 구법 항목 successor 호출은 최대 3개 항목 제한.
2. 공식 약칭이 아닌 통칭(조특법, 국계법, 도정법 등)의 약칭 사전 등록 및 "통칭(공식 약칭 아님)" 표시.
"""
import pytest

import law_kit as kit
from law_kit import laws, mcp_server
from law_kit.client import Result


def test_laws_find_common_name_marked(monkeypatch):
    """공식 약칭이 아닌 통칭(조특법)으로 검색하면 '통칭(공식 약칭 아님)'으로 찾는다."""
    def fake_call(target, **kwargs):
        query = kwargs.get("query", "")
        if target == "law" and query == "조특법":
            return Result("law", ok=True, complete=True, total=0, items=[])
        if target == "law" and query == "조세특례제한법":
            return Result("law", ok=True, complete=True, total=1, items=[{
                "법령명한글": "조세특례제한법",
                "법령약칭명": "",
                "법령ID": "001584",
                "법령일련번호": "284389",
                "법령구분명": "법률",
                "소관부처명": "기획재정부",
                "시행일자": "20260918",
                "공포일자": "20260318",
                "현행연혁코드": "현행",
            }])
        return Result(target, ok=True, complete=True, total=0, items=[])

    monkeypatch.setattr(kit.client, "call", fake_call)
    monkeypatch.setattr(kit.client, "call_all", lambda *a, **k: Result("lsAbrv", ok=True, complete=True, items=[]))

    hits = laws.find("조특법")
    assert len(hits) == 1
    assert hits[0]["법령명"] == "조세특례제한법"
    assert hits[0]["찾은방법"] == "통칭(공식 약칭 아님)"
    assert hits[0]["현행"] == "현행"


def test_mcp_law_find_historic_marking_and_successor(monkeypatch):
    """구법 항목이면 '현행아님': True, successor 후보, 머리 안내를 단다."""
    fake_items = [{
        "법령명": "도시공원법",
        "약칭": "",
        "ID": "000547",
        "MST": "67355",
        "법령구분": "법률",
        "소관부처": "건설교통부",
        "시행일자": "20050331",
        "공포일자": "20050331",
        "현행": "연혁",
        "찾은방법": "historic",
    }]
    monkeypatch.setattr(kit.laws, "find", lambda name, **k: [dict(fake_items[0])])

    fake_succ = {
        "name": "도시공원법",
        "status": "구법",
        "complete": True,
        "candidates": [{"직접_후보": "도시공원 및 녹지 등에 관한 법률", "근거": "같은 법령ID 제명변경"}],
    }
    monkeypatch.setattr(kit.history, "successor", lambda name: fake_succ)

    res = mcp_server.dispatch_tool("law_find", {"name": "도시공원법"})
    assert res["complete"] is True
    assert res.get("안내") == "옛 법령명이다. 현행 법령은 현행_법령명_후보 를 확인하라"
    items = res.get("items", [])
    assert len(items) == 1
    assert items[0]["법령명"] == "도시공원법"
    assert items[0]["현행아님"] is True
    assert items[0]["현행_법령명_후보"] == [{"직접_후보": "도시공원 및 녹지 등에 관한 법률", "근거": "같은 법령ID 제명변경"}]


def test_mcp_law_find_historic_without_successor(monkeypatch):
    """successor 후보를 못 찾으면 후보 없이 머리 안내만 달고 complete는 그대로 유지한다."""
    fake_items = [{
        "법령명": "가상의옛법",
        "약칭": "",
        "ID": "099999",
        "MST": "99999",
        "법령구분": "법률",
        "소관부처": "어딘가",
        "시행일자": "19900101",
        "공포일자": "19900101",
        "현행": "연혁",
        "찾은방법": "historic",
    }]
    monkeypatch.setattr(kit.laws, "find", lambda name, **k: [dict(fake_items[0])])

    # 후보 없는 응답 (complete: false 여도 law_find complete는 유지)
    fake_succ = {
        "name": "가상의옛법",
        "status": "구법",
        "complete": False,
        "why": "승계 근거를 찾지 못했다",
        "candidates": [],
    }
    monkeypatch.setattr(kit.history, "successor", lambda name: fake_succ)

    res = mcp_server.dispatch_tool("law_find", {"name": "가상의옛법"})
    assert res["complete"] is True
    assert res.get("안내") == "옛 법령명이다. 현행 법령은 현행_법령명_후보 를 확인하라"
    items = res.get("items", [])
    assert len(items) == 1
    assert items[0]["현행아님"] is True
    assert "현행_법령명_후보" not in items[0]


def test_mcp_law_find_current_law_not_changed(monkeypatch):
    """현행 법령 검색 결과는 변경하지 않는다 (현행아님·후보·머리안내 미부착)."""
    fake_items = [{
        "법령명": "건축법",
        "약칭": "",
        "ID": "001600",
        "MST": "260000",
        "법령구분": "법률",
        "소관부처": "국토교통부",
        "시행일자": "20260101",
        "공포일자": "20251201",
        "현행": "현행",
        "찾은방법": "exact",
    }]
    monkeypatch.setattr(kit.laws, "find", lambda name, **k: [dict(fake_items[0])])

    res = mcp_server.dispatch_tool("law_find", {"name": "건축법"})
    assert res["complete"] is True
    assert "안내" not in res
    items = res.get("items", [])
    assert len(items) == 1
    assert "현행아님" not in items[0]
    assert "현행_법령명_후보" not in items[0]


def test_mcp_law_find_historic_max_three_calls(monkeypatch):
    """구법 항목이 여러 개여도 successor 호출은 최대 3개 항목에만 한다."""
    fake_items = [{
        "법령명": f"옛법_{i}",
        "약칭": "",
        "ID": f"00000{i}",
        "MST": f"1000{i}",
        "법령구분": "법률",
        "소관부처": "부처",
        "시행일자": "19900101",
        "공포일자": "19900101",
        "현행": "연혁",
        "찾은방법": "historic",
    } for i in range(5)]

    monkeypatch.setattr(kit.laws, "find", lambda name, **k: [dict(it) for it in fake_items])

    call_count = []
    def fake_successor(name):
        call_count.append(name)
        return {"name": name, "complete": True, "candidates": [{"직접_후보": f"현행법_{name}", "근거": "개정"}]}

    monkeypatch.setattr(kit.history, "successor", fake_successor)

    res = mcp_server.dispatch_tool("law_find", {"name": "옛법들"})
    assert len(call_count) == 3
    items = res.get("items", [])
    assert len(items) == 5
    # 모든 구법 항목은 현행아님: True
    for it in items:
        assert it["현행아님"] is True
    # 앞의 3개 항목만 후보가 붙고, 뒤의 2개는 후보 없음
    for it in items[:3]:
        assert "현행_법령명_후보" in it
    for it in items[3:]:
        assert "현행_법령명_후보" not in it
