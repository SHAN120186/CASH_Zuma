# Запуск ZUMA Cash Flow как веб-сайта через Cloudflare Tunnel.
# Останавливает старые процессы, запускает туннель, подставляет новый адрес в config.env и стартует сайт.
$ErrorActionPreference = 'Stop'
$proj = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $proj

$cloudflared = @(
    "C:\Program Files (x86)\cloudflared\cloudflared.exe",
    "C:\Program Files\cloudflared\cloudflared.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $cloudflared) { Write-Host 'cloudflared не найден. Установите: winget install Cloudflare.cloudflared'; exit 1 }

# Остановить прежние экземпляры
try { Get-Process cloudflared -ErrorAction Stop | Stop-Process -Force } catch {}
try {
    $conn = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction Stop | Select-Object -First 1
    if ($conn) { Stop-Process -Id $conn.OwningProcess -Force }
} catch {}
Start-Sleep -Seconds 1

# Запустить туннель и дождаться публичного адреса
$log = Join-Path $env:TEMP 'cloudflared_tunnel.log'
Remove-Item $log -ErrorAction SilentlyContinue
Start-Process -FilePath $cloudflared -ArgumentList 'tunnel','--url','http://127.0.0.1:8000','--logfile',$log -WindowStyle Hidden
$url = $null
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    try {
        $m = Select-String -Path $log -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -ErrorAction Stop | Select-Object -First 1
        if ($m) { $url = $m.Matches[0].Value; break }
    } catch {}
}
if (-not $url) { Write-Host 'Не удалось получить адрес туннеля. Проверьте интернет и запустите снова.'; exit 1 }

# Подставить адрес в config.env
$cfg = @(Get-Content "$proj\config.env" -ErrorAction SilentlyContinue | Where-Object { $_ -and $_ -notmatch '^PUBLIC_ORIGIN=' })
$cfg += "PUBLIC_ORIGIN=$url"
Set-Content "$proj\config.env" $cfg -Encoding ASCII
Set-Content "$proj\PUBLIC_URL.txt" $url -Encoding ASCII

# Запустить сайт
Start-Process -FilePath "$proj\.venv\Scripts\python.exe" -ArgumentList 'start.py','--no-browser' -WorkingDirectory $proj -WindowStyle Hidden

Write-Host ''
Write-Host '======================================================='
Write-Host "САЙТ ДОСТУПЕН ПО АДРЕСУ: $url"
Write-Host 'Адрес также сохранён в файле PUBLIC_URL.txt'
Write-Host 'Адрес меняется при каждом перезапуске этого скрипта.'
Write-Host '======================================================='
