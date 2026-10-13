@echo off
rem doctool for cmd.exe: tools\doc.cmd <command>. Looks for py, python, python3 (3.10+); the Microsoft Store stub is skipped.
setlocal
set "DOC_PY="
for %%P in (py python python3) do (
  if not defined DOC_PY (
    %%P -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>nul && set "DOC_PY=%%P"
  )
)
if not defined DOC_PY (
  echo doc: Python 3.10+ is required ^(py, python or python3 not found in PATH^) 1>&2
  exit /b 1
)
%DOC_PY% "%~dp0doc" %*
exit /b %errorlevel%
