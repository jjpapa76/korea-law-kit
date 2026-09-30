# -*- coding: utf-8 -*-
"""표준 라이브러리만 쓰는 stdio 기반 국가법령 MCP 서버.

외부 패키지(`mcp` 패키지 포함) 없이 오직 파이썬 표준 라이브러리만으로
JSON-RPC 2.0 프로토콜을 구현해 국가법령 도구 9종을 제공한다.
`python -m law_kit.mcp_server` 로 직접 띄워 사용한다.

왜 표준 라이브러리만 쓰나:
    이 꾸러미의 설계 원칙은 "파이썬 표준 라이브러리만으로 동작한다"이다.
    어떤 환경에서든 추가 설치(pip install) 없이 폴더를 복사하거나
    경로에 올리는 것만으로 즉시 구동될 수 있어야 한다.

왜 불완전 결과를 강제하나:
    국가법령 검색에서 "못 찾은 것"과 "진짜 없는 것"은 완전히 다르다.
    상한 절단, 통신 실패, 페이징 중단, 연혁 미확인 등으로 온전하지 못한 결과를
    그대로 돌려주면, 모델이 "해당 법령이나 조문은 존재하지 않습니다"라는
    치명적인 거짓 보증(False Negative)을 만들어낸다.
    따라서 결과가 조금이라도 불완전하면 complete: false 와 구체적 이유(why),
    그리고 단정적 답변을 금지하는 지시(지시)를 반드시 붙여 돌려준다.
"""
import io
import json
import os
import sys
import traceback

import law_kit as kit
from law_kit.client import Incomplete, Result, is_demo_key
from law_kit.shape import Answer, PAYLOAD_KEYS

#: MCP 기본 프로토콜 버전
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "korea-law"
SERVER_VERSION = "1.0.0"

#: 서버 instructions 안내 문구
SERVER_INSTRUCTIONS = (
    "국가법령정보 공동활용 OPEN API를 토큰 0으로 정밀 조회하는 서버다. "
    "결과가 불완전하면 '없다'·'전부다'라고 단정하지 말고 "
    "확인하지 못한 부분을 사용자에게 알리거나 범위를 좁혀 다시 물어라."
)

#: 불완전한 결과일 때 모델에게 내리는 강제 지침
INSTRUCTION_INCOMPLETE = (
    "이 결과로 '없다'·'전부다'라고 답하지 마라. "
    "확인하지 못한 부분을 사용자에게 알리거나 범위를 좁혀 다시 물어라"
)

#: 데모 키(test) 사용 시 경고 문구
DEMO_WARNING = (
    "인증키 없이 데모 키(test)로 불렀다. "
    "결과가 제한됐을 수 있다. LAW_API_OC 를 설정하라"
)

#: 단일 응답 텍스트 최대 글자 수 상한
MAX_TEXT_LENGTH = 60000


#: MCP 도구 9종 명세 정의
TOOLS = [
    {
        "name": "law_brief",
        "description": "주제 하나를 법령용어·8축 검색·하위법령·별표까지 한 번에 훑는다. 가장 흔한 입구다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "조회할 주제 또는 낱말"},
                "deep": {"type": "boolean", "description": "하위법령과 별표까지 깊게 볼지 여부 (기본값 true)"}
            },
            "required": ["topic"]
        }
    },
    {
        "name": "law_find",
        "description": "법령 이름이나 약칭, 구법명으로 현행 및 연혁 법령을 찾아 법령ID와 MST 일련번호를 확인한다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "찾을 법령 이름 (약칭·구법명 포함)"},
                "limit": {"type": "integer", "description": "최대 반환 건수 (기본값 20)"},
                "include_historic": {"type": "boolean", "description": "폐지·개정 이전 구법 포함 여부 (기본값 true)"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "law_term",
        "description": "법령용어로 등재된 낱말을 찾아 직접 연계된 법령과 조문 목록을 확인한다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "term": {"type": "string", "description": "조회할 법령용어 (예: 도시혁신구역)"},
                "law": {"type": "string", "description": "이 법령명(일부 일치)의 조문만 좁혀서 본다."},
                "with_text": {"type": "boolean", "description": "조문 본문(조문내용)까지 포함할지 여부 (기본값 false)."},
                "offset": {"type": "integer", "description": "조문 목록 시작 위치 (기본값 0)"},
                "limit": {"type": "integer", "description": "조문 목록 최대 반환 건수 (기본값 200)"}
            },
            "required": ["term"]
        }
    },
    {
        "name": "law_tree",
        "description": "법률 하나에서 아래로 위임된 시행령, 시행규칙, 자치법규(조례), 행정규칙 체계를 한 번에 확인한다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "law": {"type": "string", "description": "기준 법률명 (예: 주차장법)"},
                "group": {"type": "string", "description": "이 위임구분의 조문 행만 (시행령, 시행규칙, 위임자치법규, 위임행정규칙, 인용법령). 비우면 구분별 건수와 하위법령 이름만 준다"},
                "contains": {"type": "string", "description": "대상법령 이름에 이 말이 든 행만 (예: 서울특별시 강남구)"},
                "offset": {"type": "integer", "description": "위임 체계 rows 시작 위치 (기본값 0)"},
                "limit": {"type": "integer", "description": "위임 체계 rows 최대 반환 건수 (기본값 200)"}
            },
            "required": ["law"]
        }
    },
    {
        "name": "law_annex",
        "description": "법령에 딸린 별표와 서식 목록 및 파일 링크를 확인한다. (목록과 파일 링크만 확인하며 파일 내려받기는 없음)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "law_name": {"type": "string", "description": "법령명 (예: 건축법)"},
                "include_subordinate": {"type": "boolean", "description": "시행령·시행규칙 별표 포함 여부 (기본값 true)"}
            },
            "required": ["law_name"]
        }
    },
    {
        "name": "law_history",
        "description": "법령 이름이 지금도 살아 있는 현행법인지 확인하고, 구법인 경우 개정·대체된 승계 법령 후보를 추적한다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "확인할 법령 이름 (예: 도시계획법)"}
            },
            "required": ["name"]
        }
    },
    {
        "name": "law_search",
        "description": "법령, 자치법규, 행정규칙, 판례, 헌재결정례, 법령해석례, 행정심판례, 감사원 사전컨설팅의 8개 축을 한꺼번에 검색한다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색할 주제 또는 질의어"},
                "display": {"type": "integer", "description": "각 축당 조회 건수 (기본값 20)"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "law_api",
        "description": "국가법령정보 공동활용 195종 OPEN API 설명서를 검색하거나, target 코드로 요청변수와 응답필드 상세 명세를 조회한다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "검색할 API 키워드 (예: 건폐율)"},
                "target": {"type": "string", "description": "특정 API의 target 코드 (주어지면 해당 target의 상세 사용법을 반환)"}
            }
        }
    },
    {
        "name": "law_call",
        "description": "195종 법제처 OPEN API를 target 코드로 직접 호출한다. target 을 모르면 law_api 로 먼저 찾아라.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "호출할 API target 코드. target 을 모르면 law_api 로 먼저 찾아라"},
                "service": {"type": "boolean", "description": "lawService 여부 (기본값 false는 lawSearch)"},
                "params": {"type": "object", "description": "API에 전달할 추가 쿼리 파라미터 딕셔너리"}
            },
            "required": ["target"]
        }
    }
]


def extract_partial(val):
    """Result/Answer 등 내부 객체에서 Incomplete 예외 없이 받은 만큼 안전하게 꺼낸다.

    왜 이 함수가 필요한가:
        Answer 객체는 complete=False 일 때 PAYLOAD_KEYS(__getitem__)에 접근하면
        Incomplete 예외를 발생시키도록 설계되어 있다.
        따라서 일반 재귀 덤프를 수행하면 직렬화 도중 예외가 터져버린다.
        이 함수는 partial() 메서드를 통해 "알고 쓰는 안전한 문"으로 데이터를 꺼내어
        JSON 직렬화가 가능한 순수 파이썬 데이터(dict/list/scalar)로 변환한다.
    """
    return _walk(val, [], "")


def _walk(val, gaps, where):
    """펴면서 **어느 깊이에 있든** 불완전 표시를 `gaps` 에 모은다.

    겉만 보면 안 된다. `law_history` 는 현행 여부가 확정이어도 안쪽
    `successor` 가 미확인일 수 있고, `brief` 는 축 하나만 잘려도 불완전이다.
    안쪽 Result/Answer 를 조용히 펴 버리면 겉의 complete: true 가 거짓
    보증이 된다 - 이 꾸러미가 막으려는 바로 그것이다.
    """
    here = where or "결과"
    if isinstance(val, Result):
        if not val.complete:
            gaps.append("%s: %s" % (here, val.why_incomplete() or "끝까지 받지 못했다"))
        return [_walk(x, gaps, where) for x in val.partial]
    if isinstance(val, dict):
        if isinstance(val, Answer) and not dict.get(val, "complete", True):
            gaps.append("%s: %s" % (here, val.why()))
        elif not dict.get(val, "complete", True):
            gaps.append("%s: %s" % (here, dict.get(val, "why")
                                    or dict.get(val, "note") or "끝까지 받지 못했다"))
        if dict.get(val, "status") == "미확인":
            gaps.append("%s: 미확인 - %s" % (here, dict.get(val, "why", "")))
        for key in ("gaps", "incomplete"):
            listed = dict.get(val, key)
            if listed:
                gaps.append("%s.%s: %s" % (here, key, "; ".join(str(g) for g in listed)))
        return {k: _walk(dict.get(val, k), gaps, "%s.%s" % (where, k) if where else str(k))
                for k in val.keys()}
    if isinstance(val, (list, tuple)):
        return [_walk(x, gaps, where) for x in val]
    return val


def serialize_response(val, is_incomplete_exc=False, exc_obj=None):
    """Result/Answer/dict/list 를 한 곳에서 JSON 문자열로 직렬화한다.

    왜 한 곳에 모으는가:
        불완전 결과 방어, 데모 키 경고, 60,000자 절단 방어는
        모든 도구 응답에 일관되게 적용되어야 하는 핵심 계약이다.
        여러 곳에 흩어지면 특정 도구에서 불완전 결과가 complete 로
        누출되는 사고가 재발할 수 있다.

    지키는 원칙:
        1. Result.complete 가 False, Answer 의 complete 가 False,
           Incomplete 예외, 또는 gaps/incomplete 가 비어 있지 않으면
           반드시 complete: false, why, 지시, partial 로 구성한다.
        2. 데모 키(test) 사용 시 경고 문구를 포함한다.
        3. 60,000자를 초과하면 절단하고 complete: false 와 사유를 남긴다.
        4. 한글은 ensure_ascii=False 로 온전히 출력한다.
    """
    gaps = []
    if is_incomplete_exc:
        gaps.append(str(exc_obj) if exc_obj else "끝까지 받지 못했다")
        data = _walk(getattr(exc_obj, "partial", None), gaps, "")
    else:
        data = _walk(val, gaps, "")
    if isinstance(val, Result):
        data = {"target": val.target, "total": val.total,
                "count": len(val.partial), "items": data}
    elif not isinstance(data, dict):
        data = {"count": len(data), "items": data} if isinstance(data, list) else {"result": data}

    if gaps:
        instruction = INSTRUCTION_INCOMPLETE
        if isinstance(data, dict) and data.get("found") is False and "지시" in data:
            instruction = data["지시"]
        payload = {
            "complete": False,
            "why": " | ".join(dict.fromkeys(gaps)),
            "지시": instruction,
            "partial": data,
        }
    else:
        payload = dict(data)
        payload["complete"] = True

    # 데모 키 사용 중이면 사용자 주의를 위해 경고 첨부
    if kit.client.is_demo_key():
        payload["경고"] = DEMO_WARNING

    text_out = json.dumps(payload, ensure_ascii=False, indent=2)

    # 60,000자 초과 절단 방어 (MCP 계층의 절단도 불완전이다)
    if len(text_out) > MAX_TEXT_LENGTH:
        why = "응답 크기(%d자)가 상한 %s자를 초과하여 앞부분만 절단했습니다." % (
            len(text_out), format(MAX_TEXT_LENGTH, ",d"))
        if payload.get("why"):
            # 사유 자체가 길면 뼈대만으로 상한을 넘는다 - 사유도 자른다.
            why = str(payload["why"])[:4000] + " | " + why
        # 잘라 낸 원문을 다시 JSON 문자열에 넣으면 따옴표·역슬래시가 이스케이프돼
        # 길이가 불어난다(따옴표 3만 개 -> 11만 자, 판독 재현). 상한 안에 들 때까지 줄인다.
        keep = MAX_TEXT_LENGTH
        while True:
            cut_payload = {"complete": False, "why": why,
                           "지시": INSTRUCTION_INCOMPLETE, "partial": text_out[:keep]}
            if kit.client.is_demo_key():
                cut_payload["경고"] = DEMO_WARNING
            cut_text = json.dumps(cut_payload, ensure_ascii=False, indent=2)
            if len(cut_text) <= MAX_TEXT_LENGTH or keep == 0:
                break
            # 이스케이프로 불어난 비율만큼 줄인다. 차이만큼 빼면 두 배로
            # 불어난 경우 0 까지 깎여 알맹이가 사라진다(실측 240자).
            keep = max(0, int(keep * MAX_TEXT_LENGTH / len(cut_text)) - 64)
        text_out = cut_text

    return text_out


def _make_call_spec(entry):
    """카탈로그 항목에서 law_call 에 넘길 인자 예시를 만든다.

    왜 이 도우미가 필요한가:
        카탈로그를 조회한 뒤 실제 API를 호출하려면 target 코드뿐 아니라
        엔드포인트 구분(lawService 여부)을 파악해야 한다.
        law_call 로 즉시 연계할 수 있는 인자 뼈대를 제공한다.
        (필수 인자 설명문이 검색어로 오인 전송되지 않도록 params는 빈 딕셔너리로 비운다)
    """
    endpoint = entry.get("endpoint", "")
    service = "lawService" in endpoint
    return {
        "target": entry.get("target", ""),
        "service": service,
        "params": {},
    }


def _enrich_api_entry(entry):
    """엔트리 사본에 'call' 인자 예시와 필수 파라미터 설명을 붙인다."""
    out = dict(entry)
    required = {}
    for p in entry.get("request_params", []):
        val = p.get("value", "")
        p_name = p.get("name", "")
        if "필수" in val and p_name.strip() not in ("OC", "target", "type"):
            required[p_name] = p.get("desc", "")
    out["required"] = required
    out["call"] = _make_call_spec(entry)
    return out


def _validate_paging(offset, limit):
    """offset 과 limit 의 유효성을 검증한다.

    limit 은 1 이상의 정수, offset 은 0 이상의 정수만 받는다.
    아니면 ValueError 로 도구 오류(isError: True)를 발생시킨다.
    """
    if offset is None:
        off = 0
    else:
        if isinstance(offset, bool):
            raise ValueError("offset 은 0 이상의 정수여야 한다")
        try:
            off = int(offset)
        except (ValueError, TypeError):
            raise ValueError("offset 은 0 이상의 정수여야 한다")
        if off < 0:
            raise ValueError("offset 은 0 이상의 정수여야 한다")

    if limit is None:
        lim = 200
    else:
        if isinstance(limit, bool):
            raise ValueError("limit 은 1 이상의 정수여야 한다")
        try:
            lim = int(limit)
        except (ValueError, TypeError):
            raise ValueError("limit 은 1 이상의 정수여야 한다")
        if lim < 1:
            raise ValueError("limit 은 1 이상의 정수여야 한다")

    return off, lim


def _paginate(items, offset, limit):
    """목록을 offset 과 limit 범위만 자르고 total 과 next_offset 을 계산한다.

    왜 이 도우미가 필요한가:
        대용량 목록(조문 목록, 위임 체계 rows)을 한 번에 내려주면
        MCP 60,000자 상한에 걸려 불완전 잘림이 발생한다.
        offset/limit 으로 나누어 순차적으로 이어 읽을 수 있게 하되,
        전체 건수(total)와 다음 위치(next_offset)를 함께 돌려주어
        자른 것 자체가 불완전으로 오인되지 않도록 한다.
    """
    offset, limit = _validate_paging(offset, limit)
    total = len(items)
    paged = items[offset:offset + limit]
    has_more = (offset + len(paged)) < total
    next_offset = (offset + limit) if has_more else None
    return total, paged, next_offset


def _apply_pagination(items, raw_offset, raw_limit, missed):
    """목록을 페이징하고 complete 여부와 남은 범위를 missed 에 반영한다.

    원칙:
        1. 전체를 다 담았을 때만 complete: true 다 (offset==0, next_offset is None, not missed).
        2. offset 이 total 이상이면 complete: false 와 'offset 이 전체 건수를 넘었다'.
        3. 그 밖에는 complete: false, why 에 '전체 N건 중 a~b 번째만 줬다' 와 앞/뒤에 남은 범위를 적는다.
    """
    offset, limit = _validate_paging(raw_offset, raw_limit)
    total, paged, next_offset = _paginate(items, offset, limit)

    if (total > 0 and offset >= total) or (total == 0 and offset > 0):
        missed.append("offset 이 전체 건수를 넘었다 (offset=%d, total=%d)" % (offset, total))
    elif offset > 0 or next_offset is not None:
        a = offset + 1
        b = offset + len(paged)
        remains = []
        if offset > 0:
            remains.append("앞 1~%d번째(%d건)" % (offset, offset))
        if b < total:
            remains.append("뒤 %d~%d번째(%d건)" % (b + 1, total, total - b))
        msg = "전체 %d건 중 %d~%d 번째만 줬다" % (total, a, b)
        if remains:
            msg += " (%s 남음)." % ", ".join(remains)
        else:
            msg += "."
        if next_offset is not None:
            msg += " offset=%s 으로 이어 읽어라" % next_offset
        missed.append(msg)

    is_complete = (not missed) and (offset == 0) and (next_offset is None)
    return total, paged, next_offset, is_complete


def dispatch_tool(name, args):
    """도구 이름에 따라 실제 law_kit 기능을 호출한다."""
    args = args or {}
    # 필수 인자가 비면 None 이 그대로 법제처로 나가 엉뚱한 "결과" 가 온다.
    # 부르기 전에 막는다 - 호출자는 isError 로 무엇이 빠졌는지 본다.
    spec = next((t for t in TOOLS if t["name"] == name), None)
    if spec is not None:
        # 아래 분기가 받아 주는 별칭도 필수 인자로 인정한다(판독 반례).
        aliases = {"term": ("word",), "law": ("law_name",), "law_name": ("law",),
                   "name": ("law_name",), "query": ("topic",)}
        missing = [k for k in spec.get("inputSchema", {}).get("required", [])
                   if all(args.get(a) in (None, "") for a in (k,) + aliases.get(k, ()))]
        if missing:
            raise ValueError("필수 인자가 비었다: %s" % ", ".join(missing))
    if name == "law_brief":
        topic = args.get("topic")
        deep = args.get("deep", True)
        found = kit.brief(topic, deep=deep)
        # 통째로 주면 6만 자 상한에 걸려 잘린다(의료폐기물 실측). 입구 도구는
        # 사람이 읽는 요약(약 1,400자)과 못 본 것 목록만 준다 - 세부는
        # law_term·law_search·law_tree·law_annex 로 판다.
        # 요약에 안 실린 안쪽 불완전도 놓치지 않게, 판정은 원래 결과 전체로 한다.
        missed = []
        _walk(found, missed, "")
        return {"topic": topic, "report": kit.format_brief(found),
                "complete": not missed, "why": " | ".join(dict.fromkeys(missed))}
    elif name == "law_find":
        law_name = args.get("name")
        limit = args.get("limit", 20)
        include_historic = args.get("include_historic", True)
        found = kit.laws.find(law_name, limit=limit, include_historic=include_historic)
        # limit 을 꽉 채웠으면 뒤가 더 있는지 모른다 - 다 찾은 척하지 않는다.
        full = len(found) >= limit
        return {"name": law_name, "items": found, "complete": not full,
                "why": "limit %d 건을 채웠다 - 더 있을 수 있다. limit 을 늘려라" % limit
                if full else ""}
    elif name == "law_term":
        term = args.get("term") or args.get("word")
        law_filter = args.get("law") or args.get("law_name")
        with_text = bool(args.get("with_text", False))
        found = kit.terms.articles(term)
        # 통째로 주면 흔한 낱말(예: 건폐율)은 응답이 14만 자라 6만 자 상한에 잘린다(실측).
        # 기본 200건씩 잘라 total·next_offset 으로 이어 읽게 한다.
        missed = []
        _walk(found, missed, "")      # 판정은 요약이 아니라 원래 결과 전체로
        raw_articles = found.partial("articles") if hasattr(found, "partial") else dict.get(found, "articles")
        if raw_articles is None:
            raw_articles = found.partial("items") if hasattr(found, "partial") else dict.get(found, "items", [])
        raw_articles = raw_articles or []

        # 법령별 건수 집계 (전체 기준)
        counts = {}
        for r in raw_articles:
            lname = r.get("법령명") or "미분류"
            counts[lname] = counts.get(lname, 0) + 1
        counts = dict(sorted(counts.items(), key=lambda kv: -kv[1]))

        # law 필터가 있으면 해당 법령의 조문만
        if law_filter:
            key = "".join(str(law_filter).split())
            filtered = [r for r in raw_articles
                        if key in "".join(str(r.get("법령명", "")).split())]
        else:
            filtered = raw_articles

        # with_text 가 false 면 조문내용(본문) 제외
        articles_out = []
        for r in filtered:
            row = dict(r)
            if not with_text:
                row.pop("조문내용", None)
            articles_out.append(row)

        total, paged_articles, next_offset, is_complete = _apply_pagination(
            articles_out, args.get("offset"), args.get("limit"), missed
        )

        is_found = bool(dict.get(found, "found", True))

        kept_laws = list(dict.fromkeys(r.get("법령명") for r in articles_out if r.get("법령명")))
        out = {
            "term": term,
            "found": is_found,
            "total": total,
            "next_offset": next_offset,
            "complete": is_complete,
            "why": " | ".join(dict.fromkeys(missed)),
            "note": dict.get(found, "note", ""),
            "counts": counts,
            "laws": kept_laws,
            "articles": paged_articles,
        }
        if not is_found:
            out["지시"] = "용어 사전에 없는 낱말이다. 조문이 없다는 뜻이 아니다 - law_search 로 본문검색하라"

        if isinstance(found, dict):
            if "terms" in found:
                out["terms"] = found.partial("terms") if hasattr(found, "partial") else dict.get(found, "terms")
            if "items" in found:
                out["items"] = paged_articles
        return out
    elif name == "law_tree":
        law = args.get("law") or args.get("law_name")
        found = kit.tree.delegated(law)
        # 통째로 주면 주차장법 하나가 387만 자다(조례 4,839행, 실측) - 상한에
        # 잘려 앞부분만 남는다. 기본 200행씩 잘라 total·next_offset 으로 이어 읽게 한다.
        missed = []
        _walk(found, missed, "")      # 판정은 요약이 아니라 원래 결과 전체로

        raw_rows = found.partial("rows") if hasattr(found, "partial") else dict.get(found, "rows")
        raw_rows = raw_rows or []
        group, contains = args.get("group"), args.get("contains")
        filtered_rows = [
            r for r in raw_rows
            if (not group or r.get("위임구분") == group)
            and (not contains or contains in str(r.get("대상법령", "")))
        ]

        total, paged_rows, next_offset, is_complete = _apply_pagination(
            filtered_rows, args.get("offset"), args.get("limit"), missed
        )

        out = {
            "law": law,
            "total": total,
            "next_offset": next_offset,
            "complete": is_complete,
            "why": " | ".join(dict.fromkeys(missed)),
            "note": dict.get(found, "note", ""),
            "counts": kit.tree.summary(found),
            "하위법령": kit.tree.subordinate_laws(found),
            "rows": paged_rows,
        }
        return out
    elif name == "law_annex":
        law_name = args.get("law_name") or args.get("law")
        include_sub = args.get("include_subordinate", True)
        return kit.annex.of_law(law_name, include_subordinate=include_sub)
    elif name == "law_history":
        law_name = args.get("name") or args.get("law_name")
        alive, why = kit.history.is_current(law_name)
        succ = kit.history.successor(law_name)
        return {
            "name": law_name,
            # alive 가 None 이면 현행인지 구법인지 확인하지 못한 것이다.
            "is_current": {"alive": alive, "why": why, "complete": alive is not None},
            "successor": succ
        }
    elif name == "law_search":
        query = args.get("query") or args.get("topic")
        display = args.get("display", 20)
        return kit.search.across(query, display=display)
    elif name == "law_api":
        target = args.get("target")
        query = args.get("query")
        if target:
            entries = kit.catalog.describe(target)
            return {"target": target, "entries": [_enrich_api_entry(e) for e in entries]}
        entries = kit.catalog.search(query or "")
        return {"query": query or "", "entries": [_enrich_api_entry(e) for e in entries]}
    elif name == "law_call":
        target = args.get("target")
        service = args.get("service", False)
        params = args.get("params") or {}
        extra = {k: v for k, v in args.items() if k not in ("target", "service", "params")}
        call_params = dict(params)
        call_params.update(extra)
        return kit.client.call(target, service=service, **call_params)
    else:
        raise ValueError("알 수 없는 도구입니다: %s" % name)


def _write_msg(writer, msg):
    """sys.stdout.buffer 에 UTF-8 로 쓰고 즉시 flush 한다. print() 를 쓰지 않는다."""
    line = json.dumps(msg, ensure_ascii=False) + "\n"
    writer.write(line.encode("utf-8"))
    writer.flush()


def _log(text):
    """로그는 stderr 로만 출력한다."""
    # Windows 에서 sys.stderr 는 cp949 로 나가 클라이언트 로그에서 깨진다(실측).
    sys.stderr.buffer.write((text + "\n").encode("utf-8"))
    sys.stderr.flush()


def handle_message(req, writer):
    """단일 JSON-RPC 메시지를 처리한다."""
    if not isinstance(req, dict):
        return

    msg_id = req.get("id")
    method = req.get("method")
    params = req.get("params") or {}

    # 알림(notification)은 id가 없으므로 어떤 경우에도 응답하지 않는다
    is_notification = msg_id is None

    if method == "initialize":
        client_proto = params.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION
        resp = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": client_proto,
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION
                },
                "instructions": SERVER_INSTRUCTIONS
            }
        }
        _write_msg(writer, resp)
    elif method == "notifications/initialized":
        # 알림은 응답하지 않는다
        pass
    elif method == "ping":
        if not is_notification:
            _write_msg(writer, {"jsonrpc": "2.0", "id": msg_id, "result": {}})
    elif method == "tools/list":
        if not is_notification:
            _write_msg(writer, {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": TOOLS}
            })
    elif method == "tools/call":
        if is_notification:
            return
        tool_name = params.get("name")
        tool_args = params.get("arguments") or {}
        try:
            raw_res = dispatch_tool(tool_name, tool_args)
            text_body = serialize_response(raw_res)
            _write_msg(writer, {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": text_body}],
                    "isError": False
                }
            })
        except Incomplete as exc:
            # 불완전 예외는 complete: false 와 why, 지시, partial 을 담아 정상 전달
            text_body = serialize_response(None, is_incomplete_exc=True, exc_obj=exc)
            _write_msg(writer, {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": text_body}],
                    "isError": False
                }
            })
        except Exception as exc:
            # 네트워크 오류 등 기타 예외는 tools/call 결과의 isError: true 에 원인을 넣는다
            # JSON-RPC 오류로 삼키지 않는다
            err_msg = "도구 실행 실패: %s: %s" % (type(exc).__name__, exc)
            _write_msg(writer, {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [{"type": "text", "text": err_msg}],
                    "isError": True
                }
            })
    else:
        # 알 수 없는 메서드는 -32601 에러를 반환하되, 알림에는 응답하지 않는다
        if not is_notification:
            _write_msg(writer, {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32601,
                    "message": "Method not found: %s" % method
                }
            })


def main():
    """서버 메인 루프. sys.stdin.buffer 로 한 줄씩 읽어 처리한다."""
    reader = sys.stdin.buffer
    writer = sys.stdout.buffer

    if kit.client.is_demo_key():
        _log("경고: 데모 키(test)로 MCP 서버가 구동 중입니다. LAW_API_OC 환경변수를 설정하세요.")

    while True:
        try:
            line_bytes = reader.readline()
            if not line_bytes:
                # EOF 도착 시 종료
                break
            line_str = line_bytes.decode("utf-8", "replace").strip()
            if not line_str:
                continue

            try:
                req = json.loads(line_str)
            except Exception as exc:
                _write_msg(writer, {
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {
                        "code": -32700,
                        "message": "Parse error: %s" % exc
                    }
                })
                continue

            handle_message(req, writer)
        except Exception as exc:
            # 한 요청이 예외를 내도 서버는 죽지 않고 계속 돈다
            _log("서버 루프 처리 중 예외 발생: %s\n%s" % (exc, traceback.format_exc()))


if __name__ == "__main__":
    main()
