# -*- coding: utf-8 -*-
"""law_kit 이 **모르는 것을 모른다고 말하는지**를 시험한다.

여기 있는 시험은 대부분 "잘 찾는가" 가 아니라 "못 찾았을 때 못 찾았다고
하는가" 를 본다. 잘 찾는 것은 실물로 확인하면 되지만, 거짓 보증은 실물을
봐도 안 보인다 - 그럴듯한 답이 나오기 때문이다.

망에 나가지 않는다. 실제 응답에서 떠온 모양만 쓴다.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest                                   # noqa: E402

from law_kit import annex, catalog, client, history, laws, search  # noqa: E402
from law_kit import shape, terms, tree          # noqa: E402


# ---------------------------------------------------------------- shape

def test_parallel_lists_become_separate_rows():
    """법제처는 한 자리에 값 여러 개를 평행 리스트로 넣는다.

    펴지 않으면 첫 값만 보고 나머지를 조용히 버린다.
    """
    rows = shape.rows({"제목": ["건설산업기본법", "전기공사업법"],
                       "구분": ["인용법령", "인용법령"],
                       "공통": "같음"})
    assert len(rows) == 2
    assert rows[1]["제목"] == "전기공사업법"
    assert rows[0]["공통"] == rows[1]["공통"] == "같음"


def test_short_parallel_list_is_left_empty_not_repeated():
    """길이가 모자라면 마지막 값을 되쓰지 않는다. 없는 값을 지어내면 거짓이다."""
    rows = shape.rows({"가": [1, 2, 3], "나": ["x"]})
    assert [r["나"] for r in rows] == ["x", None, None]


def test_single_value_stays_one_row():
    assert shape.rows({"가": 1, "나": "둘"}) == [{"가": 1, "나": "둘"}]


# ---------------------------------------------------------------- client

def test_meta_dict_is_not_mistaken_for_a_result():
    """목록이 있으면 목록이 답이다. 앞에 놓인 메타 딕셔너리가 아니라.

    이전 구현은 처음 만난 dict 를 반환해서 진짜 목록 50건을 메타 1건으로
    바꿔 놓았다.
    """
    payload = {"LawSearch": {"totalCnt": 2, "searchInfo": {"a": 1},
                             "law": [{"법령명한글": "가"}, {"법령명한글": "나"}]}}
    total, items = client.unwrap(payload)
    assert total == 2 and len(items) == 2


def test_single_record_still_comes_back():
    payload = {"Svc": {"검색결과개수": 1, "법령용어": {"법령용어명": "가"}}}
    _total, items = client.unwrap(payload)
    assert len(items) == 1 and items[0]["법령용어명"] == "가"


def test_result_knows_the_difference_between_empty_and_unknown():
    """0건과 미확인은 다르다. 이 구분이 이 꾸러미의 전부다."""
    empty = client.Result("law", items=[])
    unknown = client.Result("law", ok=False, error="TimeoutError")
    assert empty.complete and not empty.why_incomplete()
    assert not unknown.complete and "실패" in unknown.why_incomplete()


def test_truncation_is_confessed():
    cut = client.Result("law", items=[1] * 100, total=5000, truncated=True,
                        complete=False)
    assert not cut.complete
    assert "5000" in cut.why_incomplete() and "100" in cut.why_incomplete()


def _fake_pages(pages, fail_at=None):
    calls = {"n": 0}

    def fake(target, service=False, ttl=0, **params):
        calls["n"] += 1
        page = int(params.get("page", 1))
        if fail_at and page == fail_at:
            return client.Result(target, ok=False, complete=False,
                                 error="TimeoutError")
        items = pages[page - 1] if page <= len(pages) else []
        return client.Result(target, items=items,
                             total=sum(len(p) for p in pages), cached=True)
    return fake, calls


def test_paging_that_breaks_midway_is_not_called_complete(monkeypatch):
    """2페이지에서 끊겼는데 1페이지치를 '전부' 라고 주면 안 된다."""
    fake, _ = _fake_pages([[{"a": i} for i in range(100)],
                           [{"a": i} for i in range(100)]], fail_at=2)
    monkeypatch.setattr(client, "call", fake)
    result = client.call_all("law", query="x", page_size=100, ttl=0)
    assert len(result.partial) == 100
    assert not result.complete and result.ok is False
    assert "TimeoutError" in result.why_incomplete()


def test_paging_stops_cleanly_at_the_end(monkeypatch):
    fake, calls = _fake_pages([[{"a": i} for i in range(100)], [{"a": 1}]])
    monkeypatch.setattr(client, "call", fake)
    result = client.call_all("law", query="x", page_size=100, ttl=0)
    assert result.complete and len(result.items) == 101 and calls["n"] == 2


def test_page_cap_is_reported_as_truncation(monkeypatch):
    # 페이지마다 내용이 달라야 한다. 똑같이 만들면 중복 페이지 감지기가
    # 먼저 걸려서 상한 시험이 되지 않는다.
    full = [[{"a": p * 100 + i} for i in range(100)] for p in range(5)]
    fake, _ = _fake_pages(full)
    monkeypatch.setattr(client, "call", fake)
    result = client.call_all("law", query="x", page_size=100, max_pages=2,
                             ttl=0)
    assert result.truncated and not result.complete


def test_cache_dir_default_is_user_home_cache(monkeypatch, tmp_path):
    """LAW_KIT_CACHE 가 없을 때 기본값은 사용자 홈의 .cache/korea-law-kit 이다."""
    import importlib
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.delenv("LAW_KIT_CACHE", raising=False)
    if sys.platform == "win32":
        try:
            import winreg
            orig_query = winreg.QueryValueEx

            def fake_query(key, name):
                if name == "LAW_KIT_CACHE":
                    raise FileNotFoundError()
                return orig_query(key, name)

            monkeypatch.setattr(winreg, "QueryValueEx", fake_query)
        except ImportError:
            pass
    importlib.reload(client)
    try:
        expected = os.path.normpath(os.path.expanduser(
            os.path.join("~", ".cache", "korea-law-kit")))
        assert os.path.normpath(client.CACHE_DIR) == expected
        assert os.path.normpath(client.CACHE_DIR) == os.path.normpath(
            str(fake_home / ".cache" / "korea-law-kit"))
    finally:
        importlib.reload(client)
        importlib.reload(laws)


def test_cache_dir_honors_law_kit_cache_setting(monkeypatch, tmp_path):
    """LAW_KIT_CACHE 가 있으면 그 경로를 우선한다."""
    import importlib
    custom_dir = str(tmp_path / "custom_cache")
    monkeypatch.setenv("LAW_KIT_CACHE", custom_dir)
    importlib.reload(client)
    try:
        assert client.CACHE_DIR == custom_dir
    finally:
        importlib.reload(client)
        importlib.reload(laws)


# ---------------------------------------------------------------- search

def test_display_cap_is_not_reported_as_complete(monkeypatch):
    """'총 257건 중 앞 20건' 을 '20건 확인' 으로 말하면 안 된다.

    실측에서 이 거짓말이 실제로 나왔다.
    """
    monkeypatch.setattr(client, "call", lambda *a, **k: client.Result(
        "law", items=[{"법령명한글": str(i)} for i in range(20)], total=257))
    axis = search.one("law", "의료폐기물", display=20)
    assert axis["count"] == 20
    assert not axis["complete"]
    assert "257" in axis["note"]


def test_full_page_equal_to_total_is_complete(monkeypatch):
    monkeypatch.setattr(client, "call", lambda *a, **k: client.Result(
        "law", items=[{"법령명한글": str(i)} for i in range(3)], total=3))
    assert search.one("law", "x", display=20)["complete"]


def test_missing_title_is_left_blank_not_guessed():
    """제목 칸이 없으면 아무 칸이나 집어 제목인 척하지 않는다."""
    assert search._title({"엉뚱한칸": "값"}) == ""
    assert search._title({"사건명": "대법원 판결"}) == "대법원 판결"


def test_incomplete_axes_are_listed_for_the_caller(monkeypatch):
    def fake(target, service=False, ttl=0, **params):
        if target == "prec":
            return client.Result(target, ok=False, error="TimeoutError")
        return client.Result(target, items=[], total=0)
    monkeypatch.setattr(client, "call", fake)
    result = search.across("x", display=5)
    assert "prec" in result["incomplete"]
    assert "없다" in search.format_report(result)


# ---------------------------------------------------------------- terms

_TERM_PAYLOAD = {
    "법령용어명": "도시혁신구역",
    "연계법령": [
        {"법령명": "국토의 계획 및 이용에 관한 법률", "조번호": "0040",
         "조가지번호": "03", "조문내용": "제40조의3(도시혁신구역의 지정 등)",
         "용어구분": "선정용어"},
        {"법령명": "국토의 계획 및 이용에 관한 법률 시행령", "조번호": "0025",
         "조가지번호": "00", "조문내용": "제25조(도시·군관리계획의 결정)",
         "용어구분": "선정용어"},
        {"법령명": "국토의 계획 및 이용에 관한 법률 시행령", "조번호": "0025",
         "조가지번호": "00", "조문내용": "", "용어구분": "선정용어"},
    ],
}


def test_repeated_article_is_merged_with_a_count(monkeypatch):
    """같은 조문이 용어 출현 횟수만큼 온다. 합치되 횟수는 남긴다."""
    monkeypatch.setattr(client, "call", lambda *a, **k: client.Result(
        "lstrmRltJo", items=[_TERM_PAYLOAD]))
    found = terms.articles("도시혁신구역")
    assert len(found["articles"]) == 2
    merged = [a for a in found["articles"] if a["조"] == "제25조"][0]
    assert merged["출현횟수"] == 2
    assert merged["조문내용"], "빈 행이 본문 있는 행을 덮어쓰면 안 된다"


def test_article_label_is_built_from_padded_numbers(monkeypatch):
    monkeypatch.setattr(client, "call", lambda *a, **k: client.Result(
        "lstrmRltJo", items=[_TERM_PAYLOAD]))
    labels = {a["조"] for a in terms.articles("x")["articles"]}
    assert labels == {"제40조의3", "제25조"}


def test_unregistered_term_is_not_reported_as_no_articles(monkeypatch):
    """'용어로 등재 안 됨' 과 '관련 조문 없음' 은 다르다."""
    monkeypatch.setattr(client, "call", lambda *a, **k: client.Result(
        "lstrmRltJo", items=[]))
    found = terms.articles("없는낱말")
    assert found["found"] is False
    assert found["complete"] is True
    assert "없다는 뜻이 아니다" in found["note"]


def test_failed_term_lookup_is_not_silently_empty(monkeypatch):
    monkeypatch.setattr(client, "call", lambda *a, **k: client.Result(
        "lstrmRltJo", ok=False, error="TimeoutError"))
    found = terms.articles("x")
    assert found["complete"] is False and found["error"]


# ---------------------------------------------------------------- tree

_ORDINANCE_BLOCK = {
    "조정보": {"조문번호": "6", "조문제목": "주차장설비기준"},
    "위임정보": [
        {"위임구분": "시행령", "위임법령제목": "주차장법 시행령",
         "위임법령일련번호": "111",
         "위임법령조문정보": {"위임법령조문번호": "6", "조항호목": "제6조제1항",
                             "위임법령조문제목": "주차전용건축물의 주차면적비율",
                             "라인텍스트": "대통령령으로 정하는"}},
        {"위임구분": "위임자치법규",
         "위임자치법규조문정보": [
             {"위임자치법규제목": "서울특별시 광진구 주차장 설치 및 관리 조례",
              "위임자치법규일련번호": "2112919", "라인텍스트": "조례로 정하는 지역"},
             {"위임자치법규제목": "부산광역시 수영구 주차장 설치 및 관리 조례",
              "위임자치법규일련번호": "2098527", "라인텍스트": "조례로 정하는 비율"}]},
        {"위임법령조문정보": {"위임법령조문번호": "19", "조항호목": "제19조의11",
                             "위임법령조문제목": "기계식주차장의 사용검사 등",
                             "라인텍스트": "국토교통부령으로 정하는"}},
    ],
}


def _stub_delegated(monkeypatch):
    monkeypatch.setattr(client, "call", lambda *a, **k: client.Result(
        "lsDelegated", items=[{"법령정보": {"법령명": "주차장법", "법령ID": "001902"},
                               "위임조문정보": [_ORDINANCE_BLOCK]}]))
    monkeypatch.setattr(laws, "resolve", lambda name: {"ID": "001902",
                                                       "법령명": "주차장법"})


def test_ordinances_keep_their_names(monkeypatch):
    """조례는 칸 이름이 다르다. 그걸 모르면 4,840건이 이름 없이 떨어진다.

    주차장법 실측에서 실제로 그렇게 됐다. 이름을 못 읽은 행은 쓸모가 없고,
    쓰는 쪽에서는 '조례가 이름이 없구나' 로 보이지 '내가 잘못 읽었구나'
    로는 안 보인다 - 그래서 시험으로 못 박는다.
    """
    _stub_delegated(monkeypatch)
    branch = tree.delegated("주차장법")
    ordinances = branch["groups"]["위임자치법규"]
    assert len(ordinances) == 2
    assert all(row["대상법령"] for row in ordinances)
    assert "광진구" in ordinances[0]["대상법령"]


def test_article_title_is_never_used_as_a_law_name(monkeypatch):
    """'주차전용건축물의 주차면적비율' 은 법 이름이 아니라 조문 제목이다."""
    _stub_delegated(monkeypatch)
    branch = tree.delegated("주차장법")
    decree = branch["groups"]["시행령"][0]
    assert decree["대상법령"] == "주차장법 시행령"
    assert decree["대상조제목"] == "주차전용건축물의 주차면적비율"


def test_unnamed_delegation_is_labelled_not_invented(monkeypatch):
    """대상 법령이 아예 없는 블록이 있다. 이름을 지어 붙이지 않는다."""
    _stub_delegated(monkeypatch)
    branch = tree.delegated("주차장법")
    assert "자체·미지정" in branch["groups"]
    assert branch["groups"]["자체·미지정"][0]["대상법령"] == ""


def test_citations_are_not_counted_as_subordinate(monkeypatch):
    """인용법령은 아래가 아니라 옆이다."""
    _stub_delegated(monkeypatch)
    names = tree.subordinate_laws(tree.delegated("주차장법"))
    assert "주차장법 시행령" in names
    assert len(names) == 3


def test_unknown_law_name_fails_loudly(monkeypatch):
    monkeypatch.setattr(laws, "resolve", lambda name: None)
    branch = tree.delegated("없는법")
    assert branch["ok"] is False and "찾지 못했다" in branch["note"]


# ---------------------------------------------------------------- annex

_ANNEX_ITEMS = [
    {"관련법령명": "건축법 시행규칙", "별표명": "(임시)사용승인 신청서",
     "별표번호": "001700", "별표서식파일링크": "/LSW/flDownload.do?flSeq=1",
     "별표서식PDF파일링크": "/LSW/flDownload.do?flSeq=2"},
    {"관련법령명": "건축법", "별표명": "가목 기준", "별표번호": "000100"},
    {"관련법령명": "건축법대장법", "별표명": "남의 법", "별표번호": "000200"},
]


def test_annex_includes_subordinate_laws_by_default(monkeypatch):
    """'건축법 별표 0건' 은 틀린 답이다. 별표는 시행규칙에 붙는다.

    실측: 건축법 이름으로 정확히 걸리는 별표는 0건, 시행령·시행규칙에 99건.
    """
    monkeypatch.setattr(client, "call_all", lambda *a, **k: client.Result(
        "licbyl", items=_ANNEX_ITEMS, total=3))
    found = annex.of_law("건축법")
    levels = {r["법령명"]: r["수준"] for r in found["items"]}
    assert levels == {"건축법": "본법", "건축법 시행규칙": "하위법령"}


def test_annex_can_be_limited_to_the_law_itself(monkeypatch):
    monkeypatch.setattr(client, "call_all", lambda *a, **k: client.Result(
        "licbyl", items=_ANNEX_ITEMS, total=3))
    found = annex.of_law("건축법", include_subordinate=False)
    assert [r["법령명"] for r in found["items"]] == ["건축법"]


def test_annex_reads_both_spellings_of_the_file_link():
    """가이드는 '별표서식 파일링크', 실제 응답은 '별표서식파일링크' 다."""
    spaced = annex._entry({"별표서식 파일링크": "/a", "별표서식 PDF파일링크": "/b"})
    tight = annex._entry({"별표서식파일링크": "/a", "별표서식PDF파일링크": "/b"})
    assert spaced["hwp"] == tight["hwp"] == "/a"
    assert spaced["pdf"] == tight["pdf"] == "/b"


def test_missing_attachment_is_reported_not_substituted(tmp_path):
    result = annex.fetch({"별표명": "없는 것"}, str(tmp_path))
    assert result["ok"] is False and "링크가 없다" in result["error"]


# ---------------------------------------------------------------- laws

def _law_item(name, alias="", law_id="1", mst="2", kind="법률"):
    return {"법령명한글": name, "법령약칭명": alias, "법령ID": law_id,
            "법령일련번호": mst, "법령구분명": kind, "현행연혁코드": "현행",
            "시행일자": "20260701"}


def test_spacing_difference_still_finds_the_law(monkeypatch):
    monkeypatch.setattr(client, "call", lambda *a, **k: client.Result(
        "law", items=[_law_item("국토의 계획 및 이용에 관한 법률", "국토계획법")]))
    hits = laws.find("국토의계획및이용에관한법률")
    assert len(hits) == 1 and hits[0]["찾은방법"] == "spacing"


def test_partial_name_is_not_accepted(monkeypatch):
    """'건축' 으로 '건축법' 을 집어 주면 엉뚱한 법을 확신하게 된다."""
    monkeypatch.setattr(client, "call", lambda *a, **k: client.Result(
        "law", items=[_law_item("건축법")]))
    monkeypatch.setattr(client, "call_all", lambda *a, **k: client.Result(
        "lsAbrv", items=[]))
    assert laws.find("건축", include_historic=False) == []


def test_abbreviation_falls_back_to_the_alias_dictionary(monkeypatch):
    monkeypatch.setattr(client, "call", lambda *a, **k: client.Result(
        "law", items=[]))
    monkeypatch.setattr(client, "call_all", lambda *a, **k: client.Result(
        "lsAbrv", items=[_law_item("국토의 계획 및 이용에 관한 법률", "국토계획법")]))
    hits = laws.find("국토계획법", include_historic=False)
    assert len(hits) == 1 and hits[0]["찾은방법"] == "alias"


def test_resolve_returns_nothing_rather_than_a_guess(monkeypatch):
    monkeypatch.setattr(client, "call", lambda *a, **k: client.Result(
        "law", items=[]))
    monkeypatch.setattr(client, "call_all", lambda *a, **k: client.Result(
        "lsAbrv", items=[]))
    assert laws.resolve("있을리없는법") is None


# ---------------------------------------------------------------- history

def test_ls_history_is_documented_as_html_only():
    """`lsHistory` 는 JSON·XML 을 주지 않는다 (2026-09-22 실측, 6개 조합).

    이전 조사에서 '연혁 추적 가능' 이라고 보고됐던 것이 사실이 아니어서
    적어 둔다. 그 일은 eflaw 가 한다.
    """
    assert "lsHistory" in history.HTML_ONLY
    assert history.TARGET == "eflaw"


def test_versions_reject_other_laws_with_the_same_prefix(monkeypatch):
    """'도시계획법' 검색에 '도시계획법 시행령' 이 섞여 들어온다."""
    monkeypatch.setattr(client, "call_all", lambda *a, **k: client.Result(
        "eflaw", items=[
            {"법령명한글": "도시계획법", "시행일자": "20030101", "법령ID": "1"},
            {"법령명한글": "도시계획법 시행령", "시행일자": "20030101", "법령ID": "2"},
            {"법령명한글": "도시계획법", "시행일자": "20000701", "법령ID": "1"}],
        total=3))
    found = history.versions("도시계획법")
    assert len(found["versions"]) == 2
    assert found["versions"][0]["시행일자"] == "20030101"


def test_a_repealed_name_is_called_old_not_wrong(monkeypatch):
    """구법 인용은 틀린 인용이 아니다. 이걸 섞으면 멀쩡한 문서를 고치게 된다."""
    monkeypatch.setattr(client, "call",
                        lambda target, **k: client.Result(target, items=[]))
    monkeypatch.setattr(client, "call_all", lambda *a, **k: client.Result(
        "eflaw", items=[{"법령명한글": "도시계획법", "시행일자": "20030101",
                         "제개정구분명": "타법폐지", "법령ID": "1"}], total=1))
    alive, why = history.is_current("도시계획법")
    assert alive is False and "구법" in why


def test_lookup_failure_is_unknown_not_absent(monkeypatch):
    """조회가 실패했으면 None 이다. False(없다) 가 아니다."""
    monkeypatch.setattr(client, "call", lambda *a, **k: client.Result(
        "law", ok=False, error="TimeoutError"))
    alive, why = history.is_current("아무법")
    assert alive is None and "미확인" in why


def test_successor_calls_its_answers_candidates(monkeypatch):
    monkeypatch.setattr(history, "is_current", lambda n: (True, "현행"))
    assert history.successor("건축법")["candidates"] == []


# ---------------------------------------------------------------- catalog

def test_catalog_is_present_and_healthy():
    state = catalog.health()
    if not state["ok"]:
        pytest.skip("카탈로그를 아직 수집하지 않았다")
    assert state["entries"] >= 190
    assert state["targets"] >= 90
    assert state["내가못읽음"] == 0, "못 읽은 항목이 성공으로 세어지고 있다"


def test_catalog_search_looks_deeper_than_names():
    if not catalog.load():
        pytest.skip("카탈로그 없음")
    # 'MST' 는 이름에는 안 나오고 요청변수 설명에만 나온다
    assert len(catalog.search("MST")) > len(catalog.find("MST"))


def test_every_core_target_is_in_the_catalog():
    """이 꾸러미가 쓰는 target 은 전부 설명서에 있어야 한다."""
    if not catalog.load():
        pytest.skip("카탈로그 없음")
    known = set(catalog.targets())
    used = {terms.TARGET, tree.TARGET, annex.TARGET, history.TARGET,
            history.TARGET_DIFF, laws.TARGET_CURRENT, laws.TARGET_HISTORIC}
    used |= {axis[0] for axis in search.AXES}
    assert used <= known, sorted(used - known)


# ---------------------------------------------------------------- 토큰 0

def test_nothing_in_law_kit_calls_a_model():
    """'파이썬만 돈다' 를 말로 두지 않고 시험으로 못 박는다."""
    import glob
    import io as _io
    banned = ("anthropic", "openai", "google.generativeai", "litellm",
              "subprocess", "agy", "codex")
    offenders = []
    for path in glob.glob(os.path.join(ROOT, "law_kit", "*.py")):
        body = _io.open(path, encoding="utf-8").read()
        for line in body.splitlines():
            stripped = line.strip()
            if not (stripped.startswith("import ")
                    or stripped.startswith("from ")):
                continue
            if any(word in stripped for word in banned):
                offenders.append((os.path.basename(path), stripped))
    assert offenders == [], offenders


# ------------------------------------------- 제4자 판독 지적 6건 회귀 방지
#
# 2026-09-22 적대적 검토(agy gemini-3.8-flash / medium)가 재현 가능한
# 반례와 함께 6건을 잡았다. 전부 "실패했는데 0건으로 보인다" 한 가지
# 유형이다. 아래 시험이 그 여섯을 각각 못 박는다.


def test_server_error_code_is_not_a_zero_result():
    """지적 1 (CRITICAL). 법제처는 인증키 만료·장애를 200 응답 **본문 안**
    resultCode 로 알린다. 그걸 메타로 넘기면 장애가 '검색 결과 0건' 이 되고,
    그 다음에 나가는 말은 "그런 법은 없습니다" 다.
    """
    assert client.server_error(
        {"LawSearch": {"resultCode": "30", "resultMsg": "인증키 만료"}})
    assert client.server_error({"LawSearch": {"resultCode": "00"}}) == ""
    assert client.server_error({"LawSearch": {"law": [{"a": 1}]}}) == ""


def test_server_error_makes_the_call_fail_not_empty(monkeypatch):
    monkeypatch.setattr(client, "raw", lambda *a, **k: (
        '{"LawSearch":{"resultCode":"99","resultMsg":"서버 오류"}}', False))
    result = client.call("law", query="x")
    assert result.ok is False and "99" in result.error
    assert not result.complete


def test_server_capped_page_is_not_mistaken_for_the_end(monkeypatch):
    """지적 2 (CRITICAL). page_size=100 으로 물었는데 서버가 20건만 주는
    API 가 있다. 짧은 페이지를 무조건 '끝' 으로 읽으면 총 500건 중 20건만
    받고 완전하다고 말한다.
    """
    def fake(target, service=False, ttl=0, **params):
        return client.Result(target, items=[{"a": i} for i in range(20)],
                             total=500, cached=True)
    monkeypatch.setattr(client, "call", fake)
    result = client.call_all("law", query="x", page_size=100, ttl=0)
    assert len(result.partial) == 20
    assert not result.complete
    assert "500" in result.error and "20" in result.error


def test_short_last_page_is_still_a_clean_end(monkeypatch):
    """반대로 진짜 끝은 끝이라고 말해야 한다. 안 그러면 경보가 울려 댄다."""
    def fake(target, service=False, ttl=0, **params):
        page = int(params.get("page", 1))
        items = [{"a": i} for i in range(100)] if page == 1 else [{"a": 1}]
        return client.Result(target, items=items, total=101, cached=True)
    monkeypatch.setattr(client, "call", fake)
    result = client.call_all("law", query="x", page_size=100, ttl=0)
    assert result.complete and len(result.items) == 101


def test_failed_paging_reports_not_ok(monkeypatch):
    """지적 5 (HIGH). 통신이 죽었는데 ok=True 를 돌려주면, ok 만 보는
    호출자는 빈 손을 성공으로 받는다.
    """
    monkeypatch.setattr(client, "call", lambda *a, **k: client.Result(
        "law", ok=False, error="ConnectionRefusedError"))
    result = client.call_all("law", query="x", ttl=0)
    assert result.ok is False and not result.complete


def test_incomplete_result_refuses_to_be_read():
    """지적 6 (HIGH). 문서로 '확인하세요' 라고 부탁하는 것은 장치가 아니다.

    `if not result:` 한 줄이면 조회 실패가 '0건' 이 된다. 그래서 읽지
    못하게 막는다. 알고 쓰려면 partial 을 쓰면 된다.
    """
    dead = client.Result("law", ok=False, error="ConnectionRefusedError")
    with pytest.raises(client.Incomplete):
        len(dead)
    with pytest.raises(client.Incomplete):
        list(dead)
    with pytest.raises(client.Incomplete):
        bool(dead)
    assert dead.partial == []                  # 알고 여는 문은 열려 있다


def test_incomplete_answer_dict_refuses_too(monkeypatch):
    """딕셔너리로 돌려주는 답에도 같은 문을 단다."""
    monkeypatch.setattr(client, "call", lambda *a, **k: client.Result(
        "lstrmRltJo", ok=False, error="TimeoutError"))
    found = terms.articles("x")
    with pytest.raises(client.Incomplete):
        found["articles"]
    assert found["complete"] is False          # 상태 칸은 언제나 열려 있다
    assert found.partial("articles") == []


def test_tree_does_not_hardcode_completeness(monkeypatch):
    """지적 3 (HIGH). 아래가 잘렸는데 위에서 complete=True 로 덮으면
    호출자는 알 길이 없다.
    """
    monkeypatch.setattr(laws, "resolve", lambda name: {"ID": "1",
                                                       "법령명": "주차장법"})
    monkeypatch.setattr(client, "call", lambda *a, **k: client.Result(
        "lsDelegated", items=[], total=900, truncated=True, complete=False))
    branch = tree.delegated("주차장법")
    assert branch["complete"] is False
    assert branch["note"]


def test_axis_without_a_total_does_not_claim_completeness(monkeypatch):
    """지적 4 (MEDIUM). 총건수를 안 주는 축이 있다(판례 등). display 를
    가득 채워 왔으면 뒤가 더 있는지 **알 수 없다.** 모르는 것을 완전하다고
    말하지 않는다.
    """
    monkeypatch.setattr(client, "call", lambda *a, **k: client.Result(
        "prec", items=[{"사건명": str(i)} for i in range(20)], total=None))
    axis = search.one("prec", "x", display=20)
    assert not axis["complete"] and "알 수 없다" in axis["note"]


def test_axis_below_the_cap_without_a_total_is_fine(monkeypatch):
    """가득 안 찼으면 끝이다. 여기까지 의심하면 쓸 수 없는 도구가 된다."""
    monkeypatch.setattr(client, "call", lambda *a, **k: client.Result(
        "prec", items=[{"사건명": "하나"}], total=None))
    assert search.one("prec", "x", display=20)["complete"]


def test_body_fetch_is_not_reduced_to_one_section():
    """알맹이가 여럿이면 하나를 골라 주지 않는다. 고르면 나머지가 사라진다.

    법령 본문조회는 기본정보·조문·부칙·개정문·제개정이유를 나란히 준다.
    앞에서부터 처음 만난 것을 집는 방식이었을 때, 조문을 달라고 했는데
    **개정문**이 돌아왔다(2026-09-22 실측, 도심 복합개발 지원에 관한 법률).
    조문이 없다고 보이지만 실제로는 있었다.
    """
    payload = {"법령": {"개정문": {"개정문내용": ["..."]},
                        "기본정보": {"법령명_한글": "가"},
                        "조문": {"조문단위": [{"조문번호": "0037"}]},
                        "부칙": {"부칙단위": []}}}
    _total, items = client.unwrap(payload)
    assert len(items) == 1
    assert set(items[0]) >= {"조문", "기본정보", "개정문"}


def test_a_list_of_strings_is_not_the_record_list():
    """문자열 목록(개정문 줄들)을 결과 목록으로 착각하지 않는다."""
    payload = {"법령": {"개정문내용": ["첫 줄", "둘째 줄"],
                        "조문단위": [{"조문번호": "0001"}]}}
    _total, items = client.unwrap(payload)
    assert items == [{"조문번호": "0001"}]


def test_single_section_response_is_still_unwrapped():
    """알맹이가 하나뿐이면 그건 골라도 잃을 것이 없다 - 그대로 꺼낸다."""
    payload = {"lsDelegated": {"법령": {"법령정보": {"법령명": "주차장법"}}}}
    _total, items = client.unwrap(payload)
    assert items == [{"법령정보": {"법령명": "주차장법"}}]


def test_catalog_lives_inside_the_package():
    """law_kit 폴더만 떼어 가도 설명서가 따라가야 한다.

    상위 폴더의 law_api_catalog 모듈을 import 하고 있었다. 그러면 다른
    프로젝트에 law_kit 만 복사했을 때 카탈로그 기능이 죽는다
    (2026-09-22 이식성 검증 지적).
    """
    assert os.path.dirname(catalog.PATH).endswith("law_kit")
    body = _read_package_sources()
    assert "import law_api_catalog" not in body


def _read_package_sources():
    import glob
    import io as _io
    sources = [_io.open(f, encoding="utf-8").read()
               for f in glob.glob(os.path.join(ROOT, "law_kit", "*.py"))]
    return chr(10).join(sources)


# ------------------------------------------- 제4자 판독 2회차 지적 4건
#
# 2026-09-22 codex gpt-5.6-luna / medium, 읽기 전용, 대조 문항 3/3 정답.
# 네 건 모두 같은 유형이다 - "실패했는데 0건으로 보인다".


def test_unrecognized_response_is_not_counted_as_zero():
    """지적 2. 응답을 못 알아봤으면 0건이 아니라 미확인이다.

    다행히 법제처는 진짜 0건을 분명히 말한다(실측): 목록조회는 늘
    `totalCnt: "0"` 을 주고, 본문조회는 안내 문장을 준다. 둘 다 아닌데
    항목도 없으면 그건 내가 못 읽은 것이다.
    """
    assert client.recognized({"LawSearch": {"totalCnt": "0"}}, [], "0")
    assert client.recognized({"Law": "일치하는 정보가 없습니다."}, [], None)
    assert client.recognized({"X": {"a": 1}}, [{"a": 1}], None)
    assert not client.recognized({"LawSearch": {"unexpected": "x"}}, [], None)


def test_unrecognized_response_fails_the_call(monkeypatch):
    monkeypatch.setattr(client, "raw",
                        lambda *a, **k: ('{"LawSearch":{"unexpected":"x"}}',
                                         False))
    result = client.call("law", query="x")
    assert not result.complete and "알아보지 못했다" in result.error


def test_a_real_zero_result_stays_a_clean_zero(monkeypatch):
    """거짓 경보를 만들면 진짜 경보가 죽는다. 진짜 0건은 조용히 0건이다."""
    monkeypatch.setattr(client, "raw",
                        lambda *a, **k: ('{"LawSearch":{"totalCnt":"0"}}',
                                         False))
    result = client.call("law", query="있을리없는법")
    assert result.complete and len(result.items) == 0


def test_repeated_page_is_caught(monkeypatch):
    """지적 1. 서버가 페이지를 안 넘기면 같은 100건이 계속 온다.

    그걸 새 것으로 세면 총건수를 채우고 '전수를 봤다' 고 말하게 된다.
    `pageNo` 를 잘못 보냈을 때 실제로 그랬다 - 4페이지를 받은 줄 알았는데
    같은 100건을 네 번 받고 있었다.
    """
    page = [{"a": i} for i in range(100)]
    monkeypatch.setattr(client, "call", lambda *a, **k: client.Result(
        "law", items=list(page), total=200, cached=True))
    result = client.call_all("law", query="x", page_size=100, ttl=0)
    assert not result.complete
    assert "똑같은 내용" in result.error
    assert len(result.partial) == 100          # 두 배로 세지 않았다


def test_law_lookup_failure_is_not_an_empty_answer(monkeypatch):
    """지적 3. 빈 목록을 돌려주면 부르는 쪽은 '그런 법이 없다' 로 읽는다."""
    monkeypatch.setattr(client, "call", lambda *a, **k: client.Result(
        "law", ok=False, error="TimeoutError"))
    monkeypatch.setattr(client, "call_all", lambda *a, **k: client.Result(
        "lsAbrv", ok=False, error="TimeoutError"))
    with pytest.raises(client.Incomplete):
        laws.find("주차장법")


def test_a_genuinely_missing_law_still_returns_empty(monkeypatch):
    """조회가 멀쩡한데 못 찾은 것은 그냥 없는 것이다. 여기까지 막으면 못 쓴다."""
    monkeypatch.setattr(client, "call", lambda *a, **k: client.Result(
        "law", items=[], total=0))
    monkeypatch.setattr(client, "call_all", lambda *a, **k: client.Result(
        "lsAbrv", items=[], total=0))
    assert laws.find("있을리없는법ㅁㄴㅇ") == []


def test_incomplete_history_is_unknown_not_absent(monkeypatch):
    """지적 4. 연혁을 끝까지 못 봤는데 '어디에도 없다' 고 하면, 멀쩡한
    구법 인용이 '틀린 인용' 으로 찍힌다. 그 사고를 낸 적이 있다.
    """
    monkeypatch.setattr(client, "call",
                        lambda target, **k: client.Result(target, items=[],
                                                          total=0))
    monkeypatch.setattr(client, "call_all", lambda *a, **k: client.Result(
        "eflaw", ok=True, items=[], total=900, truncated=True, complete=False))
    alive, why = history.is_current("어떤법")
    assert alive is None and "미확인" in why


def test_setting_prefers_process_env_then_user_registry(monkeypatch):
    """키는 한 곳(사용자 환경변수)에만 둔다. 클라이언트가 환경을 걸러도 읽혀야 한다."""
    from law_kit import client
    monkeypatch.setenv("LAW_KIT_TEST_SETTING", "from-env")
    assert client.setting("LAW_KIT_TEST_SETTING") == "from-env"
    monkeypatch.delenv("LAW_KIT_TEST_SETTING")
    assert client.setting("LAW_KIT_TEST_SETTING") is None


def _old_law(monkeypatch, body):
    monkeypatch.setattr(history, "is_current", lambda n: (False, "구법"))
    monkeypatch.setattr(history, "versions", lambda n: shape.Answer({
        "complete": True, "versions": [{"MST": "57195", "시행일자": "20030101"}]}))
    seen = {}

    def fake_call(target, service=False, **params):
        seen.update(params)
        return body
    monkeypatch.setattr(client, "call", fake_call)
    return seen


def test_successor_sends_efyd_and_reads_the_abolishing_law(monkeypatch):
    """MST 본문조회는 efYd 가 필수다. 타법폐지면 승계법 이름은 부칙 머리에 온다."""
    body = client.Result("eflaw", items=[{"부칙": {"부칙내용": [
        ["부칙(국토의계획및이용에관한법률) <제6655호,2002.2.4>"]]}}], total=1)
    seen = _old_law(monkeypatch, body)
    found = history.successor("도시계획법")
    assert seen.get("efYd") == "20030101"
    assert found["candidates"] == ["국토의계획및이용에관한법률"]


def test_successor_body_failure_is_not_zero_candidates(monkeypatch):
    _old_law(monkeypatch, client.Result("eflaw", ok=False, error="HTML 응답"))
    found = history.successor("도시계획법")
    assert found["complete"] is False and "HTML 응답" in found["note"]


def test_successor_without_hints_is_unknown(monkeypatch):
    _old_law(monkeypatch, client.Result("eflaw", items=[{"조문": "폐지한다"}], total=1))
    assert history.successor("도시계획법")["complete"] is False


def test_single_call_below_total_is_not_complete(monkeypatch):
    import json
    """한 번 부른 목록이 총건수보다 적으면 앞부분이다 - law_call 이 그대로 노출한다."""
    body = json.dumps({"LawSearch": {"totalCnt": "257", "law": [{"법령명한글": "가"}] * 20}})
    monkeypatch.setattr(client, "raw", lambda url, q, ttl=0: (body, False))
    result = client.call("law", query="의료폐기물", display=20)
    assert result.ok and not result.complete and result.truncated
    assert "257" in result.why_incomplete()


def test_successor_with_cut_body_or_history_is_unknown(monkeypatch):
    cut = client.Result("eflaw", items=[{"x": "「국토계획법」"}], total=1,
                        complete=False, truncated=True)
    _old_law(monkeypatch, cut)
    assert history.successor("도시계획법")["complete"] is False
    monkeypatch.setattr(history, "versions", lambda n: shape.Answer({
        "complete": False, "note": "3페이지에서 끊김",
        "versions": [{"MST": "1", "시행일자": "20000101"}]}))
    found = history.successor("도시계획법")
    assert found["status"] == "미확인" and found["complete"] is False


def test_setting_reads_the_user_registry_when_env_is_filtered(monkeypatch):
    """MCP 클라이언트가 환경을 걸러도 사용자 환경변수(HKCU\Environment)는 읽힌다."""
    import sys
    import types
    fake = types.SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        OpenKey=lambda root, sub: __import__("contextlib").nullcontext(sub),
        QueryValueEx=lambda key, name: ("from-registry", 1))
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(client.os, "name", "nt")
    monkeypatch.delenv("LAW_KIT_TEST_REG", raising=False)
    assert client.setting("LAW_KIT_TEST_REG") == "from-registry"


def test_paging_to_the_end_is_complete_despite_partial_pages(monkeypatch):
    """페이지마다 call 은 '앞부분' 이지만, 끝까지 넘긴 call_all 은 완전해야 한다."""
    import json
    pages = {1: 100, 2: 100, 3: 50}

    def fake_raw(url, q, ttl=0):
        n = pages[int(q["page"])]
        rows = [{"법령명한글": "p%s-%d" % (q["page"], i)} for i in range(n)]
        return json.dumps({"LawSearch": {"totalCnt": "250", "law": rows}}), True
    monkeypatch.setattr(client, "raw", fake_raw)
    result = client.call_all("law", query="x", page_size=100)
    assert result.complete and len(result.items) == 250
