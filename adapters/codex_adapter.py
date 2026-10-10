#!/usr/bin/env python3
"""Wrap Codex CLI in the agent-use protocol with a read-only sandbox.

Run: python3 -B adapters/codex_adapter.py < request.json
Requires the official CLI (`codex login`).
Protocol: https://github.com/Jerryx333/agent-use/blob/main/docs/DISPATCHER.md
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

EFFORTS = {"minimal", "low", "medium", "high", "xhigh"}
LIMIT = 4 * 1024 * 1024


def interrupted(_signal, _frame):
    raise KeyboardInterrupt


def classify(returncode, stderr):
    text = (stderr or "").lower()
    if any(k in text for k in ("not logged in", "login required", "please run `codex login`", "codex login", "unauthorized")) or re.search(r"\b401\b", text):
        return "credential_missing", True
    if re.search(r"quota|usage.?limit|hit your .*limit|limit.{0,24}resets|"
                 r"per.?(day|week|month)|daily|weekly|monthly", text):
        return "quota_exhausted", False
    if re.search(r"rate.?limit|too many requests|per.?(second|sec|minute|min)\b|\b[rt]pm\b|\b429\b", text):
        return "rate_limited", False
    if any(k in text for k in ("network", "connection", "timed out", "timeout", "dns")) or re.search(r"\b50[023]\b", text):
        return "network_failed", False
    return "upstream_failed", False


def generate(request):
    response = {"protocol_version": 1, "state": "failed", "accepted": False}
    binary = os.environ.get("CODEX_BIN") or shutil.which("codex")
    if not binary or not Path(binary).exists():
        response.update(reason="credential_missing", fatal=True, missing_credential="codex-cli")
        return response
    options = request.get("options") or {}
    if set(options) - {"effort"}:
        response.update(reason="invalid_request", fatal=True)
        return response
    effort = str(options.get("effort", "low"))
    if effort not in EFFORTS:
        response.update(reason="invalid_request", fatal=True)
        return response
    model = str(request.get("model") or "gpt-6.1-sol")
    timeout = max(5, int(request.get("timeout", 60)))
    output = Path(request["output"])
    output.mkdir(parents=True, exist_ok=True)
    task = Path(request["task_file"]).read_text(encoding="utf-8")
    answer = output / "response.txt"
    # Use an empty directory, read-only sandbox, and ephemeral session; send the task via stdin.
    workdir = Path(tempfile.mkdtemp(prefix="codex-adapter-", dir=str(output)))
    argv = [binary, "exec", "-m", model, "-c", f'model_reasoning_effort="{effort}"',
            "--cd", str(workdir), "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral",
            "-o", str(answer), "-"]
    started = time.monotonic()
    try:
        proc = subprocess.run(argv, input=task, text=True, capture_output=True, timeout=timeout,
                              env={**os.environ, "AIW_NESTED": "1"})
    except subprocess.TimeoutExpired:
        response.update(reason="timeout", fatal=False)
        return response
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    elapsed = round(time.monotonic() - started, 2)
    text = answer.read_text(encoding="utf-8") if answer.exists() else ""
    if proc.returncode != 0 or not text.strip():
        reason, fatal = classify(proc.returncode, proc.stderr)
        if proc.returncode == 0 and not text.strip():
            reason, fatal = "empty_response", False
        response.update(reason=reason, fatal=fatal)
        if reason == "credential_missing":
            response["missing_credential"] = "codex-login"
        return response
    status = {"state": "staged_for_review", "accepted": False, "requested_model": model, "effort": effort,
              "elapsed_s": elapsed, "transport": "codex_cli"}
    (output / "status.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    response.update(state="staged_for_review", response=text, metadata={"transport": "codex_cli", "elapsed_s": elapsed})
    return response


def main():
    signal.signal(signal.SIGTERM, interrupted)
    try:
        request = json.loads(sys.stdin.buffer.read(LIMIT + 1))
        if not isinstance(request, dict) or request.get("protocol_version") != 1:
            raise ValueError("invalid protocol")
        result = generate(request)
    except (Exception, KeyboardInterrupt):
        result = {"protocol_version": 1, "state": "failed", "accepted": False, "reason": "invalid_request", "fatal": True}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["state"] == "staged_for_review" else 1


if __name__ == "__main__":
    sys.exit(main())
