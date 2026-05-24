@echo off
chcp 65001 >nul
echo ========================================
echo   B 站收藏夹备份系统 - 快速安装脚本
echo ========================================
echo.

REM 检查 Python 是否安装
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.8+
    echo 下载地址：https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [1/4] 检查 Python 版本...
python --version

echo.
echo [2/4] 安装 Python 依赖...
pip install -r requirements.txt
if errorlevel 1 (
    echo [错误] 安装依赖失败
    pause
    exit /b 1
)

echo.
echo [3/4] 检查 yt-dlp...
python -m yt_dlp --version >nul 2>&1
if errorlevel 1 (
    echo [警告] 未检测到 yt-dlp，请重新执行 pip install -r requirements.txt
)

echo.
echo [4/4] 创建必要目录...
if not exist "logs" mkdir logs
if not exist "temp" mkdir temp

echo.
echo ========================================
echo   安装完成！
echo ========================================
echo.
echo 下一步:
echo 1. 编辑 config.yaml 文件，配置 B 站 Cookie 和 S3 信息
echo 2. 运行 start.bat 启动系统
echo 3. 访问 http://localhost:8000 查看界面
echo.
pause
