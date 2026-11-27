# AGENTS.md — Tài liệu Ngữ cảnh & Kiến trúc Hệ thống (`ai-telegram`)

Tài liệu này lưu trữ **mục tiêu thiết kế**, **kiến trúc luồng dữ liệu**, và **các lưu ý kỹ thuật quan trọng (technical gotchas)** để bất kỳ AI Agent nào (`agy`, `claude`, `codex`) khi vào bảo trì/nâng cấp project này đều hiểu đầy đủ ngữ cảnh.

---

## 1. Mục tiêu bài toán (Goals & UX Principles)

1. **Hỗ trợ đồng nhất cả 3 AI (`agy`, `claude`, `codex`) và cả 2 môi trường (`CLI/tmux` + `IDE`)** trên 3 hệ điều hành (**macOS**, **Ubuntu/Linux/WSL2**, **Windows 10/11**).
2. **Không làm phiền khi đang ngồi máy (`Zero-Noise when Here`)**:
   - Khi người dùng đang ngồi làm việc trực tiếp trước màn hình PC (màn hình mở, không bật `/away`), AI trả lời xong trên PC thì **KHÔNG gửi thông báo về Telegram**, trừ khi:
     - Câu hỏi đó được gửi từ chính **Telegram**.
     - Hoặc người dùng chủ động gõ từ khóa **`tg`** ở đầu hoặc cuối câu lệnh trên PC (ví dụ: `tg chạy test...`, `tg: ...`, `... tg`, `... (tg)`).
3. **Tự động chuyển đổi trạng thái `Here` $\leftrightarrow$ `Away` (Zero-Effort)**:
   - **Tự động biết `Away` khi khóa màn hình PC**: Không cần chạy tiến trình theo dõi màn hình liên tục. Khi kết thúc lượt (`Stop` hook), `hook_notify.py` kiểm tra trạng thái khóa màn hình của OS (`is_screen_locked()`). Nếu màn hình đang khóa $\rightarrow$ tự coi là `Away`.
   - **Hỗ trợ bật `/away` từ xa trên Telegram**: Nếu người dùng rời máy mà quên khóa màn hình PC, có thể bấm `/away` trên Telegram (hoặc nhắn bất kỳ câu hỏi nào từ Telegram cũng tự kích hoạt `Away`).
   - **Tự động về `Here` khi gõ lại trên PC**: Khi `start` hook chạy mà không có cờ `from_tg_dispatch.flag` (tức là người dùng gõ bàn phím trực tiếp trên PC), hệ thống tự động xóa `away_mode.flag` mà người dùng không cần gõ `/here`.
4. **An toàn khi thao tác trên điện thoại**:
   - Có menu lệnh `[☰]` (`setMyCommands`).
   - Mọi lệnh gửi phím điều khiển (`/enter`, `/esc`, `/ctrlc`, `/yes`, `/no` và các nút Inline Keyboard) đều bắt buộc qua **bước xác nhận 2 lớp (`confirmkey:`)** để tránh chạm nhầm trên màn hình cảm ứng.

---

## 2. Kiến trúc các thành phần (Components)

- **`config.json`** (được tạo từ `config.sample.json`, bị ignore khỏi Git):
  - Chứa `bot_token`, `chat_id`, và `min_duration_seconds` (mặc định `180` giây = 3 phút).
  - `multiplexer_bin` (mặc định `tmux`): tên binary multiplexer — đặt `"psmux"` nếu dùng psmux (xem mục 15).
  - Mọi thay đổi trong `config.json` được `hook_notify.py` đọc động ở mỗi lượt chạy mà không cần restart.
- **`hook_notify.py`**:
  - Được gọi bởi Lifecycle Hooks của:
    - **Antigravity (`agy` / Antigravity IDE)**: `~/.gemini/config/hooks.json` (`PreInvocation` $\rightarrow$ `start`, `PreToolUse[ask_question]` $\rightarrow$ `ask`, `Stop` $\rightarrow$ `stop`).
    - **Claude Code (`claude` CLI / IDE)**: `~/.claude/settings.json` (`UserPromptSubmit` $\rightarrow$ `start`, `Notification` $\rightarrow$ `ask`, `Stop` $\rightarrow$ `stop`).
    - **Codex (`codex` CLI / IDE)**: `~/.codex/hooks.json` (`UserPromptSubmit` $\rightarrow$ `start`, `Stop` $\rightarrow$ `stop`).
- **`tg_bot_daemon.py`**:
  - Dịch vụ chạy ngầm 24/7 (`launchd` trên macOS, `systemd --user` / `nohup` trên Ubuntu, `pythonw.exe` + `Startup/*.vbs` trên Windows 10/11).
  - Sử dụng Long Polling (`getUpdates`) chỉ với thư viện chuẩn Python (`urllib.request`), không phụ thuộc thư viện bên thứ ba (`pip`).
- **Thư mục `state/`** (lưu trạng thái runtime):
  - `active_pane.txt`: Cửa sổ `tmux` hoặc `ide:` hoặc `PID` đang được chọn để nhận lệnh từ Telegram.
  - `away_mode.flag`: Có mặt khi người dùng bật `/away` hoặc vừa nhắn từ Telegram. Tự bị xóa khi người dùng gõ phím trực tiếp trên PC.
  - `from_tg_dispatch.flag`: Được `tg_bot_daemon.py` tạo ngay trước khi đẩy lệnh vào `tmux`/IDE (hiệu lực 15s) để `hook_notify.py start` phân biệt lệnh từ Telegram với lệnh gõ trên PC.
  - `waiting_<safe_pane>.flag` & `waiting_any.flag`: Đánh dấu lượt chạy này do người dùng hỏi từ Telegram để `hook_notify.py stop` luôn trả lời về Telegram.
  - `prompt_tg_<conv_id>.flag`: Đánh dấu câu lệnh gõ trên PC có tiền tố/hậu tố `tg`.
  - `ide_sessions.json` & `ide_inbox_<safe_pane>.txt`: Hỗ trợ liệt kê và gửi tin nhắn trả lời vào phiên IDE khi chạy ở chế độ `Away`.

---

## 3. Ma trận Quyết định Gửi Thông báo (`hook_notify.py`)

Tại sự kiện `stop`:
```text
from_telegram = (có cờ waiting_*.flag) HOẶC (câu lệnh của user bắt đầu/kết thúc bằng "tg")
is_away       = (có file away_mode.flag) HOẶC (is_screen_locked() == True)
needs_human   = câu trả lời của AI bắt đầu bằng nhãn thông báo (text.lstrip().startswith(...))
is_long_task  = duration >= min_duration_seconds (mặc định 180s)
is_error      = terminationReason thuộc ("ERROR", "MAX_INVOCATIONS", "MAX_TOKEN_BUDGET_EXCEEDED")

should_notify = from_telegram OR (is_away AND (needs_human OR is_long_task OR is_error))
```

---

## 4. Các bài học kỹ thuật bắt buộc lưu ý khi sửa code (Critical Gotchas)

1. **Lỗi treo `tmux 3.8` trên macOS dưới `launchd`**:
   - Khi chạy dưới `launchd`, `stdout` (`fd 1`) của daemon là một file thường (`REG` - `daemon.log`). Nếu gọi `subprocess.run(["tmux", ...])` mà để `tmux` kế thừa `fd 1` là file thường, `tmux` gửi `fd 1` qua `SCM_RIGHTS` tới server và bị treo vĩnh viễn ở `libevent select_dispatch`.
   - **Quy tắc**: Mọi lời gọi `tmux` bắt buộc phải đi qua hàm `run_tmux()` với `stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5`.
2. **Dấu hai chấm `:` trong `pane_id` của `tmux` và `ide:`**:
   - `pane_id` luôn chứa dấu `:` (ví dụ `coreinfraagy:0.0` hoặc `ide:agy-ide:vivas-infra`).
   - Khi đóng gói `callback_data` dạng `key:<pane_id>:<key>` hoặc `confirmkey:<pane_id>:<key>`, khi tách chuỗi **bắt buộc dùng `rsplit(":", 1)` từ bên phải sang** để lấy `key`, tuyệt đối không dùng `split(":", 2)` từ trái sang.
3. **Độ trễ `150ms` giữa `send-keys -l` và `Enter`**:
   - Các giao diện TUI hiện đại (`agy`, `claude`, `codex`) nếu nhận chuỗi ký tự và phím `Enter` ở cùng một mili-giây sẽ coi đó là thao tác Paste nhiều dòng (chỉ xuống dòng chứ không submit). Luôn giữ `time.sleep(0.15)` trước khi gửi `Enter`.
4. **Chỉ kiểm tra nhãn thông báo ở đầu câu (`startswith`)**:
   - Dùng `text.lstrip().startswith(...)` thay vì `in text` để tránh việc AI nhắc đến tên nhãn ở giữa câu giải thích cũng kích hoạt gửi Telegram nhầm.
5. **Đọc `transcript_full.jsonl` trên Antigravity (`agy`)**:
   - File `transcript.jsonl` của `agy` cắt ngắn các câu trả lời dài. Luôn ưu tiên kiểm tra và đọc `transcript_full.jsonl` cùng thư mục nếu tồn tại.
6. **Không bao giờ giữ vòng lặp chờ trong Hook (`Stop`)**:
   - Mọi Hook (`start`, `ask`, `stop`) phải thực thi xong và thoát ngay lập tức (`print("{}")`). Tuyệt đối không dùng vòng lặp chờ (`wait_for_ide_telegram_reply`) trong `Stop` hook vì sẽ làm treo Terminal chạy ngoài `tmux` và giữ trạng thái `Working` trên IDE.
   - Xem chi tiết luồng nghiệp vụ và lộ trình nâng cấp tại `FLOW_NGHIEP_VU_VA_KE_HOACH.md`.

### 7. Cơ chế Xác nhận 2 lớp (Pre-execution Hook ACK — `v1.2.0`)
- Khi gửi lệnh từ Telegram vào `tmux`, Daemon ghi `state/ack_pending_<safe_pane>.json` và tự động thoát `copy-mode` (`tmux copy-mode -q`).
- Ngay khi AI nhận được prompt (trước khi thực thi), Hook `start` (`PreInvocation` / `UserPromptSubmit`) xóa `ack_pending` và gửi bản tin `✅ OK — Đã nhận lệnh tại cửa sổ <pane>` kèm tên công cụ và thư mục.
- Nếu sau 5 giây Hook `start` không phản hồi (do gửi nhầm cửa sổ `tmux` hoặc con trỏ không ở ô nhập), Daemon gửi cảnh báo kèm màn hình hiện tại của `tmux`.

### 8. Khóa cố định cửa sổ `A`, Hỗ trợ Swipe `Reply` sang `B` & Phân loại Báo cáo (`v1.3.0`)
- **Không tự động đổi `active_pane.txt` ngầm**: Khi người dùng đã chọn cửa sổ `A`, `hook_notify.py` của cửa sổ `B` tuyệt đối không ghi đè `active_pane.txt`. Cuối tin nhắn của `B` sẽ có dòng nhắc `📌 Đang chọn mặc định: A`.
- **Hỗ trợ vuốt `Reply` trên Telegram**: `extract_reply_target_pane(msg)` trong `tg_bot_daemon.py` tự nhận diện tiêu đề `🖥 [B]` hoặc `📺 [B]` của tin nhắn được Reply để định tuyến riêng câu trả lời đó vào `B`.
- **Tách biệt `📋 Báo cáo tổng kết` và `🙋‍♂️ Cần chốt phương án / nghiệp vụ`**: `is_really_asking_decision()` kiểm tra đoạn kết của tin nhắn có câu hỏi cần người dùng quyết định hay chỉ là báo cáo hoàn thành cột mốc.
- **Quy tắc bắt buộc khi sửa đổi**: Mỗi lần thay đổi nghiệp vụ, **bắt buộc phải cập nhật đồng bộ file `FLOW_NGHIEP_VU_VA_KE_HOACH.md` và `AGENTS.md`**.

### 9. Giám sát Hết Quota & Báo khi Quota Reset chạy lại (`check_tmux_quota_limits`)
- `tg_bot_daemon.py` quét định kỳ vùng thanh trạng thái (status footer bên dưới vạch kẻ ô nhập `────────`) mỗi cửa sổ `tmux` mỗi 15 giây bằng biểu thức `QUOTA_LIMIT_RE`.
- Khi cửa sổ AI chạm trần (`⚠ Usage limit reached · limit resets ...`), gửi cảnh báo `🚨 CẢNH BÁO HẾT QUOTA / USAGE LIMIT!` kèm giờ reset và ghi `state/quota_alert_<safe_pane>.json` (chống báo lặp).
- Khi đến giờ reset và cảnh báo biến mất khỏi màn hình `tmux`, xóa `state/quota_alert_<safe_pane>.json` và gửi bản tin `🟢 ĐÃ RESET QUOTA & CHẠY LẠI!`.

### 10. Trạng thái sống trong `/list` & Dọn rác `state/` (`v1.4.0`)
- `get_pane_live_status(pid)` hiển thị trạng thái trực tiếp của từng cửa sổ ngay trong `/list` (`💤 Sẵn sàng`, `⚙️ Đang thực thi...`, `⏰ Đang hẹn giờ (Timer)`, `🚨 Hết quota`).
- `cleanup_orphan_state_files()` tự động xóa các file `start_*.txt` và `prompt_tg_*.flag` mồ côi cũ quá 12 giờ.

### 11. Xử lý Bảng hỏi (`1/2/3`, `y/n`) & Hàng đợi thông minh (`Smart Queue` — `v1.4.0`)
- `detect_interactive_prompt(pid)` nhận diện khi cửa sổ `tmux` đang dừng ở bảng hỏi quyền hoặc menu chọn `1/2/3`, `y/n`.
- Khi gửi `1`, `2`, `3`, `y`, `n` lúc đang ở bảng hỏi, Daemon bơm phím trực tiếp và trả lời ACK ngay mà không chờ Hook `start`. Khi AI đang bận (`⚙️ Đang thực thi...`), Daemon gửi lệnh vào queue tự nhiên của `tmux`, tăng bộ đếm trong `waiting_<safe_pane>.flag`, và pong ngay bản tin `⏳ OK — Đã nhận lệnh (Lệnh này đang chờ xử lý)`.

### 12. Nhận diện thao tác trực tiếp trên `tmux` / SSH (`is_tmux_session_actively_watched` — `v1.4.0`)
- Kiểm tra `tmux list-clients -t <session> -F "#{client_activity}"` (chỉ tăng khi người dùng gõ phím thật trên client, không tăng khi Bot dùng `send-keys`).
- Nếu người dùng đang gõ trực tiếp trên phiên `tmux` (trong vòng 5 phút), coi phiên đó đang ở chế độ `Here` kể cả khi màn hình vật lý của máy Mac đang khóa, tránh gửi trùng tin nhắn lên Telegram khi đang chat trực tiếp trên `tmux`.

- **3 Cờ cấu hình trong `config.json`**:
  - `notify_on_screen_lock` (bool, mặc định `true`): Có tự động báo Telegram khi khóa màn hình vật lý PC hay không.
  - `notify_on_away` (bool, mặc định `true`): Có tự động báo Telegram khi bật `/away` hay không.
  - `suppress_when_tmux_active` (bool, mặc định `true`): Tự động tắt báo trùng lên Telegram cho phiên `tmux` đang có người gõ trực tiếp (SSH/Terminal) trong 5 phút qua.

- `clean_tmux_screen(raw_text)` tự động cắt gọn các đường kẻ `────────` dài 200 ký tự, khoảng trắng thừa trên thanh status bar, `(ctrl+o to expand)` và nội dung `Thought` nội bộ để hiển thị gọn đẹp trên màn hình điện thoại.

### 13. 5 Chốt chặn chống rớt tin nhắn Telegram & `[TG_NOTIFY]` (`v1.5.1`)
1. **Không bao giờ xóa `waiting_<pane>.flag` tại `start` hook**: Trên `agy`, `PreInvocation` chạy lại ở mỗi bước gọi tool (`invocationNum = 1, 2, 3...`). Nếu xóa `waiting_<pane>.flag` hoặc `away_mode.flag` ở bước thứ 2 sẽ làm mất cờ khi `Stop` hook chạy. Chỉ `Stop` hook mới được tiêu thụ `waiting_<pane>.flag`.
2. **Luôn gửi khi có `[TG_NOTIFY]` (`tagged_notify`) hoặc `is_error`**: Công thức `should_notify = from_telegram or tagged_notify or is_error or (is_away and is_long_task)` đảm bảo mọi bản tin có `[TG_NOTIFY]` trên `tmux` luôn gửi về Telegram bất kể trạng thái `is_away`.
3. **Gộp các `PLANNER_RESPONSE` trong cùng lượt (`extract_last_turn`)**: Không để câu thông báo đặt hẹn giờ ngắn ở bước cuối ghi đè mất báo cáo `[TG_NOTIFY]` ở bước trước trong cùng 1 lượt.
4. **Giữ cờ `followup:<ts>` (120s) khi `fullyIdle == False`**: Khi lệnh `run_command` chuyển sang chạy nền (`WaitMsBeforeAsync`), `Stop` lần 1 chuyển cờ thành `followup:<ts>` để khi lệnh nền chạy xong vài giây sau, `Stop` lần 2 vẫn gửi kết quả cuối cùng về Telegram.
5. **Tách riêng `from_tg_dispatch_<safe_pane>.flag` theo từng cửa sổ** và bổ sung fallback gửi không kèm `reply_markup` + log vào `daemon.err.log`.

### 14. Nút `[💬 Trả lời]` (`reply:<pane_id>`) & Tự động khôi phục nhãn `tmux` khi Server Restart (`v1.6.0`)
- Mọi tin nhắn từ cửa sổ `tmux` (`ack`, `stop`, `ask`, `queued pong`) đều có nút **`[💬 Trả lời]`** (`reply:<pane_id>`) ở góc trái trên cùng.
- Khi bấm **`[💬 Trả lời]`** vào tin nhắn của cửa sổ `X`, Daemon kiểm tra `X` còn tồn tại trong `tmux` không, lưu `state/reply_next_pane.json` (TTL 300s) và bật khung nhập `ForceReply` (`💬 [X] — Nhập nội dung bạn muốn gửi vào X:`) để tin nhắn tiếp theo đi thẳng vào `X` mà không làm thay đổi `active_pane.txt`.
- Khi `agy` background server tự restart làm mất `TMUX_PANE` trong `os.environ`, `get_session_label()` tự đối chiếu `cwd` với `tmux list-panes` để giữ đúng tên cửa sổ `tmux` (thay vì rơi về `term:<folder>`).
- Nâng giới hạn hiển thị `/list` từ 8 lên 20 cửa sổ.

### 15. Backend Multiplexer Có Cấu Hình — Hỗ Trợ `psmux` (`v1.7.0`)
- **Không được hardcode `"tmux"`**. Binary multiplexer đọc từ `config.json` → `"multiplexer_bin"` (mặc định `tmux`; đặt `"psmux"` nếu dùng psmux).
- `tg_bot_daemon.py`: `get_mux_bin()` (cache 10s) → `has_mux()` / `run_mux()`; giữ alias `run_tmux = run_mux`, `has_tmux = has_mux` cho code cũ.
- `hook_notify.py`: `get_mux_bin()` / `run_tmux()` / `is_real_tmux()` tương tự — **phải dùng cùng `config.json`** với daemon, nếu không hai bên sẽ dùng khác multiplexer.
- **Biến môi trường pane**: `get_pane_id_from_env()` lần lượt kiểm tra `TMUX_PANE` → `PSMUX_PANE` → `MUX_PANE`. psmux **không** dùng `TMUX_PANE`, nên nếu chỉ đọc `TMUX_PANE` thì Hook sẽ luôn rơi về nhãn `term:<folder>` và mất ID pane thật.
- Lệnh **tmux-specific** (`list-clients`, `client_activity`) chỉ chạy khi `is_real_tmux()` đúng — psmux thường không có, tính năng "tắt báo trùng khi đang gõ trực tiếp" sẽ tự động bỏ qua.
- `get_session_label()`: nếu `display-message` không trả về `#S:#I.#P` (định dạng ID của psmux khác tmux) → dùng luôn ID pane lấy từ biến môi trường.

### 16. Chụp Ảnh Cửa Sổ Thật Trên Windows (`v1.7.0`)
- `📸 Xem màn hình` (nút `screen:<pane>`) và lệnh `/screen` gọi `send_screen_snapshot()`:
  - Có multiplexer → `capture-pane` (text) như cũ.
  - **Windows native → gửi ẢNH PNG** của cửa sổ qua `sendPhoto` (multipart/form-data tự dựng, không dùng thư viện ngoài).
- `capture_window_png(pid)`: ghi `state/_screenshot.ps1` rồi chạy `powershell -File`. PowerShell dùng `PrintWindow(PW_RENDERFULLCONTENT)` để chụp được cả khi cửa sổ bị che; nếu thất bại thì fallback `SetForegroundWindow` + `CopyFromScreen`. Ảnh được thu nhỏ về tối đa 1400px bề ngang để nhẹ khi gửi.
- **DPI**: gọi `SetProcessDPIAware()` trước khi `GetWindowRect` để không bị lệch toạ độ trên màn hình scaling >100%.
- `resolve_window_pid(pane)` là bước **bắt buộc** để ánh xạ định danh sang PID:
  - PID (`2996`) → dùng luôn.
  - `term:<folder>` hoặc `<node>:<folder>` → tìm cửa sổ có **tiêu đề** hoặc **command line** chứa `<folder>`; fallback cửa sổ đang chọn mặc định.
- `list_windows_terminals()` giờ trả 4 trường `PID | ProcessName | WindowTitle | CommandLine` và có **cache 8 giây** (truy vấn `Win32_Process` khá chậm).
- **Quy tắc**: khi sửa `capture_screen` phải giữ nguyên chữ ký trả về **text** (dùng ở `verify_ai_received_command`), phần ảnh nằm ở `capture_screen_photo()`.

### 17. Chống "Kẹt" Daemon — Đây là nguyên nhân nút bấm im lặng (`v1.7.0`)
Triệu chứng: **nhận thông báo bình thường** (do Hook gửi trực tiếp) nhưng **bấm nút / gõ tin không có phản hồi**. Ba nguyên nhân đã sửa:
1. **`offset` phải cập nhật TRƯỚC khi xử lý update.** Nếu cập nhật sau, một update lỗi sẽ bị xử lý lại vô hạn và **mọi** tin nhắn/nút bấm về sau đều bị kẹt.
2. **Bọc try/except theo từng update** (`handle_update()` / `handle_text_message()`), lỗi của 1 update không được làm hỏng các update còn lại.
3. **`answerCallbackQuery` phải bọc try/except** (`tg_answer()`): query cũ >10 phút trả HTTP 400 → nếu để nổi lỗi sẽ kẹt daemon ngay.
- Daemon ghi log vào `daemon.log` / `daemon.err.log` (không phụ thuộc stdout — trên Windows chạy `pythonw` nên stdout bị `DEVNULL`), ghi heartbeat `state/daemon_heartbeat.txt`, và có lệnh **`/ping`** để kiểm tra daemon còn sống + xem cấu hình.
- `install.py` (Windows) giờ truyền stdout/stderr của daemon vào file log và **kiểm tra heartbeat** sau khi khởi động để báo ngay nếu daemon không sống.

### 18. Bật/Tắt Bot & Chống Chạy Trùng (`v1.7.0`)
- **Daemon TỰ BẬT khi đăng nhập máy** qua script `Startup\ai-telegram-daemon.vbs` (Windows) / `LaunchAgent` (macOS) / `systemd --user` (Linux).
- Bộ lệnh CLI của `install.py` (nhận cả tham số từ `install.bat` / `install.sh`):
  - `--status` / `-s`: in trạng thái tự khởi động + heartbeat daemon + trạng thái hook (`check_hooks_installed()`).
  - `--disable` / `--off`: **TẮT TẤT CẢ** — gỡ script Startup (Windows) hoặc unload service + dừng daemon + **gỡ hook** (`remove_all_hooks()`) và **gỡ rule `[TG_NOTIFY]`** khỏi `CLAUDE.md`/`GEMINI.md`/`AGENTS.md`. Thêm cờ `--keep-hooks` để chỉ tắt daemon mà giữ hook.
  - `--enable` / `--on`: **BẬT LẠI TẤT CẢ** — cài lại hook + rule + tạo lại script Startup + chạy daemon.
  - `--start` / `--stop`: chỉ bật/tắt tiến trình daemon, **không** đổi cấu hình tự khởi động.
  - `--hooks-only`: chỉ cài hook + rule, **không** chạy daemon và **không** bật tự khởi động.
- ⚠️ **Quy tắc đa máy**: chỉ **một** daemon được chạy cho mỗi bot token. Nếu chạy daemon trên 2 máy (ví dụ máy local + máy remote), cả hai sẽ poll `getUpdates` cùng lúc → Telegram trả `409 Conflict` và nút bấm bị "mất". Máy phụ chỉ nên chạy `install.py --hooks-only`.

### 19. Script Tự Kiểm Tra (`selftest.py` — `v1.7.0`)
- `python selftest.py [--shot]` kiểm tra 9 mục: config.json → phiên bản 2 module → multiplexer → cửa sổ Windows → ánh xạ `term:<folder>` → PID → chụp ảnh → Telegram API (kèm phát hiện **409 Conflict** = daemon khác đang poll cùng token) → hook đã cài → heartbeat daemon.
- Dùng script này **trước khi debug** khi nút bấm "không phản hồi": nó chỉ ra ngay là do token xung đột, do chưa mở cửa sổ nào, hay do daemon chưa chạy.
- `--shot` lưu ảnh chụp thử vào `state/selftest.png` để kiểm tra bằng mắt.

### 20. Gửi Lệnh Vào Ô Chat AI Của IDE Bằng UI Automation (`v1.8.0`)
**Vấn đề gốc**: `AppActivate` chỉ đưa **cửa sổ** lên trước, **không** đưa con trỏ vào ô chat AI. Với IDE (Electron: Antigravity/VS Code/Cursor) lệnh gửi từ Telegram rơi vào trình soạn thảo hoặc mất → hook `PreInvocation` không chạy → cảnh báo "AI chưa nhận được lệnh" sau 5 giây.

- `send_to_ide_window(pid, text)`: chạy `state/_uia_send.ps1` (`UIA_SEND_PS`) — dùng `UIAutomationClient`/`UIAutomationTypes` để:
  1. `AutomationElement::FromHandle(hwnd)` → cây UIA của cửa sổ IDE.
  2. Tìm mọi `ControlType::Edit`; chọn phần tử có `Name/AutomationId/ClassName` chứa mẫu nhận dạng ô chat AI (`ask anything`, `chat`, `prompt`, `message`, `send`, `gemini`, `claude`, `codex`, `antigravity`, `agent`, `input`, `composer`…).
  3. `SetFocus()` → `Ctrl+V` (clipboard đã đặt sẵn) → `Enter`.
- **Chống dán nhầm vào editor (bắt buộc)**: nếu không tìm thấy ô AI, script trả về thông tin **phần tử đang focus** (`AutomationElement::FocusedElement`). Nếu phần tử đó là `Edit`/`Document` (trình soạn thảo mã) → daemon **TỪ CHỐI dán** (`status="refused"`) và báo cho người dùng *bấm vào ô chat AI rồi gửi lại*. Tuyệt đối không dán mù vào editor (có thể ghi đè file nguồn).
- `send_to_target()` trả về trạng thái: `"sent" | "refused" | "no_input" | "error"`. Khi `refused/no_input` → `build_ide_refuse_notice()` gửi hướng dẫn cụ thể **ngay**, không chờ 5 giây.
- Danh sách tiến trình IDE: `IDE_PROCESS_NAMES` (`code`, `antigravity`, `cursor`, `windsurf`, `idea64`…). Phần còn lại (Terminal/PowerShell/CMD) dùng `AppActivate` + clipboard như cũ.
- Mọi lần gửi đều được `log_line()` ghi lại: pane, PID, tên tiến trình, có phải IDE không, kết quả UIA — để chẩn đoán từ `daemon.log`.

### 21. Lệnh `/force` — Lối Thoát An Toàn Cho IDE (`v1.8.0`)
- Khi UIA **không lộ** ô chat AI (một số bản Electron chưa bật accessibility), daemon **từ chối dán mù** và báo: *"bấm vào ô chat AI rồi dùng `/force <lệnh>`"*.
- `/force <lệnh>` → `send_to_target(..., force=True)` → `send_to_ide_window_force()` → `AppActivate` + clipboard + `Ctrl+V` + `Enter` (bỏ qua UIA). Dán vào **phần tử đang focus** ⇒ người dùng phải bấm vào ô chat AI trên PC trước.
- Vì sao không tự động dán mù: IDE có **nhiều ô nhập** (editor + ô chat AI) và cả hai đều là `ControlType.Edit`; nếu UIA không lộ tên/placeholder thì **không phân biệt được** → dán mù có thể ghi đè file nguồn. Chọn "từ chối + hướng dẫn" an toàn hơn.
- Khi không tìm thấy ô AI, script UIA **dump cây UIA** ra `state/_uia_dump.txt` và daemon ghi 40 dòng đầu vào `daemon.log` ⇒ có thể tinh chỉnh `$patterns` trong `UIA_SEND_PS` mà không cần chạy script tay.
- Phần tử đang focus phải được **phân biệt theo tiến trình**: `AutomationElement::FocusedElement` là phần tử đang focus của **toàn hệ thống** → script ghi kèm `this-app` / `other-app` để không kết luận nhầm "đang ở editor".

### 22. Script `install_agy.py` — Tìm & Cài Antigravity CLI (`v1.8.0`)
- **Vấn đề gốc**: ô chat AI trong IDE là WebView Electron → không tự động hoá được. CLI `agy` trong terminal thì mọi tính năng của tool chạy đúng bản đồ (nhận text chắc chắn, hook bắn, `capture-pane` ra text, bắt bảng hỏi `1/2/3`, monitor quota).
- `python install_agy.py` (hoặc `python install.py --install-agy`):
  1. `shutil.which("agy")` + các shim `agy.cmd/.bat/.ps1` trong `%APPDATA%\npm`.
  2. Quét thư mục cài IDE (`%LOCALAPPDATA%\Programs\Antigravity`, `%APPDATA%\npm`, …) tìm `agy.exe/cmd/bat/ps1` (depth ≤ 4).
  3. Launcher trong `%LOCALAPPDATA%\Microsoft\WindowsApps`.
  4. Nếu chưa có → `--npm [package]` để cài (tên package lấy từ `config.json → install_agy.package`, **không hardcode tên package** vì Antigravity CLI mới ra).
- Thêm thư mục vào PATH của USER bằng `winreg` (`HKCU\Environment\Path`) — vĩnh viễn, không cần đăng nhập lại.
- **Không** sửa PATH nếu không tìm thấy `agy` thật (tránh ghi rác PATH).

### 23. Sửa "Cửa Sổ Console Nhấp Nháy" Khi Chạy Ngầm (`v1.8.1`)
**Triệu chứng**: mở máy → cửa sổ PowerShell/console bật lên rồi tắt liên tục.

**Nguyên nhân gốc (2 tầng)**:
1. **Script giám sát `.vbs` spawn daemon mới mỗi 15 giây.** Mỗi bản sao mới chạy `main()` → `acquire_single_instance_lock()` → kiểm tra PID → trước đây dùng `tasklist.exe` (**console app**) → Windows mở thêm cửa sổ console cho tiến trình con (vì `pythonw` không có console) → **nhấp nháy mỗi 15s**.
2. **Daemon đang chạy gọi `powershell.exe`** (`list_windows_terminals()`) mỗi vòng lặp → mỗi lần là **một cửa sổ PowerShell mở rồi đóng** (đặc biệt nặng vì truy vấn `Get-CimInstance Win32_Process` mất 1–2s).

**Cách sửa (bắt buộc áp dụng khi thêm lệnh con mới)**:
- Mọi lệnh console con **phải** đi qua `run_hidden()` (đặt `creationflags=CREATE_NO_WINDOW` trên Windows) — không spawn trực tiếp `subprocess.run()`.
- `is_pid_alive()` và `get_process_name_for_pid()` dùng **Win32 API qua `ctypes`** (`OpenProcess`, `QueryFullProcessImageNameW`) → **không spawn tiến trình nào**, nhanh hơn và không bao giờ nhấp nháy.
- Script giám sát `.vbs` **chỉ spawn lại daemon khi heartbeat `state/daemon_heartbeat.txt` đã cũ > 3 phút** (trước: spawn vô điều kiện mỗi 15s). VBS dùng `FileSystemObject.GetFile(hb).DateLastModified` để kiểm tra, không cần chạy lệnh khác.
- `install.py` phải gọi `kill_old_supervisor()` khi cài lại: file `.vbs` cũ đã bị ghi đè nhưng **tiến trình `wscript.exe` cũ vẫn chạy vòng lặp trong RAM** và tiếp tục spawn daemon → nếu không kill thì hiện tượng nhấp nháy vẫn còn dù đã sửa code.
- `os.kill(pid, 0)` **không** dùng trên Windows (sẽ giết process) — xem mục 18.

### 24. Hook Phải Chạy Bằng `pythonw.exe` (`v1.8.1`)
- **Thủ phạm thứ 3 (mới phát hiện)**: `install.py` ghi lệnh hook bằng `python.exe` — đây là **console app**. Khi **IDE (tiến trình GUI)** gọi hook, Windows tạo một cửa sổ console cho chính `python.exe` đó → **cửa sổ đen bật lên rồi tắt mỗi lần AI chạy lượt**.
- Sửa: `install.py` dùng **`pythonw.exe`** (không console) cho `HOOK_CMD_PREFIX` trên Windows (fallback về `python.exe` nếu không có `pythonw.exe`).
- **An toàn khi bỏ stdout**: dưới `pythonw.exe`, `sys.stdout` là `None` → `print()` bị CPython bỏ qua (không lỗi). Hook chỉ in JSON thông báo (`{}` hoặc `{"decision":"allow"}`) — mọi hành vi quan trọng đều đi qua HTTP nên **không mất gì**.
- Vì không còn stdout/stderr, lỗi hook giờ được ghi vào **`state/hook.err.log`** (bọc try/except ở `__main__` của `hook_notify.py`).
- Script chẩn đoán `watch_console.ps1` (repo root): in ra tiến trình console mới xuất hiện + **tiến trình mẹ** → chỉ ra chính xác thủ phạm (daemon / hook từ IDE / script giám sát / app khác).

### 25. Định Tuyến ID: Pane Multiplexer vs PID Cửa Sổ Windows (`v1.8.1`)
**Bug triệu chứng**: đã cài `agy` + dùng `psmux`, gửi lệnh từ Telegram → *"AI chưa nhận được lệnh"* và *"Cửa sổ [10640] không còn tồn tại trong tmux"*.

**Nguyên nhân**: daemon quyết định "gửi qua multiplexer" bằng điều kiện `has_mux() and not pane.startswith("term:")`. Nhưng:
1. `psmux` có trong PATH ⇒ `has_mux()` đúng.
2. Nhưng `psmux list-panes -a -F ...` **không trả về pane nào** (lệnh không tương thích) ⇒ `list_targets()` rơi về danh sách **cửa sổ Windows** (PID `10640`).
3. Khi gửi lệnh, `10640` không bắt đầu bằng `term:` ⇒ đi nhánh multiplexer ⇒ `psmux send-keys -t 10640` ⇒ **không có pane tên đó** ⇒ lệnh rơi mất, im lặng.

**Sửa (bắt buộc)**: định tuyến dựa trên **bản thân ID**, không dựa trên sự tồn tại của multiplexer:
- `is_mux_pane(pane)`: `False` nếu `pane.isdigit()` (PID Windows) hoặc `pane.startswith("term:")`; `True` nếu nằm trong `MUX_PANE_IDS` (lấy từ lệnh list-panes) hoặc có dạng `session:0.1` / `%1`.
- Mọi nơi gọi multiplexer (`capture_screen`, `capture_screen_photo`, `send_to_target`, `detect_interactive_prompt`, `get_pane_live_status`, `check_tmux_quota_limits`, `check_tmux_prompts_and_queues`) **phải** dùng `is_mux_pane()` thay cho `has_mux()`.
- `list_targets()` phải **ghi log ERROR** khi multiplexer có trong PATH nhưng không liệt kê được pane (kèm lệnh gợi ý để kiểm tra) — đây là manh mối để biết multiplexer có tương thích không.
- `send_to_target()` sau khi `send-keys` phải **kiểm tra lại bằng `capture-pane`**; nếu lỗi thì trả `"error"` và ghi log (không giả vờ thành công).
- `get_active_pane()` ưu tiên **pane multiplexer** đang chạy AI trước cửa sổ Windows.

### 26. Electron/Chromium: Ô Chat AI KHÔNG NẰM Trong Cây UIA Của Cửa Sổ (`v1.8.1`)
**Phát hiện thực địa** (từ log daemon): ô chat AI của IDE Antigravity có
`Name = "Write a message..."`, `ClassName = "Ui::InputField::Inner"`, `ControlType = Edit`
— **nhưng** `AutomationElement::FromHandle(hwnd).FindAll(Descendants)` **KHÔNG tìm thấy** nó.

**Lý do**: Chromium/Electron render nội dung web ở **tiến trình riêng (renderer process)** ⇒ cây UIA
của handle cửa sổ chính không chứa phần tử web. Nhưng `AutomationElement::FocusedElement` **vẫn thấy** nó
(vì nó là phần tử đang nhận focus).

**Sửa trong `UIA_SEND_PS`**:
1. Dò cây UIA của cửa sổ (Edit/Document/ComboBox/Text) theo `$patterns` — như trước.
2. **Nếu không có** → kiểm tra `FocusedElement`: nếu tên/class của nó khớp `$patterns`
   (`write a message`, `inputfield`, `message`, `chat`…) → dùng chính phần tử đó: `SetFocus()` → `Ctrl+V` → `Enter`.
   ⇒ Người dùng chỉ cần **bấm vào ô chat AI một cái** trên PC, rồi gửi lệnh từ Telegram là chạy.
3. Thêm `SetForegroundWindow`/`BringWindowToTop` cho cửa sổ đích trước khi `SendKeys` — nếu không,
   `SendKeys` gửi phím vào **cửa sổ đang foreground** (có thể là cửa sổ khác).
4. **Phạm vi ứng dụng**: `ProcessId` của phần tử focus khác PID cửa sổ (vì renderer process) ⇒ phải
   **leo lên cửa sổ cha (top-level)** bằng `TreeWalker.ControlViewWalker` rồi mới so `ProcessId`.
   Nhãn `this-app` / `other-app` dựa trên phép leo này, không dựa trên PID trực tiếp.

### 27. `/screen` Không Được Hiện Transcript Của Phiên Khác (`v1.8.1`)
**Triệu chứng**: chọn cửa sổ Antigravity IDE rồi bấm `📸 Xem màn hình` → nhận về *"👤 User: … 🤖 AI: …"*
— tức **log chat của một phiên hoàn toàn khác** (chính là phiên Claude Code đang chạy), không phải màn hình IDE.

**Nguyên nhân**: `capture_screen()` với cửa sổ Windows rơi vào fallback và đọc
`state/last_transcript.txt` — đó là transcript của **phiên hook chạy gần nhất trên máy**, không
phải của cửa sổ được chọn.

**Sửa**:
- `capture_screen()` với cửa sổ Windows chỉ đọc **`last_transcript_<safe_pane>.txt`** (transcript
  riêng của đúng cửa sổ đó) — **bỏ hẳn** fallback `last_transcript.txt`.
- Không tìm thấy transcript riêng ⇒ trả lời giải thích *"🪟 đây là cửa sổ Windows, hãy bấm 📸 để xem ảnh"*.
- `send_screen_snapshot()`: khi **đã gửi ảnh** thì **không** gửi kèm text fallback (trừ khi text đó là
  nội dung riêng của cửa sổ, tức không bắt đầu bằng `⚠️` / `🪟`).
- Quy tắc chung: **không bao giờ hiển thị nội dung của phiên khác dưới tên một cửa sổ khác**.

### 28. Thông Báo Phải Nói Rõ **Loại Đích**, Không Phải Tên Binary Cấu Hình (`v1.8.1`)
- Chuỗi "tmux"/"psmux" trong tin nhắn cảnh báo đến từ `get_mux_bin()` (đọc `config.json`) ⇒ nếu `config.json` để `"tmux"` thì mọi tin nhắn in ra "tmux" dù máy dùng psmux ⇒ gây hiểu nhầm.
- Sửa: `verify_ai_received_command()` mô tả theo **bản thân đích** (`is_mux_pane(pane)`):
  - pane multiplexer → *"đã gửi vào pane `psmux`"*
  - cửa sổ Windows → *"đã gửi vào cửa sổ Windows"* + giải thích riêng (dán bằng clipboard, cần con trỏ ở ô nhập, có thể cửa sổ không có AI).
- `config.json` phải khai `"multiplexer_bin": "psmux"` nếu dùng psmux (đọc động, **không cần restart daemon** — cache 10s).

### 29. Phân Biệt **Ô Chat AI** vs **Ô Nhập Terminal** Khi Dán (`v1.8.1`)
**Thực tế**: trong IDE Antigravity, AI thường chạy dưới dạng **CLI trong terminal nhúng** (Antigravity CLI 1.5.18), **không** phải ô chat bên phải (ô chat là model khác: *"Gemini 3.8 Flash High"*). Ô chat có placeholder *"Ask anything…"*, còn terminal có `xterm-helper-textarea`.

- `$patterns` (dò cây UIA) = mẫu **ô chat AI**.
- `$focusPatterns` (dò phần tử đang focus) = mẫu **rộng hơn**, có thêm `xterm`, `terminal input`, `xterm-helper-textarea`, `auto mode`, `antigravity cli` ⇒ nhận ra ô nhập terminal khi người dùng bấm vào terminal.
- **Phân loại đích** để tránh rủi ro: nếu blob của phần tử focus chứa `xterm`/`terminal` ⇒ `kind='terminal'` ⇒ **gộp mọi xuống dòng thành 1 dòng** trước khi dán. Dán text nhiều dòng vào terminal = **chạy nhiều lệnh liên tiếp** (rất nguy hiểm). Ô chat thì giữ nguyên xuống dòng.
- Vì vậy script UIA **tự đặt clipboard** (đọc nội dung từ `state/_send_text.txt` do Python ghi ra) thay vì Python đặt sẵn — vì Python không biết trước đích là chat hay terminal.
- Quy tắc: **dán vào terminal phải là 1 dòng**; **dán vào ô chat** mới được nhiều dòng.

### 30. psmux v3.4.0 — KHÔNG hỗ trợ `list-panes -a -F` (`v1.8.1`)
**Đã xác minh bằng `psmux --help`**: `list-panes, lsp — List panes in current window` (không có `-a`, không có `-F`).
⇒ Lệnh `psmux list-panes -a -F "..."` **không in gì** ⇒ daemon không lấy được pane ⇒ rơi về chế độ cửa sổ Windows.

**Cú pháp psmux đã xác minh**:
- `psmux list-sessions` / `ls` → danh sách session.
- `psmux list-windows -t <session>` → danh sách window của session.
- `psmux list-panes -t <session>:<window>` → danh sách pane của window (ID dạng `%0`, `%1`).
- `psmux capture-pane -t <pane> -p` → in nội dung pane ra stdout ✓.
- `psmux send-keys -t <pane> -l "<text>"` và `psmux send-keys -t <pane> Enter` ✓.
- `psmux display-message -t <pane> -p "<format>"` → hỗ trợ format variables kiểu tmux ✓.
- Target: `session:window.pane`, `:2.1`, `%3` ✓.
- **Biến môi trường bên trong pane**: `TMUX_PANE` (dạng `%1`) + `PSMUX_SESSION_NAME` — tương thích tmux ✓.

**Sửa trong `tg_bot_daemon.list_mux_panes()`**:
1. Thử `list-panes -a -F` (tmux-style) → nếu ra dòng thì dùng luôn.
2. Nếu không (psmux) → duyệt **session → window → pane**, lấy `path` qua `display-message -t <pane> -p "#{pane_current_path}"`.
3. ID lấy từ phần trước dấu `:` của mỗi dòng (`%0: [80x24] ... "pwsh"` → `%0`).

**Sửa trong `hook_notify.get_session_label()`**: psmux đặt `TMUX_PANE` ⇒ hook dùng luôn ID `%N` (target hợp lệ cho `send-keys -t %N`) khi `display-message` không trả về `#S:#I.#P`. `is_in_tmux_session()` **không** cần `has_tmux()` nữa — chỉ cần có biến pane (vì hook do IDE gọi, PATH có thể không chứa multiplexer).

**Tìm binary**: `find_mux_binary()` — tìm trong PATH trước, sau đó quét `%LOCALAPPDATA%\Microsoft\WinGet\Packages\*` (psmux cài bằng winget nằm trong package dir, daemon chạy ngầm từ lúc đăng nhập nên thường **không** có trong PATH của tiến trình đó).

### 31. Hook và Daemon Phải Dùng Cùng Dạng ID Pane (`v1.8.1`)
**Bug đã gặp**: daemon dùng ID pane `%1` (psmux) ⇒ ghi `state/ack_pending_%1.json`; hook lại lấy ID theo kiểu tmux (`ai:0.1`) ⇒ xoá `state/ack_pending_ai_0_1.json` ⇒ **hai bên không bao giờ gặp nhau** ⇒ ACK không bao giờ về ⇒ cảnh báo "AI chưa nhận được lệnh" **giả** (dù lệnh đã vào AI và AI đang chạy).

**Sửa (2 lớp)**:
1. `hook_notify.get_session_label()`: nếu multiplexer **không phải tmux thật** (psmux) → **trả về luôn ID gốc** (`TMUX_PANE` = `%N`), KHÔNG dùng `display-message` (vì daemon liệt kê psmux theo ID gốc).
2. `tg_bot_daemon`: ghi file ACK cho **cả 2 dạng ID** — `ack_pending_<safe(pane)>.json` **và** `ack_pending_<safe(canonical)>.json` (canonical lấy từ `display-message -p "#S:#I.#P"`). `verify_ai_received_command()` theo dõi **cả hai** tên ⇒ ACK nhận được dù hook dùng dạng nào.
   ⇒ Cùng nguyên tắc áp dụng cho `waiting_<pane>.flag` (file mà hook `stop` kiểm tra để gửi câu trả lời về Telegram).

### 32. Dán Vào Ô Nhập Đang Còn Text Sẽ Bị **NỐI** (`v1.8.1`)
- Triệu chứng: gửi lệnh từ Telegram → AI nhận được `"send-keys -t %0kiểm tra cho tôi agent đang có"` (text cũ của user **còn sót** trong ô nhập bị nối tiếp).
- Sửa: `mux_input_has_text(pane)` đọc `capture-pane`, dòng cuối bắt đầu bằng `>` và có text sau ⇒ gửi **`C-u`** (xoá dòng) trước khi dán lệnh mới.
- Thứ tự gửi chuẩn (đúng gotcha #3 của AGENTS.md): `C-u` (nếu cần) → `send-keys -l <text>` → **chờ ≥150ms** → `send-keys Enter`.

### 33. Timeout ACK & Nhận Diện "AI Đang Bận" (`v1.8.1`)
- Timeout ACK nâng 5s → **9s** (AI có thể cần vài giây để bắt đầu lượt, nhất là khi đang xử lý lượt trước).
- Nếu hết timeout mà `get_pane_live_status()` báo *đang thực thi / timer / monitor* ⇒ **không cảnh báo**, mà trả *"⏳ OK — Đã gửi vào hàng đợi, AI đang bận"*. Lệnh đã vào hàng đợi tự nhiên của TUI ⇒ không phải lỗi.
- Cấu trúc hook để **gỡ** phải soi đúng hình dạng file (rất dễ sai):
  - Claude Code `~/.claude/settings.json`: `data["hooks"][event][] = { "hooks": [ { "command": ... } ] }` → dùng `_strip_hook_entries()` để lọc entry có `command` chứa `hook_notify.py`.
  - Antigravity `~/.gemini/config/hooks.json`: key `data["telegram-bridge"]` → xoá cả key.
  - Codex `~/.codex/hooks.json`: giống Claude Code.
  - Rule file: xoá đúng khối `RULE_BLOCK.strip()`.

### 34. Backend Paseo — Điều Khiển Agent Qua `paseo send` (`v1.8.1`)
**Bối cảnh**: Paseo là daemon quản lý agent (chạy CLI `claude`/`codex`/`agy` thật trong terminal của nó). Người dùng muốn điều khiển agent đó từ Telegram thay vì pane tmux/psmux.

**Kiểu A (đã implement)**: agent chạy **CLI thật** ⇒ **Hook của CLI vẫn bắn** ⇒ đường thông báo Telegram **giữ nguyên**, chỉ thay phần "bơm lệnh".
- **Paseo KHÔNG có khái niệm "chat id"** — nó định danh theo `agentId` (+ `serverId` cho host từ xa). `chat_id` trong `config.json` vẫn là phía Telegram, **không đổi**.
- `config.json` → `"target_backend": "paseo" | "mux" | "windows"` (không khai thì tự nhận diện: có multiplexer → `mux`, không → `windows`).
- `list_paseo_agents()`: `paseo ls --json` → parse `agentId|title|status|cwd|serverId` (cache 8s). Chấp nhận nhiều hình dạng JSON: list trực tiếp, hoặc khoá `agents`/`items`/`data`/`results`/`sessions`.
- `send_to_paseo_agent()`: `paseo send <agentId> "<prompt>"` (mặc định `queue`); lệnh `/force` → `--behavior steer`. Nếu agentId dạng `agent_xxx` bị từ chối ⇒ thử lại với ID rút gọn.
- **ACK tức thì**: `paseo send` trả về ngay ⇒ daemon báo *"⏳ OK — Đã gửi lệnh vào agent Paseo"* **không chờ hook** ⇒ bỏ được cảnh báo 9s.
- `capture_paseo_status()`: `/screen` cho agent → hiện title/status/cwd/host (dạng text).
- `pane_state_aliases(pane)`: ghi cờ `waiting_*`, `from_tg_dispatch_*`, `ack_pending_*` dưới **NHIỀU bí danh** — `agentId`, `<node>:<folder>`, `term:<folder>`, `<folder>` ⇒ hook (vốn dùng nhãn `term:<folder>` cho phiên CLI không có multiplexer) **luôn tìm thấy cờ** ⇒ câu trả lời của AI về Telegram không bị rớt.
- Ưu điểm lớn: **daemon Telegram chỉ cần chạy ở MỘT máy** nhưng điều khiển agent ở **mọi host** qua `serverId` ⇒ hết vướng `409 Conflict` khi chạy nhiều máy.

### 35. `net_compat.py` — Ép IPv4 + Retry Cho API Telegram (`v1.8.1`)
**Triệu chứng**: `install.py` báo `[WARN] Không gửi được tin nhắn test Telegram: <urlopen error [WinError 10054] An existing connection was forcibly closed by the remote host>`.
- **WinError 10054** = bị RST từ server. Trên Windows nguyên nhân phổ biến nhất: `api.telegram.org` được phân giải sang **IPv6** trong khi đường IPv6 của mạng nội bộ hỏng.
- `net_compat.force_ipv4()`: vá `socket.getaddrinfo` để **mọi** kết nối dùng `AF_INET` (áp dụng cho urllib/http.client).
- `net_compat.with_retry(fn, attempts, delay, backoff)`: thử lại 2–4 lần với backoff tăng dần cho lỗi mạng tạm thời ⇒ **tin nhắn Telegram không còn bị mất im lặng**.
- Áp dụng cho: `tg_bot_daemon.tg_api()` (mọi lệnh API), `tg_send_photo()`, `hook_notify.send_telegram()`, `install.send_welcome_message()`.
- ⚠️ `net_compat.py` là **file dùng chung** ⇒ khi triển khai phải copy **cả thư mục** (thiếu file này thì 3 script vẫn chạy được nhờ fallback nội tuyến trong `except ImportError`).

### 36. Lệnh `/shot` — Chụp Ảnh Giao Diện Web Từ `localhost` (`v1.8.1`)
- **Vấn đề**: `/screen` chỉ chụp được **cửa sổ** (và với pane multiplexer là ra **text** buffer terminal) ⇒ không xem được **giao diện web** mà app đang chạy.
- `capture_url_png(url)`: dùng **Edge/Chrome headless** (`--headless=new`, fallback `--headless`) với `--screenshot=`, `--window-size=1600,900`, `--virtual-time-budget=8000` (để SPA kịp render) và `--user-data-dir=state/_browser_profile` (profile riêng, không đụng profile Chrome của bạn).
- `find_browser()`: dò theo đường dẫn cài đặt (Edge/Chrome ở `Program Files`, `%LOCALAPPDATA%`) rồi tới `shutil.which()`.
- `detect_local_dev_url()`: dò các cổng dev phổ biến (`3000, 5173, 8080, 4200, 8000, 5174, 9000, 4000, 1234, 5500, 3030`) bằng `socket.create_connection` ⇒ `/shot` không cần URL.
- Lệnh: `/shot`, `/web`, `/screenshot` (có trong menu Bot). Trả về ảnh PNG qua `sendPhoto`.
- ⚠️ Cần Edge hoặc Chrome trên máy đang chạy daemon (không cần cài thêm gì).
- **Script giám sát (Windows)**: file `.vbs` chạy vòng lặp `Do While True` — mỗi 15 giây thử khởi động lại daemon ⇒ **tự phục hồi khi daemon crash** (đây là nguyên nhân gốc khiến bot "chết lặng" trước đây).
- **Chống chạy trùng**: daemon giữ file khóa `state/daemon.pid`. Bản mới khởi động mà thấy PID cũ **còn sống** → thoát im lặng (không spam log). File khóa được giải phóng trong `finally` khi tiến trình kết thúc.
- ⚠️ **Không dùng `os.kill(pid, 0)` để kiểm tra tiến trình trên Windows** — trên Windows `os.kill` gọi `TerminateProcess` ⇒ sẽ **giết** tiến trình đang kiểm tra. Dùng `tasklist /FI "PID eq <pid>"` (hàm `is_pid_alive()`).
- `/away` và `/here` trên Telegram **chỉ** quyết định gửi thông báo tự động, **không** tắt daemon. Muốn tắt hẳn phải dùng `install.py --disable`.
