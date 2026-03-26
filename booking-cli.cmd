@echo off
setlocal

set "BOOKING_REPO=%~dp0"
set "BOOKING_PYTHONPATH=%BOOKING_REPO%src;%BOOKING_REPO%.vendor"

if defined PYTHONPATH (
    set "PYTHONPATH=%BOOKING_PYTHONPATH%;%PYTHONPATH%"
) else (
    set "PYTHONPATH=%BOOKING_PYTHONPATH%"
)

py -3 -m booking_cli %*
if not errorlevel 9009 exit /b %errorlevel%

python -m booking_cli %*
if not errorlevel 9009 exit /b %errorlevel%

>&2 echo booking-cli: Python was not found on PATH. This launcher expects `py -3 -m booking_cli` to work. Install Python or add `py` to PATH.
exit /b 1
