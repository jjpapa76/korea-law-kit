# -*- coding: utf-8 -*-
"""명령줄 입구. `python -m law_kit <명령> <말>`

모델을 거치지 않고 사람이 바로 쓸 수 있어야 한다. 그래야 "토큰 0" 이
말뿐이 아니게 된다.
"""
import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import law_kit as kit                          # noqa: E402

USAGE = """python -m law_kit <명령> <말>

  brief   <주제>        한 번에 훑기 (용어·8축·하위법령·별표)
  term    <낱말>        낱말 -> 조문
  tree    <법령명>      법률 -> 시행령·시행규칙·조례
  annex   <법령명>      별표 목록
  hist    <법령명>      연혁 / 옛 이름인지
  find    <법령명>      법 찾기 (ID·MST)
  axes    <주제>        8개 축 검색
  api     <낱말>        어떤 API 가 있나 (195건 설명서)
  cache                 캐시 상태
"""


def main(argv):
    if not argv:
        print(USAGE)
        return 0
    cmd, word = argv[0], " ".join(argv[1:])
    if cmd == "cache":
        print(json.dumps(kit.client.cache_stats(), ensure_ascii=False, indent=1))
        print(json.dumps(kit.catalog.health(), ensure_ascii=False, indent=1))
        return 0
    if not word:
        print(USAGE)
        return 2
    if cmd == "brief":
        print(kit.format_brief(kit.brief(word)))
    elif cmd == "term":
        found = kit.terms.articles(word)
        print("용어 '%s': %s" % (word, "등재됨" if found["found"] else "미등재"))
        if found["note"]:
            print(found["note"])
        for row in found.partial("articles") or []:
            print("  %-34s %-10s %s"
                  % (row["법령명"][:34], row["조"], row["조문내용"][:60]))
        print("조문 %d · 법령 %d" % (len(found.partial("articles") or []),
                                   len(found.partial("laws") or [])))
    elif cmd == "tree":
        branch = kit.tree.delegated(word)
        if not branch["ok"]:
            print("실패: %s" % branch.get("note") or branch.get("error"))
            return 1
        print(branch["법령정보"].get("법령명"), branch["법령정보"].get("시행일자"))
        for kind, count in kit.tree.summary(branch):
            print("  %-12s %4d" % (kind, count))
        print("하위 법령:", ", ".join(kit.tree.subordinate_laws(branch)) or "없음")
    elif cmd == "annex":
        sheets = kit.annex.of_law(word)
        sheet_rows = sheets.partial("items") or []
        print("별표 %d건%s" % (len(sheet_rows),
                             "" if sheets["complete"] else " (미완: %s)" % sheets["note"]))
        for row in sheet_rows[:40]:
            print("  %-10s %s" % (row["별표번호"], row["별표명"][:60]))
    elif cmd == "hist":
        info = kit.history.successor(word)
        print("%s -> %s (%s)" % (word, info["status"], info["why"]))
        if info.get("candidates"):
            print("  이어받았을 수 있는 법 후보:", ", ".join(info["candidates"]))
            print(" ", info.get("note", ""))
        history = kit.history.versions(word)
        print("  시행판 %d개%s" % (len(history.partial("versions") or []),
                                "" if history["complete"] else " (미완)"))
    elif cmd == "find":
        for hit in kit.laws.find(word):
            print("  %-46s %-8s ID=%s MST=%s [%s]"
                  % (hit["법령명"][:46], hit["법령구분"], hit["ID"], hit["MST"],
                     hit["찾은방법"]))
    elif cmd == "axes":
        print(kit.search.format_report(kit.search.across(word)))
    elif cmd == "api":
        for entry in kit.catalog.search(word):
            print("  %-16s %s" % (entry.get("target"), entry.get("name")))
    else:
        print(USAGE)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
