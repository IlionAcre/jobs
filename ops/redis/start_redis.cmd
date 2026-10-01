@echo off
rem Starts the Podman machine and the Redis container used by the scraper pipeline.
rem Registered as the logon task "UpworkRedis" (see ops/redis/README.md). Safe to run repeatedly.
rem The machine's own podman-restart.service can't be enabled over ssh (Access denied), so the
rem container is started from here instead of relying on --restart=always.
setlocal
set "PODMAN=%LOCALAPPDATA%\Programs\Podman\podman.exe"
set "LOG=%~dp0start_redis.log"

echo [%date% %time%] starting >> "%LOG%"
"%PODMAN%" machine start >> "%LOG%" 2>&1
"%PODMAN%" start upwork-redis >> "%LOG%" 2>&1
"%PODMAN%" exec upwork-redis redis-cli PING >> "%LOG%" 2>&1
echo [%date% %time%] done (exit %errorlevel%) >> "%LOG%"
endlocal
