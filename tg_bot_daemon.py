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
import platform

try:
    from net_compat import force_ipv4, with_retry
    force_ipv4()
except Exception:  # pragma: no cover - thiếu file net_compat.py thì vẫn chạy được
    def force_ipv4():
        return False

    def with_retry(fn, attempts=3, delay=1.5, backoff=1.8):
        try:
            return True, fn()
        except Exception as e:
            return False, str(e)

VERSION = "1.8.1"
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


# ============================================================================
# LAYER MULTIPLEXER (tmux / psmux / ...) + LOGGING
# ============================================================================
# Binary multiplexer đọc động từ config.json: "multiplexer_bin": "tmux" | "psmux"
# Nhờ đó cùng một bộ code chạy được với tmux thật lẫn psmux (Windows-native).
MUX_CACHE = {"bin": None, "ts": 0.0}
MUX_CACHE_TTL = 10.0

# Tên biến môi trường chứa ID pane, theo thứ tự ưu tiên.
# tmux dùng TMUX_PANE, psmux (PowerShell) dùng PSMUX_PANE.
PANE_ENV_NAMES = ("TMUX_PANE", "PSMUX_PANE", "MUX_PANE")


def get_mux_bin():
    """Lấy tên binary multiplexer từ config.json (cache 10s để không đọc file liên tục)."""
    now = time.time()
    if MUX_CACHE["bin"] and (now - MUX_CACHE["ts"]) < MUX_CACHE_TTL:
        return MUX_CACHE["bin"]
    bin_name = "tmux"
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        bin_name = (cfg.get("multiplexer_bin") or "tmux").strip() or "tmux"
    except Exception:
        pass
    MUX_CACHE["bin"] = bin_name
    MUX_CACHE["ts"] = now
    return bin_name


def find_mux_binary():
    """
    Tìm đường dẫn đầy đủ của binary multiplexer.
    Ngoài PATH, còn tìm trong thư mục WinGet vì daemon chạy ngầm từ lúc đăng nhập
    nên có thể không thừa hưởng PATH mới (psmux cài bằng winget nằm trong package dir).
    """
    name = get_mux_bin()
    if os.path.isabs(name):
        return name if os.path.exists(name) else ""
    found = shutil.which(name)
    if found:
        return found
    if IS_WINDOWS:
        pkg_root = os.path.join(
            os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages"
        )
        if os.path.isdir(pkg_root):
            try:
                for entry in os.listdir(pkg_root):
                    if name.lower() not in entry.lower():
                        continue
                    base = os.path.join(pkg_root, entry)
                    for root, _dirs, files in os.walk(base):
                        for f in files:
                            if f.lower() == (name.lower() + ".exe"):
                                return os.path.join(root, f)
            except Exception:
                pass
    return ""


def has_mux():
    """Có multiplexer nào khả dụng không (tmux / psmux / ... theo config.json)."""
    return find_mux_binary() != ""


def is_real_tmux():
    """Chỉ đúng khi đang dùng tmux thật (các lệnh tmux-specific mới an toàn chạy)."""
    return get_mux_bin() == "tmux"


def get_pane_env_name():
    """Trả về tên biến môi trường đang chứa ID pane (TMUX_PANE / PSMUX_PANE / ...)."""
    for env_name in PANE_ENV_NAMES:
        if os.environ.get(env_name):
            return env_name
    return ""


# ============================================================================
# BACKEND PASEO — điều khiển agent của Paseo (thay cho pane tmux/psmux)
# ============================================================================
# Kiểu A: agent trong Paseo chạy CLI thật (claude/codex/agy) trong terminal của
# Paseo ⇒ Hook của CLI vẫn bắn ⇒ đường thông báo về Telegram GIỮ NGUYÊN.
# Chỉ phần "bơm lệnh vào đích" được thay bằng `paseo send <agentId> "<prompt>"`.
#
# Lưu ý: Paseo KHÔNG có khái niệm "chat id" — nó định danh theo `agentId`
# (+ `serverId` cho host từ xa). `chat_id` trong config.json vẫn là phía Telegram.
PASEO_BIN = "paseo"
PASEO_CACHE = {"agents": [], "ts": 0.0}
BACKEND_CACHE = {"value": "", "ts": 0.0}


def get_target_backend():
    """
    Backend đích của lệnh: 'paseo' | 'mux' | 'windows'.
    Đọc `config.json` → "target_backend"; nếu không khai thì tự nhận diện.
    """
    now = time.time()
    if BACKEND_CACHE["value"] and (now - BACKEND_CACHE["ts"]) < 10:
        return BACKEND_CACHE["value"]
    backend = ""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        backend = (cfg.get("target_backend") or "").strip().lower()
    except Exception:
        backend = ""
    if backend not in ("paseo", "mux", "windows"):
        backend = "mux" if has_mux() else "windows"
    BACKEND_CACHE["value"] = backend
    BACKEND_CACHE["ts"] = now
    return backend


def find_paseo_binary():
    """
    Tìm binary `paseo`: PATH trước, sau đó các thư mục cài đặt phổ biến
    (npm global, WinGet, Program Files) — daemon chạy ngầm từ lúc đăng nhập
    nên thường không thừa hưởng PATH mới như shell của bạn.
    """
    found = shutil.which(PASEO_BIN)
    if found:
        return found
    if not IS_WINDOWS:
        return ""
    candidates = []
    local = os.environ.get("LOCALAPPDATA", "")
    roaming = os.environ.get("APPDATA", "")
    programfiles = os.environ.get("ProgramFiles", "")
    if local:
        candidates.append(os.path.join(local, "Microsoft", "WinGet", "Packages"))
        candidates.append(os.path.join(local, "Programs"))
    if roaming:
        candidates.append(os.path.join(roaming, "npm"))
    if programfiles:
        candidates.append(os.path.join(programfiles, "Paseo"))
    for base in candidates:
        if not base or not os.path.isdir(base):
            continue
        try:
            for root, _dirs, files in os.walk(base):
                depth = root[len(base):].count(os.sep)
                if depth > 3:
                    _dirs[:] = []
                    continue
                for f in files:
                    if f.lower() in (PASEO_BIN + ".exe", PASEO_BIN + ".cmd", PASEO_BIN + ".bat"):
                        return os.path.join(root, f)
        except Exception:
            pass
    return ""


def paseo_available():
    return find_paseo_binary() != ""


def run_paseo(args, timeout=30):
    """Chạy CLI paseo (không mở cửa sổ console trên Windows)."""
    binary = find_paseo_binary()
    if not binary:
        return 1, "", (
            "không tìm thấy lệnh `paseo` trong PATH. Cài Paseo CLI hoặc thêm đường dẫn vào PATH."
        )
    try:
        res = run_hidden([binary] + args, text=True, timeout=timeout, check=False)
        return res.returncode, (res.stdout or ""), (res.stderr or "")
    except Exception as e:
        return 1, "", str(e)


def list_paseo_agents():
    """
    Danh sách agent từ Paseo (cache 8s).
    Trả về list dict: {id, title, status, cwd, serverId}
    """
    if (time.time() - PASEO_CACHE["ts"]) < 8 and PASEO_CACHE["agents"]:
        return PASEO_CACHE["agents"]
    agents = []
    code, out, err = run_paseo(["ls", "--json"], timeout=25)
    if code == 0 and out.strip():
        data = None
        try:
            data = json.loads(out)
        except Exception:
            data = None
        items = []
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            for key in ("agents", "items", "data", "results", "sessions"):
                if isinstance(data.get(key), list):
                    items = data[key]
                    break
        for it in items:
            if not isinstance(it, dict):
                continue
            aid = str(
                it.get("agentId") or it.get("id") or it.get("agent_id") or ""
            ).strip()
            if not aid:
                continue
            agents.append({
                "id": aid,
                "title": str(it.get("title") or it.get("name") or it.get("label") or "").strip(),
                "status": str(it.get("status") or it.get("state") or "").strip(),
                "cwd": str(
                    it.get("cwd") or it.get("workspacePath") or it.get("path") or ""
                ).strip(),
                "serverId": str(it.get("serverId") or it.get("server_id") or "").strip(),
            })
    if not agents:
        log_line(
            f"paseo ls --json không trả về agent nào (mã {code}) | "
            f"{((out or err) or '').strip()[:200]}",
            level="ERROR",
        )
    else:
        PASEO_CACHE["agents"] = agents
        PASEO_CACHE["ts"] = time.time()
    return agents


def get_paseo_agent(agent_id):
    for a in list_paseo_agents():
        if a["id"] == agent_id:
            return a
    return None


def is_paseo_target(pane):
    """
    Đích có phải agent của Paseo không?
    Chỉ gọi `paseo ls` khi backend đang là "paseo" (tránh spawn thừa ở các mode khác).
    """
    pane = (pane or "").strip()
    if not pane:
        return False
    if get_target_backend() == "paseo":
        if pane.startswith("agent_"):
            return True
        for a in list_paseo_agents():
            if a["id"] == pane:
                return True
        return False
    # Ở mode khác: chỉ nhận ra ID dạng "agent_*"
    return pane.startswith("agent_")


# ============================================================================
# CHỤP ẢNH GIAO DIỆN WEB (localhost) — dùng Edge/Chrome headless
# ============================================================================
BROWSER_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\Edge\Application\msedge.exe"),
]

COMMON_DEV_PORTS = (3000, 5173, 8080, 4200, 8000, 5174, 9000, 4000, 1234, 5500, 3030)


def find_browser():
    """Tìm trình duyệt để chụp ảnh headless (Edge/Chrome)."""
    for p in BROWSER_PATHS:
        if os.path.exists(p):
            return p
    for name in ("msedge", "chrome", "msedge.exe", "chrome.exe"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def detect_local_dev_url():
    """Dò cổng dev server phổ biến đang mở trên localhost."""
    import socket
    for port in COMMON_DEV_PORTS:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                return f"http://localhost:{port}"
        except Exception:
            continue
    return ""


def capture_url_png(url, width=1600, height=900, timeout=60):
    """
    Chụp ảnh trang web bằng Edge/Chrome headless.
    Trả về chuỗi base64 PNG (hoặc chuỗi rỗng nếu lỗi).
    """
    browser = find_browser()
    if not browser:
        log_line("capture_url_png: không tìm thấy Edge/Chrome để chụp headless", level="ERROR")
        return ""
    out_png = os.path.join(STATE_DIR, "_web_shot.png")
    profile_dir = os.path.join(STATE_DIR, "_browser_profile")
    os.makedirs(profile_dir, exist_ok=True)
    for headless_flag in ("--headless=new", "--headless"):
        try:
            if os.path.exists(out_png):
                os.remove(out_png)
        except OSError:
            pass
        args = [
            browser, headless_flag, "--disable-gpu", "--no-sandbox",
            "--hide-scrollbars", "--disable-extensions",
            f"--user-data-dir={profile_dir}",
            f"--window-size={width},{height}",
            "--virtual-time-budget=8000",
            f"--screenshot={out_png}",
            url,
        ]
        try:
            run_hidden(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=timeout, check=False)
        except Exception as e:
            log_line(f"capture_url_png lỗi chạy browser: {e}", level="ERROR")
            continue
        try:
            if os.path.exists(out_png) and os.path.getsize(out_png) > 1000:
                import base64
                with open(out_png, "rb") as f:
                    return base64.b64encode(f.read()).decode("ascii")
        except Exception as e:
            log_line(f"capture_url_png lỗi đọc ảnh: {e}", level="ERROR")
    log_line(f"capture_url_png: không tạo được ảnh cho {url}", level="ERROR")
    return ""


def send_to_paseo_agent(agent_id, text, force=False):
    """
    Gửi lệnh tới agent qua CLI Paseo.
      `paseo send <agent-id> "<prompt>"`  (mặc định: queue - chờ hết lượt hiện tại)
      `paseo send <agent-id> --behavior steer "<prompt>"`  (ép đọc ngay ở bước sau)
    Trả về (ok, message)
    """
    clean = (text or "").replace("\r", " ").replace("\n", " ").strip()
    args = ["send", agent_id]
    if force:
        args += ["--behavior", "steer"]
    args.append(clean)
    code, out, err = run_paseo(args, timeout=30)
    ok = code == 0
    msg = ((out or "") + (err or "")).strip()
    log_line(f"paseo send [{agent_id}]: mã {code} | {msg[:200]}")
    if not ok and agent_id.startswith("agent_"):
        # Thử lại với dạng ID rút gọn (paseo có thể chấp nhận cả id ngắn)
        short = agent_id.replace("agent_", "")
        code2, out2, err2 = run_paseo(
            ["send", short] + (["--behavior", "steer"] if force else []) + [clean],
            timeout=30,
        )
        if code2 == 0:
            log_line(f"paseo send [{short}]: mã {code2} (dùng ID rút gọn)")
            return True, ((out2 or "") + (err2 or "")).strip()
    return ok, msg


def capture_paseo_status(agent_id):
    """Nội dung hiển thị cho /screen khi đích là agent Paseo (dạng text)."""
    a = get_paseo_agent(agent_id) or {}
    lines = [f"🤖 *Agent:* `{agent_id}`"]
    if a.get("title"):
        lines.append(f"📝 Tiêu đề: {a['title']}")
    if a.get("status"):
        lines.append(f"📊 Trạng thái: `{a['status']}`")
    if a.get("cwd"):
        lines.append(f"📂 Thư mục: `{a['cwd']}`")
    if a.get("serverId"):
        lines.append(f"🖥 Host: `{a['serverId']}`")
    lines.append("")
    lines.append("_Agent chạy CLI thật trong terminal của Paseo → câu trả lời được gửi về Telegram qua Hook._")
    return "\n".join(lines)


def log_line(msg, level="INFO"):
    """Ghi log ra file (daemon.log / daemon.err.log) — không phụ thuộc stdout (Windows chạy pythonw)."""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] [{level}] {msg}"
    try:
        target = os.path.join(BASE_DIR, "daemon.err.log" if level == "ERROR" else "daemon.log")
        with open(target, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        print(line, flush=True)
    except Exception:
        pass


def log_exception(msg):
    import traceback
    detail = traceback.format_exc().replace("\n", " | ")
    log_line(f"{msg} :: {detail}", level="ERROR")


# Windows: không cho tiến trình console con (powershell.exe, tasklist.exe...) mở cửa sổ nhấp nháy.
# Không có cờ này, mỗi lần daemon gọi PowerShell sẽ hiện 1 cửa sổ console bật lên rồi tắt.
NO_WINDOW_FLAG = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if IS_WINDOWS else 0


def run_hidden(cmd, **kwargs):
    """subprocess.run() nhưng KHÔNG bao giờ mở cửa sổ console trên Windows."""
    kwargs.setdefault("stdin", subprocess.DEVNULL)
    kwargs.setdefault("stdout", subprocess.PIPE)
    kwargs.setdefault("stderr", subprocess.PIPE)
    if IS_WINDOWS:
        kwargs.setdefault("creationflags", NO_WINDOW_FLAG)
    return subprocess.run(cmd, **kwargs)


def sanitize_filename(name):
    for ch in (":", ".", "/", "\\", " "):
        name = name.replace(ch, "_")
    return name


def run_mux(args):
    """
    Luôn truyền stdin=DEVNULL, stdout=PIPE, stderr=PIPE và timeout=5.
    Trên macOS/Linux, tránh lỗi treo libevent khi fd 1 là file thường.
    Dùng binary multiplexer do config.json chỉ định (tmux/psmux/...).
    """
    try:
        res = run_hidden(
            [find_mux_binary() or get_mux_bin()] + args,
            text=True,
            timeout=8,
            check=False,
        )
        return res.returncode, res.stdout, res.stderr
    except Exception as e:
        return 1, "", str(e)


# Backward-compatible alias (nhiều chỗ gọi run_tmux)
run_tmux = run_mux
has_tmux = has_mux


def is_any_tmux_client_active(idle_threshold=300):
    if not has_mux() or not is_real_tmux():
        return False
    code_rc, out, _ = run_mux(["list-clients", "-F", "#{client_activity}"])
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
            out = run_hidden(
                ["tasklist", "/FI", "IMAGENAME eq LogonUI.exe"],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=3,
            ).stdout
            return "LogonUI.exe" in (out or "")
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
# WINDOWS NATIVE: Liệt kê cửa sổ + Chụp ảnh màn hình cửa sổ (gửi ảnh về Telegram)
# ============================================================================
# Các tiến trình có cửa sổ để điều khiển: Terminal/PowerShell/CMD + các IDE AI.
WINDOW_PROCESS_PATTERN = (
    "WindowsTerminal|powershell|pwsh|cmd|agy|claude|codex|mintty|ConEmu|"
    "Code|Antigravity|Cursor|Codex|Windsurf|idea64|pycharm64|webstorm64|node|python"
)


WINDOW_LIST_CACHE = {"lines": [], "ts": 0.0}

# ============================================================================
# ĐỊNH DANH CỬA SỔ: pane của multiplexer vs PID của cửa sổ Windows
# ============================================================================
# LỖI NGHIÊM TRỌNG (v1.8.1): trước đây daemon quyết định "gửi qua multiplexer" chỉ dựa vào
# `has_mux()`. Nếu multiplexer CÓ trong PATH nhưng lệnh `list-panes` không trả về pane nào,
# daemon rơi về danh sách cửa sổ Windows (PID) — nhưng khi gửi lệnh lại đi nhánh multiplexer
# ⇒ `send-keys -t <PID>` không tồn tại ⇒ lệnh rơi mất, không có phản hồi.
# → Nay định tuyến dựa trên BẢN THÂN của ID, không dựa trên sự tồn tại của multiplexer.
MUX_PANE_IDS = {"ids": set(), "ts": 0.0}


def refresh_mux_pane_ids():
    """Ghi nhớ các ID pane lấy được từ multiplexer (dùng để định tuyến gửi lệnh)."""
    if not has_mux():
        MUX_PANE_IDS["ids"] = set()
        MUX_PANE_IDS["ts"] = time.time()
        return set()
    ids = set()
    for line in list_mux_panes():
        parts = [p.strip() for p in line.split(" | ")]
        if parts and parts[0]:
            ids.add(parts[0])
    MUX_PANE_IDS["ids"] = ids
    MUX_PANE_IDS["ts"] = time.time()
    return ids


def is_mux_pane(pane):
    """
    True nếu `pane` là ID của một pane multiplexer (tmux/psmux).
    False nếu là PID của cửa sổ Windows hoặc nhãn kiểu "term:<folder>".
    """
    pane = (pane or "").strip()
    if not pane:
        return False
    if pane.isdigit():
        # PID của cửa sổ Windows — KHÔNG bao giờ gửi vào multiplexer
        return False
    if pane.startswith("term:"):
        return False
    ids = MUX_PANE_IDS["ids"]
    if pane in ids:
        return True
    # ID do hook sinh (TMUX_PANE/PSMUX_PANE) chưa nằm trong danh sách -> coi như pane mux
    # (định dạng tmux: "session:0.1", psmux có thể là "%1"/"0.1")
    return ":" in pane or pane.startswith("%")


def list_windows_terminals(use_cache=True):
    """Liệt kê các cửa sổ đang mở trên Windows: 'PID | ProcessName | WindowTitle | CommandLine'."""
    if use_cache and (time.time() - WINDOW_LIST_CACHE["ts"]) < 8:
        return list(WINDOW_LIST_CACHE["lines"])
    ps_script = (
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "$pat = '" + WINDOW_PROCESS_PATTERN + "'; "
        "$clMap = @{}; "
        "try { Get-CimInstance Win32_Process -ErrorAction Stop | ForEach-Object { $clMap[[string]$_.ProcessId] = $_.CommandLine } } catch {}; "
        "Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and ($_.ProcessName -match $pat) } | "
        "ForEach-Object { "
        "$p = $_; "
        "$cl = ''; "
        "if ($clMap.ContainsKey([string]$p.Id)) { $cl = $clMap[[string]$p.Id] }; "
        "if ($cl -and $cl.Length -gt 200) { $cl = $cl.Substring(0,200) }; "
        "\"$($p.Id) | $($p.ProcessName) | $($p.MainWindowTitle) | $cl\" }"
    )
    try:
        res = run_hidden(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            lines = [line.strip() for line in res.stdout.strip().splitlines() if line.strip()]
            WINDOW_LIST_CACHE["lines"] = lines
            WINDOW_LIST_CACHE["ts"] = time.time()
            return lines
    except Exception as e:
        log_line(f"list_windows_terminals lỗi: {e}", level="ERROR")
    return list(WINDOW_LIST_CACHE["lines"])


def resolve_window_pid(pane, fallback_default=True):
    """
    Đổi định danh cửa sổ về PID tiến trình Windows để chụp ảnh / gõ phím.
    - pane dạng số ("2996") -> dùng luôn
    - pane dạng "term:<folder>" -> tìm cửa sổ có tiêu đề hoặc command line chứa <folder>
    """
    pane = (pane or "").strip()
    if pane.isdigit():
        return int(pane)
    folder = ""
    if pane.startswith("term:"):
        folder = pane.split(":", 1)[1]
    elif ":" in pane:
        folder = pane.split(":", 1)[1]
    else:
        folder = pane
    folder = folder.strip().lower()
    if not folder:
        return 0
    best_pid = 0
    for line in list_windows_terminals():
        parts = [p.strip() for p in line.split(" | ")]
        if not parts or not parts[0].isdigit():
            continue
        pid, proc, title = parts[0], (parts[1] if len(parts) > 1 else ""), (parts[2] if len(parts) > 2 else "")
        cmdline = parts[3] if len(parts) > 3 else ""
        if folder and folder in title.lower():
            return int(pid)
        if folder and folder in cmdline.lower():
            best_pid = best_pid or int(pid)
    if best_pid:
        return best_pid
    if not fallback_default:
        return 0
    # Không khớp thư mục -> thử cửa sổ đang được chọn mặc định
    saved = ""
    try:
        if os.path.exists(ACTIVE_PANE_FILE):
            with open(ACTIVE_PANE_FILE, "r", encoding="utf-8") as f:
                saved = f.read().strip()
    except Exception:
        pass
    if saved.isdigit():
        return int(saved)
    return 0


# Script PowerShell chụp ảnh cửa sổ -> trả về PNG dạng base64.
# Ưu tiên PrintWindow (chụp được cả khi cửa sổ bị che), fallback CopyFromScreen.
SCREENSHOT_PS = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Drawing
if (-not ('Win32Cap' -as [type])) {
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32Cap {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT lpRect);
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdcBlt, uint nFlags);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
}
"@
}
[Win32Cap]::SetProcessDPIAware() | Out-Null

$pidArg = $args[0]
$proc = Get-Process -Id ([int]$pidArg) -ErrorAction Stop
$h = $proc.MainWindowHandle
if ($h -eq [IntPtr]::Zero) { Write-Output 'ERR:NO_WINDOW'; exit 2 }

if ([Win32Cap]::IsIconic($h)) { [Win32Cap]::ShowWindow($h, 9) | Out-Null; Start-Sleep -Milliseconds 500 }

$r = New-Object Win32Cap+RECT
[Win32Cap]::GetWindowRect($h, [ref]$r) | Out-Null
$w = $r.Right - $r.Left
$ht = $r.Bottom - $r.Top
if ($w -lt 10 -or $ht -lt 10) { Write-Output 'ERR:BAD_RECT'; exit 3 }

$bmp = New-Object System.Drawing.Bitmap($w, $ht)
$g = [System.Drawing.Graphics]::FromImage($bmp)
$hdc = $g.GetHdc()
$ok = [Win32Cap]::PrintWindow($h, $hdc, 2)
$g.ReleaseHdc($hdc)
$g.Dispose()

if (-not $ok) {
  # Fallback: đưa cửa sổ lên trước rồi chụp vùng màn hình
  [Win32Cap]::SetForegroundWindow($h) | Out-Null
  Start-Sleep -Milliseconds 400
  $g2 = [System.Drawing.Graphics]::FromImage($bmp)
  $g2.CopyFromScreen($r.Left, $r.Top, 0, 0, $bmp.Size)
  $g2.Dispose()
}
else {
  # PrintWindow có thể trả về ảnh đen với app dùng GPU -> thử lại bằng chụp màn hình thật
  $allBlack = $true
  $stepX = [Math]::Max(1, [int]($w / 20))
  $stepY = [Math]::Max(1, [int]($ht / 20))
  for ($x = 0; $x -lt $w -and $allBlack; $x += $stepX) {
    for ($y = 0; $y -lt $ht; $y += $stepY) {
      $c = $bmp.GetPixel($x, $y)
      if (($c.R + $c.G + $c.B) -gt 30) { $allBlack = $false; break }
    }
  }
  if ($allBlack) {
    [Win32Cap]::SetForegroundWindow($h) | Out-Null
    Start-Sleep -Milliseconds 400
    $g4 = [System.Drawing.Graphics]::FromImage($bmp)
    $g4.CopyFromScreen($r.Left, $r.Top, 0, 0, $bmp.Size)
    $g4.Dispose()
  }
}

# Thu nhỏ nếu quá rộng để gửi nhẹ qua Telegram
$maxW = 1400
if ($w -gt $maxW) {
  $nh = [int]($ht * $maxW / $w)
  $small = New-Object System.Drawing.Bitmap($maxW, $nh)
  $g3 = [System.Drawing.Graphics]::FromImage($small)
  $g3.InterpolationMode = 'HighQualityBicubic'
  $g3.DrawImage($bmp, 0, 0, $maxW, $nh)
  $g3.Dispose()
  $bmp.Dispose()
  $bmp = $small
}

$ms = New-Object System.IO.MemoryStream
$bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
$bmp.Dispose()
[Convert]::ToBase64String($ms.ToArray())
'''


def capture_window_png(pid):
    """Chụp ảnh cửa sổ Windows theo PID. Trả về chuỗi base64 PNG hoặc chuỗi rỗng nếu lỗi."""
    if not IS_WINDOWS or not pid:
        return ""
    script_path = os.path.join(STATE_DIR, "_screenshot.ps1")
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(SCREENSHOT_PS)
    except Exception:
        return ""
    try:
        res = run_hidden(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", script_path, str(int(pid))],
            encoding="utf-8",
            errors="replace",
            timeout=30,
            check=False,
        )
    except Exception as e:
        log_line(f"capture_window_png({pid}) exception: {e}", level="ERROR")
        return ""
    out = (res.stdout or "").strip()
    if not out or out.startswith("ERR:"):
        log_line(f"capture_window_png({pid}) failed: {out or res.stderr}", level="ERROR")
        return ""
    # base64 có thể bị ngắt dòng -> gộp lại
    return "".join(out.split())


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
        run_hidden(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            timeout=6,
            check=False,
        )
    except Exception:
        pass


# ============================================================================
# GỬI LỆNH VÀO Ô CHAT AI CỦA IDE (Antigravity / VS Code / Cursor ...)
# ============================================================================
# AppActivate chỉ đưa CỬA SỔ lên trước, không đưa con trỏ vào ô chat AI.
# Với IDE (Electron) điều này khiến lệnh dán nhầm vào editor hoặc rơi mất.
# → Dùng UI Automation để tìm đúng ô nhập AI, focus vào nó rồi mới dán.
IDE_PROCESS_NAMES = (
    "code", "code - insiders", "codex", "antigravity", "cursor", "windsurf",
    "idea64", "pycharm64", "webstorm64", "devenv", "zed", "trae",
)

# Ghi nhớ lần gần nhất: lệnh bị từ chối vì IDE đang focus vào editor (pane -> thông tin)
IDE_REFUSE_HINT = {}

# Script UIA: tìm ô nhập AI trong cửa sổ, focus, dán clipboard, Enter.
# Nếu không tìm thấy -> dump cây UIA ra file để daemon ghi log (tự chẩn đoán).
UIA_SEND_PS = r'''
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$pidArg = $args[0]
$textFile = $args[1]
$dumpPath = $args[2]
$script:textFile = $textFile
$proc = Get-Process -Id ([int]$pidArg) -ErrorAction Stop
$h = $proc.MainWindowHandle
if ($h -eq [IntPtr]::Zero) { Write-Output 'ERR:NO_WINDOW'; exit 2 }

$root = [System.Windows.Automation.AutomationElement]::FromHandle($h)
if ($null -eq $root) { Write-Output 'ERR:NO_ROOT'; exit 3 }

# Đưa cửa sổ ứng dụng lên trước, để SendKeys gửi đúng nơi (không gửi nhầm cửa sổ khác)
$script:targetHwnd = $h
if (-not ('User32Focus' -as [type])) {
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class User32Focus {
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr hWnd);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
}
"@
}
try { [User32Focus]::ShowWindow($script:targetHwnd, 9) | Out-Null } catch {}
try { [User32Focus]::BringWindowToTop($script:targetHwnd) | Out-Null } catch {}
try { [User32Focus]::SetForegroundWindow($script:targetHwnd) | Out-Null } catch {}
Start-Sleep -Milliseconds 150

# Mẫu nhận dạng ô chat AI (placeholder / tên hiển thị / automation id / class)
$patterns = @('ask anything','ask a question','type a message','enter a prompt','chat','prompt',
              'message','send','new chat','gemini','claude','codex','antigravity','agent',
              'input','composer','ask ai','what can i do','ask the agent','task','instruction',
              'write a message','inputfield','textfield','say something')

# Mẫu RIÊNG cho phần tử đang focus: thêm cả ô nhập của terminal (xterm.js trong IDE)
# vì AI thường chạy dưới dạng CLI (Antigravity CLI / claude / codex) trong terminal nhúng.
$focusPatterns = @('write a message','ask anything','xterm','terminal input','xterm-helper-textarea',
                    'auto mode','antigravity cli','inputfield','textfield','message','chat','prompt')

function Find-AiInput($root) {
  # Dò theo nhiều ControlType: Edit / Document / Text / ComboBox
  $types = @(
    [System.Windows.Automation.ControlType]::Edit,
    [System.Windows.Automation.ControlType]::Document,
    [System.Windows.Automation.ControlType]::ComboBox,
    [System.Windows.Automation.ControlType]::Text
  )
  foreach ($ct in $types) {
    try {
      $cond = [System.Windows.Automation.PropertyCondition]::new(
        [System.Windows.Automation.AutomationElement]::ControlTypeProperty, $ct)
      $items = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)
      foreach ($e in $items) {
        try {
          $blob = ("{0} {1} {2} {3}" -f $e.Current.Name, $e.Current.AutomationId,
                   $e.Current.ClassName, $e.Current.HelpText).ToLower()
          foreach ($p in $patterns) {
            if ($blob.Contains($p)) {
              return $e
            }
          }
        } catch {}
      }
    } catch {}
  }
  return $null
}

function SendToElement($el, $kind) {
  # $kind: 'chat' | 'terminal'  -> terminal thì gộp mọi xuống dòng thành 1 dòng
  # (dán nhiều dòng vào terminal = chạy nhiều lệnh -> RẤT NGUY HIỂM)
  $t = ''
  try { $t = [System.IO.File]::ReadAllText($script:textFile) } catch { $t = '' }
  if ($kind -eq 'terminal') {
    $t = ($t -replace "`r`n", ' ') -replace "`n", ' '
    $t = ($t -replace "`r", ' ') -replace '[\r\n]+', ' '
  }
  try { Set-Clipboard -Value $t } catch {}
  try { [User32Focus]::SetForegroundWindow($script:targetHwnd) | Out-Null } catch {}
  try { $el.SetFocus() } catch {}
  Start-Sleep -Milliseconds 300
  $ws = New-Object -ComObject WScript.Shell
  $ws.SendKeys('^v')
  Start-Sleep -Milliseconds 350
  $ws.SendKeys('{ENTER}')
}

$best = Find-AiInput $root
if ($null -ne $best) {
  SendToElement $best 'chat'
  Write-Output ('OK:UIA|chat:' + $best.Current.Name)
  exit 0
}

# ---------------------------------------------------------------
# QUAN TRỌNG (Electron/Chromium): nội dung web của IDE được render ở TIẾN TRÌNH RIÊNG
# (renderer) nên KHÔNG nằm trong cây UIA của handle cửa sổ chính -> FindAll không thấy.
# Nhưng phần tử ĐANG FOCUS thì AutomationElement::FocusedElement vẫn thấy.
# => Nếu người dùng đã bấm vào ô chat AI / ô nhập terminal, ta dùng chính phần tử đó.
# ---------------------------------------------------------------
$focused = [System.Windows.Automation.AutomationElement]::FocusedElement
if ($null -ne $focused) {
  try {
    $fblob = ("{0} {1} {2}" -f $focused.Current.Name, $focused.Current.AutomationId,
              $focused.Current.ClassName).ToLower()
    $kind = 'chat'
    if ($fblob.Contains('xterm') -or $fblob.Contains('terminal')) { $kind = 'terminal' }
    foreach ($p in $focusPatterns) {
      if ($fblob.Contains($p)) {
        SendToElement $focused $kind
        Write-Output ('OK:UIA|' + $kind + ':' + $focused.Current.Name)
        exit 0
      }
    }
  } catch {}
}

# Không tìm thấy -> dump cây UIA để tự chẩn đoán
try {
  $all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants,
                        [System.Windows.Automation.Condition]::TrueCondition)
  $lines = New-Object System.Collections.Generic.List[string]
  $lines.Add(("TREE_SIZE=" + $all.Count))
  $i = 0
  foreach ($e in $all) {
    $i++
    if ($i -gt 400) { break }
    try {
      $n = $e.Current.Name
      $aid = $e.Current.AutomationId
      $cls = $e.Current.ClassName
      $ct = $e.Current.ControlType.ProgrammaticName
      if ($n -or $aid) {
        $lines.Add(("{0}|{1}|{2}|{3}" -f $ct, $n, $aid, $cls))
      }
    } catch {}
  }
  Set-Content -Path $dumpPath -Value $lines -Encoding UTF8
} catch {
  Set-Content -Path $dumpPath -Value @("DUMP_ERROR: " + $_.Exception.Message) -Encoding UTF8
}

# Phần tử đang focus + phạm vi ứng dụng
# LƯU Ý: Electron/Chromium render web content ở TIẾN TRÌNH RIÊNG (renderer) nên
# ProcessId của phần tử focus KHÁC PID cửa sổ, nhưng vẫn cùng ứng dụng.
# => Phải leo lên cửa sổ cha (top-level) rồi mới so sánh.
$focused = [System.Windows.Automation.AutomationElement]::FocusedElement
$finfo = 'UNKNOWN'
if ($null -ne $focused) {
  try {
    $scope = 'other-app'
    try {
      $top = $focused
      $walker = [System.Windows.Automation.TreeWalker]::ControlViewWalker
      while ($true) {
        $parent = $walker.GetParent($top)
        if ($null -eq $parent) { break }
        $top = $parent
      }
      if ($top.Current.ProcessId -eq $proc.Id) { $scope = 'this-app' }
      elseif ($top.Current.Name -eq $proc.MainWindowTitle) { $scope = 'this-app' }
    } catch {}
    $finfo = ("{0}|{1}|{2}|{3}" -f $focused.Current.Name, $focused.Current.ClassName,
              $focused.Current.ControlType.ProgrammaticName, $scope)
  } catch { $finfo = 'UNKNOWN' }
}
Write-Output ('ERR:NO_AI_INPUT|' + $finfo)
exit 4
'''


def get_process_name_for_pid(pid):
    """Tên tiến trình (vd 'Antigravity') của một PID, lấy từ cache danh sách cửa sổ."""
    try:
        for line in list_windows_terminals():
            parts = [p.strip() for p in line.split(" | ")]
            if parts and parts[0].isdigit() and int(parts[0]) == int(pid):
                return (parts[1] if len(parts) > 1 else "").lower()
    except Exception:
        pass
    # Fallback: hỏi trực tiếp hệ thống (PID có thể mới, chưa có trong cache)
    # Dùng ctypes (không spawn tasklist.exe) để không mở cửa sổ console nhấp nháy.
    try:
        name = get_process_name_ctypes(int(pid))
        if name:
            return name
    except Exception:
        pass
    return ""


def get_process_name_ctypes(pid):
    """Lấy tên tiến trình bằng Win32 API (không spawn tiến trình con)."""
    try:
        import ctypes
        from ctypes import wintypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            handle = kernel32.OpenProcess(0x100000, False, int(pid))  # SYNCHRONIZE
        if not handle:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(1024)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                full = buf.value
                return os.path.basename(full).lower()
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        pass
    return ""


def set_clipboard(text):
    """Đưa text vào clipboard (dùng để dán sau khi focus đúng ô nhập)."""
    escaped = text.replace("'", "''")
    try:
        run_hidden(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             f"Set-Clipboard -Value '{escaped}'"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
            check=False,
        )
        return True
    except Exception as e:
        log_line(f"set_clipboard lỗi: {e}", level="ERROR")
        return False


def send_to_ide_window(pid, text):
    """
    Gửi text vào ô chat AI của IDE bằng UI Automation.
    Trả về (status, detail):
      ("sent", "tên ô nhập")       -> đã gửi thành công
      ("no_input", "<focus info>") -> không tìm thấy ô chat AI
      ("refused", "<focus info>")  -> đang focus vào trình soạn thảo -> KHÔNG dán mù
      ("error", detail)            -> lỗi kỹ thuật
    """
    script_path = os.path.join(STATE_DIR, "_uia_send.ps1")
    dump_path = os.path.join(STATE_DIR, "_uia_dump.txt")
    text_path = os.path.join(STATE_DIR, "_send_text.txt")
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(UIA_SEND_PS)
        # Nội dung cần gửi đưa qua file (script tự đặt clipboard sau khi biết đích là chat hay terminal)
        with open(text_path, "w", encoding="utf-8") as f:
            f.write(text or "")
    except Exception as e:
        return "error", f"không ghi được script: {e}"

    try:
        res = run_hidden(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", script_path, str(int(pid)), text_path, dump_path],
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except Exception as e:
        return "error", str(e)

    out = (res.stdout or "").strip()
    if out.startswith("OK:UIA"):
        return "sent", out.split("|", 1)[1] if "|" in out else "ô nhập AI"
    if out.startswith("ERR:NO_AI_INPUT"):
        detail = out.split("|", 1)[1] if "|" in out else ""
        # Đọc dump cây UIA để tự chẩn đoán (ghi vào daemon.log)
        if os.path.exists(dump_path):
            try:
                with open(dump_path, "r", encoding="utf-8", errors="replace") as f:
                    dump_lines = f.read().splitlines()
                head_txt = " / ".join(dump_lines[:40])
                log_line(f"UIA tree của PID {pid} ({len(dump_lines)} dòng): {head_txt}")
            except Exception:
                pass
        # Phần tử đang focus là trình soạn thảo mã CỦA CHÍNH IDE -> KHÔNG dán mù (tránh hỏng source)
        # (nếu focus nằm ở ứng dụng khác thì không nói "đang ở editor", chỉ nói chưa thấy ô chat)
        low = detail.lower()
        in_this_app = low.endswith("this-app")
        is_code_editor = in_this_app and (
            ("edit" in low and not any(k in low for k in ("ask", "chat", "prompt", "message", "input")))
            or "document" in low
        )
        if is_code_editor:
            return "refused", detail
        return "no_input", detail
    return "error", out or (res.stderr or "")[:200]


def send_to_ide_window_force(pid, text):
    """Ép dán vào phần tử đang focus của IDE (bỏ qua UIA) — dùng khi user đã bấm vào ô chat AI."""
    log_line(f"send_to_ide_window_force: dán mù vào phần tử đang focus của PID {pid}")
    send_to_windows_terminal(str(pid), text, is_special_key=False)
    return "sent", "ép focus (force)"


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def tg_api(token, method, data, attempts=3):
    """Gọi Telegram API. Có retry cho lỗi mạng tạm thời (WinError 10054, timeout...)."""

    def _call():
        url = f"https://api.telegram.org/bot{token}/{method}"
        req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode("utf-8"))
        with urllib.request.urlopen(req, timeout=35) as res:
            return json.loads(res.read().decode("utf-8"))

    ok, result = with_retry(_call, attempts=attempts, delay=1.5)
    if ok:
        return result
    raise RuntimeError(f"Telegram API {method} thất bại: {result}")


def tg_answer(token, callback_id, text=""):
    """Trả lời callback query. LUÔN bắt lỗi: query quá cũ (>10 phút) sẽ trả 400 và
    nếu để nổi lỗi ra ngoài sẽ kẹt vĩnh viễn vòng lặp daemon (không cập nhật offset)."""
    try:
        payload = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text[:190]
        tg_api(token, "answerCallbackQuery", payload)
    except Exception as e:
        log_line(f"answerCallbackQuery failed: {e}", level="ERROR")


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


def tg_send_photo(token, chat_id, png_b64, caption="", reply_markup=None, use_markdown=True):
    """Gửi ảnh PNG (base64) lên Telegram qua sendPhoto (multipart/form-data)."""
    try:
        import base64
        png_bytes = base64.b64decode(png_b64)
    except Exception as e:
        log_line(f"tg_send_photo decode error: {e}", level="ERROR")
        return None
    if not png_bytes:
        return None
    boundary = "----AiTelegramBoundary" + str(int(time.time() * 1000))
    fields = {"chat_id": str(chat_id)}
    if caption:
        fields["caption"] = caption[:1024]
        if use_markdown:
            fields["parse_mode"] = "Markdown"
    body = b""
    for key, value in fields.items():
        body += f"--{boundary}\r\n".encode("utf-8")
        body += f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8")
        body += f"{value}\r\n".encode("utf-8")
    body += f"--{boundary}\r\n".encode("utf-8")
    body += b'Content-Disposition: form-data; name="photo"; filename="screen.png"\r\n'
    body += b"Content-Type: image/png\r\n\r\n"
    body += png_bytes + b"\r\n"
    body += f"--{boundary}--\r\n".encode("utf-8")

    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )

    def _call():
        with urllib.request.urlopen(req, timeout=60) as res:
            return json.loads(res.read().decode("utf-8"))

    ok, result = with_retry(_call, attempts=3, delay=2.0)
    if ok:
        return result
    # Caption Markdown lỗi -> thử lại không dùng parse_mode
    if use_markdown:
        return tg_send_photo(token, chat_id, png_b64, caption, reply_markup, use_markdown=False)
    log_line(f"tg_send_photo error: {result}", level="ERROR")
    return None


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
        {"command": "ping", "description": "🩺 Kiểm tra daemon còn sống + cấu hình"},
        {"command": "force", "description": "⚡ Ép gửi lệnh vào ô đang focus của IDE"},
        {"command": "shot", "description": "🌐 Chụp ảnh giao diện web (localhost)"},
    ]
    try:
        tg_api(token, "setMyCommands", {"commands": json.dumps(commands)})
        tg_api(token, "setChatMenuButton", {"menu_button": json.dumps({"type": "commands"})})
    except Exception:
        pass


def list_mux_panes():
    """
    Liệt kê pane của multiplexer, trả về các dòng 'id | command | path'.

    psmux v3.4.0 KHÔNG hỗ trợ `list-panes -a -F` (chỉ "list panes in current window"),
    nên phải duyệt: list-sessions -> list-windows -t <session> -> list-panes -t <sess>:<win>.
    Với tmux thật, dùng cú pháp 1 lệnh `list-panes -a -F`.
    """
    if not has_mux():
        return []

    # 1) Cú pháp tmux (một lệnh) — nếu binary là tmux thật hoặc bản psmux tương thích
    code, out, err = run_mux([
        "list-panes", "-a", "-F",
        "#{pane_id} | #{pane_current_command} | #{pane_current_path}",
    ])
    if code == 0 and out.strip():
        return out.strip().splitlines()

    if get_mux_bin() == "tmux":
        log_line(f"tmux list-panes -a thất bại (mã {code}): {err.strip()[:200]}", level="ERROR")
        return []

    # 2) psmux: duyệt session -> window -> pane
    code, out, err = run_mux(["list-sessions"])
    if code != 0:
        log_line(
            f"psmux list-sessions thất bại (mã {code}): {err.strip()[:200]} — "
            f"có thể chưa có session nào đang chạy (thử: psmux ls)",
            level="ERROR",
        )
        return []

    sessions = []
    for raw in out.splitlines():
        raw = raw.strip()
        if not raw:
            continue
        name = raw.split(":", 1)[0].strip()
        if name:
            sessions.append(name)
    if not sessions:
        log_line("psmux không có session nào đang chạy → thử: psmux new -s ai (rồi chạy `agy` bên trong)")
        return []

    lines = []
    for sess in sessions:
        code, wout, _ = run_mux(["list-windows", "-t", sess])
        windows = []
        if code == 0:
            for raw in wout.splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                wid = raw.split(":", 1)[0].strip()
                if wid.isdigit():
                    windows.append(wid)
        pane_targets = [f"{sess}:{wid}" for wid in windows]
        if not pane_targets:
            pane_targets = [sess]  # fallback: liệt kê pane của cả session
        for target in pane_targets:
            code, pout, _ = run_mux(["list-panes", "-t", target])
            if code != 0 or not pout.strip():
                continue
            for raw in pout.splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                pid_ = raw.split(":", 1)[0].strip()
                if not pid_:
                    continue
                rest = raw.split(":", 1)[1].strip() if ":" in raw else ""
                cmd = ""
                m = re.search(r'"([^"]*)"', rest)
                if m:
                    cmd = m.group(1)
                # Lấy thư mục hiện tại của pane (psmux không trả trong list-panes)
                path = ""
                code2, pout2, _ = run_mux([
                    "display-message", "-t", pid_, "-p", "#{pane_current_path}",
                ])
                if code2 == 0 and pout2.strip():
                    path = pout2.strip().splitlines()[0]
                lines.append(f"{pid_} | {cmd} | {path}")
    return lines


def list_targets():
    # Backend Paseo: liệt kê agent (thay cho pane tmux/psmux)
    if get_target_backend() == "paseo":
        agents = list_paseo_agents()
        lines = []
        for a in agents:
            folder = os.path.basename(os.path.normpath(a["cwd"])) if a.get("cwd") else ""
            title = a.get("title") or "agent"
            lines.append(f"{a['id']} | {title} | {folder}")
        return lines

    if has_mux():
        lines = list_mux_panes()
        if lines:
            # Ghi nhớ ID pane để định tuyến gửi lệnh (xem is_mux_pane)
            MUX_PANE_IDS["ids"] = {l.split(" | ")[0].strip() for l in lines if l.strip()}
            MUX_PANE_IDS["ts"] = time.time()
            return lines
        # Multiplexer có trong PATH nhưng không trả về pane nào -> ghi log để lộ nguyên nhân
        log_line(
            f"Multiplexer '{get_mux_bin()}' không liệt kê được pane nào "
            f"→ tạm dùng danh sách cửa sổ Windows.",
            level="ERROR",
        )
    if IS_WINDOWS:
        return list_windows_terminals()
    return []


def get_active_pane():
    targets = list_targets()
    valid_ids = [t.split(" | ")[0] for t in targets]
    mux_ids = [i for i in valid_ids if is_mux_pane(i)]
    if os.path.exists(ACTIVE_PANE_FILE):
        with open(ACTIVE_PANE_FILE, "r", encoding="utf-8") as f:
            saved = f.read().strip()
        if saved and (not valid_ids or saved in valid_ids):
            return saved
    # Ưu tiên pane của multiplexer đang chạy AI (chính xác hơn cửa sổ Windows)
    for line in targets:
        pid = line.split(" | ")[0]
        if is_mux_pane(pid) and any(k in line.lower() for k in ("agy", "claude", "codex", "node", "python")):
            return pid
    for line in targets:
        if any(k in line.lower() for k in ("agy", "claude", "codex", "node", "python")):
            return line.split(" | ")[0]
    if mux_ids:
        return mux_ids[0]
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
    """
    Chụp nội dung màn hình (text) của một cửa sổ.
    - Pane multiplexer (tmux/psmux): dùng `capture-pane` -> text sạch.
    - Cửa sổ Windows: dùng transcript **riêng của đúng cửa sổ đó** (nếu có).
      ⚠️ KHÔNG dùng `last_transcript.txt` (transcript của phiên khác) — trước đây nó khiến
      `/screen` hiện "log chat" của một phiên hoàn toàn khác, gây hiểu nhầm.
      Phần ảnh thật của cửa sổ do capture_screen_photo() phụ trách.
    """
    if is_mux_pane(pane):
        code, out, _ = run_mux(["capture-pane", "-t", pane, "-p"])
        if code == 0 and out.strip():
            return clean_tmux_screen(out)
        return f"⚠️ Cửa sổ `[{pane}]` không còn tồn tại hoặc đã đổi tên trong `{get_mux_bin()}` (kiểm tra `/list`)."

    if is_paseo_target(pane):
        return capture_paseo_status(pane)

    safe_pane = sanitize_filename(pane)
    for candidate in (
        os.path.join(STATE_DIR, f"last_transcript_{safe_pane}.txt"),
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
    return (
        "🪟 Đây là cửa sổ Windows (không phải ô terminal của multiplexer) nên không có "
        "nội dung text để đọc.\n"
        "👉 Hãy bấm nút **📸 Xem màn hình** để nhận **ảnh chụp cửa sổ**.\n"
        "💡 Muốn đọc được nội dung dạng text: chạy AI trong `tmux`/`psmux` và đặt "
        "`\"multiplexer_bin\"` trong `config.json`."
    )


def capture_screen_photo(pane):
    """
    Chụp ẢNH thật của cửa sổ Windows (Terminal / IDE / psmux / bất kỳ app nào có cửa sổ).
    Trả về chuỗi base64 PNG, hoặc chuỗi rỗng nếu không chụp được.
    """
    if not IS_WINDOWS:
        return ""
    if is_paseo_target(pane):
        return ""  # agent Paseo: nội dung text, không chụp ảnh cửa sổ
    if is_mux_pane(pane):
        # Nếu multiplexer đọc được nội dung pane -> text đã đủ, không cần ảnh.
        # Nếu multiplexer KHÔNG hỗ trợ capture-pane (vd psmux) -> thử chụp ảnh cửa sổ Windows.
        code, out, _ = run_mux(["capture-pane", "-t", pane, "-p"])
        if code == 0 and out.strip():
            return ""
        pid = resolve_window_pid(pane, fallback_default=False)
    else:
        pid = resolve_window_pid(pane)
    if not pid:
        return ""
    return capture_window_png(pid)


def send_screen_snapshot(token, chat_id, pane, extra_caption=""):
    """
    Gửi "xem màn hình" về Telegram:
    - Windows native: gửi ẢNH thật của cửa sổ (sendPhoto). KHÔNG kèm transcript của phiên khác.
    - Có multiplexer: gửi nội dung text từ `capture-pane`.
    """
    caption = f"📺 *[{pane}]*" + (f" — {extra_caption}" if extra_caption else "")
    photo = capture_screen_photo(pane)
    photo_sent = False
    if photo:
        res = tg_send_photo(token, chat_id, photo, caption=caption)
        photo_sent = bool(res and res.get("ok"))
    text = capture_screen(pane)
    if not photo_sent:
        tg_send(token, chat_id, f"{caption}:\n\n{text}")
    elif text and not text.startswith(("⚠️", "🪟")):
        # Chỉ kèm text khi nó thực sự là nội dung RIÊNG của cửa sổ này
        tg_send(token, chat_id, f"{caption}\n\n{text}")
    return photo_sent


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
    if not is_mux_pane(pid):
        return False, ""
    code_rc, out, _ = run_mux(["capture-pane", "-t", pid, "-p"])
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
    if is_paseo_target(pid):
        agent = get_paseo_agent(pid) or {}
        status = (agent.get("status") or "").lower()
        if not status:
            return "🤖 Agent"
        if "run" in status or "busy" in status or "work" in status:
            return "⚙️ Đang thực thi..."
        if "wait" in status or "idle" in status or "ready" in status:
            return "💤 Sẵn sàng"
        if "error" in status or "fail" in status:
            return "❌ Lỗi"
        return f"🤖 {status}"
    if not is_mux_pane(pid):
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

    code_rc, out, _ = run_mux(["capture-pane", "-t", pid, "-p"])
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
        buttons.append([
            {"text": f"🎯 Chọn {pid} ({label})", "callback_data": f"use:{pid}"},
            {"text": "📸", "callback_data": f"screen:{pid}"},
        ])
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


def pane_state_aliases(pane):
    """
    Các tên file state/ mà hook có thể dùng cho cửa sổ này.
    - Pane multiplexer: ID gốc + dạng canonical ("session:0.1").
    - Agent Paseo: agentId + các nhãn mà hook dùng cho phiên CLI không có multiplexer
      ("<node>:<folder>", "term:<folder>") ⇒ hook luôn tìm thấy cờ `waiting_*`/`ack_pending_*`.
    """
    names = {sanitize_filename(pane)}
    if is_paseo_target(pane):
        agent = get_paseo_agent(pane) or {}
        cwd = agent.get("cwd") or ""
        folder = os.path.basename(os.path.normpath(cwd)) if cwd else ""
        if folder:
            names.add(sanitize_filename(f"{platform.node()}:{folder}"))
            names.add(sanitize_filename(f"term:{folder}"))
            names.add(sanitize_filename(folder))
        if agent.get("serverId"):
            names.add(sanitize_filename(f"{agent['serverId']}:{folder or agent['id']}"))
    else:
        canonical = get_mux_canonical_id(pane)
        if canonical:
            names.add(sanitize_filename(canonical))
    return names


def get_mux_canonical_id(pane):
    """
    ID canonical kiểu tmux ("session:0.1") của một pane.
    Dùng để ghi file state/ack_pending_*.json trùng với tên file mà hook sẽ xoá.
    """
    if not is_mux_pane(pane):
        return ""
    code, out, _ = run_mux(["display-message", "-t", pane, "-p", "#S:#I.#P"])
    if code == 0 and out.strip():
        return out.strip().splitlines()[0].strip()
    return ""


def mux_input_has_text(pane):
    """
    Ô nhập của pane đang còn text chưa gửi?
    Đọc dòng cuối của pane: nếu bắt đầu bằng ">" và phần sau khác rỗng -> còn text.
    """
    if not is_mux_pane(pane):
        return False
    code, out, _ = run_mux(["capture-pane", "-t", pane, "-p"])
    if code != 0 or not out.strip():
        return False
    lines = [l.rstrip() for l in out.splitlines() if l.strip()]
    if not lines:
        return False
    last = lines[-1].strip()
    m = re.match(r"^>\s?(.*)$", last)
    if m and m.group(1).strip():
        return True
    return False


def send_to_target(pane, text, mark_waiting=True, is_special_key=False, force=False):
    """
    Gửi lệnh vào cửa sổ AI. Trả về trạng thái:
      "sent" | "refused" (IDE: đang focus vào editor, không dán mù) | "no_input" | "error"
    force=True: bỏ qua kiểm tra UIA, dán thẳng vào phần tử đang focus (lệnh /force)
    """
    safe = sanitize_filename(pane)
    if mark_waiting:
        now_ts = time.time()
        is_busy_now = get_pane_live_status(pane).startswith("⚙️ Đang thực thi")
        # Ghi cờ dưới MỌI bí danh mà hook có thể dùng cho cửa sổ này
        # (agentId, "<node>:<folder>", "term:<folder>", ...) để hook luôn tìm thấy.
        aliases = pane_state_aliases(pane)
        for name in aliases:
            increment_waiting_count(name, is_busy=is_busy_now)
            try:
                with open(os.path.join(STATE_DIR, f"from_tg_dispatch_{name}.flag"),
                          "w", encoding="utf-8") as f:
                    f.write(str(now_ts))
            except Exception:
                pass
        with open(FROM_TG_DISPATCH_FILE, "w", encoding="utf-8") as f:
            f.write(str(now_ts))
        if not is_special_key and not is_busy_now:
            ack_payload = {"pane": pane, "text": text, "ts": now_ts}
            for name in aliases:
                try:
                    with open(os.path.join(STATE_DIR, f"ack_pending_{name}.json"), "w",
                              encoding="utf-8") as f:
                        json.dump(ack_payload, f, ensure_ascii=False)
                except Exception:
                    pass

    # ---- Backend Paseo: gửi lệnh tới agent qua CLI `paseo send` ----
    if is_paseo_target(pane):
        if is_special_key:
            ok, msg = send_to_paseo_agent(pane, text, force=False)
            return "sent" if ok else "error"
        ok, msg = send_to_paseo_agent(pane, text, force=force)
        if ok:
            # ACK tức thì: `paseo send` trả về ngay khi lệnh đã được đưa vào agent
            return "sent"
        return "error"

    if is_mux_pane(pane):
        # Tự động thoát copy-mode (nếu đang cuộn màn hình trong tmux) để con trỏ quay về ô nhập
        run_mux(["copy-mode", "-q", "-t", pane])
        if is_special_key:
            if text in ("y", "n", "1", "2", "3", "4"):
                run_mux(["send-keys", "-t", pane, "-l", text])
                time.sleep(0.2)
                run_mux(["send-keys", "-t", pane, "Enter"])
            else:
                run_mux(["send-keys", "-t", pane, text])
        else:
            clean = text.replace("\n", " ").strip()
            # XÓA text còn sót trong ô nhập trước khi dán (nếu không, lệnh mới bị NỐI vào
            # text cũ, ví dụ "send-keys -t %0kiểm tra cho tôi...").
            if mux_input_has_text(pane):
                run_mux(["send-keys", "-t", pane, "C-u"])
                time.sleep(0.2)
            run_mux(["send-keys", "-t", pane, "-l", clean])
            time.sleep(0.25)
            run_mux(["send-keys", "-t", pane, "Enter"])
        # Kiểm tra kết quả: multiplexer có thực sự nhận lệnh không
        code, out, err = run_mux(["capture-pane", "-t", pane, "-p"])
        if code != 0:
            log_line(
                f"send_mux thất bại cho pane [{pane}]: mã {code} | {err.strip()[:160]}",
                level="ERROR",
            )
            return "error"
        return "sent"

    if IS_WINDOWS:
        # Định danh kiểu "term:<folder>" (hook sinh ra) cần được ánh xạ về PID cửa sổ thật
        target_pid = resolve_window_pid(pane)
        if not target_pid:
            log_line(f"send_to_target: không ánh xạ được pane [{pane}] -> PID", level="ERROR")
            return "error"
        proc_name = get_process_name_for_pid(target_pid)
        is_ide = any(p in proc_name for p in IDE_PROCESS_NAMES) if proc_name else False
        log_line(f"send_to_target: pane=[{pane}] pid={target_pid} proc={proc_name or '?'} ide={is_ide} special={is_special_key}")

        if is_ide and not is_special_key:
            if force:
                send_to_ide_window_force(target_pid, text)
                return "sent"
            # IDE (Electron): phải focus đúng ô chat AI, nếu không thì TUỒNG (không dán mù vào editor)
            status, detail = send_to_ide_window(target_pid, text)
            log_line(f"send_to_ide_window: status={status} detail={detail}")
            if status in ("refused", "no_input"):
                # Dọn gợi cũ (>10 phút) để không phình bộ nhớ
                now_ts = time.time()
                for old_pane in [k for k, v in IDE_REFUSE_HINT.items() if now_ts - v.get("ts", 0) > 600]:
                    IDE_REFUSE_HINT.pop(old_pane, None)
                IDE_REFUSE_HINT[pane] = {"ts": now_ts, "status": status, "detail": detail}
                return status
            if status == "sent":
                return "sent"
            # status == error -> thử cách cũ (AppActivate) làm phương án cuối
        send_to_windows_terminal(str(target_pid), text, is_special_key=is_special_key)
        return "sent"



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
    if not has_mux():
        return
    targets = list_targets()
    active_default = get_active_pane()

    for pane_line in targets:
        pid, label = format_pane_summary(pane_line)
        if not is_mux_pane(pid):
            continue  # đây là cửa sổ Windows, không phải pane multiplexer
        safe = sanitize_filename(pid)
        alert_file = os.path.join(STATE_DIR, f"quota_alert_{safe}.json")

        code_rc, out, _ = run_mux(["capture-pane", "-t", pid, "-p"])
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
    if not has_mux():
        return
    targets = list_targets()
    active_default = get_active_pane()
    is_away = os.path.exists(AWAY_FLAG_FILE) or is_screen_locked()

    for pane_line in targets:
        pid, label = format_pane_summary(pane_line)
        if not is_mux_pane(pid):
            continue  # đây là cửa sổ Windows, không phải pane multiplexer
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




def build_ide_refuse_notice(token, chat_id, pane):
    """
    Nếu lệnh vừa bị từ chối do IDE đang focus vào trình soạn thảo -> gửi hướng dẫn cụ thể.
    Trả về True nếu đã gửi thông báo.
    """
    hint = IDE_REFUSE_HINT.pop(pane, None)
    if not hint:
        return False
    detail = hint.get("detail", "")
    if hint.get("status") == "refused":
        reason = (
            "Ô nhập AI của IDE đang **chưa được focus** (con trỏ đang ở trình soạn thảo mã).\n"
            f"   _Phần tử đang focus: `{detail}`_\n\n"
            "👉 **Cách xử lý:** trên PC, **bấm vào ô chat AI** của IDE (ô *Ask anything*) một cái, "
            "rồi gửi lại lệnh từ Telegram.\n"
            "   _Nếu vẫn lỗi, dùng `/force <lệnh>` để ép dán vào ô đang focus._\n"
            "   _(Công cụ cố tình KHÔNG dán mù để tránh ghi đè file mã nguồn của bạn.)_"
        )
    else:
        reason = (
            "Chưa tìm thấy ô nhập AI nào trong cửa sổ IDE (IDE chưa lộ cây UI Automation).\n"
            f"   _Phần tử đang focus: `{detail}`_\n\n"
            "👉 **Cách xử lý:** trên PC, **bấm vào ô chat AI** của IDE một cái, rồi gửi lệnh bằng "
            "`/force <lệnh>` (ép dán vào ô đang focus)."
        )
    scr = capture_screen(pane)
    scr_lines = [line for line in scr.splitlines() if line.strip()]
    scr_tail = "\n".join(scr_lines[-10:]) if scr_lines else "(Trống)"
    warn_msg = (
        f"⚠️ *Lệnh CHƯA được gửi vào AI tại `[{pane}]`*\n\n"
        f"{reason}\n\n"
        f"📺 *Trạng thái hiện tại của `[{pane}]`:*\n{scr_tail}"
    )
    reply_markup = {
        "inline_keyboard": [
            [
                {"text": "📸 Xem màn hình", "callback_data": f"screen:{pane}"},
                {"text": "📋 Đổi cửa sổ", "callback_data": "list"},
            ],
        ]
    }
    photo = capture_screen_photo(pane)
    if photo:
        tg_send_photo(token, chat_id, photo, caption=f"⚠️ *[{pane}]* — Lệnh chưa vào ô chat AI")
    tg_send(token, chat_id, warn_msg, reply_markup=reply_markup)
    return True


def verify_ai_received_command(token, chat_id, pane, text):
    """
    Chờ Hook 'start' của AI xác nhận đã nhận lệnh trước khi thực thi.
    - Nếu Hook nhận được lệnh: Hook tự gửi '✅ OK — Đã nhận lệnh tại cửa sổ [pane]' và xóa file ack_pending.
    - Nếu sau 3s chưa thấy Hook phản hồi: Thử gửi bồi thêm 1 phím Enter (phòng TUI bị trễ khi paste).
    - Nếu sau 5s vẫn không có phản hồi từ Hook: Báo cảnh báo ngay kèm màn hình hiện tại (do gửi nhầm tmux hoặc con trỏ không ở ô nhập).
    """
    safe = sanitize_filename(pane)
    ack_file = os.path.join(STATE_DIR, f"ack_pending_{safe}.json")
    # Hook có thể xoá file theo tên canonical ("session_0_1") -> theo dõi cả 2 tên
    ack_files = [ack_file]
    canonical = get_mux_canonical_id(pane)
    if canonical:
        alt = os.path.join(STATE_DIR, f"ack_pending_{sanitize_filename(canonical)}.json")
        if alt != ack_file:
            ack_files.append(alt)

    def acked():
        return not any(os.path.exists(f) for f in ack_files)

    for _ in range(24):  # 6.0 giây đầu (AI có thể cần vài giây để bắt đầu lượt)
        time.sleep(0.25)
        if acked():
            return True

    # Thử gửi bồi 1 phím Enter nếu chữ đã vào ô nhập nhưng chưa submit
    if has_mux():
        run_mux(["send-keys", "-t", pane, "Enter"])

    for _ in range(12):  # Thêm 3.0 giây (tổng 9.0 giây)
        time.sleep(0.25)
        if acked():
            return True

    # Nếu AI đang BẬN thì lệnh đã vào hàng đợi -> không phải lỗi, chỉ là chưa xong
    live_st = get_pane_live_status(pane)
    if live_st.startswith("⚙️ Đang thực thi") or live_st.startswith("⏳") or live_st.startswith("👀"):
        for f in ack_files:
            try:
                os.remove(f)
            except OSError:
                pass
        tg_send(
            token, chat_id,
            f"⏳ *OK — Đã gửi vào hàng đợi của `[{pane}]`*\n"
            f"AI đang bận ({live_st}) nên lệnh sẽ chạy ngay khi lượt hiện tại xong.",
        )
        return False

    # Quá 9 giây mà Hook start của AI không chạy -> Con trỏ không ở ô nhập hoặc nhầm cửa sổ!
    for f in ack_files:
        try:
            os.remove(f)
        except OSError:
            pass

    # Nếu lệnh vừa bị TỪ CHỐI vì IDE đang focus vào editor -> giải thích chính xác
    if build_ide_refuse_notice(token, chat_id, pane):
        return False

    scr = capture_screen(pane)
    scr_lines = [line for line in scr.splitlines() if line.strip()]
    scr_tail = "\n".join(scr_lines[-16:]) if scr_lines else "(Trống)"

    # Mô tả đúng loại đích: pane multiplexer hay cửa sổ Windows
    if is_mux_pane(pane):
        target_kind = f"pane `{get_mux_bin()}`"
        reasons = (
            f"1️⃣ Con trỏ trong `{get_mux_bin()}` đang **không ở ô nhập** "
            "(đang mở hộp thoại, menu, hoặc AI đang bận chạy tác vụ khác).\n"
            f"2️⃣ Hoặc đang chọn **nhầm pane**."
        )
    else:
        target_kind = "cửa sổ Windows"
        reasons = (
            "1️⃣ Cửa sổ này **không phải ô terminal** (nó là IDE/Windows Terminal chạy ngoài multiplexer)\n"
            "   → lệnh được dán bằng clipboard, nên AI có thể chưa nhận kịp.\n"
            "2️⃣ Con trỏ trong cửa sổ **chưa ở ô nhập** của AI.\n"
            "3️⃣ Hoặc cửa sổ này **không có AI nào đang chạy**."
        )

    warn_msg = (
        f"⚠️ *CẢNH BÁO: AI tại `[{pane}]` CHƯA nhận được lệnh!*\n"
        f"Đã gửi `{text[:80]}` vào {target_kind} nhưng sau 5 giây Hook của AI không phản hồi.\n\n"
        f"👉 *Nguyên nhân có thể:*\n{reasons}\n\n"
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
    # Gửi kèm ảnh chụp cửa sổ (Windows) để thấy ngay tình trạng thực tế
    photo = capture_screen_photo(pane)
    if photo:
        tg_send_photo(token, chat_id, photo, caption=f"⚠️ *[{pane}]* — AI chưa nhận được lệnh")
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


HEARTBEAT_FILE = os.path.join(STATE_DIR, "daemon_heartbeat.txt")
LOCK_FILE = os.path.join(STATE_DIR, "daemon.pid")
START_TS = time.time()


def is_pid_alive(pid):
    """
    Kiểm tra tiến trình còn sống.
    - KHÔNG dùng os.kill trên Windows (sẽ gọi TerminateProcess => giết process).
    - KHÔNG spawn `tasklist.exe` (console app) để tránh cửa sổ nhấp nháy -> dùng Win32 API.
    """
    if not pid or pid <= 0:
        return False
    if IS_WINDOWS:
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # SYNCHRONIZE (0x100000) - quyền tối thiểu, hầu như luôn được cấp
            handle = kernel32.OpenProcess(0x100000, False, int(pid))
            if not handle:
                handle = kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


def acquire_single_instance_lock():
    """
    Chống chạy nhiều daemon cùng lúc (2 tiến trình cùng poll getUpdates sẽ đụng nhau).
    Trả về False nếu đã có daemon khác đang chạy.
    """
    try:
        if os.path.exists(LOCK_FILE):
            old_pid = 0
            try:
                with open(LOCK_FILE, "r", encoding="utf-8") as f:
                    old_pid = int(f.read().strip() or 0)
            except Exception:
                old_pid = 0
            if old_pid and old_pid != os.getpid() and is_pid_alive(old_pid):
                return False
        with open(LOCK_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        return True
    except Exception:
        return True


def release_single_instance_lock():
    try:
        with open(LOCK_FILE, "r", encoding="utf-8") as f:
            cur = int(f.read().strip() or 0)
        if cur == os.getpid():
            os.remove(LOCK_FILE)
    except Exception:
        pass


def write_heartbeat():
    """Ghi dấu hiệu 'daemon còn sống' để watchdog / chẩn đoán dễ kiểm tra."""
    try:
        with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "pid": os.getpid(), "version": VERSION}))
    except Exception:
        pass


def build_diag_text(token, chat_id):
    """Nội dung lệnh /ping + /diag: giúp biết daemon có sống và cấu hình ra sao."""
    mux_bin = get_mux_bin()
    mux_ok = has_mux()
    targets = list_targets()
    active = get_active_pane()
    uptime = int(time.time() - START_TS)
    lines = [
        f"🩺 *Chẩn đoán daemon (v{VERSION})*",
        f"⏱ Uptime: {uptime}s | PID: {os.getpid()}",
        f"🪟 OS: {'Windows' if IS_WINDOWS else ('macOS' if IS_MAC else 'Linux')}",
        f"🧩 Multiplexer: `{mux_bin}` → {'✅ có' if mux_ok else '❌ không có (dùng chế độ Windows native)'}",
        f"📂 State dir: `{STATE_DIR}`",
        f"🎯 Đang chọn: `{active}`",
        f"🪟 Số cửa sổ phát hiện được: {len(targets)}",
    ]
    for t in targets[:10]:
        pid, label = format_pane_summary(t)
        lines.append(f"   • `{pid}` — {label}")
    if IS_WINDOWS:
        lines.append("📸 Chụp ảnh cửa sổ: dùng `📸 Xem màn hình` (gửi ảnh thật qua sendPhoto)")
    return "\n".join(lines)


def main():
    cfg = load_config()
    token = cfg["bot_token"]
    chat_id = str(cfg["chat_id"])
    offset = 0

    # Chống chạy trùng: nếu đã có daemon khác đang giữ file khóa thì thoát im lặng
    if not acquire_single_instance_lock():
        return

    log_line(f"=== Daemon khởi động v{VERSION} | PID {os.getpid()} | state={STATE_DIR} | mux={get_mux_bin()} ===")
    write_heartbeat()

    register_bot_menu(token)

    try:
        res = tg_api(token, "getUpdates", {"timeout": 1})
        if res.get("ok") and res.get("result"):
            offset = res["result"][-1]["update_id"] + 1
    except Exception as e:
        log_line(f"Khởi tạo getUpdates thất bại: {e}", level="ERROR")

    try:
        while True:
            try:
                cleanup_orphan_state_files()
                check_tmux_quota_limits(token, chat_id)
                check_tmux_prompts_and_queues(token, chat_id)
                write_heartbeat()
                res = tg_api(token, "getUpdates", {"offset": offset, "timeout": 15})
                for item in res.get("result", []):
                    # QUAN TRỌNG: cập nhật offset TRƯỚC khi xử lý.
                    # Nếu để sau, một update lỗi sẽ khiến daemon lặp lại vô hạn trên cùng update
                    # và mọi tin nhắn/nút bấm về sau đều bị "kẹt" (không phản hồi).
                    offset = item["update_id"] + 1
                    try:
                        handle_update(item, token, chat_id)
                    except Exception as e:
                        log_exception(f"Lỗi khi xử lý update {item.get('update_id')}: {e}")
                        continue
            except Exception as e:
                log_exception(f"Lỗi vòng lặp chính: {e}")
                time.sleep(2)
    finally:
        release_single_instance_lock()


def handle_update(item, token, chat_id):
    """Xử lý MỘT update (callback query hoặc tin nhắn). Luôn bọc try/except ở tầng caller."""
    # 1. Xử lý khi bấm nút Inline Keyboard
    if "callback_query" in item:
        cb = item["callback_query"]
        if str(cb.get("from", {}).get("id")) != chat_id:
            return
        data = cb.get("data", "")

        if data == "cancel":
            tg_answer(token, cb["id"], "Đã hủy thao tác")
            tg_send(token, chat_id, "✖️ Đã hủy thao tác.")

        elif data == "mode:away":
            with open(AWAY_FLAG_FILE, "w", encoding="utf-8") as f:
                f.write(str(time.time()))
            tg_answer(token, cb["id"], "Đã bật chế độ Away")
            tg_send(token, chat_id, "🚶‍♂️ Đã chuyển sang chế độ *Away (Vắng mặt)* — AI sẽ tự động gửi thông báo về Telegram.")

        elif data == "mode:here":
            if os.path.exists(AWAY_FLAG_FILE):
                try:
                    os.remove(AWAY_FLAG_FILE)
                except OSError:
                    pass
            tg_answer(token, cb["id"], "Đã bật chế độ Here")
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
            tg_answer(token, cb["id"], "Vui lòng xác nhận...")
            ask_key_confirmation(token, chat_id, pane, k, label_map.get(k, k))

        elif data.startswith("confirmkey:"):
            _, rest = data.split(":", 1)
            pane, k = rest.rsplit(":", 1)
            mark = k in ("y", "n", "1", "2", "3", "4", "Enter")
            send_to_target(pane, k, mark_waiting=mark, is_special_key=True)
            tg_answer(token, cb["id"], f"Đã gửi {k} vào {pane}")
            tg_send(token, chat_id, f"✅ OK — Đã gửi lựa chọn `{k}` vào `[{pane}]`, AI đang tiếp tục thực thi!")

        elif data.startswith("screen:"):
            _, pane = data.split(":", 1)
            tg_answer(token, cb["id"], "Đang chụp màn hình...")
            send_screen_snapshot(token, chat_id, pane)

        elif data == "list":
            tg_answer(token, cb["id"], "Danh sách cửa sổ")
            send_list_menu(token, chat_id, get_active_pane())

        elif data.startswith("reply:"):
            _, pane = data.split(":", 1)
            valid_ids = [t.split(" | ")[0] for t in list_targets()]
            # Định danh term:<folder> (hook sinh ra ngoài multiplexer) vẫn dùng được
            # nếu ánh xạ được sang cửa sổ Windows thật.
            is_term_pane = pane.startswith("term:")
            if is_term_pane:
                is_term_pane = bool(resolve_window_pid(pane, fallback_default=False))
            if valid_ids and pane not in valid_ids and not is_term_pane:
                tg_answer(token, cb["id"], f"Cửa sổ {pane} không còn tồn tại")
                tg_send(
                    token, chat_id,
                    f"⚠️ Cửa sổ `[{pane}]` không còn tồn tại (đã đóng hoặc đổi tên trong `{get_mux_bin()}`).\n👉 Dưới đây là danh sách cửa sổ đang chạy:",
                )
                send_list_menu(token, chat_id, get_active_pane())
                return
            set_one_shot_reply_pane(pane)
            tg_answer(token, cb["id"], f"Trả lời vào {pane}")
            tg_send(
                token, chat_id,
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
            tg_answer(token, cb["id"], f"Đã chọn {pane}")
            tg_send(token, chat_id, f"✅ Đã chuyển điều khiển sang: `{pane}`")
        return

    # 2. Xử lý tin nhắn văn bản từ bạn
    msg = item.get("message", {})
    if str(msg.get("chat", {}).get("id")) != chat_id or "text" not in msg:
        return
    handle_text_message(msg, token, chat_id)


def handle_text_message(msg, token, chat_id):
    text = msg.get("text", "").strip()
    cmd_word = text.split()[0].lower().split("@")[0] if text else ""
    default_pane = get_active_pane()
    reply_pane = extract_reply_target_pane(msg)
    pane = reply_pane if reply_pane else default_pane

    if reply_pane and reply_pane.startswith("term:"):
        # Định danh kiểu term:<folder> (hook sinh ra khi không có multiplexer):
        # vẫn gửi được nếu ánh xạ được sang cửa sổ Windows thật.
        resolved = resolve_window_pid(reply_pane)
        if not resolved:
            tg_send(
                token, chat_id,
                f"⚠️ Không tìm thấy cửa sổ nào ứng với `[{reply_pane}]` trên máy này.\n👉 Cửa sổ đang chọn mặc định của bạn vẫn là `[{default_pane}]`.\n💡 Gõ `/list` để xem danh sách cửa sổ hiện có.",
            )
            send_list_menu(token, chat_id, default_pane)
            return

    valid_ids = [t.split(" | ")[0] for t in list_targets()]
    if reply_pane and valid_ids and reply_pane not in valid_ids and not reply_pane.startswith("term:"):
        tg_send(
            token, chat_id,
            f"⚠️ Cửa sổ `[{reply_pane}]` trong tin nhắn cũ không còn tồn tại (đã đóng hoặc đổi tên trong `{get_mux_bin()}`).\n👉 Cửa sổ đang chọn mặc định của bạn là `[{default_pane}]`.",
        )
        send_list_menu(token, chat_id, default_pane)
        return

    if cmd_word in ("/start", "/help"):
        help_text = (
            f"🤖 *Điều khiển AI (agy / claude / codex) qua `{get_mux_bin()}` (v{VERSION})*\n\n"
            f"📡 Trạng thái: {get_mode_status_text()}\n"
            f"🖥 Cửa sổ đang chọn: `{pane}`\n\n"
            "• Nhắn tin bất kỳ: Gửi thẳng vào cửa sổ AI đang chọn và nhận câu trả lời qua Telegram.\n"
            "• `/list` : Xem danh sách cửa sổ AI & chọn nhanh.\n"
            "• `/screen` : Xem màn hình hiện tại (gửi ảnh chụp cửa sổ trên Windows).\n"
            "• `/away` : Bật chế độ Vắng mặt (tự động báo Telegram).\n"
            "• `/here` : Bật chế độ Ngồi máy (tắt tự động báo).\n"
            "• `/yes`, `/no`, `/enter`, `/esc`, `/ctrlc` : Gửi phím điều khiển (có hỏi xác nhận).\n"
            "• `/force <lệnh>` : Ép dán lệnh vào ô đang focus của cửa sổ IDE (khi không tự tìm thấy ô chat AI).\n"
            "• `/shot [url]` : Chụp ảnh giao diện web (mặc định dò dev server trên localhost).\n"
            "• `/ping` : Kiểm tra daemon còn sống + xem cấu hình.\n"
        )
        tg_send(
            token, chat_id, help_text,
            reply_markup={
                "inline_keyboard": [[
                    {"text": "🚶‍♂️ Bật Away", "callback_data": "mode:away"},
                    {"text": "💻 Bật Here", "callback_data": "mode:here"},
                ]]
            },
        )

    elif cmd_word in ("/ping", "/diag", "/status2"):
        tg_send(token, chat_id, build_diag_text(token, chat_id))

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

    elif cmd_word == "/force" and len(text.split(None, 1)) > 1:
        # Ép dán vào phần tử đang focus của cửa sổ IDE (dùng khi UIA không nhận ra ô chat AI)
        forced_text = text.split(None, 1)[1].strip()
        status = send_to_target(pane, forced_text, mark_waiting=True, is_special_key=False, force=True)
        if status == "sent":
            tg_send(token, chat_id, f"⚡ Đã ép gửi vào ô đang focus của `[{pane}]`:\n\n`{forced_text[:200]}`")
        else:
            tg_send(token, chat_id, f"⚠️ Không ép gửi được vào `[{pane}]` (trạng thái: {status}).")

    elif cmd_word in ("/screen", "/status"):
        send_screen_snapshot(token, chat_id, pane)

    elif cmd_word in ("/shot", "/web", "/screenshot"):
        # Chụp ảnh giao diện web (mặc định dò dev server trên localhost)
        parts = text.split(None, 1)
        url = parts[1].strip() if len(parts) > 1 else ""
        if not url:
            url = detect_local_dev_url()
        if not url:
            tg_send(
                token, chat_id,
                "🌐 *Chụp ảnh giao diện web*\n\n"
                "Cách dùng: `/shot http://localhost:3000`\n"
                "_(Nếu bỏ trống, tool sẽ dò các cổng dev phổ biến: "
                + ", ".join(str(p) for p in COMMON_DEV_PORTS) + ")_",
            )
            return
        if not url.startswith("http://") and not url.startswith("https://"):
            url = "http://" + url
        tg_send(token, chat_id, f"📸 Đang chụp `{url}`…")
        png = capture_url_png(url)
        if png:
            tg_send_photo(token, chat_id, png, caption=f"🌐 *{url}*")
        else:
            tg_send(
                token, chat_id,
                f"⚠️ Không chụp được `{url}`.\n"
                "Kiểm tra: trang đã chạy chưa? Máy này có Edge/Chrome không?\n"
                "(chi tiết trong `daemon.err.log`)",
            )

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
                    token, chat_id,
                    f"✅ *OK — Đã gửi lựa chọn `{choice}` vào bảng hỏi của `[{pane}]`!*\n⏳ AI đang tiếp tục thực thi...",
                )
            else:
                tg_send(
                    token, chat_id,
                    f"⚠️ *Cửa sổ `[{pane}]` đang dừng ở bảng hỏi lựa chọn (`1/2/3` hoặc `y/n`)!*\n"
                    f"👉 Hãy chọn phương án bên dưới để giải quyết bảng hỏi trước khi nhập câu lệnh mới:\n\n"
                    f"📺 *Hộp thoại hiện tại:*\n{snippet}",
                    reply_markup=build_interactive_prompt_buttons(pane),
                )
            return

        # Case B: Cửa sổ đang bận thực thi lệnh trước -> Bơm vào queue tự nhiên của AI và pong báo "đang chờ xử lý"
        live_st = get_pane_live_status(pane)
        if live_st.startswith("⚙️ Đang thực thi"):
            send_to_target(pane, text, mark_waiting=True, is_special_key=False)
            tg_send(
                token, chat_id,
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
            return

        # Case C: Cửa sổ đang sẵn sàng nhận lệnh
        status = send_to_target(pane, text, mark_waiting=True, is_special_key=False)
        if status in ("refused", "no_input"):
            # Lệnh không vào được ô chat AI -> báo hướng dẫn ngay, khỏi chờ 5s
            build_ide_refuse_notice(token, chat_id, pane)
            return
        if is_paseo_target(pane):
            # Paseo: `paseo send` đã trả về ngay => ACK tức thì, không cần chờ hook
            tg_send(
                token, chat_id,
                f"⏳ *OK — Đã gửi lệnh vào agent Paseo `[{pane}]`*\n"
                f"Agent sẽ xử lý; câu trả lời sẽ về Telegram qua Hook.",
            )
            return
        verify_ai_received_command(token, chat_id, pane, text)




if __name__ == "__main__":
    main()
