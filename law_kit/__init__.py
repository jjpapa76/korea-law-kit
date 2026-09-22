# -*- coding: utf-8 -*-
"""law_kit - 어떤 법이든 파이썬만으로 부르고 확인하고 따져 보는 작은 꾸러미.

누가 쓰나:
    법과 판례를 바닥에 깔고 있는 프로그램들. 지금까지는 각자 조금씩
    다르게 법제처를 불렀다. 같은 법을 여러 번 받고, 실패를 저마다
    다르게 삼켰다.

무엇이 다른가 - 셋이다:

    1. **토큰 0.** 전부 파이썬이다. 이 꾸러미 어디에도 LLM 호출이 없다.
       모델은 "무엇을 물을지" 만 정하면 되고, 묻고 받고 세는 일은 여기서 한다.

    2. **모르는 것을 모른다고 말한다.** 이게 핵심이다.
       조회 실패 · 상한 절단 · 페이징 중단 · 진짜 0건 - 넷은 다른 것이다.
       하나로 뭉뚱그리면 "다 찾아봤는데 없습니다" 라는 **거짓 보증**이 나오고,
       그 말을 믿고 결정이 내려진다. 모든 반환값에 `complete` 가 있다.

    3. **한 번 받은 것은 디스크에 남는다.** 여러 프로그램이 같은 캐시를 본다
       (`LAW_KIT_CACHE` 로 위치를 맞춘다). 하루 호출 한도를 셋이 나눠 쓰지
       않아도 된다.

다섯 갈래로 쓴다:

    law_kit.terms.articles("도시혁신구역")     낱말 -> 조문 (1.2초)
    law_kit.tree.delegated("주차장법")          법률 -> 시행령·규칙·조례
    law_kit.history.successor("도시계획법")     옛 이름 -> 지금 이름
    law_kit.annex.of_law("건축법")              별표 목록 + 파일
    law_kit.search.across("의료폐기물")         8개 축 한꺼번에

    law_kit.catalog.search("건폐율")            어떤 API 가 있나 (195건)
    law_kit.client.call("prec", query="...")    카탈로그의 아무 API 나 직접
"""
from . import annex, catalog, client, history, laws, search, shape, terms, tree
from .client import Result

__all__ = ["annex", "catalog", "client", "history", "laws", "search", "shape",
           "terms", "tree", "Result", "brief"]
__version__ = "1.0"


def brief(topic, deep=True):
    """주제 하나를 **한 번에** 훑는다. 가장 흔한 입구다.

    낱말이 법령용어면 조문까지 바로 짚고, 아니면 여덟 축 검색으로 내려간다.
    그 다음 나온 법들의 하위 법령과 별표를 붙인다.

    돌려주는 것에는 반드시 `gaps` 가 있다. **무엇을 확인하지 못했는지**의
    목록이다. 비어 있을 때만 "다 봤다" 고 말할 수 있다.
    """
    out = {"topic": topic, "gaps": []}

    found = terms.articles(topic)
    out["terms"] = found
    if not found["complete"]:
        out["gaps"].append("용어-조문 연계: " + (found.get("note") or "미확인"))

    axes = search.across(topic, display=20)
    out["search"] = axes
    for target in axes["incomplete"]:
        out["gaps"].append("%s 축: %s"
                           % (axes["axes"][target]["label"],
                              axes["axes"][target]["note"]))

    out["laws"] = found.partial("laws") or []
    out["tree"] = {}
    out["annex"] = {}
    if deep:
        for name in out["laws"][:5]:          # 앞 5개만. 더 필요하면 직접 부른다
            branch = tree.delegated(name)
            out["tree"][name] = branch
            if not branch.get("complete"):
                out["gaps"].append("%s 위임법령: %s"
                                   % (name, branch.get("note") or "미확인"))
            sheets = annex.of_law(name)
            out["annex"][name] = sheets
            if not sheets["complete"]:
                out["gaps"].append("%s 별표: %s" % (name, sheets["note"]))
        if len(out["laws"]) > 5:
            out["gaps"].append(
                "법령 %d개 중 앞 5개만 하위법령·별표를 봤다. 나머지 %d개는 미확인"
                % (len(out["laws"]), len(out["laws"]) - 5))
    return out


def format_brief(result):
    """`brief` 결과를 사람이 읽는 글로. 빈칸은 빈칸으로 남긴다."""
    lines = ["# %s" % result["topic"], ""]
    found = result["terms"]
    rows = found.partial("articles") or []
    if found["found"]:
        lines.append("법령용어로 등재돼 있다. 조문 %d개 · 법령 %d개."
                     % (len(rows), len(found.partial("laws") or [])))
        for row in rows[:15]:
            lines.append("  - %s %s" % (row["법령명"], row["조"]))
        if len(rows) > 15:
            lines.append("  ... 외 %d개" % (len(rows) - 15))
    else:
        lines.append("법령용어로는 등재돼 있지 않다. "
                     "(관련 조문이 없다는 뜻이 아니다)")
    lines += ["", search.format_report(result["search"]), ""]
    for name, branch in result.get("tree", {}).items():
        if branch.get("ok"):
            counts = ", ".join("%s %d" % kv for kv in tree.summary(branch))
            lines.append("%s 하위: %s" % (name, counts or "없음"))
    for name, sheets in result.get("annex", {}).items():
        sheet_rows = sheets.partial("items") or []
        if sheets.get("ok") and sheet_rows:
            lines.append("%s 별표 %d건%s"
                         % (name, len(sheet_rows),
                            "" if sheets.get("complete") else " (미확인 포함)"))
    lines.append("")
    if result["gaps"]:
        lines.append("확인하지 못한 것 %d건:" % len(result["gaps"]))
        for gap in result["gaps"]:
            lines.append("  ! " + gap)
        lines.append("")
        lines.append("위 목록이 남아 있는 한 '다 찾아봤다' 고 말하면 안 된다.")
    else:
        lines.append("확인하지 못한 것 없음. 위 범위 안에서는 전수다.")
    return "\n".join(lines)
