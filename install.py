#!/usr/bin/env python3
import sys
import os
import json
import stat
import shutil
import subprocess
import urllib.request
import urllib.parse

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HOOK_SCRIPT = os.path.join(BASE_DIR, "hook_notify.py")
DAEMON_SCRIPT = os.path.join(BASE_DIR, "tg_bot_daemon.py")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

# Lệnh gọi hook đa nền tảng (Windows dùng đường dẫn python hiện tại, Unix dùng python3)
if IS_WINDOWS:
    PY_EXEC = f'"{sys.executable}"'
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
    add_claude_hook("Stop", f"{HOOK_CMD_PREFIX} stop", 600, "AI Telegram Bridge (Stop)")

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
    print(f"[OK] Đã cấu hình Hook cho Codex CLI: {hooks_path}")


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

    startup_dir = os.path.join(
        os.environ.get("APPDATA", os.path.expanduser("~\\AppData\\Roaming")),
        "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
    )
    os.makedirs(startup_dir, exist_ok=True)
    vbs_path = os.path.join(startup_dir, "ai-telegram-daemon.vbs")

    vbs_content = (
        'Set WshShell = CreateObject("WScript.Shell")\n'
        f'WshShell.Run """{pythonw}"" ""{DAEMON_SCRIPT}""", 0, False\n'
    )
    with open(vbs_path, "w", encoding="utf-8") as f:
        f.write(vbs_content)

    # Dừng bản cũ nếu đang chạy và khởi chạy bản mới ngay lập tức
    subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*tg_bot_daemon.py*' } | Invoke-CimMethod -MethodName Terminate"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.Popen(
        [pythonw, DAEMON_SCRIPT],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )
    print(f"[OK] Đã cài đặt tự khởi động cùng Windows 10/11 và bật chạy ngầm: {vbs_path}")


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
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as res:
            out = json.loads(res.read().decode("utf-8"))
            if out.get("ok"):
                print("[OK] Đã gửi tin nhắn kiểm tra thành công tới Telegram!")
    except Exception as e:
        print(f"[WARN] Không gửi được tin nhắn test Telegram: {e}")


if __name__ == "__main__":
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
    else:
        setup_linux_service()

    send_welcome_message(cfg)
