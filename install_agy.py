#!/usr/bin/env python3
"""
install_agy.py — Tìm & cài Antigravity CLI (lệnh `agy`) để điều khiển AI từ Telegram.

Vì sao cần CLI?
  Ô chat AI bên trong IDE (Antigravity/VS Code) là WebView của Electron → rất khó điều khiển
  bằng tự động hoá (UI Automation không lộ ô nhập). Còn CLI `agy` chạy trong terminal thì:
    • nhận text qua stdin một cách chắc chắn,
    • hook PreInvocation/Stop chắc chắn bắn,
    • `/screen` đọc được buffer terminal, bắt được bảng hỏi 1/2/3, monitor được quota.

Cách dùng:
    python install_agy.py            # tìm `agy`; nếu thấy -> thêm thư mục vào PATH
    python install_agy.py --check    # chỉ kiểm tra, không sửa gì
    python install_agy.py --npm      # cài qua npm (tên package lấy từ config.json)
    python install_agy.py --npm <package>   # cài qua npm với package chỉ định

Không sửa PATH của hệ thống nếu không tìm thấy `agy` thật.
"""
import os
import sys
import json
import shutil
import subprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
IS_WINDOWS = sys.platform == "win32"

OK = "\033[92m✅\033[0m"
BAD = "\033[91m❌\033[0m"
WARN = "\033[93m⚠️ \033[0m"


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def find_agy_in_path():
    """`agy` đã có trong PATH chưa?"""
    exe = shutil.which("agy")
    if exe:
        return os.path.abspath(exe)
    # Windows: kiểm tra cả các shim .cmd/.bat/.ps1 trong thư mục npm
    for name in ("agy.cmd", "agy.bat", "agy.ps1", "agy.exe"):
        for base in filter(None, [
            os.environ.get("APPDATA"),
            os.environ.get("LOCALAPPDATA"),
            os.environ.get("ProgramFiles"),
        ]):
            cand = os.path.join(base, "npm", name)
            if os.path.exists(cand):
                return cand
    return ""


def _dirs_of_interest():
    """Các thư mục có thể chứa Antigravity IDE / CLI."""
    dirs = []
    local = os.environ.get("LOCALAPPDATA", "")
    roaming = os.environ.get("APPDATA", "")
    programfiles = os.environ.get("ProgramFiles", "")
    if local:
        dirs.append(os.path.join(local, "Programs", "Antigravity"))
        dirs.append(os.path.join(local, "Programs", "antigravity"))
        dirs.append(os.path.join(local, "Antigravity"))
    if roaming:
        dirs.append(os.path.join(roaming, "npm"))
        dirs.append(os.path.join(roaming, "Antigravity"))
    if programfiles:
        dirs.append(os.path.join(programfiles, "Antigravity"))
    return [d for d in dirs if os.path.isdir(d)]


def find_agy_in_ide(max_depth=4):
    """
    Tìm file thực thi tên agy* bên trong thư mục cài IDE (Antigravity thường đi kèm CLI).
    Trả về đường dẫn đầy đủ hoặc chuỗi rỗng.
    """
    names = ("agy.exe", "agy.cmd", "agy.bat", "agy.ps1")
    for root_dir in _dirs_of_interest():
        base_depth = root_dir.rstrip(os.sep).count(os.sep)
        for cur, dirs, files in os.walk(root_dir):
            if cur.count(os.sep) - base_depth >= max_depth:
                dirs[:] = []
                continue
            # Bỏ qua các thư mục con không liên quan
            dirs[:] = [d for d in dirs if not d.lower().startswith(("locale", "locales", "node_modules"))]
            for n in names:
                if n in files:
                    return os.path.join(cur, n)
    return ""


def find_agy_via_antigravity_launcher():
    """
    Antigravity IDE có thể cài qua 'Antigravity CLI' launcher.
    Thử tạo shim tới launcher nếu tìm thấy.
    """
    # 1) Tìm trong thư mục ứng dụng của Windows Store / Start Menu
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = []
    if local:
        candidates.append(os.path.join(local, "Microsoft", "WindowsApps"))
    for folder in candidates:
        if not os.path.isdir(folder):
            continue
        try:
            for entry in os.listdir(folder):
                if entry.lower().startswith("agy"):
                    candidates_path = os.path.join(folder, entry)
                    if os.path.isfile(candidates_path):
                        return candidates_path
        except Exception:
            pass
    return ""


def add_to_user_path(directory):
    """Thêm thư mục vào PATH của USER (vĩnh viễn, không cần đăng nhập lại)."""
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE
        )
        try:
            current, _ = winreg.QueryValueEx(key, "Path")
        except FileNotFoundError:
            current = ""
        parts = [p for p in current.split(";") if p]
        if directory.lower() in [p.lower() for p in parts]:
            return True
        parts.append(directory)
        winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, ";".join(parts))
        winreg.CloseKey(key)
        # Cập nhật PATH của tiến trình hiện tại luôn
        os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + directory
        return True
    except Exception as e:
        print(f"{WARN} Không sửa được PATH: {e}")
        return False


def verify_agy(exe):
    """Chạy `agy --version` để chắc chắn lệnh chạy được."""
    try:
        res = subprocess.run(
            [exe, "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=30,
            check=False,
        )
        out = (res.stdout or "").strip()
        return res.returncode == 0, out
    except Exception as e:
        return False, str(e)


def install_via_npm(package=""):
    """Cài `agy` qua npm (global)."""
    if not package:
        package = (load_config().get("install_agy") or {}).get("package", "")
    if not package:
        print(f"{WARN} Chưa biết tên package npm của Antigravity CLI.")
        print("   Cài tay rồi chạy lại, hoặc chỉ định:")
        print("     python install_agy.py --npm <tên-package>")
        print("   (VD: python install_agy.py --npm @google/gemini-cli  — nếu Antigravity CLI dựa trên Gemini CLI)")
        return False
    npm = shutil.which("npm")
    if not npm:
        print(f"{BAD} Không tìm thấy `npm` trong PATH. Cài Node.js trước: https://nodejs.org")
        return False
    print(f"→ Đang cài `{package}` bằng npm ( có thể mất 1-2 phút )…")
    try:
        res = subprocess.run([npm, "install", "-g", package], check=False, timeout=900)
    except Exception as e:
        print(f"{BAD} Lỗi npm: {e}")
        return False
    if res.returncode != 0:
        print(f"{BAD} npm cài đặt thất bại (mã {res.returncode}).")
        return False
    print(f"{OK} npm đã cài `{package}`.")
    return True


def main():
    args = [a for a in sys.argv[1:]]
    check_only = "--check" in args

    print("=" * 64)
    print(" Antigravity CLI (agy) — tìm & cài")
    print("=" * 64)

    # 1) Đã có trong PATH?
    found = find_agy_in_path()
    if found:
        print(f"{OK} Đã có `agy`: {found}")
        ok, out = verify_agy(found)
        if ok:
            print(f"{OK} `agy --version` -> {out.splitlines()[0] if out else 'OK'}")
        else:
            print(f"{WARN} `agy --version` lỗi: {out[:200]}")
        print("\n=> Không cần cài gì thêm. Dùng: mở terminal, chạy `agy` trong thư mục dự án.")
        return 0

    print(f"{WARN} Chưa có `agy` trong PATH.")

    # 2) Tìm trong thư mục cài IDE
    bundled = find_agy_in_ide()
    if bundled:
        folder = os.path.dirname(bundled)
        print(f"{OK} Tìm thấy CLI đi kèm IDE: {bundled}")
        if not check_only:
            if add_to_user_path(folder):
                print(f"{OK} Đã thêm `{folder}` vào PATH của USER (áp dụng từ lần đăng nhập sau).")
            else:
                print(f"{WARN} Hãy tự thêm PATH: setx PATH \"%PATH%;{folder}\"")
        ok, out = verify_agy(bundled)
        if ok:
            print(f"{OK} `agy --version` -> {out.splitlines()[0] if out else 'OK'}")
        else:
            print(f"{WARN} Chạy `{bundled} --version` báo: {out[:200]}")
        print("\n=> Mở terminal mới, chạy `agy` trong thư mục dự án.")
        return 0

    # 3) Launcher trong WindowsApps
    launcher = find_agy_via_antigravity_launcher()
    if launcher:
        print(f"{OK} Tìm thấy launcher: {launcher}")
        folder = os.path.dirname(launcher)
        if not check_only:
            add_to_user_path(folder)
        print("=> Mở terminal mới và thử chạy `agy`.")
        return 0

    # 4) Chưa tìm thấy -> gợi ý cài
    print(f"{WARN} Không tìm thấy `agy` trong máy (không có npm global, không thấy trong thư mục IDE).")
    if "--npm" in args:
        idx = args.index("--npm")
        package = args[idx + 1] if len(args) > idx + 1 and not args[idx + 1].startswith("--") else ""
        if install_via_npm(package):
            found = find_agy_in_path()
            if found:
                print(f"{OK} Cài xong: {found}")
                return 0
        return 1

    print("\nCách cài (chọn 1):")
    print("  a) Nếu Antigravity IDE đã cài: mở IDE → Settings → cài 'Antigravity CLI' (nếu có mục này),")
    print("     rồi chạy lại: python install_agy.py")
    print("  b) Nếu Antigravity CLI là bản npm:")
    print("     python install_agy.py --npm <tên-package-chính-thức>")
    print("  c) Tự cài tay rồi chạy lại script này để nó thêm vào PATH.")
    print("\nSau khi có `agy`, hãy chạy nó trong terminal ở thư mục dự án:")
    print("     cd D:\\Vivas\\myTV_landingpage")
    print("     agy")
    return 1


if __name__ == "__main__":
    sys.exit(main())
