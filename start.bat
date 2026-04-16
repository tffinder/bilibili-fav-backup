@echo off
chcp 65001 >nul
echo ========================================
echo   B 站收藏夹备份系统
echo ========================================
echo.

REM 检查配置文件
if not exist "config.yaml" (
    echo [错误] config.yaml 文件不存在
    echo 请先创建并编辑配置文件
    pause
    exit /b 1
)

echo 正在启动服务...
echo 访问地址：http://localhost:8000
echo 按 Ctrl+C 停止服务
echo.

python main.py

pause
