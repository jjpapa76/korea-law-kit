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
        ("40조의3", "004003"),
        ("40의3", "004003"),
        ("제40조의 3", "004003"),
        ("제 40 조", "004000"),
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

    # 결함 반례 (1-가): 가지번호 0 거절 ("가지번호는 1 이상")
    for inv_zero in ["제40조의0", "40의0", "40조의0"]:
        with pytest.raises(ValueError) as excinfo:
            articles.parse_jo(inv_zero)
        assert "가지번호는 1 이상" in str(excinfo.value)

    # 결함 반례 (1-나): 모호한 구분자(-, ., _) 거절
    ambig_cases = [
        ("84-1", "84-1 은 모호하다 - 제84조의1 또는 제84조 제1항(hang=1)으로 적어라"),
        ("84.1", "84.1 은 모호하다 - 제84조의1 또는 제84조 제1항(hang=1)으로 적어라"),
        ("84_1", "84_1 은 모호하다 - 제84조의1 또는 제84조 제1항(hang=1)으로 적어라"),
        ("제84-1", "제84-1 은 모호하다 - 제84조의1 또는 제84조 제1항(hang=1)으로 적어라"),
    ]
    for inp, expected_msg in ambig_cases:
        with pytest.raises(ValueError) as excinfo:
            articles.parse_jo(inp)
        assert expected_msg in str(excinfo.value)


def test_parse_jo_and_hang_split():
    """조와 항이 함께 주어졌을 때 jo_code와 hang을 정확히 분리한다."""
    split_cases = [
        ("제84조제1항", "008400", "1"),
        ("제84조 제1항", "008400", "1"),
        ("84조 1항", "008400", "1"),
        ("84조제1항", "008400", "1"),
        ("제84조의2제1항", "008402", "1"),
        ("제40조의3 제2항", "004003", "2"),
        ("제84조 ①", "008400", "①"),
        ("제84조①", "008400", "①"),
        ("84조 ①", "008400", "①"),
    ]
    for inp, exp_jo, exp_hang in split_cases:
        jo_code, hang = articles.parse_jo_and_hang(inp)
        assert jo_code == exp_jo
        assert hang == exp_hang


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
        ("별표 1의2", (1, 2)),
        ("[별표 2]", (2, 0)),
        ("서식 1", (1, 0)),
        # 결함 반례 (2): 키워드가 있으면 그 키워드 바로 뒤의 번호만 읽는다 ("제6조 관련 별표 1" -> 1)
        ("제6조 관련 별표 1", (1, 0)),
        ("제6조 관련 별표 1의2", (1, 2)),
        ("제6조 관련 별지 제1호서식", (1, 0)),
    ]
    for inp, expected in cases:
        assert articles.parse_annex_num(inp) == expected

    invalid_cases = ["", "   ", None, "별표", "서식"]
    for inv in invalid_cases:
        with pytest.raises(ValueError):
            articles.parse_annex_num(inv)

    # 결함 반례 (2-모호): 키워드가 없는데 숫자가 둘 이상 흩어진 경우 거절
    ambig_annex_cases = ["6 관련 1", "6 1", "1 그리고 2", "제6조 1"]
    for inv in ambig_annex_cases:
        with pytest.raises(ValueError) as excinfo:
            articles.parse_annex_num(inv)
        assert "별표 번호가 모호하다 - '별표 1' 처럼 적어라" in str(excinfo.value)


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

    # MST 직접 지정 호출 (다양한 hang 형식: "1", "제1항", "①")
    for h_arg in ["1", "제1항", "①"]:
        res1 = articles.get_article("262791", "16", hang=h_arg)
        assert res1["complete"] is True
        assert len(res1["항"]) == 1
        assert res1["항"][0]["항번호"] == "①"
        assert res1["조문내용"] == "① 1항 내용"
        assert len(res1["호"]) == 1

    # 조와 항을 jo 문자열에 함께 지정 ("제16조제1항", "16조 1항")
    res_combined = articles.get_article("262791", "제16조제1항")
    assert res_combined["complete"] is True
    assert len(res_combined["항"]) == 1
    assert res_combined["항"][0]["항번호"] == "①"
    assert res_combined["조문내용"] == "① 1항 내용"

    # 없는 항 요청 (hang="3") -> 조문 전체를 주지 않고 안내 제공
    res2 = articles.get_article("262791", "16", hang="3")
    assert res2["complete"] is True
    assert res2.get("status") == "해당 항 없음"
    assert res2.get("안내") == "이 조에 그 항이 없다(있는 항: ①, ②)"
    assert res2.get("조문내용") == ""
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

    res = articles.get_annex_text("123456", "별지 1")
    assert res["complete"] is False
    assert "별표 본문 텍스트가 응답에 없다 - 첨부 파일(HWP/PDF)을 받아 확인해야 한다" in res["why"]
    assert "https://www.law.go.kr/LSW/flDownload.do?flSeq=169197743" in res["hwp"]
    assert "https://www.law.go.kr/LSW/flDownload.do?flSeq=169197745" in res["pdf"]

    # 본문 텍스트와 파일 링크가 둘 다 비어 있는 경우 -> why에 "파일 링크도 없다"
    annex_payload_no_links = {
        "기본정보": {"법령명_한글": "변호인 참여 등에 관한 규칙"},
        "별표": {
            "별표단위": [
                {
                    "별표번호": "0001",
                    "별표가지번호": "00",
                    "별표제목": "신청서",
                    "별표구분": "별지",
                    "별표내용": None,
                    "별표서식파일링크": "",
                    "별표서식PDF파일링크": ""
                }
            ]
        }
    }
    monkeypatch.setattr(kit.client, "call", lambda *a, **k: Result(
        "law", ok=True, complete=True, items=[annex_payload_no_links]
    ))
    res2 = articles.get_annex_text("123456", "별지 1")
    assert res2["complete"] is False
    assert "파일 링크도 없다" in res2["why"]


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


# ---------------------------------------------------------------- 11. annex 종류(별표/서식) 파싱
def test_parse_annex_spec_variants():
    """서식, 별지, 별표 종류 및 번호/가지번호가 정확히 분리된다."""
    cases = [
        ("1", ("별표", 1, 0)),
        ("별표 1", ("별표", 1, 0)),
        ("[별표 1]", ("별표", 1, 0)),
        ("0001", ("별표", 1, 0)),
        ("별표 2의3", ("별표", 2, 3)),
        ("1의2", ("별표", 1, 2)),
        ("서식 1", ("서식", 1, 0)),
        ("별지 1", ("서식", 1, 0)),
        ("별지 제1호서식", ("서식", 1, 0)),
        ("제1호서식", ("서식", 1, 0)),
        ("서식 2의3", ("서식", 2, 3)),
        ("별지 제2호의3서식", ("서식", 2, 3)),
    ]
    for inp, expected in cases:
        assert articles.parse_annex_spec(inp) == expected


# ---------------------------------------------------------------- 12. 조번호 불일치 방어
def test_law_article_mismatched_jo_code(monkeypatch):
    """요청한 조번호와 다른 조문이 반환되면 절대 complete:true 로 내지 않는다."""
    fake_payload = {
        "기본정보": {"법령명_한글": "테스트법", "시행일자": "20260101"},
        "조문": {
            "조문단위": [
                {
                    "조문번호": "2",
                    "조문가지번호": "0",
                    "조문여부": "조문",
                    "조문제목": "정의",
                    "조문내용": "제2조(정의)"
                }
            ]
        }
    }
    monkeypatch.setattr(kit.client, "call", lambda *a, **k: Result(
        "law", ok=True, complete=True, items=[fake_payload]
    ))
    res = articles.get_article("123456", "제1조")
    # 제1조를 요청했는데 제2조뿐이면 일치 조문이 없으므로 '조문 없음' 또는 complete: False
    assert res.get("조번호") == "" or res["complete"] is False


# ---------------------------------------------------------------- 13. 실제 법제처 픽스처 시험
import json
import os

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")

def _load_fixture(filename):
    path = os.path.join(FIXTURES_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def test_fixture_building_act_art1_and_art55(monkeypatch):
    """실제 건축법 응답: 제1조에서 장제목(총칙)이 아닌 실제 목적 조문을 고르고, 제55조도 정확히 추출한다."""
    art1_payload = _load_fixture("building_act_art1.json")
    art55_payload = _load_fixture("building_act_art55.json")

    def fake_call(target, service=False, **params):
        if params.get("JO") == "000100":
            return Result("law", ok=True, complete=True, items=art1_payload)
        elif params.get("JO") == "005500":
            return Result("law", ok=True, complete=True, items=art55_payload)
        return Result("law", ok=False, complete=False, error="not found")

    monkeypatch.setattr(kit.client, "call", fake_call)
    monkeypatch.setattr(kit.laws, "find", lambda name, **k: [
        {"법령명": "건축법", "MST": "273437", "현행": "현행", "찾은방법": "exact"}
    ])

    # 건축법 제1조
    res1 = articles.get_article("건축법", "제1조")
    assert res1["complete"] is True
    assert res1["조번호"] == "제1조"
    assert res1["조문제목"] == "목적"
    # 장 제목("제1장 총칙")이 아닌 실제 목적 내용이어야 함
    assert "제1장 총칙" not in res1["조문내용"]
    assert "건축물의 대지ㆍ구조ㆍ설비 기준 및 용도 등을 정하여" in res1["조문내용"]

    # 건축법 제55조
    res55 = articles.get_article("건축법", "제55조")
    assert res55["complete"] is True
    assert res55["조번호"] == "제55조"
    assert res55["조문제목"] == "건축물의 건폐율"
    assert "건폐율" in res55["조문내용"]


def test_fixture_civil_act_art1(monkeypatch):
    """실제 민법 응답: 제1편 총칙, 제1장 통칙을 건너뛰고 제1조(법원) 조문만 정확히 고른다."""
    civil_payload = _load_fixture("civil_act_art1.json")

    monkeypatch.setattr(kit.client, "call", lambda *a, **k: Result(
        "law", ok=True, complete=True, items=civil_payload
    ))
    monkeypatch.setattr(kit.laws, "find", lambda name, **k: [
        {"법령명": "민법", "MST": "284415", "현행": "현행", "찾은방법": "exact"}
    ])

    res = articles.get_article("민법", "제1조")
    assert res["complete"] is True
    assert res["조번호"] == "제1조"
    assert res["조문제목"] == "법원"
    assert "제1편 총칙" not in res["조문내용"]
    assert "제1장 통칙" not in res["조문내용"]
    assert "민사에 관하여 법률에 규정이 없으면 관습법에 의하고" in res["조문내용"]


def test_fixture_building_rule_annex_and_form(monkeypatch):
    """실제 건축법 시행규칙 응답: 서식 1은 서식 1을, 별표 1은 별표 1(삭제 안내)을 정확히 고른다."""
    rule_annex_payload = _load_fixture("building_rule_annex.json")

    monkeypatch.setattr(kit.client, "call", lambda *a, **k: Result(
        "law", ok=True, complete=True, items=rule_annex_payload
    ))
    monkeypatch.setattr(kit.laws, "find", lambda name, **k: [
        {"법령명": "건축법 시행규칙", "MST": "283727", "현행": "현행", "찾은방법": "exact"}
    ])

    # 서식 1 조회 -> 별표 1이 아닌 건축위원회 신청서(서식 1)
    res_form = articles.get_annex_text("건축법 시행규칙", "서식 1")
    assert res_form["complete"] is True
    assert res_form["별표구분"] == "서식"
    assert res_form["별표번호"] == "0001"
    assert "건축위원회" in res_form["별표제목"]
    assert "삭제" not in res_form["별표제목"]

    # 별표 1 조회 -> 삭제된 [별표 1] (complete: true + 머리 안내 '삭제된 별표다')
    res_annex = articles.get_annex_text("건축법 시행규칙", "별표 1")
    assert res_annex["complete"] is True
    assert res_annex["별표구분"] == "별표"
    assert res_annex["별표번호"] == "0001"
    assert res_annex.get("안내") == "삭제된 별표다"
    assert "삭제" in res_annex["별표제목"]

    # 없는 별표/서식 조회 -> complete: false + 그 법의 별표/서식 목록 요약
    res_none = articles.get_annex_text("건축법 시행규칙", "서식 999")
    assert res_none["complete"] is False
    assert "목록" in res_none
    assert len(res_none["목록"]) > 0


def test_fixture_urban_plan_act_art1_historic(monkeypatch):
    """실제 도시계획법(구법) 응답:
    - 마지막 판(폐지판본)에서 제1조가 없으면 complete:false + 구법:true + 사유 안내
    - 연혁본 조문이 있는 판본에서는 실제 목적 조문 추출 + 구법:true
    """
    urban_last_payload = _load_fixture("urban_plan_act_art1.json")
    urban_hist_payload = _load_fixture("urban_plan_act_art1_hist.json")

    # Case A: 마지막 판(57195) - 조문 없음
    monkeypatch.setattr(kit.laws, "find", lambda name, **k: [
        {"법령명": "도시계획법", "MST": "57195", "현행": "연혁", "찾은방법": "historic", "시행일자": "20030101"}
    ])
    monkeypatch.setattr(kit.client, "call", lambda *a, **k: Result(
        "eflaw", ok=True, complete=True, items=urban_last_payload
    ))

    res_last = articles.get_article("도시계획법", "제1조")
    assert res_last["complete"] is False
    assert res_last.get("구법") is True
    assert res_last.get("마지막_시행일자") == "20030101"
    assert "구법이라 현행본에 없다. 연혁본 조회에 실패했다" in res_last["why"]

    # Case B: 연혁본 조문이 있는 판(8776)
    monkeypatch.setattr(kit.laws, "find", lambda name, **k: [
        {"법령명": "도시계획법", "MST": "8776", "현행": "연혁", "찾은방법": "historic", "시행일자": "20000701"}
    ])
    monkeypatch.setattr(kit.client, "call", lambda *a, **k: Result(
        "eflaw", ok=True, complete=True, items=urban_hist_payload
    ))

    res_hist = articles.get_article("도시계획법", "제1조")
    assert res_hist["complete"] is True
    assert res_hist.get("구법") is True
    assert res_hist["조번호"] == "제1조"
    assert res_hist["조문제목"] == "목적"
    assert "제1장 총칙" not in res_hist["조문내용"]
    assert "도시계획의 수립 및 집행에 관하여" in res_hist["조문내용"]


def test_fixture_planning_act_art84_hang(monkeypatch):
    """실제 국토계획법 제84조 응답:
    - hang="1", hang="제1항", hang="①" -> 해당 항만 담김
    - "제84조제1항" 처럼 조와 항을 함께 넘겼을 때도 1항만 정상 추출됨
    - hang="99" (없는 항) -> 조문 전체를 주지 않고 안내 "이 조에 그 항이 없다(있는 항: ①, ②, ③)"
    """
    art84_payload = _load_fixture("planning_act_art84.json")

    monkeypatch.setattr(kit.laws, "find", lambda name, **k: [
        {"법령명": "국토의 계획 및 이용에 관한 법률", "MST": "284013", "현행": "현행", "찾은방법": "exact"}
    ])
    monkeypatch.setattr(kit.client, "call", lambda *a, **k: Result(
        "law", ok=True, complete=True, items=art84_payload
    ))

    # 1. 다양한 항 형식으로 제1항 조회
    for h_arg in ["1", "제1항", "①"]:
        res = articles.get_article("국토계획법", "제84조", hang=h_arg)
        assert res["complete"] is True
        assert len(res["항"]) == 1
        assert res["항"][0]["항번호"] == "①"
        assert "하나의 대지가 둘 이상의 용도지역" in res["조문내용"]
        assert len(res["항"][0]["호"]) == 2

    # 2. "제84조제1항" 조+항 분리
    res_comb = articles.get_article("국토계획법", "제84조제1항")
    assert res_comb["complete"] is True
    assert len(res_comb["항"]) == 1
    assert res_comb["항"][0]["항번호"] == "①"
    assert "하나의 대지가 둘 이상의 용도지역" in res_comb["조문내용"]

    # 3. hang="99" 없는 항 요청 -> 조문 전체를 complete:true로 주지 않고 안내 제공
    res_none = articles.get_article("국토계획법", "제84조", hang="99")
    assert res_none["complete"] is True
    assert res_none.get("status") == "해당 항 없음"
    assert res_none.get("안내") == "이 조에 그 항이 없다(있는 항: ①, ②, ③)"
    assert res_none.get("조문내용") == ""
    assert len(res_none["항"]) == 0




def test_annex_multiple_numbers_rejected():
    """'별표 1, 2' 의 뒤 번호를 버리고 별표 1 만 complete 로 주지 않는다."""
    for bad in ["별표 1, 2", "별표 1 및 별표 2", "서식 1, 3"]:
        with pytest.raises(ValueError):
            articles.parse_annex_spec(bad)
    assert articles.parse_annex_spec("제4조의3 관련 별표 1") == ("별표", 1, 0)
    assert articles.parse_annex_spec("별지 제2호 서식") == ("서식", 2, 0)
    assert articles.parse_annex_spec("[별표 1의2]") == ("별표", 1, 2)


def test_hang_with_ho_or_zero_rejected():
    """'제2항제1호' 의 호를 버리고 항 전체를 주지 않는다 - 망에 나가기 전에 거절."""
    for bad in ["제2항제1호", "2항1호", "0", "제0항"]:
        with pytest.raises(ValueError):
            articles.get_article("건축법", "제11조", hang=bad)
