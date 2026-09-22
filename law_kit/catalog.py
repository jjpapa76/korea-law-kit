# -*- coding: utf-8 -*-
"""**어떤 API 가 있는지** 묻는 곳. 195건 설명서가 파일 하나에 들어 있다.

법제처가 공개한 API 는 195건인데 실제로 쓰이던 것은 3건이었다. 없어서가
아니라 **있는 줄 몰라서**다. 여기서 검색하면 target 코드·요청변수·응답
필드까지 나온다. 인터넷도 LLM 도 안 쓴다 - 파일을 읽을 뿐이다.
"""
import io
import json
import os

#: 설명서는 이 꾸러미 **안에** 있다. 수집기(law_api_catalog.py)가 없어도
#: 읽기는 된다 - 그래야 law_kit 폴더만 떼어 다른 프로젝트에 넣을 수 있다.
PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "law_api_catalog.json")

_CACHE = {}


def load(path=None):
    """한 번 읽고 들고 있는다. 668KB 짜리를 매번 여는 것은 낭비다."""
    target = path or PATH
    if target not in _CACHE:
        if not os.path.exists(target):
            return None
        with io.open(target, encoding="utf-8") as handle:
            _CACHE[target] = json.load(handle)
    return _CACHE[target]


def find(keyword):
    """이름·target·가이드ID 에서 찾는다."""
    data = load()
    if not data:
        return []
    key = str(keyword).strip()
    return [e for e in data["entries"]
            if key in " ".join([e.get("name", ""), e.get("target", ""),
                                e.get("guide_id", "")])]


def search(keyword):
    """`find` 보다 넓게 - **요청변수·응답필드 설명까지** 뒤진다.

    `find` 는 이름만 본다. "건폐율" 처럼 이름에 안 나오고 응답 필드에만
    있는 말은 `find` 로 못 찾는다. 못 찾으면 사람은 "그런 API 가 없다" 고
    결론 내린다 - 그게 바로 침묵형 누락이다.
    """
    data = load()
    if not data:
        return []
    key = str(keyword).strip()
    hits = []
    for entry in data["entries"]:
        blob = " ".join([
            entry.get("name", ""), entry.get("target", ""),
            entry.get("guide_id", ""),
            " ".join(p.get("name", "") + p.get("desc", "")
                     for p in entry.get("request_params", [])),
            " ".join(f.get("field", "") + f.get("desc", "")
                     for f in entry.get("response_fields", [])),
        ])
        if key in blob:
            hits.append(entry)
    return hits


def targets():
    """쓸 수 있는 target 코드 전부."""
    data = load()
    if not data:
        return []
    return sorted({e["target"] for e in data["entries"] if e.get("target")})


def describe(target):
    """target 하나의 사용법. 요청변수와 응답필드."""
    data = load()
    if not data:
        return []
    return [e for e in data["entries"] if e.get("target") == target]


def health():
    """설명서 자체가 얼마나 온전한지. **덮지 않고 드러낸다.**"""
    data = load()
    if not data:
        return {"ok": False, "note": "아직 수집하지 않았다"}
    entries = data["entries"]
    absent = [e for e in entries if e.get("response_fields_absent")]
    empty = [e for e in entries
             if not e.get("response_fields") and not e.get("response_fields_absent")]
    return {"ok": True, "entries": len(entries),
            "targets": len(targets()),
            "응답필드있음": len([e for e in entries if e.get("response_fields")]),
            "페이지에응답표없음": len(absent),
            "내가못읽음": len(empty),
            "수집실패": len(data.get("failed", [])),
            "harvested_at": data.get("harvested_at")}
