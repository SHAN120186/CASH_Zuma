@echo off
rem ZUMA в одной доверенной Wi-Fi/LAN сети. Не открывает порт в интернет.
cd /d "%~dp0"
call START_LOCAL.bat --lan
