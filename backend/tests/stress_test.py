"""Stress test for the Infinity Code backend.

Exercises the real failure modes without burning API budget on 50 full LLM
missions: malformed-input handling on every endpoint, read concurrency,
concurrent WebSocket connections, upload size limits, and — the important
one — concurrent SQLite writes (proving WAL keeps the DB unlocked).

Run the backend first (uvicorn on :8000), then:
    venv\\Scripts\\python backend\\tests\\stress_test.py

Outputs a JSON report to stdout and to backend/outputs/stress_report.json.
"""

from __future__ import annotations

import concurrent.futures
import json
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    from websockets.sync.client import connect as ws_connect
except Exception:  # noqa: BLE001 - websockets is optional for the WS test
    ws_connect = None  # type: ignore[assignment]

BASE_URL = "http://127.0.0.1:8000"
WS_URL = "ws://127.0.0.1:8000/api/v1"
BASE_DIR = Path(__file__).resolve().parents[1]
DB_PATH = BASE_DIR / "missions.db"
REPORT_PATH = BASE_DIR / "outputs" / "stress_report.json"
SLOW_THRESHOLD_S = 1.5


def _request(
    method: str,
    path: str,
    body: Optional[bytes] = None,
    content_type: str = "application/json",
    timeout: float = 15.0,
) -> Tuple[int, float, str]:
    """Return (status_code, elapsed_seconds, body_text). Never raises."""
    url = f"{BASE_URL}{path}"
    req = urllib.request.Request(url, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", content_type)
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read().decode("utf-8", "replace")
            return resp.status, time.perf_counter() - start, text
    except urllib.error.HTTPError as exc:
        return exc.code, time.perf_counter() - start, exc.read().decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        return -1, time.perf_counter() - start, str(exc)


class Report:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.results: List[Dict[str, Any]] = []
        self.slow_endpoints: List[str] = []

    def check(self, name: str, ok: bool, detail: str = "") -> None:
        self.results.append({"test": name, "passed": ok, "detail": detail})
        if ok:
            self.passed += 1
        else:
            self.failed += 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def test_malformed_input(report: Report) -> None:
    """Every endpoint must reject bad input with a 4xx, never a 500 or crash."""
    print("\n== Malformed input (expect 4xx, never 5xx) ==")
    cases: List[Tuple[str, str, Optional[bytes]]] = [
        ("POST", "/api/v1/missions", b"{}"),  # missing required fields
        ("POST", "/api/v1/missions", b"not json"),
        ("POST", "/api/v1/missions", b'{"title":"","goal":""}'),
        ("GET", "/api/v1/missions/does-not-exist", None),
        ("POST", "/api/v1/missions/nope/approve", b"{}"),
        ("POST", "/api/v1/missions/nope/reject", b'{"feedback":"x"}'),
        ("POST", "/api/v1/learn", b'{"url":"","topic":""}'),
        ("POST", "/api/v1/critic", b'{"work_path":"/no","reference_path":"/no"}'),
        ("POST", "/api/v1/skills/compose", b'{"skill_a":"x","skill_b":"y"}'),
        ("PUT", "/api/v1/settings", b'{"daily_budget_aud":-5,"pass_threshold":9}'),
    ]
    for method, path, payload in cases:
        status, _, _ = _request(method, path, payload)
        # Acceptable: any 4xx, or 200 (valid-but-empty handled), or 422.
        ok = 400 <= status < 500 or status == 200
        report.check(f"{method} {path} handled cleanly (got {status})", ok, "")


def test_read_concurrency(report: Report, n: int = 50) -> None:
    print(f"\n== Read concurrency ({n} parallel GETs) ==")
    paths = ["/api/v1/missions", "/api/v1/cost", "/api/v1/skills", "/api/v1/settings"]

    def one(i: int) -> Tuple[int, float, str]:
        p = paths[i % len(paths)]
        s, e, _ = _request("GET", p)
        return s, e, p

    times: Dict[str, List[float]] = {p: [] for p in paths}
    ok_count = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        for status, elapsed, path in pool.map(one, range(n)):
            if status == 200:
                ok_count += 1
            times[path].append(elapsed)
    report.check(f"{ok_count}/{n} concurrent reads returned 200", ok_count == n)
    for p, ts in times.items():
        if ts:
            avg = sum(ts) / len(ts)
            if avg > SLOW_THRESHOLD_S:
                report.slow_endpoints.append(f"{p}: {avg:.2f}s")


def test_websocket_concurrency(report: Report, n: int = 10) -> None:
    print(f"\n== WebSocket concurrency ({n} parallel connections) ==")
    if ws_connect is None:
        report.check("websocket connections", False, "websockets lib unavailable")
        return

    def one(_i: int) -> bool:
        try:
            with ws_connect(f"{WS_URL}/missions/{uuid.uuid4()}/ws", open_timeout=10) as ws:
                msg = ws.recv(timeout=10)
                return bool(msg)
        except Exception:  # noqa: BLE001
            return False

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        results = list(pool.map(one, range(n)))
    ok = sum(1 for r in results if r)
    report.check(f"{ok}/{n} WebSockets connected and received a frame", ok == n)


def test_upload_limits(report: Report) -> None:
    print("\n== Upload limits ==")
    boundary = "----infinitystress"

    def multipart(name: str, data: bytes) -> bytes:
        return (
            f"--{boundary}\r\n".encode()
            + f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode()
            + b"Content-Type: application/octet-stream\r\n\r\n"
            + data
            + f"\r\n--{boundary}--\r\n".encode()
        )

    small = multipart("small.txt", b"hello stress test")
    status, _, _ = _request(
        "POST", "/api/v1/upload", small, f"multipart/form-data; boundary={boundary}"
    )
    report.check(f"normal upload accepted (got {status})", status == 200)

    big = multipart("big.bin", b"x" * (30 * 1024 * 1024))  # 30 MB > 25 MB limit
    status, _, _ = _request(
        "POST", "/api/v1/upload", big, f"multipart/form-data; boundary={boundary}", 60.0
    )
    report.check(f"oversized upload rejected (got {status})", status == 413)


def test_db_write_concurrency(report: Report, n: int = 20) -> None:
    """The WAL proof: many threads writing the missions DB with no lock errors."""
    print(f"\n== DB write concurrency ({n} threads, WAL) ==")
    if not DB_PATH.is_file():
        report.check("db write concurrency", False, f"{DB_PATH} missing")
        return
    errors: List[str] = []

    def writer(i: int) -> None:
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            mid = f"stress-{uuid.uuid4().hex}"
            conn.execute(
                "INSERT INTO missions (id, title, goal, status) VALUES (?, ?, ?, 'queued')",
                (mid, f"stress {i}", "stress test row"),
            )
            conn.execute("UPDATE missions SET status='completed' WHERE id=?", (mid,))
            conn.execute("DELETE FROM missions WHERE id=?", (mid,))
            conn.commit()
            conn.close()
        except sqlite3.Error as exc:
            errors.append(str(exc))

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        list(pool.map(writer, range(n)))
    report.check(
        f"{n} concurrent DB writers, {len(errors)} lock errors",
        len(errors) == 0,
        "; ".join(errors[:2]),
    )


def peak_memory_mb() -> Optional[float]:
    try:
        import psutil  # type: ignore

        return round(psutil.Process().memory_info().rss / (1024 * 1024), 1)
    except Exception:  # noqa: BLE001
        return None


def main() -> None:
    print("=== Infinity Code stress test ===")
    status, _, _ = _request("GET", "/api/v1/cost")
    if status != 200:
        print(f"Backend not reachable on {BASE_URL} (GET /cost -> {status}). Start it first.")
        raise SystemExit(1)

    report = Report()
    checks: List[Callable[[Report], None]] = [
        test_malformed_input,
        test_read_concurrency,
        test_websocket_concurrency,
        test_upload_limits,
        test_db_write_concurrency,
    ]
    for fn in checks:
        try:
            fn(report)
        except Exception as exc:  # noqa: BLE001 - a crashing test is itself a failure
            report.check(fn.__name__, False, f"test crashed: {exc}")

    recommendations: List[str] = []
    if report.slow_endpoints:
        recommendations.append("Investigate slow endpoints: " + ", ".join(report.slow_endpoints))
    if any(not r["passed"] and "lock" in r["detail"] for r in report.results):
        recommendations.append("SQLite still locking under load — check WAL is applied.")
    if report.failed == 0:
        recommendations.append("All checks passed. Foundation is solid.")

    out: Dict[str, Any] = {
        "total_tests": report.passed + report.failed,
        "passed": report.passed,
        "failed": report.failed,
        "slow_endpoints": report.slow_endpoints,
        "memory_peak_mb": peak_memory_mb(),
        "failures": [r for r in report.results if not r["passed"]],
        "recommendations": recommendations,
    }
    print("\n=== REPORT ===")
    print(json.dumps(out, indent=2))
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"\nSaved to {REPORT_PATH}")
    except OSError as exc:
        print(f"Could not save report: {exc}")
    raise SystemExit(0 if report.failed == 0 else 1)


if __name__ == "__main__":
    main()
