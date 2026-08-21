@echo off
chcp 65001 >nul
REM 用 pythonw 无控制台运行红绿灯（双击即启动，不弹出黑框）
setlocal
set "PY=%~dp0"
if exist "%PY%traffic_light.py" (
    start "" pythonw "%PY%traffic_light.py"
) else (
    echo 未找到 traffic_light.py
    pause
)
endlocal
