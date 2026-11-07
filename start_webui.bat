@echo off
setlocal enableextensions enabledelayedexpansion

REM ============================================================
REM  ComfyUI-Mie 短剧工作台 —— 独立启动（不经过 ComfyUI）
REM
REM  网页端随 H3 engine 插件发布，在 webapp\ 目录里。它跑在 ComfyUI
REM  自己的 embedded python 上 —— 和插件节点同一个解释器、同一套依赖、
REM  同一份资产库与 mie_llm_keys.json。
REM
REM  所以：
REM    - 不建 venv，也不装依赖（依赖是 ComfyUI 装插件 requirements.txt
REM      时装进 embedded python 的）
REM    - ComfyUI 不开也能用：资产、剧本、LLM 设置全是本地操作。
REM      只有「渲染」要 ComfyUI。
REM
REM  要在 ComfyUI 里起，用 MieH3StartWebUI 节点。
REM ============================================================

cd /d "%~dp0"

set "WEBUI_PORT=8199"
set "DISPLAY_HOST=127.0.0.1"

REM --- 0. 清理占用端口的旧进程 ---
echo [1/4] 检查端口 %WEBUI_PORT% ...
set "KILLED=0"
for /f "tokens=5" %%P in ('netstat -ano -p tcp ^| findstr ":%WEBUI_PORT% " ^| findstr "LISTENING"') do (
    echo       结束占用进程 PID=%%P ...
    taskkill /F /PID %%P >nul 2>&1
    set "KILLED=1"
)
if "!KILLED!"=="0" (echo       端口空闲。) else (ping -n 2 127.0.0.1 >nul)

REM --- 1. 找 ComfyUI 的 embedded python ---
echo [2/4] 找 ComfyUI 的 embedded python ...
set "PYCMD="
REM 插件装在 <ComfyUI>\custom_nodes\ 下，所以上两级就是 ComfyUI 根。
if exist "%~dp0..\..\python_embeded\python.exe" set "PYCMD=%~dp0..\..\python_embeded\python.exe"
if not defined PYCMD (
    for %%P in (C D E F) do (
        if exist "%%P:\PP\V9\V9-Large\ComfyUI_Mie_2026_V9.0_Large\python_embeded\python.exe" (
            set "PYCMD=%%P:\PP\V9\V9-Large\ComfyUI_Mie_2026_V9.0_Large\python_embeded\python.exe"
        )
    )
)
if not defined PYCMD (
    echo [错误] 找不到 ComfyUI 的 embedded python。
    echo        把这个插件装进 ComfyUI 的 custom_nodes 目录，或手动指定 python.exe。
    pause
    exit /b 1
)
echo       用 %PYCMD%

REM --- 2. 依赖预检 ---
echo [3/4] 检查依赖 ...
"%PYCMD%" -c "import flask, mie_llm, mie_craft, soundfile" >nul 2>&1
if errorlevel 1 (
    echo       缺依赖。装一次：ComfyUI 启动时会读本插件的 requirements.txt，
    echo       或者手动： "%PYCMD%" -m pip install -r "%~dp0requirements.txt"
    pause
    exit /b 1
)
echo       依赖就绪。

REM --- 3. 预检：资产库在不在、ComfyUI 在不在 ---
REM r'%~dp0.' 末尾补一个点：%~dp0 自带反斜杠，直接插进引号会转义掉引号。
"%PYCMD%" -c "import sys; sys.path.insert(0, r'%~dp0.'); from webapp import config; from webapp.services import library; print('       资产库  :', library.library_dir()); print('       密钥文件:', config.LLM_KEYS_FILE)" 2>nul
"%PYCMD%" -c "import sys; sys.path.insert(0, r'%~dp0.'); from webapp.services import comfy; s=comfy.status(); print('       ComfyUI :', ('在线 '+(s.get('comfyui_version') or '')) if s.get('online') else '不在线（渲染要用它，其余功能不受影响）')" 2>nul

REM --- 4. 启动 ---
echo.
echo [4/4] 启动短剧工作台...
echo       地址: http://%DISPLAY_HOST%:%WEBUI_PORT%
echo       局域网: http://本机IP:%WEBUI_PORT%  （换成你这台机器的局域网地址）
echo       按 Ctrl+C 停止。
echo ============================================================
echo.

REM 没有关联浏览器时 start 会报一句"系统找不到指定的文件"，那是无关噪音。
start "" cmd /c "timeout /t 3 /nobreak >nul && start http://%DISPLAY_HOST%:%WEBUI_PORT% 2>nul"

REM 跑文件，不是 -m webapp：embedded python 是隔离模式（._pth 固定了
REM sys.path），-m 找不到包。webapp\__main__.py 自己会把包的父目录放回去。
set "MIE_WEB_PORT=%WEBUI_PORT%"
set "MIE_WEB_HOST=0.0.0.0"
"%PYCMD%" "%~dp0webapp\__main__.py"
if errorlevel 1 (
    echo.
    echo [错误] WebUI 退出码 %errorlevel%。
    pause
)
endlocal
