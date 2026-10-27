# 🤖 AI Telegram Remote Bridge (`agy` / `claude` / `codex`)

Bộ công cụ giúp kết nối các AI CLI (**Antigravity `agy`**, **Claude Code `claude`**, **OpenAI Codex `codex`**) với **Telegram** để:
1. **Nhận thông báo tự động thông minh** trên điện thoại khi AI làm xong việc hoặc cần hỏi ý kiến.
2. **Chat & điều khiển AI 2 chiều** từ xa ngay trên Telegram (hỗ trợ **Windows 10/11**, **Ubuntu/Linux**, và **macOS**).

---

## 📋 Cơ chế thông báo thông minh (Không gây spam điện thoại)

Hệ thống chỉ gửi tin nhắn về Telegram khi rơi vào **1 trong 4 trường hợp**:
1. **Bạn chủ động nhắn hỏi AI từ Telegram** (hoặc khi bạn gõ trên Terminal nhưng thêm chữ `tg` ở đầu/cuối câu, ví dụ: `tg kiểm tra lỗi...`, `tg: chạy test...`, `... tg`, `... (tg)`) $\rightarrow$ AI xử lý xong sẽ trả lời lại qua Telegram.
2. **AI cần con người can thiệp / chốt nghiệp vụ / chọn phương án thiết kế** (nhận diện qua nhãn `[TG_NOTIFY]`).
3. **AI dừng để xin quyền chạy lệnh (Permission / Question)**.
4. **Task chạy lâu ($\ge 180$ giây / 3 phút) đã hoàn thành** hoặc **gặp lỗi dừng bất thường**.
   - 💡 *Muốn đổi thời gian này (ví dụ sang 60 giây hoặc 300 giây)*: Chỉ cần mở file **`config.json`** trong thư mục `ai-telegram` và sửa con số ở dòng `"min_duration_seconds": 180` (tính bằng giây). Lưu file lại là **có hiệu lực ngay lập tức ở lượt chạy kế tiếp**, không cần khởi động lại dịch vụ.

*(Các câu chat ngắn dưới 3 phút khi bạn đang ngồi gõ trực tiếp trên máy tính mà không gắn `tg` sẽ không báo về điện thoại).*

---

## 🚀 Hướng dẫn cài đặt từ A - Z (Step-by-Step)

### Bước 1: Tạo Bot Telegram cá nhân để lấy `BOT_TOKEN` (Mất 1 phút)

Mỗi người nên tạo 1 con Bot riêng (hoàn toàn miễn phí) để nhận thông báo và điều khiển máy tính của mình:

1. Mở ứng dụng **Telegram**, tìm kiếm tài khoản chính chủ **[@BotFather](https://t.me/BotFather)** (có tích xanh) và bấm **Start**.
2. Gửi lệnh:
   ```text
   /newbot
   ```
3. **BotFather** hỏi tên hiển thị của Bot (*"Alright, a new bot. How are we going to call it?"*) $\rightarrow$ Nhập tên bất kỳ (ví dụ: `My AI Assistant`).
4. **BotFather** hỏi username của Bot (*"Now let's choose a username for your bot. It must end in `bot`."*) $\rightarrow$ Nhập username viết liền không dấu, kết thúc bằng chữ `bot` (ví dụ: `tenban_ai_bot`).
5. **BotFather** sẽ trả về thông báo thành công kèm mã **HTTP API Token** có dạng:
   ```text
   8582278735:AAF66si-o6cNznXkJP5wexZ5YAzkB2srKrg
   ```
   👉 **Copy mã này lại**, đây chính là **`BOT_TOKEN`** của bạn.
6. ⚠️ **Quan trọng**: Bấm vào link `t.me/tenban_ai_bot` ngay trong tin nhắn của BotFather và bấm nút **START** (hoặc gửi chữ `/start`) để kích hoạt cuộc trò chuyện với Bot của bạn. *(Nếu chưa bấm Start, Bot sẽ không được phép gửi tin nhắn cho bạn).*

---

### Bước 2: Lấy `CHAT_ID` Telegram của bạn (Mất 30 giây)

`CHAT_ID` giúp bảo mật để **chỉ một mình tài khoản Telegram của bạn** mới có quyền nhận thông báo và điều khiển máy tính của bạn:

1. Trên Telegram, tìm kiếm bot **[@userinfobot](https://t.me/userinfobot)**.
2. Bấm **Start** (hoặc nhắn tin bất kỳ cho nó).
3. Nó sẽ trả về thông tin tài khoản của bạn dạng:
   ```text
   @username_cua_ban
   Id: 754880858
   First: Ten Ban
   Lang: vi
   ```
   👉 **Copy dãy số ở dòng `Id:`** (ví dụ `754880858`), đây chính là **`CHAT_ID`** của bạn.

---

### Bước 3: Chuẩn bị môi trường theo Hệ điều hành

#### 🪟 Nếu bạn dùng Windows 10 / 11:
- Cần cài sẵn **Python 3** (nếu chưa có, mở PowerShell gõ: `winget install Python.Python.3.12` hoặc tải từ [python.org](https://www.python.org/downloads/) — nhớ tích chọn *"Add python.exe to PATH"* khi cài).
- Bạn có thể dùng theo **1 trong 2 cách**:
  - **Cách A (Windows Native)**: Chạy trực tiếp `agy`, `claude`, `codex` trên **Windows Terminal / PowerShell / CMD**.
  - **Cách B (WSL2 Ubuntu)**: Nếu bạn chạy code trong WSL2, hãy làm theo hướng dẫn của Ubuntu bên dưới.

#### 🐧 Nếu bạn dùng Ubuntu / Linux (hoặc WSL2 trên Windows):
- Cài đặt `python3` và `tmux`:
  ```bash
  sudo apt update && sudo apt install -y python3 tmux
  ```

#### 🍎 Nếu bạn dùng macOS:
- Cài đặt `tmux` qua Homebrew (Python 3 đã có sẵn):
  ```bash
  brew install tmux
  ```

---

### Bước 4: Cấu hình `config.json` và Chạy cài đặt tự động

1. Trong thư mục `ai-telegram`, nhân bản file mẫu **`config.sample.json`** thành **`config.json`**:
   - **Trên macOS / Ubuntu**:
     ```bash
     cp config.sample.json config.json
     ```
   - **Trên Windows (CMD / PowerShell)**:
     ```powershell
     copy config.sample.json config.json
     ```
2. Mở file `config.json` vừa tạo và điền `bot_token` (ở Bước 1) cùng `chat_id` (ở Bước 2) của bạn vào:
   ```json
   {
     "bot_token": "8582278735:AAF66si-o6cNznXkJP5wexZ5YAzkB2srKrg",
     "chat_id": "754880858",
     "min_duration_seconds": 180
   }
   ```
   *(Hoặc nếu bạn chưa tạo `config.json`, khi chạy bộ cài ở bước dưới nó cũng sẽ tự động hỏi bạn 2 thông tin này và tạo `config.json` giúp bạn).*

3. **Chạy cài đặt tự động**:

#### 🪟 Trên Windows 10 / 11 (Native):
- Mở thư mục `ai-telegram`, **nhấp đúp chuột vào file `install.bat`** (hoặc mở PowerShell/CMD tại thư mục đó và gõ `python install.py`).
- Khi thấy thông báo `[OK] Đã gửi tin nhắn kiểm tra thành công tới Telegram!` là hoàn tất!

> **Bộ cài đã tự động làm gì trên Windows?**
> - Tự động gắn Hook vào `~/.gemini/config/hooks.json` (`agy`), `~/.claude/settings.json` (`claude`), và `~/.codex/hooks.json` (`codex`).
> - Tự động thêm Rule `[TG_NOTIFY]` vào `GEMINI.md`, `CLAUDE.md`, `AGENTS.md`.
> - Tự động tạo file khởi động ngầm `ai-telegram-daemon.vbs` (chạy qua `pythonw.exe` nên **không hiện cửa sổ CMD màu đen**) trong thư mục `Startup` của Windows để mỗi lần bật máy là tự chạy.

#### 🐧 Trên Ubuntu / Linux và 🍎 macOS:
- Mở Terminal tại thư mục `ai-telegram` và chạy:
  ```bash
  chmod +x install.sh
  ./install.sh
  ```
- Kiểm tra điện thoại sẽ nhận được ngay tin nhắn chào mừng từ Bot!

> **Bộ cài đã tự động làm gì trên Ubuntu / macOS?**
> - Gắn đầy đủ Hook & Rule cho cả `agy`, `claude`, `codex`.
> - **Trên macOS**: Tạo và kích hoạt `LaunchAgent` (`~/Library/LaunchAgents/com.thaipd.ai-telegram.plist`).
> - **Trên Ubuntu**: Tạo và kích hoạt dịch vụ `systemd --user` (`~/.config/systemd/user/ai-telegram.service`) tự chạy ngầm khi bật máy.

---

## 📱 Bước 5: Cách sử dụng hàng ngày

### 1. Mở AI trên máy tính:
- **Trên macOS / Ubuntu / WSL2**: Hãy mở AI bên trong `tmux` để có trải nghiệm tốt nhất:
  ```bash
  tmux new -s work
  # Sau đó gõ lệnh chạy AI như bình thường:
  agy        # hoặc: claude / codex
  ```
- **Trên Windows 10/11 Native**: Mở **Windows Terminal** hoặc **PowerShell** và chạy `agy`, `claude`, hoặc `codex` như bình thường.

*(Lưu ý: Nếu bạn đang mở sẵn cửa sổ `agy`/`claude`/`codex` từ trước khi chạy `install`, hãy tắt đi bật lại phiên AI đó 1 lần để nó nhận cấu hình Hook mới).*

### 2. Điều khiển từ điện thoại (Telegram):
Ở góc dưới bên trái khung chat với Bot sẽ có nút **Menu màu xanh `[☰]`**. Bấm vào đó sẽ có sẵn các lệnh (không cần tự gõ):

| Lệnh Menu | Chức năng |
| :--- | :--- |
| **`/list`** | Liệt kê tất cả cửa sổ `tmux` (hoặc cửa sổ `Windows Terminal`/`PowerShell` trên Windows) đang mở kèm nút bấm **`🎯 Chọn ...`** để chuyển cửa sổ cần điều khiển. |
| **`/screen`** | Chụp nhanh màn hình terminal hiện tại của cửa sổ đang chọn gửi về điện thoại. |
| **Nhắn chữ bất kỳ** | Gửi câu hỏi / yêu cầu đó thẳng vào cửa sổ AI đang chọn. Ngay khi AI trả lời xong sẽ tự động gửi câu trả lời về Telegram. |
| **`/yes`** | Gửi phím `y + Enter` vào cửa sổ AI (có hiện bảng hỏi xác nhận trước khi gửi để tránh bấm nhầm). |
| **`/no`** | Gửi phím `n + Enter` vào cửa sổ AI (có xác nhận). |
| **`/enter`** | Gửi phím `Enter` (có xác nhận). |
| **`/esc`** | Gửi phím `Escape` (có xác nhận). |
| **`/ctrlc`** | Gửi tổ hợp `Ctrl+C` để dừng tác vụ đang chạy (có xác nhận). |
| **`/help`** | Xem lại bảng hướng dẫn nhanh. |

---

## 🛠 Xử lý sự cố thường gặp (Troubleshooting)

1. **Cài xong nhưng không thấy tin nhắn chào mừng trên Telegram?**
   - Kiểm tra xem bạn đã bấm nút **START** trong cuộc trò chuyện với con Bot của bạn chưa.
   - Kiểm tra lại `bot_token` và `chat_id` trong file `config.json` đã chính xác chưa, sau đó chạy lại `install.bat` (Windows) hoặc `./install.sh` (Ubuntu/macOS).

2. **Muốn đổi `bot_token`, `chat_id` hoặc thời gian báo cáo task dài (`min_duration_seconds`)?**
   - Mở file `config.json`, sửa thông số (mặc định `"min_duration_seconds": 30`), rồi chạy lại `./install.sh` hoặc `install.bat` để khởi động lại dịch vụ chạy ngầm.

3. **Lệnh kiểm tra dịch vụ chạy ngầm:**
   - **macOS**: `ps aux | grep tg_bot_daemon`
   - **Ubuntu**: `systemctl --user status ai-telegram.service`
   - **Windows (PowerShell)**: `Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*tg_bot_daemon.py*' }`
