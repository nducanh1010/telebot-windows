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
6. **Tự động nhả `Working` trên IDE ngay khi chạm chuột/bàn phím PC (`get_user_idle_seconds`)**:
   - Trên IDE (ngoài `tmux`), để nhận được tin nhắn trả lời từ Telegram, Hook `Stop` phải giữ vòng lặp chờ (`wait_for_ide_telegram_reply`). Trong thời gian đó, giao diện IDE sẽ hiển thị trạng thái `Working`.
   - Để người dùng không bao giờ bị vướng khi quay lại dùng IDE trên PC: mỗi `0.5s`, vòng lặp kiểm tra `HIDIdleTime` (`ioreg -c IOHIDSystem` trên macOS / `GetLastInputInfo` trên Windows). Ngay khoảnh khắc người dùng lắc chuột hoặc chạm phím trên PC (`idle < 0.8s`), Hook tự động thoát ngay lập tức (`Working` biến mất trong chưa đầy nửa giây).
