# -*- coding: utf-8 -*-
"""pytest 세션 동안 실제 사용자 캐시(~/.cache/korea-law-kit)를 건드리지 않게 격리한다."""
import os
import sys
import tempfile
import shutil
import pytest

# 세션 시작 시, client 모듈 import 전에 임시 폴더로 고정
_TEST_CACHE_DIR = tempfile.mkdtemp(prefix="law_kit_pytest_cache_")
os.environ["LAW_KIT_CACHE"] = _TEST_CACHE_DIR

# 이미 import 된 경우 CACHE_DIR 도 바꿔라
if "law_kit.client" in sys.modules:
    sys.modules["law_kit.client"].CACHE_DIR = _TEST_CACHE_DIR
if "law_kit" in sys.modules and hasattr(sys.modules["law_kit"], "client"):
    sys.modules["law_kit"].client.CACHE_DIR = _TEST_CACHE_DIR


@pytest.fixture(autouse=True)
def _ensure_cache_isolation():
    """각 테스트 전후에 CACHE_DIR가 실제 캐시로 새지 않도록 임시 폴더를 유지한다."""
    if "LAW_KIT_CACHE" not in os.environ or os.environ["LAW_KIT_CACHE"] != _TEST_CACHE_DIR:
        os.environ["LAW_KIT_CACHE"] = _TEST_CACHE_DIR
    if "law_kit.client" in sys.modules:
        sys.modules["law_kit.client"].CACHE_DIR = _TEST_CACHE_DIR
    yield
    os.environ["LAW_KIT_CACHE"] = _TEST_CACHE_DIR
    if "law_kit.client" in sys.modules:
        sys.modules["law_kit.client"].CACHE_DIR = _TEST_CACHE_DIR


def pytest_sessionfinish(session, exitstatus):
    """세션 종료 시 임시 캐시 디렉토리를 정리한다."""
    try:
        shutil.rmtree(_TEST_CACHE_DIR, ignore_errors=True)
    except Exception:
        pass
