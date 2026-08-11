# Phase 1 Security Audit Findings Report
## Date: 2026-08-09 | Target: Infinity Code v0.1.59 (commit 74b1ed9)

---

## CRITICAL Findings

### S1.1 Secrets Committed to Git History [CRITICAL]
**Status:** CONFIRMED
**Evidence:**
- `backend/dashscope.key`, `backend/moonshot.key`, `backend/dashscope.base` exist as plaintext files on disk in the repo directory
- Commit `133a2a8` ("feat(p2+p3): referee fidelity scoring") added these files to git history
- `.gitignore` now has `*.key` but this only prevents FUTURE commits; history still contains them
- `providers.json` stores API keys as plaintext JSON in the data dir (confirmed by SELF_REVIEW.md line 5: "The UI never claims plaintext JSON storage is encrypted")
- Key loading code in `main.py` lines 889-976 reads from `DATA_DIR / "moonshot.key"` and `DATA_DIR / "dashscope.key"` as plaintext files
- `openrouter_client.py` line 352 also reads `moonshot.key` from a relative path

**Impact:** Anyone with read access to the git repo (or any fork/clone made before scrubbing) has live API keys for DashScope and Moonshot accounts.

**Remediation:**
1. ROTATE all exposed keys immediately (DashScope, Moonshot)
2. Scrub git history with `git filter-repo --path backend/dashscope.key --path backend/moonshot.key --path backend/dashscope.base --invert-paths`
3. Move key storage to Windows DPAPI or env vars only; remove file-based key reading
4. Add pre-commit gitleaks hook
5. Force-push cleaned history (coordinate with any collaborators)

---

### S1.2 Code Execution Sandbox Has No OS-Level Isolation [CRITICAL]
**Status:** CONFIRMED
**Evidence:**
- `sandbox.py` uses AST validation + `subprocess.run()` with timeout and env scrubbing
- `python -I` flag is used (isolates user site-packages) -- GOOD
- Env is scrubbed: only PYTHONUNBUFFERED, OMP_NUM_THREADS, PYTHONDONTWRITEBYTECODE, PATH, SYSTEMROOT, TEMP, TMP -- GOOD
- BUT: No AppContainer, no restricted token, no Job Object, no network block, no filesystem namespace isolation
- `exec_utils.py run_python()` does NOT use `-I` flag at all -- runs with full environment inheritance
- `auto_fix.py` uses `SandboxedExecutor` (has AST check) AND raw `subprocess.run(["git", "apply"])` -- git apply runs unsandboxed
- `redteam.py` uses `exec_utils.run_python()` which has NO AST validation and NO `-I` flag

**Attack Surface Summary:**
| Exec Path | AST Check | -I Flag | Env Scrub | Timeout | OS Isolation |
|-----------|-----------|---------|-----------|---------|--------------|
| sandbox.py SandboxedExecutor | Yes | No | Partial | Yes | None |
| sandbox.py run_sandboxed | No | Yes | Yes | Yes | None |
| exec_utils.py run_python | No | No | No | Yes | None |
| auto_fix.py git apply | N/A | N/A | No | No | None |

**Impact:** LLM-generated code can read/write arbitrary files, spawn persistent processes, access network, and exfiltrate data. The AST check blocks `eval/exec/compile` calls and forbidden imports, but is trivially bypassed via `__import__()`, `getattr()`, or ctypes.

**Remediation:**
1. IMMEDIATE: Add `-I` flag to ALL exec paths (exec_utils.py, auto_fix.py)
2. IMMEDIATE: Add AST validation to exec_utils.run_python() 
3. SHORT-TERM: Implement Windows AppContainer or restricted-token execution
4. SHORT-TERM: Block network access in sandboxed subprocesses
5. MEDIUM-TERM: Evaluate WSL2 sandbox or Docker container isolation

---

### S1.3 Zero Authentication on Backend API [HIGH]
**Status:** CONFIRMED
**Evidence:**
- `main.py` line 1390: middleware rejects non-localhost clients (`host not in {None, "127.0.0.1", "localhost", "::1"}`)
- CORS allows `http://127.0.0.1:4173-4175`, `tauri.localhost`, `tauri://localhost`
- No bearer token, API key, or session auth on ANY endpoint
- All POST/PUT/DELETE endpoints are accessible to any localhost process

**Impact:** Any malware, browser tab, or compromised application running on the same machine can control Infinity Code: create missions, execute code, access knowledge base, modify settings, trigger paid API calls.

**Remediation:**
1. Generate a random API token at startup; require it as `Authorization: Bearer <token>` header
2. Tauri frontend passes the token via IPC or env var
3. Exempt health/read-only endpoints if desired
4. Tighten CORS to exact Tauri origin only

---

## HIGH Findings

### S1.4 SSRF Guard Missing Redirect-Following Protection [HIGH]
**Status:** PARTIAL
**Evidence:**
- `url_is_blocked()` checks DNS resolution against private/loopback/link-local/reserved ranges -- GOOD
- Uses `socket.getaddrinfo()` which resolves BEFORE the HTTP request -- GOOD
- BUT: Does not prevent DNS rebinding (TOCTOU between check and fetch)
- Does not check redirect targets (if `fetch_url` follows redirects, attacker can redirect to internal IP)
- IPv6 mapped addresses (`::ffff:127.0.0.1`) may bypass depending on Python version

**Remediation:**
1. Disable redirect following in HTTP client, or re-validate each redirect target
2. Use a connection-level IP check (after TCP connect, before HTTP send)
3. Test with `::ffff:127.0.0.1`, `0x7f000001`, `2130706433` formats

### S1.5 MCP Server Config Trust [HIGH]
**Status:** NEEDS VERIFICATION
**Evidence:**
- `mcp_router.py` (15KB) handles MCP server connections
- MCP servers are configured via JSON config that specifies stdio commands
- If an attacker can modify `mcp_servers.json`, they can execute arbitrary commands

**Remediation:** Verify mcp_servers.json is integrity-checked and not writable by untrusted processes.

---

## MEDIUM Findings

### S1.6 Prompt Injection Surface [MEDIUM]
**Status:** DOCUMENTED
**Evidence:**
- `fetch_url` content enters prompts without sanitization
- `web_search` results enter prompts without sanitization  
- Knowledge ingest (Obsidian vault, Downloads) enters RAG without adversarial filtering
- No instruction hierarchy markers in system prompts

### S1.7 Supply Chain [MEDIUM]
**Status:** PENDING AUDIT
- `pip-audit`, `npm audit`, `cargo audit` need to be run
- Tauri v1 is EOL -- no security patches forthcoming
- `react-grab` and `react-scan` are dev dependencies (lower risk)

---

## LOW Findings

### S1.8 partial_executor.py Is Not An Execution Surface [INFO]
**Status:** FALSE ALARM
- Contains only TypedDict definitions and config class
- No subprocess calls, no eval, no exec
- Pure data structure definitions for partial rollout protocol

---

## Summary Scorecard

| ID | Severity | Status | Remediation Effort |
|----|----------|--------|--------------------|
| S1.1 | CRITICAL | Confirmed | Medium (key rotation + history scrub) |
| S1.2 | CRITICAL | Confirmed | High (OS-level sandbox) |
| S1.3 | HIGH | Confirmed | Low (bearer token middleware) |
| S1.4 | HIGH | Partial | Low (redirect check) |
| S1.5 | HIGH | Needs Verification | Low |
| S1.6 | MEDIUM | Documented | Medium |
| S1.7 | MEDIUM | Pending | Low (run audit tools) |
| S1.8 | INFO | False Alarm | None |

## Approved Quick Wins (Can Apply Without Further Approval)
- Add `-I` flag to `exec_utils.py run_python()`
- Add AST validation to `exec_utils.py run_python()`
- Add bearer token auth middleware to `main.py`

## Requires Explicit Approval Before Implementation
- Git history scrubbing (destructive, requires force-push)
- Key rotation (requires updating external services)
- AppContainer/restricted-token sandbox implementation
- MCP server config hardening
