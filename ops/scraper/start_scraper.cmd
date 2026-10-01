@echo off
rem Starts the scraper pipeline (scheduler + fetcher + dispatcher) and keeps it running.
rem Registered as the logon task "UpworkScraper" (see ops/scraper/README.md).
rem Waits for Redis first (the UpworkRedis task starts it), then runs `main.py scraper up`,
rem restarting it if the supervisor itself ever exits.
setlocal
set "ROOT=%~dp0..\.."
set "PY=%ROOT%\.venv\Scripts\python.exe"
set "PODMAN=%LOCALAPPDATA%\Programs\Podman\podman.exe"
set "LOGDIR=%ROOT%\logs"
set "LOG=%LOGDIR%\scraper.log"
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
cd /d "%ROOT%"

rem keep one previous log once it passes ~20 MB
for %%F in ("%LOG%") do if exist "%LOG%" if %%~zF GTR 20000000 move /y "%LOG%" "%LOG%.1" > nul

:wait_redis
"%PODMAN%" exec upwork-redis redis-cli PING > nul 2>&1
if errorlevel 1 (
    echo {"msg":"waiting_for_redis","ts":"%date% %time%"} >> "%LOG%"
    timeout /t 10 /nobreak > nul
    goto wait_redis
)

:run
"%PY%" -u main.py scraper up >> "%LOG%" 2>&1
echo {"msg":"supervisor_exited_restarting","code":"%errorlevel%","ts":"%date% %time%"} >> "%LOG%"
timeout /t 15 /nobreak > nul
goto wait_redis
