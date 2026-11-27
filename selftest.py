#!/usr/bin/env python3
"""
selftest.py — Kiểm tra nhanh xem bộ công cụ đã cài đặt & chạy được hay chưa.

Cách dùng:
    python selftest.py            # kiểm tra đầy đủ
    python selftest.py --shot     # kiểm tra + chụp thử ảnh cửa sổ (lưu vào state/selftest.png)

Không cần mạng cho tới bước "Telegram API" (bước đó sẽ báo lỗi nếu mất mạng).
"""
import os
import sys
import json
import time
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

OK = "\033[92m✅\033[0m"
BAD = "\033[91m❌\033[0m"
WARN = "\033[93m⚠️ \033[0m"


def head(t):
    print("\n" + "=" * 64)
    print(t)
    print("=" * 64)


def main():
    shot = "--shot" in sys.argv

    head("1) Cấu hình")
    cfg_path = os.path.join(BASE_DIR, "config.json")
    if not os.path.exists(cfg_path):
        print(f"{BAD} Chưa có config.json. Chạy: python install.py")
        return 1
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        print(f"{BAD} config.json không đọc được: {e}")
        return 1
    token = (cfg.get("bot_token") or "").strip()
    chat_id = str(cfg.get("chat_id") or "").strip()
    print(f"{OK if token else BAD} bot_token: {'đã có (' + token[:6] + '...)' if token else 'THIẾU'}")
    print(f"{OK if chat_id else BAD} chat_id: {'đã có (' + chat_id + ')' if chat_id else 'THIẾU'}")
    print(f"ℹ️  multiplexer_bin: {cfg.get('multiplexer_bin', 'tmux')}")
    print(f"ℹ️  min_duration_seconds: {cfg.get('min_duration_seconds', 180)}")

    head("2) Module & phiên bản")
    import tg_bot_daemon as d
    import hook_notify as h
    print(f"{OK} tg_bot_daemon v{d.VERSION}")
    print(f"{OK} hook_notify   v{h.VERSION}")
    if d.VERSION != h.VERSION:
        print(f"{WARN} Hai file lệch phiên bản — hãy copy cả 2 file cùng lúc.")

    head("3) Multiplexer")
    mux = d.get_mux_bin()
    print(f"ℹ️  binary cấu hình: {mux}")
    if d.has_mux():
        print(f"{OK} Tìm thấy `{mux}` trong PATH")
        if d.is_real_tmux():
            print(f"ℹ️  Đây là tmux thật → capture-pane / list-clients hoạt động đầy đủ.")
        else:
            print(f"{WARN} Không phải tmux thật → tính năng tmux-specific (list-clients) sẽ bị bỏ qua.")
    else:
        print(f"{WARN} Không có `{mux}` → chạy chế độ Windows native:")
        print("     • /screen sẽ gửi ẢNH chụp thật của cửa sổ")
        print("     • gõ lệnh dùng AppActivate của Windows")

    head("4) Cửa sổ Windows phát hiện được")
    if not d.IS_WINDOWS:
        print(f"{WARN} Không phải Windows → bỏ qua bước này.")
    else:
        lines = d.list_windows_terminals()
        if not lines:
            print(f"{BAD} Không tìm thấy cửa sổ nào (Terminal/IDE). Thử mở Windows Terminal.")
        else:
            print(f"{OK} Tìm thấy {len(lines)} cửa sổ:")
            for line in lines[:15]:
                pid, label = d.format_pane_summary(line)
                print(f"   • PID {pid:<8} {label}")

        head("5) Ánh xạ định danh -> PID (term:<folder>)")
        for probe in ("term:Vivas", "term:ai-telegram"):
            pid = d.resolve_window_pid(probe)
            print(f"   {probe:<22} -> {'PID ' + str(pid) if pid else 'KHÔNG khớp cửa sổ nào'}")

    head("6) Chụp ảnh cửa sổ")
    if not d.IS_WINDOWS:
        print(f"{WARN} Bỏ qua (chỉ hỗ trợ Windows).")
    else:
        targets = d.list_targets()
        target = targets[0].split(" | ")[0] if targets else ""
        if not target:
            print(f"{WARN} Không có cửa sổ nào để chụp.")
        else:
            png = d.capture_window_png(target)
            if png:
                import base64
                out = os.path.join(d.STATE_DIR, "selftest.png")
                os.makedirs(d.STATE_DIR, exist_ok=True)
                with open(out, "wb") as f:
                    f.write(base64.b64decode(png))
                size_kb = os.path.getsize(out) // 1024
                print(f"{OK} Chụp được cửa sổ PID {target} -> {out} ({size_kb} KB)")
            else:
                print(f"{BAD} Không chụp được ảnh (xem daemon.err.log để biết lý do)")

    head("7) Telegram API")
    try:
        res = json.load(urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/getMe", timeout=15))
        if res.get("ok"):
            print(f"{OK} Token hợp lệ — bot: @{res['result'].get('username')}")
        else:
            print(f"{BAD} getMe: {res}")
    except Exception as e:
        print(f"{BAD} Không gọi được Telegram API: {e}")
        return 1

    # Kiểm tra xung đột: daemon khác đang poll cùng token?
    try:
        url = f"https://api.telegram.org/bot{token}/getUpdates?timeout=1"
        r = json.load(urllib.request.urlopen(url, timeout=20))
        if r.get("ok"):
            print(f"{WARN} getUpdates trả về OK và không bị 409 → hiện KHÔNG có daemon nào "
                  f"đang poll token này (bạn có thể chạy daemon ở đây).")
        else:
            print(f"{BAD} getUpdates: {r}")
    except Exception as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            body = str(e)[:400]
        if "409" in body or "Conflict" in body:
            print(f"{WARN} 409 Conflict → CÓ daemon khác đang poll cùng token!")
            print("     ➜ Chạy 2 daemon cùng lúc sẽ làm nút bấm 'mất'.")
            print("     ➜ Trên máy kia hãy chạy:  python install.py --stop")
            print("     ➜ Hoặc dùng token khác cho máy này.")
        else:
            print(f"{WARN} getUpdates lỗi: {body}")

    head("8) Hook đã cài cho AI")
    for name, path in (
        ("claude", os.path.expanduser("~/.claude/settings.json")),
        ("agy", os.path.expanduser("~/.gemini/config/hooks.json")),
        ("codex", os.path.expanduser("~/.codex/hooks.json")),
    ):
        found = False
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    txt = f.read()
                found = "hook_notify.py" in txt
            except Exception:
                pass
        print(f"   {name:<7} {OK if found else WARN} {path}")

    head("9) Daemon & heartbeat")
    hb = os.path.join(d.STATE_DIR, "daemon_heartbeat.txt")
    if os.path.exists(hb):
        try:
            with open(hb, "r", encoding="utf-8") as f:
                info = json.load(f)
            age = int(time.time() - float(info.get("ts", 0)))
            print(f"{OK} Daemon đang chạy (PID {info.get('pid')}, v{info.get('version')}, heartbeat {age}s trước)")
        except Exception as e:
            print(f"{WARN} Heartbeat không đọc được: {e}")
    else:
        print(f"{WARN} Chưa có heartbeat → daemon chưa chạy ở thư mục này.")
        print("     ➜ Chạy: python install.py")

    print("\n" + "=" * 64)
    print("Xong. Nếu mọi dòng đều ✅ thì bot sẽ hoạt động.")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
