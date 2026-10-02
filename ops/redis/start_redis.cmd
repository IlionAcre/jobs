@echo off
rem Makes sure the Podman machine and the Redis container used by the scraper pipeline are running.
rem Registered as the task "UpworkRedis": at logon AND every 5 minutes (see ops/redis/README.md).
rem
rem Why every 5 minutes: the WSL virtual machine can stop without a reboot. On 2026-10-02 a Microsoft Store
rem update of WSL shut it down and nothing restarted it for 3 h 40 min. When Redis already answers, this
rem script exits at once and writes nothing.
rem
rem The machine's own podman-restart.service can't be enabled over ssh (Access denied), so the
rem container is started from here instead of relying on --restart=always.
setlocal
set "PODMAN=%LOCALAPPDATA%\Programs\Podman\podman.exe"
set "LOG=%~dp0start_redis.log"

"%PODMAN%" exec upwork-redis redis-cli PING > nul 2>&1
if not errorlevel 1 exit /b 0

echo [%date% %time%] redis not answering, starting >> "%LOG%"
"%PODMAN%" machine start >> "%LOG%" 2>&1
"%PODMAN%" start upwork-redis >> "%LOG%" 2>&1
"%PODMAN%" exec upwork-redis redis-cli PING >> "%LOG%" 2>&1
echo [%date% %time%] done (exit %errorlevel%) >> "%LOG%"
endlocal
