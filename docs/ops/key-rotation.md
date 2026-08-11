# Key Rotation Runbook (URGENT)
Leak source: commit 133a2a8 committed backend/dashscope.key + backend/moonshot.key
in plaintext. They remain in git history even though .gitignore now excludes *.key.

## 1. Rotate DashScope key
1. https://dashscope.console.aliyun.com/ -> API-KEY management
2. Create NEW key, copy it
3. DELETE the old key (the leaked one)
4. Set it: [Environment]::SetEnvironmentVariable('DASHSCOPE_API_KEY', '<new>', 'User')
   (or systemsetx DASHSCOPE_API_KEY <new> in an admin shell)
5. Delete backend/dashscope.key and backend/dashscope.base from disk

## 2. Rotate Moonshot key
1. https://platform.moonshot.cn/console/api-keys
2. Create NEW key, DELETE old key
3. Set MOONSHOT_API_KEY env var as above
4. Delete backend/moonshot.key from disk

## 3. Scrub git history (after rotation)
    cd C:\Users\caleb\infinity-code
    git bundle create ..\infinity-code-backup.bundle --all   # safety backup
    git filter-repo --invert-paths --path backend/dashscope.key --path backend/moonshot.key --path backend/dashscope.base --force
    # filter-repo removes the origin remote; re-add + force-push when ready:
    git remote add origin https://github.com/calebhomwe/InfinityCode.git
    git push origin master --force-with-lease

## 4. Verify
    git log --all -p -- backend/dashscope.key backend/moonshot.key   # must be empty
    .\venv\Scripts\python.exe -m pip install gitleaks  # or winget install gitleaks
    gitleaks detect --no-git                        # zero findings

## 5. Going forward
- Keys ONLY via env vars (backend warns DEPRECATED when reading *.key files)
- gitleaks pre-commit hook blocks secret commits
- providers.json (UI-saved keys) stays plaintext in DATA_DIR for now;
  DPAPI-backed store is on the Stage 2 backlog
