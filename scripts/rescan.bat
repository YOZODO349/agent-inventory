@echo off
chcp 936 >nul
cd /d "%~dp0.."
echo.
echo   Agent 资产总览 - 重新扫描本机
echo   ------------------------------------
where uv >nul 2>nul
if errorlevel 1 (
  python src\scan_agents.py
) else (
  uv run python src\scan_agents.py
)
if errorlevel 1 (
  echo.
  echo   [失败] 请检查上面的报错。
) else (
  echo.
  echo   [完成] 已刷新 src\agent_inventory.json。
)
echo.
pause
