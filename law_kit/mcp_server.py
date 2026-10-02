# -*- coding: utf-8 -*-
"""표준 라이브러리만 쓰는 stdio 기반 국가법령 MCP 서버.

외부 패키지(`mcp` 패키지 포함) 없이 오직 파이썬 표준 라이브러리만으로
JSON-RPC 2.0 프로토콜을 구현해 국가법령 도구 11종을 제공한다.
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
import re
import sys
import traceback

import law_kit as kit
from law_kit.client import Incomplete, Result, is_demo_key

#: 법제처 응답 링크의 OC=<인증키> 마스킹용 정규식
_OC_MASK_RE = re.compile(r"(?i)\bOC=[^&\"'\s\\]+")


def _mask_oc(text):
    """문자열 안의 OC=<값> 을 OC=*** 로 가린다 (캐시·내부 호출 영향 없이 MCP 출력 직전에만)."""
    if not text or not isinstance(text, str):
        return text
    return _OC_MASK_RE.sub("OC=***", text)
from law_kit.shape import Answer, PAYLOAD_KEYS

#: MCP 기본 프로토콜 버전
DEFAULT_PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "korea-law"
SERVER_VERSION = "1.1.0"


def _build_id():
    """이 서버 코드의 지문. 클라이언트가 서버를 계속 켜 두면(keep-alive) 업데이트 뒤에도
    옛 코드가 답한다(2026-10-01 OmO 실측). initialize 응답에 지문을 실어 어느 판이 도는지 보이게 한다."""
    import hashlib
    try:
        with open(os.path.abspath(__file__), "rb") as f:
            return hashlib.sha1(f.read()).hexdigest()[:10]
    except OSError:
        return "unknown"


SERVER_BUILD = _build_id()

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

#: 근거가 확인된 클라이언트별 (최대 글자 수, 최대 UTF-8 바이트 수) 표.
#: 대소문자 무시 및 앞뒤 공백 제거 후 완전 일치만 적용한다.
#: 2026-10-01 clients.log 로 수집된 실제 clientInfo.name 만 반영한다.
#: antigravity-client, agy, cline, openclaude 등은 표에 넣지 않는다(기본값 적용).
CLIENT_LIMITS_TABLE = {
    # Claude Code (조사값 25,000토큰의 약 80%)
    "claude-code": (50000, 150000),
    # OpenAI Codex (조사값 10,000토큰의 약 80%)
    "codex-mcp-client": (20000, 60000),
    # OmO · senpi (조사값 51,200바이트의 약 80%)
    "senpi-mcp-client": (15000, 40000),
    # Grok (조사값 20,000바이트의 약 80%)
    "grok-shell-korea-law": (6000, 16000),
}

#: 모르는 이름이거나 이름이 없을 때의 기본 상한 (가장 작은 쪽)
DEFAULT_LIMITS = (6000, 16000)

#: 단일 응답 텍스트 최대 글자 수 상한 (이전 버전 호환용)
MAX_TEXT_LENGTH = 60000

#: agy(Antigravity CLI) 등 파일 저장 클라이언트 안내 문구 및 바이트 임계치 (3,800바이트 초과 시)
READ_GUIDE_TEXT = (
    "응답이 길어 클라이언트가 파일로 저장했을 수 있다. 저장됐다면 그 파일 전체를 읽은 뒤 답하라"
)
READ_GUIDE_THRESHOLD_BYTES = 3800

#: 현재 연결된 클라이언트 상태 (initialize 시 저장)
CURRENT_CLIENT = {
    "name": "",
    "version": ""
}


#: 환경변수 상한의 최솟값 (터무니없이 작게 설정되어 방어선이 무너지는 것을 방지)
MIN_MAX_CHARS = 2000
MIN_MAX_BYTES = 4000


def get_client_limits(client_name=None):
    """클라이언트 이름에 따른 (최대 글자 수, 최대 UTF-8 바이트 수)를 구한다.

    환경변수 KOREA_LAW_MCP_MAX_CHARS, KOREA_LAW_MCP_MAX_BYTES 가 있으면
    사용자 설정이 항상 최우선한다.
    이름 비교는 소문자·앞뒤 공백 제거 후 완전 일치만 허용한다.
    최솟값은 2000자 / 4000바이트로 고정하고, 작게 주면 최솟값으로 올리고 stderr 에 한 줄 알린다.
    숫자가 아니거나 0·음수면 무시하고 기본값을 유지한다.
    """
    if client_name is None:
        client_name = CURRENT_CLIENT.get("name", "")

    max_chars, max_bytes = DEFAULT_LIMITS
    if client_name:
        cleaned = str(client_name).strip().lower()
        max_chars, max_bytes = CLIENT_LIMITS_TABLE.get(cleaned, DEFAULT_LIMITS)

    env_chars = os.environ.get("KOREA_LAW_MCP_MAX_CHARS")
    if env_chars is not None and env_chars != "":
        try:
            val_chars = int(env_chars)
            if val_chars > 0:
                if val_chars < MIN_MAX_CHARS:
                    sys.stderr.write("KOREA_LAW_MCP_MAX_CHARS(%d) 가 최솟값(%d자)보다 작아 %d자로 올린다\n" % (
                        val_chars, MIN_MAX_CHARS, MIN_MAX_CHARS
                    ))
                    max_chars = MIN_MAX_CHARS
                else:
                    max_chars = val_chars
        except (ValueError, TypeError):
            pass

    env_bytes = os.environ.get("KOREA_LAW_MCP_MAX_BYTES")
    if env_bytes is not None and env_bytes != "":
        try:
            val_bytes = int(env_bytes)
            if val_bytes > 0:
                if val_bytes < MIN_MAX_BYTES:
                    sys.stderr.write("KOREA_LAW_MCP_MAX_BYTES(%d) 가 최솟값(%d바이트)보다 작아 %d바이트로 올린다\n" % (
                        val_bytes, MIN_MAX_BYTES, MIN_MAX_BYTES
                    ))
                    max_bytes = MIN_MAX_BYTES
                else:
                    max_bytes = val_bytes
        except (ValueError, TypeError):
            pass

    return max_chars, max_bytes


#: MCP 도구 11종 명세 정의
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
                "limit": {"type": "integer", "description": "조문 목록 최대 반환 건수 (기본값 50)"}
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
                "limit": {"type": "integer", "description": "위임 체계 rows 최대 반환 건수 (기본값 50)"}
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
        "description": "법령, 자치법규, 행정규칙, 판례, 헌재결정례, 법령해석례, 행정심판례, 감사원 사전컨설팅의 8개 축을 한꺼번에 검색한다. 넓은 탐색용. 특정 조문은 law_article, 법 이름은 law_find",
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
    },
    {
        "name": "law_article",
        "description": "특정 조문은 law_search 말고 law_article. 법령의 특정 조문(항·호 포함)을 법제처 JO 6자리 코드로 정밀 조회한다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "law": {"type": "string", "description": "법령명, 약칭, 구법명 또는 MST 일련번호"},
                "jo": {"type": "string", "description": "조 번호 (예: '제84조', '84', '제40조의3', '40의3')"},
                "hang": {"type": "string", "description": "선택적 항 번호 (예: '1', '①', '제1항')"}
            },
            "required": ["law", "jo"]
        }
    },
    {
        "name": "law_annex_text",
        "description": "별표 내용은 law_annex_text. 법령에 딸린 특정 별표나 서식의 본문 텍스트 내용을 직접 조회한다.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "law": {"type": "string", "description": "법령명 또는 MST 일련번호"},
                "annex": {"type": "string", "description": "별표 번호 (예: '1', '별표 2', '2의3')"}
            },
            "required": ["law", "annex"]
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
    here = where
    prefix = ("%s: " % here) if here else ""
    if isinstance(val, Result):
        if not val.complete:
            gaps.append("%s%s" % (prefix, val.why_incomplete() or "끝까지 받지 못했다"))
        return [_walk(x, gaps, where) for x in val.partial]
    if isinstance(val, dict):
        if isinstance(val, Answer) and not dict.get(val, "complete", True):
            gaps.append("%s%s" % (prefix, val.why()))
        elif not dict.get(val, "complete", True):
            why_text = dict.get(val, "why") or dict.get(val, "note") or "끝까지 받지 못했다"
            gaps.append("%s%s" % (prefix, why_text))
        if dict.get(val, "status") == "미확인":
            gaps.append("%s미확인 - %s" % (prefix, dict.get(val, "why", "")))
        for key in ("gaps", "incomplete"):
            listed = dict.get(val, key)
            if listed:
                gaps.append("%s%s: %s" % (prefix, key, "; ".join(str(g) for g in listed)))
        return {k: _walk(dict.get(val, k), gaps, "%s.%s" % (where, k) if where else str(k))
                for k in val.keys()}
    if isinstance(val, (list, tuple)):
        return [_walk(x, gaps, where) for x in val]
    if isinstance(val, str):
        return _mask_oc(val)
    return val


def _compute_count(data):
    """응답 데이터에서 주 목록의 항목 수(목록이 여러 개면 {이름: 개수})를 실제 길이로 센다."""
    if isinstance(data, (list, tuple)):
        return len(data)
    if not isinstance(data, dict):
        return 0

    # 0. 조문·별표 단건 조회 도구 특화 (law_article, law_annex_text)
    is_article = (
        ("조번호" in data and ("조문내용" in data or "조문제목" in data))
        or data.get("status") in ("조문 없음", "해당 항 없음")
        or ("jo" in data and ("law" in data or "MST" in data) and "articles" not in data)
    )
    if is_article:
        if data.get("status") in ("조문 없음", "해당 항 없음"):
            return 0
        if not data.get("complete", True):
            return 0
        return 1

    is_annex_text = ("annex" in data and ("law" in data or "MST" in data))
    if is_annex_text:
        if data.get("status") == "별표 없음":
            return 0
        if not data.get("complete", True):
            return 0
        return 1

    # 1. 특수 구조: axes (law_search)
    if "axes" in data and isinstance(data["axes"], dict):
        axes_counts = {}
        for target, ax in data["axes"].items():
            if isinstance(ax, dict):
                items = ax.get("items")
                if isinstance(items, (list, tuple)):
                    axes_counts[target] = len(items)
                elif "count" in ax and isinstance(ax["count"], int):
                    axes_counts[target] = ax["count"]
                else:
                    axes_counts[target] = 0
            else:
                axes_counts[target] = 0
        return axes_counts

    # 2. 최상위 딕셔너리에서 리스트 찾기
    list_map = {}
    for k, v in data.items():
        if k in ("counts", "incomplete", "이번에_담은"):
            # counts 는 요약 집계(튜플 리스트 등), incomplete 는 불완전 축 이름 리스트
            continue
        if isinstance(v, (list, tuple)):
            list_map[k] = len(v)

    # 3. 최상위에 리스트가 없는 경우 대표적 하위 목록 확인 (예: law_history 의 successor.candidates)
    if not list_map:
        if "successor" in data and isinstance(data["successor"], dict):
            cands = data["successor"].get("candidates")
            if isinstance(cands, (list, tuple)):
                return len(cands)
        return 0

    # 4. 리스트가 1개인 경우 -> 정수 반환
    if len(list_map) == 1:
        return next(iter(list_map.values()))

    # 5. 리스트가 여러 개인 경우 -> {이름: 개수} dict 반환
    return list_map


class _BodyListEntry(object):
    """몸통에서 줄일 수 있는 목록 정보를 담는 헬퍼 객체."""
    def __init__(self, name, container, key, tool_type, is_primary_paged=False):
        self.name = name
        self.container = container
        self.key = key
        self.tool_type = tool_type
        self.is_primary_paged = is_primary_paged
        self.orig_items = list(container[key])
        self.current_items = list(container[key])


def _sync_dependent_lists(data, tool_type):
    """주 목록이 축소되었을 때 종속 목록(조례, laws 등)을 일치시킨다."""
    if tool_type == "tree" and "rows" in data:
        seen = set()
        ords = []
        for r in data["rows"]:
            if isinstance(r, dict):
                kind = r.get("위임구분") or ""
                if "자치법규" in kind or "조례" in kind:
                    t = r.get("대상법령")
                    if t and t not in seen:
                        seen.add(t)
                        ords.append(t)
        data["조례"] = ords
    elif tool_type == "term" and "articles" in data:
        data["laws"] = list(dict.fromkeys(r.get("법령명") for r in data["articles"] if isinstance(r, dict) and r.get("법령명")))
        if "items" in data:
            data["items"] = data["articles"]


def _sync_headers_and_dependent_data(data, tool_type, list_entries, counts_shrink_msg=None):
    """주 목록이 축소되었을 때 종속 목록 및 머리 요약(축별, 법령체계, counts)을 실제 몸통 기준으로 일치시킨다."""
    # 1. law_search: 축별 머리 요약 및 각 axis 메타데이터 동기화
    if tool_type == "search" and "axes" in data and isinstance(data["axes"], dict):
        axes_summary = data.get("축별") if isinstance(data.get("축별"), dict) else None
        for target, ax in data["axes"].items():
            if not isinstance(ax, dict):
                continue
            lbl = ax.get("label") or target
            items = ax.get("items")
            if isinstance(items, list):
                cur_cnt = len(items)
                entry = next((e for e in list_entries if e.container is ax and e.key == "items"), None)
                orig_cnt = len(entry.orig_items) if entry else cur_cnt

                # 줄였을 때만 재계산
                if cur_cnt < orig_cnt:
                    ax["count"] = cur_cnt
                    ax["complete"] = False
                    ax["생략"] = orig_cnt - cur_cnt

                    if axes_summary is not None:
                        summary_item = axes_summary.get(lbl) or axes_summary.get(target)
                        if isinstance(summary_item, dict):
                            summary_item["받은"] = cur_cnt
                            summary_item["complete"] = False
                            summary_item["생략"] = orig_cnt - cur_cnt
                            # total 은 원래 결과 그대로 둔다

    # 2. law_tree: 조례 동기화 및 이번에_담은 집계 (counts, 법령체계는 원래 값 유지)
    elif tool_type == "tree" and "rows" in data and isinstance(data["rows"], list):
        rows = data["rows"]

        # (1) 조례 목록 동기화
        seen_ord = set()
        ords = []
        for r in rows:
            if isinstance(r, dict):
                kind = r.get("위임구분") or ""
                if "자치법규" in kind or "조례" in kind:
                    t = r.get("대상법령")
                    if t and t not in seen_ord:
                        seen_ord.add(t)
                        ords.append(t)
        data["조례"] = ords

        # (2) 이번에 담은 행 구분별 건수
        row_counts = {}
        for r in rows:
            if isinstance(r, dict):
                kind = r.get("위임구분") or "미분류"
                row_counts[kind] = row_counts.get(kind, 0) + 1
        GROUP_ORDER = ("시행령", "시행규칙", "위임행정규칙", "위임자치법규", "인용법령", "자체·미지정")
        ordered_counts = {k: row_counts[k] for k in GROUP_ORDER if k in row_counts}
        for k in row_counts:
            if k not in ordered_counts:
                ordered_counts[k] = row_counts[k]
        data["이번에_담은"] = ordered_counts

    # 3. law_term: laws, items 동기화 및 이번에_담은 집계 (counts 는 원래 값 유지)
    elif tool_type == "term" and "articles" in data and isinstance(data["articles"], list):
        articles = data["articles"]

        # (1) laws 및 items 동기화
        data["laws"] = list(dict.fromkeys(r.get("법령명") for r in articles if isinstance(r, dict) and r.get("법령명")))
        if "items" in data:
            data["items"] = articles

        # (2) 이번에 담은 조문 수 동기화
        data["이번에_담은"] = len(articles)


def _collect_body_lists(data):
    """머리 요약을 제외하고 몸통에서 줄일 수 있는 목록 엔트리들을 수집한다."""
    entries = []
    # 1. law_tree: 법령체계와 counts 는 머리이므로 줄이지 않고 rows 만 줄임 (조례는 rows 에 연동)
    if "법령체계" in data and "rows" in data:
        if isinstance(data["rows"], list):
            entries.append(_BodyListEntry("rows", data, "rows", "tree", is_primary_paged=True))
        return entries, "tree"

    # 2. law_term: counts 는 머리이므로 줄이지 않고 articles 만 줄임 (laws 는 articles 에 연동)
    if "articles" in data:
        if isinstance(data["articles"], list):
            entries.append(_BodyListEntry("articles", data, "articles", "term", is_primary_paged=True))
        return entries, "term"

    # 3. law_search: 축별 은 머리이므로 줄이지 않고 각 축의 items 목록을 줄임
    if "axes" in data and isinstance(data["axes"], dict):
        for target, ax in data["axes"].items():
            if isinstance(ax, dict) and "items" in ax and isinstance(ax["items"], list):
                lbl = ax.get("label") or target
                entries.append(_BodyListEntry(lbl, ax, "items", "search"))
        return entries, "search"

    # 4. 기타 도구들
    tool_type = "other"
    if "items" in data and isinstance(data["items"], list):
        entries.append(_BodyListEntry("items", data, "items", tool_type))
    if "entries" in data and isinstance(data["entries"], list):
        entries.append(_BodyListEntry("entries", data, "entries", tool_type))
    if "annexes" in data and isinstance(data["annexes"], list):
        entries.append(_BodyListEntry("annexes", data, "annexes", tool_type))
    if "successor" in data and isinstance(data["successor"], dict):
        cands = data["successor"].get("candidates")
        if isinstance(cands, list):
            entries.append(_BodyListEntry("candidates", data["successor"], "candidates", tool_type))

    header_keys = {"complete", "why", "지시", "경고", "count", "total", "next_offset", "offset",
                   "법령체계", "축별", "counts", "이번에_담은", "incomplete"}
    for k, v in data.items():
        if k not in header_keys and isinstance(v, list) and not any(e.key == k for e in entries):
            entries.append(_BodyListEntry(k, data, k, tool_type))

    return entries, tool_type


def _truncate_long_strings_in_obj(obj, max_str_len=150, path="", truncated_records=None):
    """긴 문자열을 앞부분만 남기고 생략 표기를 덧붙이며, 축소 정보를 기록한다."""
    if truncated_records is None:
        truncated_records = []

    if isinstance(obj, dict):
        skip_keys = {"법령체계", "축별", "counts", "이번에_담은", "안내", "검색어_정리", "why", "지시", "경고", "note", "_end", "읽기안내"}
        for k, v in list(obj.items()):
            if k in skip_keys:
                continue
            subpath = "%s.%s" % (path, k) if path else str(k)
            if isinstance(v, str) and len(v) > max_str_len + 30:
                omitted = len(v) - max_str_len
                obj[k] = v[:max_str_len] + "…(%d자 생략)" % omitted
                truncated_records.append((subpath, max_str_len, omitted))
            elif isinstance(v, (dict, list)):
                _truncate_long_strings_in_obj(v, max_str_len, subpath, truncated_records)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            subpath = "%s[%d]" % (path, i)
            if isinstance(item, str) and len(item) > max_str_len + 30:
                omitted = len(item) - max_str_len
                obj[i] = item[:max_str_len] + "…(%d자 생략)" % omitted
                truncated_records.append((subpath, max_str_len, omitted))
            elif isinstance(item, (dict, list)):
                _truncate_long_strings_in_obj(item, max_str_len, subpath, truncated_records)
    return truncated_records


def _format_str_trunc_msg(records):
    """긴 문자열 축소 기록을 why 안내 문구로 포맷한다."""
    if not records:
        return None
    unique_paths = list(dict.fromkeys(r[0] for r in records))
    total_omitted = sum(r[2] for r in records)
    n = records[0][1]
    if len(unique_paths) == 1:
        return "상한 때문에 %s 의 긴 글을 앞 %d자만 남겼다(%d자 생략)" % (unique_paths[0], n, total_omitted)
    else:
        return "상한 때문에 %d곳 의 긴 글을 앞 %d자만 남겼다(%d자 생략)" % (len(unique_paths), n, total_omitted)


def _shrink_counts_if_needed(data):
    """머리의 counts(법령별 건수)가 너무 커서 몸통이 0건이 되면, counts 를 상위 20개 + '그 밖 N개 법령' 으로 줄인다."""
    if not isinstance(data, dict):
        return False, None
    counts = data.get("counts")
    if not isinstance(counts, dict) or len(counts) <= 20:
        return False, None

    orig_items = list(counts.items())
    top20 = dict(orig_items[:20])
    rest_items = orig_items[20:]
    rest_n = len(rest_items)
    rest_sum = sum(v for _, v in rest_items if isinstance(v, (int, float)))

    key_rest = "그 밖 %d개 법령" % rest_n
    top20[key_rest] = rest_sum
    data["counts"] = top20

    msg = "상한 때문에 법령별 건수(counts)는 상위 20개만 담았다(그 밖 %d개 법령)" % rest_n
    return True, msg


def _truncate_single_item(item, max_str_len=30):
    """1건도 안 들어갈 때 해당 항목 안의 긴 문자열들을 대폭 축소한다."""
    if isinstance(item, dict):
        for k, v in list(item.items()):
            if isinstance(v, str) and len(v) > max_str_len:
                omitted = len(v) - max_str_len
                item[k] = v[:max_str_len] + "…(%d자 생략)" % omitted
            elif isinstance(v, (dict, list)):
                _truncate_single_item(v, max_str_len)
    elif isinstance(item, list):
        for i, val in enumerate(item):
            if isinstance(val, str) and len(val) > max_str_len:
                omitted = len(val) - max_str_len
                item[i] = val[:max_str_len] + "…(%d자 생략)" % omitted
            elif isinstance(val, (dict, list)):
                _truncate_single_item(val, max_str_len)


def _collect_nested_lists(obj, path=""):
    """객체 내부에서 축소 가능한 중첩 리스트들을 수집한다."""
    entries = []
    if isinstance(obj, dict):
        skip_keys = {"법령체계", "축별", "counts", "이번에_담은", "안내", "검색어_정리",
                     "why", "지시", "경고", "note", "_end", "읽기안내"}
        for k, v in obj.items():
            if k in skip_keys:
                continue
            subpath = "%s.%s" % (path, k) if path else str(k)
            if isinstance(v, list) and len(v) > 0:
                entries.append((k, obj, k, subpath, list(v)))
            elif isinstance(v, dict):
                entries.extend(_collect_nested_lists(v, subpath))
    return entries


def _shrink_single_large_item(item, test_fn, max_chars, max_bytes):
    """1건 덩어리가 너무 커서 안 들어갈 때 내부 중첩 목록(조문단위 등)과 긴 글을 축소한다.

    규칙:
        1. 그 안의 중첩 목록(조문단위 등)을 줄이고(앞에서부터 담을 수 있는 만큼, 뺀 수를 '생략' 에 기록).
        2. 긴 글을 줄인다.
        3. 이유(why)에 실제로 줄인 것과 숫자를 적는다.
    """
    nested_msgs = []
    nested = _collect_nested_lists(item)
    if nested:
        # 핵심 본문 목록(조문단위 등)과 부수 목록(부칙단위 등) 분류
        primary = [e for e in nested if "조문" in e[0] or "article" in e[0].lower()]
        if not primary:
            nested.sort(key=lambda x: len(x[4]), reverse=True)
            primary = [nested[0]]
            secondary = nested[1:]
        else:
            secondary = [e for e in nested if e not in primary]

        # 대형 부수 목록(len > 1)은 핵심 본문 목록을 담기 위해 먼저 비워둠
        for name, container, key, path, orig in secondary:
            if len(orig) > 1:
                container[key] = []
                container["생략"] = len(orig)

        # 1. 핵심 본문 목록(조문단위 등)을 앞에서부터 담을 수 있는 만큼 이진 탐색
        for name, container, key, path, orig in primary:
            low = 1
            high = len(orig)
            best_k = 0
            while low <= high:
                mid = (low + high) // 2
                container[key] = orig[:mid]
                omitted = len(orig) - mid
                if omitted > 0:
                    container["생략"] = omitted
                else:
                    container.pop("생략", None)
                fits, _, _ = test_fn()
                if fits:
                    best_k = mid
                    low = mid + 1
                else:
                    high = mid - 1

            if best_k == 0:
                best_k = 1

            container[key] = orig[:best_k]
            omitted = len(orig) - best_k
            if omitted > 0:
                container["생략"] = omitted
                nested_msgs.append("상한 때문에 %s 에서 %d건을 뺐다. 범위를 좁혀 다시 물어라" % (name, omitted))
            else:
                container.pop("생략", None)

        # 2. 남은 예산이 있으면 부수 목록들도 앞에서부터 담을 수 있는 만큼 담음
        for name, container, key, path, orig in secondary:
            if len(orig) <= 1:
                continue
            low = 1
            high = len(orig)
            best_k = 0
            while low <= high:
                mid = (low + high) // 2
                container[key] = orig[:mid]
                omitted = len(orig) - mid
                if omitted > 0:
                    container["생략"] = omitted
                else:
                    container.pop("생략", None)
                fits, _, _ = test_fn()
                if fits:
                    best_k = mid
                    low = mid + 1
                else:
                    high = mid - 1

            container[key] = orig[:best_k]
            omitted = len(orig) - best_k
            if omitted > 0:
                container["생략"] = omitted
                nested_msgs.append("상한 때문에 %s 에서 %d건을 뺐다. 범위를 좁혀 다시 물어라" % (name, omitted))
            else:
                container.pop("생략", None)

    # 3. 중첩 목록 축소 후에도 여전히 안 들어가면 긴 글을 단계적으로 축소
    fits, _, _ = test_fn()
    if not fits:
        for max_l in (100, 50, 30, 20, 10, 5):
            rec = _truncate_long_strings_in_obj(item, max_str_len=max_l)
            fits, _, _ = test_fn()
            if fits:
                msg = _format_str_trunc_msg(rec)
                if msg:
                    nested_msgs.append(msg)
                break
        else:
            _truncate_single_item(item, max_str_len=10)
            rec = _truncate_long_strings_in_obj(item, max_str_len=10)
            msg = _format_str_trunc_msg(rec)
            if msg:
                nested_msgs.append(msg)

    return nested_msgs


def _compute_current_why_and_next_offset(data, tool_type, list_entries, gaps,
                                         counts_shrink_msg=None, str_trunc_msg=None,
                                         nested_shrink_msgs=None):
    """현재 축소 상태에 따른 정확한 why 와 next_offset 을 계산한다."""
    is_paged = tool_type in ("tree", "term")
    why_parts = []
    next_offset_val = None

    if is_paged:
        orig_offset = data.get("offset", 0)
        if tool_type == "tree":
            kept_count = len(data.get("rows", []))
        else:
            kept_count = len(data.get("articles", []))
        next_offset_val = orig_offset + kept_count

        a = orig_offset + 1
        b = orig_offset + kept_count
        if kept_count > 0:
            paged_why = "상한 때문에 이번에는 %d~%d 번째만 담았다. offset=%d 으로 이어 읽어라" % (a, b, next_offset_val)
        else:
            paged_why = "상한 때문에 이번에는 0건만 담았다. offset=%d 으로 이어 읽어라" % next_offset_val

        if tool_type == "tree":
            # 원래 결과 자체에 법령체계 오류/불완전이 없었는지 확인
            has_orig_hierarchy_flaw = any("연혁 조회 실패" in g or "조회 실패" in g for g in gaps)
            if not has_orig_hierarchy_flaw:
                why_parts.append("법령체계(시행령·시행규칙·행정규칙)는 이 응답에 모두 들어 있다. 잘린 것은 조례 목록뿐")
            why_parts.append(paged_why)
        else:
            why_parts.append(paged_why)

        if str_trunc_msg:
            why_parts.append(str_trunc_msg)

        if nested_shrink_msgs:
            why_parts.extend(nested_shrink_msgs)

        if counts_shrink_msg:
            why_parts.append(counts_shrink_msg)

        if gaps:
            for g in gaps:
                if "번째만 줬다" not in g and "이어 읽어라" not in g and "상한 때문에" not in g and "잘린 것은 조례" not in g:
                    why_parts.append(g)
    else:
        for e in list_entries:
            omitted = len(e.orig_items) - len(e.current_items)
            if omitted > 0:
                why_parts.append("상한 때문에 %s 에서 %d건을 뺐다. 범위를 좁혀 다시 물어라" % (e.name, omitted))
        if str_trunc_msg:
            why_parts.append(str_trunc_msg)
        if nested_shrink_msgs:
            why_parts.extend(nested_shrink_msgs)
        if counts_shrink_msg:
            why_parts.append(counts_shrink_msg)
        if gaps:
            for g in gaps:
                if "상한 때문에" not in g:
                    why_parts.append(g)

    final_why = " | ".join(dict.fromkeys(why_parts))
    return final_why, next_offset_val


def _build_response_payload(data, is_complete, why, instruction, warning,
                            count_val, total_val, next_offset_val, read_guide=None):
    """머리, 도구별 요약, 몸통, _end 꼬리를 올바른 순서로 조립한다."""
    payload = {}
    payload["complete"] = is_complete
    if read_guide:
        payload["읽기안내"] = read_guide
    if not is_complete or why:
        if why:
            payload["why"] = why
    if instruction:
        payload["지시"] = instruction
    if warning:
        payload["경고"] = warning
    if isinstance(data, dict):
        if "안내" in data:
            payload["안내"] = data["안내"]
        if "검색어_정리" in data:
            payload["검색어_정리"] = data["검색어_정리"]
    payload["count"] = count_val
    if total_val is not None:
        payload["total"] = total_val
    if next_offset_val is not None:
        payload["next_offset"] = next_offset_val
    elif isinstance(data, dict) and "next_offset" in data:
        payload["next_offset"] = data["next_offset"]

    # 도구별 요약 (머리 - 줄이지 않고 앞부분에 배치)
    if isinstance(data, dict):
        if "법령체계" in data:
            payload["법령체계"] = data["법령체계"]
        if "축별" in data:
            payload["축별"] = data["축별"]
        if "counts" in data and ("term" in data or "articles" in data or "law" in data or "rows" in data):
            payload["counts"] = data["counts"]
        if "이번에_담은" in data:
            payload["이번에_담은"] = data["이번에_담은"]

    if is_complete:
        if isinstance(data, dict):
            header_keys = {"complete", "why", "지시", "경고", "안내", "검색어_정리", "count", "total", "next_offset", "offset",
                           "법령체계", "축별", "counts", "이번에_담은", "_end", "읽기안내"}
            for k, v in data.items():
                if k not in header_keys:
                    payload[k] = v
        else:
            payload["data"] = data
    else:
        payload["partial"] = data

    payload["_end"] = {
        "complete": is_complete,
        "count": count_val
    }
    return payload


def serialize_response(val, is_incomplete_exc=False, exc_obj=None, client_name=None):
    """Result/Answer/dict/list 를 한 곳에서 JSON 문자열로 직렬화한다.

    왜 한 곳에 모으는가:
        불완전 결과 방어, 데모 키 경고, 클라이언트별 상한 절단 방어는
        모든 도구 응답에 일관되게 적용되어야 하는 핵심 계약이다.
        여러 곳에 흩어지면 특정 도구에서 불완전 결과가 complete 로
        누출되는 사고가 재발할 수 있다.

    첫 키 고정 계약:
        codex 절단(가운데 절단) 및 모델 카운트 오류를 원천 차단하기 위해,
        모든 도구 응답(오류 응답 포함)의 앞 키 순서를 고정한다:
        "complete" -> "why"(불완전일 때) -> "지시"(있을 때) -> "경고"(데모 키일 때)
        -> "count"(주 목록 항목 수, 목록 여러 개면 {이름: 개수}) -> "total"(알면)
        -> "next_offset"(알면) -> 도구별 요약("법령체계", "축별", "counts")
        -> 실제 데이터(완전일 때) 또는 partial(불완전일 때) -> 맨 끝 "_end": {"complete": ..., "count": ...}
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

    is_complete = not gaps
    if isinstance(data, dict) and data.get("complete") is False:
        is_complete = False
        if data.get("why") and data["why"] not in gaps:
            gaps.append(data["why"])

    # 지시 추출
    instruction = None
    if not is_complete:
        instruction = INSTRUCTION_INCOMPLETE
        if isinstance(data, dict) and data.get("found") is False and "지시" in data:
            instruction = data["지시"]
    elif isinstance(data, dict) and "지시" in data:
        instruction = data["지시"]

    # 경고 추출
    warning = None
    if kit.client.is_demo_key():
        warning = DEMO_WARNING
    elif isinstance(data, dict) and "경고" in data:
        warning = data["경고"]

    # why 추출
    why = " | ".join(dict.fromkeys(gaps)) if gaps else None

    # count 및 total 계산 (직렬화 직전에 실제 리스트 길이로 측정)
    count_val = _compute_count(data)
    total_val = None
    if isinstance(val, Result) and val.total is not None:
        total_val = val.total
    elif isinstance(data, dict) and data.get("total") is not None:
        total_val = data["total"]

    next_offset_val = None
    if isinstance(data, dict) and "next_offset" in data:
        next_offset_val = data["next_offset"]

    max_chars, max_bytes = get_client_limits(client_name)

    # 초기 페이로드 빌드
    read_guide = None
    payload = _build_response_payload(data, is_complete, why, instruction, warning,
                                      count_val, total_val, next_offset_val, read_guide=None)
    text_out = json.dumps(payload, ensure_ascii=False, indent=2)
    text_out_bytes = text_out.encode("utf-8")

    # 3,800바이트 초과 시 읽기안내 추가
    if len(text_out_bytes) > READ_GUIDE_THRESHOLD_BYTES:
        read_guide = READ_GUIDE_TEXT
        payload = _build_response_payload(data, is_complete, why, instruction, warning,
                                          count_val, total_val, next_offset_val, read_guide=read_guide)
        text_out = json.dumps(payload, ensure_ascii=False, indent=2)
        text_out_bytes = text_out.encode("utf-8")

    # 상한 이내이면 바로 반환
    if len(text_out) <= max_chars and len(text_out_bytes) <= max_bytes:
        return _mask_oc(text_out)

    # --- 예산에 맞춰 담기 (상한 초과 시) ---
    is_complete = False
    instruction = INSTRUCTION_INCOMPLETE

    # 사전 단계: 긴 문자열 축소 및 why 문구 생성
    str_trunc_records = _truncate_long_strings_in_obj(data, max_str_len=150)
    str_trunc_msg = _format_str_trunc_msg(str_trunc_records)

    list_entries, tool_type = _collect_body_lists(data)
    is_paged = tool_type in ("tree", "term")
    counts_shrink_msg = None
    nested_shrink_msgs = []

    _sync_headers_and_dependent_data(data, tool_type, list_entries)

    cur_count = _compute_count(data)
    cur_why, cur_next_offset = _compute_current_why_and_next_offset(
        data, tool_type, list_entries, gaps, counts_shrink_msg, str_trunc_msg, nested_shrink_msgs
    )
    if isinstance(data, dict):
        data["why"] = cur_why
        data["complete"] = False
        if cur_next_offset is not None:
            data["next_offset"] = cur_next_offset

    temp_next_offset = cur_next_offset if cur_next_offset is not None else (data.get("next_offset") if isinstance(data, dict) else None)
    temp_payload_no_guide = _build_response_payload(
        data, False, cur_why, instruction, warning,
        cur_count, total_val, temp_next_offset, read_guide=None
    )
    t_no_guide = json.dumps(temp_payload_no_guide, ensure_ascii=False, indent=2)
    t_bytes_no_guide = t_no_guide.encode("utf-8")
    read_guide = READ_GUIDE_TEXT if len(t_bytes_no_guide) > READ_GUIDE_THRESHOLD_BYTES else None

    temp_payload = _build_response_payload(
        data, False, cur_why, instruction, warning,
        cur_count, total_val, temp_next_offset, read_guide=read_guide
    )
    temp_text = json.dumps(temp_payload, ensure_ascii=False, indent=2)
    temp_bytes = temp_text.encode("utf-8")

    if len(temp_text) <= max_chars and len(temp_bytes) <= max_bytes:
        text_out = temp_text
        text_out_bytes = temp_bytes
    else:
        # 본 단계: 가장 큰 목록부터 항목 수를 줄여(이진 탐색)
        processed = set()
        while True:
            active = [e for e in list_entries if len(e.current_items) > 0 and e not in processed]
            if not active:
                break
            active.sort(key=lambda e: len(e.current_items), reverse=True)
            target = active[0]
            processed.add(target)

            # 규칙: 목록의 마지막 1건은 빼지 않는다.
            low = 1 if len(target.orig_items) > 0 else 0
            high = len(target.current_items)
            best_k = 0

            def _test_k(k):
                target.container[target.key] = target.orig_items[:k]
                target.current_items = target.orig_items[:k]
                if not is_paged:
                    omitted_k = len(target.orig_items) - k
                    if omitted_k > 0:
                        target.container["생략"] = omitted_k
                    else:
                        target.container.pop("생략", None)
                _sync_headers_and_dependent_data(data, tool_type, list_entries, counts_shrink_msg)
                t_count = _compute_count(data)
                t_why, t_next = _compute_current_why_and_next_offset(
                    data, tool_type, list_entries, gaps, counts_shrink_msg, str_trunc_msg, nested_shrink_msgs
                )
                if isinstance(data, dict):
                    data["why"] = t_why
                    data["complete"] = False
                    if t_next is not None:
                        data["next_offset"] = t_next

                # read_guide 여부 체크
                p_chk = _build_response_payload(
                    data, False, t_why, instruction, warning,
                    t_count, total_val, t_next, read_guide=None
                )
                txt_chk = json.dumps(p_chk, ensure_ascii=False, indent=2)
                b_chk = txt_chk.encode("utf-8")
                rg = READ_GUIDE_TEXT if len(b_chk) > READ_GUIDE_THRESHOLD_BYTES else None
                if rg:
                    p_chk = _build_response_payload(
                        data, False, t_why, instruction, warning,
                        t_count, total_val, t_next, read_guide=rg
                    )
                    txt_chk = json.dumps(p_chk, ensure_ascii=False, indent=2)
                    b_chk = txt_chk.encode("utf-8")
                return (len(txt_chk) <= max_chars and len(b_chk) <= max_bytes), txt_chk, b_chk

            while low <= high:
                mid = (low + high) // 2
                fits, _, _ = _test_k(mid)
                if fits:
                    best_k = mid
                    low = mid + 1
                else:
                    high = mid - 1

            # 1건도 안 들어갈 때: 규칙에 따라 마지막 1건은 빼지 않는다.
            if best_k == 0 and len(target.orig_items) > 0:
                # 1. counts 축소 시도 (paged 일 때)
                if is_paged and not counts_shrink_msg:
                    shrunk, cmsg = _shrink_counts_if_needed(data)
                    if shrunk:
                        counts_shrink_msg = cmsg
                        low = 1
                        high = len(target.orig_items)
                        while low <= high:
                            mid = (low + high) // 2
                            fits, _, _ = _test_k(mid)
                            if fits:
                                best_k = mid
                                low = mid + 1
                            else:
                                high = mid - 1

                # 2. 여전히 1건도 안 들어가면 마지막 1건(target.orig_items[0])을 남기고 내부 중첩 목록과 긴 글을 줄인다
                if best_k == 0:
                    best_k = 1
                    target.container[target.key] = target.orig_items[:1]
                    target.current_items = target.orig_items[:1]
                    n_msgs = _shrink_single_large_item(target.orig_items[0], lambda: _test_k(1), max_chars, max_bytes)
                    if n_msgs:
                        nested_shrink_msgs.extend(n_msgs)

            target.container[target.key] = target.orig_items[:best_k]
            target.current_items = target.orig_items[:best_k]
            if not is_paged:
                omitted_final = len(target.orig_items) - best_k
                if omitted_final > 0:
                    target.container["생략"] = omitted_final
                else:
                    target.container.pop("생략", None)
            _sync_headers_and_dependent_data(data, tool_type, list_entries, counts_shrink_msg)

            cur_count = _compute_count(data)
            cur_why, cur_next_offset = _compute_current_why_and_next_offset(
                data, tool_type, list_entries, gaps, counts_shrink_msg, str_trunc_msg, nested_shrink_msgs
            )
            if isinstance(data, dict):
                data["why"] = cur_why
                data["complete"] = False
                if cur_next_offset is not None:
                    data["next_offset"] = cur_next_offset

            chk_payload = _build_response_payload(
                data, False, cur_why, instruction, warning,
                cur_count, total_val, cur_next_offset, read_guide=None
            )
            chk_text = json.dumps(chk_payload, ensure_ascii=False, indent=2)
            chk_bytes = chk_text.encode("utf-8")
            read_guide = READ_GUIDE_TEXT if len(chk_bytes) > READ_GUIDE_THRESHOLD_BYTES else None
            if read_guide:
                chk_payload = _build_response_payload(
                    data, False, cur_why, instruction, warning,
                    cur_count, total_val, cur_next_offset, read_guide=read_guide
                )
                chk_text = json.dumps(chk_payload, ensure_ascii=False, indent=2)
                chk_bytes = chk_text.encode("utf-8")

            if len(chk_text) <= max_chars and len(chk_bytes) <= max_bytes:
                break

    # 최종 메타데이터 및 생략 정보 반영
    if not is_paged:
        for e in list_entries:
            omitted = len(e.orig_items) - len(e.current_items)
            if omitted > 0:
                e.container["생략"] = omitted

    _sync_headers_and_dependent_data(data, tool_type, list_entries, counts_shrink_msg)

    final_why, final_next_offset = _compute_current_why_and_next_offset(
        data, tool_type, list_entries, gaps, counts_shrink_msg, str_trunc_msg, nested_shrink_msgs
    )
    if isinstance(data, dict):
        data["why"] = final_why
        data["complete"] = False
        if final_next_offset is not None:
            data["next_offset"] = final_next_offset

    count_val = _compute_count(data)

    final_payload = _build_response_payload(
        data, False, final_why, instruction, warning,
        count_val, total_val, final_next_offset, read_guide=None
    )
    text_out = json.dumps(final_payload, ensure_ascii=False, indent=2)
    text_out_bytes = text_out.encode("utf-8")

    if len(text_out_bytes) > READ_GUIDE_THRESHOLD_BYTES:
        read_guide = READ_GUIDE_TEXT
        final_payload = _build_response_payload(
            data, False, final_why, instruction, warning,
            count_val, total_val, final_next_offset, read_guide=read_guide
        )
        text_out = json.dumps(final_payload, ensure_ascii=False, indent=2)
        text_out_bytes = text_out.encode("utf-8")
    else:
        read_guide = None

    # 3. 머리만으로도 상한을 넘으면 그때만 지금의 문자열 절단을 쓴다.
    if len(text_out) > max_chars or len(text_out_bytes) > max_bytes:
        cut_why = "응답이 이 클라이언트 상한 %d자/%d바이트를 넘어 잘랐다 - offset/limit 또는 law 필터로 좁혀라" % (
            max_chars, max_bytes)
        if final_why:
            cut_why = final_why[:4000] + " | " + cut_why
        keep = min(len(text_out), max_chars, max_bytes)
        while True:
            cut_payload = {}
            cut_payload["complete"] = False
            if read_guide:
                cut_payload["읽기안내"] = read_guide
            cut_payload["why"] = cut_why
            cut_payload["지시"] = INSTRUCTION_INCOMPLETE
            if warning:
                cut_payload["경고"] = warning
            cut_payload["count"] = count_val
            if total_val is not None:
                cut_payload["total"] = total_val
            cut_payload["partial"] = text_out[:keep]
            cut_payload["_end"] = {
                "complete": False,
                "count": count_val
            }
            cut_text = json.dumps(cut_payload, ensure_ascii=False, indent=2)
            cut_bytes = cut_text.encode("utf-8")
            if (len(cut_text) <= max_chars and len(cut_bytes) <= max_bytes) or keep == 0:
                break
            ratio_c = max_chars / len(cut_text) if len(cut_text) > max_chars else 1.0
            ratio_b = max_bytes / len(cut_bytes) if len(cut_bytes) > max_bytes else 1.0
            ratio = min(ratio_c, ratio_b)
            step = max(0, int(keep * ratio) - 64)
            if step >= keep:
                step = max(0, keep - 64)
            keep = step
        text_out = cut_text

    # 모든 도구 출력 직전에 문자열 안의 OC=<값> 을 OC=*** 로 가린다 (모든 도구·모든 필드·중첩 포함)
    text_out = _mask_oc(text_out)

    # 3,800바이트 기준 최종 안내문 정합성 동기화
    final_bytes_len = len(text_out.encode("utf-8"))
    if final_bytes_len > READ_GUIDE_THRESHOLD_BYTES and '"읽기안내"' not in text_out[:200]:
        try:
            parsed_final = json.loads(text_out)
            reordered = {}
            for k, v in parsed_final.items():
                reordered[k] = v
                if k == "complete":
                    reordered["읽기안내"] = READ_GUIDE_TEXT
            text_out = json.dumps(reordered, ensure_ascii=False, indent=2)
        except Exception:
            pass
    elif final_bytes_len <= READ_GUIDE_THRESHOLD_BYTES and '"읽기안내"' in text_out:
        try:
            parsed_final = json.loads(text_out)
            if "읽기안내" in parsed_final:
                del parsed_final["읽기안내"]
                text_out = json.dumps(parsed_final, ensure_ascii=False, indent=2)
        except Exception:
            pass

    return _mask_oc(text_out)


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
        lim = 50
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
        aliases = {"term": ("word",), "law": ("law_name", "name"), "law_name": ("law", "name"),
                   "name": ("law_name", "law"), "query": ("topic",), "jo": ("article",),
                   "annex": ("annex_no",)}
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
        out = {"name": law_name, "items": found, "complete": not full,
               "why": "limit %d 건을 채웠다 - 더 있을 수 있다. limit 을 늘려라" % limit
               if full else ""}
        if not found:
            # 진짜 0건이지만 "그런 법은 없다" 로 읽히기 쉽다 - 이름이 틀렸을 가능성을 알린다.
            out["안내"] = ("이 이름으로 찾은 법이 없다. 띄어쓰기·약칭·옛 이름이 다를 수 있다 - "
                         "law_search 로 넓게 찾거나 이름을 바꿔 다시 물어라")
        return out
    elif name == "law_term":
        term = args.get("term") or args.get("word")
        law_filter = args.get("law") or args.get("law_name")
        with_text = bool(args.get("with_text", False))
        found = kit.terms.articles(term)
        # 통째로 주면 흔한 낱말(예: 건폐율)은 응답이 14만 자라 6만 자 상한에 잘린다(실측).
        # 기본 50건씩 잘라 total·next_offset 으로 이어 읽게 한다.
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

        offset_val, _ = _validate_paging(args.get("offset"), args.get("limit"))
        total, paged_articles, next_offset, is_complete = _apply_pagination(
            articles_out, args.get("offset"), args.get("limit"), missed
        )

        is_found = bool(dict.get(found, "found", True))

        kept_laws = list(dict.fromkeys(r.get("법령명") for r in articles_out if r.get("법령명")))
        out = {
            "term": term,
            "found": is_found,
            "total": total,
            "offset": offset_val,
            "next_offset": next_offset,
            "complete": is_complete,
            "why": " | ".join(dict.fromkeys(missed)),
            "note": dict.get(found, "note", ""),
            "counts": counts,
            "이번에_담은": len(paged_articles),
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
        # 잘려 앞부분만 남는다. 기본 50행씩 잘라 total·next_offset 으로 이어 읽게 한다.
        tree_missed = []
        _walk(found, tree_missed, "")      # 판정은 요약이 아니라 원래 결과 전체로

        raw_rows = found.partial("rows") if hasattr(found, "partial") else dict.get(found, "rows")
        raw_rows = raw_rows or []

        # 법령체계: {시행령: [이름...], 시행규칙: [이름...], 위임행정규칙: [이름...]} 전부 포함 (이어 읽기와 무관)
        hierarchy = {"시행령": [], "시행규칙": [], "위임행정규칙": []}
        for kind in ("시행령", "시행규칙", "위임행정규칙"):
            seen_h = set()
            names_h = []
            for r in raw_rows:
                if isinstance(r, dict) and r.get("위임구분") == kind:
                    target = r.get("대상법령")
                    if target and target not in seen_h:
                        seen_h.add(target)
                        names_h.append(target)
            hierarchy[kind] = names_h

        group, contains = args.get("group"), args.get("contains")
        filtered_rows = [
            r for r in raw_rows
            if (not group or r.get("위임구분") == group)
            and (not contains or contains in str(r.get("대상법령", "")))
        ]

        offset_val, _ = _validate_paging(args.get("offset"), args.get("limit"))
        paging_missed = []
        total, paged_rows, next_offset, is_complete = _apply_pagination(
            filtered_rows, args.get("offset"), args.get("limit"), paging_missed
        )

        missed = list(tree_missed)
        if paging_missed:
            missed.extend(paging_missed)
            missed.append("법령체계(시행령·시행규칙·행정규칙)는 이 응답에 모두 들어 있다. 잘린 것은 조례 목록뿐")

        # 자치법규(조례) 이름 목록 (paged_rows 에서 추출)
        paged_ordinances = []
        seen_ord = set()
        for r in paged_rows:
            if isinstance(r, dict):
                kind = r.get("위임구분") or ""
                if "자치법규" in kind or "조례" in kind:
                    target = r.get("대상법령")
                    if target and target not in seen_ord:
                        seen_ord.add(target)
                        paged_ordinances.append(target)

        # 이번에 담은 행 구분별 건수 (paged_rows 에서 추출)
        paged_counts = {}
        for r in paged_rows:
            if isinstance(r, dict):
                kind = r.get("위임구분") or "미분류"
                paged_counts[kind] = paged_counts.get(kind, 0) + 1
        GROUP_ORDER = ("시행령", "시행규칙", "위임행정규칙", "위임자치법규", "인용법령", "자체·미지정")
        ordered_paged = {k: paged_counts[k] for k in GROUP_ORDER if k in paged_counts}
        for k in paged_counts:
            if k not in ordered_paged:
                ordered_paged[k] = paged_counts[k]

        out = {
            "law": law,
            "total": total,
            "offset": offset_val,
            "next_offset": next_offset,
            "complete": is_complete,
            "why": " | ".join(dict.fromkeys(missed)),
            "note": dict.get(found, "note", ""),
            "counts": kit.tree.summary(found),
            "이번에_담은": ordered_paged,
            "법령체계": hierarchy,
            "조례": paged_ordinances,
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
        raw_query = args.get("query") or args.get("topic") or ""
        display = args.get("display", 20)
        cleaned_query = kit.search.clean_query(raw_query)
        found = kit.search.across(cleaned_query, display=display)
        axes = found.get("axes", {})
        axes_summary = {}
        for target, ax in axes.items():
            label = ax.get("label") or target
            t_val = ax.get("total")
            if t_val is not None:
                try:
                    t_val = int(t_val)
                except (ValueError, TypeError):
                    pass
            m_val = len(ax.get("items") or []) if ax.get("items") is not None else ax.get("count", 0)
            axes_summary[label] = {
                "complete": bool(ax.get("complete", False)),
                "total": t_val,
                "받은": m_val,
            }
        out = {
            "축별": axes_summary,
            "query": cleaned_query,
            "axes": axes,
            "incomplete": found.get("incomplete", []),
        }
        if cleaned_query != raw_query.strip():
            out["검색어_정리"] = cleaned_query
        if kit.search.has_article_number(raw_query):
            out["안내"] = "특정 조문은 law_article(law, jo) 로 보라"
        return out
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
        res = kit.client.call(target, service=service, **call_params)

        target_str = str(target).strip().lower() if target else ""
        has_jo = any(k.upper() == "JO" and v for k, v in call_params.items())
        if target_str in ("law", "eflaw") and service and not has_jo:
            # 법 전체 본문은 대개 상한을 넘는다. 잘리면 담기 단계가 complete:false 로
            # 바꾼다 - 여기서 미리 false 로 박으면 다 받은 결과까지 불완전으로 보인다.
            return {
                "target": res.target,
                "total": res.total,
                "count": len(res.partial),
                "items": res.partial,
                "complete": res.complete,
                "why": res.why_incomplete() if not res.complete else "",
                "안내": "법 전체 본문은 매우 크다 - JO(조번호)로 좁히거나 law_article 을 써라",
            }
        return res
    elif name == "law_article":
        law = args.get("law") or args.get("law_name") or args.get("name")
        jo = args.get("jo") or args.get("article")
        hang = args.get("hang")
        found = kit.articles.get_article(law, jo, hang=hang)
        if isinstance(found, dict) and dict.get(found, "status") == "조문 없음":
            # 몸통 안쪽 status 만으로는 놓치기 쉽다 - 머리에 올린다(조회 실패와 다르다).
            found = dict(found)
            found["안내"] = ("조회는 성공했고, 이 법(현행본)에 그 조문이 없다. "
                           "조번호·법령명(시행령/시행규칙인지)을 확인하라")
        return found
    elif name == "law_annex_text":
        law = args.get("law") or args.get("law_name") or args.get("name")
        annex_no = args.get("annex") or args.get("annex_no")
        return kit.articles.get_annex_text(law, annex_no)
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


def _log_client_to_cache(name, version):
    """initialize 시 수신한 clientInfo(name, version)와 시각을 캐시 폴더의 clients.log 에 덧붙인다.

    실패해도 서버는 계속 동작해야 한다.
    """
    try:
        import datetime
        cache_dir = getattr(kit.client, "CACHE_DIR", None)
        if not cache_dir:
            cache_dir = os.path.expanduser(os.path.join("~", ".cache", "korea-law-kit"))
        os.makedirs(cache_dir, exist_ok=True)
        log_path = os.path.join(cache_dir, "clients.log")
        now_str = datetime.datetime.now().isoformat()
        line = "%s\t%s\t%s\n" % (now_str, name, version)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


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
        client_info = params.get("clientInfo") or {}
        client_name = str(client_info.get("name") or "")
        client_version = str(client_info.get("version") or "")
        CURRENT_CLIENT["name"] = client_name
        CURRENT_CLIENT["version"] = client_version
        _log("client: %s %s" % (client_name, client_version))
        _log_client_to_cache(client_name, client_version)

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
                    "version": "%s+%s" % (SERVER_VERSION, SERVER_BUILD)
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
            err_msg = _mask_oc("도구 실행 실패: %s: %s" % (type(exc).__name__, exc))
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
