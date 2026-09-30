# -*- coding: utf-8 -*-
"""law_kit.mcp_server 의 JSON-RPC 2.0 stdio MCP 서버를 시험한다.

망에 나가지 않는다(기존 시험과 동일하게 monkeypatch).
테스트 항목:
    1. initialize 응답 (protocolVersion, capabilities, serverInfo, instructions)
    2. tools/list 가 9개 도구를 모두 제공하는지
    3. 불완전 결과가 complete: false, why, 지시, partial 로 직렬화되는지
    4. Incomplete 예외가 발생했을 때 complete: false, why, 지시 로 변환되는지
    5. 도구 예외가 isError: true 로 나가고 서버 루프가 계속 도는지
    6. 알림(id 없는 메시지)에 대해 아무 응답도 보내지 않는지
    7. 데모 키(test) 환경에서 경고 문구가 도구 응답에 포함되는지
    8. 60,000자 초과 결과가 잘리고 complete: false 와 why 가 붙는지
    9. subprocess 로 실제 `python -m law_kit.mcp_server` 를 띄워
       한글 포함 UTF-8 왕복 통신이 정상 동작하는지
"""
import io
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

import law_kit as kit
from law_kit import mcp_server
from law_kit.client import Incomplete, Result
from law_kit.shape import Answer


class BufferWriter(object):
    """sys.stdout.buffer 처럼 동작하는 메모리 버퍼."""

    def __init__(self):
        self._buf = io.BytesIO()

    def write(self, data):
        return self._buf.write(data)

    def flush(self):
        pass

    def clear(self):
        self._buf = io.BytesIO()

    def get_json_lines(self):
        self._buf.seek(0)
        raw = self._buf.read().decode("utf-8")
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        return [json.loads(line) for line in lines]


# ---------------------------------------------------------------- 1. initialize
def test_initialize_response():
    """initialize 요청에 올바른 서버 정보와 프로토콜 버전을 돌려준다."""
    writer = BufferWriter()
    req = {
        "jsonrpc": "2.0",
        "id": "req-1",
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0.1"}
        }
    }
    mcp_server.handle_message(req, writer)
    responses = writer.get_json_lines()
    assert len(responses) == 1
    res = responses[0]
    assert res["id"] == "req-1"
    assert res["result"]["protocolVersion"] == "2024-11-05"
    assert "tools" in res["result"]["capabilities"]
    assert res["result"]["serverInfo"]["name"] == "korea-law"
    assert res["result"]["serverInfo"]["version"] == "1.0.0"
    assert "불완전" in res["result"]["instructions"]

    # 프로토콜 버전이 생략된 경우 기본값(2025-06-18)을 쓰는지 확인
    writer_default = BufferWriter()
    mcp_server.handle_message({"jsonrpc": "2.0", "id": 2, "method": "initialize"},
                              writer_default)
    res_default = writer_default.get_json_lines()[0]
    assert res_default["result"]["protocolVersion"] == "2025-06-18"


# ---------------------------------------------------------------- 2. tools/list 9개
def test_tools_list_has_9_tools():
    """도구 9종이 누락 없이 등록되어 있고 적절한 설명을 갖추었는지 확인한다."""
    writer = BufferWriter()
    req = {"jsonrpc": "2.0", "id": 10, "method": "tools/list"}
    mcp_server.handle_message(req, writer)
    responses = writer.get_json_lines()
    assert len(responses) == 1
    tools = responses[0]["result"]["tools"]
    assert len(tools) == 9

    expected_names = {
        "law_brief", "law_find", "law_term", "law_tree", "law_annex",
        "law_history", "law_search", "law_api", "law_call"
    }
    actual_names = {t["name"] for t in tools}
    assert actual_names == expected_names

    # law_call 설명에 law_api 안내가 들어 있는지 확인
    law_call_tool = next(t for t in tools if t["name"] == "law_call")
    assert "target 을 모르면 law_api 로 먼저 찾아라" in law_call_tool["description"]

    # law_annex 설명에 내려받기 없음이 명시되었는지 확인
    law_annex_tool = next(t for t in tools if t["name"] == "law_annex")
    assert "내려받기는 없음" in law_annex_tool["description"]


# ---------------------------------------------------------------- 3. 불완전 결과
def test_incomplete_result_serialization(monkeypatch):
    """결과가 불완전하면 complete: false, why, 지시, partial 이 반드시 포함된다."""
    writer = BufferWriter()

    # (1) Result 객체가 불완전한 경우
    cut_result = Result("law", ok=False, error="조회시간 초과")
    monkeypatch.setattr(kit.client, "call", lambda *a, **k: cut_result)

    req = {
        "jsonrpc": "2.0",
        "id": 101,
        "method": "tools/call",
        "params": {"name": "law_call", "arguments": {"target": "law"}}
    }
    mcp_server.handle_message(req, writer)
    responses = writer.get_json_lines()
    assert len(responses) == 1
    content_text = responses[0]["result"]["content"][0]["text"]
    parsed = json.loads(content_text)
    assert parsed["complete"] is False
    assert "조회시간 초과" in parsed["why"]
    assert "이 결과로 '없다'·'전부다'라고 답하지 마라" in parsed["지시"]
    assert "partial" in parsed

    # (2) Answer 객체가 불완전한 경우
    writer2 = BufferWriter()
    fake_answer = Answer({
        "complete": False,
        "note": "상한 절단 발생",
        "items": [{"조": "제1조"}],
        "laws": ["가"]
    })
    monkeypatch.setattr(kit.terms, "articles", lambda *a, **k: fake_answer)
    req2 = {
        "jsonrpc": "2.0",
        "id": 102,
        "method": "tools/call",
        "params": {"name": "law_term", "arguments": {"term": "도시혁신"}}
    }
    mcp_server.handle_message(req2, writer2)
    parsed2 = json.loads(writer2.get_json_lines()[0]["result"]["content"][0]["text"])
    assert parsed2["complete"] is False
    assert "상한 절단" in parsed2["why"]
    assert "이 결과로 '없다'·'전부다'라고 답하지 마라" in parsed2["지시"]
    assert parsed2["partial"]["items"] == [{"조": "제1조"}]

    # (3) brief 의 gaps 가 존재하는 경우
    writer3 = BufferWriter()
    monkeypatch.setattr(kit, "format_brief", lambda r: "요약")
    monkeypatch.setattr(kit, "brief", lambda *a, **k: {
        "topic": "폐기물",
        "gaps": ["시행령 미확인", "별표 누락"],
        "terms": Answer({"complete": True}),
        "laws": []
    })
    req3 = {
        "jsonrpc": "2.0",
        "id": 103,
        "method": "tools/call",
        "params": {"name": "law_brief", "arguments": {"topic": "폐기물"}}
    }
    mcp_server.handle_message(req3, writer3)
    parsed3 = json.loads(writer3.get_json_lines()[0]["result"]["content"][0]["text"])
    assert parsed3["complete"] is False
    assert "시행령 미확인" in parsed3["why"]
    assert "이 결과로 '없다'·'전부다'라고 답하지 마라" in parsed3["지시"]


def _call(name, arguments):
    writer = BufferWriter()
    mcp_server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": name, "arguments": arguments}}, writer)
    return json.loads(writer.get_json_lines()[0]["result"]["content"][0]["text"])


def test_nested_incomplete_is_not_hidden_by_a_complete_outside(monkeypatch):
    """겉이 확정이어도 안쪽이 미확인이면 전체가 불완전이다.

    현행 여부는 확정(True)인데 승계법 조회가 미확인인 경우, 겉만 보면
    complete: true 로 나간다. 안쪽 Result 도 같다 - 잘린 목록을 펴서
    넣으면 겉에서는 잘렸다는 사실이 사라진다.
    """
    monkeypatch.setattr(kit.history, "is_current", lambda n: (True, "현행"))
    monkeypatch.setattr(kit.history, "successor", lambda n: {
        "name": n, "status": "미확인", "why": "연혁 조회 실패", "candidates": []})
    parsed = _call("law_history", {"name": "도시계획법"})
    assert parsed["complete"] is False
    assert "연혁 조회 실패" in parsed["why"]

    cut = Result("prec", ok=False, error="3페이지에서 끊김")
    monkeypatch.setattr(kit, "format_brief", lambda r: "요약")
    monkeypatch.setattr(kit, "brief", lambda *a, **k: {
        "topic": "폐기물", "gaps": [], "axes": {"prec": cut}})
    parsed = _call("law_brief", {"topic": "폐기물"})
    assert parsed["complete"] is False
    assert "3페이지에서 끊김" in parsed["why"]


def test_unknown_currency_is_incomplete(monkeypatch):
    monkeypatch.setattr(kit.history, "is_current", lambda n: (None, "미확인: 연혁 조회 실패"))
    monkeypatch.setattr(kit.history, "successor", lambda n: {
        "name": n, "status": "현행", "why": "", "candidates": []})
    assert _call("law_history", {"name": "주차장법"})["complete"] is False


def test_find_that_fills_the_limit_is_not_complete(monkeypatch):
    monkeypatch.setattr(kit.laws, "find", lambda name, limit=20, include_historic=True:
                        [{"법령명": "가%d" % i} for i in range(limit)])
    assert _call("law_find", {"name": "가", "limit": 3})["complete"] is False
    monkeypatch.setattr(kit.laws, "find", lambda name, limit=20, include_historic=True:
                        [{"법령명": "가"}])
    assert _call("law_find", {"name": "가", "limit": 3})["complete"] is True


# ---------------------------------------------------------------- 4. Incomplete 예외
def test_incomplete_exception_handling(monkeypatch):
    """Incomplete 예외가 발생해도 complete: false 와 지시를 담아 정상 처리된다."""
    writer = BufferWriter()

    def raise_incomplete(*a, **k):
        raise Incomplete("미확인 항목이 있어 열 수 없습니다")

    monkeypatch.setattr(kit.laws, "find", raise_incomplete)
    req = {
        "jsonrpc": "2.0",
        "id": 201,
        "method": "tools/call",
        "params": {"name": "law_find", "arguments": {"name": "주차장법"}}
    }
    mcp_server.handle_message(req, writer)
    responses = writer.get_json_lines()
    assert len(responses) == 1
    assert responses[0]["result"]["isError"] is False
    parsed = json.loads(responses[0]["result"]["content"][0]["text"])
    assert parsed["complete"] is False
    assert "미확인 항목이 있어 열 수 없습니다" in parsed["why"]
    assert "이 결과로 '없다'·'전부다'라고 답하지 마라" in parsed["지시"]


# ---------------------------------------------------------------- 5. 도구 예외 & 서버 유지
def test_tool_error_is_error_true_and_server_continues(monkeypatch):
    """네트워크 단절 등 도구 예외는 isError: true 로 반환되며 서버는 죽지 않는다."""
    writer = BufferWriter()

    def raise_conn_error(*a, **k):
        raise ConnectionResetError("법제처 서버 응답 없음")

    monkeypatch.setattr(kit.laws, "find", raise_conn_error)
    req_err = {
        "jsonrpc": "2.0",
        "id": 301,
        "method": "tools/call",
        "params": {"name": "law_find", "arguments": {"name": "건축법"}}
    }
    mcp_server.handle_message(req_err, writer)
    responses = writer.get_json_lines()
    assert len(responses) == 1
    res = responses[0]
    assert res["result"]["isError"] is True
    assert "ConnectionResetError" in res["result"]["content"][0]["text"]

    # 서버가 죽지 않고 다음 요청(ping)을 정상 처리하는지 확인
    writer.clear()
    req_ping = {"jsonrpc": "2.0", "id": 302, "method": "ping"}
    mcp_server.handle_message(req_ping, writer)
    ping_resp = writer.get_json_lines()
    assert len(ping_resp) == 1
    assert ping_resp[0]["id"] == 302
    assert ping_resp[0]["result"] == {}


# ---------------------------------------------------------------- 6. 알림(id 없음) 무응답
def test_notification_no_response():
    """id 가 없는 알림 메시지에는 어떤 응답도 출력하지 않는다."""
    writer = BufferWriter()
    mcp_server.handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"},
                              writer)
    mcp_server.handle_message({"jsonrpc": "2.0", "method": "ping"}, writer)
    mcp_server.handle_message({"jsonrpc": "2.0", "method": "unknown_notice"}, writer)
    assert writer.get_json_lines() == []


# ---------------------------------------------------------------- 7. 데모 키 경고
def test_demo_key_warning(monkeypatch):
    """데모 키로 구동 중일 때 모든 도구 응답에 경고 문구가 추가된다."""
    monkeypatch.setattr(kit.client, "is_demo_key", lambda: True)
    monkeypatch.setattr(kit.terms, "articles",
                        lambda *a, **k: Answer({"complete": True, "items": []}))

    writer = BufferWriter()
    req = {
        "jsonrpc": "2.0",
        "id": 401,
        "method": "tools/call",
        "params": {"name": "law_term", "arguments": {"term": "건축선"}}
    }
    mcp_server.handle_message(req, writer)
    parsed = json.loads(writer.get_json_lines()[0]["result"]["content"][0]["text"])
    assert "경고" in parsed
    assert "인증키 없이 데모 키(test)로 불렀다" in parsed["경고"]

    # 정식 키 환경에서는 경고가 없어야 함
    monkeypatch.setattr(kit.client, "is_demo_key", lambda: False)
    writer2 = BufferWriter()
    mcp_server.handle_message(req, writer2)
    parsed2 = json.loads(writer2.get_json_lines()[0]["result"]["content"][0]["text"])
    assert "경고" not in parsed2


# ---------------------------------------------------------------- 8. 60,000자 절단
def test_response_truncation_at_60000(monkeypatch):
    """응답이 60,000자를 넘으면 절단하고 complete: false 와 사유를 남긴다."""
    # 70,000자 분량의 거대한 결과 시뮬레이션
    huge_list = [{"내용": "가" * 1000} for _ in range(70)]
    monkeypatch.setattr(kit.terms, "articles",
                        lambda *a, **k: Answer({"complete": True, "items": huge_list}))

    writer = BufferWriter()
    req = {
        "jsonrpc": "2.0",
        "id": 501,
        "method": "tools/call",
        "params": {"name": "law_term", "arguments": {"term": "테스트"}}
    }
    mcp_server.handle_message(req, writer)
    raw_text = writer.get_json_lines()[0]["result"]["content"][0]["text"]
    assert len(raw_text) <= 60000
    parsed = json.loads(raw_text)
    assert parsed["complete"] is False
    assert "60,000자를 초과하여" in parsed["why"]
    assert "이 결과로 '없다'·'전부다'라고 답하지 마라" in parsed["지시"]


# ---------------------------------------------------------------- 9. subprocess 왕복 통신
def test_subprocess_mcp_server_roundtrip():
    """subprocess 로 mcp_server 프로세스를 띄워 initialize 와 tools/list 한글 통신을 검증한다."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"

    proc = subprocess.Popen(
        [sys.executable, "-m", "law_kit.mcp_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=ROOT,
        env=env
    )

    try:
        # 1. initialize 요청
        init_req = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"}
        }
        init_line = (json.dumps(init_req, ensure_ascii=False) + "\n").encode("utf-8")
        proc.stdin.write(init_line)
        proc.stdin.flush()

        resp_line = proc.stdout.readline().decode("utf-8")
        init_resp = json.loads(resp_line)
        assert init_resp["id"] == 1
        assert init_resp["result"]["serverInfo"]["name"] == "korea-law"
        assert "국가법령정보" in init_resp["result"]["instructions"]

        # 2. tools/list 요청
        tools_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        tools_line = (json.dumps(tools_req, ensure_ascii=False) + "\n").encode("utf-8")
        proc.stdin.write(tools_line)
        proc.stdin.flush()

        tools_resp_line = proc.stdout.readline().decode("utf-8")
        tools_resp = json.loads(tools_resp_line)
        assert tools_resp["id"] == 2
        tools = tools_resp["result"]["tools"]
        assert len(tools) == 9
        # 한글 설명이 정상적으로 전송되었는지 검증
        brief_tool = next(t for t in tools if t["name"] == "law_brief")
        assert "주제 하나를" in brief_tool["description"]
    finally:
        proc.stdin.close()
        proc.terminate()
        proc.wait(timeout=5)


def test_truncation_stays_under_the_cap_even_with_escapes():
    """잘라 낸 원문을 다시 JSON 에 넣으면 이스케이프로 불어난다 - 그래도 상한 안이어야 한다."""
    for data in ({"items": ['"' * 30000]}, {"items": [{"a": "b\c"}] * 9000}):
        text = mcp_server.serialize_response(data)
        parsed = json.loads(text)
        assert len(text) <= mcp_server.MAX_TEXT_LENGTH
        assert parsed["complete"] is False
        assert len(parsed["partial"]) > 20000      # 알맹이가 남아 있다


def test_missing_required_argument_is_an_error_not_a_query(monkeypatch):
    """빈 인자가 None 으로 법제처에 나가면 엉뚱한 결과가 '답' 이 된다."""
    monkeypatch.setattr(kit.terms, "articles",
                        lambda *a, **k: pytest.fail("비어 있는 인자로 불렀다"))
    writer = BufferWriter()
    mcp_server.handle_message({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                               "params": {"name": "law_term", "arguments": {}}}, writer)
    result = writer.get_json_lines()[0]["result"]
    assert result["isError"] is True
    assert "term" in result["content"][0]["text"]


def test_falsy_complete_flag_is_still_incomplete():
    parsed = json.loads(mcp_server.serialize_response(
        {"complete": True, "child": {"complete": 0, "items": []}}))
    assert parsed["complete"] is False


def test_alias_arguments_pass_the_required_check(monkeypatch):
    monkeypatch.setattr(kit.tree, "delegated", lambda law: Answer({"complete": True, "rows": []}))
    monkeypatch.setattr(kit.tree, "summary", lambda r: [])
    monkeypatch.setattr(kit.tree, "subordinate_laws", lambda r: [])
    assert _call("law_tree", {"law_name": "주차장법"})["law"] == "주차장법"


def test_tree_summary_still_reports_nested_incomplete(monkeypatch):
    monkeypatch.setattr(kit.tree, "delegated", lambda law: Answer({
        "complete": True, "rows": [{"complete": False, "why": "3페이지에서 끊김"}]}))
    monkeypatch.setattr(kit.tree, "summary", lambda r: [])
    monkeypatch.setattr(kit.tree, "subordinate_laws", lambda r: [])
    parsed = _call("law_tree", {"law": "주차장법"})
    assert parsed["complete"] is False and "3페이지에서 끊김" in parsed["why"]


def test_huge_reason_cannot_push_the_reply_over_the_cap():
    text = mcp_server.serialize_response({"complete": False, "why": "가" * 100000, "items": []})
    assert len(text) <= mcp_server.MAX_TEXT_LENGTH


def test_law_term_summary_stays_under_60000_with_huge_result(monkeypatch):
    """원래 14만 자가 넘는 대형 결과도 요약 모드(기본)에서는 본문을 빼서 60,000자 상한 안에 든다."""
    # 100개 조문, 각 조문내용 1,400자 -> 통째로는 15만 자 이상
    fake_articles = []
    for i in range(100):
        fake_articles.append({
            "법령명": "국토의 계획 및 이용에 관한 법률",
            "조": "제%d조" % (i + 1),
            "조번호": "%04d" % (i + 1),
            "조가지번호": "00",
            "조문내용": "가" * 1400,
            "용어구분": "법령",
            "출현횟수": 2,
        })
    fake = Answer({
        "term": "건폐율",
        "found": True,
        "complete": True,
        "terms": [{"용어": "건폐율", "조문수": 100}],
        "articles": fake_articles,
        "laws": ["국토의 계획 및 이용에 관한 법률"],
    })
    monkeypatch.setattr(kit.terms, "articles", lambda *a, **k: fake)

    writer = BufferWriter()
    mcp_server.handle_message({
        "jsonrpc": "2.0",
        "id": 901,
        "method": "tools/call",
        "params": {"name": "law_term", "arguments": {"term": "건폐율"}}
    }, writer)

    raw_text = writer.get_json_lines()[0]["result"]["content"][0]["text"]
    assert len(raw_text) <= mcp_server.MAX_TEXT_LENGTH
    parsed = json.loads(raw_text)
    assert parsed["complete"] is True
    # 본문(조문내용)이 빠져 있는지 확인
    assert len(parsed["articles"]) == 100
    assert "조문내용" not in parsed["articles"][0]
    # 법령별 건수가 제공되는지 확인
    assert parsed["counts"]["국토의 계획 및 이용에 관한 법률"] == 100


def test_law_term_filter_by_law(monkeypatch):
    """law 인자를 주면 해당 법령명의 조문만 남긴다."""
    fake_articles = [
        {"법령명": "건축법", "조": "제1조", "출현횟수": 1, "조문내용": "건축법 제1조"},
        {"법령명": "주택법", "조": "제2조", "출현횟수": 1, "조문내용": "주택법 제2조"},
        {"법령명": "건축법시행령", "조": "제3조", "출현횟수": 1, "조문내용": "시행령 제3조"},
    ]
    fake = Answer({
        "term": "건축",
        "found": True,
        "complete": True,
        "terms": [],
        "articles": fake_articles,
        "laws": ["건축법", "주택법", "건축법시행령"],
    })
    monkeypatch.setattr(kit.terms, "articles", lambda *a, **k: fake)

    # 1. '주택'으로 필터링
    parsed = _call("law_term", {"term": "건축", "law": "주택"})
    assert len(parsed["articles"]) == 1
    assert parsed["articles"][0]["법령명"] == "주택법"

    # 2. '건축법'으로 필터링 (건축법, 건축법시행령 포함)
    parsed2 = _call("law_term", {"term": "건축", "law": "건축법"})
    assert len(parsed2["articles"]) == 2
    assert {r["법령명"] for r in parsed2["articles"]} == {"건축법", "건축법시행령"}


def test_law_term_with_text(monkeypatch):
    """with_text: true 면 조문내용(본문)을 포함하고, false(기본)면 제외한다."""
    fake_articles = [
        {"법령명": "건축법", "조": "제1조", "출현횟수": 1, "조문내용": "제1조의 실제 본문 내용"},
    ]
    fake = Answer({
        "term": "건축선",
        "found": True,
        "complete": True,
        "terms": [],
        "articles": fake_articles,
        "laws": ["건축법"],
    })
    monkeypatch.setattr(kit.terms, "articles", lambda *a, **k: fake)

    # 기본: 본문 없음
    parsed_no_text = _call("law_term", {"term": "건축선"})
    assert "조문내용" not in parsed_no_text["articles"][0]

    # with_text: true: 본문 있음
    parsed_with_text = _call("law_term", {"term": "건축선", "with_text": True})
    assert parsed_with_text["articles"][0]["조문내용"] == "제1조의 실제 본문 내용"


def test_law_term_incomplete_propagation(monkeypatch):
    """원래 결과가 불완전하면 요약 모드에서도 complete: false 와 사유가 남는다."""
    fake = Answer({
        "term": "건폐율",
        "found": True,
        "complete": False,
        "note": "2페이지 조회 중 통신 오류 발생",
        "terms": [],
        "articles": [{"법령명": "건축법", "조": "제1조"}],
        "laws": ["건축법"],
    })
    monkeypatch.setattr(kit.terms, "articles", lambda *a, **k: fake)

    parsed = _call("law_term", {"term": "건폐율"})
    assert parsed["complete"] is False
    assert "통신 오류" in parsed["why"]
    assert "이 결과로 '없다'·'전부다'라고 답하지 마라" in parsed["지시"]


# ---------------------------------------------------------------- 10. law_api call 생성
def test_law_api_call_generation(monkeypatch):
    """law_api 응답의 각 항목에 law_call 인자 예시(call)가 올바르게 붙는지 확인한다.

    왜 이 시험이 필요한가:
        law_api 로 찾은 API 를 law_call 로 즉시 호출할 수 있어야 한다.
        엔드포인트가 lawService 면 service: true, lawSearch 면 service: false 여야 하고,
        카탈로그 request_params 중 '필수' 가 든 변수에서 공통 헤더(OC·target·type)를
        제외한 필수 매개변수만 params 에 추출되어야 한다.
    """
    fake_entries = [
        {
            "name": "현행법령 조회 API",
            "endpoint": "http://www.law.go.kr/DRF/lawService.do",
            "target": "eflaw",
            "request_params": [
                {"name": "OC", "value": "string(필수)", "desc": "인증키"},
                {"name": "target", "value": "string : eflaw(필수)", "desc": "서비스 대상"},
                {"name": "type", "value": "char(필수)", "desc": "출력 형태"},
                {"name": "efYd", "value": "string(필수)", "desc": "시행일자 범위"},
                {"name": "query", "value": "string", "desc": "검색어 (선택)"},
            ]
        },
        {
            "name": "판례 검색 API",
            "endpoint": "http://www.law.go.kr/DRF/lawSearch.do",
            "target": "prec",
            "request_params": [
                {"name": "OC", "value": "string(필수)", "desc": "인증키"},
                {"name": "target", "value": "string(필수)", "desc": "서비스 대상"},
                {"name": "type", "value": "char(필수)", "desc": "출력 형태"},
                {"name": "query", "value": "string(필수)", "desc": "판례 질의어"},
                {"name": "display", "value": "int", "desc": "표시 건수 (선택)"},
            ]
        },
        {
            "name": "법령 목록 검색 API",
            "endpoint": "http://www.law.go.kr/DRF/lawSearch.do",
            "target": "law",
            "request_params": [
                {"name": "OC", "value": "string(필수)", "desc": "인증키"},
                {"name": "target", "value": "string(필수)", "desc": "서비스 대상"},
                {"name": "type", "value": "char(필수)", "desc": "출력 형태"},
            ]
        }
    ]

    # 1. target 지정 조회 시 call 객체 검증
    monkeypatch.setattr(kit.catalog, "describe", lambda t: [e for e in fake_entries if e["target"] == t])
    parsed_target = _call("law_api", {"target": "eflaw"})
    assert len(parsed_target["entries"]) == 1
    call1 = parsed_target["entries"][0]["call"]
    assert call1["target"] == "eflaw"
    assert call1["service"] is True           # lawService.do
    assert call1["params"] == {}              # 설명문 대신 빈 params
    assert parsed_target["entries"][0]["required"] == {"efYd": "시행일자 범위"}  # OC, target, type 제외

    # 2. query 키워드 검색 시 call 객체 검증
    monkeypatch.setattr(kit.catalog, "search", lambda q: fake_entries)
    parsed_search = _call("law_api", {"query": "검색"})
    assert len(parsed_search["entries"]) == 3

    # prec: lawSearch -> service: False, 필수: query 만
    call2 = parsed_search["entries"][1]["call"]
    assert call2["target"] == "prec"
    assert call2["service"] is False          # lawSearch.do
    assert call2["params"] == {}              # 설명문 대신 빈 params
    assert parsed_search["entries"][1]["required"] == {"query": "판례 질의어"}

    # law: 필수 파라미터가 OC, target, type 외에 없는 경우 빈 params 와 빈 required
    call3 = parsed_search["entries"][2]["call"]
    assert call3["target"] == "law"
    assert call3["service"] is False
    assert call3["params"] == {}
    assert parsed_search["entries"][2]["required"] == {}


# ---------------------------------------------------------------- 11. law_term 페이징
def test_law_term_pagination_boundaries_and_total(monkeypatch):
    """law_term 에 offset 과 limit 을 적용해 결과를 이어 읽고 경계값을 확인한다.

    왜 이 시험이 필요한가:
        대용량 조문 결과(예: 250건)를 한 번에 주지 않고 200건 단위로 나누어 주며,
        total 과 next_offset 을 통해 이어 읽을 수 있어야 한다.
        잘라서 준 것은 불완전(complete: false)이 아니라 정상 페이징(complete: true)이어야 한다.
    """
    fake_articles = [{"법령명": "건축법", "조": "제%d조" % i} for i in range(1, 251)]
    fake = Answer({
        "term": "건축",
        "found": True,
        "complete": True,
        "terms": [],
        "articles": fake_articles,
        "laws": ["건축법"],
    })
    monkeypatch.setattr(kit.terms, "articles", lambda *a, **k: fake)

    # 1. 기본 호출 (offset=0, limit=200 기본값): 200건 반환, 다음 offset 200 -> 불완전(complete: false)
    res1 = _call("law_term", {"term": "건축"})
    assert res1["complete"] is False
    assert "offset=200" in res1["why"]
    assert "전체 250건 중 1~200 번째만 줬다" in res1["why"]
    assert res1["partial"]["total"] == 250
    assert len(res1["partial"]["articles"]) == 200
    assert res1["partial"]["articles"][0]["조"] == "제1조"
    assert res1["partial"]["articles"][199]["조"] == "제200조"
    assert res1["partial"]["next_offset"] == 200

    # 2. 다음 페이지 호출 (offset=200, limit=200): 나머지 50건 반환, 전체를 다 담지 못했으므로 complete: false
    res2 = _call("law_term", {"term": "건축", "offset": 200, "limit": 200})
    assert res2["complete"] is False
    assert "전체 250건 중 201~250 번째만 줬다" in res2["why"]
    assert "앞 1~200번째" in res2["why"]
    assert res2["partial"]["total"] == 250
    assert len(res2["partial"]["articles"]) == 50
    assert res2["partial"]["articles"][0]["조"] == "제201조"
    assert res2["partial"]["articles"][49]["조"] == "제250조"
    assert res2["partial"]["next_offset"] is None

    # 3. 경계값: limit 이 전체를 넘는 경우 (offset=0, limit=300) -> 한 번에 다 받음 complete: true
    res3 = _call("law_term", {"term": "건축", "offset": 0, "limit": 300})
    assert res3["complete"] is True
    assert res3["total"] == 250
    assert len(res3["articles"]) == 250
    assert res3["next_offset"] is None

    # 4. 경계값: 마지막 바로 앞 (offset=245, limit=5) -> 끝 5건이지만 전체가 아니므로 complete: false
    res4 = _call("law_term", {"term": "건축", "offset": 245, "limit": 5})
    assert res4["complete"] is False
    assert "전체 250건 중 246~250 번째만 줬다" in res4["why"]
    assert "앞 1~245번째" in res4["why"]
    assert len(res4["partial"]["articles"]) == 5
    assert res4["partial"]["next_offset"] is None

    # 5. 경계값: 마지막보다 덜 읽음 (offset=240, limit=5) -> next_offset=245 -> complete: false
    res5 = _call("law_term", {"term": "건축", "offset": 240, "limit": 5})
    assert res5["complete"] is False
    assert "offset=245" in res5["why"]
    assert res5["partial"]["total"] == 250
    assert len(res5["partial"]["articles"]) == 5
    assert res5["partial"]["next_offset"] == 245

    # 6. 경계값: offset 이 total 이상인 경우 (offset=300, limit=200) -> complete: false 및 why 에 전체 건수 초과 알림
    res6 = _call("law_term", {"term": "건축", "offset": 300, "limit": 200})
    assert res6["complete"] is False
    assert "offset 이 전체 건수를 넘었다" in res6["why"]
    assert res6["partial"]["total"] == 250
    assert len(res6["partial"]["articles"]) == 0
    assert res6["partial"]["next_offset"] is None


def test_law_term_pagination_preserves_incomplete_on_original_flaw(monkeypatch):
    """페이징으로 잘라 주더라도 원래 결과가 불완전하면 complete: false 가 유지된다."""
    fake_articles = [{"법령명": "건축법", "조": "제%d조" % i} for i in range(1, 251)]
    fake = Answer({
        "term": "건축",
        "found": True,
        "complete": False,
        "note": "네트워크 지연으로 3페이지 누락",
        "terms": [],
        "articles": fake_articles,
        "laws": ["건축법"],
    })
    monkeypatch.setattr(kit.terms, "articles", lambda *a, **k: fake)

    res = _call("law_term", {"term": "건축", "offset": 0, "limit": 50})
    assert res["complete"] is False
    assert "네트워크 지연" in res["why"]
    assert "이 결과로 '없다'·'전부다'라고 답하지 마라" in res["지시"]
    # partial 안에 페이징 정보가 보존되는지 확인
    assert res["partial"]["total"] == 250
    assert len(res["partial"]["articles"]) == 50
    assert res["partial"]["next_offset"] == 50


# ---------------------------------------------------------------- 12. law_tree 페이징
def test_law_tree_pagination_and_filtering(monkeypatch):
    """law_tree 에 offset 과 limit 을 적용해 rows 범위를 확인하고 필터링과 결합한다."""
    fake_rows = []
    # 시행령 100건, 조례 150건 = 총 250건
    for i in range(1, 101):
        fake_rows.append({"위임구분": "시행령", "대상법령": "주차장법시행령%d" % i})
    for i in range(1, 151):
        fake_rows.append({"위임구분": "위임자치법규", "대상법령": "서울특별시조례%d" % i})

    fake = Answer({
        "law": "주차장법",
        "complete": True,
        "rows": fake_rows,
    })
    monkeypatch.setattr(kit.tree, "delegated", lambda *a, **k: fake)
    monkeypatch.setattr(kit.tree, "summary", lambda r: [])
    monkeypatch.setattr(kit.tree, "subordinate_laws", lambda r: [])

    # 1. 전체 rows 기본 페이징 (기본 limit=200): 200건, next_offset 200 -> complete: false
    res1 = _call("law_tree", {"law": "주차장법"})
    assert res1["complete"] is False
    assert "offset=200" in res1["why"]
    assert "전체 250건 중 1~200 번째만 줬다" in res1["why"]
    assert res1["partial"]["total"] == 250
    assert len(res1["partial"]["rows"]) == 200
    assert res1["partial"]["next_offset"] == 200

    # 2. 다음 페이지 호출 (offset=200, limit=200): 나머지 50건, 전체가 아니므로 complete: false
    res2 = _call("law_tree", {"law": "주차장법", "offset": 200, "limit": 200})
    assert res2["complete"] is False
    assert "전체 250건 중 201~250 번째만 줬다" in res2["why"]
    assert "앞 1~200번째" in res2["why"]
    assert res2["partial"]["total"] == 250
    assert len(res2["partial"]["rows"]) == 50
    assert res2["partial"]["next_offset"] is None

    # 3. group 필터링 결합 (조례 150건 대상, limit=50) -> next_offset=50 -> complete: false
    res3 = _call("law_tree", {"law": "주차장법", "group": "위임자치법규", "offset": 0, "limit": 50})
    assert res3["complete"] is False
    assert "offset=50" in res3["why"]
    assert res3["partial"]["total"] == 150
    assert len(res3["partial"]["rows"]) == 50
    assert res3["partial"]["next_offset"] == 50
    assert all(r["위임구분"] == "위임자치법규" for r in res3["partial"]["rows"])

    # 4. group 필터링 마지막 페이지 (offset=100, limit=50): 50건, 전체가 아니므로 complete: false
    res4 = _call("law_tree", {"law": "주차장법", "group": "위임자치법규", "offset": 100, "limit": 50})
    assert res4["complete"] is False
    assert "전체 150건 중 101~150 번째만 줬다" in res4["why"]
    assert "앞 1~100번째" in res4["why"]
    assert res4["partial"]["total"] == 150
    assert len(res4["partial"]["rows"]) == 50
    assert res4["partial"]["next_offset"] is None


def test_law_tree_pagination_preserves_incomplete(monkeypatch):
    """law_tree 페이징 중에도 원래 rows 에 불완전 요소가 있으면 complete: false 가 유지된다."""
    fake_rows = [{"위임구분": "시행령", "대상법령": "시행령%d" % i} for i in range(10)]
    fake_rows.append({"complete": False, "why": "하위법령 조회 타임아웃"})

    fake = Answer({
        "law": "주차장법",
        "complete": True,
        "rows": fake_rows,
    })
    monkeypatch.setattr(kit.tree, "delegated", lambda *a, **k: fake)
    monkeypatch.setattr(kit.tree, "summary", lambda r: [])
    monkeypatch.setattr(kit.tree, "subordinate_laws", lambda r: [])

    res = _call("law_tree", {"law": "주차장법", "offset": 0, "limit": 5})
    assert res["complete"] is False
    assert "하위법령 조회 타임아웃" in res["why"]
    assert "이 결과로 '없다'·'전부다'라고 답하지 마라" in res["지시"]


# ---------------------------------------------------------------- 13. 결함 수정 정밀 시험
def test_defect_1a_law_term_pagination_500_items(monkeypatch):
    """500건 중 첫 페이지 complete:false, 마지막 페이지도 전체가 아니므로 complete:false"""
    fake_articles = [{"법령명": "건축법", "조": "제%d조" % i} for i in range(1, 501)]
    fake = Answer({
        "term": "건축",
        "found": True,
        "complete": True,
        "terms": [],
        "articles": fake_articles,
        "laws": ["건축법"],
    })
    monkeypatch.setattr(kit.terms, "articles", lambda *a, **k: fake)

    # 1. 500건 첫 페이지 (기본 limit=200): complete:false, why 에 offset 및 건수 범위
    res1 = _call("law_term", {"term": "건축"})
    assert res1["complete"] is False
    assert "offset=200" in res1["why"]
    assert "전체 500건 중 1~200 번째만 줬다" in res1["why"]
    assert "뒤 201~500번째" in res1["why"]
    assert res1["partial"]["total"] == 500
    assert len(res1["partial"]["articles"]) == 200
    assert res1["partial"]["next_offset"] == 200

    # 2. 마지막 페이지 (offset=400, limit=200): 전체를 다 담지 못했으므로 complete:false, 남은 100건, next_offset None
    res_last = _call("law_term", {"term": "건축", "offset": 400, "limit": 200})
    assert res_last["complete"] is False
    assert "전체 500건 중 401~500 번째만 줬다" in res_last["why"]
    assert "앞 1~400번째" in res_last["why"]
    assert res_last["partial"]["total"] == 500
    assert len(res_last["partial"]["articles"]) == 100
    assert res_last["partial"]["next_offset"] is None


def test_defect_1a_law_tree_pagination_500_items(monkeypatch):
    """law_tree 동일: 500건 첫 페이지 complete:false, 마지막 페이지도 complete:false"""
    fake_rows = [{"위임구분": "시행령", "대상법령": "령%d" % i} for i in range(1, 501)]
    fake = Answer({
        "law": "주차장법",
        "complete": True,
        "rows": fake_rows,
    })
    monkeypatch.setattr(kit.tree, "delegated", lambda *a, **k: fake)
    monkeypatch.setattr(kit.tree, "summary", lambda r: [])
    monkeypatch.setattr(kit.tree, "subordinate_laws", lambda r: [])

    # 1. 500건 첫 페이지 (기본 limit=200): complete:false, why 에 offset
    res1 = _call("law_tree", {"law": "주차장법"})
    assert res1["complete"] is False
    assert "offset=200" in res1["why"]
    assert "전체 500건 중 1~200 번째만 줬다" in res1["why"]
    assert "뒤 201~500번째" in res1["why"]
    assert res1["partial"]["total"] == 500
    assert len(res1["partial"]["rows"]) == 200
    assert res1["partial"]["next_offset"] == 200

    # 2. 마지막 페이지 (offset=400, limit=200): complete:false
    res_last = _call("law_tree", {"law": "주차장법", "offset": 400, "limit": 200})
    assert res_last["complete"] is False
    assert "전체 500건 중 401~500 번째만 줬다" in res_last["why"]
    assert "앞 1~400번째" in res_last["why"]
    assert res_last["partial"]["total"] == 500
    assert len(res_last["partial"]["rows"]) == 100
    assert res_last["partial"]["next_offset"] is None


def test_defect_1b_missing_term_found_false_and_instruction(monkeypatch):
    """미등재 용어: found:false 와 지시 확인 (complete 값은 원래 결과 True 를 따름)"""
    fake = Answer({
        "term": "미등재단어",
        "found": False,
        "complete": True,
        "terms": [],
        "articles": [],
        "laws": [],
    })
    monkeypatch.setattr(kit.terms, "articles", lambda *a, **k: fake)

    res = _call("law_term", {"term": "미등재단어"})
    assert res["found"] is False
    assert res["complete"] is True
    assert res["지시"] == "용어 사전에 없는 낱말이다. 조문이 없다는 뜻이 아니다 - law_search 로 본문검색하라"


def test_defect_2_law_api_call_params_empty_and_required(monkeypatch):
    """law_api: call.params 가 비었고 required 에 필수 인자 설명이 있음"""
    fake_entry = {
        "name": "판례 검색 API",
        "endpoint": "http://www.law.go.kr/DRF/lawSearch.do",
        "target": "prec",
        "request_params": [
            {"name": "OC", "value": "string(필수)", "desc": "인증키"},
            {"name": "target", "value": "string(필수)", "desc": "서비스 대상"},
            {"name": "type", "value": "char(필수)", "desc": "출력 형태"},
            {"name": "query", "value": "string(필수)", "desc": "판례 질의어"},
            {"name": "display", "value": "int(선택)", "desc": "조회 건수"}
        ]
    }
    monkeypatch.setattr(kit.catalog, "describe", lambda t: [fake_entry])
    res = _call("law_api", {"target": "prec"})
    entry = res["entries"][0]
    assert entry["call"]["params"] == {}
    assert entry["call"]["target"] == "prec"
    assert entry["call"]["service"] is False
    assert entry["required"] == {"query": "판례 질의어"}


def _call_raw(name, arguments):
    writer = BufferWriter()
    mcp_server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                               "params": {"name": name, "arguments": arguments}}, writer)
    return writer.get_json_lines()[0]["result"]


def test_pagination_rules_a5_law_term(monkeypatch):
    """law_term 에 대한 4가지 결함/경계 케이스 + 정상 1케이스 전수 시험.
    1. offset=400 끝 페이지 (500건 중 100건) -> complete: false, 앞 남은 범위 why
    2. limit=0 및 음수 -> isError: true, 'limit 은 1 이상'
    3. offset 음수 -> isError: true, 'offset 은 0 이상'
    4. offset >= total (offset=500, total=500) -> complete: false, 'offset 이 전체 건수를 넘었다'
    5. 정상 (offset=0, 전부 한 페이지) -> complete: true
    """
    fake_articles = [{"법령명": "건축법", "조": "제%d조" % i} for i in range(1, 501)]
    fake = Answer({
        "term": "건축",
        "found": True,
        "complete": True,
        "terms": [],
        "articles": fake_articles,
        "laws": ["건축법"],
    })
    monkeypatch.setattr(kit.terms, "articles", lambda *a, **k: fake)

    # 1. offset=400 끝 페이지 -> complete: false
    res1 = _call("law_term", {"term": "건축", "offset": 400, "limit": 200})
    assert res1["complete"] is False
    assert "전체 500건 중 401~500 번째만 줬다" in res1["why"]
    assert "앞 1~400번째" in res1["why"]
    assert res1["partial"]["total"] == 500
    assert len(res1["partial"]["articles"]) == 100
    assert res1["partial"]["next_offset"] is None

    # 2. limit=0 및 음수 -> isError: true
    for bad_limit in (0, -1, -10):
        raw = _call_raw("law_term", {"term": "건축", "limit": bad_limit})
        assert raw["isError"] is True
        assert "limit 은 1 이상" in raw["content"][0]["text"]

    # 3. offset 음수 -> isError: true
    for bad_offset in (-1, -100):
        raw = _call_raw("law_term", {"term": "건축", "offset": bad_offset})
        assert raw["isError"] is True
        assert "offset 은 0 이상" in raw["content"][0]["text"]

    # 4. offset >= total -> complete: false, 'offset 이 전체 건수를 넘었다'
    res4 = _call("law_term", {"term": "건축", "offset": 500, "limit": 200})
    assert res4["complete"] is False
    assert "offset 이 전체 건수를 넘었다" in res4["why"]
    assert res4["partial"]["total"] == 500
    assert len(res4["partial"]["articles"]) == 0

    # 5. 정상 (offset=0, 전부 한 페이지) -> 오직 이 경우만 complete: true
    res5 = _call("law_term", {"term": "건축", "offset": 0, "limit": 500})
    assert res5["complete"] is True
    assert res5["total"] == 500
    assert len(res5["articles"]) == 500
    assert res5["next_offset"] is None


def test_pagination_rules_a5_law_tree(monkeypatch):
    """law_tree 에 대한 4가지 결함/경계 케이스 + 정상 1케이스 전수 시험.
    1. offset=400 끝 페이지 (500건 중 100건) -> complete: false, 앞 남은 범위 why
    2. limit=0 및 음수 -> isError: true, 'limit 은 1 이상'
    3. offset 음수 -> isError: true, 'offset 은 0 이상'
    4. offset >= total (offset=500, total=500) -> complete: false, 'offset 이 전체 건수를 넘었다'
    5. 정상 (offset=0, 전부 한 페이지) -> complete: true
    """
    fake_rows = [{"위임구분": "시행령", "대상법령": "령%d" % i} for i in range(1, 501)]
    fake = Answer({
        "law": "주차장법",
        "complete": True,
        "rows": fake_rows,
    })
    monkeypatch.setattr(kit.tree, "delegated", lambda *a, **k: fake)
    monkeypatch.setattr(kit.tree, "summary", lambda r: [])
    monkeypatch.setattr(kit.tree, "subordinate_laws", lambda r: [])

    # 1. offset=400 끝 페이지 -> complete: false
    res1 = _call("law_tree", {"law": "주차장법", "offset": 400, "limit": 200})
    assert res1["complete"] is False
    assert "전체 500건 중 401~500 번째만 줬다" in res1["why"]
    assert "앞 1~400번째" in res1["why"]
    assert res1["partial"]["total"] == 500
    assert len(res1["partial"]["rows"]) == 100
    assert res1["partial"]["next_offset"] is None

    # 2. limit=0 및 음수 -> isError: true
    for bad_limit in (0, -1, -10):
        raw = _call_raw("law_tree", {"law": "주차장법", "limit": bad_limit})
        assert raw["isError"] is True
        assert "limit 은 1 이상" in raw["content"][0]["text"]

    # 3. offset 음수 -> isError: true
    for bad_offset in (-1, -100):
        raw = _call_raw("law_tree", {"law": "주차장법", "offset": bad_offset})
        assert raw["isError"] is True
        assert "offset 은 0 이상" in raw["content"][0]["text"]

    # 4. offset >= total -> complete: false, 'offset 이 전체 건수를 넘었다'
    res4 = _call("law_tree", {"law": "주차장법", "offset": 500, "limit": 200})
    assert res4["complete"] is False
    assert "offset 이 전체 건수를 넘었다" in res4["why"]
    assert res4["partial"]["total"] == 500
    assert len(res4["partial"]["rows"]) == 0

    # 5. 정상 (offset=0, 전부 한 페이지) -> 오직 이 경우만 complete: true
    res5 = _call("law_tree", {"law": "주차장법", "offset": 0, "limit": 500})
    assert res5["complete"] is True
    assert res5["total"] == 500
    assert len(res5["rows"]) == 500
    assert res5["next_offset"] is None




