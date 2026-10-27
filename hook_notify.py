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

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

if not IS_WINDOWS:
    os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + os.environ.get("PATH", "")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE_DIR, "state")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
AWAY_FLAG_FILE = os.path.join(STATE_DIR, "away_mode.flag")
FROM_TG_DISPATCH_FILE = os.path.join(STATE_DIR, "from_tg_dispatch.flag")
IDE_SESSIONS_FILE = os.path.join(STATE_DIR, "ide_sessions.json")

NOTIFY_TAG = "[TG_" + "NOTIFY]"

os.makedirs(STATE_DIR, exist_ok=True)


def has_tmux():
    return shutil.which("tmux") is not None


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


def is_screen_locked():
    """Tự động phát hiện máy tính đang khóa màn hình (macOS / Windows / Ubuntu)."""
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
            # Ubuntu / Linux (systemd-logind)
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


def is_in_tmux_session():
    return bool(os.environ.get("TMUX_PANE")) and has_tmux()


def get_session_label(payload):
    if is_in_tmux_session():
        pane = os.environ.get("TMUX_PANE", "")
        out = run_tmux(["display-message", "-t", pane, "-p", "#S:#I.#P"])
        if out:
            return out
    workspaces = payload.get("workspacePaths") or []
    cwd = workspaces[0] if workspaces else os.getcwd()
    folder = os.path.basename(os.path.normpath(cwd)) or "workspace"
    tpath = (payload.get("transcriptPath") or payload.get("transcript_path") or "").lower()
    if "antigravity-ide" in tpath or ("antigravity" in tpath and "cli" not in tpath):
        surface = "agy-ide"
    elif ".claude" in tpath:
        surface = "claude"
    elif ".codex" in tpath:
        surface = "codex"
    else:
        surface = "ide"
    return f"ide:{surface}:{folder}"


def register_ide_session(conv_id, label, payload):
    if is_in_tmux_session():
        return
    sessions = {}
    if os.path.exists(IDE_SESSIONS_FILE):
        try:
            with open(IDE_SESSIONS_FILE, "r", encoding="utf-8") as f:
                sessions = json.load(f)
        except Exception:
            sessions = {}
    workspaces = payload.get("workspacePaths") or []
    cwd = workspaces[0] if workspaces else os.getcwd()
    sessions[label] = {
        "conv_id": conv_id,
        "label": label,
        "cwd": cwd,
        "updated_at": time.time(),
    }
    # Giữ tối đa 10 phiên IDE gần nhất
    sorted_items = sorted(sessions.items(), key=lambda x: x[1].get("updated_at", 0), reverse=True)[:10]
    try:
        with open(IDE_SESSIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(dict(sorted_items), f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def send_telegram(text, pane_id):
    cfg = load_config()
    url = f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage"
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "✅ Yes (y)", "callback_data": f"key:{pane_id}:y"},
                {"text": "❌ No (n)", "callback_data": f"key:{pane_id}:n"},
                {"text": "⏎ Enter", "callback_data": f"key:{pane_id}:Enter"},
                {"text": "📸 Màn hình", "callback_data": f"screen:{pane_id}"},
            ]
        ]
    }
    for use_md in (True, False):
        payload = {
            "chat_id": cfg["chat_id"],
            "text": f"🖥 *[{pane_id}]*\n\n{text[:3800]}" if use_md else f"🖥 [{pane_id}]\n\n{text[:3800]}",
            "reply_markup": json.dumps(reply_markup),
        }
        if use_md:
            payload["parse_mode"] = "Markdown"
        try:
            req = urllib.request.Request(url, data=urllib.parse.urlencode(payload).encode("utf-8"))
            urllib.request.urlopen(req, timeout=10)
            return
        except Exception:
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
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                obj = json.loads(line)
                # Định dạng của Antigravity (agy)
                if obj.get("type") == "USER_INPUT" and obj.get("content"):
                    last_user = extract_user_request_tag(obj["content"])
                elif obj.get("type") == "PLANNER_RESPONSE" and obj.get("content"):
                    last_text = obj["content"]
                # Định dạng của Claude Code (claude)
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
                # Định dạng của OpenAI Codex CLI (codex)
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
    return last_user.strip(), last_text.strip()


def extract_last_response(transcript_path):
    _, last_text = extract_last_turn(transcript_path)
    return last_text


# Nhận diện câu lệnh người dùng bắt đầu hoặc kết thúc bằng "tg"
# Ví dụ: "tg kiểm tra...", "tg:...", "(tg) ...", "... tg", "... (tg)", "... [tg]"
TG_USER_PROMPT_RE = re.compile(
    r"^(?:\(tg\)|\[tg\]|tg(?:\s*[:,-]|\s+))|(?:\s+tg|\s*[\(\[]tg[\)\]]|\s*[:,-]\s*tg)$",
    re.IGNORECASE,
)


def user_prompt_requests_telegram(user_text):
    if not user_text:
        return False
    cleaned = extract_user_request_tag(user_text).strip()
    return bool(TG_USER_PROMPT_RE.search(cleaned))


def looks_like_human_decision_needed(text):
    # Chỉ nhận diện khi nhãn nằm ở ngay đầu câu trả lời (tránh bắt nhầm khi thảo luận về nhãn ở giữa câu)
    return text.lstrip().startswith(NOTIFY_TAG)


def sanitize_filename(name):
    for ch in (":", ".", "/", "\\", " "):
        name = name.replace(ch, "_")
    return name


def wait_for_ide_telegram_reply(safe_pane, transcript_path, timeout_sec=300):
    """
    Khi chạy trên IDE (ngoài tmux) ở chế độ Remote/Away, chờ phản hồi từ hộp thư IDE
    nếu người dùng nhắn lại qua Telegram.
    """
    inbox_file = os.path.join(STATE_DIR, f"ide_inbox_{safe_pane}.txt")
    release_file = os.path.join(STATE_DIR, "ide_release.flag")
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        # Nếu người dùng đã mở khóa màn hình và tắt away mode -> nhả IDE ngay
        if not os.path.exists(AWAY_FLAG_FILE) and not is_screen_locked():
            break
        if os.path.exists(release_file):
            try:
                os.remove(release_file)
            except OSError:
                pass
            break
        if os.path.exists(inbox_file):
            try:
                with open(inbox_file, "r", encoding="utf-8") as f:
                    reply = f.read().strip()
                os.remove(inbox_file)
                if reply:
                    is_claude_or_codex = ".claude" in transcript_path.lower() or ".codex" in transcript_path.lower()
                    if is_claude_or_codex:
                        return {"decision": "block", "reason": f"User replied via Telegram: {reply}"}
                    return {"decision": "continue", "reason": f"User replied via Telegram: {reply}"}
            except Exception:
                break
        time.sleep(1.5)
    return {}


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
    any_tg_flag_file = os.path.join(STATE_DIR, "waiting_any.flag")

    transcript_path = payload.get("transcriptPath") or payload.get("transcript_path") or ""
    if transcript_path:
        try:
            with open(os.path.join(STATE_DIR, "last_transcript.txt"), "w", encoding="utf-8") as f:
                f.write(transcript_path)
        except Exception:
            pass

    register_ide_session(conv_id, pane_id, payload)
    user_input_payload = payload.get("lastUserInput") or payload.get("prompt") or ""

    # 1. Sự kiện bắt đầu lượt chạy (PreInvocation / UserPromptSubmit)
    if event == "start":
        with open(timer_file, "w", encoding="utf-8") as f:
            f.write(str(time.time()))

        # Kiểm tra xem lệnh này vừa được bắn từ Telegram hay do bạn gõ trực tiếp trên PC
        dispatched_from_tg = False
        if os.path.exists(FROM_TG_DISPATCH_FILE):
            try:
                with open(FROM_TG_DISPATCH_FILE, "r", encoding="utf-8") as f:
                    ts = float(f.read().strip())
                if time.time() - ts < 15.0:
                    dispatched_from_tg = True
                os.remove(FROM_TG_DISPATCH_FILE)
            except Exception:
                pass

        if not dispatched_from_tg and not is_screen_locked():
            # Bạn vừa gõ trực tiếp trên bàn phím PC -> Tự động tắt chế độ Away (về Here)!
            if os.path.exists(AWAY_FLAG_FILE):
                try:
                    os.remove(AWAY_FLAG_FILE)
                except OSError:
                    pass

        if user_prompt_requests_telegram(user_input_payload):
            with open(prompt_tg_flag, "w", encoding="utf-8") as f:
                f.write("1")

        print("{}")
        return

    # 2. Khi AI gọi tool hỏi user hoặc xin quyền (PreToolUse / Notification)
    if event == "ask":
        is_away = os.path.exists(AWAY_FLAG_FILE) or is_screen_locked()
        from_tg = os.path.exists(tg_flag_file) or os.path.exists(any_tg_flag_file) or os.path.exists(prompt_tg_flag)
        if is_away or from_tg:
            tool_call = payload.get("toolCall", {})
            msg = payload.get("message") or json.dumps(tool_call.get("args", {}), ensure_ascii=False, indent=2)
            send_telegram(f"❓ *AI cần bạn xác nhận / trả lời:*\n{msg}", pane_id)
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

    from_telegram = os.path.exists(tg_flag_file) or os.path.exists(any_tg_flag_file) or explicit_tg_prompt
    if os.path.exists(tg_flag_file) or os.path.exists(any_tg_flag_file):
        for fpath in (tg_flag_file, any_tg_flag_file):
            if os.path.exists(fpath):
                try:
                    os.remove(fpath)
                except OSError:
                    pass

    # Kiểm tra xem người dùng có đang vắng mặt (/away hoặc đã khóa màn hình PC) hay không
    is_away = os.path.exists(AWAY_FLAG_FILE) or is_screen_locked()
    needs_human = looks_like_human_decision_needed(last_msg)
    is_error = term_reason in ("ERROR", "MAX_INVOCATIONS", "MAX_TOKEN_BUDGET_EXCEEDED")
    is_long_task = duration >= cfg.get("min_duration_seconds", 180)

    # QUY TẮC QUYẾT ĐỊNH GỬI TELEGRAM:
    # - Nếu bạn đang ngồi trực tiếp trước PC (is_away == False): chỉ gửi khi bạn chủ động nhắn từ Telegram hoặc gõ kèm "tg".
    # - Nếu bạn đang Vắng mặt / Khóa màn hình PC (is_away == True): gửi khi xong việc quan trọng (needs_human, is_long_task, is_error) hoặc từ Telegram.
    should_notify = from_telegram or (is_away and (needs_human or is_error or is_long_task))

    if should_notify:
        clean_msg = last_msg.replace(NOTIFY_TAG, "").strip() or "(Đã hoàn thành thao tác)"
        if from_telegram:
            prefix = "💬 *Trả lời truy vấn của bạn:*"
        elif needs_human:
            prefix = "🙋‍♂️ *Cần chốt phương án / nghiệp vụ:*"
        elif is_error:
            prefix = f"⚠️ *Cảnh báo dừng ({term_reason}):*"
        else:
            prefix = f"✅ *Hoàn thành task ({int(duration)}s):*"
        send_telegram(f"{prefix}\n\n{clean_msg}", pane_id)

        # Nếu đang chạy trên IDE (ngoài tmux) VÀ đang ở chế độ Away/Lock Screen, chờ phản hồi từ Telegram
        if not is_in_tmux_session() and is_away:
            out_decision = wait_for_ide_telegram_reply(safe_pane, transcript_path)
            print(json.dumps(out_decision))
            return

    print("{}")


if __name__ == "__main__":
    main()
