# -*- coding: utf-8 -*-
"""특정 조문(law_article) 및 별표 본문 텍스트(law_annex_text) 조회.

조번호를 빼면 법 전체(수십만 자)가 오므로, 특정 조문만 짚어 법제처 JO 6자리 코드로
정밀 조회하여 평평한 텍스트 필드로 제공한다.
별표는 목록/파일링크뿐 아니라 법 본문 응답에서 별표 본문 텍스트를 꺼내 제공한다.
"""
import re
from . import client, laws
from .client import Incomplete, Result
from .shape import text


def parse_jo(jo):
    """조 번호 문자열을 법제처 JO 6자리 코드(조 4자리 + 가지 2자리)로 변환한다.

    예:
        "제84조", "84" -> "008400"
        "제40조의3", "40의3", "제40조의 3" -> "004003"
        "004003" -> "004003"
    잘못된 jo는 ValueError를 발생시킨다.
    """
    if jo is None:
        raise ValueError("조 번호가 비어 있습니다")
    s = str(jo).strip()
    if not s:
        raise ValueError("조 번호가 비어 있습니다")

    # 이미 6자리 숫자인 경우
    if re.match(r"^\d{6}$", s):
        return s

    m = re.match(r"^(?:제\s*)?(\d+)(?:\s*조)?(?:\s*(?:의|-)\s*(\d+))?$", s)
    if not m:
        raise ValueError("유효하지 않은 조 번호 형식입니다: %r" % jo)

    main_num = int(m.group(1))
    sub_num = int(m.group(2)) if m.group(2) else 0

    if main_num <= 0 or main_num > 9999:
        raise ValueError("조 번호는 1~9999 범위여야 합니다: %d" % main_num)
    if sub_num < 0 or sub_num > 99:
        raise ValueError("가지 번호는 0~99 범위여야 합니다: %d" % sub_num)

    return "%04d%02d" % (main_num, sub_num)


def parse_annex_num(annex):
    """별표 번호 문자열에서 (번호, 가지번호)를 추출한다.

    예:
        "1", "별표 1", "0001" -> (1, 0)
        "별표 2의3", "2의3" -> (2, 3)
    """
    if annex is None:
        raise ValueError("별표 번호가 비어 있습니다")
    s = str(annex).strip()
    if not s:
        raise ValueError("별표 번호가 비어 있습니다")

    m = re.search(r"(\d+)(?:\s*(?:의|-)\s*(\d+))?", s)
    if not m:
        raise ValueError("유효하지 않은 별표 번호입니다: %r" % annex)

    main_num = int(m.group(1))
    sub_num = int(m.group(2)) if m.group(2) else 0
    return main_num, sub_num


def _resolve_law_mst(law):
    """law 인자로부터 (mst, law_name, candidates, error_msg)를 반환한다.

    - law가 5자리 이상 숫자면 직접 MST로 사용
    - laws.find(law)로 검색
      - 0건이면 error_msg
      - 2건 이상이면 candidates와 함께 안내 (고르지 않음)
      - 1건이면 (mst, law_name, [], None)
    """
    s = str(law).strip()
    if s.isdigit() and len(s) >= 5:
        return s, "", [], None

    hits = laws.find(s)
    if not hits:
        return None, s, [], "법령 '%s'을(를) 찾을 수 없습니다" % s

    if len(hits) == 1:
        return hits[0].get("MST"), hits[0].get("법령명"), [], None

    # 후보가 여러 개면 고르지 않고 그대로 후보 목록 반환
    return None, s, hits, "일치하는 법령 후보가 여러 개입니다 (%d건). 특정 법령명이나 MST로 다시 조회하세요" % len(hits)


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
    jo_code = parse_jo(jo)
    mst, law_name, candidates, err = _resolve_law_mst(law)

    if mst is None:
        return {
            "law": law,
            "jo": jo,
            "complete": False,
            "why": err,
            "candidates": candidates
        }

    res = client.call("law", service=True, MST=mst, JO=jo_code)
    if not res.ok:
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
        return {
            "law": law_name or law,
            "MST": mst,
            "jo": jo,
            "complete": False,
            "why": "원 응답을 알아보지 못했다 - 0건인지 알 수 없다"
        }

    law_dict = root.get("법령") if "법령" in root else root
    if not isinstance(law_dict, dict):
        return {
            "law": law_name or law,
            "MST": mst,
            "jo": jo,
            "complete": False,
            "why": "원 응답을 알아보지 못했다 - 법령 구조 없음"
        }

    info = law_dict.get("기본정보", {})
    final_law_name = text(info.get("법령명_한글") or info.get("법령명")) or law_name or law
    enforce_date = text(info.get("시행일자")) or ""

    jo_root = law_dict.get("조문")
    if not jo_root or not isinstance(jo_root, dict) or not jo_root.get("조문단위"):
        return {
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

    unit = jo_root.get("조문단위")
    units = unit if isinstance(unit, list) else [unit]
    u = units[0] if units else {}

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
        else:
            return {
                "law": final_law_name,
                "MST": mst,
                "시행일자": enforce_date,
                "조번호": jo_display,
                "조문제목": jo_title,
                "조문내용": jo_content,
                "status": "해당 항 없음",
                "why": "%s에 요청하신 항(%s)이 없습니다" % (jo_display, hang),
                "complete": True,
                "항": [],
                "호": []
            }

    return {
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


def get_annex_text(law, annex):
    """법 본문 응답에서 특정 별표나 서식의 제목과 본문 텍스트를 추출한다.

    인자:
        law: 법령명 또는 MST 일련번호
        annex: 별표 번호 (예: "1", "별표 2", "2의3")
    """
    target_num, target_sub = parse_annex_num(annex)
    mst, law_name, candidates, err = _resolve_law_mst(law)

    if mst is None:
        return {
            "law": law,
            "annex": annex,
            "complete": False,
            "why": err,
            "candidates": candidates
        }

    res = client.call("law", service=True, MST=mst)
    if not res.ok:
        return {
            "law": law_name or law,
            "MST": mst,
            "annex": annex,
            "complete": False,
            "why": res.why_incomplete() or "조회 실패: %s" % (res.error or "사유 미상")
        }

    items = res.partial
    root = items[0] if items else None
    if not isinstance(root, dict):
        return {
            "law": law_name or law,
            "MST": mst,
            "annex": annex,
            "complete": False,
            "why": "원 응답을 알아보지 못했다 - 0건인지 알 수 없다"
        }

    law_dict = root.get("법령") if "법령" in root else root
    if not isinstance(law_dict, dict):
        return {
            "law": law_name or law,
            "MST": mst,
            "annex": annex,
            "complete": False,
            "why": "원 응답을 알아보지 못했다 - 법령 구조 없음"
        }

    info = law_dict.get("기본정보", {})
    final_law_name = text(info.get("법령명_한글") or info.get("법령명")) or law_name or law

    annex_root = law_dict.get("별표")
    if not annex_root or not isinstance(annex_root, dict) or not annex_root.get("별표단위"):
        # 별표가 없음 -> 하위법령 후보 안내
        sub_cands = _find_subordinate_candidates(final_law_name)
        if sub_cands:
            return {
                "law": final_law_name,
                "MST": mst,
                "annex": annex,
                "complete": False,
                "why": "해당 법령에는 별표가 없습니다. 시행령·시행규칙 등 하위법령에 별표가 규정되어 있을 수 있습니다",
                "candidates": sub_cands
            }
        return {
            "law": final_law_name,
            "MST": mst,
            "annex": annex,
            "status": "별표 없음",
            "why": "해당 법령에 별표가 없습니다",
            "complete": True
        }

    units = annex_root.get("별표단위")
    unit_list = units if isinstance(units, list) else [units]

    matched_unit = None
    for u in unit_list:
        if not isinstance(u, dict):
            continue
        try:
            u_num = int(str(u.get("별표번호", "")).lstrip("0") or "0")
        except ValueError:
            u_num = -1
        try:
            u_sub = int(str(u.get("별표가지번호", "")).lstrip("0") or "0")
        except ValueError:
            u_sub = 0

        if u_num == target_num and u_sub == target_sub:
            matched_unit = u
            break

    if not matched_unit:
        sub_cands = _find_subordinate_candidates(final_law_name)
        if sub_cands:
            return {
                "law": final_law_name,
                "MST": mst,
                "annex": annex,
                "complete": False,
                "why": "해당 법령에서 별표 %s을(를) 찾을 수 없습니다. 시행령·시행규칙 등 하위법령에 있을 수 있습니다" % annex,
                "candidates": sub_cands
            }
        return {
            "law": final_law_name,
            "MST": mst,
            "annex": annex,
            "status": "별표 없음",
            "why": "해당 별표(%s)를 찾을 수 없습니다" % annex,
            "complete": True
        }

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

    if not text_content:
        return {
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

    return {
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
        "complete": True
    }
