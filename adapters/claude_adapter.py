#!/usr/bin/env python3
"""Wrap Claude Code in the agent-use protocol with all tools disabled.

Run: python3 -B adapters/claude_adapter.py < request.json
Requires the official CLI (`/login` inside `claude`).
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

EFFORTS = {"low", "medium", "high", "max"}
LIMIT = 4 * 1024 * 1024


def interrupted(_signal, _frame):
    raise KeyboardInterrupt


def classify(returncode, stderr):
    text = (stderr or "").lower()
    if any(k in text for k in ("not logged in", "login required", "please run /login", "invalid api key", "api key not valid", "unauthorized", "authentication", "failed to authenticate", "oauth session expired")) or re.search(r"\b(?:http(?:/\d(?:\.\d)?)?|status(?:\s+code)?|code|error)[\s:=]*401\b", text):
        return "credential_missing", True
    if re.search(r"quota|usage.?limit|hit your .*limit|limit.{0,24}resets|"
                 r"per.?(day|week|month)|daily|weekly|monthly", text):
        return "quota_exhausted", False
    if re.search(r"rate.?limit|too many requests|per.?(second|sec|minute|min)\b|\b[rt]pm\b|\b429\b", text):
        return "rate_limited", False
    if re.search(r"\b(?:network|connection|timed out|timeout|dns|enotfound|econnreset|econnrefused)\b|"
                 r"fetch failed|socket hang up|\b50[023]\b", text):
        return "network_failed", False
    return "upstream_failed", False


def model_output_tokens(usage):
    if not isinstance(usage, dict):
        return 0
    count = usage.get("outputTokens", usage.get("output_tokens", 0))
    return count if type(count) is int else 0


def claude_result(stdout):
    """返回正文、错误文本、agent-use 口径的 usage 和实际模型名，后两项可为 None。
    Claude 的 input_tokens 不含缓存读写，这里把缓存写入并进 input_tokens。"""
    try:
        data = json.loads(stdout or "")
    except ValueError:
        return "", stdout or "", None, None
    if not isinstance(data, dict) or data.get("type") != "result":
        return "", "", None, None
    text = data.get("result") if isinstance(data.get("result"), str) else ""
    if data.get("is_error") is not False or data.get("subtype") != "success":
        return "", text, None, None
    actual_model = None
    model_usage = data.get("modelUsage")
    if isinstance(model_usage, dict) and model_usage:
        model = max(model_usage, key=lambda name: model_output_tokens(model_usage[name]))
        if re.fullmatch(r"[A-Za-z0-9_.:/-]{1,256}", model):
            actual_model = model
    usage, count = data.get("usage"), lambda u, k: u.get(k) if type(u.get(k)) is int and u.get(k) >= 0 else None
    keys = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    if not isinstance(usage, dict) or any(count(usage, k) is None for k in keys):
        return text, "", None, actual_model
    write, read = usage["cache_creation_input_tokens"], usage["cache_read_input_tokens"]
    return text, "", {"input_tokens": usage["input_tokens"] + write, "output_tokens": usage["output_tokens"],
                      "cache_read_tokens": read, "cache_write_tokens": write}, actual_model


def generate(request):
    response = {"protocol_version": 1, "state": "failed", "accepted": False}
    binary = os.environ.get("CLAUDE_BIN") or shutil.which("claude")
    if not binary or not Path(binary).exists():
        response.update(reason="credential_missing", fatal=True, missing_credential="claude-cli")
        return response
    options = request.get("options") or {}
    if set(options) - {"effort"}:
        response.update(reason="invalid_request", fatal=True)
        return response
    effort = str(options.get("effort", "low"))
    if effort not in EFFORTS:
        response.update(reason="invalid_request", fatal=True)
        return response
    model = str(request.get("model") or "claude-opus-5-5")
    timeout = max(5, int(request.get("timeout", 60)))
    output = Path(request["output"])
    output.mkdir(parents=True, exist_ok=True)
    task = Path(request["task_file"]).read_text(encoding="utf-8")
    answer = output / "response.txt"
    # Use an empty directory, no tools, one turn, and no session persistence; send the task via stdin.
    # Remove Claude session markers to avoid being treated as a nested Claude session.
    workdir = Path(tempfile.mkdtemp(prefix="claude-adapter-", dir=str(output)))
    argv = [binary, "-p", "--output-format", "json", "--model", model, "--effort", effort,
            "--tools", "", "--disallowedTools", "Bash,Edit,Write,NotebookEdit,WebFetch,WebSearch,Read,Grep,Glob,Agent",
            "--max-turns", "1", "--no-session-persistence"]
    env = {k: v for k, v in os.environ.items() if not k.startswith(("CLAUDECODE", "CLAUDE_CODE_"))}
    env["AIW_NESTED"] = "1"
    started = time.monotonic()
    try:
        proc = subprocess.run(argv, input=task, text=True, capture_output=True, timeout=timeout, cwd=str(workdir), env=env)
    except subprocess.TimeoutExpired:
        response.update(reason="timeout", fatal=False)
        return response
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    elapsed = round(time.monotonic() - started, 2)
    text, error, usage, actual_model = claude_result(proc.stdout)
    answer.write_text(text, encoding="utf-8")
    if proc.returncode != 0 or not text.strip():
        # 未登录等错误写在 JSON 结果里，不一定在 stderr
        reason, fatal = classify(proc.returncode, (proc.stderr or "") + "\n" + error)
        if proc.returncode == 0 and not text.strip() and not error.strip():
            reason, fatal = "empty_response", False
        response.update(reason=reason, fatal=fatal)
        if reason == "credential_missing":
            response["missing_credential"] = "claude-login"
        return response
    metadata = {"transport": "claude_cli", "elapsed_s": elapsed}
    if usage:
        metadata["usage"] = usage
    if actual_model:
        metadata["actual_model"] = actual_model
    status = {"state": "staged_for_review", "accepted": False, "requested_model": model, "effort": effort,
              **metadata}
    (output / "status.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    response.update(state="staged_for_review", response=text, metadata=metadata)
    return response


def main():
    signal.signal(signal.SIGTERM, interrupted)
    try:
        request = json.loads(sys.stdin.buffer.read(LIMIT + 1))
        if not isinstance(request, dict) or request.get("protocol_version") != 1:
            raise ValueError("invalid protocol")
        result = generate(request)
    except KeyboardInterrupt:
        result = {"protocol_version": 1, "state": "failed", "accepted": False, "reason": "interrupted", "fatal": False}
    except Exception:
        result = {"protocol_version": 1, "state": "failed", "accepted": False, "reason": "invalid_request", "fatal": True}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["state"] == "staged_for_review" else 1


if __name__ == "__main__":
    sys.exit(main())
