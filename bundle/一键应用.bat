@echo off
chcp 65001 >nul
title 皮糙肉厚的红警 - 一键应用
set "GAMEDIR=%~1"
if not defined GAMEDIR set /p GAMEDIR=请把游戏目录拖到这里后回车: 
set "GAMEDIR=%GAMEDIR:"=%"
where python >nul 2>nul
if errorlevel 1 (
  echo.
  echo 没找到 python，请先安装 Python 3 并勾选 Add to PATH。
  echo.
  pause
  exit /b 1
)
echo.
python "%~dp0apply_tanky_mod.py" "%GAMEDIR%"
echo.
pause
