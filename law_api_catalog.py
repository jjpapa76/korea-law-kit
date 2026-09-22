# -*- coding: utf-8 -*-
"""국가법령정보 공동활용 OPEN API 전수 목록을 긁어 자산으로 남긴다.

왜 필요한가:
    법제처가 공개한 API 는 191~195건이다. 그런데 실제로 쓰이는 것은
    극히 일부다(실측 2026-09-22: 한 프로젝트 1종 · 다른 프로젝트 3종).
    쓰이지 않는 이유는 필요 없어서가 아니라 **무엇이 있는지 모르기**
    때문이다. 가이드는 자바스크립트로 여는 상세 페이지 195장에 흩어져
    있어서 사람이 훑기 어렵다.

    한 번 긁어 두면 어느 프로젝트에서든
    "이런 API 가 있나" 를 **검색으로** 답할 수 있다. 매번 사이트를
    뒤지거나 LLM 에게 묻지 않아도 된다 - 토큰도 안 든다.

무엇을 남기는가:
    항목마다 target 코드 · 엔드포인트 · 요청 변수 · 응답 필드 · 샘플 URL.
    즉 **그 API 를 부르는 데 필요한 전부**다.

예의:
    한 번만 긁는다. 요청 사이에 쉬고, 실패해도 재시도로 몰아치지 않는다.
    결과는 파일로 남겨 두 번 긁지 않는다.
"""
import io
import json
import os
import re
import time
import urllib.parse
import urllib.request

LIST_URL = "https://open.law.go.kr/LSO/openApi/guideList.do"
DETAIL_URL = "https://open.law.go.kr/LSO/openApi/guideResult.do"
#: 수집 결과는 **law_kit 안에** 둔다. 그 꾸러미가 이 파일을 읽어 살고,
#: 다른 프로젝트에는 law_kit 폴더만 통째로 가져가면 되게 하기 위해서다.
DEFAULT_OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "law_kit", "law_api_catalog.json")
#: 요청 사이 간격(초). 남의 서버다.
DELAY = 0.35
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) axiom-law-catalog"


def _fetch(url, data=None, timeout=30):
    body = urllib.parse.urlencode(data).encode() if data else None
    request = urllib.request.Request(
        url, data=body,
        headers={"User-Agent": _UA, "Referer": LIST_URL})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


def _text(fragment):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment or "")).strip()


def list_guide_ids(html=None):
    """목록 페이지에서 상세 가이드 ID 를 뽑는다."""
    html = html if html is not None else _fetch(LIST_URL)
    return list(dict.fromkeys(re.findall(r"openApiGuide\('([^']+)'\)", html)))


def _parse_tables(html):
    """(요청 변수, 응답 필드, 샘플 URL) 로 나눠 담는다."""
    params, fields, samples = [], [], []
    for table in re.findall(r"<table[^>]*>(.*?)</table>", html, re.S):
        caption = _text(re.search(r"<caption[^>]*>(.*?)</caption>", table, re.S)
                        .group(1)) if re.search(r"<caption", table) else ""
        rows = []
        for row in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S):
            cells = [_text(c) for c in
                     re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, re.S)]
            if cells:
                rows.append(cells)
        if not rows:
            continue
        header = rows[0] if rows else []
        # 캡션이 없는 표가 있다. 그때는 **머리행**으로 가른다.
        # 실측(2026-09-22): detcListGuide 등 16건이 캡션 없는 응답표를 갖고
        # 있어서 조용히 '응답필드 0개' 로 수집됐다.
        if "요청변수" in caption or (len(header) >= 2 and header[0] == "요청변수"):
            params = [r for r in rows[1:] if len(r) >= 2]
        elif ("출력 결과" in caption or "응답" in caption
              or (len(header) >= 2 and header[0] == "필드")):
            fields = [r for r in rows[1:] if len(r) >= 2]
        else:
            samples += [c for r in rows for c in r
                        if c.startswith("http") or "DRF/" in c]
    return params, fields, samples


def parse_guide(html):
    """상세 페이지 하나를 구조화한다. 못 읽은 칸은 비워 둔다 - 지어내지 않는다."""
    # 이름은 "- 요청 URL" 바로 앞에 온다. 페이지 머리의 "OPEN API 활용가이드"
    # 를 집지 않도록 그 위치를 기준으로 거꾸로 찾는다.
    name = ""
    anchor = html.find("요청 URL")
    window = html[max(0, anchor - 900):anchor] if anchor > 0 else ""
    candidates = [_text(c) for c in re.findall(r">([^<>]{4,80}?API)\s*<", window)]
    candidates = [c for c in candidates
                  if c and not c.startswith("OPEN API") and "활용" not in c]
    if candidates:
        name = candidates[-1]
    endpoint, target = "", ""
    url_hit = re.search(r"(https?://[^\s\"'<]*DRF/(law\w+)\.do)\?target=([a-zA-Z]+)",
                        html)
    if url_hit:
        endpoint, target = url_hit.group(1), url_hit.group(3)
    else:                                    # 요청 변수 표에서 줍는다
        t = re.search(r"target[^<]*</t[hd]>\s*<t[hd][^>]*>[^<]*?:\s*([a-zA-Z]+)", html)
        target = t.group(1) if t else ""
    params, fields, samples = _parse_tables(html)
    # 응답표가 **페이지에 아예 없는** 것과 **내가 못 읽은** 것은 다르다.
    # 앞은 사실이고 뒤는 결함이다. 구분해서 남긴다 - 안 그러면 불완전한
    # 항목이 완전한 것처럼 보인다.
    has_field_table = bool(re.search(r"<t[hd][^>]*>\s*필드\s*</t[hd]>", html))
    return {
        "name": name,
        "endpoint": endpoint,
        "target": target,
        "request_params": [{"name": r[0], "value": r[1],
                            "desc": r[2] if len(r) > 2 else ""} for r in params],
        "response_fields": [{"field": r[0], "type": r[1],
                             "desc": r[2] if len(r) > 2 else ""} for r in fields],
        "response_fields_absent": not has_field_table,
        "samples": samples[:4],
    }


def harvest(out_path=DEFAULT_OUT, delay=DELAY, limit=0, progress=None):
    """전수 수집. 한 번만 돌리고 파일로 남긴다."""
    ids = list_guide_ids()
    if limit:
        ids = ids[:limit]
    entries, failed = [], []
    for index, guide_id in enumerate(ids, 1):
        try:
            html = _fetch(DETAIL_URL, data={"htmlName": guide_id})
            entry = parse_guide(html)
        except Exception as exc:
            failed.append({"guide_id": guide_id,
                           "error": "%s: %s" % (type(exc).__name__, exc)})
            continue
        entry["guide_id"] = guide_id
        entries.append(entry)
        if progress:
            progress(index, len(ids), entry)
        time.sleep(delay)
    payload = {
        "source": LIST_URL,
        "harvested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(entries),
        "failed": failed,
        "entries": entries,
    }
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=1)
    return payload


def load(path=DEFAULT_OUT):
    if not os.path.exists(path):
        return None
    with io.open(path, encoding="utf-8") as handle:
        return json.load(handle)


def find(keyword, path=DEFAULT_OUT):
    """목록에서 검색한다. 이게 이 자산을 쓰는 가장 흔한 방법이다."""
    data = load(path)
    if not data:
        return []
    key = str(keyword).strip()
    hits = []
    for entry in data["entries"]:
        haystack = " ".join([entry.get("name", ""), entry.get("target", ""),
                             entry.get("guide_id", "")])
        if key in haystack:
            hits.append(entry)
    return hits


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    argv = sys.argv[1:]
    if argv and argv[0] == "harvest":
        limit = int(argv[1]) if len(argv) > 1 else 0

        def show(i, total, entry):
            if i % 20 == 0 or i == total:
                print("  %3d/%d  %-14s %s" % (i, total, entry.get("target", "?"),
                                              entry.get("name", "")[:44]))

        result = harvest(limit=limit, progress=show)
        print("수집 %d건 / 실패 %d건 -> %s"
              % (result["count"], len(result["failed"]), DEFAULT_OUT))
    elif argv and argv[0] == "find":
        for entry in find(argv[1]):
            print("%-16s %-20s %s" % (entry.get("target"), entry.get("guide_id"),
                                      entry.get("name")))
    else:
        data = load()
        if not data:
            print("아직 수집하지 않았다. `python law_api_catalog.py harvest`")
        else:
            print("수집 %s건 (%s)" % (data["count"], data["harvested_at"]))
            targets = sorted({e["target"] for e in data["entries"] if e["target"]})
            print("target 코드 %d종" % len(targets))
            print(", ".join(targets))
