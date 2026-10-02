# -*- coding: utf-8 -*-
"""결함 2건에 대한 감사 재현 및 검증 시험 (r15).

1. articles.get_article: JO 요청에 조문이 없을 때 전체 본문 재조회가 실패·빈 응답·구조 미인식이면
   폐지 판으로 오분류하지 않고 complete:false + why "그 판 전체 본문을 받지 못해 조문이 없는지 확인하지 못했다".
   재조회 성공 0조문이면 폐지 판으로 인정하여 앞 판 탐색.
2. laws.find 연혁(eflaw) 쪽 넘김: 5쪽까지 넘겨도 끝(total 또는 마지막 쪽)을 못 봤는데 못 찾았으면
   complete:false + why "연혁 목록 N쪽까지 봤지만 끝을 보지 못했다".
   끝까지 보고 못 찾았을 때만 "없음".
   get_article·get_annex_text·law_find 가 이것을 거짓 "없음" 으로 바꾸지 않게 하고 예외 크래시 방지.
"""
import json
import pytest
from law_kit import client, laws, articles, history, mcp_server
from law_kit.client import Result, Incomplete
from law_kit.shape import text


class BufferWriter(object):
    def __init__(self):
        self.chunks = []

    def write(self, b):
        self.chunks.append(b)

    def flush(self):
        pass

    def get_json_lines(self):
        combined = b"".join(self.chunks).decode("utf-8")
        lines = [line.strip() for line in combined.split("\n") if line.strip()]
        return [json.loads(line) for line in lines]


def _call_mcp(name, arguments):
    writer = BufferWriter()
    mcp_server.handle_message({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments}
    }, writer)
    lines = writer.get_json_lines()
    assert len(lines) == 1
    resp = lines[0]
    assert resp.get("id") == 1
    result = resp.get("result", {})
    assert result.get("isError") is False
    content_text = result["content"][0]["text"]
    return json.loads(content_text)


# =========================================================================
# 결함 1 시험
# =========================================================================

def test_defect_1_refetch_failure_incomplete(monkeypatch):
    """(1) JO 응답에 조문 없음 -> 전체 본문 재조회 실패(ok=False) -> complete:false + why"""
    def fake_call(target, service=False, **params):
        if target == "law" and "JO" in params:
            return Result("law", ok=True, items=[{
                "법령": {
                    "기본정보": {"법령명_한글": "테스트법", "시행일자": "20200101", "제개정구분": "일부개정"},
                    "조문": {}
                }
            }])
        if target == "law" and "JO" not in params:
            return Result("law", ok=False, error="Connection timeout")
        return Result(target, ok=True, items=[])

    monkeypatch.setattr(client, "call", fake_call)
    res = articles.get_article("12345", "1")
    assert res["complete"] is False
    assert res["why"] == "그 판 전체 본문을 받지 못해 조문이 없는지 확인하지 못했다"


def test_defect_1_refetch_empty_incomplete(monkeypatch):
    """(1) JO 응답에 조문 없음 -> 전체 본문 재조회 빈 응답(partial=[]) -> complete:false + why"""
    def fake_call(target, service=False, **params):
        if target == "law" and "JO" in params:
            return Result("law", ok=True, items=[{
                "법령": {
                    "기본정보": {"법령명_한글": "테스트법", "시행일자": "20200101", "제개정구분": "일부개정"},
                    "조문": {}
                }
            }])
        if target == "law" and "JO" not in params:
            return Result("law", ok=True, items=[])
        return Result(target, ok=True, items=[])

    monkeypatch.setattr(client, "call", fake_call)
    res = articles.get_article("12345", "1")
    assert res["complete"] is False
    assert res["why"] == "그 판 전체 본문을 받지 못해 조문이 없는지 확인하지 못했다"


def test_defect_1_refetch_unrecognized_incomplete(monkeypatch):
    """(1) JO 응답에 조문 없음 -> 전체 본문 재조회 알아볼 수 없는 응답 -> complete:false + why"""
    def fake_call(target, service=False, **params):
        if target == "law" and "JO" in params:
            return Result("law", ok=True, items=[{
                "법령": {
                    "기본정보": {"법령명_한글": "테스트법", "시행일자": "20200101", "제개정구분": "일부개정"},
                    "조문": {}
                }
            }])
        if target == "law" and "JO" not in params:
            return Result("law", ok=True, items=["잘못된형식의응답"])
        return Result(target, ok=True, items=[])

    monkeypatch.setattr(client, "call", fake_call)
    res = articles.get_article("12345", "1")
    assert res["complete"] is False
    assert res["why"] == "그 판 전체 본문을 받지 못해 조문이 없는지 확인하지 못했다"


def test_defect_1_refetch_success_zero_units_searches_prev_version(monkeypatch):
    """(1) JO 응답에 조문 없음 -> 전체 본문 재조회 성공했으나 조문 0개 -> 폐지 판으로 인정하고 앞 판 탐색"""
    def fake_call(target, service=False, **params):
        mst = params.get("MST")
        if target == "law" and "JO" in params:
            # 12345는 현재판(폐지 판)
            return Result("law", ok=True, items=[{
                "법령": {
                    "기본정보": {"법령명_한글": "폐지된법", "시행일자": "20200101", "제개정구분": "일부개정"},
                    "조문": {}
                }
            }])
        if target == "law" and "JO" not in params:
            # 전체 본문 재조회 성공, 그러나 조문 0개
            return Result("law", ok=True, items=[{
                "법령": {
                    "기본정보": {"법령명_한글": "폐지된법", "시행일자": "20200101", "제개정구분": "일부개정"},
                    "조문": {}
                }
            }])
        if target == "eflaw" and str(mst) == "12340":
            # 앞 판 (MST 12340) 조회
            return Result("eflaw", ok=True, items=[{
                "법령": {
                    "기본정보": {"법령명_한글": "폐지된법", "시행일자": "20100101", "제개정구분": "일부개정"},
                    "조문": {
                        "조문단위": [{
                            "조문여부": "조문",
                            "조문번호": "1",
                            "조문가지번호": "0",
                            "조문제목": "목적",
                            "조문내용": "이 법은 목적이다.",
                            "항": []
                        }]
                    }
                }
            }])
        return Result(target, ok=True, items=[])

    monkeypatch.setattr(client, "call", fake_call)
    monkeypatch.setattr(history, "versions", lambda name, **k: {
        "ok": True, "complete": True,
        "versions": [
            {"법령명": "폐지된법", "MST": "12345", "시행일자": "20200101", "제개정": "일부개정"},
            {"법령명": "폐지된법", "MST": "12340", "시행일자": "20100101", "제개정": "일부개정"},
        ]
    })

    res = articles.get_article("12345", "1")
    assert res["complete"] is True
    assert res["조번호"] == "제1조"
    assert res["조문내용"] == "이 법은 목적이다."
    assert res.get("구법") is True


# =========================================================================
# 결함 2 시험
# =========================================================================

def test_defect_2_1201_items_5_pages_not_ended_incomplete(monkeypatch):
    """(2) 연혁 법령 1201건 중 5쪽(1000건)만 본 경우 -> complete:false + why '연혁 목록 5쪽까지 봤지만 끝을 보지 못했다'"""
    def fake_call(target, query=None, search=1, display=200, page=1, **params):
        if target == "law":
            return Result("law", ok=True, complete=True, items=[], total=0)
        if target == "eflaw":
            items = [{"법령명한글": "다른법%d_%d" % (page, i), "법령ID": "%d%d" % (page, i), "법령일련번호": "%d%d" % (page, i)}
                     for i in range(200)]
            return Result("eflaw", ok=True, complete=True, items=items, total=1201)
        return Result(target, ok=True, items=[])

    monkeypatch.setattr(client, "call", fake_call)
    monkeypatch.setattr(client, "call_all", lambda *a, **k: Result("lsAbrv", ok=True, complete=True, items=[], total=0))

    # 1. laws.find
    with pytest.raises(client.Incomplete) as excinfo:
        laws.find("끝없는구법")
    assert "연혁 목록 5쪽까지 봤지만 끝을 보지 못했다" in str(excinfo.value)

    # 2. articles.get_article
    res_art = articles.get_article("끝없는구법", "1")
    assert res_art["complete"] is False
    assert res_art["why"] == "연혁 목록 5쪽까지 봤지만 끝을 보지 못했다"

    # 3. articles.get_annex_text
    res_annex = articles.get_annex_text("끝없는구법", "1")
    assert res_annex["complete"] is False
    assert res_annex["why"] == "연혁 목록 5쪽까지 봤지만 끝을 보지 못했다"

    # 4. mcp law_find
    mcp_res = _call_mcp("law_find", {"name": "끝없는구법"})
    assert mcp_res["complete"] is False
    assert mcp_res["why"] == "연혁 목록 5쪽까지 봤지만 끝을 보지 못했다"


def test_defect_2_saw_end_and_missing_is_genuinely_missing(monkeypatch):
    """(2) 연혁 법령 끝까지 보고(total=300, 2쪽에서 끝) 못 찾았을 때 -> 정상적인 '없음' 처리"""
    def fake_call(target, query=None, search=1, display=200, page=1, **params):
        if target == "law":
            return Result("law", ok=True, complete=True, items=[], total=0)
        if target == "eflaw":
            if page == 1:
                items = [{"법령명한글": "다른법1_%d" % i, "법령ID": "1%d" % i, "법령일련번호": "1%d" % i} for i in range(200)]
                return Result("eflaw", ok=True, complete=True, items=items, total=300)
            elif page == 2:
                items = [{"법령명한글": "다른법2_%d" % i, "법령ID": "2%d" % i, "법령일련번호": "2%d" % i} for i in range(100)]
                return Result("eflaw", ok=True, complete=True, items=items, total=300)
            return Result("eflaw", ok=True, complete=True, items=[], total=300)
        return Result(target, ok=True, items=[])

    monkeypatch.setattr(client, "call", fake_call)
    monkeypatch.setattr(client, "call_all", lambda *a, **k: Result("lsAbrv", ok=True, complete=True, items=[], total=0))

    # 1. laws.find -> 빈 목록 []
    hits = laws.find("진짜없는구법")
    assert hits == []

    # 2. articles.get_article -> 법령을 찾을 수 없습니다
    res_art = articles.get_article("진짜없는구법", "1")
    assert res_art["complete"] is False
    assert "찾을 수 없습니다" in res_art["why"]

    # 3. articles.get_annex_text -> 법령을 찾을 수 없습니다
    res_annex = articles.get_annex_text("진짜없는구법", "1")
    assert res_annex["complete"] is False
    assert "찾을 수 없습니다" in res_annex["why"]

    # 4. mcp law_find -> complete: True, items: []
    mcp_res = _call_mcp("law_find", {"name": "진짜없는구법"})
    assert mcp_res["complete"] is True
    assert mcp_res["items"] == []
    assert "안내" in mcp_res
