@echo off
echo ============================================
echo   LadyViewer Build Script
echo ============================================

pip install -r requirements.txt

echo Building executable...
pyinstaller --onefile --windowed --name LadyViewer ^
    --add-data "canvas.py;." ^
    --add-data "thumbnails.py;." ^
    main.py

echo.
echo Build complete!
echo Output: dist\LadyViewer.exe
pause
