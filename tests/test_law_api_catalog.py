# -*- coding: utf-8 -*-
"""법제처 API 목록 수집기.

이 목록의 값어치는 **무엇이 있는지 검색으로 알 수 있다**는 것이다.
그러려면 항목이 완전해야 하고, 불완전하면 불완전하다고 말해야 한다.

실측(2026-09-22): 처음 수집에서 16건이 응답필드 0개로 들어왔다.
"실패 0건" 이라 보고했지만 그 16건은 **응답 필드를 모르는 채** 성공으로
세어졌다. 캡션 없는 표를 파서가 못 읽은 것이었다.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import law_api_catalog as cat                   # noqa: E402

CATALOG = os.path.join(ROOT, "law_kit", "law_api_catalog.json")

_CAPTIONED = """
<table><caption>요청변수 안내</caption>
<tr><th>요청변수</th><th>값</th><th>설명</th></tr>
<tr><td>OC</td><td>string(필수)</td><td>인증값</td></tr></table>
<table><caption>출력 결과 필드 안내</caption>
<tr><th>필드</th><th>값</th><th>설명</th></tr>
<tr><td>target</td><td>string</td><td>대상</td></tr></table>
"""
#: 캡션이 없는 응답표 - 실제 detcListGuide 등 16건이 이 모양이다.
_UNCAPTIONED = """
<table><tr><th>요청변수</th><th>값</th><th>설명</th></tr>
<tr><td>OC</td><td>string(필수)</td><td>인증값</td></tr></table>
<table><tr><th>필드</th><th>값</th><th>설명</th></tr>
<tr><td>사건번호</td><td>string</td><td>사건번호</td></tr></table>
"""


def test_captioned_tables_are_parsed():
    params, fields, _ = cat._parse_tables(_CAPTIONED)
    assert [p[0] for p in params] == ["OC"]
    assert [f[0] for f in fields] == ["target"]


def test_uncaptioned_response_table_is_not_lost():
    """회귀 방지 - 캡션이 없으면 머리행으로 가른다."""
    params, fields, _ = cat._parse_tables(_UNCAPTIONED)
    assert [p[0] for p in params] == ["OC"], "요청변수를 놓쳤다"
    assert [f[0] for f in fields] == ["사건번호"], "응답필드를 놓쳤다"


def test_absent_response_table_is_marked_not_silently_empty():
    """'페이지에 없다' 와 '내가 못 읽었다' 는 다르다.

    구분하지 않으면 불완전한 항목이 완전한 것처럼 보인다.
    """
    only_params = "<table><caption>요청변수 안내</caption>" \
                  "<tr><th>요청변수</th><th>값</th></tr>" \
                  "<tr><td>OC</td><td>string</td></tr></table>"
    entry = cat.parse_guide(only_params)
    assert entry["response_fields"] == []
    assert entry["response_fields_absent"] is True

    entry2 = cat.parse_guide(_UNCAPTIONED)
    assert entry2["response_fields"]
    assert entry2["response_fields_absent"] is False


def test_guide_ids_are_extracted():
    html = "javascript:openApiGuide('aGuide') ... openApiGuide('bGuide')"
    assert cat.list_guide_ids(html) == ["aGuide", "bGuide"]


@pytest.mark.skipif(not os.path.exists(CATALOG), reason="아직 수집 안 함")
def test_harvested_catalog_is_complete():
    """실물 회귀 - 수집 결과가 조용히 불완전해지지 않게."""
    data = cat.load(CATALOG)
    entries = data["entries"]
    assert len(entries) >= 190, len(entries)
    assert not data["failed"], data["failed"]

    # 못 읽은 것(결함)은 0이어야 한다. 페이지에 없는 것(사실)은 허용한다.
    missed = [e for e in entries
              if not e["response_fields"] and not e.get("response_fields_absent")]
    assert not missed, [e["guide_id"] for e in missed]

    assert all(e["target"] for e in entries), "target 없는 항목이 있다"
    assert all(e["name"] for e in entries), "이름 없는 항목이 있다"
    assert all(e["request_params"] for e in entries), "요청변수 없는 항목이 있다"


@pytest.mark.skipif(not os.path.exists(CATALOG), reason="아직 수집 안 함")
def test_find_locates_the_audit_consulting_api():
    """이 목록을 만든 이유 - 검색으로 찾을 수 있어야 한다."""
    hits = cat.find("사전컨설팅", CATALOG)
    assert hits, "감사원 사전컨설팅 의견서 API 를 못 찾는다"
    assert any(h["target"] == "baiPvcs" for h in hits)
