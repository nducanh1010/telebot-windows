#!/usr/bin/env python3
import sys
import os
import re
import json
import time
import shutil
import plistlib
import subprocess
import urllib.request
import urllib.parse

VERSION = "1.6.0"
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


def is_any_tmux_client_active(idle_threshold=300):
    if not has_tmux():
        return False
    code_rc, out, _ = run_tmux(["list-clients", "-F", "#{client_activity}"])
    if code_rc != 0 or not out.strip():
        return False
    now = time.time()
    for line in out.splitlines():
        line = line.strip()
        if line.isdigit() and (now - int(line)) < idle_threshold:
            return True
    return False


def is_screen_locked():
    """Tự động phát hiện màn hình vật lý của máy tính đang khóa."""
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
    try:
        cfg = load_config()
    except Exception:
        cfg = {}
    lock_cfg = "BẬT" if cfg.get("notify_on_screen_lock", True) else "TẮT"
    away_cfg = "BẬT" if cfg.get("notify_on_away", True) else "TẮT"
    ssh_suppress = "BẬT" if cfg.get("suppress_when_tmux_active", True) else "TẮT"
    flags_line = f"⚙️ Cờ cấu hình: `lock={lock_cfg}` | `away={away_cfg}` | `ssh_quiet={ssh_suppress}`"

    if cfg.get("suppress_when_tmux_active", True) and is_any_tmux_client_active():
        return f"📱 *Here (Đang thao tác trực tiếp trên tmux/SSH — tắt báo trùng)*\n{flags_line}"
    if cfg.get("notify_on_away", True) and os.path.exists(AWAY_FLAG_FILE):
        return f"🚶‍♂️ *Away (Đang bật /away — tự động báo Telegram)*\n{flags_line}"
    if cfg.get("notify_on_screen_lock", True) and is_screen_locked():
        return f"🔒 *Away (Màn hình PC đang khóa — tự động báo Telegram)*\n{flags_line}"
    return f"💻 *Here (Đang ngồi máy — chỉ báo khi nhắn từ Telegram hoặc gõ kèm `tg`)*\n{flags_line}"


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


def split_chunks(text, max_len=3600):
    text = text.strip()
    if len(text) <= max_len:
        return [text] if text else ["(Trống)"]
    chunks = []
    rem = text
    while rem:
        if len(rem) <= max_len:
            chunks.append(rem)
            break
        cut = rem.rfind("\n", 0, max_len)
        if cut < max_len // 2:
            cut = rem.rfind(" ", 0, max_len)
        if cut < max_len // 2:
            cut = max_len
        chunks.append(rem[:cut].rstrip())
        rem = rem[cut:].lstrip()
    if len(chunks) > 4:
        omitted = len(chunks) - 4
        chunks = (
            chunks[:2]
            + [f"... _(Đã lược bớt {omitted} đoạn giữa)_ ..."]
            + chunks[-2:]
        )
    return chunks


def tg_send(token, chat_id, text, reply_markup=None):
    chunks = split_chunks(text, max_len=3600)
    last_res = None
    for idx, chunk in enumerate(chunks, start=1):
        is_last = idx == len(chunks)
        for use_md in (True, False):
            payload = {"chat_id": chat_id, "text": chunk}
            if use_md:
                payload["parse_mode"] = "Markdown"
            if is_last and reply_markup:
                payload["reply_markup"] = json.dumps(reply_markup)
            try:
                last_res = tg_api(token, "sendMessage", payload)
                break
            except Exception:
                continue
    return last_res


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


REPLY_NEXT_FILE = os.path.join(STATE_DIR, "reply_next_pane.json")


def set_one_shot_reply_pane(pane):
    with open(REPLY_NEXT_FILE, "w", encoding="utf-8") as f:
        json.dump({"pane": pane.strip(), "ts": time.time()}, f, ensure_ascii=False)


def consume_one_shot_reply_pane(max_age=300):
    if not os.path.exists(REPLY_NEXT_FILE):
        return ""
    try:
        with open(REPLY_NEXT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        os.remove(REPLY_NEXT_FILE)
        if time.time() - float(data.get("ts", 0)) <= max_age:
            return data.get("pane", "").strip()
    except Exception:
        pass
    return ""


def extract_reply_target_pane(msg):
    """
    Khi người dùng vuốt Reply trực tiếp vào một tin nhắn của cửa sổ B trên Telegram (hoặc sau khi bấm nút [💬 Trả lời]),
    trích xuất mã cửa sổ B từ tiêu đề '🖥 [B]' / '📺 [B]' / '💬 [B]' để gửi đúng vào B mà không làm mất cửa sổ A đang chọn.
    """
    reply_msg = msg.get("reply_to_message")
    if isinstance(reply_msg, dict):
        replied_text = reply_msg.get("text") or ""
        m = re.search(r"(?:🖥|📺|💬)\s*\*?\[([^\]]+)\]", replied_text)
        if m:
            consume_one_shot_reply_pane()
            return m.group(1).strip()
    return consume_one_shot_reply_pane()


def clean_tmux_screen(raw_text):
    """Làm sạch các ký tự kẻ khung dài, khoảng trắng thừa và chi tiết TUI rườm rà khi chụp /screen gửi lên điện thoại."""
    cleaned_lines = []
    for line in raw_text.splitlines():
        s = line.strip()
        if not s:
            continue
        if re.match(r"^[─━═\-]{8,}$", s):
            if cleaned_lines and cleaned_lines[-1] != "──────────":
                cleaned_lines.append("──────────")
            continue
        if s in (">", "❯", "? for shortcuts") or s.startswith("└ Tip:"):
            continue
        # Rút gọn các vạch kẻ ngang dài bên trong dòng (ví dụ: '───── Conversation compacted ─────')
        s = re.sub(r"[─━═]{5,}", "───", s)
        # Bỏ '(ctrl+o to expand)'
        s = re.sub(r"\s*\(ctrl\+o to expand\)", "", s)
        # Rút gọn dòng Thought nội bộ của AI
        s = re.sub(r"^(○ Thought for [\d.]+s)\s*\(.*\)$", r"\1", s)
        # Rút gọn dòng log lệnh dài '● Ran (...)' / '● Scheduled 1 timer (...)'
        s = re.sub(r"^(● (?:Ran|Running|Scheduled[^\(]*|Scheduling[^\(]*)\()(.{55}).*\)$", r"\1\2...)", s)
        # Thu gọn khoảng trắng dài ở thanh status bar thành ' | '
        s = re.sub(r"\s{4,}", " | ", s)
        s = re.sub(r"^\? for shortcuts\s*\|\s*", "ℹ️ ", s)
        cleaned_lines.append(s)

    final_lines = []
    for l in cleaned_lines:
        if l == "──────────" and (not final_lines or final_lines[-1] == "──────────"):
            continue
        final_lines.append(l)
    while final_lines and final_lines[-1] == "──────────":
        final_lines.pop()
    return "\n".join(final_lines[-35:]) or "(Màn hình trống)"


def capture_screen(pane):
    if has_tmux() and not pane.startswith("term:"):
        code, out, _ = run_tmux(["capture-pane", "-t", pane, "-p"])
        if code == 0 and out.strip():
            return clean_tmux_screen(out)
        return f"⚠️ Cửa sổ `[{pane}]` không còn tồn tại hoặc đã đổi tên trong `tmux` (kiểm tra `/list`)."

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
                        return f"👤 User: {last_u[-600:]}\n\n🤖 AI:\n{last_a}"
            except Exception:
                pass
    return "⚠️ Không thể chụp màn hình cửa sổ này (kiểm tra `/list`)."


def format_pane_summary(pane_line):
    parts = [p.strip() for p in pane_line.split(" | ")]
    pid = parts[0] if parts else pane_line
    cmd_name = parts[1] if len(parts) > 1 else ""
    if re.match(r"^\d+\.\d+\.\d+$", cmd_name):
        cmd_name = f"claude ({cmd_name})"
    folder = os.path.basename(os.path.normpath(parts[2])) if len(parts) > 2 else ""
    label = f"{cmd_name} • {folder}" if (cmd_name and folder) else (cmd_name or folder)
    return pid, label



INTERACTIVE_PROMPT_RE = re.compile(
    r"(?:"
    r"Do you want to (?:proceed|allow|make this edit|run this)"
    r"|Allow (?:command|tool|mcp|execution)"
    r"|\([yY]/[nN]\)|\[[yY]/[nN]\]"
    r"|Enter to select.*(?:navigate|cancel)"
    r"|Use arrow keys to navigate"
    r")",
    re.IGNORECASE,
)


def detect_interactive_prompt(pid):
    """
    Kiểm tra xem cửa sổ tmux có đang dừng ở bảng hỏi lựa chọn (1/2/3, y/n, Permission prompt) hay không.
    Trả về (is_prompt: bool, snippet: str).
    """
    if not has_tmux() or pid.startswith("term:"):
        return False, ""
    code_rc, out, _ = run_tmux(["capture-pane", "-t", pid, "-p"])
    if code_rc != 0 or not out.strip():
        return False, ""
    non_empty = [l.rstrip() for l in out.splitlines() if l.strip()]
    bottom_12 = non_empty[-12:]
    bottom_text = "\n".join(bottom_12)

    # Kiểm tra mẫu câu hỏi quyền hoặc menu chọn 1. / 2. đang mở ở đáy màn hình
    has_menu_cursor = bool(
        re.search(r"^\s*(?:❯|>|›)\s*1\.\s+", bottom_text, re.MULTILINE)
        and re.search(r"^\s*2\.\s+", bottom_text, re.MULTILINE)
    )
    if has_menu_cursor or INTERACTIVE_PROMPT_RE.search(bottom_text):
        return True, "\n".join(bottom_12[-10:])
    return False, ""


def increment_waiting_count(safe_pane, is_busy=False):
    """Tăng bộ đếm số lượt đang chờ phản hồi Telegram trên cửa sổ này (để khi gửi lệnh nối tiếp vào queue không bị lượt trước xóa mất cờ)."""
    wfile = os.path.join(STATE_DIR, f"waiting_{safe_pane}.flag")
    count = 0
    if os.path.exists(wfile):
        try:
            with open(wfile, "r", encoding="utf-8") as f:
                val = f.read().strip()
            count = 0 if val.startswith("followup:") else (int(val) if val.isdigit() else 1)
        except Exception:
            count = 1
    elif is_busy:
        # Nếu lượt hiện tại khởi phát từ PC nhưng đang chạy dở và user gửi thêm lệnh từ Telegram vào queue
        count = 1
    count += 1
    with open(wfile, "w", encoding="utf-8") as f:
        f.write(str(count))


def get_pane_live_status(pid):
    """Đọc trạng thái thời gian thực của cửa sổ tmux (Sẵn sàng / Đang chạy / Hẹn giờ / Hết quota)."""
    if not has_tmux() or pid.startswith("term:"):
        return "💻 Terminal"
    safe = sanitize_filename(pid)
    alert_file = os.path.join(STATE_DIR, f"quota_alert_{safe}.json")
    if os.path.exists(alert_file):
        try:
            with open(alert_file, "r", encoding="utf-8") as f:
                d = json.load(f).get("detail", "")
            m_reset = re.search(r"resets\s+([^\n·]+)", d, re.IGNORECASE)
            if m_reset:
                return f"🚨 Hết quota (reset {m_reset.group(1).strip()})"
        except Exception:
            pass
        return "🚨 Hết quota"

    code_rc, out, _ = run_tmux(["capture-pane", "-t", pid, "-p"])
    if code_rc != 0 or not out.strip():
        return "💤 Sẵn sàng"

    non_empty = [l.rstrip() for l in out.splitlines() if l.strip()]
    last_div_idx = -1
    for i, line in enumerate(non_empty):
        s = line.strip()
        if s.startswith("────") and len(s) >= 15:
            last_div_idx = i
    footer = "\n".join(non_empty[last_div_idx + 1:] if last_div_idx != -1 else non_empty[-4:]).lower()
    bottom_8 = "\n".join(non_empty[-8:]).lower()

    if "usage limit reached" in footer:
        return "🚨 Hết quota"
    is_prompt, _ = detect_interactive_prompt(pid)
    if is_prompt:
        return "❓ Đang chờ chọn (1/2, y/n)"
    if "timer running" in bottom_8:
        return "⏰ Đang hẹn giờ (Timer)"
    if "esc to cancel" in footer or "esc to interrupt" in footer:
        return "⚙️ Đang thực thi..."
    if "1 monitor" in footer or "monitors" in footer:
        return "👀 Đang chạy Monitor"
    return "💤 Sẵn sàng"


def cleanup_orphan_state_files(max_age_seconds=43200):
    """Tự động dọn các file start_*.txt và prompt_tg_*.flag mồ côi cũ quá 12 giờ."""
    now = time.time()
    try:
        for fname in os.listdir(STATE_DIR):
            if fname.startswith(("start_", "prompt_tg_", "ack_pending_")):
                fpath = os.path.join(STATE_DIR, fname)
                ageLimit = 300 if fname.startswith("ack_pending_") else max_age_seconds
                if os.path.isfile(fpath) and (now - os.path.getmtime(fpath)) > ageLimit:
                    try:
                        os.remove(fpath)
                    except OSError:
                        pass
    except Exception:
        pass


def send_list_menu(token, chat_id, current_pane):
    panes = list_targets()
    if not panes:
        tg_send(token, chat_id, "⚠️ Hiện không tìm thấy cửa sổ tmux nào đang chạy.")
        return
    lines = []
    buttons = []
    for p in panes[:20]:
        pid, label = format_pane_summary(p)
        live_st = get_pane_live_status(pid)
        marker = "🟢" if pid == current_pane else "⚪️"
        lines.append(f"{marker} `{pid}` — `{label}`\n   └ {live_st}")
        buttons.append([{"text": f"🎯 Chọn {pid} ({label})", "callback_data": f"use:{pid}"}])
    buttons.append([
        {"text": "🚶‍♂️ Chế độ Away", "callback_data": "mode:away"},
        {"text": "💻 Chế độ Here", "callback_data": "mode:here"},
    ])
    body = "\n".join(lines)
    tg_send(
        token,
        chat_id,
        f"📋 *Danh sách cửa sổ AI (v{VERSION})*\n📡 {get_mode_status_text()}\n🎯 Đang chọn: `{current_pane}`\n\n{body}",
        reply_markup={"inline_keyboard": buttons},
    )


def send_to_target(pane, text, mark_waiting=True, is_special_key=False):
    safe = sanitize_filename(pane)
    if mark_waiting:
        now_ts = time.time()
        is_busy_now = get_pane_live_status(pane).startswith("⚙️ Đang thực thi")
        increment_waiting_count(safe, is_busy=is_busy_now)
        with open(FROM_TG_DISPATCH_FILE, "w", encoding="utf-8") as f:
            f.write(str(now_ts))
        with open(os.path.join(STATE_DIR, f"from_tg_dispatch_{safe}.flag"), "w", encoding="utf-8") as f:
            f.write(str(now_ts))
        if not is_special_key and not is_busy_now:
            ack_file = os.path.join(STATE_DIR, f"ack_pending_{safe}.json")
            with open(ack_file, "w", encoding="utf-8") as f:
                json.dump({"pane": pane, "text": text, "ts": now_ts}, f, ensure_ascii=False)

    if has_tmux():
        # Tự động thoát copy-mode (nếu đang cuộn màn hình trong tmux) để con trỏ quay về ô nhập
        run_tmux(["copy-mode", "-q", "-t", pane])
        if is_special_key:
            if text in ("y", "n", "1", "2", "3", "4"):
                run_tmux(["send-keys", "-t", pane, "-l", text])
                time.sleep(0.2)
                run_tmux(["send-keys", "-t", pane, "Enter"])
            else:
                run_tmux(["send-keys", "-t", pane, text])
        else:
            clean = text.replace("\n", " ").strip()
            run_tmux(["send-keys", "-t", pane, "-l", clean])
            time.sleep(0.25)
            run_tmux(["send-keys", "-t", pane, "Enter"])
        return

    if IS_WINDOWS:
        send_to_windows_terminal(pane, text, is_special_key=is_special_key)



QUOTA_LIMIT_RE = re.compile(
    r"(?:"
    r"⚠\s*Usage limit reached[^\n]*"
    r"|Usage limit reached\s*·\s*limit resets[^\n]*"
    r"|Claude(?:\s+AI)?\s+usage limit reached[^\n]*"
    r"|You've hit your(?: usage)? limit[^\n]*"
    r"|exceeded your current quota[^\n]*"
    r"|Quota exceeded for quota metric[^\n]*"
    r"|ResourceExhausted:.*quota[^\n]*"
    r")",
    re.IGNORECASE,
)


def check_tmux_quota_limits(token, chat_id):
    """
    Quét định kỳ phần đáy của các cửa sổ tmux để:
    1. Phát hiện ngay khi cửa sổ AI chạm giới hạn Quota (Usage limit reached) và báo về Telegram (1 lần duy nhất).
    2. Phát hiện khi cửa sổ đó hết giới hạn (Quota reset) và tự động chạy lại để báo tin vui về Telegram.
    """
    if not has_tmux():
        return
    targets = list_targets()
    active_default = get_active_pane()

    for pane_line in targets:
        pid, label = format_pane_summary(pane_line)
        safe = sanitize_filename(pid)
        alert_file = os.path.join(STATE_DIR, f"quota_alert_{safe}.json")

        code_rc, out, _ = run_tmux(["capture-pane", "-t", pid, "-p"])
        if code_rc != 0 or not out.strip():
            continue

        non_empty = [line.rstrip() for line in out.splitlines() if line.strip()]
        # Chỉ lấy vùng thanh trạng thái (status footer) nằm BÊN DƯỚI đường kẻ ngang cuối cùng của ô nhập ('────────')
        # để tuyệt đối không bắt nhầm nội dung tin nhắn chat của AI/User phía trên.
        last_div_idx = -1
        for i, line in enumerate(non_empty):
            s = line.strip()
            if s.startswith("────") and len(s) >= 15:
                last_div_idx = i
        raw_footer = non_empty[last_div_idx + 1:] if last_div_idx != -1 else non_empty[-4:]
        tail_lines = [
            l.strip() for l in raw_footer
            if "`" not in l and not l.strip().startswith((">", "•", "-", "*"))
        ]
        tail_text = "\n".join(tail_lines)

        m = QUOTA_LIMIT_RE.search(tail_text)
        if m:
            matched_line = m.group(0).strip()
            detail_lines = [matched_line]
            for idx, l in enumerate(tail_lines):
                if matched_line in l and idx + 1 < len(tail_lines):
                    next_l = tail_lines[idx + 1]
                    if any(k in next_l.lower() for k in ("continuing", "reset", "upgrade", "wait", "cancel")):
                        detail_lines.append(next_l)
                    break
            detail_str = "\n".join(detail_lines)

            if not os.path.exists(alert_file):
                try:
                    with open(alert_file, "w", encoding="utf-8") as f:
                        json.dump(
                            {"pane": pid, "label": label, "detail": detail_str, "ts": time.time()},
                            f,
                            ensure_ascii=False,
                        )
                except Exception:
                    pass

                note = ""
                if active_default and active_default != pid:
                    note = f"\n\n📌 _Đang chọn mặc định: `{active_default}` — Hãy **Reply** tin nhắn này hoặc bấm **🎯 Chọn {pid}** nếu muốn thao tác với `{pid}`._"

                msg = (
                    f"🖥 *[{pid}]*\n\n"
                    f"🚨 *CẢNH BÁO HẾT QUOTA / USAGE LIMIT!*\n"
                    f"📂 Cửa sổ: `{pid}` (`{label}`)\n"
                    f"⚠️ Trạng thái trên màn hình:\n`{detail_str}`\n\n"
                    f"⏳ _Hệ thống sẽ tự động báo lại ngay khi cửa sổ `{pid}` được reset quota và chạy tiếp._"
                    f"{note}"
                )
                buttons = [[
                    {"text": f"🎯 Chọn {pid}", "callback_data": f"use:{pid}"},
                    {"text": "📸 Xem màn hình", "callback_data": f"screen:{pid}"},
                    {"text": "📋 Đổi cửa sổ", "callback_data": "list"},
                ]]
                tg_send(token, chat_id, msg, reply_markup={"inline_keyboard": buttons})
        else:
            # Nếu trước đó đang bị đánh dấu hết quota mà nay dòng cảnh báo đã biến mất -> Đã reset quota & chạy lại!
            if os.path.exists(alert_file):
                prev_info = {}
                try:
                    with open(alert_file, "r", encoding="utf-8") as f:
                        prev_info = json.load(f)
                    os.remove(alert_file)
                except Exception:
                    pass

                prev_detail = prev_info.get("detail", "Usage limit reached")
                note = ""
                if active_default and active_default != pid:
                    note = f"\n\n📌 _Đang chọn mặc định: `{active_default}` — Hãy **Reply** tin nhắn này hoặc bấm **🎯 Chọn {pid}** nếu muốn thao tác với `{pid}`._"

                msg = (
                    f"🖥 *[{pid}]*\n\n"
                    f"🟢 *ĐÃ RESET QUOTA & CHẠY LẠI!*\n"
                    f"📂 Cửa sổ: `{pid}` (`{label}`)\n"
                    f"✅ Trạng thái giới hạn trước đó (`{prev_detail.splitlines()[0]}`) đã kết thúc — AI tại cửa sổ này đã hoạt động trở lại!"
                    f"{note}"
                )
                buttons = [[
                    {"text": f"🎯 Chọn {pid}", "callback_data": f"use:{pid}"},
                    {"text": "📸 Xem màn hình", "callback_data": f"screen:{pid}"},
                    {"text": "📋 Đổi cửa sổ", "callback_data": "list"},
                ]]
                tg_send(token, chat_id, msg, reply_markup={"inline_keyboard": buttons})



def build_interactive_prompt_buttons(pid):
    return {
        "inline_keyboard": [
            [
                {"text": "1️⃣ Chọn 1", "callback_data": f"confirmkey:{pid}:1"},
                {"text": "2️⃣ Chọn 2", "callback_data": f"confirmkey:{pid}:2"},
                {"text": "3️⃣ Chọn 3", "callback_data": f"confirmkey:{pid}:3"},
            ],
            [
                {"text": "✅ Yes (y)", "callback_data": f"confirmkey:{pid}:y"},
                {"text": "❌ No (n)", "callback_data": f"confirmkey:{pid}:n"},
                {"text": "⏎ Enter", "callback_data": f"confirmkey:{pid}:Enter"},
                {"text": "⏹ Esc", "callback_data": f"confirmkey:{pid}:Escape"},
            ],
            [
                {"text": "📸 Xem màn hình", "callback_data": f"screen:{pid}"},
                {"text": "📋 Đổi cửa sổ", "callback_data": "list"},
            ],
        ]
    }


def check_tmux_prompts_and_queues(token, chat_id):
    """
    1. Phát hiện nếu cửa sổ tmux đang dừng ở bảng hỏi (1/2/3, y/n, Permission prompt) để báo về Telegram kèm nút chọn nhanh.
    2. Tự động xả Hàng đợi (Queue) cho cửa sổ ngay khi cửa sổ chuyển sang trạng thái '💤 Sẵn sàng'.
    """
    if not has_tmux():
        return
    targets = list_targets()
    active_default = get_active_pane()
    is_away = os.path.exists(AWAY_FLAG_FILE) or is_screen_locked()

    for pane_line in targets:
        pid, label = format_pane_summary(pane_line)
        safe = sanitize_filename(pid)
        prompt_alert_file = os.path.join(STATE_DIR, f"prompt_alert_{safe}.json")
        waiting_flag = os.path.join(STATE_DIR, f"waiting_{safe}.flag")

        is_prompt, snippet = detect_interactive_prompt(pid)
        if is_prompt:
            if (is_away or os.path.exists(waiting_flag)) and not os.path.exists(prompt_alert_file):
                try:
                    with open(prompt_alert_file, "w", encoding="utf-8") as f:
                        json.dump({"pane": pid, "snippet": snippet, "ts": time.time()}, f, ensure_ascii=False)
                except Exception:
                    pass
                note = ""
                if active_default and active_default != pid:
                    note = f"\n\n📌 _Đang chọn mặc định: `{active_default}` — Bạn có thể bấm trực tiếp nút bên dưới hoặc **Reply** tin nhắn này._"
                msg = (
                    f"🖥 *[{pid}]*\n\n"
                    f"❓ *AI ĐANG CHỜ BẠN CHỌN / XÁC NHẬN (`1/2/3` hoặc `y/n`)!*\n"
                    f"📂 Cửa sổ: `{pid}` (`{label}`)\n\n"
                    f"📺 *Nội dung hộp thoại:*\n{snippet}"
                    f"{note}"
                )
                tg_send(token, chat_id, msg, reply_markup=build_interactive_prompt_buttons(pid))
            continue
        else:
            if os.path.exists(prompt_alert_file):
                try:
                    os.remove(prompt_alert_file)
                except OSError:
                    pass




def verify_ai_received_command(token, chat_id, pane, text):
    """
    Chờ Hook 'start' của AI xác nhận đã nhận lệnh trước khi thực thi.
    - Nếu Hook nhận được lệnh: Hook tự gửi '✅ OK — Đã nhận lệnh tại cửa sổ [pane]' và xóa file ack_pending.
    - Nếu sau 3s chưa thấy Hook phản hồi: Thử gửi bồi thêm 1 phím Enter (phòng TUI bị trễ khi paste).
    - Nếu sau 5s vẫn không có phản hồi từ Hook: Báo cảnh báo ngay kèm màn hình hiện tại (do gửi nhầm tmux hoặc con trỏ không ở ô nhập).
    """
    safe = sanitize_filename(pane)
    ack_file = os.path.join(STATE_DIR, f"ack_pending_{safe}.json")

    for _ in range(12):  # 3.0 giây đầu
        time.sleep(0.25)
        if not os.path.exists(ack_file):
            return True

    # Thử gửi bồi 1 phím Enter nếu chữ đã vào ô nhập nhưng chưa submit
    if has_tmux():
        run_tmux(["send-keys", "-t", pane, "Enter"])

    for _ in range(8):  # Thêm 2.0 giây (tổng 5.0 giây)
        time.sleep(0.25)
        if not os.path.exists(ack_file):
            return True

    # Quá 5 giây mà Hook start của AI không chạy -> Con trỏ không ở ô nhập hoặc nhầm cửa sổ tmux!
    try:
        os.remove(ack_file)
    except OSError:
        pass

    scr = capture_screen(pane)
    scr_lines = [line for line in scr.splitlines() if line.strip()]
    scr_tail = "\n".join(scr_lines[-16:]) if scr_lines else "(Trống)"

    warn_msg = (
        f"⚠️ *CẢNH BÁO: AI tại `[{pane}]` CHƯA nhận được lệnh!*\n"
        f"Đã gửi `{text[:80]}` vào `tmux` nhưng sau 5 giây Hook của AI không phản hồi.\n\n"
        "👉 *Nguyên nhân có thể:*\n"
        "1️⃣ Con trỏ trong `tmux` đang **không ở ô nhập** (đang mở hộp thoại, menu, hoặc AI đang bận chạy tác vụ khác).\n"
        "2️⃣ Hoặc đang chọn **nhầm cửa sổ `tmux`**.\n\n"
        f"📺 *Màn hình hiện tại của `[{pane}]`:*\n{scr_tail}"
    )
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "⏎ Gửi thêm Enter", "callback_data": f"confirmkey:{pane}:Enter"},
                {"text": "⏹ Gửi phím Esc", "callback_data": f"confirmkey:{pane}:Escape"},
            ],
            [
                {"text": "📸 Xem màn hình", "callback_data": f"screen:{pane}"},
                {"text": "📋 Đổi cửa sổ khác", "callback_data": "list"},
            ],
        ]
    }
    tg_send(token, chat_id, warn_msg, reply_markup=reply_markup)
    return False


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
            cleanup_orphan_state_files()
            check_tmux_quota_limits(token, chat_id)
            check_tmux_prompts_and_queues(token, chat_id)
            res = tg_api(token, "getUpdates", {"offset": offset, "timeout": 15})
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
                        mark = k in ("y", "n", "1", "2", "3", "4", "Enter")
                        send_to_target(pane, k, mark_waiting=mark, is_special_key=True)
                        tg_api(token, "answerCallbackQuery", {"callback_query_id": cb["id"], "text": f"Đã gửi {k} vào {pane}"})
                        tg_send(token, chat_id, f"✅ OK — Đã gửi lựa chọn `{k}` vào `[{pane}]`, AI đang tiếp tục thực thi!")



                    elif data.startswith("screen:"):
                        _, pane = data.split(":", 1)
                        scr = capture_screen(pane)
                        tg_api(token, "answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Đang chụp màn hình..."})
                        tg_send(token, chat_id, f"📺 *[{pane}]*:\n\n{scr}")

                    elif data == "list":
                        tg_api(token, "answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Danh sách cửa sổ"})
                        send_list_menu(token, chat_id, get_active_pane())

                    elif data.startswith("reply:"):
                        _, pane = data.split(":", 1)
                        valid_ids = [t.split(" | ")[0] for t in list_targets()]
                        if valid_ids and pane not in valid_ids:
                            tg_api(token, "answerCallbackQuery", {"callback_query_id": cb["id"], "text": f"Cửa sổ {pane} không còn tồn tại"})
                            tg_send(
                                token,
                                chat_id,
                                f"⚠️ Cửa sổ `[{pane}]` không còn tồn tại (đã đóng hoặc đổi tên trong `tmux`).\n👉 Dưới đây là danh sách cửa sổ đang chạy:",
                            )
                            send_list_menu(token, chat_id, get_active_pane())
                            continue
                        set_one_shot_reply_pane(pane)
                        tg_api(token, "answerCallbackQuery", {"callback_query_id": cb["id"], "text": f"Trả lời vào {pane}"})
                        tg_send(
                            token,
                            chat_id,
                            f"💬 *[{pane}]* — Nhập nội dung bạn muốn gửi vào `{pane}`:",
                            reply_markup={
                                "force_reply": True,
                                "selective": True,
                                "input_field_placeholder": f"Gửi lệnh vào {pane}...",
                            },
                        )

                    elif data.startswith("use:"):
                        _, pane = data.split(":", 1)
                        consume_one_shot_reply_pane()
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
                default_pane = get_active_pane()
                reply_pane = extract_reply_target_pane(msg)
                pane = reply_pane if reply_pane else default_pane

                if reply_pane and reply_pane.startswith("term:"):
                    tg_send(
                        token,
                        chat_id,
                        f"⚠️ Cửa sổ `[{reply_pane}]` là Terminal thường chạy ngoài `tmux` nên không nhận được phím từ Telegram.\n👉 Cửa sổ đang chọn mặc định của bạn vẫn là `[{default_pane}]`.",
                    )
                    continue

                valid_ids = [t.split(" | ")[0] for t in list_targets()]
                if reply_pane and valid_ids and reply_pane not in valid_ids:
                    tg_send(
                        token,
                        chat_id,
                        f"⚠️ Cửa sổ `[{reply_pane}]` trong tin nhắn cũ không còn tồn tại (đã đóng hoặc đổi tên trong `tmux`).\n👉 Cửa sổ đang chọn mặc định của bạn là `[{default_pane}]`.",
                    )
                    send_list_menu(token, chat_id, default_pane)
                    continue

                if cmd_word in ("/start", "/help"):
                    help_text = (
                        f"🤖 *Điều khiển AI (agy / claude / codex) qua tmux (v{VERSION})*\n\n"
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
                    send_list_menu(token, chat_id, pane)

                elif cmd_word == "/use" and len(text.split()) > 1:
                    target = text.split(None, 1)[1].strip()
                    set_active_pane(target)
                    tg_send(token, chat_id, f"✅ Đã chuyển điều khiển sang: `{target}`")

                elif cmd_word in ("/screen", "/status"):
                    scr = capture_screen(pane)
                    tg_send(token, chat_id, f"📺 *[{pane}]*:\n\n{scr}")

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
                    is_prompt, snippet = detect_interactive_prompt(pane)
                    short_choice_map = {
                        "1": "1", "2": "2", "3": "3", "4": "4",
                        "y": "y", "yes": "y", "ok": "y",
                        "n": "n", "no": "n",
                    }
                    # Case A: Màn hình đang ở bảng hỏi (1/2/3 hoặc y/n)
                    if is_prompt:
                        choice = short_choice_map.get(text.lower())
                        if choice:
                            send_to_target(pane, choice, mark_waiting=True, is_special_key=True)
                            tg_send(
                                token,
                                chat_id,
                                f"✅ *OK — Đã gửi lựa chọn `{choice}` vào bảng hỏi của `[{pane}]`!*\n⏳ AI đang tiếp tục thực thi...",
                            )
                        else:
                            tg_send(
                                token,
                                chat_id,
                                f"⚠️ *Cửa sổ `[{pane}]` đang dừng ở bảng hỏi lựa chọn (`1/2/3` hoặc `y/n`)!*\n"
                                f"👉 Hãy chọn phương án bên dưới để giải quyết bảng hỏi trước khi nhập câu lệnh mới:\n\n"
                                f"📺 *Hộp thoại hiện tại:*\n{snippet}",
                                reply_markup=build_interactive_prompt_buttons(pane),
                            )
                        continue

                    # Case B: Cửa sổ đang bận thực thi lệnh trước -> Bơm vào queue tự nhiên của AI và pong báo "đang chờ xử lý"
                    live_st = get_pane_live_status(pane)
                    if live_st.startswith("⚙️ Đang thực thi"):
                        send_to_target(pane, text, mark_waiting=True, is_special_key=False)
                        tg_send(
                            token,
                            chat_id,
                            f"🖥 *[{pane}]*\n\n"
                            f"⏳ *OK — Đã nhận lệnh tại `[{pane}]` (Lệnh này đang chờ xử lý)*\n"
                            f"⚙️ AI hiện đang thực thi lệnh trước, lệnh mới của bạn (`{text[:150]}`) đã vào hàng đợi (queue) và sẽ tự động thực thi ngay khi lệnh hiện tại hoàn tất.",
                            reply_markup={
                                "inline_keyboard": [[
                                    {"text": "💬 Trả lời", "callback_data": f"reply:{pane}"},
                                    {"text": "📸 Xem màn hình", "callback_data": f"screen:{pane}"},
                                    {"text": "📋 Đổi cửa sổ", "callback_data": "list"},
                                ]]
                            },
                        )
                        continue

                    # Case C: Cửa sổ đang sẵn sàng nhận lệnh
                    send_to_target(pane, text, mark_waiting=True, is_special_key=False)
                    verify_ai_received_command(token, chat_id, pane, text)

        except Exception:
            time.sleep(2)


if __name__ == "__main__":
    main()
