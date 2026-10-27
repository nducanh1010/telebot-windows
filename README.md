# 🤖 AI Telegram Remote Bridge (`agy` / `claude` / `codex`)

Bộ công cụ giúp kết nối các AI (**Antigravity `agy`**, **Claude Code `claude`**, **OpenAI Codex `codex`** — cả bản CLI và IDE) với **Telegram** để:
1. **Nhận thông báo tự động thông minh** trên điện thoại khi bạn rời máy tính (khóa màn hình hoặc bật `/away`).
2. **Chat & điều khiển AI 2 chiều** từ xa ngay trên Telegram (hỗ trợ **Windows 10/11**, **Ubuntu/Linux**, và **macOS**).

*(Xem file [`AGENTS.md`](./AGENTS.md) nếu bạn cần tìm hiểu sâu về kiến trúc kỹ thuật hoặc dùng AI để chỉnh sửa thêm công cụ này).*

---

## 📋 Cơ chế thông báo thông minh (Tự động phân biệt Ngồi máy vs Vắng mặt)

Hệ thống tự động nhận biết bạn đang ngồi làm việc trên PC hay đang ở xa để **không bao giờ làm phiền điện thoại thừa thãi**:

### 1. 💻 Khi bạn đang ngồi trước PC (Chế độ `Here` — Mặc định):
- Mọi câu gõ trực tiếp trên PC sẽ chỉ trả lời trên màn hình PC, **điện thoại hoàn toàn im lặng**.
- **Chỉ gửi về Telegram khi**:
  - Bạn chủ động nhắn câu hỏi từ **Telegram**.
  - Hoặc bạn gõ câu lệnh trên PC nhưng **thêm chữ `tg` ở đầu hoặc cuối câu** (ví dụ: `tg chạy test giúp mình`, `tg: kiểm tra lỗi...`, `... tg`, `... (tg)`) trước khi đứng dậy đi ra ngoài.

### 2. 🔒 Khi bạn rời khỏi PC (Tự động bật `Away` khi Lock màn hình PC, hoặc bấm `/away` trên Telegram):
- **Tự động phát hiện khóa màn hình**: Hễ bạn bấm khóa màn hình PC (`Ctrl + Cmd + Q` trên Mac / `Win + L` trên Windows), hệ thống tự biết bạn đã rời bàn làm việc mà không cần gõ `/away`.
- **Nếu quên khóa màn hình PC**: Ra ngoài bạn chỉ cần mở Telegram bấm **`/away`** trong menu `[☰]` (hoặc nhắn bất kỳ câu gì vào Telegram) là chế độ `Away` được bật.
- **Khi ở chế độ `Away`, AI sẽ tự động gửi thông báo về Telegram khi**:
  1. AI cần con người chốt nghiệp vụ / phương án kiến trúc (qua nhãn `[TG_NOTIFY]`).
  2. AI cần xin quyền xác nhận (`ask_question` / `Notification`).
  3. Task chạy lâu ($\ge 180$ giây / 3 phút) đã hoàn thành hoặc dừng do lỗi.
     - 💡 *Muốn đổi thời gian 180 giây này*: Chỉ cần mở file **`config.json`** trong thư mục `ai-telegram` và sửa con số ở dòng `"min_duration_seconds": 180` (tính bằng giây). Lưu file lại là **có hiệu lực ngay lập tức**, không cần sửa file Hook hay khởi động lại dịch vụ.
- **Khi bạn quay lại ngồi gõ trên PC**: Ngay khi bạn gõ 1 câu lệnh bất kỳ trên bàn phím PC, hệ thống **tự động tắt `Away` (về `Here`)**, bạn không cần phải nhớ gõ `/here`!

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
  - **Cách A (Windows Native)**: Chạy trực tiếp `agy`, `claude`, `codex` trên **Windows Terminal / PowerShell / CMD** hoặc **IDE**.
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

#### 🐧 Trên Ubuntu / Linux và 🍎 macOS:
- Mở Terminal tại thư mục `ai-telegram` và chạy:
  ```bash
  chmod +x install.sh
  ./install.sh
  ```
- Kiểm tra điện thoại sẽ nhận được ngay tin nhắn chào mừng từ Bot!

---

## 📱 Bước 5: Cách sử dụng hàng ngày

Ở góc dưới bên trái khung chat với Bot trên Telegram có nút **Menu màu xanh `[☰]`**. Bấm vào đó sẽ có sẵn các lệnh:

| Lệnh Menu | Chức năng |
| :--- | :--- |
| **`/list`** | Xem trạng thái (`Here` / `Away` / `Screen Locked`), liệt kê tất cả cửa sổ `tmux` / `IDE` / `Windows Terminal` đang mở kèm nút **`🎯 Chọn ...`**. |
| **`/screen`** | Chụp nhanh màn hình terminal (hoặc nội dung trả lời mới nhất) gửi về điện thoại. |
| **`/away`** | Chủ động bật chế độ Vắng mặt từ điện thoại (nếu lúc rời bàn bạn quên khóa màn hình PC). |
| **`/here`** | Chuyển về chế độ Ngồi máy (hoặc chỉ cần gõ phím trực tiếp trên PC là tự động về `/here`). |
| **Nhắn chữ bất kỳ** | Gửi câu hỏi / yêu cầu đó thẳng vào cửa sổ AI đang chọn và nhận câu trả lời qua Telegram. |
| **`/yes`, `/no`, `/enter`, `/esc`, `/ctrlc`** | Gửi phím điều khiển vào cửa sổ AI (có hộp thoại xác nhận 2 bước để chống bấm nhầm). |
| **`/help`** | Xem lại bảng hướng dẫn và trạng thái hiện tại. |
