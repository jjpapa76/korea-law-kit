# -*- coding: utf-8 -*-
"""국가법령정보 공동활용 OPEN API 를 부르는 공통 바닥.

여러 프로그램이 **같은 부품**을 쓰기 위한 것이다. 그래서 보통의 HTTP 래퍼보다 지키는 것이 많다.

세 가지를 반드시 지킨다:

1. **토큰 0.** 파이썬만 돈다. LLM 을 부르지 않는다.

2. **거짓말하지 않는다.** 이게 제일 중요하다.
   못 물은 것과 물었는데 없는 것은 다르다. 끝까지 받은 것과 중간에
   잘린 것도 다르다. `Result` 가 그 넷을 **따로** 들고 다닌다.
   호출자가 "없다" 고 단정하기 전에 `result.complete` 를 봐야 한다.

   실측(2026-09-22)에서 이걸 안 지켜 사고가 났다. 이전 도구는 1,200건에서
   조용히 자르고, 페이징 중 오류가 나도 앞부분만 돌려주면서 호출자에게는
   "다 찾아봤다" 로 보이게 했다.

3. **캐시를 공유한다.** 법령은 하루에 몇 번씩 바뀌지 않는다.
   여러 프로그램이 같은 법을 각각 받으면 한도를 그만큼 더 쓴다.
   디스크에 남겨 두면 두 번째부터는 호출이 0이다.
"""
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request

SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
SERVICE_URL = "https://www.law.go.kr/DRF/lawService.do"
BASE = "https://www.law.go.kr"

def setting(name):
    """환경변수 하나. 프로세스 환경에 없으면 Windows 사용자 환경변수를 본다.

    MCP 클라이언트는 서버를 띄울 때 환경을 걸러 넘긴다(codex 는 부모
    환경을 아예 안 넘긴다). 그러면 사용자가 키를 설정해 두어도 서버는
    못 보고 데모 키로 조용히 떨어진다. 키를 클라이언트 설정 파일마다
    복사해 두면 키가 여섯 군데로 흩어진다 - 한 곳(HKCU\\Environment)에만
    두고 여기서 직접 읽는다.
    """
    value = os.environ.get(name)
    if value or os.name != "nt":
        return value
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return winreg.QueryValueEx(key, name)[0] or None
    except OSError:
        return None


#: 요청 사이 간격(초). 남의 서버다.
DELAY = 0.2
#: 캐시 기본 수명(초). 법령은 자주 안 바뀐다.
CACHE_TTL = 7 * 24 * 3600
#: 0건 응답 캐시 수명(초). 일시적 누락이나 장애 대비 1시간으로 짧게 둔다.
CACHE_TTL_EMPTY = 3600
#: 캐시 위치. 환경변수로 옮길 수 있게 둔다 - 여러 프로그램이 **같은 곳**을
#: 봐야 공유가 된다.
#: 여러 에이전트가 따로 띄워도 같은 캐시를 보게 하려고.
#: 설치 때 OS 환경변수를 바꾸지 않아도 되게.
CACHE_DIR = setting("LAW_KIT_CACHE") or os.path.expanduser(
    os.path.join("~", ".cache", "korea-law-kit"))
_UA = "law-kit/1.0"
#: 법제처는 Referer 없는 요청을 거부하기도 한다.
_REFERER = BASE

#: 응답 봉투에서 항목 목록이 아닌 것으로 걸러 낼 키.
_META_KEYS = frozenset((
    "target", "totalCnt", "page", "numOfRows", "키워드", "section",
    "resultCode", "resultMsg", "searchInfo", "numOfRow",
))


class Incomplete(Exception):
    """끝까지 받지 못한 결과를 그냥 쓰려고 했을 때 나는 오류.

    이 예외가 이 꾸러미의 **유일한 강제 장치**다. 문서에 "complete 를
    확인하세요" 라고 적어 두는 것은 장치가 아니다 - 아무도 안 읽고, 안
    읽은 채로 `if not result:` 라고 쓰면 조회 실패가 "0건" 이 된다.
    그래서 못 읽게 막는다. 받은 만큼이라도 보고 싶으면 `partial` 을
    쓰면 되고, 그때는 **부르는 쪽이 알고 쓰는 것**이다.
    """


class Result(object):
    """호출 결과. **무엇을 모르는지**까지 들고 다닌다.

    ok        요청이 성립했는가 (통신·파싱 성공)
    complete  **끝까지 받았는가.** False 면 "없다" 고 말하면 안 된다
    items     받은 항목들. **complete 가 아니면 읽으려 할 때 예외가 난다**
    partial   검사 없이 받은 만큼. 알고 쓰는 사람을 위한 문
    total     서버가 말한 총건수 (모르면 None)
    truncated 상한에 걸려 잘렸는가
    error     실패 사유
    """

    __slots__ = ("ok", "complete", "_items", "total", "truncated", "error",
                 "target", "cached", "pages")

    def __init__(self, target, ok=True, items=None, total=None,
                 complete=True, truncated=False, error="", cached=False,
                 pages=1):
        self.target = target
        self.ok = ok
        self._items = items or []
        self.total = total
        self.complete = complete and ok
        self.truncated = truncated
        self.error = error
        self.cached = cached
        self.pages = pages

    @property
    def items(self):
        if not self.complete:
            raise Incomplete(self.why_incomplete() +
                             " - 그래도 보려면 .partial 을 쓰라")
        return self._items

    @property
    def partial(self):
        """검사 없이 받은 만큼. 몇 건인지도 여기서 센다."""
        return self._items

    def __len__(self):
        return len(self.items)                # 불완전하면 여기서 막힌다

    def __iter__(self):
        return iter(self.items)               # 여기서도

    def __repr__(self):
        return ("<Result %s ok=%s complete=%s items=%d total=%s%s%s>"
                % (self.target, self.ok, self.complete, len(self._items),
                   self.total, " truncated" if self.truncated else "",
                   " cached" if self.cached else ""))

    def why_incomplete(self):
        """왜 완전하지 않은지 사람 말로. 완전하면 빈 문자열."""
        if self.complete:
            return ""
        if not self.ok:
            return "조회 실패: %s" % (self.error or "사유 미상")
        if self.truncated:
            return ("상한에 걸려 %s건 중 %d건만 받았다"
                    % (self.total or "?", len(self._items)))
        return "일부만 받았다(%d건). 끝까지 받지 못했다" % len(self._items)

    def as_dict(self):
        return {"target": self.target, "ok": self.ok,
                "complete": self.complete, "count": len(self._items),
                "total": self.total, "truncated": self.truncated,
                "error": self.error, "cached": self.cached,
                "pages": self.pages}


#: 인증키가 없을 때 쓰는 값. 법제처가 주는 결과가 제한된다.
DEMO_OC = "test"


def oc():
    """인증키. `LAW_API_OC` 환경변수로 준다.

    키를 저장소에 두지 않는다. 공개 저장소라서만이 아니라, 키가 코드에
    박혀 있으면 누가 어떤 키로 부르고 있는지 아무도 모르게 되기 때문이다.

    키가 없으면 `test` 로 떨어지는데, 그때는 **결과가 제한된다.**
    조용히 적게 나오는 것이 가장 헷갈리므로 `is_demo_key()` 로 확인할 수
    있게 해 둔다.
    """
    return (setting("LAW_API_OC")
            or setting("NATIONAL_LAW_API_OC")
            or DEMO_OC)


def is_demo_key():
    """지금 데모 키로 부르고 있는가. True 면 결과가 제한된 것일 수 있다."""
    return oc() == DEMO_OC


def _url_digest(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def _cache_path(url):
    digest = _url_digest(url)
    return os.path.join(CACHE_DIR, digest[:2], digest + ".json")


def _is_empty_response(body):
    """응답 본문이 0건(결과 없음)인지 검사한다."""
    if not body:
        return True
    try:
        payload = json.loads(body)
    except Exception:
        return False
    if not isinstance(payload, dict) or not payload:
        return True
    root = payload[list(payload.keys())[0]]
    if isinstance(root, str):
        return True
    if isinstance(root, dict):
        total = root.get("totalCnt")
        if total in (0, "0", "00"):
            return True
        for k, v in root.items():
            if k in _META_KEYS:
                continue
            if isinstance(v, list) and len(v) == 0:
                return True
    return False


def _cache_read(url, ttl):
    path = _cache_path(url)
    try:
        with open(path, encoding="utf-8") as handle:
            content = handle.read()
    except (OSError, ValueError):
        return None

    try:
        data = json.loads(content)
    except Exception:
        # JSON이 아니거나 깨진 파일: 버린다
        try:
            os.remove(path)
        except OSError:
            pass
        return None

    if not isinstance(data, dict):
        try:
            os.remove(path)
        except OSError:
            pass
        return None

    if data.get("_writer") != "law_kit.client" or data.get("_v") != 2:
        # 표지가 없거나 버전 불일치: 버린다
        try:
            os.remove(path)
        except OSError:
            pass
        return None

    expected_digest = _url_digest(url)
    if data.get("url_digest") != expected_digest:
        # 요청 URL 해시 불일치: 버린다
        try:
            os.remove(path)
        except OSError:
            pass
        return None

    body = data.get("body")
    if body is None:
        try:
            os.remove(path)
        except OSError:
            pass
        return None

    # 유효기간(TTL) 검사: 0건 응답은 CACHE_TTL_EMPTY(1시간) 적용
    is_empty = data.get("empty")
    if is_empty is None:
        is_empty = _is_empty_response(body)

    effective_ttl = min(ttl, CACHE_TTL_EMPTY) if is_empty else ttl
    try:
        if time.time() - os.path.getmtime(path) > effective_ttl:
            try:
                os.remove(path)
            except OSError:
                pass
            return None
    except OSError:
        return None

    return body


def _cache_write(url, body, _verified=False):
    path = _cache_path(url)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if _verified:
            digest = _url_digest(url)
            envelope = {
                "_writer": "law_kit.client",
                "_v": 2,
                "url_digest": digest,
                "body": body,
                "empty": _is_empty_response(body),
            }
            content = json.dumps(envelope, ensure_ascii=False)
        else:
            # 밖에서 직접 호출하거나 위조된 경우: 표지를 붙이지 않아 읽힐 때 거부됨
            content = body
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
    except OSError:
        pass                                  # 캐시 실패로 조회를 죽이지 않는다


def raw(url, params, ttl=CACHE_TTL, timeout=25):
    """본문 문자열을 돌려준다. (body, 캐시적중여부). 실패하면 예외."""
    full = url + "?" + urllib.parse.urlencode(params)
    if ttl:
        hit = _cache_read(full, ttl)
        if hit is not None:
            return hit, True
    request = urllib.request.Request(
        full, headers={"User-Agent": _UA, "Referer": _REFERER})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8", "replace")
    if ttl:
        _cache_write(full, body, _verified=True)
    return body, False


#: 법제처가 성공으로 쓰는 코드. 이것 말고는 실패다.
_OK_CODES = frozenset(("00", "0", "200", ""))


def server_error(payload):
    """서버가 오류를 말하고 있으면 그 말을 돌려준다. 아니면 빈 문자열.

    법제처는 인증키 만료·파라미터 오류·내부 장애를 **200 응답 본문 안의
    resultCode** 로 알린다. 그걸 메타로 여기고 넘기면 장애가 "검색 결과
    0건" 으로 둔갑한다. 그러면 "그런 법은 없습니다" 라는 답이 나간다.
    """
    if not isinstance(payload, dict) or not payload:
        return ""
    root = payload[list(payload.keys())[0]]
    if not isinstance(root, dict):
        return ""
    code = str(root.get("resultCode", "")).strip()
    if code and code not in _OK_CODES:
        return "서버 오류 %s: %s" % (code, root.get("resultMsg", ""))
    return ""


def unwrap(payload):
    """봉투 이름을 모른 채 (총건수, 항목들) 을 꺼낸다.

    봉투 이름은 33종이다(LawSearch·PrecSearch·CgmExpc…). 이름을 박아 두면
    새 API 에서 깨지므로 구조로 읽는다.

    **메타 딕셔너리를 항목으로 착각하지 않는다.** 이전 구현은 root 안에서
    처음 만난 dict 를 곧바로 결과로 반환해, 뒤에 있는 진짜 목록 50건이
    메타 1건으로 바뀌는 일이 있었다(레드팀 지적, 재현 확인).
    """
    if not isinstance(payload, dict) or not payload:
        return None, []
    root = payload[list(payload.keys())[0]]
    if not isinstance(root, dict):
        return None, []
    total = root.get("totalCnt")
    records, strings = [], []
    for key, value in root.items():
        if key in _META_KEYS:
            continue
        if isinstance(value, list):
            if value and isinstance(value[0], dict):
                return total, value           # 딕셔너리 목록이면 그것이 답이다
            strings.append(value)             # 문자열 목록은 답이 아니다
        elif isinstance(value, dict):
            records.append(value)
    if len(records) == 1:
        return total, records                 # 알맹이가 하나면 그것이 답이다
    if records:
        # 알맹이가 여럿이다(본문조회의 기본정보·조문·부칙·개정문…).
        # 그중 하나를 골라 주면 **고르지 않은 것은 사라진다.** 실제로
        # 법령 본문조회에서 조문 대신 개정문이 돌아온 적이 있다.
        # 고르지 말고 통째로 준다 - 부르는 쪽이 어디를 볼지 안다.
        return total, [root]
    if strings:
        return total, strings[0]
    return total, []


def recognized(payload, items, total):
    """이 응답을 **알아봤는가**. 못 알아본 것을 0건으로 세면 안 된다.

    다행히 법제처는 진짜 0건을 분명하게 말한다(실측 2026-09-22):
      - 목록조회는 항상 `totalCnt: "0"` 을 준다
      - 본문조회는 봉투 대신 안내 문장을 준다
        ({"Law": "일치하는 법령용어-조문 연계 정보가 없습니다..."})
    둘 다 아닌데 항목도 없다면, 그건 "없다" 가 아니라 **내가 못 읽은 것**이다.
    """
    if items:
        return True
    if total is not None:
        return True                           # 서버가 0건이라고 말했다
    if isinstance(payload, dict) and payload:
        root = payload[list(payload.keys())[0]]
        if isinstance(root, str) and root.strip():
            return True                       # 안내 문장을 줬다
    return False


def call(target, service=False, ttl=CACHE_TTL, **params):
    """아무 target 이나 같은 방식으로 한 번 호출한다."""
    query = {"OC": oc(), "target": target, "type": "JSON"}
    query.update({k: v for k, v in params.items() if v is not None})
    url = SERVICE_URL if service else SEARCH_URL
    try:
        body, cached = raw(url, query, ttl=ttl)
    except Exception as exc:
        return Result(target, ok=False, complete=False,
                      error="%s: %s" % (type(exc).__name__, exc))
    if body.lstrip().startswith("<"):
        # HTML 이 오면 그 target 이 이 요청 조합을 안 받는 것이다.
        return Result(target, ok=False, complete=False, cached=cached,
                      error="HTML 응답 - 지원하지 않는 요청")
    try:
        payload = json.loads(body)
    except Exception as exc:
        return Result(target, ok=False, complete=False, cached=cached,
                      error="파싱 실패: %s" % type(exc).__name__)
    fault = server_error(payload)
    if fault:
        return Result(target, ok=False, complete=False, cached=cached,
                      error=fault)
    total, items = unwrap(payload)
    if not recognized(payload, items, total):
        return Result(target, ok=True, complete=False, cached=cached,
                      total=total,
                      error="응답을 알아보지 못했다 - 0건인지 알 수 없다")
    # 한 번 부른 목록조회는 display 만큼만 온다. 총건수가 더 크면 앞부분이다 -
    # 그걸 완전하다고 하면 "257건 중 20건" 이 "20건이 전부" 가 된다(레드팀 재현).
    # call_all 은 페이지마다 ok 와 partial 만 보므로 이 표시에 흔들리지 않는다.
    try:
        expected = int(total)
    except (TypeError, ValueError):
        expected = None
    if not service and expected is not None and expected > len(items):
        return Result(target, items=items, total=total, cached=cached,
                      complete=False, truncated=True,
                      error="총 %d건 중 %d건만 받았다 - 전수는 call_all"
                            % (expected, len(items)))
    return Result(target, items=items, total=total, cached=cached)


def call_all(target, page_size=100, max_pages=40, delay=DELAY, service=False,
             ttl=CACHE_TTL, **params):
    """페이지를 **끝까지** 넘긴다. 못 넘겼으면 그 사실을 남긴다.

    파라미터 이름은 `page` 다. `pageNo` 로 보내면 서버가 무시하고 1페이지를
    반복해서 준다(실측). 그래서 카탈로그의 요청변수 이름을 지켜야 한다.
    """
    items, page, total, truncated, complete = [], 1, None, False, True
    error, short, seen = "", False, set()
    while True:
        result = call(target, service=service, ttl=ttl,
                      display=page_size, page=page, **params)
        if not result.ok:
            # 중간에 끊겼다. 지금까지 받은 것은 주되 **완전하지 않다**고 말한다.
            complete = False
            error = result.error
            break
        got = result.partial
        # **같은 페이지를 또 받았는가.** 페이지 변수를 서버가 무시하면
        # 1페이지가 계속 온다(`pageNo` 로 보냈을 때 실제로 그랬다).
        # 그걸 새 것으로 세면 총건수를 채우고 "전수" 라고 말하게 된다.
        mark = hashlib.sha1(
            json.dumps(got, ensure_ascii=False, sort_keys=True,
                       default=str).encode("utf-8")).hexdigest()
        if got and mark in seen:
            complete = False
            error = ("%d페이지에서 앞 페이지와 똑같은 내용이 왔다. "
                     "서버가 페이지를 넘기지 않는다" % page)
            break
        seen.add(mark)
        items.extend(got)
        total = result.total if total is None else total
        try:
            expected = int(total or 0)
        except (TypeError, ValueError):
            expected = 0
        if expected and len(items) >= expected:
            break
        if len(got) < page_size:
            # 한 페이지를 다 못 채웠다. 보통은 끝이라는 뜻이지만,
            # **서버가 페이지당 상한을 따로 두는 경우**도 있다. 총건수가
            # 있는데 아직 못 채웠다면 끝이 아니다 - 끝인 척하면 그 자리에서
            # 대량 누락이 된다.
            if expected and len(items) < expected:
                short = True
                complete = False
            break
        if page >= max_pages:
            truncated = True
            complete = False
            break
        page += 1
        if not result.cached:
            time.sleep(delay)
    if short and not error:
        error = ("서버가 한 페이지에 %d건만 줬다. 총 %s건 중 %d건에서 멈췄다"
                 % (page_size, total, len(items)))
    return Result(target, ok=not error, items=items, total=total,
                  complete=complete, truncated=truncated, error=error,
                  pages=page)


def download(link, out_path, timeout=60):
    """별표·서식 같은 첨부 파일을 받는다. link 는 상대경로여도 된다."""
    url = link if str(link).startswith("http") else BASE + str(link)
    request = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Referer": _REFERER})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "wb") as handle:
        handle.write(data)
    return {"path": out_path, "bytes": len(data), "url": url}


def cache_stats():
    """캐시에 무엇이 얼마나 쌓였는지. 공유가 되고 있는지 보는 창."""
    files = total = 0
    for root, _dirs, names in os.walk(CACHE_DIR):
        for name in names:
            files += 1
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return {"dir": CACHE_DIR, "files": files, "bytes": total}
