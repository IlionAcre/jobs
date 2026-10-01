# Redis for the scraper pipeline (Podman)

Redis holds the work queue (streams), the shared search token and the rate-limit counters.
Everything durable (searches, jobs, deliveries) lives in Postgres; Redis can be rebuilt from scratch.

## What is installed on this Windows PC (2026-10-01)
- Podman 6.1.3 (official signed MSI from the Podman GitHub release; `winget` download was broken).
  Binary: `%LOCALAPPDATA%\Programs\Podman\podman.exe`.
- Podman machine `podman-machine-default` on WSL2 (rootless).
- Container `upwork-redis`: `docker.io/library/redis:7-alpine` (Redis 7.4.11), published on
  `127.0.0.1:6379` only, data on the named volume `upwork-redis-data`,
  `redis-server --appendonly yes --appendfsync everysec` (survives restarts).
- Scheduled task **UpworkRedis** (at logon) runs `ops/redis/start_redis.cmd`, which starts the machine and
  the container. Log: `ops/redis/start_redis.log`.

## Recreate from scratch
```
podman machine init
podman machine start
podman volume create upwork-redis-data
podman run -d --name upwork-redis --restart=always -p 127.0.0.1:6379:6379 -v upwork-redis-data:/data docker.io/library/redis:7-alpine redis-server --appendonly yes --appendfsync everysec
schtasks /Create /TN "UpworkRedis" /TR "<repo>\ops\redis\start_redis.cmd" /SC ONLOGON /RL LIMITED /F
```

## Things that are not obvious
- `--restart=always` alone does **not** bring the container back after the machine restarts: the machine's
  `podman-restart.service` is disabled and can't be enabled over `podman machine ssh` ("Access denied").
  The logon task starts the container explicitly instead. Verified: from a stopped machine, Redis is back
  ~20 s after the task runs, data intact.
- The task runs at **logon**, not at boot: WSL is per-user, so Redis is down until this user logs in.
- Use `REDIS_URL=redis://127.0.0.1:6379/0`. With `localhost` the first connection takes ~2 s (IPv6 is tried
  first; Redis is only published on IPv4).
- Each Redis call from Windows costs ~1.5 ms through the WSL port forward (inside the VM: ~0.8 ms p50,
  13k XADD/s). Fine for this workload.
- Memory: Redis itself uses ~7 MB. The WSL2 VM is capped with `%USERPROFILE%\.wslconfig`
  (`[wsl2] memory=1GB`, applies to all of WSL on this PC): it holds **~1.03 GB** of Windows RAM, down from
  ~1.2 GB uncapped. Inside: 896 MB total, ~430–450 MB used, no swap use, no OOM kills under a 50k-op load.
  To remove the cap: delete the `memory=` line, `wsl --shutdown`, run `ops\redis\start_redis.cmd`.
  Details in `research/upwork_recon/06_resources.md`.

## On Linux (after the server move)
No VM: `podman run` the same container (or `apt install redis`) and enable it with systemd/Quadlet.
Nothing in the application changes; only `REDIS_URL`.

## Handy commands
```
podman ps                      # is it up
podman exec upwork-redis redis-cli INFO memory
podman machine stop            # frees the ~1.2 GB; ops\redis\start_redis.cmd brings it back
```
