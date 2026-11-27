#!/usr/bin/env python3
import sys
import os
import json
import stat
import shutil
import subprocess
import urllib.request
import urllib.parse

try:
    from net_compat import force_ipv4, with_retry
    force_ipv4()
except Exception:  # pragma: no cover
    def force_ipv4():
        return False

    def with_retry(fn, attempts=3, delay=1.5, backoff=1.8):
        try:
            return True, fn()
        except Exception as e:
            return False, str(e)

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOOK_SCRIPT = os.path.join(BASE_DIR, "hook_notify.py")
DAEMON_SCRIPT = os.path.join(BASE_DIR, "tg_bot_daemon.py")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
# Không cho tiến trình console con mở cửa sổ nhấp nháy trên Windows
NO_WINDOW_FLAG = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if IS_WINDOWS else 0

# Lệnh gọi hook đa nền tảng.
# Windows: dùng pythonw.exe (KHÔNG có console) để khi IDE (tiến trình GUI) gọi hook
# thì không bật lên cửa sổ console đen rồi tắt. Hook chỉ in JSON thông báo ra stdout,
# nên khi stdout = None thì print() bị bỏ qua — mọi hành vi quan trọng đều đi qua HTTP.
if IS_WINDOWS:
    _pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(_pyw):
        _pyw = sys.executable
    PY_EXEC = f'"{_pyw}"'
    HOOK_CMD_PREFIX = f'{PY_EXEC} "{HOOK_SCRIPT}"'
else:
    HOOK_CMD_PREFIX = f'python3 "{HOOK_SCRIPT}"'

RULE_BLOCK = """
<!-- AI_TELEGRAM_NOTIFY_RULE -->
# Telegram Notification Rule
Khi bạn rơi vào một trong các trường hợp sau:
1. Cần con người quyết định nghiệp vụ, kiến trúc, hoặc chọn giữa các phương án thiết kế.
2. Hoàn thành một cột mốc (milestone) quan trọng hoặc gặp lỗi chặn (blocker) không thể tự giải quyết.
Hãy luôn đặt nhãn `[TG_NOTIFY]` ở dòng đầu tiên của câu trả lời để hệ thống Hook gửi thông báo sang Telegram cho tôi.
"""


def ensure_executable(path):
    if IS_WINDOWS:
        return
    try:
        st = os.stat(path)
        os.chmod(path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    except Exception:
        pass


def ensure_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            token = cfg.get("bot_token", "").strip()
            chat_id = str(cfg.get("chat_id", "")).strip()
            if token and chat_id and "YOUR_" not in token and "DÁN_" not in token:
                return cfg
        except Exception:
            pass

    print("=== Cấu hình Telegram Bot (Lần đầu sử dụng) ===")
    print("Bạn có thể copy config.sample.json -> config.json hoặc nhập trực tiếp dưới đây:")
    token = input("1. Nhập BOT_TOKEN (lấy từ @BotFather): ").strip()
    chat_id = input("2. Nhập CHAT_ID (lấy từ @userinfobot): ").strip()
    cfg = {
        "bot_token": token,
        "chat_id": chat_id,
        "min_duration_seconds": 180,
        "multiplexer_bin": "tmux",
    }
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    print(f"[OK] Đã lưu cấu hình vào: {CONFIG_PATH}")
    return cfg


def update_agy_hooks():
    hooks_path = os.path.expanduser("~/.gemini/config/hooks.json")
    os.makedirs(os.path.dirname(hooks_path), exist_ok=True)
    data = {}
    if os.path.exists(hooks_path):
        try:
            with open(hooks_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

    data["telegram-bridge"] = {
        "enabled": True,
        "PreInvocation": [
            {
                "type": "command",
                "command": f"{HOOK_CMD_PREFIX} start",
            }
        ],
        "PreToolUse": [
            {
                "matcher": "ask_question",
                "hooks": [
                    {
                        "type": "command",
                        "command": f"{HOOK_CMD_PREFIX} ask",
                    }
                ],
            }
        ],
        "Stop": [
            {
                "type": "command",
                "command": f"{HOOK_CMD_PREFIX} stop",
                "timeout": 600,
            }
        ],
    }

    with open(hooks_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"[OK] Đã cấu hình Hook cho Antigravity (agy): {hooks_path}")


def update_claude_hooks():
    settings_path = os.path.expanduser("~/.claude/settings.json")
    os.makedirs(os.path.dirname(settings_path), exist_ok=True)
    data = {}
    if os.path.exists(settings_path):
        try:
            with open(settings_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

    hooks = data.setdefault("hooks", {})

    def add_claude_hook(event_name, cmd, timeout=30, label="AI Telegram Bridge"):
        arr = hooks.setdefault(event_name, [])
        arr[:] = [
            item for item in arr
            if not any("hook_notify.py" in h.get("command", "") for h in item.get("hooks", []))
        ]
        arr.append({
            "hooks": [{
                "type": "command",
                "name": label,
                "statusMessage": label,
                "command": cmd,
                "timeout": timeout,
            }]
        })

    add_claude_hook("UserPromptSubmit", f"{HOOK_CMD_PREFIX} start", 15, "AI Telegram Bridge (Start)")
    add_claude_hook("Notification", f"{HOOK_CMD_PREFIX} ask", 15, "AI Telegram Bridge (Ask)")
    add_claude_hook("PermissionRequest", f"{HOOK_CMD_PREFIX} ask", 15, "AI Telegram Bridge (PermissionRequest)")
    add_claude_hook("Stop", f"{HOOK_CMD_PREFIX} stop", 600, "AI Telegram Bridge (Stop)")
    add_claude_hook("StopFailure", f"{HOOK_CMD_PREFIX} stop_failure", 30, "AI Telegram Bridge (StopFailure)")

    with open(settings_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"[OK] Đã cấu hình Hook cho Claude Code: {settings_path}")


def update_codex_hooks():
    hooks_path = os.path.expanduser("~/.codex/hooks.json")
    os.makedirs(os.path.dirname(hooks_path), exist_ok=True)
    data = {}
    if os.path.exists(hooks_path):
        try:
            with open(hooks_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}

    hooks = data.setdefault("hooks", {})

    def add_codex_hook(event_name, cmd, timeout=30, label="AI Telegram Bridge"):
        arr = hooks.setdefault(event_name, [])
        arr[:] = [
            item for item in arr
            if not any("hook_notify.py" in h.get("command", "") for h in item.get("hooks", []))
        ]
        arr.append({
            "hooks": [{
                "type": "command",
                "statusMessage": label,
                "command": cmd,
                "timeout": timeout,
            }]
        })

    add_codex_hook("UserPromptSubmit", f"{HOOK_CMD_PREFIX} start", 15, "AI Telegram Bridge (Start)")
    add_codex_hook("Stop", f"{HOOK_CMD_PREFIX} stop", 600, "AI Telegram Bridge (Stop)")

    with open(hooks_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    trust_codex_hooks(hooks_path)
    print(f"[OK] Đã cấu hình Hook cho Codex CLI: {hooks_path}")


def trust_codex_hooks(hooks_path):
    """Tự động cập nhật trusted_hash trong ~/.codex/config.toml để Codex không chặn Hook khi cấu hình thay đổi."""
    codex_bin = shutil.which("codex")
    if not codex_bin and IS_MAC:
        candidate = "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex"
        if os.path.exists(candidate):
            codex_bin = candidate
    if not codex_bin:
        return

    config_toml = os.path.expanduser("~/.codex/config.toml")
    try:
        p = subprocess.Popen(
            [codex_bin, "app-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "ai-telegram", "version": "1.0"}}}) + "\n")
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "initialized"}) + "\n")
        p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "hooks/list", "params": {}}) + "\n")
        p.stdin.flush()

        hook_hashes = {}
        for _ in range(15):
            line = p.stdout.readline()
            if not line:
                break
            if '"id":2' in line or '"id": 2' in line:
                res = json.loads(line).get("result", {})
                for group in res.get("data", []):
                    for h in group.get("hooks", []):
                        if "hook_notify.py" in (h.get("command") or "") and h.get("key") and h.get("currentHash"):
                            hook_hashes[h["key"]] = h["currentHash"]
                break
        p.terminate()

        if hook_hashes and os.path.exists(config_toml):
            with open(config_toml, "r", encoding="utf-8") as f:
                toml_text = f.read()
            import re
            for key, cur_hash in hook_hashes.items():
                section_header = f'[hooks.state."{key}"]'
                if section_header in toml_text:
                    pattern = re.compile(
                        re.escape(section_header) + r'(\s*\n(?:[^\[]*?))trusted_hash\s*=\s*"[^"]*"',
                        re.DOTALL,
                    )
                    toml_text = pattern.sub(f'{section_header}\\1trusted_hash = "{cur_hash}"', toml_text)
                else:
                    toml_text = toml_text.rstrip() + f'\n\n{section_header}\ntrusted_hash = "{cur_hash}"\nenabled = true\n'
            with open(config_toml, "w", encoding="utf-8") as f:
                f.write(toml_text)
    except Exception:
        pass


def append_rule_file(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = ""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            existing = f.read()
    if "AI_TELEGRAM_NOTIFY_RULE" not in existing:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n" + RULE_BLOCK.strip() + "\n")
        print(f"[OK] Đã thêm Rule [TG_NOTIFY] vào: {path}")
    else:
        print(f"[OK] Rule [TG_NOTIFY] đã có sẵn trong: {path}")


def setup_macos_service():
    plist_dir = os.path.expanduser("~/Library/LaunchAgents")
    os.makedirs(plist_dir, exist_ok=True)
    plist_path = os.path.join(plist_dir, "com.thaipd.ai-telegram.plist")
    log_out = os.path.join(BASE_DIR, "daemon.log")
    log_err = os.path.join(BASE_DIR, "daemon.err.log")
    py_bin = shutil.which("python3") or "/usr/bin/python3"

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.thaipd.ai-telegram</string>
    <key>ProgramArguments</key>
    <array>
        <string>{py_bin}</string>
        <string>{DAEMON_SCRIPT}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{log_out}</string>
    <key>StandardErrorPath</key>
    <string>{log_err}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    </dict>
</dict>
</plist>
"""
    with open(plist_path, "w", encoding="utf-8") as f:
        f.write(plist_content)

    subprocess.run(["launchctl", "unload", plist_path], stderr=subprocess.DEVNULL)
    subprocess.run(["launchctl", "load", "-w", plist_path], check=False)
    print(f"[OK] Đã kích hoạt LaunchAgent trên macOS: {plist_path}")


def setup_linux_service():
    systemd_dir = os.path.expanduser("~/.config/systemd/user")
    os.makedirs(systemd_dir, exist_ok=True)
    service_path = os.path.join(systemd_dir, "ai-telegram.service")
    py_bin = shutil.which("python3") or "/usr/bin/python3"

    service_content = f"""[Unit]
Description=AI Telegram Bridge Daemon (agy / claude / codex)
After=network.target

[Service]
Type=simple
ExecStart={py_bin} "{DAEMON_SCRIPT}"
Restart=always
RestartSec=3
Environment=PATH=/usr/local/bin:/usr/bin:/bin

[Install]
WantedBy=default.target
"""
    with open(service_path, "w", encoding="utf-8") as f:
        f.write(service_content)

    res = subprocess.run(["systemctl", "--user", "daemon-reload"], stderr=subprocess.DEVNULL)
    if res.returncode == 0:
        subprocess.run(["systemctl", "--user", "enable", "--now", "ai-telegram.service"], check=False)
        subprocess.run(["systemctl", "--user", "restart", "ai-telegram.service"], check=False)
        print(f"[OK] Đã kích hoạt systemd user service trên Ubuntu/Linux: {service_path}")
    else:
        # Fallback cho WSL / Docker không bật systemd
        subprocess.run(["pkill", "-f", "tg_bot_daemon.py"], stderr=subprocess.DEVNULL)
        log_out = open(os.path.join(BASE_DIR, "daemon.log"), "a")
        subprocess.Popen(
            [py_bin, DAEMON_SCRIPT],
            stdin=subprocess.DEVNULL,
            stdout=log_out,
            stderr=log_out,
            start_new_session=True,
        )
        print("[OK] Đã chạy ngầm tg_bot_daemon.py (chế độ nohup cho WSL/Linux)")


def setup_windows_service():
    # Tìm pythonw.exe để chạy ngầm không hiện cửa sổ CMD đen trên Windows 10/11
    py_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(py_dir, "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable

    vbs_path = create_windows_startup(pythonw)

    # Dừng script giám sát CŨ (đang chạy vòng lặp trong RAM) + daemon cũ
    kill_old_supervisor()
    stop_daemon()

    # QUAN TRỌNG: ghi stdout/stderr vào file log để không bị "chết lặng" khi daemon lỗi
    log_out = open(os.path.join(BASE_DIR, "daemon.log"), "a", encoding="utf-8")
    log_err = open(os.path.join(BASE_DIR, "daemon.err.log"), "a", encoding="utf-8")
    subprocess.Popen(
        [pythonw, DAEMON_SCRIPT],
        stdin=subprocess.DEVNULL,
        stdout=log_out,
        stderr=log_err,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )
    print(f"[OK] Đã cài đặt tự khởi động cùng Windows 10/11 và bật chạy ngầm: {vbs_path}")


def windows_startup_dir():
    return os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~\\AppData\\Roaming")),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
    )


def create_windows_startup(pythonw=None):
    """
    Tạo script Startup (.vbs) giám sát daemon cùng Windows.

    QUAN TRỌNG (v1.8.1): script chỉ khởi động lại daemon khi heartbeat ĐÃ CŨ (>3 phút).
    Trước đây script spawn daemon mới mỗi 15 giây → mỗi lần spawn lại chạy kiểm tra
    khóa (tasklist/powershell) → các cửa sổ console nhấp nháy liên tục trên desktop.
    """
    if pythonw is None:
        py_dir = os.path.dirname(sys.executable)
        pythonw = os.path.join(py_dir, "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable
    startup_dir = windows_startup_dir()
    os.makedirs(startup_dir, exist_ok=True)
    vbs_path = os.path.join(startup_dir, "ai-telegram-daemon.vbs")
    heartbeat = os.path.join(BASE_DIR, "state", "daemon_heartbeat.txt").replace("\\", "\\")
    vbs_content = (
        'Set WshShell = CreateObject("WScript.Shell")\n'
        'Set fso = CreateObject("Scripting.FileSystemObject")\n'
        f'cmd = """{pythonw}"" ""{DAEMON_SCRIPT}"""\n'
        f'hb = "{heartbeat}"\n'
        "Dim alive\n"
        "Do While True\n"
        "  alive = False\n"
        "  If fso.FileExists(hb) Then\n"
        "    If (Now - fso.GetFile(hb).DateLastModified) < 3 Then alive = True\n"
        "  End If\n"
        "  If Not alive Then\n"
        "    WshShell.Run cmd, 0, False\n"
        "  End If\n"
        "  WScript.Sleep 15000\n"
        "Loop\n"
    )
    with open(vbs_path, "w", encoding="utf-8") as f:
        f.write(vbs_content)
    return vbs_path


def kill_old_supervisor():
    """
    Dừng tiến trình wscript.exe đang chạy script giám sát CŨ.
    Cần thiết khi cài lại: file .vbs cũ đã bị ghi đè nhưng tiến trình cũ vẫn chạy vòng lặp
    trong RAM và tiếp tục spawn daemon mỗi 15 giây.
    """
    if not IS_WINDOWS:
        return
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'wscript.exe' -and "
         "$_.CommandLine -like '*ai-telegram-daemon.vbs*' } | "
         "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )


def remove_windows_startup():
    """Gỡ script Startup (tắt tự khởi động cùng Windows)."""
    vbs_path = os.path.join(windows_startup_dir(), "ai-telegram-daemon.vbs")
    if os.path.exists(vbs_path):
        try:
            os.remove(vbs_path)
            return True
        except OSError:
            return False
    return False


def _strip_hook_entries(hooks_obj):
    """Xoá các entry hook có chứa 'hook_notify.py' khỏi cấu trúc hooks. Trả về True nếu có thay đổi."""
    changed = False
    for event in list(hooks_obj.keys()):
        arr = hooks_obj.get(event) or []
        kept = []
        for item in arr:
            cmds = [h.get("command", "") for h in (item.get("hooks") or [])]
            if any("hook_notify.py" in c for c in cmds):
                changed = True
                continue
            kept.append(item)
        if kept:
            hooks_obj[event] = kept
        else:
            del hooks_obj[event]
    return changed


def remove_claude_hooks():
    """Gỡ hook của Claude Code khỏi ~/.claude/settings.json."""
    path = os.path.expanduser("~/.claude/settings.json")
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        hooks = data.get("hooks") or {}
        if not _strip_hook_entries(hooks):
            return False
        if hooks:
            data["hooks"] = hooks
        else:
            data.pop("hooks", None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


def remove_agy_hooks():
    """Gỡ hook của Antigravity khỏi ~/.gemini/config/hooks.json."""
    path = os.path.expanduser("~/.gemini/config/hooks.json")
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if "telegram-bridge" in data:
            data.pop("telegram-bridge", None)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            return True
    except Exception:
        pass
    return False


def remove_codex_hooks():
    """Gỡ hook của Codex khỏi ~/.codex/hooks.json."""
    path = os.path.expanduser("~/.codex/hooks.json")
    if not os.path.exists(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        hooks = data.get("hooks") or {}
        if not _strip_hook_entries(hooks):
            return False
        if hooks:
            data["hooks"] = hooks
        else:
            data.pop("hooks", None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        return True
    except Exception:
        return False


def remove_rule_files():
    """Gỡ khối rule [TG_NOTIFY] đã thêm vào CLAUDE.md / GEMINI.md / AGENTS.md."""
    removed = []
    for path in (
        os.path.expanduser("~/.gemini/config/GEMINI.md"),
        os.path.expanduser("~/.claude/CLAUDE.md"),
        os.path.expanduser("~/.codex/AGENTS.md"),
    ):
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
            block = RULE_BLOCK.strip()
            if block in text:
                text = text.replace(block, "").replace("\n\n\n", "\n\n")
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                removed.append(path)
        except Exception:
            pass
    return removed


def remove_all_hooks():
    """Gỡ toàn bộ hook + rule đã cài (AI sẽ không còn gửi thông báo Telegram nữa)."""
    results = {
        "claude": remove_claude_hooks(),
        "agy": remove_agy_hooks(),
        "codex": remove_codex_hooks(),
    }
    rules = remove_rule_files()
    return results, rules


def stop_daemon():
    """Dừng mọi tiến trình daemon đang chạy."""
    if IS_WINDOWS:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*tg_bot_daemon.py*' } | "
             "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
    else:
        subprocess.run(["pkill", "-f", "tg_bot_daemon.py"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # Dọn file khóa để lần start sau không bị hiểu nhầm là "đang chạy"
    try:
        lock = os.path.join(BASE_DIR, "state", "daemon.pid")
        if os.path.exists(lock):
            os.remove(lock)
    except OSError:
        pass


def start_daemon_only():
    """Chạy daemon ngay (không đụng tới cấu hình hook, không bật/tắt tự khởi động)."""
    if IS_WINDOWS:
        py_dir = os.path.dirname(sys.executable)
        pythonw = os.path.join(py_dir, "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = sys.executable
        log_out = open(os.path.join(BASE_DIR, "daemon.log"), "a", encoding="utf-8")
        log_err = open(os.path.join(BASE_DIR, "daemon.err.log"), "a", encoding="utf-8")
        subprocess.Popen(
            [pythonw, DAEMON_SCRIPT],
            stdin=subprocess.DEVNULL,
            stdout=log_out,
            stderr=log_err,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
        )
    else:
        log_out = open(os.path.join(BASE_DIR, "daemon.log"), "a", encoding="utf-8")
        subprocess.Popen(
            [sys.executable, DAEMON_SCRIPT],
            stdin=subprocess.DEVNULL,
            stdout=log_out,
            stderr=log_out,
            start_new_session=True,
        )
    print("[OK] Đã khởi động daemon.")


def check_hooks_installed():
    """Kiểm tra hook của agy / claude / codex còn được cài hay không."""
    state = {"claude": False, "agy": False, "codex": False}

    claude_path = os.path.expanduser("~/.claude/settings.json")
    if os.path.exists(claude_path):
        try:
            with open(claude_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for arr in (data.get("hooks") or {}).values():
                for item in arr or []:
                    for h in item.get("hooks") or []:
                        if "hook_notify.py" in (h.get("command") or ""):
                            state["claude"] = True
        except Exception:
            pass

    agy_path = os.path.expanduser("~/.gemini/config/hooks.json")
    if os.path.exists(agy_path):
        try:
            with open(agy_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            state["agy"] = "telegram-bridge" in data
        except Exception:
            pass

    codex_path = os.path.expanduser("~/.codex/hooks.json")
    if os.path.exists(codex_path):
        try:
            with open(codex_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for arr in (data.get("hooks") or {}).values():
                for item in arr or []:
                    for h in item.get("hooks") or []:
                        if "hook_notify.py" in (h.get("command") or ""):
                            state["codex"] = True
        except Exception:
            pass

    return state


def autostart_status():
    """In trạng thái tự khởi động + daemon."""
    print("=== Trạng thái AI Telegram Bridge ===")
    if IS_WINDOWS:
        vbs = os.path.join(windows_startup_dir(), "ai-telegram-daemon.vbs")
        print(f"🪟 Tự khởi động cùng Windows: {'✅ ĐANG BẬT' if os.path.exists(vbs) else '⛔ ĐANG TẮT'}")
        print(f"   ({vbs})")
    elif IS_MAC:
        plist = os.path.expanduser("~/Library/LaunchAgents/com.thaipd.ai-telegram.plist")
        print(f"🍎 LaunchAgent: {'✅ ĐANG BẬT' if os.path.exists(plist) else '⛔ ĐANG TẮT'}")
    else:
        svc = os.path.expanduser("~/.config/systemd/user/ai-telegram.service")
        print(f"🐧 systemd --user: {'✅ ĐANG BẬT' if os.path.exists(svc) else '⛔ ĐANG TẮT'}")
    hb = os.path.join(BASE_DIR, "state", "daemon_heartbeat.txt")
    if os.path.exists(hb):
        try:
            with open(hb, "r", encoding="utf-8") as f:
                info = json.load(f)
            import time as _t
            age = int(_t.time() - float(info.get("ts", 0)))
            print(f"💓 Daemon: đang chạy (PID {info.get('pid')}, v{info.get('version')}, heartbeat {age}s trước)")
        except Exception:
            print("💓 Daemon: heartbeat không đọc được")
    else:
        print("💓 Daemon: chưa thấy heartbeat (có thể đang tắt)")
    hook_state = check_hooks_installed()
    for name, ok in hook_state.items():
        print(f"🪝 Hook {name}: {'✅ đang cài' if ok else '⛔ không có'}")


def disable_autostart(remove_hooks=True):
    """
    Tắt bot:
    - Gỡ tự khởi động cùng máy (Startup / LaunchAgent / systemd)
    - Dừng daemon
    - (mặc định) Gỡ luôn hook + rule [TG_NOTIFY] để AI không còn tự gửi thông báo
    """
    if IS_WINDOWS:
        removed = remove_windows_startup()
        print("[OK] Đã gỡ script Startup (không còn tự chạy khi bật máy)." if removed else "[WARN] Không tìm thấy script Startup để gỡ.")
        stop_daemon()
        print("[OK] Đã dừng daemon.")
    elif IS_MAC:
        plist = os.path.expanduser("~/Library/LaunchAgents/com.thaipd.ai-telegram.plist")
        subprocess.run(["launchctl", "unload", "-w", plist], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            if os.path.exists(plist):
                os.remove(plist)
        except OSError:
            pass
        stop_daemon()
        print("[OK] Đã tắt LaunchAgent và dừng daemon.")
    else:
        svc = os.path.expanduser("~/.config/systemd/user/ai-telegram.service")
        subprocess.run(["systemctl", "--user", "disable", "--now", "ai-telegram.service"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            if os.path.exists(svc):
                os.remove(svc)
        except OSError:
            pass
        subprocess.run(["systemctl", "--user", "daemon-reload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        stop_daemon()
        print("[OK] Đã tắt systemd service và dừng daemon.")

    if remove_hooks:
        results, rules = remove_all_hooks()
        for name, ok in results.items():
            print(f"[OK] Đã gỡ hook {name}." if ok else f"[--] Không có hook {name} để gỡ.")
        for p in rules:
            print(f"[OK] Đã gỡ rule [TG_NOTIFY] khỏi: {p}")
        print("=> Bot đã được TẮT HOÀN TOÀN: không tự chạy khi bật máy, không tự gửi thông báo.")
    else:
        print("=> Đã tắt daemon + tự khởi động. Hook vẫn còn (AI vẫn gửi thông báo Telegram). "
              "Dùng `--disable` không kèm `--keep-hooks` để gỡ cả hook.")


def enable_autostart():
    """Bật lại toàn bộ: cài hook + tự khởi động + chạy daemon."""
    update_agy_hooks()
    update_claude_hooks()
    update_codex_hooks()
    append_rule_file(os.path.expanduser("~/.gemini/config/GEMINI.md"))
    append_rule_file(os.path.expanduser("~/.claude/CLAUDE.md"))
    append_rule_file(os.path.expanduser("~/.codex/AGENTS.md"))

    if IS_WINDOWS:
        vbs = create_windows_startup()
        stop_daemon()
        start_daemon_only()
        print(f"[OK] Đã bật tự khởi động cùng Windows: {vbs}")
    elif IS_MAC:
        setup_macos_service()
        start_daemon_only()
        print("[OK] Đã bật LaunchAgent.")
    else:
        setup_linux_service()
        start_daemon_only()
        print("[OK] Đã bật systemd user service.")
    print("[OK] Đã cài lại hook cho agy / claude / codex.")


def verify_daemon_alive(wait_seconds=6):
    """Kiểm tra daemon có thực sự sống sau khi cài (dựa vào file heartbeat trong state/)."""
    import time
    hb = os.path.join(BASE_DIR, "state", "daemon_heartbeat.txt")
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if os.path.exists(hb):
            try:
                with open(hb, "r", encoding="utf-8") as f:
                    info = json.load(f)
                print(f"[OK] Daemon đang chạy (PID {info.get('pid')}, phiên bản {info.get('version')}).")
                return True
            except Exception:
                pass
        time.sleep(0.5)
    print("[WARN] Daemon CHƯA phản hồi sau khi khởi động.")
    err_log = os.path.join(BASE_DIR, "daemon.err.log")
    if os.path.exists(err_log):
        try:
            with open(err_log, "r", encoding="utf-8", errors="replace") as f:
                tail = f.readlines()[-15:]
            if tail:
                print("----- daemon.err.log (15 dòng cuối) -----")
                for line in tail:
                    print("   " + line.rstrip())
        except Exception:
            pass
    print("→ Hãy thử chạy thủ công để xem lỗi:  python tg_bot_daemon.py")
    return False


def send_welcome_message(cfg):
    url = f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage"
    os_name = "Windows 10/11" if IS_WINDOWS else ("macOS" if IS_MAC else "Ubuntu / Linux")
    msg = (
        f"🎉 *Đã kết nối thành công ({os_name}) <-> Telegram!*\n\n"
        "• Công cụ hỗ trợ: `agy`, `claude`, `codex`\n"
        "• Bấm nút Menu `[☰]` góc trái hoặc gõ `/list` để chọn cửa sổ điều khiển.\n"
        "• Gõ `/screen` để chụp màn hình terminal hiện tại.\n"
        "• Nhắn tin bất kỳ để hỏi AI và nhận phản hồi trực tiếp tại đây!"
    )
    data = urllib.parse.urlencode({
        "chat_id": cfg["chat_id"],
        "text": msg,
        "parse_mode": "Markdown",
    }).encode("utf-8")

    def _send():
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=15) as res:
            return json.loads(res.read().decode("utf-8"))

    ok, result = with_retry(_send, attempts=4, delay=2.0)
    if ok:
        try:
            if result.get("ok"):
                print("[OK] Đã gửi tin nhắn kiểm tra thành công tới Telegram!")
        except Exception:
            pass
    else:
        print(f"[WARN] Không gửi được tin nhắn test Telegram: {result}")
        print("       (Đây chỉ là TIN NHẮN THỬ — bot vẫn hoạt động bình thường.)")
        print("       Nếu Telegram không nhận được gì: kiểm tra tường lửa/VPN hoặc thử lại sau.")


def install_hooks_only():
    """
    Chỉ cài Hook + rule [TG_NOTIFY], KHÔNG chạy daemon và KHÔNG bật tự khởi động.
    Dùng khi máy này chỉ cần AI báo thông báo Telegram, còn daemon chạy ở máy khác
    (tránh 2 tiến trình cùng poll getUpdates với 1 bot token).
    """
    update_agy_hooks()
    update_claude_hooks()
    update_codex_hooks()
    append_rule_file(os.path.expanduser("~/.gemini/config/GEMINI.md"))
    append_rule_file(os.path.expanduser("~/.claude/CLAUDE.md"))
    append_rule_file(os.path.expanduser("~/.codex/AGENTS.md"))
    print("[OK] Đã cài Hook cho agy / claude / codex (KHÔNG bật daemon, KHÔNG tự khởi động).")
    print("     → Máy này chỉ gửi thông báo; phần nhận lệnh/nút bấm do daemon ở máy khác xử lý.")


def main():
    args = [a.lower() for a in sys.argv[1:]]
    keep_hooks = "--keep-hooks" in args

    # --- Các lệnh điều khiển nhanh ---
    if "--status" in args or "-s" in args:
        autostart_status()
        return
    if "--disable" in args or "--off" in args:
        disable_autostart(remove_hooks=not keep_hooks)
        return
    if "--enable" in args or "--on" in args:
        enable_autostart()
        verify_daemon_alive()
        return
    if "--hooks-only" in args:
        install_hooks_only()
        return
    if "--install-agy" in args:
        try:
            import install_agy
            sys.exit(install_agy.main())
        except Exception as e:
            print(f"[WARN] Không chạy được install_agy: {e}")
        return
    if "--stop" in args:
        stop_daemon()
        print("[OK] Đã dừng daemon (tự khởi động vẫn giữ nguyên).")
        return
    if "--start" in args:
        start_daemon_only()
        verify_daemon_alive()
        return
    if "--help" in args or "-h" in args:
        print(
            "Cách dùng: python install.py [lệnh]\n"
            "  (không có lệnh)   Cài đặt đầy đủ: hook + tự khởi động + chạy daemon\n"
            "  --enable / --on   Bật lại TẤT CẢ: cài hook + tự khởi động + chạy daemon\n"
            "  --disable / --off TẮT TẤT CẢ: gỡ tự khởi động + dừng daemon + gỡ hook\n"
            "                    (thêm --keep-hooks để chỉ tắt daemon mà giữ hook)\n"
            "  --hooks-only      CHỈ cài hook + rule, KHÔNG chạy daemon, KHÔNG tự khởi động\n"
            "                    (dùng khi daemon đang chạy ở máy khác — tránh xung đột token)\n"
            "  --install-agy     Tìm/cài Antigravity CLI (lệnh `agy`) để điều khiển AI từ Telegram\n"
            "  --start           Chạy daemon ngay (không đổi cấu hình tự khởi động)\n"
            "  --stop            Dừng daemon (không đổi cấu hình tự khởi động)\n"
            "  --status / -s     Xem trạng thái tự khởi động + daemon + hook"
        )
        return

    # --- Cài đặt đầy đủ ---
    cfg = ensure_config()
    ensure_executable(HOOK_SCRIPT)
    ensure_executable(DAEMON_SCRIPT)
    update_agy_hooks()
    update_claude_hooks()
    update_codex_hooks()
    append_rule_file(os.path.expanduser("~/.gemini/config/GEMINI.md"))
    append_rule_file(os.path.expanduser("~/.claude/CLAUDE.md"))
    append_rule_file(os.path.expanduser("~/.codex/AGENTS.md"))

    if IS_MAC:
        setup_macos_service()
    elif IS_WINDOWS:
        setup_windows_service()
        verify_daemon_alive()
    else:
        setup_linux_service()

    send_welcome_message(cfg)


if __name__ == "__main__":
    main()
