#!/usr/bin/env python3
import sys
import os
import json
import time
import shutil
import plistlib
import subprocess
import urllib.request
import urllib.parse

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

if not IS_WINDOWS:
    os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + os.environ.get("PATH", "")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE_DIR, "state")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
ACTIVE_PANE_FILE = os.path.join(STATE_DIR, "active_pane.txt")
AWAY_FLAG_FILE = os.path.join(STATE_DIR, "away_mode.flag")
FROM_TG_DISPATCH_FILE = os.path.join(STATE_DIR, "from_tg_dispatch.flag")

os.makedirs(STATE_DIR, exist_ok=True)


def has_tmux():
    return shutil.which("tmux") is not None


def sanitize_filename(name):
    for ch in (":", ".", "/", "\\", " "):
        name = name.replace(ch, "_")
    return name


def run_tmux(args):
    """
    Luôn truyền stdin=DEVNULL, stdout=PIPE, stderr=PIPE và timeout=5.
    Trên macOS/Linux, tránh lỗi treo libevent khi fd 1 là file thường.
    """
    try:
        res = subprocess.run(
            ["tmux"] + args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            check=False,
        )
        return res.returncode, res.stdout, res.stderr
    except Exception as e:
        return 1, "", str(e)


def is_screen_locked():
    """Tự động phát hiện máy tính đang khóa màn hình."""
    try:
        if IS_MAC:
            out = subprocess.check_output(
                ["ioreg", "-n", "Root", "-d1", "-a"],
                stdin=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2,
            )
            data = plistlib.loads(out)
            root = data if isinstance(data, dict) else (data[0] if data else {})
            users = root.get("IOConsoleUsers", [])
            if users and isinstance(users[0], dict):
                return bool(users[0].get("CGSSessionScreenIsLocked", False))
        elif IS_WINDOWS:
            out = subprocess.check_output(
                ["tasklist", "/FI", "IMAGENAME eq LogonUI.exe"],
                stdin=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=3,
            )
            return "LogonUI.exe" in out
        else:
            user = os.environ.get("USER", "")
            if user:
                out = subprocess.check_output(
                    ["loginctl", "list-sessions", "--no-legend"],
                    stdin=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=2,
                )
                for line in out.splitlines():
                    parts = line.split()
                    if len(parts) >= 3 and parts[2] == user:
                        sid = parts[0]
                        prop = subprocess.check_output(
                            ["loginctl", "show-session", sid, "-p", "LockedHint"],
                            stdin=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            text=True,
                            timeout=2,
                        )
                        if "LockedHint=yes" in prop:
                            return True
    except Exception:
        pass
    return False


def get_mode_status_text():
    manual_away = os.path.exists(AWAY_FLAG_FILE)
    locked = is_screen_locked()
    if locked:
        return "🔒 *Away (Máy tính đang khóa màn hình — tự động báo Telegram)*"
    if manual_away:
        return "🚶‍♂️ *Away (Đang bật chế độ vắng mặt — tự động báo Telegram)*"
    return "💻 *Here (Đang ngồi máy — chỉ báo khi nhắn từ Telegram hoặc gõ kèm `tg`)*"


# ============================================================================
# HỖ TRỢ WINDOWS 10/11 NATIVE (Khi không dùng tmux / WSL)
# ============================================================================
def list_windows_terminals():
    ps_script = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and "
        "($_.ProcessName -match 'WindowsTerminal|powershell|pwsh|cmd|agy|claude|codex|mintty|ConEmu') } | "
        "ForEach-Object { \"$($_.Id) | $($_.ProcessName) | $($_.MainWindowTitle)\" }"
    )
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            timeout=6,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return [line.strip() for line in res.stdout.strip().splitlines() if line.strip()]
    except Exception:
        pass
    return []


def send_to_windows_terminal(pid_str, text, is_special_key=False):
    key_map = {
        "Enter": "{ENTER}",
        "Escape": "{ESC}",
        "C-c": "^c",
    }
    if is_special_key:
        send_seq = key_map.get(text, text)
        ps_script = (
            f"$ws = New-Object -ComObject WScript.Shell; "
            f"if ($ws.AppActivate({int(pid_str) if pid_str.isdigit() else 0})) {{ "
            f"Start-Sleep -Milliseconds 150; $ws.SendKeys('{send_seq}') }}"
        )
    else:
        escaped = text.replace("'", "''")
        ps_script = (
            f"Set-Clipboard -Value '{escaped}'; "
            f"$ws = New-Object -ComObject WScript.Shell; "
            f"if ($ws.AppActivate({int(pid_str) if pid_str.isdigit() else 0})) {{ "
            f"Start-Sleep -Milliseconds 200; "
            f"$ws.SendKeys('^v'); "
            f"Start-Sleep -Milliseconds 150; "
            f"$ws.SendKeys('{{ENTER}}') }}"
        )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=6,
            check=False,
        )
    except Exception:
        pass


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def tg_api(token, method, data):
    url = f"https://api.telegram.org/bot{token}/{method}"
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode("utf-8"))
    with urllib.request.urlopen(req, timeout=35) as res:
        return json.loads(res.read().decode("utf-8"))


def tg_send(token, chat_id, text, reply_markup=None):
    for use_md in (True, False):
        payload = {"chat_id": chat_id, "text": text[:3900]}
        if use_md:
            payload["parse_mode"] = "Markdown"
        if reply_markup:
            payload["reply_markup"] = json.dumps(reply_markup)
        try:
            return tg_api(token, "sendMessage", payload)
        except Exception:
            continue


def register_bot_menu(token):
    commands = [
        {"command": "list", "description": "📋 Danh sách cửa sổ tmux & chọn cửa sổ"},
        {"command": "screen", "description": "📸 Chụp màn hình cửa sổ hiện tại"},
        {"command": "away", "description": "🚶‍♂️ Bật chế độ Vắng mặt (tự động báo Telegram)"},
        {"command": "here", "description": "💻 Bật chế độ Ngồi máy (tắt tự động báo)"},
        {"command": "yes", "description": "✅ Gửi y + Enter (có xác nhận)"},
        {"command": "no", "description": "❌ Gửi n + Enter (có xác nhận)"},
        {"command": "enter", "description": "⏎ Gửi phím Enter (có xác nhận)"},
        {"command": "esc", "description": "⏹ Gửi phím Escape (có xác nhận)"},
        {"command": "ctrlc", "description": "🛑 Gửi phím Ctrl+C (có xác nhận)"},
        {"command": "help", "description": "ℹ️ Hướng dẫn sử dụng"},
    ]
    try:
        tg_api(token, "setMyCommands", {"commands": json.dumps(commands)})
        tg_api(token, "setChatMenuButton", {"menu_button": json.dumps({"type": "commands"})})
    except Exception:
        pass


def list_targets():
    if has_tmux():
        code, out, _ = run_tmux(["list-panes", "-a", "-F", "#S:#I.#P | #{pane_current_command} | #{pane_current_path}"])
        if code == 0 and out.strip():
            return out.strip().splitlines()
    if IS_WINDOWS:
        return list_windows_terminals()
    return []


def get_active_pane():
    targets = list_targets()
    valid_ids = [t.split(" | ")[0] for t in targets]
    if os.path.exists(ACTIVE_PANE_FILE):
        with open(ACTIVE_PANE_FILE, "r", encoding="utf-8") as f:
            saved = f.read().strip()
        if saved and (not valid_ids or saved in valid_ids):
            return saved
    for line in targets:
        if any(k in line.lower() for k in ("agy", "claude", "codex", "node", "python")):
            return line.split(" | ")[0]
    if valid_ids:
        return valid_ids[0]
    return "0:0.0"


def set_active_pane(pane):
    with open(ACTIVE_PANE_FILE, "w", encoding="utf-8") as f:
        f.write(pane.strip())


def capture_screen(pane):
    if has_tmux():
        code, out, _ = run_tmux(["capture-pane", "-t", pane, "-p"])
        if code == 0 and out.strip():
            lines = [line for line in out.splitlines() if line.strip()]
            return "\n".join(lines[-35:])[-3500:] or "(Màn hình trống)"

    safe_pane = sanitize_filename(pane)
    for candidate in (
        os.path.join(STATE_DIR, f"last_transcript_{safe_pane}.txt"),
        os.path.join(STATE_DIR, "last_transcript.txt"),
    ):
        if os.path.exists(candidate):
            try:
                with open(candidate, "r", encoding="utf-8") as f:
                    tpath = f.read().strip()
                if tpath and os.path.exists(tpath):
                    from hook_notify import extract_last_turn
                    last_u, last_a = extract_last_turn(tpath)
                    if last_u or last_a:
                        return f"👤 User: {last_u[-500:]}\n\n🤖 AI:\n{last_a[-2800:]}"
            except Exception:
                pass
    return "⚠️ Không thể chụp màn hình cửa sổ này (kiểm tra `/list`)."


def send_to_target(pane, text, mark_waiting=True, is_special_key=False):
    safe = sanitize_filename(pane)
    if mark_waiting:
        with open(os.path.join(STATE_DIR, f"waiting_{safe}.flag"), "w", encoding="utf-8") as f:
            f.write(str(time.time()))
        with open(FROM_TG_DISPATCH_FILE, "w", encoding="utf-8") as f:
            f.write(str(time.time()))

    if has_tmux():
        if is_special_key:
            if text in ("y", "n"):
                run_tmux(["send-keys", "-t", pane, "-l", text])
                time.sleep(0.15)
                run_tmux(["send-keys", "-t", pane, "Enter"])
            else:
                run_tmux(["send-keys", "-t", pane, text])
        else:
            clean = text.replace("\n", " ").strip()
            run_tmux(["send-keys", "-t", pane, "-l", clean])
            time.sleep(0.15)
            run_tmux(["send-keys", "-t", pane, "Enter"])
        return

    if IS_WINDOWS:
        send_to_windows_terminal(pane, text, is_special_key=is_special_key)


def ask_key_confirmation(token, chat_id, pane, key_code, key_label):
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": f"☑️ Xác nhận gửi: {key_label}", "callback_data": f"confirmkey:{pane}:{key_code}"},
                {"text": "✖️ Hủy", "callback_data": "cancel"},
            ]
        ]
    }
    tg_send(
        token,
        chat_id,
        f"⚠️ *Xác nhận thao tác*\nBạn có chắc chắn muốn gửi `{key_label}` vào cửa sổ `[{pane}]` không?",
        reply_markup=reply_markup,
    )


def main():
    cfg = load_config()
    token = cfg["bot_token"]
    chat_id = str(cfg["chat_id"])
    offset = 0

    register_bot_menu(token)

    try:
        res = tg_api(token, "getUpdates", {"timeout": 1})
        if res.get("ok") and res.get("result"):
            offset = res["result"][-1]["update_id"] + 1
    except Exception:
        pass

    while True:
        try:
            res = tg_api(token, "getUpdates", {"offset": offset, "timeout": 25})
            for item in res.get("result", []):
                offset = item["update_id"] + 1

                # 1. Xử lý khi bấm nút Inline Keyboard
                if "callback_query" in item:
                    cb = item["callback_query"]
                    if str(cb.get("from", {}).get("id")) != chat_id:
                        continue
                    data = cb.get("data", "")

                    if data == "cancel":
                        tg_api(token, "answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Đã hủy thao tác"})
                        tg_send(token, chat_id, "✖️ Đã hủy thao tác.")

                    elif data == "mode:away":
                        with open(AWAY_FLAG_FILE, "w", encoding="utf-8") as f:
                            f.write(str(time.time()))
                        tg_api(token, "answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Đã bật chế độ Away"})
                        tg_send(token, chat_id, "🚶‍♂️ Đã chuyển sang chế độ *Away (Vắng mặt)* — AI sẽ tự động gửi thông báo về Telegram.")

                    elif data == "mode:here":
                        if os.path.exists(AWAY_FLAG_FILE):
                            try:
                                os.remove(AWAY_FLAG_FILE)
                            except OSError:
                                pass
                        tg_api(token, "answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Đã bật chế độ Here"})
                        tg_send(token, chat_id, "💻 Đã chuyển sang chế độ *Here (Ngồi máy)* — chỉ gửi Telegram khi bạn nhắn từ Telegram hoặc gõ kèm `tg`.")

                    elif data.startswith("key:"):
                        _, rest = data.split(":", 1)
                        pane, k = rest.rsplit(":", 1)
                        label_map = {
                            "y": "Yes (y + Enter)",
                            "n": "No (n + Enter)",
                            "Enter": "Phím Enter",
                            "Escape": "Phím Escape",
                            "C-c": "Phím Ctrl+C",
                        }
                        tg_api(token, "answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Vui lòng xác nhận..."})
                        ask_key_confirmation(token, chat_id, pane, k, label_map.get(k, k))

                    elif data.startswith("confirmkey:"):
                        _, rest = data.split(":", 1)
                        pane, k = rest.rsplit(":", 1)
                        mark = k in ("y", "n")
                        send_to_target(pane, k, mark_waiting=mark, is_special_key=True)
                        tg_api(token, "answerCallbackQuery", {"callback_query_id": cb["id"], "text": f"Đã gửi {k} vào {pane}"})
                        tg_send(token, chat_id, f"⚡️ Đã xác nhận và gửi `{k}` vào `[{pane}]`")

                    elif data.startswith("screen:"):
                        _, pane = data.split(":", 1)
                        scr = capture_screen(pane)
                        tg_api(token, "answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Đang chụp màn hình..."})
                        tg_send(token, chat_id, f"📺 [{pane}]:\n\n{scr}")

                    elif data.startswith("use:"):
                        _, pane = data.split(":", 1)
                        set_active_pane(pane)
                        tg_api(token, "answerCallbackQuery", {"callback_query_id": cb["id"], "text": f"Đã chọn {pane}"})
                        tg_send(token, chat_id, f"✅ Đã chuyển điều khiển sang: `{pane}`")
                    continue

                # 2. Xử lý tin nhắn văn bản từ bạn
                msg = item.get("message", {})
                if str(msg.get("chat", {}).get("id")) != chat_id or "text" not in msg:
                    continue

                text = msg["text"].strip()
                cmd_word = text.split()[0].lower().split("@")[0] if text else ""
                pane = get_active_pane()

                if cmd_word in ("/start", "/help"):
                    help_text = (
                        "🤖 *Điều khiển AI (agy / claude / codex) qua tmux*\n\n"
                        f"📡 Trạng thái: {get_mode_status_text()}\n"
                        f"🖥 Cửa sổ đang chọn: `{pane}`\n\n"
                        "• Nhắn tin bất kỳ: Gửi thẳng vào cửa sổ AI đang chọn và nhận câu trả lời qua Telegram.\n"
                        "• `/list` : Xem danh sách cửa sổ AI & chọn nhanh.\n"
                        "• `/screen` : Xem màn hình hiện tại.\n"
                        "• `/away` : Bật chế độ Vắng mặt (tự động báo Telegram).\n"
                        "• `/here` : Bật chế độ Ngồi máy (tắt tự động báo).\n"
                        "• `/yes`, `/no`, `/enter`, `/esc`, `/ctrlc` : Gửi phím điều khiển (có hỏi xác nhận)."
                    )
                    tg_send(
                        token,
                        chat_id,
                        help_text,
                        reply_markup={
                            "inline_keyboard": [[
                                {"text": "🚶‍♂️ Bật Away", "callback_data": "mode:away"},
                                {"text": "💻 Bật Here", "callback_data": "mode:here"},
                            ]]
                        },
                    )

                elif cmd_word == "/away":
                    with open(AWAY_FLAG_FILE, "w", encoding="utf-8") as f:
                        f.write(str(time.time()))
                    tg_send(token, chat_id, "🚶‍♂️ Đã bật chế độ *Away (Vắng mặt)*.\nAI sẽ tự động gửi thông báo về Telegram khi hoàn thành task dài, cần quyết định `[TG_NOTIFY]`, hoặc gặp lỗi.")

                elif cmd_word == "/here":
                    if os.path.exists(AWAY_FLAG_FILE):
                        try:
                            os.remove(AWAY_FLAG_FILE)
                        except OSError:
                            pass
                    tg_send(token, chat_id, "💻 Đã chuyển về chế độ *Here (Ngồi máy)*.\nChỉ gửi Telegram khi bạn nhắn từ Telegram hoặc gõ kèm `tg` trên máy.")

                elif cmd_word == "/list":
                    panes = list_targets()
                    if not panes:
                        tg_send(token, chat_id, "⚠️ Hiện không tìm thấy cửa sổ tmux nào đang chạy.")
                    else:
                        body = "\n".join(f"• `{p}`" for p in panes)
                        buttons = []
                        for p in panes[:8]:
                            pid = p.split(" | ")[0]
                            cmd_name = p.split(" | ")[1] if " | " in p else ""
                            buttons.append([{"text": f"🎯 Chọn {pid} ({cmd_name})", "callback_data": f"use:{pid}"}])
                        buttons.append([
                            {"text": "🚶‍♂️ Chế độ Away", "callback_data": "mode:away"},
                            {"text": "💻 Chế độ Here", "callback_data": "mode:here"},
                        ])
                        tg_send(
                            token,
                            chat_id,
                            f"📋 *Danh sách cửa sổ AI*\n📡 {get_mode_status_text()}\n🎯 Đang chọn: `{pane}`\n\n{body}",
                            reply_markup={"inline_keyboard": buttons},
                        )

                elif cmd_word == "/use" and len(text.split()) > 1:
                    target = text.split(None, 1)[1].strip()
                    set_active_pane(target)
                    tg_send(token, chat_id, f"✅ Đã chuyển điều khiển sang: `{target}`")

                elif cmd_word in ("/screen", "/status"):
                    scr = capture_screen(pane)
                    tg_send(token, chat_id, f"📺 [{pane}]:\n\n{scr}")

                elif cmd_word == "/yes":
                    ask_key_confirmation(token, chat_id, pane, "y", "y + Enter")

                elif cmd_word == "/no":
                    ask_key_confirmation(token, chat_id, pane, "n", "n + Enter")

                elif cmd_word == "/enter":
                    ask_key_confirmation(token, chat_id, pane, "Enter", "Enter")

                elif cmd_word == "/esc":
                    ask_key_confirmation(token, chat_id, pane, "Escape", "Escape")

                elif cmd_word == "/ctrlc":
                    ask_key_confirmation(token, chat_id, pane, "C-c", "Ctrl+C")

                else:
                    send_to_target(pane, text, mark_waiting=True, is_special_key=False)
                    tg_send(
                        token,
                        chat_id,
                        f"📨 Đã gửi vào `[{pane}]`. Đang chờ AI xử lý...",
                        reply_markup={
                            "inline_keyboard": [[
                                {"text": "📸 Xem tiến độ màn hình", "callback_data": f"screen:{pane}"},
                                {"text": "⏎ Enter", "callback_data": f"key:{pane}:Enter"},
                            ]]
                        },
                    )

        except Exception:
            time.sleep(2)


if __name__ == "__main__":
    main()
