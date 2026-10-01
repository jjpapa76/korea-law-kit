<!-- korea-law-kit:begin -->
## 한국 법령 조회 — korea-law MCP

한국 법령·시행령·조례·행정규칙·판례·별표를 확인할 때는 기억이나 웹검색으로 답하지 말고
MCP 서버 `korea-law` 도구(law_brief, law_find, law_term, law_tree, law_annex, law_history,
law_search, law_api, law_call, law_article, law_annex_text)를 쓴다. 저장소: {REPO}

- 응답에 "complete": false 가 있으면 "없다"·"전부다"라고 답하지 마라. why 를 그대로 알리고 partial 은 "일부"라고 밝혀 쓴다.
- 특정 조문은 law_search 말고 law_article 로 짚고, 별표 본문 내용은 law_annex_text 로 본다.
- law_term 0건은 "용어 미등재"다. 조문이 없다는 뜻이 아니다 → law_search 로 본문검색.
- 수치 기준은 대부분 별표(law_annex·law_annex_text)에 있다. 승계 후보(law_history)는 후보일 뿐 단정하지 않는다.
<!-- korea-law-kit:end -->
