# watch_console.ps1 — Theo dõi tiến trình console nào BẬT LÊN trên máy và TIỂN TRÌNH MẸ là ai.
#
# Dùng để tìm thủ phạm "cửa sổ PowerShell/console bật lên rồi tắt":
#   powershell -NoProfile -ExecutionPolicy Bypass -File watch_console.ps1 -Seconds 90
#
# Nếu thấy dòng có `parent=pythonw.exe` -> do daemon của ai-telegram.
# Nếu thấy `parent=Antigravity.exe` (hoặc Code/Cursor) -> do hook chạy từ IDE.
param([int]$Seconds = 90)

$watch = @('powershell.exe', 'pwsh.exe', 'cmd.exe', 'tasklist.exe', 'conhost.exe', 'wt.exe')
$seen = @{}

Write-Output "=== Theo doi $Seconds giay | Cac tien trinh console moi se in ra ==="
Write-Output ""

$end = (Get-Date).AddSeconds($Seconds)
while ((Get-Date) -lt $end) {
    $procs = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
             Where-Object { $watch -contains $_.Name }
    foreach ($p in $procs) {
        $key = "$($p.ProcessId)"
        if (-not $seen.ContainsKey($key)) {
            $seen[$key] = $true
            $parentName = '?'
            $parentCmd = ''
            try {
                $pp = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ParentProcessId)" -ErrorAction SilentlyContinue
                if ($pp) {
                    $parentName = $pp.Name
                    $parentCmd = $pp.CommandLine
                }
            } catch {}
            $cmd = $p.CommandLine
            if ($cmd -and $cmd.Length -gt 110) { $cmd = $cmd.Substring(0, 110) }
            Write-Output ("{0} | {1} | parent={2} | cmd={3}" -f (Get-Date -Format 'HH:mm:ss'), $p.Name, $parentName, $cmd)
            Write-Output ("      parent cmd: {0}" -f $parentCmd)
        }
    }
    Start-Sleep -Milliseconds 700
}

Write-Output ""
Write-Output "=== Het. Nhanh phan biet ==="
Write-Output "parent=pythonw.exe  -> daemon ai-telegram (kiểm tra state/daemon.log)"
Write-Output "parent=Antigravity/Code/Cursor -> hook_notify.py chay tu IDE"
Write-Output "parent=wscript.exe   -> script giam sat Startup (can chay lai install.py)"
Write-Output "parent=explorer.exe -> app khac, dung Process Monitor (ProcMon) de tra"
