@echo off
:: LadyViewer 기본 프로그램 등록 해제

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo 관리자 권한이 필요합니다.
    pause
    exit /b 1
)

echo LadyViewer 등록을 해제합니다...

for %%E in (.jpg .jpeg .png .bmp .gif .webp .tiff .tif .ico .svg) do (
    reg delete "HKLM\SOFTWARE\Classes\LadyViewer%%E" /f >nul 2>&1
)
reg delete "HKLM\SOFTWARE\Classes\Applications\LadyViewer.exe" /f >nul 2>&1
reg delete "HKLM\SOFTWARE\Clients\StartMenuInternet\LadyViewer" /f >nul 2>&1
reg delete "HKLM\SOFTWARE\RegisteredApplications" /v "LadyViewer" /f >nul 2>&1

echo 등록 해제 완료!
pause
