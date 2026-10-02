# -*- coding: utf-8 -*-
"""결함 2건에 대한 감사 재현 및 검증 시험 (r18).

1. history.py _find_same_id_candidate:
   - 150건 목록에서 두 번째 쪽의 다음 이름을 찾음.
   - 5쪽 넘어 끝을 못 보면 successor 를 complete:false + why "같은 법령ID 연혁을 끝까지 받지 못했다" 로.
2. client.py 캐시 쓰기:
   - 외부에서 쓰기 함수에 아무 인자(_verified=True 등)를 줘도 그 항목이 캐시 적중으로 읽히지 않음.
"""
import json
import os
import pytest
import urllib.request
from law_kit import client, history
from law_kit.client import Result
from law_kit.shape import Answer


class FakeResponse(object):
    def __init__(self, text):
        self._data = text.encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


# =========================================================================
# 지적 (1) history.py _find_same_id_candidate / successor
# =========================================================================

def test_same_id_candidate_finds_next_name_on_second_page(monkeypatch):
    """(1) 150건 목록에서 1쪽(100건)에는 구법명만 있고 2쪽(50건)에 다음 이름이 있는 경우:
    2쪽까지 넘겨서 두 번째 쪽의 다음 이름을 찾고 successor complete: True."""
    # 1. is_current("구법A") -> (False, "구법")
    monkeypatch.setattr(history, "is_current", lambda name: (False, "구법"))

    # 2. versions("구법A") -> 1판 (제개정: "일부개정" -> 폐지가 아니므로 2번 same_id_candidate 경로로)
    fake_last_ver = {
        "법령명": "구법A",
        "ID": "001122",
        "MST": "1001",
        "시행일자": "20050101",
        "공포일자": "20041231",
        "제개정": "일부개정",
    }
    monkeypatch.setattr(history, "versions", lambda name, **k: Answer({
        "name": name,
        "ok": True,
        "complete": True,
        "versions": [fake_last_ver],
        "note": "",
    }))

    # 3. client.call_all 혹은 client.call 응답 모킹
    def fake_call(target, service=False, **params):
        if target == "eflaw" and service is True and params.get("ID") == "001122":
            # 기본정보
            return Result("eflaw", ok=True, complete=True, items=[{
                "기본정보": {
                    "법령명_한글": "구법A",
                    "이전법령명": "",
                }
            }])
        if target == "law":
            # 정규화 호출 등
            return Result("law", ok=True, complete=True, items=[])
        if target == "eflaw" and "query" in params:
            # 기존 코드가 call()을 부를 때: 1쪽(100건)만 돌아가서 2쪽의 신법B를 못 봄
            p1_items = [{"법령명한글": "구법A", "법령ID": "001122", "법령일련번호": str(1000 + i),
                         "시행일자": "20000101", "공포일자": "19991231"} for i in range(100)]
            return Result("eflaw", ok=True, complete=True, items=p1_items, total=150)
        return Result(target, ok=True, complete=True, items=[])

    def fake_call_all(target, page_size=100, max_pages=40, **params):
        if target == "eflaw":
            # 수정된 코드가 call_all()을 부를 때: 150건 모두 제공
            p1_items = [{"법령명한글": "구법A", "법령ID": "001122", "법령일련번호": str(1000 + i),
                         "시행일자": "20000101", "공포일자": "19991231"} for i in range(100)]
            p2_items = [{"법령명한글": "신법B", "법령ID": "001122", "법령일련번호": str(2000 + i),
                         "시행일자": "20100101", "공포일자": "20091231"} for i in range(50)]
            all_items = p1_items + p2_items
            return Result("eflaw", ok=True, complete=True, items=all_items, total=150, pages=2)
        return Result(target, ok=True, complete=True, items=[])

    monkeypatch.setattr(client, "call", fake_call)
    monkeypatch.setattr(client, "call_all", fake_call_all)

    res = history.successor("구법A")
    assert res["complete"] is True
    assert len(res["candidates"]) >= 1
    cand_names = [c.get("직접_후보") for c in res["candidates"]]
    assert "신법B" in cand_names


def test_same_id_candidate_over_5_pages_incomplete(monkeypatch):
    """(1) 5쪽 넘어 끝을 못 본 경우 -> successor complete: False, why: '같은 법령ID 연혁을 끝까지 받지 못했다'."""
    monkeypatch.setattr(history, "is_current", lambda name: (False, "구법"))
    fake_last_ver = {
        "법령명": "구법대형",
        "ID": "009988",
        "MST": "9001",
        "시행일자": "20050101",
        "공포일자": "20041231",
        "제개정": "일부개정",
    }
    monkeypatch.setattr(history, "versions", lambda name, **k: Answer({
        "name": name,
        "ok": True,
        "complete": True,
        "versions": [fake_last_ver],
        "note": "",
    }))

    def fake_call(target, service=False, **params):
        if target == "eflaw" and service is True and params.get("ID") == "009988":
            return Result("eflaw", ok=True, complete=True, items=[{
                "기본정보": {"법령명_한글": "구법대형", "이전법령명": ""}
            }])
        if target == "eflaw" and "query" in params:
            # 기존 코드는 call()로 1쪽만 보고 끝내서 complete=True 로 착각함
            items = [{"법령명한글": "구법대형", "법령ID": "009988", "시행일자": "20000101", "공포일자": "19991231"}
                     for _ in range(100)]
            return Result("eflaw", ok=True, complete=True, items=items, total=600)
        return Result(target, ok=True, complete=True, items=[])

    def fake_call_all(target, page_size=100, max_pages=40, **params):
        if target == "eflaw":
            # 5쪽 상한에 걸려 complete=False, truncated=True
            items = [{"법령명한글": "구법대형", "법령ID": "009988", "시행일자": "20000101", "공포일자": "19991231"}
                     for _ in range(500)]
            return Result("eflaw", ok=True, complete=False, truncated=True, items=items, total=600, pages=5,
                          error="상한에 걸려 600건 중 500건만 받았다")
        return Result(target, ok=True, complete=True, items=[])

    monkeypatch.setattr(client, "call", fake_call)
    monkeypatch.setattr(client, "call_all", fake_call_all)

    res = history.successor("구법대형")
    assert res["complete"] is False
    assert "같은 법령ID 연혁을 끝까지 받지 못했다" in res["why"]
    assert res["candidates"] == []


# =========================================================================
# 지적 (2) client.py 캐시 쓰기 보안
# =========================================================================

def test_external_cache_write_with_any_arguments_never_cached(tmp_path, monkeypatch):
    """(2) 외부에서 _cache_write 함수에 _verified=True 등 아무 인자를 줘도
    그 항목이 캐시 적중으로 읽히지 않는다."""
    cache_dir = str(tmp_path / "cache")
    monkeypatch.setattr(client, "CACHE_DIR", cache_dir)

    target_url = "https://www.law.go.kr/DRF/lawSearch.do?OC=test&target=law&type=JSON"
    fake_body = json.dumps({"LawSearch": {"totalCnt": 999, "law": [{"lawId": "poisoned"}]}})

    # 1. _verified=True 및 다양한 임의 인자를 넘겨서 쓰기 시도
    client._cache_write(target_url, fake_body, _verified=True)

    # 2. _cache_read 로 직접 읽어도 None 이어야 함
    read_res = client._cache_read(target_url, ttl=3600)
    assert read_res is None, "외부에서 _cache_write(_verified=True)로 쓴 내용은 읽히지 않아야 한다"

    # 3. 추가 임의 인자들을 넘겨도 마찬가지
    client._cache_write(target_url, fake_body, _verified=True, force=True, arbitrary="yes")
    assert client._cache_read(target_url, ttl=3600) is None

    # 4. raw() 호출 시에도 캐시 적중(hit=True)이 되지 않고 망에서 받아야 함
    calls = []
    real_body = json.dumps({"LawSearch": {"totalCnt": 1, "law": [{"lawId": "real"}]}})

    def fake_urlopen(req, timeout=25):
        calls.append(req.full_url)
        return FakeResponse(real_body)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    body, hit = client.raw("https://www.law.go.kr/DRF/lawSearch.do",
                           {"OC": "test", "target": "law", "type": "JSON"})
    assert hit is False, "외부에서 오염 시도한 캐시는 적중되지 않아야 한다"
    assert len(calls) == 1, "망에서 새로 받아야 한다"
    assert "poisoned" not in body
    assert "real" in body

    # 5. 망에서 한 번 받은 후에는 정상 표지가 붙었으므로 다음 호출은 hit=True 여야 함
    calls.clear()
    body2, hit2 = client.raw("https://www.law.go.kr/DRF/lawSearch.do",
                             {"OC": "test", "target": "law", "type": "JSON"})
    assert hit2 is True, "망에서 정식으로 받은 항목은 캐시 적중해야 한다"
    assert len(calls) == 0
    assert body2 == real_body
