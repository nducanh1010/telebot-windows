#!/usr/bin/env python3
import sys
import os
import json
import time
import shutil
import platform
import subprocess
import urllib.request
import urllib.parse

IS_WINDOWS = sys.platform == "win32"

if not IS_WINDOWS:
    os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + os.environ.get("PATH", "")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_DIR = os.path.join(BASE_DIR, "state")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

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


def get_session_label(payload):
    if has_tmux():
        pane = os.environ.get("TMUX_PANE", "")
        if pane:
            out = run_tmux(["display-message", "-t", pane, "-p", "#S:#I.#P"])
            if out:
                return out
        out = run_tmux(["display-message", "-p", "#S:#I.#P"])
        if out:
            return out
    # Fallback cho Windows Native hoặc khi chạy ngoài tmux
    workspaces = payload.get("workspacePaths") or []
    cwd = workspaces[0] if workspaces else os.getcwd()
    folder = os.path.basename(os.path.normpath(cwd)) or "terminal"
    return f"{platform.node()}:{folder}"


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


def extract_last_response(transcript_path):
    if not transcript_path:
        return ""
    if transcript_path.endswith("transcript.jsonl"):
        full_candidate = transcript_path[:-len("transcript.jsonl")] + "transcript_full.jsonl"
        if os.path.exists(full_candidate):
            transcript_path = full_candidate

    if not os.path.exists(transcript_path):
        return ""

    last_text = ""
    try:
        with open(transcript_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                obj = json.loads(line)
                # Định dạng của Antigravity (agy)
                if obj.get("type") == "PLANNER_RESPONSE" and obj.get("content"):
                    last_text = obj["content"]
                # Định dạng của Claude Code (claude)
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
                elif obj.get("type") == "response_item" and isinstance(obj.get("payload"), dict):
                    p = obj["payload"]
                    if p.get("role") == "assistant":
                        texts = [
                            b.get("text", "")
                            for b in p.get("content", [])
                            if isinstance(b, dict) and b.get("type") in ("output_text", "text")
                        ]
                        if texts:
                            last_text = "\n".join(texts)
    except Exception:
        pass
    return last_text.strip()


def looks_like_human_decision_needed(text):
    if "[TG_NOTIFY]" in text:
        return True
    lower = text.lower()
    if text.rstrip().endswith("?"):
        keywords = [
            "phương án", "bạn chọn", "bạn muốn", "xác nhận", "đồng ý",
            "option", "approach", "proceed", "clarif", "quyết định", "nghiệp vụ"
        ]
        if any(k in lower for k in keywords):
            return True
    return False


def sanitize_filename(name):
    for ch in (":", ".", "/", "\\", " "):
        name = name.replace(ch, "_")
    return name


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
    tg_flag_file = os.path.join(STATE_DIR, f"waiting_{safe_pane}.flag")
    any_tg_flag_file = os.path.join(STATE_DIR, "waiting_any.flag")

    # Lưu thông tin transcript mới nhất để trên Windows (không có tmux) vẫn xem được /screen
    transcript_path = payload.get("transcriptPath") or payload.get("transcript_path") or ""
    if transcript_path:
        try:
            with open(os.path.join(STATE_DIR, "last_transcript.txt"), "w", encoding="utf-8") as f:
                f.write(transcript_path)
        except Exception:
            pass

    # 1. Ghi nhận thời điểm bắt đầu lượt chạy
    if event == "start":
        with open(timer_file, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
        print("{}")
        return

    # 2. Khi AI gọi tool hỏi user hoặc xin quyền (PreToolUse / Notification)
    if event == "ask":
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

    last_msg = (
        payload.get("last_assistant_message")
        or payload.get("last-assistant-message")
        or extract_last_response(transcript_path)
    )
    term_reason = payload.get("terminationReason", "")

    from_telegram = os.path.exists(tg_flag_file) or os.path.exists(any_tg_flag_file)
    needs_human = looks_like_human_decision_needed(last_msg)
    is_error = term_reason in ("ERROR", "MAX_INVOCATIONS", "MAX_TOKEN_BUDGET_EXCEEDED")
    is_long_task = duration >= cfg.get("min_duration_seconds", 30)

    if from_telegram:
        for fpath in (tg_flag_file, any_tg_flag_file):
            if os.path.exists(fpath):
                try:
                    os.remove(fpath)
                except OSError:
                    pass

    if from_telegram or needs_human or is_error or is_long_task:
        clean_msg = last_msg.replace("[TG_NOTIFY]", "").strip() or "(Đã hoàn thành thao tác)"
        if from_telegram:
            prefix = "💬 *Trả lời truy vấn của bạn:*"
        elif needs_human:
            prefix = "🙋‍♂️ *Cần chốt phương án / nghiệp vụ:*"
        elif is_error:
            prefix = f"⚠️ *Cảnh báo dừng ({term_reason}):*"
        else:
            prefix = f"✅ *Hoàn thành task ({int(duration)}s):*"
        send_telegram(f"{prefix}\n\n{clean_msg}", pane_id)

    print("{}")


if __name__ == "__main__":
    main()
