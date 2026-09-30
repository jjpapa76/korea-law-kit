# korea-law-kit — 어떤 법이든 토큰 0으로

국가법령정보 공동활용 OPEN API(법제처, 195종)를 **파이썬만으로** 부르고
확인하고 따져 보는 작은 꾸러미다. 여러 프로그램이 **같은 부품 하나**를 쓴다.

파이썬 패키지 이름은 `law_kit` 이다. 저장소 이름만 `korea-law-kit` 인데,
`law_kit` 은 일반명사라 검색이 안 되기 때문이다.

> 이어서 작업하는 사람은 [HANDOVER.md](HANDOVER.md) 를 먼저 읽는다 —
> 왜 이렇게 만들었는지, 판독에서 잡힌 결함 10건, 경쟁 MCP 비교, 남은 할 일.

## 가져다 쓰는 법

`law_kit` 폴더를 통째로 복사하고 경로에 올리면 끝이다. 설치할 것도,
`pip install` 할 것도 없다 — 표준 라이브러리만 쓴다.

```python
import law_kit

law_kit.terms.articles("도시혁신구역")    # 낱말 → 조문 (1.2초, 조문 24개·법령 13개)
law_kit.tree.delegated("주차장법")         # 법률 → 시행령·시행규칙·조례
law_kit.history.successor("도시계획법")    # 옛 이름 → 지금 이름
law_kit.annex.of_law("건축법")             # 별표 99건 + HWP/PDF 내려받기
law_kit.search.across("의료폐기물")        # 법·조례·행정규칙·판례·해석례 8축
law_kit.catalog.search("건폐율")           # 어떤 API 가 있나 (설명서 195건)
law_kit.client.call("prec", query="...")   # 195종 아무거나 직접
```

명령줄로도 된다. 모델을 거치지 않는다.

```
python -m law_kit brief 의료폐기물
python -m law_kit term 도시혁신구역
python -m law_kit tree 주차장법
python -m law_kit find 국토계획법
```

## MCP 서버 — Claude·codex·Gemini/Antigravity 어디서든

표준 라이브러리만 쓰는 stdio MCP 서버가 들어 있다. 설치 없이 파일 경로 하나로 붙는다.

```
python D:\...\korea-law-kit\korea_law_mcp.py
```

| 도구 | 하는 일 |
|---|---|
| `law_brief` | 주제 한 번에 훑기 (요약문 + 못 본 것) |
| `law_find` | 법 이름 → ID·MST |
| `law_term` | 낱말 → 조문 |
| `law_tree` | 법률 → 하위법령 건수·이름 (`group`·`contains` 로 행) |
| `law_annex` | 별표·서식 목록 |
| `law_history` | 현행/구법 + 승계 후보 |
| `law_search` | 8개 축 |
| `law_api` | 설명서 195건 검색 |
| `law_call` | 아무 target 직접 |

결과가 조금이라도 불완전하면 — 어느 깊이에 있든, 응답이 60,000자에서
잘렸든 — 최상위에 `"complete": false`, `why`, `지시` 가 붙고 받은 만큼은
`partial` 에 들어간다. 모델에게 "없다고 답하지 마라" 를 문장으로 돌려준다.

인증키는 `LAW_API_OC` 환경변수로 준다(옛 이름 `NATIONAL_LAW_API_OC` 도
읽는다). 프로세스 환경에 없으면 Windows 사용자 환경변수(레지스트리)를
직접 읽는다 - MCP 클라이언트가 환경을 걸러 넘겨도(codex 는 아예 안 넘긴다)
키를 한 곳에만 두면 된다. 둘 다 없으면 `test` 로 떨어지고 결과가 제한된다 -
`law_kit.client.is_demo_key()` 로 확인할 수 있고, MCP 응답에는 `경고` 가 붙는다.

### 모든 에이전트에 등록

Claude, Codex, Gemini/Antigravity, Grok, Hermes, OpenClaw, OpenCode, Cline 등
PC의 모든 AI 에이전트에 korea-law MCP 서버와 사용 규칙을 한 번에 등록한다.

```
python tools/install_agents.py          # 미리보기 (바꿀 것만 출력, 변경 없음)
python tools/install_agents.py --apply  # 실제 적용 (백업 후 멱등 등록)
```

인증키는 환경변수로 직접 등록한다:
```
setx LAW_API_OC <발급받은_인증키>
```


## 지키는 것 셋

**1. 토큰 0.** LLM 호출이 없다. 말로 두지 않고 시험으로 못 박았다
(`test_nothing_in_law_kit_calls_a_model`). 모델은 "무엇을 물을지" 만 정하고,
묻고 받고 세는 일은 전부 파이썬이 한다.

**2. 모르는 것을 모른다고 말한다.** 이게 이 꾸러미의 전부다.

조회 실패 · 상한 절단 · 페이징 중단 · 진짜 0건은 **서로 다른 것**이다.
하나로 뭉뚱그리면 "다 찾아봤는데 없습니다" 라는 거짓 보증이 나오고,
그 말을 믿고 결정이 내려진다.

그래서 문서로 부탁하지 않고 **막는다**. 불완전한 결과의 알맹이를 읽으려
하면 `Incomplete` 예외가 난다.

```python
result = law_kit.search.one("law", "의료폐기물", display=20)
result["complete"]        # False — 총 257건 중 앞 20건만 봤다
result["items"]           # Incomplete 예외
result.partial("items")   # 받은 20건. 알고 여는 문
```

받은 만큼이라도 쓰고 싶으면 `partial` 을 쓴다. 그때는 모르고 쓰는 것이
아니라 **알고 쓰는 것**이 된다.

전수가 필요하면 페이지를 끝까지 넘기는 쪽을 쓴다.

```python
law_kit.search.all_pages("law", "의료폐기물", search_mode=2)   # 257/257, complete=True
```

**3. 캐시를 공유한다.** 법은 하루에 몇 번씩 바뀌지 않는다. 여러 프로그램이
같은 법을 각각 받으면 하루 한도를 그만큼 더 쓴다. 기본으로 사용자 홈의
공유 폴더(`~/.cache/korea-law-kit`)를 바라보므로(필요 시 `LAW_KIT_CACHE` 로
변경 가능) 두 번째부터는 호출이 0이다.

실측(2026-09-22, 낱말 5개): 처음 4.08초 → 두 번째 **0.007초 (약 580배)**,
API 호출 0회.

## 무엇이 어디에 있나

| 파일 | 하는 일 | 쓰는 API |
|---|---|---|
| `client.py` | 호출 · 디스크 캐시 · 실패/절단 구분 | 전부 |
| `shape.py` | 응답 모양 펴기(평행 리스트) · `Answer` | — |
| `terms.py` | 낱말 → 조문 | `lstrmRltJo` |
| `tree.py` | 법률 → 시행령 · 시행규칙 · 조례 | `lsDelegated` |
| `history.py` | 연혁 · 구법명 | `eflaw`, `oldAndNew` |
| `annex.py` | 별표 · 서식 + 파일 | `licbyl` |
| `laws.py` | 이름 → 법령ID (띄어쓰기 · 약칭 · 구법) | `law`, `lsAbrv`, `eflaw` |
| `search.py` | 8개 축 한꺼번에 | `law` `ordin` `admrul` `prec` `detc` `expc` `decc` `baiPvcs` |
| `catalog.py` | 어떤 API 가 있나 (195건 설명서) | — (파일 읽기) |

## 알아 둘 것 — 실측으로 확인한 함정

- **`lsHistory` 는 JSON·XML 을 주지 않는다.** ID·MST·query 와 JSON·XML
  여섯 조합 전부 HTML 뷰어 페이지가 온다. 연혁은 `eflaw` 가 한다 —
  폐지·제명변경된 판까지 있어서 구법명이 여기서 나온다.
- **페이지 변수는 `page` 다.** `pageNo` 로 보내면 서버가 무시하고
  1페이지를 반복해 준다. 4페이지를 받은 줄 알았는데 같은 100건을 네 번
  받고 있었다.
- **`Referer` 헤더가 없으면 거부당하는 경우가 있다.** `client` 가 넣는다.
- **위임구분마다 칸 이름이 다르다.** 조례는 `위임자치법규조문정보` 안에
  이름이 있다. `tree` 가 꼬리말로 찾아 일반화했다.
- **별표는 시행령·시행규칙에 붙는다.** "건축법 별표" 를 정확 일치로 찾으면
  0건이고, 실제로는 99건이 하위법령에 있다. `annex.of_law` 가 기본으로
  포함한다(`건축법대장법` 같은 남의 법은 뺀다).
- **법령용어 미등재 ≠ 관련 조문 없음.** `terms` 가 0건을 줄 때는 그
  낱말이 지식베이스에 없다는 뜻이다. 본문검색(`search`)을 따로 해야 한다.

## 첨부 파일

별표의 HWP/PDF 는 `annex.fetch()` 로 받는다. 받은 파일은 모델 컨텍스트에
통째로 넣지 말고 `kordoc`(hwp) 또는 `anydoc`(pdf) 으로 Markdown 으로
바꿔서 필요한 부분만 읽는다.

## 설명서 다시 긁기

195건 API 설명서(`law_kit/law_api_catalog.json`)는 저장소 루트의
`law_api_catalog.py` 가 긁어 쓴다. 이미 수집돼 있으니 다시 긁을 일은
거의 없다.

## 시험

```
python -m pytest        # 93건. 망에 나가지 않는다
```

시험은 대부분 "잘 찾는가" 가 아니라 **"못 찾았을 때 못 찾았다고 하는가"**
를 본다. 잘 찾는 것은 실물로 확인하면 되지만, 거짓 보증은 실물을 봐도
안 보인다 - 그럴듯한 답이 나오기 때문이다.

## 라이선스

MIT. 인증키는 코드에 없다 - `LAW_API_OC` 환경변수로만 받는다.

```
python law_api_catalog.py harvest
python law_api_catalog.py find 사전컨설팅
```
