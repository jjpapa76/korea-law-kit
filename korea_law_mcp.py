# -*- coding: utf-8 -*-
"""MCP 서버 실행 파일. `python D:\\...\\korea-law-kit\\korea_law_mcp.py`

`python -m law_kit.mcp_server` 는 law_kit 이 import 경로에 있어야 돈다.
MCP 클라이언트마다 PYTHONPATH 를 따로 넣어 주면 설정이 여섯 군데로
흩어지고, pip 로 설치하면 어느 파이썬에 깔았는지가 또 하나의 의존이
된다. 이 파일은 자기 옆의 law_kit 을 직접 경로에 올린다 - 파일 경로 하나만
알면 어떤 클라이언트에서든 같은 한 줄로 붙는다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from law_kit.mcp_server import main  # noqa: E402

if __name__ == "__main__":
    main()
