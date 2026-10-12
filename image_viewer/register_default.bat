@echo off
:: ============================================
::  LadyViewer - Windows 기본 이미지 뷰어 등록
::  관리자 권한으로 실행하세요
:: ============================================

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo 관리자 권한이 필요합니다.
    echo 이 파일을 우클릭 후 "관리자 권한으로 실행"을 선택하세요.
    pause
    exit /b 1
)

set "VIEWER_PATH=%~dp0dist\LadyViewer.exe"

if not exist "%VIEWER_PATH%" (
    echo LadyViewer.exe를 찾을 수 없습니다.
    echo 먼저 build.bat을 실행하여 빌드하세요.
    pause
    exit /b 1
)

echo LadyViewer를 이미지 파일 기본 프로그램으로 등록합니다...

:: Register application
reg add "HKLM\SOFTWARE\Classes\Applications\LadyViewer.exe\shell\open\command" /ve /d "\"%VIEWER_PATH%\" \"%%1\"" /f >nul

:: Register supported extensions
for %%E in (.jpg .jpeg .png .bmp .gif .webp .tiff .tif .ico .svg) do (
    reg add "HKLM\SOFTWARE\Classes\LadyViewer%%E" /ve /d "LadyViewer Image" /f >nul
    reg add "HKLM\SOFTWARE\Classes\LadyViewer%%E\shell\open\command" /ve /d "\"%VIEWER_PATH%\" \"%%1\"" /f >nul

    :: Set file association
    assoc %%E=LadyViewer%%E >nul 2>&1
    ftype LadyViewer%%E="%VIEWER_PATH%" "%%1" >nul 2>&1
)

:: Register in Default Programs
reg add "HKLM\SOFTWARE\RegisteredApplications" /v "LadyViewer" /d "SOFTWARE\Clients\StartMenuInternet\LadyViewer\Capabilities" /f >nul
reg add "HKLM\SOFTWARE\Clients\StartMenuInternet\LadyViewer\Capabilities" /v "ApplicationName" /d "LadyViewer" /f >nul
reg add "HKLM\SOFTWARE\Clients\StartMenuInternet\LadyViewer\Capabilities" /v "ApplicationDescription" /d "가볍고 빠른 이미지 뷰어" /f >nul

for %%E in (.jpg .jpeg .png .bmp .gif .webp .tiff .tif .ico .svg) do (
    reg add "HKLM\SOFTWARE\Clients\StartMenuInternet\LadyViewer\Capabilities\FileAssociations" /v "%%E" /d "LadyViewer%%E" /f >nul
)

echo.
echo 등록 완료!
echo Windows 설정 ^> 앱 ^> 기본 앱에서 LadyViewer를 확인할 수 있습니다.
echo 또는 이미지 파일을 우클릭 ^> 연결 프로그램에서 LadyViewer를 선택하세요.
pause
