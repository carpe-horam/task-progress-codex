@echo off
setlocal
rem Python helper for hooks and explicit commands; MCP uses python directly.

if defined CODEX_MCP_PYTHON_PATH if exist "%CODEX_MCP_PYTHON_PATH%" (
  "%CODEX_MCP_PYTHON_PATH%" %*
  exit /b
)
if defined CODEX_PYTHON_PATH if exist "%CODEX_PYTHON_PATH%" (
  "%CODEX_PYTHON_PATH%" %*
  exit /b
)

where python >nul 2>&1
if not errorlevel 1 (
  python %*
  exit /b
)
where python3 >nul 2>&1
if not errorlevel 1 (
  python3 %*
  exit /b
)

echo task-progress: Python runtime not found. Please install Python 3. 1>&2
exit /b 127
