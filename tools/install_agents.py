# -*- coding: utf-8 -*-
"""korea-law MCP 서버를 이 PC의 모든 AI 에이전트에 등록하는 통합 설치 도구.

setup_global.py, setup_orca.py, fix_hermes.py를 하나로 통합하여
다양한 AI 에이전트(Claude, Codex, Gemini, Antigravity, Grok, Hermes, OpenClaw,
OpenCode, Cline 등)에 korea-law MCP 서버를 일괄 등록하고 사용 규칙을 반영한다.

왜 통합 스크립트가 필요한가:
    에이전트마다 MCP 서버를 등록하는 방식이 제각각이다.
    어떤 것은 CLI 명령(claude, codex, agy, gemini)을 제공하고,
    어떤 것은 JSON(Claude Desktop, OpenClaw, Cline),
    어떤 것은 TOML(Codex, Grok), 어떤 것은 YAML(Hermes)을 직접 읽는다.
    이 도구는 각 에이전트의 설정 형식에 맞춰 정확한 위치에 멱등하게 등록한다.

원칙:
    1. 기본은 --dry(미리보기)이며, 실제 파일 변경은 --apply를 명시할 때만 수행한다.
    2. 모든 설정 파일은 변경 전 `<파일명>.bak-korea-law`로 백업한다(최초 원본 보존).
    3. 여러 번 실행해도 중복 항목이 생기지 않도록 멱등성(Idempotency)을 보장한다.
    4. 공개 저장소이므로 특정 사용자명이나 절대경로를 코드에 하드코딩하지 않고,
       스크립트 위치와 환경변수(HOME, APPDATA 등), --python 인자로 동적 결정한다.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys

NAME = "korea-law"
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LAUNCHER = os.path.join(REPO, "korea_law_mcp.py")


def say(msg):
    """표준 출력에 한 줄 메시지를 출력한다."""
    try:
        sys.stdout.buffer.write((msg + "\n").encode("utf-8"))
        sys.stdout.flush()
    except Exception:
        print(msg)


def backup_file(path, dry=False):
    """파일이 존재하고 백업이 아직 없을 때만 .bak-korea-law 파일로 복사한다."""
    if not dry and os.path.exists(path):
        bak = path + ".bak-korea-law"
        if not os.path.exists(bak):
            shutil.copy2(path, bak)


def atomic_write(path, content, dry=False):
    """백업 후 임시 파일을 거쳐 안전하게 원자적으로 파일을 교체한다."""
    if dry:
        return
    backup_file(path, dry=dry)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".tmp-korea-law"
    if isinstance(content, bytes):
        with open(tmp, "wb") as f:
            f.write(content)
    else:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(content)
    os.replace(tmp, path)


def read_file_with_encoding(path):
    """BOM 유무와 줄바꿈(CRLF/LF) 형식을 보존하며 텍스트를 읽는다."""
    with open(path, "rb") as f:
        raw = f.read()
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    payload = raw[3:] if has_bom else raw
    text = payload.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text, has_bom, newline


def encode_file_content(text, has_bom):
    """BOM 유무를 반영하여 바이트로 인코딩한다."""
    data = text.encode("utf-8")
    return (b"\xef\xbb\xbf" + data) if has_bom else data


def run_command(label, cmd, dry=False):
    """외부 명령을 실행하고 결과를 기록한다."""
    cmd_str = " ".join(cmd)
    if dry:
        say("[dry] %-14s 실행 예정 -> %s" % (label, cmd_str))
        return 0
    try:
        r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
        lines = (r.stdout + r.stderr).decode("utf-8", "replace").strip().splitlines()
        last_line = lines[-1][:100] if lines else ""
        say("%-14s exit %d %s" % (label, r.returncode, last_line))
        return r.returncode
    except Exception as e:
        say("%-14s 실패: %s" % (label, e))
        return -1


def find_executable(names):
    """이름 목록에서 shutil.which 로 실행 파일의 전체 경로를 찾는다."""
    for name in names:
        p = shutil.which(name)
        if p and os.path.exists(p):
            return p
    return None


def is_server_disabled(data, name=NAME):
    """JSON 설정 데이터에서 사용자가 서버를 꺼 둔 상태인지 확인한다.

    꺼 둔 표시 모양 3가지:
    1. 최상위(또는 하위) _disabled_mcpServers 에 name 이 있는 경우 (dict 키 또는 list 항목)
    2. _disabled_{name} 같은 키가 최상위 또는 하위에 존재하는 경우
    3. name 항목의 설정에 "disabled": True 또는 "enabled": False 가 있는 경우
    """
    if not isinstance(data, (dict, list)):
        return False

    dis_prefix = f"_disabled_{name}"

    def check(obj):
        if isinstance(obj, dict):
            # 1. _disabled_mcpServers 에 name 이 있는지
            if "_disabled_mcpServers" in obj:
                d = obj["_disabled_mcpServers"]
                if isinstance(d, dict) and name in d:
                    return True
                if isinstance(d, (list, tuple, set)) and name in d:
                    return True

            # 2. _disabled_{name} 키가 있는지
            if dis_prefix in obj:
                return True

            # 3. name 항목 내부의 disabled: True / enabled: False
            if name in obj:
                val = obj[name]
                if isinstance(val, dict):
                    if val.get("disabled") is True:
                        return True
                    if val.get("enabled") is False:
                        return True

            # 재귀적으로 하위 객체 탐색
            for v in obj.values():
                if check(v):
                    return True

        elif isinstance(obj, list):
            for item in obj:
                if check(item):
                    return True

        return False

    return check(data)


def update_json_file(label, path, modifier_fn, dry=False, create_if_missing=False):
    """JSON 설정 파일에서 지정된 수정을 적용한다."""
    if not os.path.exists(path):
        if not create_if_missing:
            say("%-14s 건너뜀 - 파일 없음 (%s)" % (label, path))
            return False
        data = {}
        has_bom = False
        newline = "\n"
    else:
        try:
            text, has_bom, newline = read_file_with_encoding(path)
            data = json.loads(text)
        except Exception as e:
            say("%-14s 실패 - JSON 파싱 오류: %s (%s)" % (label, e, path))
            return False

    if is_server_disabled(data, NAME):
        say("%-14s 건너뜀: 사용자가 꺼 둔 상태 - 켜려면 앱에서 켜라 (%s)" % (label, path))
        return False

    modifier_fn(data)
    new_text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if newline == "\r\n":
        new_text = new_text.replace("\r\n", "\n").replace("\n", "\r\n")

    if dry:
        say("[dry] %-14s 등록 예정 -> %s" % (label, path))
        return True
    new_bytes = encode_file_content(new_text, has_bom)
    atomic_write(path, new_bytes, dry=dry)
    say("%-14s 등록 (%s)" % (label, path))
    return True


def update_toml_file(label, path, py_path, launcher_path, extra="", dry=False):
    """TOML 설정 파일에서 [mcp_servers.korea-law] 블록을 멱등하게 갱신한다."""
    if not os.path.exists(path):
        say("%-14s 건너뜀 - 파일 없음 (%s)" % (label, path))
        return False
    try:
        text, has_bom, newline = read_file_with_encoding(path)
    except Exception as e:
        say("%-14s 실패 - 파일 읽기 오류: %s (%s)" % (label, e, path))
        return False

    # 기존 [mcp_servers.korea-law] 블록 줄 단위 제거 (DOTALL 정규식 배제)
    lines = text.splitlines(keepends=True)
    cleaned_lines = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("["):
            if stripped == "[mcp_servers.korea-law]" or stripped.startswith("[mcp_servers.korea-law."):
                skipping = True
                # 블록 앞의 구분용 빈 줄 제거 (추가 시 넣었던 빈 줄 대칭 제거)
                if cleaned_lines and cleaned_lines[-1].strip() == "":
                    cleaned_lines.pop()
                continue
            else:
                skipping = False
        if skipping:
            continue
        cleaned_lines.append(line)
    cleaned = "".join(cleaned_lines)

    # 안전장치: 기존 항목을 지운 뒤에도 korea-law 가 남아 있으면 쓰지 않고 건너뜀
    if re.search(r'(?<![a-zA-Z0-9_-])korea-law(?![a-zA-Z0-9_-])', cleaned):
        say("%-14s 수동 확인 필요: %s - 알아볼 수 없는 korea-law 항목이 있다" % (label, path))
        return False

    if extra.strip():
        extra_formatted = extra.strip().replace("\r\n", "\n").replace("\n", newline)
        extra_part = newline + extra_formatted
    else:
        extra_part = ""
    block = "%s[mcp_servers.korea-law]%scommand = '%s'%sargs = ['%s']%s%s" % (
        newline, newline, py_path, newline, launcher_path, extra_part, newline
    )
    base = cleaned
    if base and not base.endswith(("\r", "\n")):
        base += newline
    new_text = base + block

    if dry:
        say("[dry] %-14s 등록 예정 -> %s" % (label, path))
        return True
    new_bytes = encode_file_content(new_text, has_bom)
    atomic_write(path, new_bytes, dry=dry)
    say("%-14s 등록 (%s)" % (label, path))
    return True


def update_hermes_yaml(label, path, py_path, launcher_path, dry=False):
    """Hermes YAML 설정 파일에 korea-law 항목을 멱등하게 등록한다."""
    if not os.path.exists(path):
        say("%-14s 건너뜀 - 파일 없음 (%s)" % (label, path))
        return False
    try:
        text, has_bom, newline = read_file_with_encoding(path)
    except Exception as e:
        say("%-14s 실패 - 파일 읽기 오류: %s (%s)" % (label, e, path))
        return False

    # 기존 korea-law 항목 줄 단위 제거 (DOTALL 정규식 배제)
    # 가. YAML 블록 안의 빈 줄·주석 줄은 블록의 일부로 보고, 들여쓰기 2칸 이하의 내용 줄이 나올 때 블록이 끝난다.
    lines = text.splitlines(keepends=True)
    cleaned_lines = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        header_cand = line.split("#")[0].rstrip()
        if header_cand == "  korea-law:":
            skipping = True
            continue
        if skipping:
            if stripped == "" or stripped.startswith("#"):
                continue
            indent = len(line) - len(line.lstrip(" "))
            if indent <= 2:
                skipping = False
            else:
                continue
        cleaned_lines.append(line)
    cleaned = "".join(cleaned_lines)

    # 안전장치: 기존 항목을 지운 뒤에도 korea-law 가 남아 있으면 쓰지 않고 건너뜀
    if re.search(r'(?<![a-zA-Z0-9_-])korea-law(?![a-zA-Z0-9_-])', cleaned):
        say("%-14s 수동 확인 필요: %s - 알아볼 수 없는 korea-law 항목이 있다" % (label, path))
        return False

    entry = ("  korea-law:%s    command: '%s'%s    args:%s    - '%s'%s"
             "    connect_timeout: 90%s    enabled: true%s") % (
                 newline, py_path, newline, newline, launcher_path, newline, newline, newline
             )

    result_lines = []
    found_mcp = False
    for line in cleaned_lines:
        result_lines.append(line)
        if not found_mcp and line.split("#")[0].strip() == "mcp_servers:":
            result_lines.append(entry)
            found_mcp = True

    if not found_mcp:
        say("%-14s 실패 - mcp_servers: 항목을 찾을 수 없음 (%s)" % (label, path))
        return False

    new_text = "".join(result_lines)
    if dry:
        say("[dry] %-14s 등록 예정 -> %s" % (label, path))
        return True
    new_bytes = encode_file_content(new_text, has_bom)
    atomic_write(path, new_bytes, dry=dry)
    say("%-14s 등록 (%s)" % (label, path))
    return True


def update_instruction_block(label, path, block_template, repo_path, dry=False, allow_create_dir=False):
    """전역 지시문 파일(AGENTS.md, GEMINI.md 등)에 사용 규칙 블록을 멱등하게 붙인다."""
    if not os.path.exists(path):
        # 상위 디렉터리도 없으면 건너뜀
        if not os.path.exists(os.path.dirname(path)) and not allow_create_dir:
            say("%-14s 건너뜀 - 디렉터리 없음 (%s)" % (label, path))
            return False
        old = ""
        has_bom = False
        newline = "\n"
    else:
        try:
            old, has_bom, newline = read_file_with_encoding(path)
        except Exception as e:
            say("%-14s 실패 - 읽기 오류: %s (%s)" % (label, e, path))
            return False

    # 템플릿 내 경로를 실제 저장소 경로로 치환
    block_text = block_template.replace("{REPO}", repo_path).strip()
    if newline == "\r\n":
        block_text = block_text.replace("\r\n", "\n").replace("\n", "\r\n")

    b, e = "<!-- korea-law-kit:begin -->", "<!-- korea-law-kit:end -->"
    if b in old and e in old:
        before = old[:old.index(b)].rstrip()
        after = old[old.index(e) + len(e):].lstrip("\r\n")
        parts = []
        if before:
            parts.append(before)
            parts.append(newline + newline)
        parts.append(block_text)
        if after:
            parts.append(newline + newline)
            parts.append(after)
        else:
            parts.append(newline)
        new = "".join(parts)
    else:
        before = old.rstrip()
        if before:
            new = before + newline + newline + block_text + newline
        else:
            new = block_text + newline

    if dry:
        say("[dry] %-14s 사용 규칙 블록 예정 -> %s" % (label, path))
        return True
    new_bytes = encode_file_content(new, has_bom)
    atomic_write(path, new_bytes, dry=dry)
    say("%-14s 사용 규칙 블록 반영 (%s)" % (label, path))
    return True


def install(py=None, apply=False):
    """모든 에이전트에 korea-law MCP 서버와 규칙을 등록한다."""
    dry = not apply
    py = py or sys.executable
    home = os.path.expanduser("~")
    appdata = os.environ.get("APPDATA", "")
    localappdata = os.environ.get("LOCALAPPDATA", "")

    say("=== korea-law MCP 전역 에이전트 등록 도구 ===")
    say("모드: %s" % ("적용 (APPLY)" if apply else "미리보기 (DRY-RUN, 실제 변경 없음)"))
    say("저장소: %s" % REPO)
    say("실행기: %s" % LAUNCHER)
    say("파이썬: %s" % py)
    say("사용자 홈: %s" % home)
    say("")

    if not os.path.exists(LAUNCHER):
        say("오류: MCP 실행기 파일이 없습니다: %s" % LAUNCHER)
        return 1

    # 1. 캐시 안내
    say("캐시: ~/.cache/korea-law-kit (LAW_KIT_CACHE 로 바꿀 수 있음)")

    # 지시문 템플릿 로드
    block_template_path = os.path.join(HERE, "agent_block.md")
    block_template = ""
    if os.path.exists(block_template_path):
        with open(block_template_path, "r", encoding="utf-8") as f:
            block_template = f.read()

    # 2. CLI 도구 등록 (존재하는 CLI만 호출, 없으면 건너뜀 안내)
    cmd_args = [py, LAUNCHER]

    # (1) Claude Code
    claude_bin = find_executable(["claude.exe", "claude.cmd", "claude"])
    if claude_bin:
        run_command("Claude Code", [claude_bin, "mcp", "remove", "-s", "user", NAME], dry=dry)
        run_command("Claude Code", [claude_bin, "mcp", "add", "-s", "user", NAME, "--"] + cmd_args, dry=dry)
    else:
        say("%-14s CLI 없음 - 건너뜀" % "Claude Code")

    # (2) Codex CLI
    codex_bin = find_executable(["codex.cmd", "codex.exe", "codex"])
    if codex_bin:
        run_command("codex CLI", [codex_bin, "mcp", "remove", NAME], dry=dry)
        run_command("codex CLI", [codex_bin, "mcp", "add", NAME, "--"] + cmd_args, dry=dry)
    else:
        say("%-14s CLI 없음 - 건너뜀" % "codex CLI")

    # (3) AGY CLI
    agy_bin = find_executable(["agy.exe", "agy.cmd", "agy"])
    if agy_bin:
        run_command("agy CLI", [agy_bin, "mcp", "add", NAME] + cmd_args, dry=dry)
    else:
        say("%-14s CLI 없음 - 건너뜀" % "agy CLI")

    # (4) Gemini CLI
    gemini_bin = find_executable(["gemini.cmd", "gemini.exe", "gemini"])
    if gemini_bin:
        run_command("Gemini CLI", [gemini_bin, "mcp", "add", "-s", "user", NAME] + cmd_args, dry=dry)
    else:
        say("%-14s CLI 없음 - 건너뜀" % "Gemini CLI")

    # (5) OpenClaude
    openclaude_bin = find_executable(["openclaude.cmd", "openclaude.exe", "openclaude"])
    if openclaude_bin:
        run_command("openclaude", [openclaude_bin, "mcp", "remove", "-s", "user", NAME], dry=dry)
        run_command("openclaude", [openclaude_bin, "mcp", "add", "-s", "user", NAME, "--"] + cmd_args, dry=dry)
    else:
        say("%-14s CLI 없음 - 건너뜀" % "openclaude")

    # (6) Copilot CLI
    copilot_bin = find_executable(["copilot.cmd", "copilot.exe", "copilot"])
    if copilot_bin:
        run_command("copilot", [copilot_bin, "mcp", "remove", NAME], dry=dry)
        run_command("copilot", [copilot_bin, "mcp", "add", NAME, "--"] + cmd_args, dry=dry)
    else:
        say("%-14s CLI 없음 - 건너뜀" % "copilot")

    # (7) Kiro CLI
    kiro_bin = find_executable(["kiro-cli.exe", "kiro.cmd", "kiro"])
    if kiro_bin:
        run_command("kiro", [kiro_bin, "mcp", "remove", "--name", NAME, "--scope", "global"], dry=dry)
        run_command("kiro", [kiro_bin, "mcp", "add", "--name", NAME, "--scope", "global",
                             "--command", py, "--args", LAUNCHER], dry=dry)
    else:
        say("%-14s CLI 없음 - 건너뜀" % "kiro")

    # 3. 설정 파일 직접 수정
    # (1) Claude Desktop
    if appdata:
        update_json_file(
            "Claude Desktop",
            os.path.join(appdata, "Claude", "claude_desktop_config.json"),
            lambda d: d.setdefault("mcpServers", {}).__setitem__(NAME, {"command": py, "args": [LAUNCHER]}),
            dry=dry
        )

    # (2) Antigravity IDE
    for ag_dir in ("antigravity-ide", "antigravity"):
        update_json_file(
            "Antigravity IDE",
            os.path.join(home, ".gemini", ag_dir, "mcp_config.json"),
            lambda d: d.setdefault("mcpServers", {}).__setitem__(NAME, {"command": py, "args": [LAUNCHER]}),
            dry=dry
        )

    # (3) Codex config.toml & AGENTS.md
    codex_homes = [os.path.join(home, ".codex")]
    if appdata:
        codex_homes += glob.glob(os.path.join(appdata, "orca", "codex-accounts", "*", "home"))
        codex_homes += glob.glob(os.path.join(appdata, "orca", "codex-runtime-home", "home"))
    for ch in codex_homes:
        update_toml_file("codex", os.path.join(ch, "config.toml"), py, LAUNCHER,
                         extra='default_tools_approval_mode = "approve"\nstartup_timeout_sec = 60', dry=dry)
        if block_template:
            update_instruction_block("codex", os.path.join(ch, "AGENTS.md"), block_template, REPO, dry=dry)

    # (4) Grok config.toml
    update_toml_file("grok", os.path.join(home, ".grok", "config.toml"), py, LAUNCHER,
                     extra="enabled = true\nstartup_timeout_sec = 60", dry=dry)

    # (5) Hermes config.yaml
    hermes_candidates = []
    hermes_home = os.environ.get("HERMES_HOME")
    if hermes_home:
        hermes_candidates.append(os.path.join(hermes_home, "config.yaml"))
    if localappdata:
        hermes_candidates.append(os.path.join(localappdata, "hermes", "config.yaml"))
    hermes_candidates.append(os.path.join(home, ".hermes", "config.yaml"))
    for hp in dict.fromkeys(hermes_candidates):
        update_hermes_yaml("hermes", hp, py, LAUNCHER, dry=dry)

    # (6) OpenClaw openclaw.json
    update_json_file(
        "openclaw",
        os.path.join(home, ".openclaw", "openclaw.json"),
        lambda d: d.setdefault("mcp", {}).setdefault("servers", {}).__setitem__(
            NAME, {"command": py, "args": [LAUNCHER]}
        ),
        dry=dry
    )

    # (7) OpenCode opencode.json & mcp.json
    opencode_dirs = [os.path.join(home, ".config", "opencode")]
    if appdata:
        opencode_dirs += glob.glob(os.path.join(appdata, "orca", "opencode-config-overlays", "*"))
    for od in opencode_dirs:
        update_json_file(
            "opencode",
            os.path.join(od, "opencode.json"),
            lambda d: d.setdefault("mcp", {}).__setitem__(
                NAME, {"type": "local", "command": [py, LAUNCHER], "enabled": True}
            ),
            dry=dry
        )
        update_json_file(
            "opencode",
            os.path.join(od, "mcp.json"),
            lambda d: d.setdefault("mcpServers", {}).__setitem__(
                NAME, {"command": py, "args": [LAUNCHER], "disabled": False, "autoApprove": []}
            ),
            dry=dry
        )

    # (8) Cline cline_mcp_settings.json
    cline_candidates = [os.path.join(home, ".cline", "data", "settings", "cline_mcp_settings.json")]
    if appdata:
        cline_candidates.append(
            os.path.join(appdata, "Code", "User", "globalStorage", "saoudrizwan.claude-dev", "settings", "cline_mcp_settings.json")
        )
    for cp in cline_candidates:
        update_json_file(
            "cline",
            cp,
            lambda d: d.setdefault("mcpServers", {}).__setitem__(NAME, {"command": py, "args": [LAUNCHER]}),
            dry=dry
        )

    # (9) OmO (senpi)
    omo_agent_dir = os.path.join(home, ".omo", "agent")
    update_json_file(
        "OmO",
        os.path.join(omo_agent_dir, "mcp.json"),
        lambda d: d.setdefault("mcpServers", {}).__setitem__(
            NAME,
            {
                "command": py,
                "args": [LAUNCHER],
                "connectTimeoutMs": 60000,
                "startupTimeoutMs": 60000,
                "lifecycle": "keep-alive",
            }
        ),
        dry=dry,
        create_if_missing=True
    )

    # 4. 전역 지시문 (GEMINI.md / AGENTS.md)
    if block_template:
        update_instruction_block("Gemini/agy", os.path.join(home, ".gemini", "GEMINI.md"), block_template, REPO, dry=dry)
        update_instruction_block("agy config", os.path.join(home, ".gemini", "config", "AGENTS.md"), block_template, REPO, dry=dry)
        update_instruction_block("OmO", os.path.join(omo_agent_dir, "AGENTS.md"), block_template, REPO, dry=dry, allow_create_dir=True)

    # 5. 스킬 설치 (Claude, OmO)
    skill_src = os.path.join(HERE, "skills", "korea-law", "SKILL.md")
    if os.path.exists(skill_src):
        for skill_label, skill_parent in [
            ("Claude Skill", os.path.join(home, ".claude", "skills")),
            ("OmO Skill", os.path.join(omo_agent_dir, "skills")),
        ]:
            skill_dst_dir = os.path.join(skill_parent, "korea-law")
            skill_dst_file = os.path.join(skill_dst_dir, "SKILL.md")
            if dry:
                say("[dry] %-14s 스킬 복사 예정 -> %s" % (skill_label, skill_dst_file))
            else:
                os.makedirs(skill_dst_dir, exist_ok=True)
                with open(skill_src, "r", encoding="utf-8") as f:
                    skill_content = f.read()
                skill_content = skill_content.replace("{REPO}", REPO)
                atomic_write(skill_dst_file, skill_content, dry=dry)
                say("%-14s 스킬 등록 (%s)" % (skill_label, skill_dst_file))

    say("")
    if dry:
        say("미리보기가 끝났습니다. 실제 적용하려면 --apply 옵션을 붙여 다시 실행하십시오:")
        say("  python tools/install_agents.py --apply")
    else:
        say("모든 에이전트 등록이 완료되었습니다.")
        say("인증키는 환경변수로 직접 설정하십시오 (새로 연 터미널부터 적용):")
        say("  setx LAW_API_OC <발급받은_인증키>")
    return 0


def main():
    parser = argparse.ArgumentParser(description="korea-law MCP 서버 전역 에이전트 통합 설치 도구")
    parser.add_argument("--apply", action="store_true", help="실제 변경 사항을 적용합니다 (기본은 dry-run)")
    parser.add_argument("--dry", action="store_true", help="미리보기 모드로 실행합니다 (기본값)")
    parser.add_argument("--python", dest="py", default=sys.executable, help="사용할 파이썬 실행 파일 경로 (기본값: 현재 파이썬)")
    args = parser.parse_args()

    apply_mode = args.apply and not args.dry
    return install(py=args.py, apply=apply_mode)


if __name__ == "__main__":
    sys.exit(main())
