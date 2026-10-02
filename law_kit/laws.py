# -*- coding: utf-8 -*-
"""법 **이름**으로 법을 찾는다. 사람은 ID 를 모르기 때문이다.

다른 모듈(위임법령·연혁·별표)은 전부 법령ID 나 MST 를 요구한다. 그런데
사람이 손에 쥔 것은 "국토계획법", "국토의 계획 및 이용에 관한 법률",
심지어 띄어쓰기가 다른 "국토의계획및이용에관한법률" 이다. 여기서 그
간극을 메운다.

네 갈래로 찾는다. **찾은 방법을 결과에 적어 둔다** - 약칭으로 맞춘 것과
정확히 맞춘 것은 확신의 무게가 다르고, 쓰는 쪽이 그걸 알아야 한다.

    exact     이름이 그대로 맞았다
    spacing   띄어쓰기만 달랐다
    alias     약칭이었다 (예: 국토계획법)
    historic  현행법에 없다. 폐지·제명변경된 구법에서 찾았다
"""
from . import client
from .client import Incomplete
from .shape import text
import json
import os

#: 현행 법령
TARGET_CURRENT = "law"
#: 시행일 법령. **폐지·개정 이전 판까지** 들어 있다 - 구법명은 여기서만 나온다
TARGET_HISTORIC = "eflaw"

#: 통칭 약칭 사전 파일 경로
_ALIASES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aliases.json")
_COMMON_NAMES_CACHE = None


def _get_common_names():
    global _COMMON_NAMES_CACHE
    if _COMMON_NAMES_CACHE is None:
        _COMMON_NAMES_CACHE = {}
        if os.path.exists(_ALIASES_PATH):
            try:
                with open(_ALIASES_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                common = data.get("통칭", {})
                for k, v in common.items():
                    squashed_k = _squash(k)
                    canonical = v.get("정식명") if isinstance(v, dict) else v
                    if canonical:
                        _COMMON_NAMES_CACHE[squashed_k] = canonical
            except Exception:
                pass
    return _COMMON_NAMES_CACHE


def _squash(name):
    return "".join(str(name).split())


def _entry(item, how):
    return {
        "법령명": text(item.get("법령명한글")),
        "약칭": text(item.get("법령약칭명")),
        "ID": text(item.get("법령ID")),
        "MST": text(item.get("법령일련번호")),
        "법령구분": text(item.get("법령구분명")),
        "소관부처": text(item.get("소관부처명")),
        "시행일자": text(item.get("시행일자")),
        "공포일자": text(item.get("공포일자")),
        "현행": text(item.get("현행연혁코드")),
        "찾은방법": how,
    }


def find(name, limit=20, include_historic=True):
    """이름으로 법을 찾는다. 후보 목록을 돌려준다.

    한 건만 나올 거라고 가정하지 않는다. "주차장법" 처럼 법률·시행령·
    시행규칙이 한꺼번에 걸리는 이름이 흔하다. **고르는 것은 부르는 쪽 몫**
    이고, 여기서는 고를 수 있게 다 보여 준다.
    """
    wanted = _squash(name)
    seen, out, failures = set(), [], []

    def take(result, how):
        for item in result.partial:
            title = text(item.get("법령명한글"))
            if not title:
                continue
            key = (title, text(item.get("법령ID")))
            if key in seen:
                continue
            squashed = _squash(title)
            if squashed == wanted:
                label = how if how != "search" else (
                    "exact" if title == str(name).strip() else "spacing")
            elif _squash(text(item.get("법령약칭명"))) == wanted:
                label = "alias"
            else:
                continue                      # 부분 일치는 채택하지 않는다
            seen.add(key)
            out.append(_entry(item, label))

    current = client.call(TARGET_CURRENT, query=name, search=1, display=100)
    if not current.complete:
        failures.append("현행 법령 조회: " + current.why_incomplete())
    take(current, "search")
    if not out:
        # 공식 약칭은 아니지만 실무에서 쓰이는 통칭(관용명)을 확인한다
        common_map = _get_common_names()
        if wanted in common_map:
            canonical_name = common_map[wanted]
            c_res = client.call(TARGET_CURRENT, query=canonical_name, search=1, display=100)
            if not c_res.complete:
                failures.append("통칭 정식 법령 조회: " + c_res.why_incomplete())
            c_wanted = _squash(canonical_name)
            for item in c_res.partial:
                title = text(item.get("법령명한글"))
                if not title:
                    continue
                key = (title, text(item.get("법령ID")))
                if key in seen:
                    continue
                if _squash(title) == c_wanted:
                    seen.add(key)
                    out.append(_entry(item, "통칭(공식 약칭 아님)"))
    if not out:
        # 약칭은 이름 검색으로 안 걸린다. 약칭 사전을 통째로 받아 맞춘다
        # (2,713건, 캐시되므로 두 번째부터 호출 0).
        abbr = client.call_all("lsAbrv", page_size=100, max_pages=40)
        if not abbr.complete:
            failures.append("약칭 사전 조회: " + abbr.why_incomplete())
        for item in abbr.partial:
            if _squash(text(item.get("법령약칭명"))) == wanted:
                out.append(_entry(item, "alias"))
    if not out and include_historic:
        # 현행에 없다 = 폐지됐거나 제명이 바뀌었다. 구법 목록을 본다.
        # 필요한 만큼 쪽을 넘겨 받아라 (상한 5쪽, 그래도 끝을 못 보면 complete:false + why, 예외 금지)
        historic_failures = []
        saw_end = False
        last_page = 0
        for page in range(1, 6):
            last_page = page
            old = client.call(TARGET_HISTORIC, query=name, search=1, display=200, page=page)
            if not old.ok:
                historic_failures.append("연혁 법령 조회: " + (old.error or old.why_incomplete()))
                break
            take(old, "historic")
            if out:
                break
            if not old.complete:
                why_inc = old.why_incomplete()
                if why_inc and why_inc not in historic_failures:
                    historic_failures.append("연혁 법령 조회: " + why_inc)
            try:
                exp_total = int(old.total or 0)
            except (ValueError, TypeError):
                exp_total = None
            if not old.partial or (exp_total is not None and page * 200 >= exp_total) or (len(old.partial) < 200 and old.complete):
                saw_end = True
                break
        if not out:
            if historic_failures:
                failures.extend(historic_failures)
            elif not saw_end:
                failures.append("연혁 목록 %d쪽까지 봤지만 끝을 보지 못했다" % last_page)
        for entry in out:
            entry["찾은방법"] = "historic"
    if not out and failures:
        # 빈 목록을 돌려주면 부르는 쪽은 "그런 법이 없다" 로 읽는다.
        # 못 물어본 것을 없는 것으로 만들지 않는다.
        if len(failures) == 1 and failures[0].startswith("연혁 목록"):
            raise Incomplete(failures[0])
        raise Incomplete("'%s' 를 찾지 못했지만 조회가 온전하지 않았다: %s"
                         % (name, " / ".join(failures)))
    out.sort(key=lambda e: (e["찾은방법"] != "exact", e.get("시행일자") or ""),
             reverse=False)
    return out[:limit]


def resolve(name):
    """후보 하나를 고른다. 못 고르면 None.

    고르는 기준은 **현행 + 가장 최근 시행분**이다. 애매하면 None 을 주고
    `find` 로 사람이 고르게 한다 - 말없이 아무거나 집는 것이 제일 나쁘다.
    """
    hits = find(name)
    if not hits:
        return None
    current = [h for h in hits if h["현행"] == "현행"] or hits
    current.sort(key=lambda e: e.get("시행일자") or "", reverse=True)
    return current[0]
