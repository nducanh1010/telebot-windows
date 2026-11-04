#!/usr/bin/env python3
import sys
import os
import re
import json
import time
import shutil
import plistlib
import platform
import subprocess
import urllib.request
import urllib.parse

VERSION = "1.5.1"
IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

if not IS_WINDOWS:
    os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + os.environ.get("PATH", "")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE_DIR, "state")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
AWAY_FLAG_FILE = os.path.join(STATE_DIR, "away_mode.flag")
FROM_TG_DISPATCH_FILE = os.path.join(STATE_DIR, "from_tg_dispatch.flag")
ACTIVE_PANE_FILE = os.path.join(STATE_DIR, "active_pane.txt")

NOTIFY_TAG = "[TG_" + "NOTIFY]"

os.makedirs(STATE_DIR, exist_ok=True)


def has_tmux():
    return shutil.which("tmux") is not None


def is_in_tmux_session():
    return bool(os.environ.get("TMUX_PANE")) and has_tmux()


def run_tmux(args):
    """Luôn dùng PIPE cho stdout/stderr để tránh lỗi treo libevent của tmux trên macOS/Linux."""
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
        return res.stdout.strip()
    except Exception:
        return ""


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def is_tmux_session_actively_watched(pane_id, idle_threshold=300):
    """
    Kiểm tra xem người dùng có đang trực tiếp mở và gõ trên phiên tmux này (qua Terminal hoặc SSH)
    trong vòng idle_threshold giây (mặc định 5 phút) hay không.
    Lưu ý: tmux send-keys từ Bot KHÔNG làm tăng client_activity, chỉ thao tác bàn phím/chuột thật mới làm tăng.
    """
    if not has_tmux() or not pane_id or pane_id.startswith("term:"):
        return False
    session_name = pane_id.split(":")[0]
    out = run_tmux(["list-clients", "-t", session_name, "-F", "#{client_activity}"])
    if not out:
        return False
    now = time.time()
    for line in out.splitlines():
        line = line.strip()
        if line.isdigit():
            if (now - int(line)) < idle_threshold:
                return True
    return False


def is_away_mode(cfg=None, pane_id=""):
    """
    Xác định có gửi thông báo tự động (Away) hay không dựa trên 3 cờ cấu hình trong config.json:
    - suppress_when_tmux_active (mặc định True): Nếu đang trực tiếp gõ/xem phiên tmux (kể cả qua SSH điện thoại), không báo trùng lên Telegram.
    - notify_on_away (mặc định True): Báo Telegram khi bật /away.
    - notify_on_screen_lock (mặc định True): Báo Telegram khi màn hình vật lý của PC đang khóa.
    """
    if cfg is None:
        try:
            cfg = load_config()
        except Exception:
            cfg = {}
    if cfg.get("suppress_when_tmux_active", True) and pane_id and is_tmux_session_actively_watched(pane_id):
        return False
    if cfg.get("notify_on_away", True) and os.path.exists(AWAY_FLAG_FILE):
        return True
    if cfg.get("notify_on_screen_lock", True) and is_screen_locked():
        return True
    return False


def is_screen_locked(pane_id=""):
    """Phát hiện màn hình vật lý của PC đang khóa (macOS / Windows / Ubuntu)."""
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


def get_session_label(payload):
    """
    Lấy mã phiên tmux (#S:#I.#P):
    1. Ưu tiên đọc từ biến môi trường TMUX_PANE.
    2. Nếu server AI khởi động lại mất biến TMUX_PANE, dò trong tmux list-panes theo thư mục cwd và tiến trình AI.
    """
    if is_in_tmux_session():
        pane = os.environ.get("TMUX_PANE", "")
        out = run_tmux(["display-message", "-t", pane, "-p", "#S:#I.#P"])
        if out:
            return out
    workspaces = payload.get("workspacePaths") or []
    cwd = os.path.normpath(workspaces[0] if workspaces else (payload.get("cwd") or os.getcwd()))
    if has_tmux():
        out = run_tmux(["list-panes", "-a", "-F", "#S:#I.#P | #{pane_current_command} | #{pane_current_path}"])
        for line in out.splitlines():
            parts = [p.strip() for p in line.split(" | ")]
            if len(parts) >= 3 and os.path.normpath(parts[2]) == cwd:
                cmd_l = parts[1].lower()
                if any(k in cmd_l for k in ("agy", "claude", "codex")) or re.match(r"^\d+\.\d+\.\d+$", cmd_l):
                    return parts[0]
    folder = os.path.basename(cwd) or "terminal"
    if IS_WINDOWS and not has_tmux():
        return f"{platform.node()}:{folder}"
    return f"term:{folder}"


def split_message_chunks(text, max_len=3500):
    """
    P3: Chia thông minh tin nhắn dài theo dòng để không bao giờ bị cắt mất phần kết luận cuối câu.
    Nếu quá dài (> 4 phần), giữ 2 phần đầu + 2 phần cuối.
    """
    text = text.strip()
    if len(text) <= max_len:
        return [text] if text else ["(Trống)"]

    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, max_len)
        if cut < max_len // 2:
            cut = remaining.rfind(" ", 0, max_len)
        if cut < max_len // 2:
            cut = max_len
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()

    if len(chunks) > 4:
        omitted = len(chunks) - 4
        chunks = (
            chunks[:2]
            + [f"... _(Đã lược bớt {omitted} đoạn giữa để giữ trọn phần đầu và phần kết luận cuối)_ ..."]
            + chunks[-2:]
        )
    return chunks


def get_context_meta(payload):
    """Lấy tên công cụ AI (agy/claude/codex) và tên thư mục dự án của cửa sổ hiện tại."""
    workspaces = payload.get("workspacePaths") or []
    cwd = workspaces[0] if workspaces else (payload.get("cwd") or os.getcwd())
    folder = os.path.basename(os.path.normpath(cwd)) or "workspace"
    tpath = (payload.get("transcriptPath") or payload.get("transcript_path") or "").lower()
    if payload.get("workspacePaths") or payload.get("transcriptPath") or "antigravity" in tpath:
        tool = "agy"
    elif ".codex" in tpath:
        tool = "codex"
    elif ".claude" in tpath:
        tool = "claude"
    else:
        tool = "ai"
    return tool, folder


def build_inline_buttons(pane_id, mode="stop"):
    """
    P2: Hiển thị nút bấm theo đúng ngữ cảnh (kèm nút [💬 Trả lời] gửi về đúng cửa sổ của tin nhắn đó):
    - mode == 'ack': Khi Hook xác nhận đã nhận lệnh từ Telegram trước khi thực thi.
    - mode == 'ask': Hiện đầy đủ Yes / No / Enter + Trả lời / Chọn cửa sổ / Xem màn hình.
    - mode == 'stop': Hiện Trả lời / Chọn cửa sổ / Xem màn hình / Đổi cửa sổ.
    """
    is_tmux_pane = not pane_id.startswith("term:")
    if mode == "ack":
        if is_tmux_pane:
            return {
                "inline_keyboard": [
                    [
                        {"text": "💬 Trả lời", "callback_data": f"reply:{pane_id}"},
                        {"text": "📸 Xem màn hình", "callback_data": f"screen:{pane_id}"},
                    ],
                    [
                        {"text": "⏹ Hủy lệnh (Esc)", "callback_data": f"key:{pane_id}:Escape"},
                        {"text": "📋 Đổi cửa sổ", "callback_data": "list"},
                    ],
                ]
            }
        return {
            "inline_keyboard": [[
                {"text": "📸 Xem màn hình", "callback_data": f"screen:{pane_id}"},
                {"text": "📋 Đổi cửa sổ", "callback_data": "list"},
            ]]
        }

    if mode == "ask":
        rows = [
            [
                {"text": "✅ Yes (y)", "callback_data": f"key:{pane_id}:y"},
                {"text": "❌ No (n)", "callback_data": f"key:{pane_id}:n"},
                {"text": "⏎ Enter", "callback_data": f"key:{pane_id}:Enter"},
            ],
        ]
        if is_tmux_pane:
            rows.append([
                {"text": "💬 Trả lời", "callback_data": f"reply:{pane_id}"},
                {"text": f"🎯 Chọn {pane_id}", "callback_data": f"use:{pane_id}"},
            ])
        rows.append([
            {"text": "📸 Xem màn hình", "callback_data": f"screen:{pane_id}"},
            {"text": "📋 Đổi cửa sổ", "callback_data": "list"},
        ])
        return {"inline_keyboard": rows}

    # mode == "stop"
    rows = []
    if is_tmux_pane:
        rows.append([
            {"text": "💬 Trả lời", "callback_data": f"reply:{pane_id}"},
            {"text": f"🎯 Chọn {pane_id}", "callback_data": f"use:{pane_id}"},
        ])
    rows.append([
        {"text": "📸 Xem màn hình", "callback_data": f"screen:{pane_id}"},
        {"text": "📋 Đổi cửa sổ", "callback_data": "list"},
    ])
    return {"inline_keyboard": rows}


def send_telegram(text, pane_id, mode="stop"):
    cfg = load_config()
    url = f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage"
    active_now = get_saved_active_pane()
    if active_now and active_now != pane_id and mode in ("stop", "ask"):
        text = (
            f"{text}\n\n"
            f"📌 _Đang chọn mặc định: `{active_now}` — Hãy **Reply** tin nhắn này hoặc bấm **🎯 Chọn {pane_id}** nếu muốn gửi lệnh vào `{pane_id}`._"
        )
    chunks = split_message_chunks(text, max_len=3500)
    total = len(chunks)
    reply_markup = build_inline_buttons(pane_id, mode=mode)

    for idx, chunk in enumerate(chunks, start=1):
        is_last = idx == total
        part_tag = f" ({idx}/{total})" if total > 1 else ""
        for use_md in (True, False):
            header = f"🖥 *[{pane_id}]{part_tag}*" if use_md else f"🖥 [{pane_id}]{part_tag}"
            payload = {
                "chat_id": cfg["chat_id"],
                "text": f"{header}\n\n{chunk}",
            }
            if is_last:
                payload["reply_markup"] = json.dumps(reply_markup)
            if use_md:
                payload["parse_mode"] = "Markdown"
            try:
                req = urllib.request.Request(url, data=urllib.parse.urlencode(payload).encode("utf-8"))
                urllib.request.urlopen(req, timeout=12)
                break
            except Exception as e:
                if not use_md:
                    # Fallback cuối cùng: bỏ reply_markup nếu có lỗi format button và ghi log
                    try:
                        payload.pop("reply_markup", None)
                        req2 = urllib.request.Request(url, data=urllib.parse.urlencode(payload).encode("utf-8"))
                        urllib.request.urlopen(req2, timeout=12)
                    except Exception as e2:
                        try:
                            with open(os.path.join(BASE_DIR, "daemon.err.log"), "a", encoding="utf-8") as ef:
                                ef.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] send_telegram error ({pane_id}): {e} | {e2}\n")
                        except Exception:
                            pass
                continue


def extract_user_request_tag(raw_text):
    if not raw_text:
        return ""
    m = re.search(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", raw_text, re.DOTALL)
    if m:
        return m.group(1).strip()
    return raw_text.strip()


def extract_last_turn(transcript_path):
    if not transcript_path:
        return "", ""
    if transcript_path.endswith("transcript.jsonl"):
        full_candidate = transcript_path[:-len("transcript.jsonl")] + "transcript_full.jsonl"
        if os.path.exists(full_candidate):
            transcript_path = full_candidate

    if not os.path.exists(transcript_path):
        return "", ""

    last_user = ""
    last_text = ""
    turn_responses = []
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                obj = json.loads(line)
                step_type = obj.get("type")
                if step_type == "USER_INPUT" and obj.get("content"):
                    last_user = extract_user_request_tag(obj["content"])
                    turn_responses = []
                elif step_type == "SYSTEM_MESSAGE":
                    turn_responses = []
                elif step_type == "PLANNER_RESPONSE" and obj.get("content"):
                    c_text = obj["content"].strip()
                    if c_text:
                        turn_responses.append(c_text)
                        last_text = c_text
                elif obj.get("type") == "user" and isinstance(obj.get("message"), dict):
                    content = obj["message"].get("content", "")
                    if isinstance(content, str) and content.strip():
                        last_user = content.strip()
                    elif isinstance(content, list):
                        u_texts = [
                            b.get("text", "")
                            for b in content
                            if isinstance(b, dict) and b.get("type") == "text"
                        ]
                        if u_texts:
                            last_user = "\n".join(u_texts).strip()
                elif obj.get("type") == "assistant" and isinstance(obj.get("message"), dict):
                    texts = []
                    for block in obj["message"].get("content", []):
                        if isinstance(block, dict) and block.get("type") == "text":
                            texts.append(block.get("text", ""))
                    if texts:
                        last_text = "\n".join(texts)
                elif obj.get("type") == "event_msg" and isinstance(obj.get("payload"), dict):
                    p = obj["payload"]
                    if p.get("type") == "task_complete" and p.get("last_agent_message"):
                        last_text = p["last_agent_message"]
                    elif p.get("type") == "user_message" and p.get("message"):
                        last_user = str(p["message"]).strip()
                elif obj.get("type") == "response_item" and isinstance(obj.get("payload"), dict):
                    p = obj["payload"]
                    if p.get("role") == "user":
                        u_texts = [
                            b.get("text", "")
                            for b in p.get("content", [])
                            if isinstance(b, dict) and b.get("type") in ("input_text", "text")
                        ]
                        if u_texts:
                            last_user = "\n".join(u_texts).strip()
                    elif p.get("role") == "assistant":
                        texts = [
                            b.get("text", "")
                            for b in p.get("content", [])
                            if isinstance(b, dict) and b.get("type") in ("output_text", "text")
                        ]
                        if texts:
                            last_text = "\n".join(texts)
    except Exception:
        pass
    if turn_responses:
        any_tagged = any(has_notify_tag(r) for r in turn_responses)
        if len(turn_responses) == 1:
            last_text = turn_responses[0]
        else:
            # Nếu trong cùng 1 lượt có bước chính (dài hoặc có [TG_NOTIFY]) rồi bước cuối đặt timer ngắn, gộp lại để không mất nội dung và nhãn
            cleaned_parts = [r.replace(NOTIFY_TAG, "").strip() for r in turn_responses if r.replace(NOTIFY_TAG, "").strip()]
            merged = "\n\n".join(cleaned_parts)
            last_text = f"{NOTIFY_TAG}\n{merged}" if any_tagged else merged
    return last_user.strip(), last_text.strip()


TG_USER_PROMPT_RE = re.compile(
    r"^(?:\(tg\)|\[tg\]|tg(?:\s*[:,-]|\s+))|(?:\s+tg|\s*[\(\[]tg[\)\]]|\s*[:,-]\s*tg)$",
    re.IGNORECASE,
)


def user_prompt_requests_telegram(user_text):
    if not user_text:
        return False
    cleaned = extract_user_request_tag(user_text).strip()
    return bool(TG_USER_PROMPT_RE.search(cleaned))


def has_notify_tag(text):
    return text.lstrip().startswith(NOTIFY_TAG)


ASKING_USER_RE = re.compile(
    r"(\?\s*$|bạn chọn|bạn muốn|phương án nào|hướng nào|xác nhận giúp|bạn có muốn|bạn đồng ý|choose|which option|do you want)",
    re.IGNORECASE | re.MULTILINE,
)


def is_really_asking_decision(text):
    """
    Phân biệt rõ giữa:
    - Báo cáo tổng kết / hoàn thành cột mốc (có [TG_NOTIFY] nhưng không hỏi user chốt phương án).
    - Thực sự cần user quyết định / trả lời câu hỏi ở phần kết.
    """
    clean = text.replace(NOTIFY_TAG, "").strip()
    if not clean:
        return False
    tail = clean[-400:]
    if "?" in tail or ASKING_USER_RE.search(tail):
        return True
    return False


def sanitize_filename(name):
    for ch in (":", ".", "/", "\\", " "):
        name = name.replace(ch, "_")
    return name


def get_saved_active_pane():
    if os.path.exists(ACTIVE_PANE_FILE):
        try:
            with open(ACTIVE_PANE_FILE, "r", encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            pass
    return ""


def sync_active_pane_if_tmux(pane_id):
    """
    Chỉ khởi tạo active_pane.txt nếu chưa có cửa sổ nào được chọn.
    Khi người dùng đã chọn cửa sổ A, tuyệt đối KHÔNG tự động ghi đè sang B khi B bắn thông báo.
    """
    if is_in_tmux_session() and not pane_id.startswith("term:"):
        if not get_saved_active_pane():
            try:
                with open(ACTIVE_PANE_FILE, "w", encoding="utf-8") as f:
                    f.write(pane_id.strip())
            except Exception:
                pass


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else "stop"
    raw_in = sys.stdin.read().strip()
    try:
        payload = json.loads(raw_in) if raw_in else {}
    except Exception:
        payload = {}

    conv_id = sanitize_filename(payload.get("conversationId") or payload.get("session_id") or "default")
    pane_id = get_session_label(payload)
    safe_pane = sanitize_filename(pane_id)
    timer_file = os.path.join(STATE_DIR, f"start_{conv_id}.txt")
    prompt_tg_flag = os.path.join(STATE_DIR, f"prompt_tg_{conv_id}.flag")
    tg_flag_file = os.path.join(STATE_DIR, f"waiting_{safe_pane}.flag")

    transcript_path = payload.get("transcriptPath") or payload.get("transcript_path") or ""
    if transcript_path:
        try:
            with open(os.path.join(STATE_DIR, f"last_transcript_{safe_pane}.txt"), "w", encoding="utf-8") as f:
                f.write(transcript_path)
            with open(os.path.join(STATE_DIR, "last_transcript.txt"), "w", encoding="utf-8") as f:
                f.write(transcript_path)
        except Exception:
            pass

    user_input_payload = payload.get("lastUserInput") or payload.get("prompt") or ""

    pane_dispatch_file = os.path.join(STATE_DIR, f"from_tg_dispatch_{safe_pane}.flag")

    # 1. Sự kiện bắt đầu lượt chạy (PreInvocation / UserPromptSubmit)
    if event == "start":
        # Trên Antigravity (agy), PreInvocation chạy lại ở MỌI bước gọi tool (invocationNum = 1, 2, 3...) trong cùng 1 lượt.
        # Kiểm tra xem đây là bước đầu tiên của lượt mới hay chỉ là bước gọi tool tiếp theo giữa lượt.
        inv_num = payload.get("invocationNum", 1)
        is_continuation_step = (isinstance(inv_num, int) and inv_num > 1) or os.path.exists(timer_file)

        if not os.path.exists(timer_file):
            with open(timer_file, "w", encoding="utf-8") as f:
                f.write(str(time.time()))

        ack_pending_file = os.path.join(STATE_DIR, f"ack_pending_{safe_pane}.json")
        ack_data = None
        if os.path.exists(ack_pending_file):
            try:
                with open(ack_pending_file, "r", encoding="utf-8") as f:
                    ack_data = json.load(f)
                os.remove(ack_pending_file)
            except Exception:
                ack_data = {}

        dispatched_from_tg = (ack_data is not None) or os.path.exists(tg_flag_file)
        for dfile in (pane_dispatch_file, FROM_TG_DISPATCH_FILE):
            if os.path.exists(dfile):
                try:
                    with open(dfile, "r", encoding="utf-8") as f:
                        ts = float(f.read().strip())
                    if time.time() - ts < 30.0:
                        dispatched_from_tg = True
                    os.remove(dfile)
                except Exception:
                    pass

        # CHỈ tắt away_mode khi đây là LƯỢT MỚI (không phải bước tool giữa lượt) VÀ do người dùng gõ trực tiếp trên PC
        # TUYỆT ĐỐI KHÔNG BAO GIỜ xóa tg_flag_file (waiting_*.flag) tại start hook!
        if not is_continuation_step and not dispatched_from_tg:
            if os.path.exists(AWAY_FLAG_FILE):
                try:
                    os.remove(AWAY_FLAG_FILE)
                except OSError:
                    pass

        if user_prompt_requests_telegram(user_input_payload):
            with open(prompt_tg_flag, "w", encoding="utf-8") as f:
                f.write("1")

        # Nếu nhận được lệnh từ Telegram, Hook trả lời ngay OK + tên cửa sổ trước khi AI thực thi
        if ack_data is not None:
            tool_name, folder_name = get_context_meta(payload)
            cmd_preview = (
                ack_data.get("text")
                or extract_user_request_tag(user_input_payload)
                or "(Lệnh điều khiển)"
            ).strip()
            if len(cmd_preview) > 200:
                cmd_preview = cmd_preview[:200] + "..."
            send_telegram(
                f"✅ *OK — Đã nhận lệnh tại cửa sổ `{pane_id}`*\n"
                f"📂 Ngữ cảnh: `{tool_name}` • `{folder_name}`\n"
                f"📝 Nội dung: `{cmd_preview}`\n"
                f"⏳ AI đang bắt đầu thực thi...",
                pane_id,
                mode="ack",
            )

        print("{}")
        return

    # 2. Khi AI gọi tool hỏi user hoặc xin quyền (PreToolUse / Notification)
    if event == "ask":
        notif_type = (payload.get("notification_type") or "").lower()
        raw_msg = payload.get("message") or ""
        if notif_type == "idle_prompt" or "waiting for your input" in raw_msg.lower():
            print(json.dumps({"decision": "allow"}))
            return

        is_away = is_away_mode( None, pane_id)
        from_tg = os.path.exists(tg_flag_file) or os.path.exists(prompt_tg_flag)
        if is_away or from_tg:
            sync_active_pane_if_tmux(pane_id)
            tool_call = payload.get("toolCall", {})
            msg = raw_msg or json.dumps(tool_call.get("args", {}), ensure_ascii=False, indent=2)
            send_telegram(f"❓ *AI cần bạn xác nhận / trả lời:*\n{msg}", pane_id, mode="ask")
        print(json.dumps({"decision": "allow"}))
        return

    # 3. Khi AI kết thúc lượt (Stop)
    cfg = load_config()
    duration = 0.0
    if os.path.exists(timer_file):
        try:
            with open(timer_file, "r", encoding="utf-8") as f:
                duration = time.time() - float(f.read().strip())
            os.remove(timer_file)
        except Exception:
            pass

    last_user_from_transcript, last_msg_from_transcript = extract_last_turn(transcript_path)
    last_msg = (
        payload.get("last_assistant_message")
        or payload.get("last-assistant-message")
        or last_msg_from_transcript
    )
    last_user_text = user_input_payload or last_user_from_transcript
    term_reason = payload.get("terminationReason", "")

    explicit_tg_prompt = (
        os.path.exists(prompt_tg_flag)
        or user_prompt_requests_telegram(last_user_text)
    )
    if os.path.exists(prompt_tg_flag):
        try:
            os.remove(prompt_tg_flag)
        except OSError:
            pass

    ack_pending_file = os.path.join(STATE_DIR, f"ack_pending_{safe_pane}.json")
    fully_idle = payload.get("fullyIdle", True)
    has_valid_tg_flag = False

    if os.path.exists(tg_flag_file):
        try:
            with open(tg_flag_file, "r", encoding="utf-8") as f:
                raw_cnt = f.read().strip()
            if raw_cnt.startswith("followup:"):
                fts = float(raw_cnt.split(":", 1)[1])
                if time.time() - fts < 120.0:
                    has_valid_tg_flag = True
                if not os.path.exists(ack_pending_file):
                    os.remove(tg_flag_file)
            else:
                has_valid_tg_flag = True
                cnt = int(raw_cnt) if raw_cnt.isdigit() else 1
                if not os.path.exists(ack_pending_file):
                    if cnt > 1:
                        with open(tg_flag_file, "w", encoding="utf-8") as f:
                            f.write(str(cnt - 1))
                    elif fully_idle is False:
                        # Lệnh chạy nền (WaitMsBeforeAsync) chưa xong hẳn -> Giữ followup 120s để gửi nốt kết quả cuối khi task nền xong
                        with open(tg_flag_file, "w", encoding="utf-8") as f:
                            f.write(f"followup:{int(time.time())}")
                    else:
                        os.remove(tg_flag_file)
        except OSError:
            pass

    from_telegram = has_valid_tg_flag or explicit_tg_prompt

    is_away = is_away_mode( None, pane_id)
    tagged_notify = has_notify_tag(last_msg)
    needs_decision = tagged_notify and is_really_asking_decision(last_msg)
    is_error = (event == "stop_failure") or (term_reason in ("ERROR", "MAX_INVOCATIONS", "MAX_TOKEN_BUDGET_EXCEEDED"))
    is_long_task = duration >= cfg.get("min_duration_seconds", 180)

    # Khi câu trả lời đến từ Telegram, HOẶC có gắn nhãn [TG_NOTIFY] trên tmux, HOẶC bị lỗi dừng -> LUÔN LUÔN gửi về Telegram!
    # Chế độ is_away chỉ dùng để tự động báo thêm các task thường chạy lâu (>= min_duration_seconds).
    should_notify = from_telegram or tagged_notify or is_error or (is_away and is_long_task)

    if should_notify:
        sync_active_pane_if_tmux(pane_id)
        clean_msg = last_msg.replace(NOTIFY_TAG, "").strip() or "(Đã hoàn thành thao tác)"
        if from_telegram:
            prefix = "💬 *Trả lời truy vấn của bạn:*"
        elif needs_decision:
            prefix = "🙋‍♂️ *Cần chốt phương án / nghiệp vụ:*"
        elif tagged_notify:
            prefix = f"📋 *Báo cáo tổng kết ({int(duration)}s):*" if duration >= 5 else "📋 *Báo cáo tổng kết:*"
        elif is_error:
            prefix = f"⚠️ *Cảnh báo dừng ({term_reason}):*"
        else:
            prefix = f"✅ *Hoàn thành task ({int(duration)}s):*"
        send_telegram(f"{prefix}\n\n{clean_msg}", pane_id, mode="ask" if needs_decision else "stop")

    print("{}")


if __name__ == "__main__":
    main()
