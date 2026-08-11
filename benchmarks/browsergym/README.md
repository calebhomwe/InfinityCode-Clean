# BrowserGym scaffold

This is an opt-in, Docker-isolated MiniWoB environment smoke test. It resets a
BrowserGym task and prints structured JSON; it **does not** run Infinity's agent
or claim a benchmark score yet.

Run it after installing Docker Desktop:

```powershell
docker compose -f benchmarks/browsergym/compose.yaml run --rm browsergym --task browsergym/miniwob.click-test
```

The container uses `network_mode: none` because MiniWoB runs locally. Keep
WebArena, WorkArena, SWE-bench and OSWorld separate until their services and
credential boundaries are explicitly configured.
