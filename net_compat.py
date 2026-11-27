"""
net_compat.py — Tiện ích mạng dùng chung cho daemon / hook / installer.

Vì sao cần:
1. **Ép IPv4**: trên Windows, `api.telegram.org` có thể được phân giải sang IPv6
   trong khi đường IPv6 của mạng nội bộ hỏng ⇒ `WinError 10054`
   ("An existing connection was forcibly closed by the remote host").
   Ép IPv4 giải quyết dứt điểm lỗi này.
2. **Retry có backoff**: lỗi mạng tạm thời (RST, timeout) khiến tin nhắn Telegram
   bị mất im lặng. Thử lại 2–3 lần với backoff tăng dần.

Dùng chung cho cả 3 script ⇒ **phải copy cả file này** khi triển khai.
"""
import socket
import time

_IPV4_FORCED = False


def force_ipv4():
    """Ép mọi kết nối socket dùng IPv4. Trả về True nếu áp dụng được."""
    global _IPV4_FORCED
    if _IPV4_FORCED:
        return True
    try:
        _orig_getaddrinfo = socket.getaddrinfo

        def _patched(host, port, family=0, type=0, proto=0, flags=0):
            try:
                return _orig_getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
            except Exception:
                return _orig_getaddrinfo(host, port, family, type, proto, flags)

        socket.getaddrinfo = _patched
        _IPV4_FORCED = True
        return True
    except Exception:
        return False


def with_retry(fn, attempts=3, delay=1.5, backoff=1.8):
    """
    Gọi `fn()` tối đa `attempts` lần; lỗi mạng tạm thời thì chờ và thử lại.
    Trả về (ok, result_or_error_message).
    """
    last_err = ""
    wait = delay
    for i in range(attempts):
        try:
            return True, fn()
        except Exception as e:
            last_err = str(e)
            if i < attempts - 1:
                time.sleep(wait)
                wait *= backoff
    return False, last_err
