# -*- coding: utf-8 -*-
"""law_kit.articles 모듈 시험 (law_article, law_annex_text).

망에 나가지 않는다 (monkeypatch 로 가짜 응답을 주입).
테스트 항목:
    1. jo 변환 여러 형태 (단위 및 에러)
    2. 별표 번호 파싱 여러 형태
    3. 조문 하나만 평평한 텍스트 필드로 정상 추출
    4. 항(hang) 필터링 및 없는 항 처리
    5. 법령명 후보 여러 개일 때 고르지 않고 complete: false + candidates
    6. 조문 없음(complete: true)과 조회 실패(complete: false)의 구분
    7. 별표 텍스트 정상 추출
    8. 텍스트 없는 별표의 complete: false 및 HWP/PDF 링크 제공
    9. 법률에 별표 없을 때 하위법령(시행령·시행규칙) 후보 안내
    10. MCP 서버 dispatch_tool 연계 시험
"""
import pytest
import law_kit as kit
from law_kit import articles, mcp_server
from law_kit.client import Result


# ---------------------------------------------------------------- 1. jo 변환 여러 형태
def test_parse_jo_variants():
    """다양한 조 번호 표기를 법제처 JO 6자리 코드로 변환한다."""
    cases = [
        ("제84조", "008400"),
        ("84", "008400"),
        ("제40조의3", "004003"),
        ("40의3", "004003"),
        ("제40조의 3", "004003"),
        ("제1조", "000100"),
        ("1", "000100"),
        ("004003", "004003"),
        ("008400", "008400"),
        ("제1234조의56", "123456"),
    ]
    for inp, expected in cases:
        assert articles.parse_jo(inp) == expected

    invalid_cases = [
        "", "   ", None, "제조", "abc", "제조의3",
        0, -1, 10000, "40의100", "1조의2조",
    ]
    for inv in invalid_cases:
        with pytest.raises(ValueError):
            articles.parse_jo(inv)


# ---------------------------------------------------------------- 2. annex 파싱 여러 형태
def test_parse_annex_variants():
    """다양한 별표 번호 표기에서 (번호, 가지번호)를 추출한다."""
    cases = [
        ("1", (1, 0)),
        ("별표 1", (1, 0)),
        ("0001", (1, 0)),
        ("별표 2", (2, 0)),
        ("2의3", (2, 3)),
        ("별표 2의3", (2, 3)),
        ("별표 2의 3", (2, 3)),
        ("1의2", (1, 2)),
        ("별지 1", (1, 0)),
        ("별지 제1호서식", (1, 0)),
    ]
    for inp, expected in cases:
        assert articles.parse_annex_num(inp) == expected

    invalid_cases = ["", "   ", None, "별표", "서식"]
    for inv in invalid_cases:
        with pytest.raises(ValueError):
            articles.parse_annex_num(inv)


# ---------------------------------------------------------------- 3. 조문 하나만 담김
def test_law_article_single_result(monkeypatch):
    """client.call 응답에서 조문 하나만 받아 평평한 텍스트 필드로 제공한다."""
    fake_payload = {
        "기본정보": {
            "법령명_한글": "행정소송법",
            "법령ID": "001218",
            "시행일자": "20260512",
        },
        "조문": {
            "조문단위": {
                "조문번호": "16",
                "조문제목": "제3자의 소송참가",
                "조문내용": "제16조(제3자의 소송참가)",
                "항": [
                    {
                        "항번호": "①",
                        "항내용": "①법원은 소송의 결과에 따라 권리 또는 이익의 침해를 받을 제3자가 있는 경우 참가시킬 수 있다.",
                        "호": [
                            {"호번호": "1.", "호내용": "1. 신청에 의한 경우"},
                            {"호번호": "2.", "호내용": "2. 직권에 의한 경우"}
                        ]
                    },
                    {
                        "항번호": "②",
                        "항내용": "②법원이 결정을 하고자 할 때에는 의견을 들어야 한다."
                    }
                ]
            }
        }
    }

    # laws.find 가 단일 MST 반환
    monkeypatch.setattr(kit.laws, "find", lambda name, **k: [
        {"법령명": "행정소송법", "MST": "262791", "현행": "현행", "찾은방법": "exact"}
    ])
    # client.call 이 fake_payload 를 items 로 가진 Result 반환
    monkeypatch.setattr(kit.client, "call", lambda target, service=False, **params: Result(
        "law", ok=True, complete=True, items=[fake_payload]
    ))

    res = articles.get_article("행정소송법", "제16조")
    assert res["complete"] is True
    assert res["law"] == "행정소송법"
    assert res["MST"] == "262791"
    assert res["시행일자"] == "20260512"
    assert res["조번호"] == "제16조"
    assert res["조문제목"] == "제3자의 소송참가"
    assert res["조문내용"] == "제16조(제3자의 소송참가)"
    assert len(res["항"]) == 2
    assert res["항"][0]["항번호"] == "①"
    assert len(res["항"][0]["호"]) == 2
    assert len(res["호"]) == 2
    assert res["호"][0]["호번호"] == "1."


# ---------------------------------------------------------------- 4. 항(hang) 필터링
def test_law_article_with_hang(monkeypatch):
    """hang 인자가 주어지면 해당 항만 걸러내고, 없는 항은 '해당 항 없음'으로 표시한다."""
    fake_payload = {
        "기본정보": {"법령명_한글": "행정소송법", "시행일자": "20260512"},
        "조문": {
            "조문단위": {
                "조문번호": "16",
                "조문제목": "제3자의 소송참가",
                "조문내용": "제16조(제3자의 소송참가)",
                "항": [
                    {"항번호": "①", "항내용": "① 1항 내용", "호": [{"호번호": "1.", "호내용": "1호"}]},
                    {"항번호": "②", "항내용": "② 2항 내용"}
                ]
            }
        }
    }
    monkeypatch.setattr(kit.client, "call", lambda *a, **k: Result(
        "law", ok=True, complete=True, items=[fake_payload]
    ))

    # MST 직접 지정 호출
    res1 = articles.get_article("262791", "16", hang="1")
    assert res1["complete"] is True
    assert len(res1["항"]) == 1
    assert res1["항"][0]["항번호"] == "①"
    assert len(res1["호"]) == 1

    # 없는 항 요청
    res2 = articles.get_article("262791", "16", hang="3")
    assert res2["complete"] is True
    assert res2.get("status") == "해당 항 없음"
    assert len(res2["항"]) == 0


# ---------------------------------------------------------------- 5. 후보 여러 개일 때
def test_law_article_multiple_candidates(monkeypatch):
    """법령명이 여러 개로 걸리면 고르지 않고 complete: false 와 candidates 후보 목록을 돌려준다."""
    candidates = [
        {"법령명": "주차장법", "MST": "1001", "현행": "현행"},
        {"법령명": "주차장법 시행령", "MST": "1002", "현행": "현행"},
        {"법령명": "주차장법 시행규칙", "MST": "1003", "현행": "현행"},
    ]
    monkeypatch.setattr(kit.laws, "find", lambda name, **k: candidates)

    res = articles.get_article("주차장", "제1조")
    assert res["complete"] is False
    assert "후보가 여러 개" in res["why"]
    assert len(res["candidates"]) == 3


# ---------------------------------------------------------------- 6. 조문 없음 vs 조회 실패
def test_law_article_not_found_vs_failure(monkeypatch):
    """조문 없음(complete: true)과 원 응답 판독 불가/실패(complete: false)를 명확히 구분한다."""
    # (1) 조문 없음: 기본정보는 정상이지만 조문 단위가 없는 경우 -> complete: True, status="조문 없음"
    empty_jo_payload = {
        "기본정보": {"법령명_한글": "공공감사에 관한 법률", "시행일자": "20260102"},
        # 조문 키 자체가 없거나 조문단위가 없음
    }
    monkeypatch.setattr(kit.client, "call", lambda *a, **k: Result(
        "law", ok=True, complete=True, items=[empty_jo_payload]
    ))
    res_empty = articles.get_article("123456", "99")
    assert res_empty["complete"] is True
    assert res_empty["status"] == "조문 없음"
    assert "조문(99)이 없습니다" in res_empty["why"]
    assert res_empty["조번호"] == ""

    # (2) 조회 실패: client.call 이 ok=False 인 경우 -> complete: False
    monkeypatch.setattr(kit.client, "call", lambda *a, **k: Result(
        "law", ok=False, complete=False, error="네트워크 단절"
    ))
    res_fail = articles.get_article("123456", "99")
    assert res_fail["complete"] is False
    assert "조회 실패" in res_fail["why"]

    # (3) 원 응답을 알아보지 못한 경우 (items 가 비정상) -> complete: False
    monkeypatch.setattr(kit.client, "call", lambda *a, **k: Result(
        "law", ok=True, complete=True, items=["정의되지 않은 이상한 문자열"]
    ))
    res_unknown = articles.get_article("123456", "99")
    assert res_unknown["complete"] is False
    assert "원 응답을 알아보지 못했다" in res_unknown["why"]


# ---------------------------------------------------------------- 7. 별표 텍스트 추출
def test_law_annex_text_extraction(monkeypatch):
    """법 본문 응답에서 특정 별표의 본문 텍스트를 정상 추출한다."""
    annex_payload = {
        "기본정보": {"법령명_한글": "건축법 시행령"},
        "별표": {
            "별표단위": [
                {
                    "별표번호": "0001",
                    "별표가지번호": "00",
                    "별표제목": "용도별 건축물의 종류",
                    "별표구분": "별표",
                    "별표내용": [
                        ["■ 건축법 시행령 [별표 1]", "용도별 건축물의 종류(제3조의5 관련)"],
                        ["1. 단독주택", "2. 공동주택"]
                    ],
                    "별표서식파일링크": "/LSW/flDownload.do?flSeq=111",
                    "별표서식PDF파일링크": "/LSW/flDownload.do?flSeq=222"
                },
                {
                    "별표번호": "0002",
                    "별표가지번호": "00",
                    "별표제목": "대지의 공지 기준",
                    "별표구분": "별표",
                    "별표내용": "■ 건축법 시행령 [별표 2] 대지의 공지 기준"
                }
            ]
        }
    }
    monkeypatch.setattr(kit.client, "call", lambda *a, **k: Result(
        "law", ok=True, complete=True, items=[annex_payload]
    ))

    # 별표 1 조회 (중첩 리스트 텍스트 평탄화)
    res = articles.get_annex_text("123456", "1")
    assert res["complete"] is True
    assert res["별표번호"] == "0001"
    assert res["별표제목"] == "용도별 건축물의 종류"
    assert "단독주택" in res["별표내용"]
    assert "공동주택" in res["별표내용"]
    assert "https://www.law.go.kr/LSW/flDownload.do?flSeq=111" in res["hwp"]

    # 별표 2 조회 (단일 문자열)
    res2 = articles.get_annex_text("123456", "별표 2")
    assert res2["complete"] is True
    assert res2["별표제목"] == "대지의 공지 기준"
    assert "대지의 공지 기준" in res2["별표내용"]


# ---------------------------------------------------------------- 8. 텍스트 없는 별표 complete: false 및 링크
def test_law_annex_text_empty_with_links(monkeypatch):
    """별표 텍스트가 비었거나 그림·첨부만 있으면 complete: false 와 파일 링크를 준다."""
    annex_payload = {
        "기본정보": {"법령명_한글": "변호인 참여 등에 관한 규칙"},
        "별표": {
            "별표단위": [
                {
                    "별표번호": "0001",
                    "별표가지번호": "00",
                    "별표제목": "변호인 참여 신청서",
                    "별표구분": "별지",
                    "별표내용": None,  # 본문 텍스트 비어 있음
                    "별표서식파일링크": "/LSW/flDownload.do?flSeq=169197743",
                    "별표서식PDF파일링크": "/LSW/flDownload.do?flSeq=169197745"
                }
            ]
        }
    }
    monkeypatch.setattr(kit.client, "call", lambda *a, **k: Result(
        "law", ok=True, complete=True, items=[annex_payload]
    ))

    res = articles.get_annex_text("123456", "1")
    assert res["complete"] is False
    assert "별표 본문 텍스트가 응답에 없다 - 첨부 파일(HWP/PDF)을 받아 확인해야 한다" in res["why"]
    assert "https://www.law.go.kr/LSW/flDownload.do?flSeq=169197743" in res["hwp"]
    assert "https://www.law.go.kr/LSW/flDownload.do?flSeq=169197745" in res["pdf"]


# ---------------------------------------------------------------- 9. 법률에 별표 없을 때 하위법령 안내
def test_law_annex_subordinate_candidates(monkeypatch):
    """법률에 해당 별표가 없으면 시행령·시행규칙 등 하위법령 후보를 안내한다."""
    # 법률 본문에는 별표 키가 없음
    law_payload = {
        "기본정보": {"법령명_한글": "건축법"},
        "조문": {"조문단위": {"조문번호": "1"}}
    }
    monkeypatch.setattr(kit.client, "call", lambda *a, **k: Result(
        "law", ok=True, complete=True, items=[law_payload]
    ))
    monkeypatch.setattr(kit.laws, "find", lambda name, **k: [
        {"법령명": name.strip(), "MST": "9999", "현행": "현행"}
    ] if "시행령" in name or "시행규칙" in name else [
        {"법령명": "건축법", "MST": "123456", "현행": "현행"}
    ])

    res = articles.get_annex_text("건축법", "1")
    assert res["complete"] is False
    assert "하위법령에 별표가 규정되어 있을 수 있습니다" in res["why"]
    assert any("시행령" in c["법령명"] for c in res["candidates"])


# ---------------------------------------------------------------- 10. MCP 서버 dispatch_tool 연계
def test_mcp_server_dispatch_article_tools(monkeypatch):
    """MCP 서버 dispatch_tool 을 통해 law_article 과 law_annex_text 가 정상 동작한다."""
    fake_article_payload = {
        "기본정보": {"법령명_한글": "테스트법", "시행일자": "20260101"},
        "조문": {
            "조문단위": {
                "조문번호": "1",
                "조문제목": "목적",
                "조문내용": "제1조(목적) 이 법은 테스트를 목적으로 한다."
            }
        }
    }
    monkeypatch.setattr(kit.client, "call", lambda *a, **k: Result(
        "law", ok=True, complete=True, items=[fake_article_payload]
    ))

    # law_article 호출
    art_res = mcp_server.dispatch_tool("law_article", {"law": "123456", "jo": "제1조"})
    assert art_res["complete"] is True
    assert art_res["조번호"] == "제1조"
    assert art_res["조문제목"] == "목적"

    # law_annex_text 호출
    fake_annex_payload = {
        "기본정보": {"법령명_한글": "테스트법"},
        "별표": {
            "별표단위": [
                {
                    "별표번호": "0001",
                    "별표가지번호": "00",
                    "별표제목": "서식1",
                    "별표내용": "서식 내용입니다"
                }
            ]
        }
    }
    monkeypatch.setattr(kit.client, "call", lambda *a, **k: Result(
        "law", ok=True, complete=True, items=[fake_annex_payload]
    ))
    annex_res = mcp_server.dispatch_tool("law_annex_text", {"law": "123456", "annex": "1"})
    assert annex_res["complete"] is True
    assert annex_res["별표내용"] == "서식 내용입니다"
