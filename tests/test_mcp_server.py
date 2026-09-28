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
