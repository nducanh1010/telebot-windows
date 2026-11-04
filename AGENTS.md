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
