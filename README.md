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
  - **Cách A (Windows Native)**: Chạy trực tiếp `agy`, `claude`, `codex` trên **Windows Terminal / PowerShell / CMD** hoặc **IDE**. *(Không cần `tmux` — công cụ sẽ gửi **ảnh chụp thật** của cửa sổ khi bạn bấm `📸 Xem màn hình`.)*
  - **Cách B (WSL2 Ubuntu)**: Nếu bạn chạy code trong WSL2, hãy làm theo hướng dẫn của Ubuntu bên dưới.
- Nếu bạn dùng **multiplexer khác `tmux`** (ví dụ `psmux`): xem mục *🧩 Dùng `psmux`…* ở Bước 5 để khai báo `"multiplexer_bin"` trong `config.json`.

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
     "min_duration_seconds": 180,
     "multiplexer_bin": "tmux"
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

## 🩺 Tự kiểm tra nhanh (selftest)

```powershell
python selftest.py          # kiểm tra config, multiplexer, cửa sổ, Telegram API, hook, daemon
python selftest.py --shot   # thêm chụp thử ảnh cửa sổ (lưu vào state/selftest.png)
```

Script sẽ in kết quả từng mục và chỉ ra chính xác chỗ nào còn vấn đề, ví dụ:
- `409 Conflict → CÓ daemon khác đang poll cùng token` (xung đột 2 máy)
- `Không tìm thấy cửa sổ nào` (chưa mở Terminal/IDE)
- `Chưa có heartbeat → daemon chưa chạy ở thư mục này`

---

## ⏯️ Bật / Tắt bot (tự khởi động cùng máy)

Mặc định, sau khi cài, bot **tự chạy mỗi lần bạn đăng nhập Windows** (nhờ script `ai-telegram-daemon.vbs` trong thư mục `Startup`). Bạn có thể bật/tắt bất kỳ lúc nào:

| Lệnh (chạy trong thư mục `ai-telegram`) | Tác dụng |
| :--- | :--- |
| `python install.py --status` | Xem trạng thái: tự khởi động đang bật/tắt + daemon có sống không + hook còn cài không |
| `python install.py --disable` | **TẮT TẤT CẢ**: gỡ tự khởi động cùng máy + dừng daemon + **gỡ hook & rule `[TG_NOTIFY]`** (AI sẽ không còn tự gửi thông báo) |
| `python install.py --disable --keep-hooks` | Chỉ tắt daemon + tự khởi động, **giữ nguyên hook** (AI vẫn gửi thông báo Telegram) |
| `python install.py --hooks-only` | **Chỉ cài hook** (AI báo Telegram) mà **không** chạy daemon, **không** tự khởi động — dùng khi daemon đang chạy ở máy khác |
| `python install.py --enable` | **BẬT LẠI TẤT CẢ**: cài lại hook + tự khởi động + chạy daemon |
| `python install.py --stop` | Dừng daemon tạm thời (vẫn giữ cấu hình tự khởi động) |
| `python install.py --start` | Chạy daemon ngay (không đổi cấu hình tự khởi động) |

Trên Windows có thể gõ trực tiếp trong cmd/PowerShell:
```powershell
install.bat --disable     # tắt hẳn
install.bat --enable      # bật lại
install.bat --status      # xem trạng thái
```

💡 Trên Windows, script giám sát sẽ **tự khởi động lại daemon** nếu nó bị crash (mỗi 15 giây kiểm tra 1 lần). Nếu chạy 2 daemon cùng lúc sẽ xung đột `getUpdates`, nên daemon đã tự giữ **file khóa `state/daemon.pid`** để chỉ một tiến trình chạy.

> Lưu ý: `/away` và `/here` trên Telegram chỉ quyết định **có gửi thông báo tự động hay không**, chúng **không** tắt daemon. Muốn tắt hẳn (không còn tự chạy khi bật máy, không còn tự gửi thông báo) thì dùng `--disable` ở trên.

---

## 🧩 Cài Antigravity CLI (`agy`) — bước nên làm để điều khiển AI ổn định

Ô chat AI **bên trong IDE** (Antigravity/VS Code) là WebView của Electron → rất khó để tự động hoá
(UI Automation không lộ ô nhập, và dán mù có thể ghi đè file mã nguồn). Còn **CLI `agy`** chạy trong
terminal thì mọi thứ chạy đúng bản đồ: nhận text chắc chắn, hook bắn đúng, `/screen` đọc được buffer,
bắt được bảng hỏi `1/2/3`, monitor được quota.

```powershell
python install_agy.py            # tự dò `agy`; nếu tìm thấy -> thêm thư mục vào PATH
python install_agy.py --check    # chỉ kiểm tra
python install_agy.py --npm      # cài qua npm (tên package lấy từ config.json "install_agy.package")
python install_agy.py --npm <tên-package>
```

Sau khi có `agy`, chạy nó trong **terminal riêng** (không dùng terminal nhúng trong IDE):

```powershell
cd D:\Vivas\myTV_landingpage
agy
```

Rồi trong Telegram: `/list` → bấm **`🎯 Chọn`** cửa sổ terminal đó → nhắn lệnh là chạy.

> 💡 Muốn điều khiển **chính xác từng cửa sổ** (nhiều project song song) thì chạy `agy` trong
> `tmux`/`psmux` và đặt `"multiplexer_bin": "psmux"` trong `config.json` — lúc đó daemon gửi phím
> trực tiếp vào đúng pane, không phụ thuộc focus của cửa sổ.

---

## 🤖 Điều khiển agent của **Paseo** từ Telegram

Nếu bạn chạy AI qua **Paseo** (agent chạy CLI `claude`/`codex`/`agy` thật trong terminal của Paseo) thì bật backend Paseo:

```json
// config.json
"target_backend": "paseo"
```

| Thao tác | Cách hoạt động |
|---|---|
| `/list` | Liệt kê **agent** của Paseo (`paseo ls --json`) — chọn bằng nút `🎯 Chọn` |
| Nhắn lệnh | `paseo send <agentId> "<lệnh>"` (mặc định lệnh được **xếp hàng** nếu agent đang bận) |
| `/force <lệnh>` | `paseo send <agentId> --behavior steer "<lệnh>"` (ép agent đọc ngay) |
| `/screen` | Hiện title / trạng thái / thư mục / host của agent |
| Câu trả lời AI | **Vẫn về Telegram qua Hook** (vì agent chạy CLI thật) |

> 💡 Paseo **không có "chat id"** — nó định danh theo `agentId` (+ `serverId` cho host từ xa).
> `chat_id` trong `config.json` vẫn là phía Telegram, không đổi.
>
> 🎯 Lợi ích lớn: daemon Telegram chỉ cần chạy ở **một máy** nhưng điều khiển được agent ở **mọi host**
> ⇒ hết vướng lỗi `409 Conflict` khi chạy bot trên nhiều máy.

---

## 🌐 Lỗi mạng khi gửi Telegram (`WinError 10054`)

```
[WARN] Không gửi được tin nhắn test Telegram: <urlopen error [WinError 10054] ...>
```

Đây là lỗi mạng **tạm thời** (bị RST từ server) — **không ảnh hưởng việc cài đặt**, bot vẫn chạy.
Từ `v1.8.1` công cụ đã tự xử lý:

| Cải tiến | Tác dụng |
|---|---|
| `net_compat.py` — ép **IPv4** | Trên Windows, `api.telegram.org` hay bị phân giải sang IPv6 trong khi đường IPv6 hỏng ⇒ gây `10054` |
| **Retry có backoff** (3–4 lần) | Lỗi mạng tạm thời không còn làm mất tin nhắn im lặng |

> ⚠️ Khi triển khai, **copy cả thư mục** (file `net_compat.py` là file dùng chung cho cả 3 script).

Nếu vẫn lỗi: kiểm tra tường lửa/VPN, hoặc thử `Test-NetConnection api.telegram.org -Port 443`.

---

## 🩺 Cửa sổ console bật lên rồi tắt? (Windows)

Nếu thấy cửa sổ PowerShell/cmd đen bật lên rồi tắt liên tục, chạy script chẩn đoán để biết
**tiến trình mẹ** nào đang sinh ra nó:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File watch_console.ps1 -Seconds 90
```

| `parent=...` trong output | Thủ phạm | Cách xử lý |
| :--- | :--- | :--- |
| `pythonw.exe` | daemon `ai-telegram` | `python install.py` (đã vá tự ẩn cửa sổ) |
| `Antigravity.exe` / `Code.exe` / `Cursor.exe` | **hook** `hook_notify.py` chạy từ IDE | `python install.py` (hook đã đổi sang `pythonw.exe`) |
| `wscript.exe` | script giám sát trong `Startup` | `python install.py` (đã vá: chỉ spawn khi heartbeat cũ) |
| `explorer.exe` | ứng dụng khác | Dùng Process Monitor để tra tiếp |

> Từ `v1.8.1`: mọi lệnh console con đều chạy với `CREATE_NO_WINDOW`, hook dùng `pythonw.exe`
> (không console), và `tasklist.exe` đã được thay bằng Win32 API qua `ctypes` ⇒ không còn cửa sổ nhấp nháy.

---

## 📱 Bước 5: Cách sử dụng hàng ngày

Ở góc dưới bên trái khung chat với Bot trên Telegram có nút **Menu màu xanh `[☰]`**. Bấm vào đó sẽ có sẵn các lệnh:

| Lệnh Menu | Chức năng |
| :--- | :--- |
| **`/list`** | Xem trạng thái (`Here` / `Away` / `Screen Locked`), liệt kê tất cả cửa sổ `tmux` / `IDE` / `Windows Terminal` đang mở kèm nút **`🎯 Chọn ...`** và **`📸`** (xem màn hình). |
| **`/screen`** | Chụp màn hình cửa sổ đang chọn gửi về điện thoại. Trên **Windows** (không dùng `tmux`/`psmux`) công cụ gửi **ảnh chụp thật** của cửa sổ; nếu có `tmux`/`psmux` thì gửi nội dung text của terminal. |
| **`/away`** | Chủ động bật chế độ Vắng mặt từ điện thoại (nếu lúc rời bàn bạn quên khóa màn hình PC). |
| **`/here`** | Chuyển về chế độ Ngồi máy (hoặc chỉ cần gõ phím trực tiếp trên PC là tự động về `/here`). |
| **Nhắn chữ bất kỳ** | Gửi câu hỏi / yêu cầu đó thẳng vào cửa sổ AI đang chọn và nhận câu trả lời qua Telegram. *Với IDE (`Antigravity`/`VS Code`/`Cursor`), công cụ tự tìm & focus ô chat AI bằng UI Automation; nếu chưa thấy ô chat, nó sẽ bảo bạn bấm vào ô chat một cái rồi gửi lại (để không dán nhầm vào file mã nguồn).* |
| **`/yes`, `/no`, `/enter`, `/esc`, `/ctrlc`** | Gửi phím điều khiển vào cửa sổ AI (có hộp thoại xác nhận 2 bước để chống bấm nhầm). |
| **`/ping`** | Kiểm tra daemon còn "sống" hay không + xem cấu hình (multiplexer đang dùng, số cửa sổ phát hiện được, thư mục `state/`). Dùng khi bấm nút mà **không có phản hồi**. |
| **`/shot [url]`** | Chụp **ảnh giao diện web** và gửi về Telegram (dùng Edge/Chrome headless). Bỏ trống URL ⇒ tự dò dev server trên `localhost` (cổng 3000, 5173, 8080, 4200, 8000…). |
| **`/force <lệnh>`** | Ép dán lệnh vào **ô đang focus** của cửa sổ IDE (Antigravity/VS Code). Dùng khi bot báo *"chưa tìm thấy ô nhập AI"*: bấm vào ô chat AI trên PC một cái rồi gửi `/force ...`. |
| **`/help`** | Xem lại bảng hướng dẫn và trạng thái hiện tại. |

> 💡 **Nếu bấm nút mà không có phản hồi**: gõ `/ping`. Nếu bot cũng không trả lời → daemon đã chết hoặc kẹt. Mở `daemon.err.log` trong thư mục `ai-telegram` để xem lỗi, rồi chạy lại `install.bat` (hoặc `python tg_bot_daemon.py` để xem lỗi trực tiếp).

---

## 🧩 Dùng `psmux` (hoặc multiplexer khác) thay cho `tmux`

Bản `v1.7.0` cho phép chọn binary multiplexer trong **`config.json`**:

```json
{
  "bot_token": "...",
  "chat_id": "...",
  "min_duration_seconds": 180,
  "multiplexer_bin": "psmux"
}
```

- Đặt `"multiplexer_bin": "tmux"` (mặc định) nếu dùng tmux thật.
- Đặt `"psmux"` nếu chạy AI trong `psmux`. Công cụ tự nhận biến pane qua `TMUX_PANE` **và** `PSMUX_PANE`.
- ⚠️ `psmux` phải hỗ trợ các lệnh tương đương tmux: `list-panes`, `capture-pane`, `send-keys`, `copy-mode`, `display-message`. Nếu thiếu, các tính năng quota monitor / bảng hỏi `1/2/3` / trạng thái sống sẽ không hoạt động (nhưng phần **ảnh chụp màn hình** và **gõ lệnh** vẫn chạy).
- **Không có multiplexer nào** (chạy AI thẳng trong Windows Terminal / IDE): mọi thứ vẫn chạy, `/screen` gửi **ảnh chụp thật** của cửa sổ, gõ lệnh từ Telegram dùng cơ chế `AppActivate` của Windows.

---

## 🚀 Các tính năng nổi bật (Cập nhật `v1.4.0`)

1. **Xác nhận 2 lớp từ Hook (`Pre-execution ACK`)**:
   - Khi nhắn tin từ Telegram vào `tmux`, chính Hook `start` của AI xác nhận `✅ OK — Đã nhận lệnh tại cửa sổ <pane>` trước khi thực thi.
   - Tự động thoát `copy-mode` (`tmux copy-mode -q`) và cảnh báo sau 5 giây kèm ảnh chụp màn hình nếu con trỏ trong `tmux` không nằm ở ô nhập.
2. **Khóa cố định cửa sổ đang chọn (`A`) & Hỗ trợ vuốt `Reply` sang cửa sổ `B`**:
   - Cửa sổ đang chọn mặc định không bao giờ bị nhảy ngầm khi cửa sổ khác bắn thông báo chen ngang.
   - Vuốt **Reply (Trả lời)** trực tiếp vào tin nhắn của cửa sổ nào trên Telegram sẽ gửi lệnh vào đúng cửa sổ đó.
3. **Phân biệt thông minh `📋 Báo cáo tổng kết` và `🙋‍♂️ Cần chốt phương án`**:
   - Chỉ hiển thị nút `Yes / No / Enter` khi AI thực sự đặt câu hỏi hoặc xin quyền.
4. **Giám sát Hết Quota & Tự động báo khi Reset Quota chạy lại**:
   - Tự động phát hiện `⚠ Usage limit reached` ở thanh trạng thái đáy `tmux` để cảnh báo ngay về Telegram kèm giờ reset, và báo tin vui `🟢 ĐÃ RESET QUOTA & CHẠY LẠI!` ngay khi đến giờ chạy tiếp.
5. **Trạng thái sống trong `/list`**:
   - Xem ngay cửa sổ nào đang `💤 Sẵn sàng`, `⚙️ Đang thực thi...`, `⏰ Đang hẹn giờ (Timer)`, hay `🚨 Hết quota (reset 11pm)`.
6. **Bắt bảng hỏi (`1/2/3`, `y/n`) & Hàng đợi thông minh (`Smart Queue`)**:
   - Tự phát hiện khi AI dừng ở bảng hỏi quyền/lựa chọn trên `tmux` để hiện nút bấm `[1️⃣ Chọn 1] [2️⃣ Chọn 2] [3️⃣ Chọn 3] [✅ Yes] [❌ No]`.
   - Khi gửi lệnh mới lúc AI đang bận chạy lệnh trước, tự động xếp vào Hàng đợi (`Queue`) và chạy ngay khi lượt cũ hoàn tất (hoặc bấm nút ép chạy ngay / hủy lượt cũ).
