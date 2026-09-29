# Быстрый запуск ZUMA Cash Flow как веб-сайта через Cloudflare Quick Tunnel.
# Для простого варианта на SQLite (после START_WINDOWS.bat, порт 8000).
# Останавливает старые процессы, запускает туннель, подставляет новый адрес в config.env и стартует сайт.
# Для рабочей базы PostgreSQL и обновления без смены адреса используйте OPEN_SITE.bat / START_WEB_SERVER.bat.
$ErrorActionPreference = 'Stop'
$proj = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $proj

$python = Join-Path $proj '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { Write-Host 'Окружение Python не найдено. Сначала запустите START_WINDOWS.bat.'; exit 1 }

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
$host_name = ([Uri]$url).Host

# Сайт принимает только перечисленные хосты и проверяет Origin, а cookies через HTTPS должны быть Secure.
# Поэтому вместе с адресом обновляются ALLOWED_HOSTS и COOKIE_SECURE. Старые адреса туннеля убираются.
$cfgPath = Join-Path $proj 'config.env'
$old = @(Get-Content $cfgPath -Encoding UTF8 -ErrorAction SilentlyContinue)
$hosts = @('127.0.0.1', 'localhost')
foreach ($line in $old) {
    if ($line -match '^ALLOWED_HOSTS=(.*)$') {
        foreach ($h in ($Matches[1] -split ',')) {
            $h = $h.Trim()
            if ($h -and $h -notlike '*.trycloudflare.com' -and $hosts -notcontains $h) { $hosts += $h }
        }
    }
}
$hosts += $host_name
$cfg = @($old | Where-Object { $_ -ne $null -and $_ -notmatch '^(PUBLIC_ORIGIN|ALLOWED_HOSTS|COOKIE_SECURE)=' })
$cfg += "PUBLIC_ORIGIN=$url"
$cfg += "ALLOWED_HOSTS=$($hosts -join ',')"
$cfg += 'COOKIE_SECURE=1'
Set-Content $cfgPath $cfg -Encoding UTF8
Set-Content (Join-Path $proj 'PUBLIC_URL.txt') $url -Encoding ASCII

# Те же значения передаются процессу напрямую: они имеют приоритет над config.env и config.local.env.
$env:PUBLIC_ORIGIN = $url
$env:ALLOWED_HOSTS = ($hosts -join ',')
$env:COOKIE_SECURE = '1'
$env:PYTHONUTF8 = '1'

# Запустить сайт и дождаться ответа /health
Start-Process -FilePath $python -ArgumentList 'start.py','--no-browser' -WorkingDirectory $proj -WindowStyle Hidden
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    try {
        $r = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2
        if ($r.status -eq 'ok') { $ready = $true; break }
    } catch {}
}

Write-Host ''
Write-Host '======================================================='
if ($ready) {
    Write-Host "САЙТ ДОСТУПЕН ПО АДРЕСУ: $url"
} else {
    Write-Host "Сайт не ответил на http://127.0.0.1:8000/health за 30 секунд. Ожидаемый адрес: $url"
    Write-Host 'Проверьте окно сервера или запустите START_WINDOWS.bat, чтобы увидеть ошибку.'
}
Write-Host 'Адрес также сохранён в файле PUBLIC_URL.txt'
Write-Host 'Адрес меняется при каждом перезапуске этого скрипта.'
Write-Host 'Не закрывайте компьютер: это временный туннель, а не постоянный хостинг.'
Write-Host '======================================================='
