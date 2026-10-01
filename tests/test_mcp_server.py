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


# ---------------------------------------------------------------- 8. 클라이언트 상한 절단
def test_response_truncation_at_client_limit(monkeypatch):
    """응답이 클라이언트 상한을 넘으면 절단하고 complete: false 와 사유를 남긴다."""
    # 거대한 결과 시뮬레이션
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
    max_c, max_b = mcp_server.get_client_limits()
    assert len(raw_text) <= max_c
    assert len(raw_text.encode("utf-8")) <= max_b
    parsed = json.loads(raw_text)
    assert parsed["complete"] is False
    assert isinstance(parsed["partial"], dict)
    assert "상한 때문에 이번에는" in parsed["why"]
    assert "offset=" in parsed["why"]
    assert parsed["next_offset"] == len(parsed["partial"]["articles"])
    assert "이 결과로 '없다'·'전부다'라고 답하지 마라" in parsed["지시"]
    assert parsed["_end"]["complete"] is False


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
    for data in ({"items": ['"' * 30000]}, {"items": [{"a": "b\\c"}] * 9000}):
        # 1. claude 클라이언트 (50,000자 상한) 환경
        text_claude = mcp_server.serialize_response(data, client_name="claude")
        parsed_claude = json.loads(text_claude)
        assert len(text_claude) <= 50000
        assert len(text_claude.encode("utf-8")) <= 150000
        assert parsed_claude["complete"] is False
        assert "partial" in parsed_claude      # 알맹이가 남아 있다

        # 2. 기본 클라이언트 (6,000자 상한) 환경
        text_def = mcp_server.serialize_response(data)
        parsed_def = json.loads(text_def)
        assert len(text_def) <= 6000
        assert len(text_def.encode("utf-8")) <= 16000
        assert parsed_def["complete"] is False


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
    """원래 3만 자가 넘는 대형 결과도 요약 모드(기본)에서는 본문을 빼서 상한 안에 든다."""
    # 20개 조문, 각 조문내용 1,400자 -> 통째로는 3만 자 이상
    fake_articles = []
    for i in range(20):
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
        "terms": [{"용어": "건폐율", "조문수": 20}],
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
    assert len(parsed["articles"]) == 20
    assert "조문내용" not in parsed["articles"][0]
    # 법령별 건수가 제공되는지 확인
    assert parsed["counts"]["국토의 계획 및 이용에 관한 법률"] == 20


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
        대용량 조문 결과를 한 번에 주지 않고 50건 단위로 나누어 주며,
        total 과 next_offset 을 통해 이어 읽을 수 있어야 한다.
        잘라서 준 것은 불완전(complete: false)이 아니라 정상 페이징(complete: true)이어야 한다.
    """
    fake_articles = [{"법령명": "건축법", "조": "제%d조" % i} for i in range(1, 101)]
    fake = Answer({
        "term": "건축",
        "found": True,
        "complete": True,
        "terms": [],
        "articles": fake_articles,
        "laws": ["건축법"],
    })
    monkeypatch.setattr(kit.terms, "articles", lambda *a, **k: fake)

    # 1. 기본 호출 (offset=0, limit=50 기본값): 50건 반환, 다음 offset 50 -> 불완전(complete: false)
    res1 = _call("law_term", {"term": "건축"})
    assert res1["complete"] is False
    assert "offset=50" in res1["why"]
    assert "전체 100건 중 1~50 번째만 줬다" in res1["why"]
    assert res1["partial"]["total"] == 100
    assert len(res1["partial"]["articles"]) == 50
    assert res1["partial"]["articles"][0]["조"] == "제1조"
    assert res1["partial"]["articles"][49]["조"] == "제50조"
    assert res1["partial"]["next_offset"] == 50

    # 2. 다음 페이지 호출 (offset=50, limit=50): 나머지 50건 반환, 전체를 다 담지 못했으므로 complete: false
    res2 = _call("law_term", {"term": "건축", "offset": 50, "limit": 50})
    assert res2["complete"] is False
    assert "전체 100건 중 51~100 번째만 줬다" in res2["why"]
    assert "앞 1~50번째" in res2["why"]
    assert res2["partial"]["total"] == 100
    assert len(res2["partial"]["articles"]) == 50
    assert res2["partial"]["articles"][0]["조"] == "제51조"
    assert res2["partial"]["articles"][49]["조"] == "제100조"
    assert res2["partial"]["next_offset"] is None

    # 3. 경계값: limit 이 전체를 넘는 경우 (offset=0, limit=100) -> 한 번에 다 받음 complete: true
    res3 = _call("law_term", {"term": "건축", "offset": 0, "limit": 100})
    assert res3["complete"] is True
    assert res3["total"] == 100
    assert len(res3["articles"]) == 100
    assert res3["next_offset"] is None

    # 4. 경계값: 마지막 바로 앞 (offset=95, limit=5) -> 끝 5건이지만 전체가 아니므로 complete: false
    res4 = _call("law_term", {"term": "건축", "offset": 95, "limit": 5})
    assert res4["complete"] is False
    assert "전체 100건 중 96~100 번째만 줬다" in res4["why"]
    assert "앞 1~95번째" in res4["why"]
    assert len(res4["partial"]["articles"]) == 5
    assert res4["partial"]["next_offset"] is None

    # 5. 경계값: 마지막보다 덜 읽음 (offset=90, limit=5) -> next_offset=95 -> complete: false
    res5 = _call("law_term", {"term": "건축", "offset": 90, "limit": 5})
    assert res5["complete"] is False
    assert "offset=95" in res5["why"]
    assert res5["partial"]["total"] == 100
    assert len(res5["partial"]["articles"]) == 5
    assert res5["partial"]["next_offset"] == 95

    # 6. 경계값: offset 이 total 이상인 경우 (offset=100, limit=50) -> complete: false 및 why 에 전체 건수 초과 알림
    res6 = _call("law_term", {"term": "건축", "offset": 100, "limit": 50})
    assert res6["complete"] is False
    assert "offset 이 전체 건수를 넘었다" in res6["why"]
    assert res6["partial"]["total"] == 100
    assert len(res6["partial"]["articles"]) == 0
    assert res6["partial"]["next_offset"] is None
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
    # 시행령 5건, 조례 60건 = 총 65건
    for i in range(1, 6):
        fake_rows.append({"위임구분": "시행령", "대상법령": "령%d" % i})
    for i in range(1, 61):
        fake_rows.append({"위임구분": "위임자치법규", "대상법령": "조례%d" % i})

    fake = Answer({
        "law": "주차장법",
        "complete": True,
        "rows": fake_rows,
    })
    monkeypatch.setattr(kit.tree, "delegated", lambda *a, **k: fake)
    monkeypatch.setattr(kit.tree, "summary", lambda r: [])
    monkeypatch.setattr(kit.tree, "subordinate_laws", lambda r: [])

    # 1. 전체 rows 기본 페이징 (기본 limit=50): 50건, next_offset 50 -> complete: false
    res1 = _call("law_tree", {"law": "주차장법"})
    assert res1["complete"] is False
    assert "offset=50" in res1["why"]
    assert "전체 65건 중 1~50 번째만 줬다" in res1["why"]
    assert res1["partial"]["total"] == 65
    assert len(res1["partial"]["rows"]) == 50
    assert res1["partial"]["next_offset"] == 50

    # 2. 다음 페이지 호출 (offset=50, limit=50): 나머지 15건, 전체가 아니므로 complete: false
    res2 = _call("law_tree", {"law": "주차장법", "offset": 50, "limit": 50})
    assert res2["complete"] is False
    assert "전체 65건 중 51~65 번째만 줬다" in res2["why"]
    assert "앞 1~50번째" in res2["why"]
    assert res2["partial"]["total"] == 65
    assert len(res2["partial"]["rows"]) == 15
    assert res2["partial"]["next_offset"] is None

    # 3. group 필터링 결합 (조례 60건 대상, limit=20) -> next_offset=20 -> complete: false
    res3 = _call("law_tree", {"law": "주차장법", "group": "위임자치법규", "offset": 0, "limit": 20})
    assert res3["complete"] is False
    assert "offset=20" in res3["why"]
    assert res3["partial"]["total"] == 60
    assert len(res3["partial"]["rows"]) == 20
    assert res3["partial"]["next_offset"] == 20
    assert all(r["위임구분"] == "위임자치법규" for r in res3["partial"]["rows"])

    # 4. group 필터링 마지막 페이지 (offset=40, limit=20): 20건, 전체가 아니므로 complete: false
    res4 = _call("law_tree", {"law": "주차장법", "group": "위임자치법규", "offset": 40, "limit": 20})
    assert res4["complete"] is False
    assert "전체 60건 중 41~60 번째만 줬다" in res4["why"]
    assert "앞 1~40번째" in res4["why"]
    assert res4["partial"]["total"] == 60
    assert len(res4["partial"]["rows"]) == 20
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

    # 1. 500건 첫 페이지 (기본 limit=50): complete:false, why 에 offset 및 건수 범위
    res1 = _call("law_term", {"term": "건축"})
    assert res1["complete"] is False
    assert "offset=50" in res1["why"]
    assert "전체 500건 중 1~50 번째만 줬다" in res1["why"]
    assert "뒤 51~500번째" in res1["why"]
    assert res1["partial"]["total"] == 500
    assert len(res1["partial"]["articles"]) == 50
    assert res1["partial"]["next_offset"] == 50

    # 2. 마지막 페이지 (offset=450, limit=50): 전체를 다 담지 못했으므로 complete:false, 남은 50건, next_offset None
    res_last = _call("law_term", {"term": "건축", "offset": 450, "limit": 50})
    assert res_last["complete"] is False
    assert "전체 500건 중 451~500 번째만 줬다" in res_last["why"]
    assert "앞 1~450번째" in res_last["why"]
    assert res_last["partial"]["total"] == 500
    assert len(res_last["partial"]["articles"]) == 50
    assert res_last["partial"]["next_offset"] is None


def test_defect_1a_law_tree_pagination_500_items(monkeypatch):
    """law_tree 동일: 500건 첫 페이지 complete:false, 마지막 페이지도 complete:false"""
    fake_rows = [{"위임구분": "위임자치법규", "대상법령": "조례%d" % i} for i in range(1, 501)]
    fake = Answer({
        "law": "주차장법",
        "complete": True,
        "rows": fake_rows,
    })
    monkeypatch.setattr(kit.tree, "delegated", lambda *a, **k: fake)
    monkeypatch.setattr(kit.tree, "summary", lambda r: [])
    monkeypatch.setattr(kit.tree, "subordinate_laws", lambda r: [])

    # 1. 500건 첫 페이지 (기본 limit=50): complete:false, why 에 offset
    res1 = _call("law_tree", {"law": "주차장법"})
    assert res1["complete"] is False
    assert "offset=50" in res1["why"]
    assert "전체 500건 중 1~50 번째만 줬다" in res1["why"]
    assert "뒤 51~500번째" in res1["why"]
    assert res1["partial"]["total"] == 500
    assert len(res1["partial"]["rows"]) == 50
    assert res1["partial"]["next_offset"] == 50

    # 2. 마지막 페이지 (offset=450, limit=50): complete:false
    res_last = _call("law_tree", {"law": "주차장법", "offset": 450, "limit": 50})
    assert res_last["complete"] is False
    assert "전체 500건 중 451~500 번째만 줬다" in res_last["why"]
    assert "앞 1~450번째" in res_last["why"]
    assert res_last["partial"]["total"] == 500
    assert len(res_last["partial"]["rows"]) == 50
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
    fake_articles = [{"법령명": "건축법", "조": "제%d조" % i} for i in range(1, 101)]
    fake = Answer({
        "term": "건축",
        "found": True,
        "complete": True,
        "terms": [],
        "articles": fake_articles,
        "laws": ["건축법"],
    })
    monkeypatch.setattr(kit.terms, "articles", lambda *a, **k: fake)

    # 1. offset=50 끝 페이지 -> complete: false
    res1 = _call("law_term", {"term": "건축", "offset": 50, "limit": 50})
    assert res1["complete"] is False
    assert "전체 100건 중 51~100 번째만 줬다" in res1["why"]
    assert "앞 1~50번째" in res1["why"]
    assert res1["partial"]["total"] == 100
    assert len(res1["partial"]["articles"]) == 50
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

    # 4. offset >= total (offset=100, total=100) -> complete: false, 'offset 이 전체 건수를 넘었다'
    res4 = _call("law_term", {"term": "건축", "offset": 100, "limit": 50})
    assert res4["complete"] is False
    assert "offset 이 전체 건수를 넘었다" in res4["why"]
    assert res4["partial"]["total"] == 100
    assert len(res4["partial"]["articles"]) == 0

    # 5. 정상 (offset=0, 전부 한 페이지) -> 오직 이 경우만 complete: true
    res5 = _call("law_term", {"term": "건축", "offset": 0, "limit": 100})
    assert res5["complete"] is True
    assert res5["total"] == 100
    assert len(res5["articles"]) == 100
    assert res5["next_offset"] is None


def test_pagination_rules_a5_law_tree(monkeypatch):
    """law_tree 에 대한 4가지 결함/경계 케이스 + 정상 1케이스 전수 시험.
    1. offset=30 끝 페이지 (50건 중 20건) -> complete: false, 앞 남은 범위 why
    2. limit=0 및 음수 -> isError: true, 'limit 은 1 이상'
    3. offset 음수 -> isError: true, 'offset 은 0 이상'
    4. offset >= total (offset=50, total=50) -> complete: false, 'offset 이 전체 건수를 넘었다'
    5. 정상 (offset=0, 전부 한 페이지) -> complete: true
    """
    fake_rows = [{"위임구분": "위임자치법규", "대상법령": "조례%d" % i} for i in range(1, 51)]
    fake = Answer({
        "law": "주차장법",
        "complete": True,
        "rows": fake_rows,
    })
    monkeypatch.setattr(kit.tree, "delegated", lambda *a, **k: fake)
    monkeypatch.setattr(kit.tree, "summary", lambda r: [])
    monkeypatch.setattr(kit.tree, "subordinate_laws", lambda r: [])

    # 1. offset=30 끝 페이지 -> complete: false
    res1 = _call("law_tree", {"law": "주차장법", "offset": 30, "limit": 30})
    assert res1["complete"] is False
    assert "전체 50건 중 31~50 번째만 줬다" in res1["why"]
    assert "앞 1~30번째" in res1["why"]
    assert res1["partial"]["total"] == 50
    assert len(res1["partial"]["rows"]) == 20
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
    res4 = _call("law_tree", {"law": "주차장법", "offset": 50, "limit": 20})
    assert res4["complete"] is False
    assert "offset 이 전체 건수를 넘었다" in res4["why"]
    assert res4["partial"]["total"] == 50
    assert len(res4["partial"]["rows"]) == 0

    # 5. 정상 (offset=0, 전부 한 페이지) -> 오직 이 경우만 complete: true
    res5 = _call("law_tree", {"law": "주차장법", "offset": 0, "limit": 50})
    assert res5["complete"] is True
    assert res5["total"] == 50
    assert len(res5["rows"]) == 50
    assert res5["next_offset"] is None


# ---------------------------------------------------------------- 14. 헤더 첫 키 고정 및 _end 꼬리 시험
def _call_raw_text(name, arguments):
    """tools/call 요청을 보내 직렬화된 JSON 원문 문자열을 그대로 반환한다."""
    writer = BufferWriter()
    mcp_server.handle_message({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments}
    }, writer)
    return writer.get_json_lines()[0]["result"]["content"][0]["text"]


def test_header_first_keys_and_end_tail_9_tools(monkeypatch):
    """도구 9종 각각에 대해 'complete' 가 처음 64자 안에 나오고 _end 꼬리가 붙는지 검증한다."""
    # 1. law_brief
    monkeypatch.setattr(kit, "brief", lambda *a, **k: {
        "topic": "소음",
        "terms": Answer({"found": True, "complete": True, "articles": [], "laws": []}),
        "search": {"query": "소음", "axes": {}, "incomplete": []},
        "tree": {}, "annex": {}, "gaps": []
    })
    monkeypatch.setattr(kit, "format_brief", lambda *a, **k: "소음 요약 보고서")

    # 2. law_find
    monkeypatch.setattr(kit.laws, "find", lambda *a, **k: [{"법령명": "소음1"}, {"법령명": "소음2"}])

    # 3. law_term
    fake_term = Answer({
        "term": "건축", "found": True, "complete": True,
        "articles": [{"법령명": "건축법", "조": "제1조"}, {"법령명": "건축법", "조": "제2조"}],
        "laws": ["건축법"]
    })
    monkeypatch.setattr(kit.terms, "articles", lambda *a, **k: fake_term)

    # 4. law_tree
    fake_tree = Answer({
        "law": "건축법", "complete": True,
        "rows": [{"위임구분": "시행령", "대상법령": "건축법시행령"}, {"위임구분": "위임자치법규", "대상법령": "건축조례"}]
    })
    monkeypatch.setattr(kit.tree, "delegated", lambda *a, **k: fake_tree)
    monkeypatch.setattr(kit.tree, "summary", lambda *a, **k: [("시행령", 1), ("위임자치법규", 1)])
    monkeypatch.setattr(kit.tree, "subordinate_laws", lambda r: ["건축조례"])

    # 5. law_annex
    fake_annex = Answer({
        "law_name": "건축법", "complete": True,
        "items": [{"별표명": "별표1"}, {"별표명": "별표2"}]
    })
    monkeypatch.setattr(kit.annex, "of_law", lambda *a, **k: fake_annex)

    # 6. law_history
    monkeypatch.setattr(kit.history, "is_current", lambda *a, **k: (False, "구법"))
    monkeypatch.setattr(kit.history, "successor", lambda *a, **k: {"name": "구법", "status": "구법", "candidates": ["신법1"]})

    # 7. law_search
    fake_search = {
        "query": "소음",
        "axes": {
            "law": Answer({"target": "law", "complete": True, "count": 2, "items": [{"제목": "1"}, {"제목": "2"}]}),
            "prec": Answer({"target": "prec", "complete": True, "count": 1, "items": [{"제목": "1"}]})
        },
        "incomplete": []
    }
    monkeypatch.setattr(kit.search, "across", lambda *a, **k: fake_search)

    # 8. law_api
    monkeypatch.setattr(kit.catalog, "describe", lambda *a, **k: [{"target": "law", "endpoint": "http://x", "request_params": []}])
    monkeypatch.setattr(kit.catalog, "search", lambda *a, **k: [
        {"target": "law", "endpoint": "http://x", "request_params": []},
        {"target": "prec", "endpoint": "http://y", "request_params": []}
    ])

    # 9. law_call
    monkeypatch.setattr(kit.client, "call", lambda *a, **k: Result(
        "law", ok=True, items=[{"법령명": "테스트1"}, {"법령명": "테스트2"}], total=2
    ))

    tool_calls = [
        ("law_brief", {"topic": "소음"}),
        ("law_find", {"name": "소음"}),
        ("law_term", {"term": "건축"}),
        ("law_tree", {"law": "건축법"}),
        ("law_annex", {"law_name": "건축법"}),
        ("law_history", {"name": "구법"}),
        ("law_search", {"query": "소음"}),
        ("law_api", {"query": "법령"}),
        ("law_call", {"target": "law"}),
    ]

    for name, args in tool_calls:
        text = _call_raw_text(name, args)

        # 1. JSON 직렬화 결과 문자열에서 실제로 "complete" 가 처음 64자 안에 나오는지 확인
        pos = text.find('"complete"')
        assert 0 <= pos < 64, "%s: 'complete' 가 처음 64자 안에 나오지 않음 (pos=%d)" % (name, pos)

        parsed = json.loads(text)
        keys = list(parsed.keys())

        # 2. 첫 키는 complete, 마지막 키는 _end
        assert keys[0] == "complete", "%s: 첫 키가 complete 가 아님 (%s)" % (name, keys[0])
        assert keys[-1] == "_end", "%s: 마지막 키가 _end 가 아님 (%s)" % (name, keys[-1])

        # 3. 꼬리 _end 일치 검증
        assert parsed["_end"]["complete"] == parsed["complete"]
        assert parsed["_end"]["count"] == parsed["count"]

        # 4. count 검증
        if name in ("law_find", "law_annex", "law_api", "law_call"):
            assert parsed["count"] == 2
        elif name == "law_history":
            assert parsed["count"] == 1
        elif name == "law_brief":
            assert parsed["count"] == 0
        elif name == "law_search":
            assert parsed["count"] == {"law": 2, "prec": 1}
        elif name == "law_term":
            assert parsed["count"] == {"laws": 1, "articles": 2}
        elif name == "law_tree":
            assert parsed["count"] == {"조례": 1, "rows": 2}


def test_header_first_keys_incomplete_and_errors(monkeypatch):
    """Incomplete 예외 및 불완전 결과에서도 앞 64자 내 complete 및 순서가 유지된다."""
    # 1. Incomplete 예외 발생 시
    def raise_incomplete(*a, **k):
        raise Incomplete("미확인 항목이 있어 열 수 없습니다")

    monkeypatch.setattr(kit.laws, "find", raise_incomplete)
    text = _call_raw_text("law_find", {"name": "주차장법"})
    pos = text.find('"complete"')
    assert 0 <= pos < 64
    parsed = json.loads(text)
    keys = list(parsed.keys())
    assert keys[0] == "complete"
    assert keys[1] == "why"
    assert keys[2] == "지시"
    assert keys[-1] == "_end"
    assert parsed["complete"] is False
    assert parsed["_end"]["complete"] is False
    assert parsed["_end"]["count"] == parsed["count"]

    # 2. 불완전 Result 객체
    monkeypatch.setattr(kit.client, "call", lambda *a, **k: Result("law", ok=False, error="조회시간 초과"))
    text2 = _call_raw_text("law_call", {"target": "law"})
    assert text2.find('"complete"') < 64
    parsed2 = json.loads(text2)
    assert parsed2["complete"] is False
    assert parsed2["_end"]["complete"] is False
    assert parsed2["_end"]["count"] == parsed2["count"]


def test_header_first_keys_with_demo_warning(monkeypatch):
    """데모 키 환경에서도 앞 64자 내 complete 가 오고 경고가 올바른 위치에 들어간다."""
    monkeypatch.setattr(kit.client, "is_demo_key", lambda: True)
    monkeypatch.setattr(kit.laws, "find", lambda *a, **k: [{"법령명": "소음"}])
    text = _call_raw_text("law_find", {"name": "소음"})
    assert text.find('"complete"') < 64
    parsed = json.loads(text)
    keys = list(parsed.keys())
    assert keys[0] == "complete"
    assert "경고" in keys
    assert keys[-1] == "_end"
    assert parsed["_end"]["complete"] is True
    assert parsed["_end"]["count"] == 1


def test_header_first_keys_truncation_preserves_header_and_end(monkeypatch):
    """클라이언트 상한 절단 시에도 complete 가 맨 앞 64자 내에 있고 _end 꼬리가 남는다."""
    huge_list = [{"내용": "가" * 1000} for _ in range(70)]
    monkeypatch.setattr(kit.terms, "articles",
                        lambda *a, **k: Answer({"complete": True, "items": huge_list}))

    text = _call_raw_text("law_term", {"term": "테스트"})
    max_c, max_b = mcp_server.get_client_limits()
    assert len(text) <= max_c
    assert len(text.encode("utf-8")) <= max_b
    pos = text.find('"complete"')
    assert 0 <= pos < 64
    parsed = json.loads(text)
    keys = list(parsed.keys())
    assert keys[0] == "complete"
    if "읽기안내" in keys:
        assert keys[1] == "읽기안내"
        assert keys[2] == "why"
    else:
        assert keys[1] == "why"
        assert keys[2] == "지시"
    assert keys[-1] == "_end"
    assert parsed["complete"] is False
    assert parsed["_end"]["complete"] is False
    assert parsed["_end"]["count"] == parsed["count"]


# ---------------------------------------------------------------- 15. 클라이언트별 응답 상한 시험
def test_client_limits_selection_by_name():
    """클라이언트 이름(소문자·앞뒤 공백 제거 후 완전 일치)에 따라 올바른 글자/바이트 상한이 선택된다."""
    cases = {
        "claude-code": (50000, 150000),
        "claude_code": (50000, 150000),
        "claudecode": (50000, 150000),
        "claude": (50000, 150000),
        "claude-desktop": (50000, 150000),
        "  Claude-Code  ": (50000, 150000),  # 공백 및 대소문자 무시 완전 일치
        "codex": (20000, 60000),
        "codex-mcp-client": (20000, 60000),
        "codex-cli": (20000, 60000),
        "copilot": (7000, 16000),
        "github-copilot": (7000, 16000),
        "copilot-chat": (7000, 16000),
        "omo": (15000, 40000),
        "senpi": (15000, 40000),
        "kiro": (20000, 60000),
        "kiro-cli": (20000, 60000),
        "hermes": (40000, 120000),
        "hermes-agent": (40000, 120000),
        "gemini-cli": (32000, 96000),
        "gemini": (32000, 96000),
        "grok": (6000, 16000),
        "grok-cli": (6000, 16000),
        "grok-agent": (6000, 16000),
    }
    for name, expected in cases.items():
        assert mcp_server.get_client_limits(name) == expected, (
            "클라이언트 '%s' 상한 불일치: 기대=%s, 실제=%s" % (
                name, expected, mcp_server.get_client_limits(name)
            )
        )


def test_unknown_or_missing_client_name_uses_default_limits():
    """모르는 이름이거나 이름이 없으면 가장 작은 쪽인 (6000, 16000)을 적용한다."""
    for unknown in ("unknown-agent", "custom-client", "", None):
        assert mcp_server.get_client_limits(unknown) == (6000, 16000)


def test_env_overrides_client_limits(monkeypatch):
    """환경변수 KOREA_LAW_MCP_MAX_CHARS, KOREA_LAW_MCP_MAX_BYTES 가 있으면 최우선한다."""
    # 1. MAX_CHARS 만 오버라이드
    monkeypatch.setenv("KOREA_LAW_MCP_MAX_CHARS", "12345")
    assert mcp_server.get_client_limits("claude") == (12345, 150000)

    # 2. MAX_BYTES 만 오버라이드
    monkeypatch.delenv("KOREA_LAW_MCP_MAX_CHARS", raising=False)
    monkeypatch.setenv("KOREA_LAW_MCP_MAX_BYTES", "54321")
    assert mcp_server.get_client_limits("claude") == (50000, 54321)

    # 3. 둘 다 오버라이드
    monkeypatch.setenv("KOREA_LAW_MCP_MAX_CHARS", "7777")
    monkeypatch.setenv("KOREA_LAW_MCP_MAX_BYTES", "8888")
    assert mcp_server.get_client_limits("claude") == (7777, 8888)
    assert mcp_server.get_client_limits("unknown") == (7777, 8888)


def test_truncation_by_utf8_byte_limit_when_chars_under_limit():
    """글자 수는 상한 미만이지만 한국어 UTF-8 바이트 수가 상한을 넘는 경우 바이트 기준으로 절단된다."""
    # copilot: (7000, 16000)
    # 한글 5600자: 글자 수는 약 5700자(JSON 포맷 포함)로 7000자 이하이지만,
    # 바이트 수는 5600*3 = 16800바이트 이상으로 16000바이트 초과!
    hangul_data = {"items": ["가" * 100 for _ in range(56)]}
    text = mcp_server.serialize_response(hangul_data, client_name="copilot")
    assert len(text) <= 7000
    assert len(text.encode("utf-8")) <= 16000
    parsed = json.loads(text)
    assert parsed["complete"] is False
    assert isinstance(parsed["partial"], dict)
    assert "상한 때문에 items 에서" in parsed["why"]
    assert "건을 뺐다. 범위를 좁혀 다시 물어라" in parsed["why"]
    assert "생략" in parsed["partial"]
    assert parsed["_end"]["complete"] is False


def test_initialize_logs_client_info_to_stderr(monkeypatch):
    """initialize 요청 시 clientInfo.name 과 version 을 기억하고 stderr 에 한 줄 남긴다."""
    logged = []
    monkeypatch.setattr(mcp_server, "_log", lambda msg: logged.append(msg))
    monkeypatch.setitem(mcp_server.CURRENT_CLIENT, "name", "")
    monkeypatch.setitem(mcp_server.CURRENT_CLIENT, "version", "")

    writer = BufferWriter()
    req = {
        "jsonrpc": "2.0",
        "id": "init-1",
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "clientInfo": {
                "name": "claude-code",
                "version": "1.0.5"
            }
        }
    }
    mcp_server.handle_message(req, writer)
    assert mcp_server.CURRENT_CLIENT["name"] == "claude-code"
    assert mcp_server.CURRENT_CLIENT["version"] == "1.0.5"
    assert logged == ["client: claude-code 1.0.5"]


def test_client_limits_exact_match_no_partial():
    """부분 일치로 걸리지 않고 오직 완전 일치만 표에 걸린다.
    부분 일치 예: 'openclaude'는 'claude'에 걸리지 않고 기본값 (6000, 16000)으로 떨어진다.
    'api-client'는 'pi'에 걸리지 않고 기본값 (6000, 16000)으로 떨어진다.
    """
    partial_cases = [
        "openclaude",
        "api-client",
        "my-api",
        "claude-something-else",
        "not-copilot",
        "pi",
    ]
    for name in partial_cases:
        assert mcp_server.get_client_limits(name) == (6000, 16000), (
            "'%s' 가 부분 일치로 기본값이 아닌 상한에 잘못 매핑됨: %s" % (
                name, mcp_server.get_client_limits(name)
            )
        )


def test_client_limits_default_and_grok():
    """grok 은 (6000, 16000)을 적용받고, agy/Antigravity/cline/openclaude 등은 기본값 (6000, 16000)이다."""
    assert mcp_server.get_client_limits("grok") == (6000, 16000)
    assert mcp_server.get_client_limits("grok-cli") == (6000, 16000)
    assert mcp_server.get_client_limits("grok-agent") == (6000, 16000)
    for default_client in ("agy", "Antigravity", "antigravity", "cline", "openclaude", "unknown-client", "", None):
        assert mcp_server.get_client_limits(default_client) == (6000, 16000)


def test_initialize_logs_client_info_to_clients_log(tmp_path, monkeypatch):
    """initialize 요청 시 clientInfo.name, version 과 시각이 캐시 폴더 아래 clients.log 에 기록된다."""
    monkeypatch.setattr(kit.client, "CACHE_DIR", str(tmp_path))
    writer = BufferWriter()
    req = {
        "jsonrpc": "2.0",
        "id": "init-log-test",
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "clientInfo": {
                "name": "test-agent-client",
                "version": "2.3.4"
            }
        }
    }
    mcp_server.handle_message(req, writer)
    log_file = tmp_path / "clients.log"
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "test-agent-client" in content
    assert "2.3.4" in content
    line = content.strip().splitlines()[-1]
    parts = line.split("\t")
    assert len(parts) == 3
    assert parts[1] == "test-agent-client"
    assert parts[2] == "2.3.4"


def test_law_tree_full_hierarchy_and_paged_ordinances(monkeypatch):
    """law_tree 기본 응답에 법령체계(시행령, 시행규칙, 위임행정규칙)는 이어 읽기와 무관하게 전부 들어가고,
    조례(자치법규) 이름 목록과 rows 는 offset/limit 으로 페이징된다.
    이어 읽기 때문에 생기는 complete:false 의 why 에는 지정된 안내 문구가 포함된다.
    """
    fake_rows = []
    for i in range(1, 4):
        fake_rows.append({"위임구분": "시행령", "대상법령": "테스트법시행령%d" % i})
    for i in range(1, 3):
        fake_rows.append({"위임구분": "시행규칙", "대상법령": "테스트법시행규칙%d" % i})
    fake_rows.append({"위임구분": "위임행정규칙", "대상법령": "테스트훈령1"})
    for i in range(1, 31):
        fake_rows.append({"위임구분": "위임자치법규", "대상법령": "지자체조례%d" % i})

    fake = Answer({
        "law": "테스트법",
        "complete": True,
        "rows": fake_rows,
    })
    monkeypatch.setattr(kit.tree, "delegated", lambda *a, **k: fake)
    monkeypatch.setattr(kit.tree, "summary", lambda r: [])
    monkeypatch.setattr(kit.tree, "subordinate_laws", lambda r: [])

    # offset=0, limit=10 (전체 36개 행 중 10개 행)
    res = _call("law_tree", {"law": "테스트법", "offset": 0, "limit": 10})
    assert res["complete"] is False
    # 법령체계는 이어 읽기와 무관하게 전부 들어 있어야 함
    assert res["partial"]["법령체계"]["시행령"] == ["테스트법시행령1", "테스트법시행령2", "테스트법시행령3"]
    assert res["partial"]["법령체계"]["시행규칙"] == ["테스트법시행규칙1", "테스트법시행규칙2"]
    assert res["partial"]["법령체계"]["위임행정규칙"] == ["테스트훈령1"]

    # 조례(자치법규) 이름 목록과 rows 는 현재 페이지만 들어 있어야 함
    assert len(res["partial"]["rows"]) == 10
    # 1~10번째 행 중 조례 행에 해당하는 조례들만 추출
    assert len(res["partial"]["조례"]) == 4
    assert res["partial"]["조례"] == ["지자체조례1", "지자체조례2", "지자체조례3", "지자체조례4"]

    # why 문구 검증
    assert "법령체계(시행령·시행규칙·행정규칙)는 이 응답에 모두 들어 있다. 잘린 것은 조례 목록뿐" in res["why"]


def test_law_tree_hierarchy_preserves_original_incomplete(monkeypatch):
    """법령체계 자체가 원래 결과에서 불완전하면 페이징과 무관하게 그대로 complete:false 다."""
    fake_rows = [{"위임구분": "시행령", "대상법령": "테스트령1"}]
    fake_rows.append({"complete": False, "why": "법령체계 연혁 조회 실패"})
    fake = Answer({
        "law": "테스트법",
        "complete": False,
        "rows": fake_rows,
    })
    monkeypatch.setattr(kit.tree, "delegated", lambda *a, **k: fake)
    monkeypatch.setattr(kit.tree, "summary", lambda r: [])

    res = _call("law_tree", {"law": "테스트법", "offset": 0, "limit": 50})
    assert res["complete"] is False
    assert "법령체계 연혁 조회 실패" in res["why"]


def test_law_search_axes_summary_immediately_after_judgment(monkeypatch):
    """law_search 응답 맨 앞 판정 정보 바로 뒤에 '축별': {축이름: {'complete': bool, 'total': n, '받은': m}} 요약을 둔다."""
    fake_search = {
        "query": "소음",
        "axes": {
            "law": Answer({"target": "law", "label": "법령", "complete": True, "total": 2, "count": 2, "items": [{"제목": "1"}, {"제목": "2"}]}),
            "prec": Answer({"target": "prec", "label": "판례", "complete": False, "total": 10, "count": 1, "items": [{"제목": "1"}], "note": "앞 1건만 조회"})
        },
        "incomplete": ["prec"]
    }
    monkeypatch.setattr(kit.search, "across", lambda *a, **k: fake_search)

    text = _call_raw_text("law_search", {"query": "소음"})
    parsed = json.loads(text)

    # 판정 정보 순서 검증: complete -> why -> 지시 -> count -> total -> 축별
    keys = list(parsed.keys())
    assert keys[0] == "complete"
    assert "축별" in keys
    idx_axes = keys.index("축별")
    target_next_key = "partial" if not parsed["complete"] else "query"
    assert idx_axes < keys.index(target_next_key)

    # 축별 요약 검증
    summary = parsed["축별"]
    assert "법령" in summary
    assert summary["법령"] == {"complete": True, "total": 2, "받은": 2}
    assert "판례" in summary
    assert summary["판례"] == {"complete": False, "total": 10, "받은": 1}


# ---------------------------------------------------------------- 17. 예산에 맞춰 담기 (Budget-fitting) 시험
def test_budget_fitting_law_tree_hierarchy_preserved_and_next_offset(monkeypatch):
    """law_tree 상한 축소 시 머리 요약(법령체계)이 온전히 남고, partial 이 dict 이며 next_offset 과 why 가 정확히 일치한다."""
    fake_rows = []
    for i in range(1, 4):
        fake_rows.append({"위임구분": "시행령", "대상법령": "테스트법시행령%d" % i})
    for i in range(1, 3):
        fake_rows.append({"위임구분": "시행규칙", "대상법령": "테스트법시행규칙%d" % i})
    for i in range(1, 200):
        fake_rows.append({"위임구분": "위임자치법규", "대상법령": "서울특별시 주차장 설치 및 관리 조례 제%d호 상세 긴 조례 명칭 규정 내용 %d" % (i, i)})

    fake = Answer({"law": "주차장법", "complete": True, "rows": fake_rows})
    monkeypatch.setattr(kit.tree, "delegated", lambda *a, **k: fake)
    monkeypatch.setattr(kit.tree, "summary", lambda r: [{"위임구분": "시행령", "건수": 3}, {"위임구분": "조례", "건수": 199}])

    max_c, max_b = mcp_server.get_client_limits() # 기본값 6000자, 16000바이트
    text = _call_raw_text("law_tree", {"law": "주차장법", "limit": 200})

    # 1. 글자 및 바이트 상한 준수
    assert len(text) <= max_c
    assert len(text.encode("utf-8")) <= max_b

    # 2. 올바른 JSON 파싱 및 partial 이 dict
    parsed = json.loads(text)
    assert parsed["complete"] is False
    assert isinstance(parsed["partial"], dict), "partial 은 잘린 문자열이 아니라 유효한 JSON dict 객체여야 한다"

    # 3. 머리 요약(법령체계, counts)이 축소 후에도 그대로 남음
    assert "법령체계" in parsed
    assert "법령체계" in parsed["partial"]
    assert parsed["법령체계"]["시행령"] == ["테스트법시행령1", "테스트법시행령2", "테스트법시행령3"]
    assert parsed["법령체계"]["시행규칙"] == ["테스트법시행규칙1", "테스트법시행규칙2"]
    assert "counts" in parsed

    # 4. next_offset 이 실제 담긴 항목 수와 일치
    kept_rows = len(parsed["partial"]["rows"])
    assert kept_rows > 0
    assert parsed["next_offset"] == kept_rows
    assert parsed["partial"]["next_offset"] == kept_rows
    assert len(parsed["partial"]["조례"]) == len([r for r in parsed["partial"]["rows"] if "자치법규" in r.get("위임구분", "")])

    # 5. why 문장과 실제 내용 일치
    assert "법령체계(시행령·시행규칙·행정규칙)는 이 응답에 모두 들어 있다. 잘린 것은 조례 목록뿐" in parsed["why"]
    assert "상한 때문에 이번에는 1~%d 번째만 담았다. offset=%d 으로 이어 읽어라" % (kept_rows, kept_rows) in parsed["why"]

    # 6. _end 꼬리 일치
    assert parsed["_end"]["complete"] is False
    assert parsed["_end"]["count"] == parsed["count"]


def test_budget_fitting_law_search_axes_preserved_and_omitted_count(monkeypatch):
    """law_search 상한 축소 시 축별 요약이 남고, partial 이 dict 이며 각 축에 생략 수가 정확히 붙는다."""
    fake_search = {
        "query": "의료폐기물",
        "axes": {
            "law": Answer({"target": "law", "label": "법령", "complete": True, "total": 80, "count": 80,
                           "items": [{"제목": "의료폐기물 관리법령 제%d조 상세 긴 법령 제목 및 조문 설명 내용 %d" % (i, i)} for i in range(80)]}),
            "prec": Answer({"target": "prec", "label": "판례", "complete": True, "total": 100, "count": 100,
                            "items": [{"제목": "대법원 2020도%d 판결 의료폐기물 관리법 위반 상세 판시사항 긴 내용 %d" % (i, i)} for i in range(100)]}),
        },
        "incomplete": []
    }
    monkeypatch.setattr(kit.search, "across", lambda *a, **k: fake_search)

    max_c, max_b = mcp_server.get_client_limits()
    text = _call_raw_text("law_search", {"query": "의료폐기물"})

    # 1. 글자 및 바이트 상한 준수
    assert len(text) <= max_c
    assert len(text.encode("utf-8")) <= max_b

    # 2. 올바른 JSON 파싱 및 partial 이 dict
    parsed = json.loads(text)
    assert parsed["complete"] is False
    assert isinstance(parsed["partial"], dict)

    # 3. 머리 요약(축별)이 축소 후에도 온전히 남음
    assert "축별" in parsed
    assert parsed["축별"]["법령"]["total"] == 80
    assert parsed["축별"]["판례"]["total"] == 100

    # 4. 생략 수 정확성 검증
    prec_axis = parsed["partial"]["axes"]["prec"]
    law_axis = parsed["partial"]["axes"]["law"]
    if "생략" in prec_axis:
        omitted_prec = prec_axis["생략"]
        assert omitted_prec == 100 - len(prec_axis["items"])
        assert "상한 때문에 판례 에서 %d건을 뺐다. 범위를 좁혀 다시 물어라" % omitted_prec in parsed["why"]
    if "생략" in law_axis:
        omitted_law = law_axis["생략"]
        assert omitted_law == 80 - len(law_axis["items"])
        assert "상한 때문에 법령 에서 %d건을 뺐다. 범위를 좁혀 다시 물어라" % omitted_law in parsed["why"]

    # 5. _end 꼬리 일치
    assert parsed["_end"]["complete"] is False
    assert parsed["_end"]["count"] == parsed["count"]


def test_budget_fitting_law_term_counts_preserved_and_next_offset(monkeypatch):
    """law_term 상한 축소 시 counts 요약이 남고, partial 이 dict 이며 next_offset 과 why 가 일치한다."""
    fake_articles = [{"법령명": "폐기물관리법", "조": "제%d조" % i, "조문내용": "가" * 500} for i in range(50)]
    fake_term = Answer({
        "term": "폐기물",
        "found": True,
        "complete": True,
        "articles": fake_articles,
        "laws": ["폐기물관리법"],
    })
    monkeypatch.setattr(kit.terms, "articles", lambda *a, **k: fake_term)

    max_c, max_b = mcp_server.get_client_limits()
    text = _call_raw_text("law_term", {"term": "폐기물", "with_text": True})

    # 1. 글자 및 바이트 상한 준수
    assert len(text) <= max_c
    assert len(text.encode("utf-8")) <= max_b

    # 2. partial 이 dict
    parsed = json.loads(text)
    assert parsed["complete"] is False
    assert isinstance(parsed["partial"], dict)

    # 3. 머리 요약(counts) 남음
    assert "counts" in parsed
    assert "폐기물관리법" in parsed["counts"]

    # 4. next_offset 및 why 일치
    kept_count = len(parsed["partial"]["articles"])
    assert kept_count > 0
    assert parsed["next_offset"] == kept_count
    assert "상한 때문에 이번에는 1~%d 번째만 담았다. offset=%d 으로 이어 읽어라" % (kept_count, kept_count) in parsed["why"]
    assert parsed["_end"]["complete"] is False
    assert parsed["_end"]["count"] == parsed["count"]


def test_budget_fitting_fallback_to_string_truncation_when_header_alone_exceeds_cap():
    """머리 요약만으로도 상한을 넘는 극단적인 경우에만 최후의 수단으로 문자열 절단을 적용한다."""
    max_c, max_b = mcp_server.DEFAULT_LIMITS
    huge_header_data = {
        "법령체계": {"시행령": ["극도로 긴 시행령 이름 %d" % i * 10 for i in range(100)]},
        "rows": []
    }
    text = mcp_server.serialize_response(huge_header_data)
    assert len(text) <= max_c
    assert len(text.encode("utf-8")) <= max_b
    parsed = json.loads(text)
    assert parsed["complete"] is False
    assert isinstance(parsed["partial"], str) # 머리만으로 넘었으므로 문자열 절단 fallback
    assert "응답이 이 클라이언트 상한" in parsed["why"]
    assert parsed["_end"]["complete"] is False


# ---------------------------------------------------------------- 18. 과업 1 & 2 신규 검증 시험
def test_budget_fitting_law_term_counts_shrinking_and_consistent_next_offset(monkeypatch):
    """(과업 1) 머리의 counts 가 너무 커서 몸통이 0건이 되면 counts 를 상위 20개 + '그 밖 N개 법령' 으로 줄이고,
    이어 읽기 응답은 항상 1건 이상 담으며, why 와 next_offset 이 같은 값으로 일치한다."""
    # 81개 법령, 100건 조문 데이터 구성 (법령별 counts 가 상한을 압박하도록 현실적인 법령명 사용)
    fake_articles = []
    for i in range(1, 101):
        lname = "국토의 계획 및 이용에 관한 법률 시행령 제%03d호" % ((i % 81) + 1)
        fake_articles.append({
            "법령명": lname,
            "조": "제%d조" % i,
            "조문내용": "본문 내용 %d %s" % (i, "조문내용" * 100)
        })

    fake_term = Answer({
        "term": "건폐율",
        "found": True,
        "complete": True,
        "articles": fake_articles
    })
    monkeypatch.setattr(kit.terms, "articles", lambda *a, **k: fake_term)

    max_c, max_b = mcp_server.get_client_limits()
    text = _call_raw_text("law_term", {"term": "건폐율", "with_text": True})

    assert len(text) <= max_c
    assert len(text.encode("utf-8")) <= max_b

    parsed = json.loads(text)
    assert parsed["complete"] is False
    assert isinstance(parsed["partial"], dict)

    # (다) counts 가 상위 20개 + '그 밖 N개 법령' 으로 줄어듦
    assert "counts" in parsed
    assert len(parsed["counts"]) == 21
    assert any("그 밖" in k for k in parsed["counts"])
    assert "상한 때문에 법령별 건수(counts)는 상위 20개만 담았다" in parsed["why"]

    # (가) 이어 읽기 응답은 항상 1건 이상 담김
    kept_count = len(parsed["partial"]["articles"])
    assert kept_count >= 1

    # (나) why 의 offset 숫자와 next_offset 이 같은 값에서 생성됨
    assert parsed["next_offset"] == kept_count
    assert parsed["partial"]["next_offset"] == kept_count
    import re
    m = re.search(r"offset=(\d+)", parsed["why"])
    assert m is not None
    assert int(m.group(1)) == parsed["next_offset"]


def test_budget_fitting_always_keeps_at_least_one_item_under_various_limits(monkeypatch):
    """(과업 1) 여러 상한 값에서 law_term·law_tree 가 항상 1건 이상 담고, why offset 과 next_offset 이 일치한다."""
    import re
    fake_rows = []
    for i in range(1, 5):
        fake_rows.append({"위임구분": "시행령", "대상법령": "테스트시행령%d" % i})
    for i in range(1, 100):
        fake_rows.append({
            "위임구분": "위임자치법규",
            "대상법령": "서울특별시 조례 제%d호 매우 긴 자치법규 명칭 및 상세 규정 내용 %s" % (i, "가" * 150)
        })

    fake_tree = Answer({"law": "주차장법", "complete": True, "rows": fake_rows})
    monkeypatch.setattr(kit.tree, "delegated", lambda *a, **k: fake_tree)
    monkeypatch.setattr(kit.tree, "summary", lambda r: [{"위임구분": "시행령", "건수": 4}, {"위임구분": "조례", "건수": 99}])

    fake_articles = [{"법령명": "테스트법", "조": "제%d조" % i, "조문내용": "본문 내용 " + "나" * 200} for i in range(50)]
    fake_term = Answer({"term": "테스트", "found": True, "complete": True, "articles": fake_articles, "laws": ["테스트법"]})
    monkeypatch.setattr(kit.terms, "articles", lambda *a, **k: fake_term)

    # 여러 상한값 시험
    test_cases = [
        ("law_tree", {"law": "주차장법", "limit": 100}, "rows", [1200, 2500, 6000]),
        ("law_term", {"term": "테스트", "with_text": True, "limit": 50}, "articles", [800, 1500, 6000]),
    ]

    for tool_name, args, key, limits in test_cases:
        for max_c in limits:
            monkeypatch.setenv("KOREA_LAW_MCP_MAX_CHARS", str(max_c))
            monkeypatch.setenv("KOREA_LAW_MCP_MAX_BYTES", str(max_c * 3))

            text = _call_raw_text(tool_name, args)
            # 환경변수 상한은 최솟값(2000자/4000바이트) 아래로 내려가지 않는다 - 실제로 쓰이는 값으로 잰다.
            eff_c, eff_b = mcp_server.get_client_limits(None)
            assert len(text) <= eff_c
            assert len(text.encode("utf-8")) <= eff_b

            parsed = json.loads(text)
            if isinstance(parsed.get("partial"), dict):
                items = parsed["partial"][key]
                # (가) 항상 1건 이상 담음
                assert len(items) >= 1
                # (나) why 의 offset 과 next_offset 일치
                m = re.search(r"offset=(\d+)", parsed["why"])
                assert m is not None
                assert int(m.group(1)) == parsed["next_offset"]


def test_read_guide_in_first_200_chars_for_large_responses_and_absent_in_small_responses():
    """(과업 2) 직렬화 결과가 3,800바이트를 넘으면 머리에 '읽기안내'가 들어가고
    JSON 맨 앞 200자 안에 complete 와 읽기안내가 모두 위치하며,
    3,800바이트 이하 작은 응답에는 읽기안내가 없다."""
    # 1. 4,000바이트 넘는 가짜 응답
    fake_large = {"complete": True, "huge_data": "한글가나다라" * 500}
    text_large = mcp_server.serialize_response(fake_large)
    large_bytes = len(text_large.encode("utf-8"))
    assert large_bytes > 4000

    # 맨 앞 200자 안에 complete 와 읽기안내 모두 존재
    front_200 = text_large[:200]
    assert '"complete"' in front_200
    assert '"읽기안내"' in front_200

    parsed_large = json.loads(text_large)
    keys = list(parsed_large.keys())
    assert keys[0] == "complete"
    assert keys[1] == "읽기안내"
    assert parsed_large["읽기안내"] == "응답이 길어 클라이언트가 파일로 저장했을 수 있다. 저장됐다면 그 파일 전체를 읽은 뒤 답하라"

    # 2. 작은 응답 (<= 3,800바이트)
    fake_small = {"complete": True, "msg": "작은 정상 응답"}
    text_small = mcp_server.serialize_response(fake_small)
    small_bytes = len(text_small.encode("utf-8"))
    assert small_bytes <= 3800
    assert "읽기안내" not in text_small
    parsed_small = json.loads(text_small)
    assert "읽기안내" not in parsed_small



