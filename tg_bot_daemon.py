#!/usr/bin/env python3
import sys
import os
import json
import time
import shutil
import subprocess
import urllib.request
import urllib.parse

IS_WINDOWS = sys.platform == "win32"

if not IS_WINDOWS:
    os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + os.environ.get("PATH", "")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE_DIR, "state")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
ACTIVE_PANE_FILE = os.path.join(STATE_DIR, "active_pane.txt")
AWAY_FLAG_FILE = os.path.join(STATE_DIR, "away_mode.flag")
FROM_TG_DISPATCH_FILE = os.path.join(STATE_DIR, "from_tg_dispatch.flag")
IDE_SESSIONS_FILE = os.path.join(STATE_DIR, "ide_sessions.json")

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


def list_ide_targets():
    if not os.path.exists(IDE_SESSIONS_FILE):
        return []
    out = []
    try:
        with open(IDE_SESSIONS_FILE, "r", encoding="utf-8") as f:
            sessions = json.load(f)
        for label, info in sessions.items():
            # Chỉ hiện các phiên IDE hoạt động trong 24h qua
            if time.time() - info.get("updated_at", 0) < 86400:
                out.append(f"{label} | IDE | {info.get('cwd', '')}")
    except Exception:
        pass
    return out


def list_targets():
    results = []
    if has_tmux():
        code, out, _ = run_tmux(["list-panes", "-a", "-F", "#S:#I.#P | #{pane_current_command} | #{pane_current_path}"])
        if code == 0 and out.strip():
            results.extend(out.strip().splitlines())
    elif IS_WINDOWS:
        results.extend(list_windows_terminals())
    results.extend(list_ide_targets())
    return results


def get_active_pane():
    panes = list_targets()
    available_ids = [p.split(" | ")[0] for p in panes]
    if os.path.exists(ACTIVE_PANE_FILE):
        with open(ACTIVE_PANE_FILE, "r", encoding="utf-8") as f:
            saved = f.read().strip()
        if saved in available_ids or not available_ids:
            return saved
    for p in panes:
        parts = p.split(" | ")
        if len(parts) >= 2 and any(ai in parts[1].lower() for ai in ("agy", "claude", "codex", "node", "windowsterminal", "ide")):
            return parts[0]
    return available_ids[0] if available_ids else ("0" if IS_WINDOWS and not has_tmux() else "0:0.0")


def set_active_pane(pane):
    with open(ACTIVE_PANE_FILE, "w", encoding="utf-8") as f:
        f.write(pane)


def set_away_mode(enabled):
    if enabled:
        with open(AWAY_FLAG_FILE, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    else:
        if os.path.exists(AWAY_FLAG_FILE):
            try:
                os.remove(AWAY_FLAG_FILE)
            except OSError:
                pass
        with open(os.path.join(STATE_DIR, "ide_release.flag"), "w", encoding="utf-8") as f:
            f.write("1")


def get_mode_status_text():
    from hook_notify import is_screen_locked
    if is_screen_locked():
        return "🔒 Away (Màn hình PC đang khóa)"
    if os.path.exists(AWAY_FLAG_FILE):
        return "🚶 Away (Đang bật chế độ vắng mặt)"
    return "💻 Here (Đang ngồi máy - chỉ báo khi nhắn từ Telegram hoặc gõ kèm tg)"


def capture_screen(pane):
    if has_tmux() and not pane.startswith("ide:"):
        code, out, err = run_tmux(["capture-pane", "-t", pane, "-p", "-S", "-40"])
        if code == 0:
            return out.strip()[-3500:] or "(Màn hình trống)"
        return f"Không đọc được pane {pane}: {err}"

    last_trans_file = os.path.join(STATE_DIR, "last_transcript.txt")
    if os.path.exists(last_trans_file):
        try:
            with open(last_trans_file, "r", encoding="utf-8") as f:
                tpath = f.read().strip()
            from hook_notify import extract_last_response
            ans = extract_last_response(tpath)
            if ans:
                return f"[Nội dung trả lời gần nhất]:\n\n{ans[-3400:]}"
        except Exception:
            pass
    return "Chưa có dữ liệu màn hình cho cửa sổ này."


def send_to_target(pane, text, mark_waiting=True, is_special_key=False):
    now_str = str(time.time())
    # Khi bạn chủ động nhắn từ Telegram -> Tự động đánh dấu lệnh từ Telegram
    with open(FROM_TG_DISPATCH_FILE, "w", encoding="utf-8") as f:
        f.write(now_str)

    if mark_waiting:
        safe_pane = sanitize_filename(pane)
        with open(os.path.join(STATE_DIR, f"waiting_{safe_pane}.flag"), "w", encoding="utf-8") as f:
            f.write(now_str)
        with open(os.path.join(STATE_DIR, "waiting_any.flag"), "w", encoding="utf-8") as f:
            f.write(now_str)

    # Nếu mục tiêu là một phiên IDE đang chờ phản hồi qua Hook
    if pane.startswith("ide:"):
        safe_pane = sanitize_filename(pane)
        with open(os.path.join(STATE_DIR, f"ide_inbox_{safe_pane}.txt"), "w", encoding="utf-8") as f:
            f.write(text)
        return

    if has_tmux():
        if is_special_key:
            run_tmux(["send-keys", "-t", pane, text])
            return
        _, mode_out, _ = run_tmux(["display-message", "-t", pane, "-p", "#{pane_in_mode}"])
        if mode_out.strip() == "1":
            run_tmux(["copy-mode", "-q", "-t", pane])
        run_tmux(["send-keys", "-t", pane, "-l", text])
        time.sleep(0.15)
        run_tmux(["send-keys", "-t", pane, "Enter"])
    elif IS_WINDOWS:
        send_to_windows_terminal(pane, text, is_special_key=is_special_key)


def register_bot_menu(token):
    commands = [
        {"command": "list", "description": "Danh sách cửa sổ AI (tmux / IDE) & chọn cửa sổ"},
        {"command": "screen", "description": "Chụp màn hình terminal / kết quả hiện tại"},
        {"command": "away", "description": "Bật chế độ Vắng mặt (Nhận mọi thông báo quan trọng)"},
        {"command": "here", "description": "Chuyển về chế độ Ngồi máy (Tắt báo tự động)"},
        {"command": "yes", "description": "Gửi phím y + Enter (Có xác nhận trước khi gửi)"},
        {"command": "no", "description": "Gửi phím n + Enter (Có xác nhận trước khi gửi)"},
        {"command": "enter", "description": "Gửi phím Enter (Có xác nhận trước khi gửi)"},
        {"command": "esc", "description": "Gửi phím Escape (Có xác nhận trước khi gửi)"},
        {"command": "ctrlc", "description": "Gửi Ctrl+C dừng lệnh (Có xác nhận trước khi gửi)"},
        {"command": "help", "description": "Xem hướng dẫn & trạng thái hiện tại"},
    ]
    try:
        tg_api(token, "setMyCommands", {"commands": json.dumps(commands)})
        tg_api(token, "setChatMenuButton", {"menu_button": json.dumps({"type": "commands"})})
    except Exception:
        pass


def ask_key_confirmation(token, chat_id, pane, key_code, key_label):
    tg_send(
        token,
        chat_id,
        f"⚠️ *Xác nhận thao tác*\nBạn có chắc chắn muốn gửi `{key_label}` vào cửa sổ `{pane}` không?",
        reply_markup={
            "inline_keyboard": [[
                {"text": f"✅ Đồng ý gửi {key_label}", "callback_data": f"confirmkey:{pane}:{key_code}"},
                {"text": "✖️ Hủy", "callback_data": "cancelkey"},
            ]]
        },
    )


def main():
    cfg = load_config()
    token = cfg["bot_token"]
    chat_id = str(cfg["chat_id"])
    offset = 0

    register_bot_menu(token)

    while True:
        try:
            res = tg_api(token, "getUpdates", {"offset": offset, "timeout": 25})
            for item in res.get("result", []):
                offset = item["update_id"] + 1

                # 1. Xử lý nút bấm Inline Keyboard
                if "callback_query" in item:
                    cb = item["callback_query"]
                    msg_obj = cb.get("message", {})
                    if str(msg_obj.get("chat", {}).get("id")) != chat_id:
                        continue
                    msg_id = msg_obj.get("message_id")
                    data = cb.get("data", "")

                    if data == "cancelkey":
                        tg_api(token, "answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Đã hủy thao tác"})
                        if msg_id:
                            tg_api(token, "editMessageText", {
                                "chat_id": chat_id,
                                "message_id": msg_id,
                                "text": "✖️ Đã hủy thao tác gửi phím."
                            })
                    elif data.startswith("key:"):
                        _, rest = data.split(":", 1)
                        pane, key = rest.rsplit(":", 1)
                        tg_api(token, "answerCallbackQuery", {"callback_query_id": cb["id"], "text": f"Xác nhận gửi {key}..."})
                        ask_key_confirmation(token, chat_id, pane, key, key)
                    elif data.startswith("confirmkey:"):
                        _, rest = data.split(":", 1)
                        pane, key = rest.rsplit(":", 1)
                        if key in ("Enter", "Escape", "C-c"):
                            send_to_target(pane, key, mark_waiting=False, is_special_key=True)
                        else:
                            send_to_target(pane, key, mark_waiting=True, is_special_key=False)
                        tg_api(token, "answerCallbackQuery", {"callback_query_id": cb["id"], "text": f"Đã gửi {key} vào {pane}"})
                        if msg_id:
                            tg_api(token, "editMessageText", {
                                "chat_id": chat_id,
                                "message_id": msg_id,
                                "text": f"✅ Đã gửi phím [{key}] vào [{pane}]."
                            })
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
                    mode_str = get_mode_status_text()
                    help_text = (
                        "🤖 *Điều khiển AI (agy / claude / codex) Đa Nền Tảng*\n\n"
                        f"🖥 Cửa sổ đang chọn: `{pane}`\n"
                        f"📡 Trạng thái: *{mode_str}*\n\n"
                        "• Nhắn tin bất kỳ: Gửi thẳng vào AI đang chạy và nhận câu trả lời qua Telegram.\n"
                        "• `/list` : Xem danh sách cửa sổ (tmux / IDE) & chọn nhanh.\n"
                        "• `/screen` : Xem màn hình terminal / câu trả lời hiện tại.\n"
                        "• `/away` : Bật chế độ Vắng mặt (khi bạn quên Lock màn hình PC).\n"
                        "• `/here` : Về chế độ Ngồi máy (khi gõ phím trên PC cũng tự động chuyển về `/here`).\n"
                        "• `/yes`, `/no`, `/enter`, `/esc`, `/ctrlc` : Gửi phím điều khiển (có hỏi xác nhận)."
                    )
                    tg_send(token, chat_id, help_text)

                elif cmd_word == "/away":
                    set_away_mode(True)
                    tg_send(token, chat_id, "🚶 Đã bật chế độ *Away (Vắng mặt)*. Mọi thông báo quan trọng từ PC sẽ được gửi về Telegram.\n*(Khi bạn ngồi gõ lại trên PC, chế độ này sẽ tự động tắt)*.")

                elif cmd_word == "/here":
                    set_away_mode(False)
                    tg_send(token, chat_id, "💻 Đã chuyển về chế độ *Here (Ngồi máy)*. Điện thoại sẽ im lặng trừ khi bạn nhắn từ Telegram hoặc gõ kèm `tg`.")

                elif cmd_word == "/list":
                    panes = list_targets()
                    mode_str = get_mode_status_text()
                    if not panes:
                        tg_send(token, chat_id, f"📡 {mode_str}\n⚠️ Hiện không tìm thấy cửa sổ tmux / IDE nào đang chạy.")
                    else:
                        body = "\n".join(f"• `{p}`" for p in panes)
                        buttons = []
                        for p in panes[:8]:
                            pid = p.split(" | ")[0]
                            cmd_name = p.split(" | ")[1] if " | " in p else ""
                            buttons.append([{"text": f"🎯 Chọn {pid} ({cmd_name})", "callback_data": f"use:{pid}"}])
                        tg_send(
                            token,
                            chat_id,
                            f"📋 *Danh sách cửa sổ AI*\n📡 {mode_str}\n🎯 Đang chọn: `{pane}`\n\n{body}",
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
                    # Khi bạn chủ động nhắn câu hỏi từ Telegram -> Tự bật Away và gửi vào cửa sổ AI
                    set_away_mode(True)
                    send_to_target(pane, text, mark_waiting=True, is_special_key=False)
                    tg_send(
                        token,
                        chat_id,
                        f"📨 Đã gửi vào `[{pane}]`. Đang chờ AI xử lý...",
                        reply_markup={
                            "inline_keyboard": [[
                                {"text": "📸 Xem tiến độ màn hình", "callback_data": f"screen:{pane}"},
                                {"text": "⏎ Enter", "callback_data": f"key:{pane}:Enter"}
                            ]]
                        },
                    )

        except Exception:
            time.sleep(2)


if __name__ == "__main__":
    main()
