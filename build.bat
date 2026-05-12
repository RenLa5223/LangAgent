@echo off
echo ============================================================
echo  LangAgent 打包脚本
echo ============================================================

REM 检查 PyInstaller
python -m PyInstaller --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [+] 安装 PyInstaller...
    pip install pyinstaller Pillow pywebview pystray
)

REM 清理旧构建
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [+] 打包中...

python -m PyInstaller --onefile --noconsole ^
    --name=LangAgent ^
    --icon=app_icon.ico ^
    --add-data "index.html;." ^
    --add-data "wechat_agent.py;." ^
    --add-data "app_icon.ico;." ^
    --hidden-import=wechat_agent ^
    --hidden-import=PIL ^
    --hidden-import=PIL.Image ^
    --collect-all pywebview ^
    --collect-all clr_loader ^
    --collect-all pystray ^
    --collect-all PIL ^
    server.py

if %errorlevel% neq 0 (
    echo [!] PyInstaller 打包失败
    pause
    exit /b 1
)

echo ============================================================
echo  打包完成: dist\LangAgent.exe
echo  接下来用 Inno Setup 编译 setup.iss 生成安装程序
echo ============================================================
pause
