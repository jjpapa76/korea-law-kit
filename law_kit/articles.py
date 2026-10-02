# -*- coding: utf-8 -*-
"""특정 조문(law_article) 및 별표 본문 텍스트(law_annex_text) 조회.

조번호를 빼면 법 전체(수십만 자)가 오므로, 특정 조문만 짚어 법제처 JO 6자리 코드로
정밀 조회하여 평평한 텍스트 필드로 제공한다.
별표는 목록/파일링크뿐 아니라 법 본문 응답에서 별표 본문 텍스트를 꺼내 제공한다.
"""
import re
from . import client, laws, history
from .client import Incomplete, Result
from .shape import text


def parse_jo_and_hang(jo, hang=None):
    """조 번호 및 선택적 항 번호를 파싱하여 (JO 6자리 코드, hang)을 반환한다.

    - "84-1", "84.1", "84_1" 처럼 모호한 구분자는 ValueError로 거절한다.
    - "제40조의0" 처럼 가지번호가 명시적으로 0인 경우 ValueError("가지번호는 1 이상")로 거절한다.
    - "제84조제1항" 처럼 조와 항이 함께 주어지면 ("008400", "1")로 분리한다.
    """
    if jo is None:
        raise ValueError("조 번호가 비어 있습니다")
    s = str(jo).strip()
    if not s:
        raise ValueError("조 번호가 비어 있습니다")

    # 1. 모호한 형식 거절: 84-1, 84.1, 84_1 등
    m_ambig = re.match(r"^(?:제\s*)?(\d+)\s*[-._]\s*(\d+)$", s)
    if m_ambig:
        main_num = int(m_ambig.group(1))
        sub_num = int(m_ambig.group(2))
        raise ValueError(
            "%s 은 모호하다 - 제%d조의%d 또는 제%d조 제%d항(hang=%d)으로 적어라"
            % (s, main_num, sub_num, main_num, sub_num, sub_num)
        )

    # 2. 이미 6자리 숫자인 경우
    if re.match(r"^\d{6}$", s):
        return s, hang

    extracted_hang = None

    # 3. 조와 항이 함께 있는 경우 분리
    # 예: 제84조제1항, 제84조 제1항, 84조 1항, 제84조의3제1항, 제84조 ①, 84조①
    m_hang = re.search(r"(?:(?:\s*제\s*|\s+)?(\d+)\s*항|\s*([①-⑳]))\s*$", s)
    if m_hang:
        hang_num = m_hang.group(1) or m_hang.group(2)
        extracted_hang = hang_num
        s_jo = s[:m_hang.start()].strip()
    else:
        s_jo = s

    if not s_jo:
        raise ValueError("유효하지 않은 조 번호 형식입니다: %r" % jo)

    # 4. 조 번호 파싱
    # 허용: "제40조의3", "40조의3", "40의3", "제 40 조", "84", "제84조"
    m_jo = re.match(r"^(?:제\s*)?(\d+)(?:\s*조)?(?:\s*(?:의)\s*(\d+))?$", s_jo)
    if not m_jo:
        raise ValueError("유효하지 않은 조 번호 형식입니다: %r" % jo)

    main_num = int(m_jo.group(1))
    has_sub = m_jo.group(2) is not None
    sub_num = int(m_jo.group(2)) if has_sub else 0

    if main_num <= 0 or main_num > 9999:
        raise ValueError("조 번호는 1~9999 범위여야 합니다: %d" % main_num)

    # 가지번호가 명시적으로 주어진 경우 1 이상이어야 함
    if has_sub and sub_num < 1:
        raise ValueError("가지번호는 1 이상이어야 합니다: %d" % sub_num)
    if sub_num > 99:
        raise ValueError("가지 번호는 0~99 범위여야 합니다: %d" % sub_num)

    jo_code = "%04d%02d" % (main_num, sub_num)
    final_hang = hang if hang is not None else extracted_hang
    return jo_code, final_hang


def parse_jo(jo):
    """조 번호 문자열을 법제처 JO 6자리 코드(조 4자리 + 가지 2자리)로 변환한다.

    예:
        "제84조", "84" -> "008400"
        "제40조의3", "40의3", "제40조의 3" -> "004003"
        "004003" -> "004003"
    잘못된 jo는 ValueError를 발생시킨다.
    """
    jo_code, _ = parse_jo_and_hang(jo)
    return jo_code


def parse_annex_spec(annex):
    """별표/서식 입력 문자열에서 (종류, 번호, 가지번호)를 추출한다.

    종류 판별:
        "서식 N", "별지 N", "별지 제N호서식", "제N호서식" 등 -> "서식"
        그 밖 ("N", "별표 N", "[별표 N]", "N의M") -> "별표"
    규칙:
        - "별표"·"서식"·"별지" 키워드가 있으면 그 키워드 바로 뒤의 번호만 읽는다 ("별지 제1호서식" 포함).
        - 키워드가 없으면 입력 전체가 번호 형식("1", "2의3")일 때만 받고,
          숫자가 둘 이상 흩어져 있으면 ValueError("별표 번호가 모호하다 - '별표 1' 처럼 적어라").
    """
    if annex is None:
        raise ValueError("별표 번호가 비어 있습니다")
    s = str(annex).strip()
    if not s:
        raise ValueError("별표 번호가 비어 있습니다")

    has_keyword = bool(re.search(r"별표|서식|별지", s))

    if has_keyword:
        if re.search(r"서식|별지", s):
            kind = "서식"
        else:
            kind = "별표"

        # "별표"·"서식"·"별지" 키워드 바로 뒤의 번호만 읽기
        m = re.search(r"(?:별표|별지|서식)\s*(?:제\s*)?(\d+)(?:\s*호)?(?:\s*(?:의|-)\s*(\d+))?", s)
        if not m:
            m = re.search(r"(?:제\s*)?(\d+)(?:\s*호)?(?:\s*(?:의|-)\s*(\d+))?\s*서식", s)

        if not m:
            raise ValueError("유효하지 않은 별표 번호입니다: %r" % annex)
        # "별표 1, 2" 처럼 번호 뒤에 숫자가 더 있으면 뒤를 버리지 않고 거절한다
        if re.search(r"\d", s[m.end():]):
            raise ValueError("별표는 한 번에 하나만 - '별표 1' 처럼 하나씩 적어라: %r" % annex)

        main_num = int(m.group(1))
        sub_num = int(m.group(2)) if m.group(2) else 0
        return kind, main_num, sub_num

    else:
        # 키워드가 없는 경우: 입력 전체가 순수 번호 형식일 때만 허용
        m_pure = re.match(r"^(?:제\s*)?(\d+)(?:\s*(?:의|-)\s*(\d+))?$", s)
        if m_pure:
            kind = "별표"
            main_num = int(m_pure.group(1))
            sub_num = int(m_pure.group(2)) if m_pure.group(2) else 0
            return kind, main_num, sub_num

        # 숫자가 둘 이상 흩어져 있으면 모호함 에러
        digits = re.findall(r"\d+", s)
        if len(digits) >= 2:
            raise ValueError("별표 번호가 모호하다 - '별표 1' 처럼 적어라")

        raise ValueError("유효하지 않은 별표 번호입니다: %r" % annex)


def parse_annex_num(annex):
    """별표 번호 문자열에서 (번호, 가지번호)를 추출한다.

    예:
        "1", "별표 1", "0001" -> (1, 0)
        "별표 2의3", "2의3" -> (2, 3)
    """
    _kind, main_num, sub_num = parse_annex_spec(annex)
    return main_num, sub_num


def _resolve_law_info(law):
    """law 인자로부터 법령 상세 메타정보 딕셔너리를 반환한다.

    반환:
        {
            "mst": str or None,
            "name": str,
            "candidates": list,
            "error": str or None,
            "is_historic": bool,
            "efYd": str,
        }
    """
    s = str(law).strip()
    if s.isdigit() and len(s) >= 5:
        return {
            "mst": s,
            "name": "",
            "candidates": [],
            "error": None,
            "is_historic": False,
            "efYd": "",
        }

    hits = laws.find(s)
    if not hits:
        return {
            "mst": None,
            "name": s,
            "candidates": [],
            "error": "법령 '%s'을(를) 찾을 수 없습니다" % s,
            "is_historic": False,
            "efYd": "",
        }

    if len(hits) == 1:
        h = hits[0]
        is_historic = (h.get("찾은방법") == "historic" or h.get("현행") != "현행")
        target_mst = h.get("MST")
        target_efyd = text(h.get("시행일자")) or ""
        base_mst = None
        base_efyd = None

        if is_historic:
            law_title = h.get("법령명") or s
            v = history.versions(law_title)
            v_complete = v.get("complete") if isinstance(v, dict) else getattr(v, "complete", True)
            v_ok = v.get("ok") if isinstance(v, dict) else getattr(v, "ok", True)
            if not v_ok or not v_complete:
                return {
                    "mst": None,
                    "name": h.get("법령명") or s,
                    "candidates": [],
                    "error": "연혁 목록을 끝까지 받지 못해 폐지 직전 판을 확정할 수 없다",
                    "is_historic": True,
                    "efYd": "",
                    "기준판_MST": None,
                    "기준판_시행일자": None,
                }

            rows = v.partial("versions") if hasattr(v, "partial") else dict.get(v, "versions") or []
            sorted_rows = sorted(rows, key=lambda r: str(r.get("시행일자") or ""), reverse=True)
            base_idx = None
            for idx, r in enumerate(sorted_rows):
                jg = (r.get("제개정") or "").strip()
                if jg in ("폐지", "타법폐지"):
                    continue
                base_mst = r.get("MST")
                base_efyd = text(r.get("시행일자")) or ""
                base_idx = idx
                break

            if base_mst:
                target_mst = base_mst
                target_efyd = base_efyd
            else:
                base_mst = target_mst
                base_efyd = target_efyd
                base_idx = 0 if sorted_rows else None

            # 기준판 직후 판 판정: sorted_rows(시행일자 내림차순)에서 base_idx - 1 이 시간상 직후 판
            next_ver = None
            if base_idx is not None and base_idx > 0:
                next_ver = sorted_rows[base_idx - 1]

            next_jg = (next_ver.get("제개정") or "").strip() if next_ver else ""
            if next_jg in ("폐지", "타법폐지"):
                historic_guide = "폐지된 법이다. 폐지 직전 판의 조문이다"
                current_cands = None
            else:
                historic_guide = "현행이 아닌 옛 법령명이다. 그 이름으로 시행된 마지막 판의 조문이다 - 현행 조문은 현행 법령명으로 다시 물어라"
                current_cands = None
                try:
                    suc = history.successor(law_title)
                    c_list = suc.get("candidates") if isinstance(suc, dict) else getattr(suc, "candidates", None)
                    if c_list:
                        current_cands = c_list
                except Exception:
                    pass

        return {
            "mst": target_mst,
            "name": h.get("법령명"),
            "candidates": [],
            "error": None,
            "is_historic": is_historic,
            "efYd": target_efyd,
            "기준판_MST": base_mst,
            "기준판_시행일자": base_efyd,
            "구법_안내": historic_guide if is_historic else None,
            "현행_법령명_후보": current_cands if is_historic else None,
        }

    # 후보가 여러 개면 고르지 않고 그대로 후보 목록 반환
    return {
        "mst": None,
        "name": s,
        "candidates": hits,
        "error": "일치하는 법령 후보가 여러 개입니다 (%d건). 특정 법령명이나 MST로 다시 조회하세요" % len(hits),
        "is_historic": False,
        "efYd": "",
        "기준판_MST": None,
        "기준판_시행일자": None,
    }


def _resolve_law_mst(law):
    """law 인자로부터 (mst, law_name, candidates, error_msg)를 반환한다.

    - law가 5자리 이상 숫자면 직접 MST로 사용
    - laws.find(law)로 검색
      - 0건이면 error_msg
      - 2건 이상이면 candidates와 함께 안내 (고르지 않음)
      - 1건이면 (mst, law_name, [], None)
    """
    info = _resolve_law_info(law)
    return info["mst"], info["name"], info["candidates"], info["error"]



_HANG_NUM_MAP = {
    "①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5,
    "⑥": 6, "⑦": 7, "⑧": 8, "⑨": 9, "⑩": 10,
    "⑪": 11, "⑫": 12, "⑬": 13, "⑭": 14, "⑮": 15,
    "⑯": 16, "⑰": 17, "⑱": 18, "⑲": 19, "⑳": 20,
}


def _match_hang(hang_str, target_hang):
    """항 문자열('①', '1항' 등)이 target_hang과 일치하는지 비교한다."""
    if not hang_str or target_hang is None:
        return False
    hs = str(hang_str).strip()
    ts = str(target_hang).strip()
    if hs == ts:
        return True

    # 숫자 추출 비교
    h_num = _HANG_NUM_MAP.get(hs)
    if h_num is None:
        m = re.search(r"\d+", hs)
        if m:
            h_num = int(m.group(0))

    t_num = _HANG_NUM_MAP.get(ts)
    if t_num is None:
        m2 = re.search(r"\d+", ts)
        if m2:
            t_num = int(m2.group(0))

    return (h_num is not None) and (t_num is not None) and (h_num == t_num)


def get_article(law, jo, hang=None):
    """특정 법령의 특정 조문(항·호 포함)을 평평한 텍스트 필드로 조회한다.

    인자:
        law: 법령명, 약칭, 구법명 또는 MST 일련번호
        jo: 조 번호 (예: "제84조", "84", "제40조의3", "40의3")
        hang: 선택적 항 번호 (예: "1", "①", "제1항")
    """
    jo_code, hang = parse_jo_and_hang(jo, hang=hang)
    # 항 번호만 받는다 - "제2항제1호" 의 호를 버리고 항 전체를 주면 요청과 다른 내용이 된다
    if hang is not None:
        hs = str(hang).strip()
        m_h = re.match(r"^(?:제\s*)?(\d+)\s*(?:항)?$", hs)
        if hs not in _HANG_NUM_MAP and not m_h:
            raise ValueError("항 번호만 적어라(예: '1', '제1항', '①') - 호 단위 조회는 지원하지 않는다: %r" % hang)
        if m_h and int(m_h.group(1)) < 1:
            raise ValueError("항 번호는 1 이상이어야 한다: %r" % hang)
    target_jo_num = int(jo_code[:4])
    target_jo_sub = int(jo_code[4:])

    info = _resolve_law_info(law)
    mst = info["mst"]
    law_name = info["name"]
    candidates = info["candidates"]
    err = info["error"]
    is_historic = info["is_historic"]
    efyd = info["efYd"]
    base_mst = info.get("기준판_MST") or mst
    base_efyd = info.get("기준판_시행일자") or efyd
    historic_guide = info.get("구법_안내") or "폐지된 법이다. 폐지 직전 판의 조문이다"
    successor_cands = info.get("현행_법령명_후보")

    if mst is None:
        fail_dict = {
            "law": law,
            "jo": jo,
            "complete": False,
            "why": err,
            "candidates": candidates
        }
        if is_historic:
            fail_dict["구법"] = True
            if base_mst:
                fail_dict["기준판_MST"] = base_mst
            if base_efyd:
                fail_dict["기준판_시행일자"] = base_efyd
        return fail_dict

    # 구법(현행 아님)으로 풀리면 연혁 본문(target=eflaw, service, MST=마지막 판 MST, efYd=그 판 시행일자)으로 조회
    if is_historic:
        res = client.call("eflaw", service=True, MST=mst, efYd=efyd, JO=jo_code)
    else:
        res = client.call("law", service=True, MST=mst, JO=jo_code)

    if not res.ok:
        if is_historic:
            return {
                "law": law_name or law,
                "MST": mst,
                "jo": jo,
                "complete": False,
                "why": "구법이라 현행본에 없다. 연혁본 조회에 실패했다",
                "구법": True,
                "기준판_MST": base_mst,
                "기준판_시행일자": base_efyd,
                "마지막_시행일자": efyd
            }
        return {
            "law": law_name or law,
            "MST": mst,
            "jo": jo,
            "complete": False,
            "why": res.why_incomplete() or "조회 실패: %s" % (res.error or "사유 미상")
        }

    items = res.partial
    root = items[0] if items else None
    if not isinstance(root, dict):
        if is_historic:
            return {
                "law": law_name or law,
                "MST": mst,
                "jo": jo,
                "complete": False,
                "why": "구법이라 현행본에 없다. 연혁본 조회에 실패했다",
                "구법": True,
                "기준판_MST": base_mst,
                "기준판_시행일자": base_efyd,
                "마지막_시행일자": efyd
            }
        return {
            "law": law_name or law,
            "MST": mst,
            "jo": jo,
            "complete": False,
            "why": "원 응답을 알아보지 못했다 - 0건인지 알 수 없다"
        }

    law_dict = root.get("법령") if "법령" in root else root
    if not isinstance(law_dict, dict):
        if is_historic:
            return {
                "law": law_name or law,
                "MST": mst,
                "jo": jo,
                "complete": False,
                "why": "구법이라 현행본에 없다. 연혁본 조회에 실패했다",
                "구법": True,
                "기준판_MST": base_mst,
                "기준판_시행일자": base_efyd,
                "마지막_시행일자": efyd
            }
        return {
            "law": law_name or law,
            "MST": mst,
            "jo": jo,
            "complete": False,
            "why": "원 응답을 알아보지 못했다 - 법령 구조 없음"
        }

    info_dict = law_dict.get("기본정보", {})
    final_law_name = text(info_dict.get("법령명_한글") or info_dict.get("법령명")) or law_name or law
    enforce_date = text(info_dict.get("시행일자")) or efyd or ""

    def _make_no_jo_result():
        res_dict = {
            "law": final_law_name,
            "MST": mst,
            "시행일자": enforce_date,
            "jo": jo,
            "jo_code": jo_code,
            "status": "조문 없음",
            "why": "해당 법령에 해당 조문(%s)이 없습니다" % jo,
            "complete": True,
            "조번호": "",
            "조문제목": "",
            "조문내용": "",
            "항": [],
            "호": []
        }
        if is_historic:
            res_dict["구법"] = True
            res_dict["기준판_MST"] = base_mst
            res_dict["기준판_시행일자"] = base_efyd
            guide_msg = "이 판(시행 %s)에 그 조문이 없다" % (base_efyd or enforce_date)
            res_dict["안내"] = guide_msg
            res_dict["머리안내"] = guide_msg
        return res_dict

    jo_root = law_dict.get("조문")
    if not jo_root or not isinstance(jo_root, dict) or not jo_root.get("조문단위"):
        return _make_no_jo_result()

    unit = jo_root.get("조문단위")
    units = unit if isinstance(unit, list) else [unit]

    # 조문단위 중 조문여부 == "조문" 이고 조문번호·조문가지번호가 요청과 같은 것만 고른다
    matched_units = []
    for u in units:
        if not isinstance(u, dict):
            continue
        u_kind = text(u.get("조문여부"))
        if u_kind and u_kind != "조문":
            continue
        raw_no = text(u.get("조문번호"))
        try:
            u_no = int(raw_no)
        except (ValueError, TypeError):
            continue
        raw_sub = text(u.get("조문가지번호"))
        try:
            u_sub = int(raw_sub) if raw_sub else 0
        except (ValueError, TypeError):
            u_sub = 0

        if u_no == target_jo_num and u_sub == target_jo_sub:
            matched_units.append(u)

    if not matched_units:
        return _make_no_jo_result()

    if len(matched_units) > 1:
        return {
            "law": final_law_name,
            "MST": mst,
            "jo": jo,
            "complete": False,
            "why": "일치하는 조문 단위가 여러 개입니다 (%d건)" % len(matched_units)
        }

    u = matched_units[0]

    jo_num = text(u.get("조문번호"))
    jo_sub = text(u.get("조문가지번호"))
    jo_title = text(u.get("조문제목"))
    jo_content = text(u.get("조문내용"))

    if jo_sub and jo_sub not in ("0", "00"):
        try:
            sub_i = int(jo_sub)
            jo_display = "제%s조의%d" % (jo_num, sub_i)
        except ValueError:
            jo_display = "제%s조의%s" % (jo_num, jo_sub)
    else:
        jo_display = "제%s조" % jo_num if jo_num else ""

    # 고른 조문의 조번호를 응답에 넣고, 요청과 다르면 절대 complete:true 로 내지 않는다
    try:
        actual_code = "%04d%02d" % (int(jo_num or 0), int(jo_sub or 0))
    except ValueError:
        actual_code = ""

    if actual_code != jo_code:
        return {
            "law": final_law_name,
            "MST": mst,
            "jo": jo,
            "complete": False,
            "why": "요청한 조번호(%s)와 조회된 조문(%s)이 일치하지 않습니다" % (jo, jo_display)
        }

    raw_hang = u.get("항")
    hang_list = raw_hang if isinstance(raw_hang, list) else ([raw_hang] if isinstance(raw_hang, dict) else [])

    parsed_hangs = []
    all_hos = []

    for h in hang_list:
        if not isinstance(h, dict):
            continue
        h_no = text(h.get("항번호"))
        h_content = text(h.get("항내용"))
        raw_ho = h.get("호")
        ho_list = raw_ho if isinstance(raw_ho, list) else ([raw_ho] if isinstance(raw_ho, dict) else [])
        parsed_hos = []
        for ho in ho_list:
            if not isinstance(ho, dict):
                continue
            ho_entry = {
                "호번호": text(ho.get("호번호")),
                "호내용": text(ho.get("호내용"))
            }
            if ho.get("목"):
                ho_entry["목"] = ho.get("목")
            parsed_hos.append(ho_entry)
            all_hos.append(ho_entry)

        parsed_hangs.append({
            "항번호": h_no,
            "항내용": h_content,
            "호": parsed_hos
        })

    # hang 필터 적용
    if hang is not None:
        matched = [h for h in parsed_hangs if _match_hang(h.get("항번호"), hang)]
        if matched:
            parsed_hangs = matched
            all_hos = matched[0].get("호", [])
            target_h_content = matched[0].get("항내용")
            if target_h_content:
                jo_content = target_h_content
        else:
            orig_hangs = [h.get("항번호") for h in parsed_hangs if h.get("항번호")]
            # 범위(①~③)로 줄이면 빠진 항(①, ③ 만 있을 때 ②)까지 있는 것처럼 보인다 - 실제 항을 모두 적는다
            hang_range = ", ".join(orig_hangs) if orig_hangs else "없음"

            if orig_hangs:
                guide_msg = "이 조에 그 항이 없다(있는 항: %s)" % hang_range
            else:
                guide_msg = "이 조에 그 항이 없다"

            res_dict = {
                "law": final_law_name,
                "MST": mst,
                "시행일자": enforce_date,
                "조번호": jo_display,
                "조문제목": jo_title,
                "조문내용": "",
                "status": "해당 항 없음",
                "안내": guide_msg,
                "why": guide_msg,
                "complete": True,
                "항": [],
                "호": []
            }
            if is_historic:
                res_dict["구법"] = True
                res_dict["기준판_MST"] = base_mst
                res_dict["기준판_시행일자"] = base_efyd
                res_dict["안내"] = historic_guide
                res_dict["머리안내"] = historic_guide
                if successor_cands:
                    res_dict["현행_법령명_후보"] = successor_cands
                res_dict["마지막_시행일자"] = efyd
            return res_dict

    res_dict = {
        "law": final_law_name,
        "MST": mst,
        "시행일자": enforce_date,
        "조번호": jo_display,
        "조문제목": jo_title,
        "조문내용": jo_content,
        "항": parsed_hangs,
        "호": all_hos,
        "complete": True
    }
    if is_historic:
        res_dict["구법"] = True
        res_dict["기준판_MST"] = base_mst
        res_dict["기준판_시행일자"] = base_efyd
        res_dict["안내"] = historic_guide
        res_dict["머리안내"] = historic_guide
        if successor_cands:
            res_dict["현행_법령명_후보"] = successor_cands
        res_dict["마지막_시행일자"] = efyd
    return res_dict


def _extract_annex_text(content):
    """별표내용(중첩 리스트, 리스트, 문자열)을 단일 문자열 텍스트로 평탄화한다."""
    if not content:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        lines = []
        for item in content:
            if isinstance(item, list):
                for sub in item:
                    if sub and str(sub).strip():
                        lines.append(str(sub).rstrip())
            elif item and str(item).strip():
                lines.append(str(item).rstrip())
        return "\n".join(lines).strip()
    return str(content).strip()


def _find_subordinate_candidates(law_title):
    """법률명으로부터 하위법령(시행령, 시행규칙) 후보 목록을 찾는다."""
    sub_cands = []
    for suffix in (" 시행령", " 시행규칙"):
        try:
            hits = laws.find(law_title + suffix)
            for h in hits:
                if h not in sub_cands:
                    sub_cands.append(h)
        except Exception:
            pass
    return sub_cands


def _is_deleted_annex(title, content):
    """내용 또는 제목이 '삭제 <날짜>' 형태뿐인 별표인지 판별한다."""
    t = (title or "").strip()
    c = (content or "").strip()
    if re.match(r"^삭제(?:\s*<[^>]+>|\s*\([^)]+\))?$", t):
        return True
    lines = [line.strip() for line in c.split("\n") if line.strip()]
    if len(lines) <= 2:
        combined = " ".join(lines)
        body = re.sub(r"^■\s*[^\[\]]+\[[^\[\]]+\]\s*", "", combined).strip()
        if re.match(r"^삭제(?:\s*<[^>]+>|\s*\([^)]+\))?$", body):
            return True
    return False


def get_annex_text(law, annex):
    """법 본문 응답에서 특정 별표나 서식의 제목과 본문 텍스트를 추출한다.

    인자:
        law: 법령명 또는 MST 일련번호
        annex: 별표 번호 (예: "1", "별표 2", "서식 1", "별지 제1호서식")
    """
    target_kind, target_num, target_sub = parse_annex_spec(annex)
    info = _resolve_law_info(law)
    mst = info["mst"]
    law_name = info["name"]
    candidates = info["candidates"]
    err = info["error"]
    is_historic = info["is_historic"]
    efyd = info["efYd"]
    base_mst = info.get("기준판_MST") or mst
    base_efyd = info.get("기준판_시행일자") or efyd
    historic_guide = info.get("구법_안내") or "폐지된 법이다. 폐지 직전 판의 조문이다"
    successor_cands = info.get("현행_법령명_후보")

    if mst is None:
        fail_dict = {
            "law": law,
            "annex": annex,
            "complete": False,
            "why": err,
            "candidates": candidates
        }
        if is_historic:
            fail_dict["구법"] = True
            if base_mst:
                fail_dict["기준판_MST"] = base_mst
            if base_efyd:
                fail_dict["기준판_시행일자"] = base_efyd
        return fail_dict

    if is_historic:
        res = client.call("eflaw", service=True, MST=mst, efYd=efyd)
    else:
        res = client.call("law", service=True, MST=mst)
    if not res.ok:
        fail_dict = {
            "law": law_name or law,
            "MST": mst,
            "annex": annex,
            "complete": False,
            "why": res.why_incomplete() or "조회 실패: %s" % (res.error or "사유 미상")
        }
        if is_historic:
            fail_dict["구법"] = True
            fail_dict["기준판_MST"] = base_mst
            fail_dict["기준판_시행일자"] = base_efyd
        return fail_dict

    items = res.partial
    root = items[0] if items else None
    if not isinstance(root, dict):
        fail_dict = {
            "law": law_name or law,
            "MST": mst,
            "annex": annex,
            "complete": False,
            "why": "원 응답을 알아보지 못했다 - 0건인지 알 수 없다"
        }
        if is_historic:
            fail_dict["구법"] = True
            fail_dict["기준판_MST"] = base_mst
            fail_dict["기준판_시행일자"] = base_efyd
        return fail_dict

    law_dict = root.get("법령") if "법령" in root else root
    if not isinstance(law_dict, dict):
        fail_dict = {
            "law": law_name or law,
            "MST": mst,
            "annex": annex,
            "complete": False,
            "why": "원 응답을 알아보지 못했다 - 법령 구조 없음"
        }
        if is_historic:
            fail_dict["구법"] = True
            fail_dict["기준판_MST"] = base_mst
            fail_dict["기준판_시행일자"] = base_efyd
        return fail_dict

    info = law_dict.get("기본정보", {})
    final_law_name = text(info.get("법령명_한글") or info.get("법령명")) or law_name or law

    annex_root = law_dict.get("별표")
    if not annex_root or not isinstance(annex_root, dict) or not annex_root.get("별표단위"):
        # 별표가 없음 -> 하위법령 후보 안내
        sub_cands = _find_subordinate_candidates(final_law_name)
        if sub_cands:
            fail_dict = {
                "law": final_law_name,
                "MST": mst,
                "annex": annex,
                "complete": False,
                "why": "해당 법령에는 별표가 없습니다. 시행령·시행규칙 등 하위법령에 별표가 규정되어 있을 수 있습니다",
                "candidates": sub_cands
            }
            if is_historic:
                fail_dict["구법"] = True
                fail_dict["기준판_MST"] = base_mst
                fail_dict["기준판_시행일자"] = base_efyd
            return fail_dict
        res_dict = {
            "law": final_law_name,
            "MST": mst,
            "annex": annex,
            "status": "별표 없음",
            "why": "해당 법령에 별표가 없습니다",
            "complete": True
        }
        if is_historic:
            res_dict["구법"] = True
            res_dict["기준판_MST"] = base_mst
            res_dict["기준판_시행일자"] = base_efyd
            res_dict["안내"] = historic_guide
            res_dict["머리안내"] = historic_guide
            if successor_cands:
                res_dict["현행_법령명_후보"] = successor_cands
        return res_dict

    units = annex_root.get("별표단위")
    unit_list = units if isinstance(units, list) else [units]

    matched_units = []
    summary_list = []
    for u in unit_list:
        if not isinstance(u, dict):
            continue
        u_kind = text(u.get("별표구분"))
        u_num_raw = text(u.get("별표번호"))
        u_sub_raw = text(u.get("별표가지번호"))
        u_title = text(u.get("별표제목"))
        summary_list.append({
            "구분": u_kind,
            "번호": u_num_raw,
            "가지번호": u_sub_raw,
            "제목": u_title
        })

        try:
            u_num = int(str(u_num_raw or "").lstrip("0") or "0")
        except ValueError:
            u_num = -1
        try:
            u_sub = int(str(u_sub_raw or "").lstrip("0") or "0")
        except ValueError:
            u_sub = 0

        # 종류 판별: 서식이면 "서식", "별지" / 별표면 "별표"
        if target_kind == "서식":
            kind_matched = (u_kind in ("서식", "별지"))
        else:
            kind_matched = (u_kind in ("별표", ""))

        if kind_matched and u_num == target_num and u_sub == target_sub:
            matched_units.append(u)

    if not matched_units:
        # 없으면 complete:false + 그 법의 별표/서식 목록 요약
        fail_dict = {
            "law": final_law_name,
            "MST": mst,
            "annex": annex,
            "complete": False,
            "why": "해당 법령에서 %s %s을(를) 찾을 수 없습니다" % (target_kind, annex),
            "목록": summary_list
        }
        if is_historic:
            fail_dict["구법"] = True
            fail_dict["기준판_MST"] = base_mst
            fail_dict["기준판_시행일자"] = base_efyd
        return fail_dict

    matched_unit = matched_units[0]

    text_content = _extract_annex_text(matched_unit.get("별표내용"))

    hwp_link = matched_unit.get("별표서식파일링크") or matched_unit.get("별표HWP파일명") or ""
    pdf_link = matched_unit.get("별표서식PDF파일링크") or matched_unit.get("별표PDF파일명") or ""
    if hwp_link and hwp_link.startswith("/"):
        hwp_link = client.BASE + hwp_link
    if pdf_link and pdf_link.startswith("/"):
        pdf_link = client.BASE + pdf_link

    b_no = text(matched_unit.get("별표번호"))
    b_sub = text(matched_unit.get("별표가지번호"))
    b_title = text(matched_unit.get("별표제목"))
    b_kind = text(matched_unit.get("별표구분"))

    is_deleted = _is_deleted_annex(b_title, text_content)

    # 내용이 "삭제 <날짜>" 뿐인 별표는 complete:true 로 주되 머리 안내에 "삭제된 별표다" 를 넣는다
    if is_deleted:
        del_dict = {
            "law": final_law_name,
            "MST": mst,
            "annex": annex,
            "별표번호": b_no,
            "별표가지번호": b_sub,
            "별표구분": b_kind,
            "별표제목": b_title,
            "별표내용": text_content,
            "안내": "삭제된 별표다",
            "머리안내": "삭제된 별표다",
            "hwp": hwp_link,
            "pdf": pdf_link,
            "파일링크": {"hwp": hwp_link, "pdf": pdf_link},
            "complete": True
        }
        if is_historic:
            del_dict["구법"] = True
            del_dict["기준판_MST"] = base_mst
            del_dict["기준판_시행일자"] = base_efyd
        return del_dict

    # 본문 텍스트·파일 링크가 둘 다 비면 why 에 "파일 링크도 없다"
    if not text_content:
        if not hwp_link and not pdf_link:
            fail_dict = {
                "law": final_law_name,
                "MST": mst,
                "annex": annex,
                "별표번호": b_no,
                "별표가지번호": b_sub,
                "별표구분": b_kind,
                "별표제목": b_title,
                "complete": False,
                "why": "별표 본문 텍스트가 없고, 파일 링크도 없다",
                "hwp": "",
                "pdf": "",
                "파일링크": {"hwp": "", "pdf": ""}
            }
            if is_historic:
                fail_dict["구법"] = True
                fail_dict["기준판_MST"] = base_mst
                fail_dict["기준판_시행일자"] = base_efyd
            return fail_dict
        fail_dict = {
            "law": final_law_name,
            "MST": mst,
            "annex": annex,
            "별표번호": b_no,
            "별표가지번호": b_sub,
            "별표구분": b_kind,
            "별표제목": b_title,
            "complete": False,
            "why": "별표 본문 텍스트가 응답에 없다 - 첨부 파일(HWP/PDF)을 받아 확인해야 한다",
            "hwp": hwp_link,
            "pdf": pdf_link,
            "파일링크": {"hwp": hwp_link, "pdf": pdf_link}
        }
        if is_historic:
            fail_dict["구법"] = True
            fail_dict["기준판_MST"] = base_mst
            fail_dict["기준판_시행일자"] = base_efyd
        return fail_dict

    res_dict = {
        "law": final_law_name,
        "MST": mst,
        "annex": annex,
        "별표번호": b_no,
        "별표가지번호": b_sub,
        "별표구분": b_kind,
        "별표제목": b_title,
        "별표내용": text_content,
        "hwp": hwp_link,
        "pdf": pdf_link,
        "파일링크": {"hwp": hwp_link, "pdf": pdf_link},
        "complete": True
    }
    if is_historic:
        res_dict["구법"] = True
        res_dict["기준판_MST"] = base_mst
        res_dict["기준판_시행일자"] = base_efyd
        res_dict["안내"] = historic_guide
        res_dict["머리안내"] = historic_guide
        if successor_cands:
            res_dict["현행_법령명_후보"] = successor_cands
    return res_dict
