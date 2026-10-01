@echo off
rem Starts the scraper pipeline and keeps it running.
rem Registered as the task "UpworkScraper" (see ops/scraper/README.md); the task re-runs this every
rem 5 minutes and Task Scheduler ignores the new run while this one is still alive.
rem
rem Waits for Redis (the UpworkRedis task starts it), then runs `main.py scraper up`, and starts it again
rem if the supervisor ever exits.
rem
rem The pipeline writes logs\scraper.log itself (--log-file). Do NOT redirect it here with ">>": a file
rem opened by cmd can't be opened by anyone else, so one lingering process would block every relaunch.
rem This script's own few lines go to logs\start_scraper.log.
setlocal
set "ROOT=%~dp0..\.."
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "PODMAN=%LOCALAPPDATA%\Programs\Podman\podman.exe"
set "LOGDIR=%ROOT%\logs"
set "LOG=%LOGDIR%\scraper.log"
set "OWNLOG=%LOGDIR%\start_scraper.log"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
cd /d "%ROOT%"

rem keep one previous log once it passes ~20 MB (skipped silently if something still has it open)
for %%F in ("%LOG%") do if exist "%LOG%" if %%~zF GTR 20000000 move /y "%LOG%" "%LOG%.1" > nul 2>&1

:wait_redis
"%PODMAN%" exec upwork-redis redis-cli PING > nul 2>&1
if errorlevel 1 (
    echo [%date% %time%] waiting for redis >> "%OWNLOG%" 2>nul
    timeout /t 10 /nobreak > nul
    goto wait_redis
)

echo [%date% %time%] starting pipeline >> "%OWNLOG%" 2>nul
"%PY%" -u main.py scraper up --log-file "%LOG%"
echo [%date% %time%] supervisor exited with code %errorlevel%, restarting in 15 s >> "%OWNLOG%" 2>nul
timeout /t 15 /nobreak > nul
goto wait_redis
