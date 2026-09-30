# -*- coding: utf-8 -*-
"""tools/install_agents.py 의 동작과 멱등성을 시험한다.

실제 사용자 환경을 건드리지 않도록 tmp_path 와 monkeypatch 로
HOME, APPDATA, LOCALAPPDATA, HERMES_HOME 환경변수를 완전히 격리하고,
외부 CLI 호출(subprocess.run)은 mock 으로 가로챈다.

검증 항목:
    1. 가짜 설정 파일 6종이 있는 환경에서 --dry 실행 시 아무것도 변경되지 않고 백업도 없음.
    2. --apply 실행 시 가짜 설정 파일 6종에 각각 korea-law 가 정상 등록되고 .bak-korea-law 백업 생성.
    3. --apply 를 2회 연속 실행해도 각 파일에 korea-law 항목이 정확히 1개만 존재 (멱등성 보장).
    4. 외부 CLI 가 가상으로 감지되어도 실제 실행 대신 mock 으로 안전하게 통과.
    5. pi(OmO) 건너뜀 메시지가 출력됨.
"""
import io
import json
import os
import re
import sys
import pytest

from tools import install_agents


@pytest.fixture
def mock_agent_env(tmp_path, monkeypatch):
    """임시 가상 에이전트 환경 디렉터리와 가짜 설정 파일 6종을 생성한다."""
    home_dir = tmp_path / "home"
    appdata_dir = tmp_path / "AppData" / "Roaming"
    localappdata_dir = tmp_path / "AppData" / "Local"
    hermes_home = tmp_path / "hermes_home"

    home_dir.mkdir(parents=True)
    appdata_dir.mkdir(parents=True)
    localappdata_dir.mkdir(parents=True)
    hermes_home.mkdir(parents=True)

    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setenv("APPDATA", str(appdata_dir))
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata_dir))
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    # 1. codex config.toml
    codex_dir = home_dir / ".codex"
    codex_dir.mkdir()
    codex_conf = codex_dir / "config.toml"
    codex_conf.write_text('model = "o3"\n', encoding="utf-8")

    # 2. grok config.toml
    grok_dir = home_dir / ".grok"
    grok_dir.mkdir()
    grok_conf = grok_dir / "config.toml"
    grok_conf.write_text('[general]\nname = "grok"\n', encoding="utf-8")

    # 3. hermes config.yaml
    hermes_conf = hermes_home / "config.yaml"
    hermes_conf.write_text('model: hermes-3\nmcp_servers:\n  notebooklm:\n    command: "py"\n    args: ["n.py"]\n', encoding="utf-8")

    # 4. openclaw.json
    openclaw_dir = home_dir / ".openclaw"
    openclaw_dir.mkdir()
    openclaw_conf = openclaw_dir / "openclaw.json"
    openclaw_conf.write_text('{\n  "version": "1.0",\n  "mcp": {\n    "servers": {}\n  }\n}\n', encoding="utf-8")

    # 5. opencode.json & mcp.json
    opencode_dir = home_dir / ".config" / "opencode"
    opencode_dir.mkdir(parents=True)
    opencode_conf = opencode_dir / "opencode.json"
    opencode_conf.write_text('{\n  "mcp": {}\n}\n', encoding="utf-8")
    opencode_mcp = opencode_dir / "mcp.json"
    opencode_mcp.write_text('{\n  "mcpServers": {}\n}\n', encoding="utf-8")

    # 6. cline_mcp_settings.json
    cline_dir = home_dir / ".cline" / "data" / "settings"
    cline_dir.mkdir(parents=True)
    cline_conf = cline_dir / "cline_mcp_settings.json"
    cline_conf.write_text('{\n  "mcpServers": {}\n}\n', encoding="utf-8")

    # subprocess.run 가로채기
    calls = []
    class DummyResult:
        returncode = 0
        stdout = b"ok"
        stderr = b""

    def dummy_run(cmd, *args, **kwargs):
        calls.append(cmd)
        return DummyResult()

    monkeypatch.setattr("subprocess.run", dummy_run)

    return {
        "home": home_dir,
        "appdata": appdata_dir,
        "localappdata": localappdata_dir,
        "hermes_home": hermes_home,
        "codex_conf": codex_conf,
        "grok_conf": grok_conf,
        "hermes_conf": hermes_conf,
        "openclaw_conf": openclaw_conf,
        "opencode_conf": opencode_conf,
        "opencode_mcp": opencode_mcp,
        "cline_conf": cline_conf,
        "calls": calls,
    }


def test_install_agents_dry_run_changes_nothing(mock_agent_env, capsys):
    """--dry (기본) 모드에서는 설정 파일 내용이 변경되지 않고 백업 파일도 생기지 않는다."""
    env = mock_agent_env

    # 실행 전 원본 내용 기록
    initial_contents = {
        "codex": env["codex_conf"].read_text(encoding="utf-8"),
        "grok": env["grok_conf"].read_text(encoding="utf-8"),
        "hermes": env["hermes_conf"].read_text(encoding="utf-8"),
        "openclaw": env["openclaw_conf"].read_text(encoding="utf-8"),
        "opencode_conf": env["opencode_conf"].read_text(encoding="utf-8"),
        "opencode_mcp": env["opencode_mcp"].read_text(encoding="utf-8"),
        "cline": env["cline_conf"].read_text(encoding="utf-8"),
    }

    # install(apply=False) 실행
    ret = install_agents.install(py="python.exe", apply=False)
    assert ret == 0

    # 파일 변경 여부 확인 - 모두 그대로여야 함
    assert env["codex_conf"].read_text(encoding="utf-8") == initial_contents["codex"]
    assert env["grok_conf"].read_text(encoding="utf-8") == initial_contents["grok"]
    assert env["hermes_conf"].read_text(encoding="utf-8") == initial_contents["hermes"]
    assert env["openclaw_conf"].read_text(encoding="utf-8") == initial_contents["openclaw"]
    assert env["opencode_conf"].read_text(encoding="utf-8") == initial_contents["opencode_conf"]
    assert env["opencode_mcp"].read_text(encoding="utf-8") == initial_contents["opencode_mcp"]
    assert env["cline_conf"].read_text(encoding="utf-8") == initial_contents["cline"]

    # 백업 파일이 생기지 않았는지 확인
    for key, path in env.items():
        if key.endswith(("_conf", "_mcp")):
            bak = path.parent / (path.name + ".bak-korea-law")
            assert not bak.exists(), f"백업 파일이 생성됨: {bak}"

    # pi(OmO) 건너뜀 출력 확인
    captured = capsys.readouterr().out
    assert "pi(OmO)" in captured
    assert "건너뜀" in captured


def test_install_agents_apply_and_idempotency(mock_agent_env, capsys):
    """--apply 를 실행하면 각 설정 파일에 korea-law 항목이 생기고 백업이 생성되며, 2회 실행해도 항목은 1개만 남는다."""
    env = mock_agent_env

    # 1회차 적용
    ret1 = install_agents.install(py="python.exe", apply=True)
    assert ret1 == 0

    # 백업 파일이 생성되었는지 확인
    for key in ("codex_conf", "grok_conf", "hermes_conf", "openclaw_conf", "opencode_conf", "opencode_mcp", "cline_conf"):
        path = env[key]
        bak = path.parent / (path.name + ".bak-korea-law")
        assert bak.exists(), f"백업 파일이 생성되지 않음: {bak}"

    # 2회차 적용 (멱등성 검증)
    ret2 = install_agents.install(py="python.exe", apply=True)
    assert ret2 == 0

    # 1. codex config.toml 검증: [mcp_servers.korea-law] 가 정확히 1개
    codex_text = env["codex_conf"].read_text(encoding="utf-8")
    assert codex_text.count("[mcp_servers.korea-law]") == 1
    assert 'default_tools_approval_mode = "approve"' in codex_text

    # 2. grok config.toml 검증: [mcp_servers.korea-law] 가 정확히 1개
    grok_text = env["grok_conf"].read_text(encoding="utf-8")
    assert grok_text.count("[mcp_servers.korea-law]") == 1
    assert "startup_timeout_sec = 60" in grok_text

    # 3. hermes config.yaml 검증: korea-law 항목이 정확히 1개
    hermes_text = env["hermes_conf"].read_text(encoding="utf-8")
    assert hermes_text.count("korea-law:") == 1
    assert "connect_timeout: 90" in hermes_text

    # 4. openclaw.json 검증: mcp.servers.korea-law 가 1개
    openclaw_data = json.loads(env["openclaw_conf"].read_text(encoding="utf-8"))
    assert "korea-law" in openclaw_data["mcp"]["servers"]
    assert len([k for k in openclaw_data["mcp"]["servers"].keys() if k == "korea-law"]) == 1

    # 5. opencode.json & mcp.json 검증
    opencode_data = json.loads(env["opencode_conf"].read_text(encoding="utf-8"))
    assert "korea-law" in opencode_data["mcp"]
    assert opencode_data["mcp"]["korea-law"]["type"] == "local"

    mcp_data = json.loads(env["opencode_mcp"].read_text(encoding="utf-8"))
    assert "korea-law" in mcp_data["mcpServers"]

    # 6. cline_mcp_settings.json 검증
    cline_data = json.loads(env["cline_conf"].read_text(encoding="utf-8"))
    assert "korea-law" in cline_data["mcpServers"]

    # pi(OmO) 건너뜀 출력 확인
    captured = capsys.readouterr().out
    assert "pi(OmO)" in captured
    assert "건너뜀" in captured


def test_install_agents_does_not_call_setx_for_cache(mock_agent_env, capsys):
    """설치 시 LAW_KIT_CACHE 를 setx 로 등록하지 않고 안내만 출력한다."""
    env = mock_agent_env

    ret = install_agents.install(py="python.exe", apply=True)
    assert ret == 0

    # setx 명령이 subprocess 로 호출되지 않았는지 확인
    for cmd in env["calls"]:
        assert "setx" not in cmd, f"setx 가 호출되었습니다: {cmd}"
        assert "LAW_KIT_CACHE" not in cmd, f"LAW_KIT_CACHE 가 외부 명령으로 실행되었습니다: {cmd}"

    # 캐시 안내 문구가 출력되었는지 확인
    captured = capsys.readouterr().out
    assert "캐시: ~/.cache/korea-law-kit (LAW_KIT_CACHE 로 바꿀 수 있음)" in captured


def test_hermes_yaml_preserves_other_servers_and_sections(tmp_path, monkeypatch):
    """Hermes YAML 에 korea-law 뒤에 다른 서버 2개와 model:, tools: 섹션이 있을 때 --apply 2회 후 원문 그대로 보존되고 korea-law 는 1개여야 한다."""
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    hermes_dir = home_dir / ".hermes"
    hermes_dir.mkdir(parents=True)
    hermes_conf = hermes_dir / "config.yaml"

    initial_yaml = (
        "model: hermes-3\n"
        "mcp_servers:\n"
        "  korea-law:\n"
        "    command: 'old_python'\n"
        "    args:\n"
        "    - 'old_launcher'\n"
        "    connect_timeout: 90\n"
        "    enabled: true\n"
        "  server2:\n"
        "    command: 'node'\n"
        "    args:\n"
        "    - 's2.js'\n"
        "  server3:\n"
        "    command: 'python'\n"
        "    args:\n"
        "    - 's3.py'\n"
        "tools:\n"
        "  web_search: true\n"
        "  bash: false\n"
    )
    hermes_conf.write_text(initial_yaml, encoding="utf-8")

    class DummyResult:
        returncode = 0
        stdout = b"ok"
        stderr = b""
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: DummyResult())

    # --apply 1회차 실행
    ret1 = install_agents.install(py="python.exe", apply=True)
    assert ret1 == 0

    # --apply 2회차 실행
    ret2 = install_agents.install(py="python.exe", apply=True)
    assert ret2 == 0

    result_text = hermes_conf.read_text(encoding="utf-8")

    # korea-law 는 정확히 1개
    assert result_text.count("korea-law:") == 1

    # korea-law 블록을 제외한 줄들이 원본과 정확히 줄 단위로 일치하는지 비교
    def extract_non_korea_law_lines(text):
        lines = text.splitlines(keepends=True)
        out = []
        skipping = False
        for line in lines:
            if line.startswith("  korea-law:"):
                skipping = True
                continue
            if skipping:
                if line.startswith("    "):
                    continue
                skipping = False
            out.append(line)
        return out

    expected_non_korea_law = extract_non_korea_law_lines(initial_yaml)
    actual_non_korea_law = extract_non_korea_law_lines(result_text)

    assert actual_non_korea_law == expected_non_korea_law

    # 각 섹션 및 서버 줄이 개별적으로도 원문 그대로 보존되었는지 확인 (줄 단위 비교)
    assert "model: hermes-3\n" in result_text
    assert "  server2:\n" in result_text
    assert "    command: 'node'\n" in result_text
    assert "  server3:\n" in result_text
    assert "    command: 'python'\n" in result_text
    assert "tools:\n" in result_text
    assert "  web_search: true\n" in result_text
    assert "  bash: false\n" in result_text


def test_toml_json_values_preserved_and_backup_byte_identical(tmp_path, monkeypatch):
    """TOML/JSON 에 기존 다른 항목이 있을 때 적용 후에도 값이 보존되고, 백업 파일은 적용 전 원본과 바이트 동일해야 한다."""
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    # 1. TOML (codex config.toml)
    codex_dir = home_dir / ".codex"
    codex_dir.mkdir(parents=True)
    codex_conf = codex_dir / "config.toml"
    orig_toml = (
        'model = "o3"\n'
        'temperature = 0.7\n\n'
        '[general]\n'
        'name = "agent_x"\n'
        'verbose = true\n\n'
        '[mcp_servers.existing_server]\n'
        'command = "node"\n'
        'args = ["existing.js"]\n'
    )
    codex_bytes = orig_toml.encode("utf-8")
    codex_conf.write_bytes(codex_bytes)

    # 2. JSON (openclaw.json)
    openclaw_dir = home_dir / ".openclaw"
    openclaw_dir.mkdir(parents=True)
    openclaw_conf = openclaw_dir / "openclaw.json"
    orig_json = (
        '{\n'
        '  "version": "2.1",\n'
        '  "custom_setting": "preserve_me",\n'
        '  "mcp": {\n'
        '    "servers": {\n'
        '      "other_tool": {\n'
        '        "command": "tool_bin"\n'
        '      }\n'
        '    }\n'
        '  }\n'
        '}\n'
    )
    openclaw_bytes = orig_json.encode("utf-8")
    openclaw_conf.write_bytes(openclaw_bytes)

    class DummyResult:
        returncode = 0
        stdout = b"ok"
        stderr = b""
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: DummyResult())

    # 적용 실행
    ret = install_agents.install(py="python.exe", apply=True)
    assert ret == 0

    # 백업 파일 바이트 동일성 검증
    codex_bak = codex_dir / "config.toml.bak-korea-law"
    assert codex_bak.exists(), "TOML 백업 파일이 생성되지 않음"
    assert codex_bak.read_bytes() == codex_bytes, "TOML 백업 파일이 원본과 바이트 동일하지 않음"

    openclaw_bak = openclaw_dir / "openclaw.json.bak-korea-law"
    assert openclaw_bak.exists(), "JSON 백업 파일이 생성되지 않음"
    assert openclaw_bak.read_bytes() == openclaw_bytes, "JSON 백업 파일이 원본과 바이트 동일하지 않음"

    # TOML 기존 항목 보존 검증
    applied_toml = codex_conf.read_text(encoding="utf-8")
    assert 'model = "o3"' in applied_toml
    assert 'temperature = 0.7' in applied_toml
    assert '[general]' in applied_toml
    assert 'name = "agent_x"' in applied_toml
    assert '[mcp_servers.existing_server]' in applied_toml
    assert 'command = "node"' in applied_toml
    assert '[mcp_servers.korea-law]' in applied_toml

    # JSON 기존 항목 보존 검증
    applied_json_data = json.loads(openclaw_conf.read_text(encoding="utf-8"))
    assert applied_json_data["version"] == "2.1"
    assert applied_json_data["custom_setting"] == "preserve_me"
    assert "other_tool" in applied_json_data["mcp"]["servers"]
    assert "korea-law" in applied_json_data["mcp"]["servers"]


def test_similar_names_not_touched(tmp_path, monkeypatch):
    """비슷한 이름([mcp_servers.korea-law-extra], 'korea-lawful' 등)은 건드리지 않아야 한다."""
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    # 1. TOML with korea-law-extra & korea-lawful
    codex_dir = home_dir / ".codex"
    codex_dir.mkdir(parents=True)
    codex_conf = codex_dir / "config.toml"
    initial_toml = (
        '[mcp_servers.korea-law-extra]\n'
        'command = "extra_py"\n'
        'args = ["extra.py"]\n\n'
        '[mcp_servers.korea-lawful]\n'
        'command = "lawful_py"\n'
        'args = ["lawful.py"]\n'
    )
    codex_conf.write_text(initial_toml, encoding="utf-8")

    # 2. YAML with korea-law-extra & korea-lawful
    hermes_dir = home_dir / ".hermes"
    hermes_dir.mkdir(parents=True)
    hermes_conf = hermes_dir / "config.yaml"
    initial_yaml = (
        'mcp_servers:\n'
        '  korea-law-extra:\n'
        '    command: "yaml_extra"\n'
        '  korea-lawful:\n'
        '    command: "yaml_lawful"\n'
    )
    hermes_conf.write_text(initial_yaml, encoding="utf-8")

    class DummyResult:
        returncode = 0
        stdout = b"ok"
        stderr = b""
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: DummyResult())

    # 2회 적용 실행
    install_agents.install(py="python.exe", apply=True)
    install_agents.install(py="python.exe", apply=True)

    # 검증: TOML
    result_toml = codex_conf.read_text(encoding="utf-8")
    assert "[mcp_servers.korea-law-extra]" in result_toml
    assert 'command = "extra_py"' in result_toml
    assert "[mcp_servers.korea-lawful]" in result_toml
    assert 'command = "lawful_py"' in result_toml
    assert result_toml.count("[mcp_servers.korea-law]") == 1

    # 검증: YAML
    result_yaml = hermes_conf.read_text(encoding="utf-8")
    assert "  korea-law-extra:\n" in result_yaml
    assert '    command: "yaml_extra"\n' in result_yaml
    assert "  korea-lawful:\n" in result_yaml
    assert '    command: "yaml_lawful"\n' in result_yaml
    assert result_yaml.count("korea-law:") == 1


def test_no_hardcoded_paths_or_username_in_tools():
    """tools/ 디렉터리 내 어떤 파일에도 'D:\\orca', 'npm-global', 사용자 이름이 없어야 한다."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tools_dir = os.path.join(repo_root, "tools")
    assert os.path.isdir(tools_dir), f"tools 디렉터리를 찾을 수 없음: {tools_dir}"

    username = os.environ.get("USERNAME", "")
    forbidden = ["d:\\orca", "d:/orca", "npm-global"]
    if username:
        forbidden.append(username.lower())

    violations = []
    for root, dirs, files in os.walk(tools_dir):
        if "__pycache__" in root:
            continue
        for f in files:
            if f.endswith((".py", ".md", ".json", ".yaml", ".yml", ".toml")):
                file_path = os.path.join(root, f)
                with open(file_path, "r", encoding="utf-8", errors="replace") as fh:
                    content = fh.read().lower()
                for kw in forbidden:
                    if kw in content:
                        violations.append((file_path, kw))

    assert not violations, f"금지된 하드코딩 문자열이 발견되었습니다: {violations}"


def test_cli_missing_output(tmp_path, monkeypatch, capsys):
    """CLI 가 없을 때 'CLI 없음 - 건너뜀' 으로 출력해야 한다."""
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    monkeypatch.setattr("shutil.which", lambda name: None)

    ret = install_agents.install(py="python.exe", apply=False)
    assert ret == 0

    captured = capsys.readouterr().out
    assert "CLI 없음 - 건너뜀" in captured
    assert "Claude Code    CLI 없음 - 건너뜀" in captured
    assert "codex CLI      CLI 없음 - 건너뜀" in captured


def test_crlf_bom_preserved_after_apply_twice(tmp_path, monkeypatch):
    """CRLF·BOM 파일에 --apply 두 번 후 korea-law 블록을 뺀 나머지 바이트가 원본과 동일하고, BOM·CRLF가 유지되어야 한다."""
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    class DummyResult:
        returncode = 0
        stdout = b"ok"
        stderr = b""
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: DummyResult())

    # 1. TOML (Codex config.toml with BOM and CRLF)
    codex_dir = home_dir / ".codex"
    codex_dir.mkdir(parents=True)
    codex_conf = codex_dir / "config.toml"
    orig_toml_str = 'model = "o3"\r\ntemperature = 0.7\r\n\r\n[general]\r\nname = "agent_x"\r\n'
    orig_toml_bytes = b"\xef\xbb\xbf" + orig_toml_str.encode("utf-8")
    codex_conf.write_bytes(orig_toml_bytes)

    # 2. YAML (Hermes config.yaml with BOM and CRLF)
    hermes_dir = home_dir / ".hermes"
    hermes_dir.mkdir(parents=True)
    hermes_conf = hermes_dir / "config.yaml"
    orig_yaml_str = 'model: hermes-3\r\nmcp_servers:\r\n  server2:\r\n    command: \'node\'\r\ntools:\r\n  web_search: true\r\n'
    orig_yaml_bytes = b"\xef\xbb\xbf" + orig_yaml_str.encode("utf-8")
    hermes_conf.write_bytes(orig_yaml_bytes)

    # 3. JSON (OpenClaw openclaw.json with BOM and CRLF)
    openclaw_dir = home_dir / ".openclaw"
    openclaw_dir.mkdir(parents=True)
    openclaw_conf = openclaw_dir / "openclaw.json"
    orig_json_str = '{\r\n  "version": "1.0",\r\n  "mcp": {\r\n    "servers": {}\r\n  }\r\n}\r\n'
    orig_json_bytes = b"\xef\xbb\xbf" + orig_json_str.encode("utf-8")
    openclaw_conf.write_bytes(orig_json_bytes)

    # --apply 1회차 및 2회차 실행
    ret1 = install_agents.install(py="python.exe", apply=True)
    assert ret1 == 0
    ret2 = install_agents.install(py="python.exe", apply=True)
    assert ret2 == 0

    # 검증 1: TOML
    applied_toml_bytes = codex_conf.read_bytes()
    assert applied_toml_bytes.startswith(b"\xef\xbb\xbf"), "TOML 파일의 BOM 이 유실되었습니다."
    applied_toml_text = applied_toml_bytes[3:].decode("utf-8")
    # 단독 LF (\r 없이 오는 \n) 검사
    assert re.search(r'(?<!\r)\n', applied_toml_text) is None, "TOML 파일에 단독 LF 줄바꿈이 발견되었습니다."
    # korea-law 블록 추출 및 제외 후 원본 바이트와 일치 검증
    # 블록 형태: \r\n[mcp_servers.korea-law]\r\n...
    assert "[mcp_servers.korea-law]" in applied_toml_text
    korea_law_toml_block = (
        '\r\n[mcp_servers.korea-law]\r\n'
        'command = \'python.exe\'\r\n'
        f'args = [\'{install_agents.LAUNCHER}\']\r\n'
        'default_tools_approval_mode = "approve"\r\n'
    )
    assert korea_law_toml_block in applied_toml_text
    toml_without_korea_law = applied_toml_text.replace(korea_law_toml_block, "")
    assert (b"\xef\xbb\xbf" + toml_without_korea_law.encode("utf-8")) == orig_toml_bytes, (
        "TOML 에서 korea-law 블록을 뺀 바이트가 원본과 일치하지 않습니다."
    )

    # 검증 2: YAML
    applied_yaml_bytes = hermes_conf.read_bytes()
    assert applied_yaml_bytes.startswith(b"\xef\xbb\xbf"), "YAML 파일의 BOM 이 유실되었습니다."
    applied_yaml_text = applied_yaml_bytes[3:].decode("utf-8")
    assert re.search(r'(?<!\r)\n', applied_yaml_text) is None, "YAML 파일에 단독 LF 줄바꿈이 발견되었습니다."
    korea_law_yaml_entry = (
        '  korea-law:\r\n'
        '    command: \'python.exe\'\r\n'
        '    args:\r\n'
        f'    - \'{install_agents.LAUNCHER}\'\r\n'
        '    connect_timeout: 90\r\n'
        '    enabled: true\r\n'
    )
    assert korea_law_yaml_entry in applied_yaml_text
    yaml_without_korea_law = applied_yaml_text.replace(korea_law_yaml_entry, "")
    assert (b"\xef\xbb\xbf" + yaml_without_korea_law.encode("utf-8")) == orig_yaml_bytes, (
        "YAML 에서 korea-law 블록을 뺀 바이트가 원본과 일치하지 않습니다."
    )

    # 검증 3: JSON
    applied_json_bytes = openclaw_conf.read_bytes()
    assert applied_json_bytes.startswith(b"\xef\xbb\xbf"), "JSON 파일의 BOM 이 유실되었습니다."
    applied_json_text = applied_json_bytes[3:].decode("utf-8")
    assert re.search(r'(?<!\r)\n', applied_json_text) is None, "JSON 파일에 단독 LF 줄바꿈이 발견되었습니다."
    json_data = json.loads(applied_json_text)
    assert json_data["version"] == "1.0"
    assert "korea-law" in json_data["mcp"]["servers"]


def test_unknown_korea_law_inline_table(tmp_path, monkeypatch, capsys):
    """인라인 표 형태의 알 수 없는 korea-law 가 있으면 쓰지 않고 수동 확인 필요를 출력해야 한다."""
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    class DummyResult:
        returncode = 0
        stdout = b"ok"
        stderr = b""
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: DummyResult())

    # TOML 인라인 표
    codex_dir = home_dir / ".codex"
    codex_dir.mkdir(parents=True)
    codex_conf = codex_dir / "config.toml"
    orig_toml = '[mcp_servers]\r\nkorea-law = { command = "old_py", args = ["old.py"] }\r\n'
    orig_toml_bytes = orig_toml.encode("utf-8")
    codex_conf.write_bytes(orig_toml_bytes)

    # YAML 인라인 표
    hermes_dir = home_dir / ".hermes"
    hermes_dir.mkdir(parents=True)
    hermes_conf = hermes_dir / "config.yaml"
    orig_yaml = "mcp_servers:\r\n  korea-law: { command: 'old_py' }\r\n"
    orig_yaml_bytes = orig_yaml.encode("utf-8")
    hermes_conf.write_bytes(orig_yaml_bytes)

    ret = install_agents.install(py="python.exe", apply=True)
    assert ret == 0

    captured = capsys.readouterr().out
    assert "수동 확인 필요" in captured
    assert "알아볼 수 없는 korea-law 항목이 있다" in captured

    # 원본 파일이 전혀 변경되지 않았는지 확인 (쓰지 않음)
    assert codex_conf.read_bytes() == orig_toml_bytes
    assert hermes_conf.read_bytes() == orig_yaml_bytes

    # 백업 파일도 생성되지 않아야 함
    assert not (codex_dir / "config.toml.bak-korea-law").exists()
    assert not (hermes_dir / "config.yaml.bak-korea-law").exists()

    # 중복 없음 (정확히 기존 1개 유지)
    assert codex_conf.read_bytes().count(b"korea-law") == 1
    assert hermes_conf.read_bytes().count(b"korea-law") == 1


def test_unknown_korea_law_tab_indent(tmp_path, monkeypatch, capsys):
    """탭 들여쓰기된 korea-law 가 있으면 쓰지 않고 수동 확인 필요를 출력해야 한다."""
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    class DummyResult:
        returncode = 0
        stdout = b"ok"
        stderr = b""
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: DummyResult())

    hermes_dir = home_dir / ".hermes"
    hermes_dir.mkdir(parents=True)
    hermes_conf = hermes_dir / "config.yaml"
    orig_yaml = "mcp_servers:\n\tkorea-law:\n\t\tcommand: 'old_py'\n"
    hermes_conf.write_text(orig_yaml, encoding="utf-8")

    ret = install_agents.install(py="python.exe", apply=True)
    assert ret == 0

    captured = capsys.readouterr().out
    assert "수동 확인 필요" in captured
    assert "알아볼 수 없는 korea-law 항목이 있다" in captured

    # 파일 쓰지 않음 및 백업 없음
    assert hermes_conf.read_text(encoding="utf-8") == orig_yaml
    assert not (hermes_dir / "config.yaml.bak-korea-law").exists()
    assert hermes_conf.read_text(encoding="utf-8").count("korea-law") == 1


def test_unknown_korea_law_3spaces_indent(tmp_path, monkeypatch, capsys):
    """3칸 들여쓰기된 korea-law 가 있으면 쓰지 않고 수동 확인 필요를 출력해야 한다."""
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    class DummyResult:
        returncode = 0
        stdout = b"ok"
        stderr = b""
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: DummyResult())

    hermes_dir = home_dir / ".hermes"
    hermes_dir.mkdir(parents=True)
    hermes_conf = hermes_dir / "config.yaml"
    orig_yaml = "mcp_servers:\n   korea-law:\n      command: 'old_py'\n"
    hermes_conf.write_text(orig_yaml, encoding="utf-8")

    ret = install_agents.install(py="python.exe", apply=True)
    assert ret == 0

    captured = capsys.readouterr().out
    assert "수동 확인 필요" in captured
    assert "알아볼 수 없는 korea-law 항목이 있다" in captured

    # 파일 쓰지 않음 및 백업 없음
    assert hermes_conf.read_text(encoding="utf-8") == orig_yaml
    assert not (hermes_dir / "config.yaml.bak-korea-law").exists()
    assert hermes_conf.read_text(encoding="utf-8").count("korea-law") == 1


def test_yaml_blank_lines_and_comments_in_block(tmp_path, monkeypatch):
    """YAML korea-law 블록 안의 빈 줄·주석 줄이 있을 때 고아 줄 없이 정확히 1개로 정상 갱신되어야 한다."""
    home_dir = tmp_path / "home"
    home_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    class DummyResult:
        returncode = 0
        stdout = b"ok"
        stderr = b""
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: DummyResult())

    hermes_dir = home_dir / ".hermes"
    hermes_dir.mkdir(parents=True)
    hermes_conf = hermes_dir / "config.yaml"
    initial_yaml = (
        "model: hermes-3\n"
        "mcp_servers:\n"
        "  korea-law:\n"
        "    command: 'old_py'\n"
        "\n"
        "    # 이전 주석 줄\n"
        "    args:\n"
        "    - 'old.py'\n"
        "\n"
        "    connect_timeout: 30\n"
        "  server2:\n"
        "    command: 'node'\n"
        "    args:\n"
        "    - 's2.js'\n"
        "tools:\n"
        "  web_search: true\n"
    )
    hermes_conf.write_text(initial_yaml, encoding="utf-8")

    # 1회차 및 2회차 --apply
    ret1 = install_agents.install(py="python.exe", apply=True)
    assert ret1 == 0
    ret2 = install_agents.install(py="python.exe", apply=True)
    assert ret2 == 0

    result_text = hermes_conf.read_text(encoding="utf-8")

    # 중복 없음: korea-law 항목 정확히 1개
    assert result_text.count("korea-law:") == 1

    # 고아 줄 없음: 이전 블록의 주석이나 이전 속성들이 남아있지 않음
    assert "이전 주석 줄" not in result_text
    assert "old_py" not in result_text
    assert "old.py" not in result_text
    assert "connect_timeout: 30" not in result_text

    # 새로운 정상 속성 반영 확인
    assert "connect_timeout: 90" in result_text

    # 인접 서버 및 섹션 온전히 보존
    assert "  server2:\n" in result_text
    assert "    command: 'node'\n" in result_text
    assert "tools:\n" in result_text
    assert "  web_search: true\n" in result_text



