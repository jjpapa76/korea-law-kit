# -*- coding: utf-8 -*-
import io
import json
import os
import time
import urllib.parse
import urllib.request
import pytest
from law_kit import client


class FakeResponse:
    def __init__(self, text):
        self._data = text.encode("utf-8")

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def test_unmarked_cache_is_discarded_and_refetched(tmp_path, monkeypatch):
    """표지 없는 날것 캐시(이전 에이전트 위조 사고)는 거부되고 망에서 다시 받는다."""
    cache_dir = str(tmp_path / "cache")
    monkeypatch.setattr(client, "CACHE_DIR", cache_dir)

    target_url = "https://www.law.go.kr/DRF/lawSearch.do?OC=test&target=law&type=JSON"
    cache_path = client._cache_path(target_url)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)

    # 1. 표지 없는 가짜 0건 응답을 파일에 직접 써 넣음 (실측 사고 재현)
    fake_body = json.dumps({"LawSearch": {"totalCnt": 0, "law": []}})
    with open(cache_path, "w", encoding="utf-8") as fp:
        fp.write(fake_body)

    # 2. 망 요청이 올 때 진짜 응답을 반환하는 가짜 urlopen 설정
    real_body = json.dumps({"LawSearch": {"totalCnt": 1, "law": [{"lawId": "12345"}]}})
    calls = []

    def fake_urlopen(req, timeout=25):
        calls.append(req.full_url)
        return FakeResponse(real_body)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    # 3. raw() 호출 -> 위조 캐시 무시, 망에서 새로 받아야 함
    body, hit = client.raw("https://www.law.go.kr/DRF/lawSearch.do", {"OC": "test", "target": "law", "type": "JSON"})
    assert hit is False, "표지 없는 캐시는 hit=False 여야 한다"
    assert len(calls) == 1, "망에서 다시 받아야 한다"
    assert "12345" in body

    # 4. 이제 정식 표지가 붙어서 캐시되었으므로 두 번째는 hit=True 여야 함
    calls.clear()
    body2, hit2 = client.raw("https://www.law.go.kr/DRF/lawSearch.do", {"OC": "test", "target": "law", "type": "JSON"})
    assert hit2 is True, "정식 캐시 항목은 hit=True 여야 한다"
    assert len(calls) == 0, "캐시 적중 시 망 호출 0회여야 한다"
    assert body2 == real_body


def test_hash_mismatch_cache_is_discarded_and_refetched(tmp_path, monkeypatch):
    """url_digest 가 일치하지 않는 위조 캐시는 거부되고 망에서 다시 받는다."""
    cache_dir = str(tmp_path / "cache")
    monkeypatch.setattr(client, "CACHE_DIR", cache_dir)

    target_url = "https://www.law.go.kr/DRF/lawSearch.do?OC=test&target=law&type=JSON"
    cache_path = client._cache_path(target_url)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)

    # 잘못된 url_digest 표지를 붙인 캐시 생성
    tampered_envelope = {
        "_writer": "law_kit.client",
        "_v": 2,
        "url_digest": "tampered_digest_value",
        "body": json.dumps({"LawSearch": {"totalCnt": 0, "law": []}}),
    }
    with open(cache_path, "w", encoding="utf-8") as fp:
        json.dump(tampered_envelope, fp)

    real_body = json.dumps({"LawSearch": {"totalCnt": 5, "law": []}})
    calls = []

    def fake_urlopen(req, timeout=25):
        calls.append(req.full_url)
        return FakeResponse(real_body)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    body, hit = client.raw("https://www.law.go.kr/DRF/lawSearch.do", {"OC": "test", "target": "law", "type": "JSON"})
    assert hit is False, "해시 불일치 캐시는 거부되어야 한다"
    assert len(calls) == 1, "망에서 새로 받아야 한다"
    assert json.loads(body)["LawSearch"]["totalCnt"] == 5


def test_version_mismatch_cache_is_discarded(tmp_path, monkeypatch):
    """버전이 다르거나 _writer 가 다른 캐시는 무시된다."""
    cache_dir = str(tmp_path / "cache")
    monkeypatch.setattr(client, "CACHE_DIR", cache_dir)

    target_url = "https://www.law.go.kr/DRF/lawSearch.do?OC=test&target=law&type=JSON"
    cache_path = client._cache_path(target_url)
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)

    old_envelope = {
        "_writer": "law_kit.client",
        "_v": 1,
        "url_digest": client._url_digest(target_url),
        "body": json.dumps({"LawSearch": {"totalCnt": 0, "law": []}}),
    }
    with open(cache_path, "w", encoding="utf-8") as fp:
        json.dump(old_envelope, fp)

    calls = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=25: FakeResponse('{"ok": true}'))

    body, hit = client.raw("https://www.law.go.kr/DRF/lawSearch.do", {"OC": "test", "target": "law", "type": "JSON"})
    assert hit is False
    assert body == '{"ok": true}'


def test_external_cache_write_cannot_poison_cache(tmp_path, monkeypatch):
    """외부에서 _cache_write 를 직접 호출해도 표지가 없으므로 쓰이지 않는다."""
    cache_dir = str(tmp_path / "cache")
    monkeypatch.setattr(client, "CACHE_DIR", cache_dir)

    target_url = "https://www.law.go.kr/DRF/lawSearch.do?OC=test&target=law&type=JSON"
    fake_body = json.dumps({"LawSearch": {"totalCnt": 0, "law": []}})

    # 외부에서 _cache_write 직접 호출
    client._cache_write(target_url, fake_body)

    # 읽으려고 할 때
    read_result = client._cache_read(target_url, ttl=3600)
    assert read_result is None, "표지 없는 _cache_write 결과는 읽히지 않아야 한다"


def test_empty_result_short_ttl(tmp_path, monkeypatch):
    """0건 응답은 1시간(CACHE_TTL_EMPTY) 후 캐시가 만료되어 망에서 다시 받는다."""
    cache_dir = str(tmp_path / "cache")
    monkeypatch.setattr(client, "CACHE_DIR", cache_dir)

    empty_body = json.dumps({"LawSearch": {"totalCnt": 0, "law": []}})
    calls = []

    def fake_urlopen(req, timeout=25):
        calls.append(req.full_url)
        return FakeResponse(empty_body)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    # 1. 처음 요청: 망에서 받아 캐시에 저장
    body, hit = client.raw("https://www.law.go.kr/DRF/lawSearch.do", {"OC": "test", "target": "law", "type": "JSON"})
    assert hit is False
    assert len(calls) == 1

    # 2. 30분 경과 시점: 캐시 적중 (1시간 이내)
    target_url = "https://www.law.go.kr/DRF/lawSearch.do?OC=test&target=law&type=JSON"
    cache_path = client._cache_path(target_url)
    now = time.time()
    os.utime(cache_path, (now - 1800, now - 1800))

    calls.clear()
    body2, hit2 = client.raw("https://www.law.go.kr/DRF/lawSearch.do", {"OC": "test", "target": "law", "type": "JSON"})
    assert hit2 is True, "1시간 이내에는 0건이어도 캐시 적중해야 한다"
    assert len(calls) == 0

    # 3. 1시간 1초 경과 시점 (3601초 전): 캐시 만료되어 망에서 다시 받아야 함
    os.utime(cache_path, (now - 3601, now - 3601))

    calls.clear()
    body3, hit3 = client.raw("https://www.law.go.kr/DRF/lawSearch.do", {"OC": "test", "target": "law", "type": "JSON"})
    assert hit3 is False, "1시간 지난 0건 응답은 캐시가 만료되어야 한다"
    assert len(calls) == 1, "망에서 다시 받아야 한다"


def test_non_empty_result_normal_ttl(tmp_path, monkeypatch):
    """결과가 있는 정상 응답은 1시간이 지나도 7일(기본 TTL) 동안 캐시 적중 유지."""
    cache_dir = str(tmp_path / "cache")
    monkeypatch.setattr(client, "CACHE_DIR", cache_dir)

    normal_body = json.dumps({"LawSearch": {"totalCnt": 10, "law": [{"lawId": "1"}]}})
    calls = []

    def fake_urlopen(req, timeout=25):
        calls.append(req.full_url)
        return FakeResponse(normal_body)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    # 1. 첫 요청
    client.raw("https://www.law.go.kr/DRF/lawSearch.do", {"OC": "test", "target": "law", "type": "JSON"})

    # 2. 2시간 경과 (7200초 전)
    target_url = "https://www.law.go.kr/DRF/lawSearch.do?OC=test&target=law&type=JSON"
    cache_path = client._cache_path(target_url)
    now = time.time()
    os.utime(cache_path, (now - 7200, now - 7200))

    # 3. 다시 요청: 7일 TTL 이내이므로 여전히 캐시 적중 유지!
    calls.clear()
    body, hit = client.raw("https://www.law.go.kr/DRF/lawSearch.do", {"OC": "test", "target": "law", "type": "JSON"})
    assert hit is True, "0건이 아닌 정상 응답은 1시간이 지나도 기본 TTL 동안 캐시 적중해야 한다"
    assert len(calls) == 0


def test_cache_isolated_from_real_cache():
    """pytest 환경에서 client.CACHE_DIR 는 실제 사용자 홈 캐시가 아니어야 한다."""
    real_cache = os.path.normpath(os.path.expanduser(os.path.join("~", ".cache", "korea-law-kit")))
    current_cache = os.path.normpath(client.CACHE_DIR)
    assert current_cache != real_cache, "client.CACHE_DIR 가 실제 캐시 경로를 보고 있다!"
    assert "law_kit_pytest_cache_" in current_cache or "pytest" in current_cache or "temp" in current_cache.lower() or "tmp" in current_cache.lower()
