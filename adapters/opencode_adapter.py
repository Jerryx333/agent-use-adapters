#!/usr/bin/env python3
"""Wrap OpenCode free models in the agent-use protocol with guarded tools.

Run: python3 -B adapters/opencode_adapter.py < request.json
Requires the official CLI (no Zen login required).
Protocol: https://github.com/Jerryx333/agent-use/blob/main/docs/DISPATCHER.md
"""
import json
import math
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from claude_adapter import classify as classify_claude

LIMIT = 4 * 1024 * 1024
DEFAULT_MODEL = "muse-spark-1.3-contributor-free"
PREAMBLE = '[Execution constraints] This is text-only generation: you have no available tools and cannot access any files, repositories, terminals, or networks; all tool calls will be rejected. Everything needed to complete the task is provided below. Do not call tools or start with statements such as "working on it"; output the final answer directly.\n\n'


def interrupted(_signal, _frame):
    raise KeyboardInterrupt


def classify(text):
    text = (text or "").lower()
    if "free tier can only be used from within opencode" in text:
        return "free_tier_rejected", True
    if re.search(r"\b(?:http|status|code|error)[\s:=]*402\b|\b402\s+payment|"
                 r"payment required|insufficient (?:balance|funds|credits?)|"
                 r"余额不足|请充值|需要付费|开通会员|付费会员", text):
        return "payment_required", False
    return classify_claude(1, text)


def parse_events(stdout):
    texts, sessions = [], set()
    calls, error = 0, None
    for line in (stdout or "").splitlines():
        try:
            event = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        session = event.get("sessionID")
        if isinstance(session, str) and session:
            sessions.add(session)
        part = event.get("part") or {}
        if event.get("type") == "text" and isinstance(part, dict) and isinstance(part.get("text"), str):
            texts.append(part["text"])
        elif event.get("type") == "tool_use":
            calls += 1
            texts.clear()
        elif event.get("type") == "error" and error is None:
            detail = event.get("error") or {}
            data = (detail.get("data") or {}) if isinstance(detail, dict) else {}
            error = str(data.get("message", "")) if isinstance(data, dict) else ""
    return "".join(texts), calls, sessions, error


def stop_process(proc):
    # The new session PID is its group ID; stop the entire group to avoid orphaned children.
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        try:
            return proc.communicate(timeout=2)
        finally:
            # Kill surviving descendants even if the parent has exited and closed its pipes.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    except subprocess.TimeoutExpired:
        return proc.communicate()


def cleanup_sessions(binary, stdout, env, workdir):
    was_interrupted = False
    try:
        deadline = time.monotonic() + 10
        for session in sorted(parse_events(stdout)[2]):
            remaining = deadline - time.monotonic()
            if remaining < 1:
                break
            try:
                subprocess.run([binary, "session", "delete", session], env=env, cwd=str(workdir),
                               capture_output=True, text=True, timeout=remaining)
            except (OSError, subprocess.TimeoutExpired):
                pass
    except KeyboardInterrupt:
        was_interrupted = True
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return was_interrupted


def attempt(binary, model, task, output, env, timeout):
    workdir = Path(tempfile.mkdtemp(prefix="opencode-adapter-", dir=str(output)))
    proc = None
    stdout, stderr = "", ""
    reason, fatal = None, False
    try:
        proc = subprocess.Popen([binary, "run", "--pure", "-m", "opencode/" + model, "--format", "json"],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, cwd=str(workdir), env=env, start_new_session=True)
        try:
            stdout, stderr = proc.communicate(input=PREAMBLE + task, timeout=timeout)
        except subprocess.TimeoutExpired:
            stdout, stderr = stop_process(proc)
            reason = "timeout"
        except KeyboardInterrupt:
            stdout, stderr = stop_process(proc)
            reason = "interrupted"
        except Exception:
            stdout, stderr = stop_process(proc)
            raise
        text, calls, sessions, error = parse_events(stdout)
        if reason is None:
            if error is not None or proc.returncode != 0:
                reason, fatal = classify((error or "") + "\n" + (stderr or ""))
            elif not text.strip():
                reason = "tool_use_rejected" if calls else "empty_response"
    finally:
        # Repeated communicate returns all output after timeout or interruption, including session IDs.
        if cleanup_sessions(binary, stdout, env, workdir):
            reason, fatal = "interrupted", False
    return text, calls, reason, fatal


def generate(request):
    response = {"protocol_version": 1, "state": "failed", "accepted": False}
    output = Path(request["output"]).resolve()
    output.mkdir(parents=True, exist_ok=True)

    def failed(reason, fatal, missing=None):
        response.update(reason=reason, fatal=fatal)
        if missing:
            response["missing_credential"] = missing
        (output / "status.json").write_text(json.dumps(response), encoding="utf-8")
        return response

    binary = os.environ.get("OPENCODE_BIN") or shutil.which("opencode") or str(Path.home() / ".opencode/bin/opencode")
    if not Path(binary).is_file():
        return failed("credential_missing", True, "opencode-cli")
    binary = str(Path(binary).resolve())
    options = request.get("options", {})
    model = request.get("model") or DEFAULT_MODEL
    valid_model = lambda value: isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*-free", value)
    if not isinstance(options, dict) or set(options) - {"fallback_models"} or not valid_model(model):
        return failed("invalid_request", True)
    fallback = options.get("fallback_models", "")
    if not isinstance(fallback, str):
        return failed("invalid_request", True)
    candidates = [item.strip() for item in fallback.split(",")] if fallback else []
    if any(not valid_model(item) for item in candidates):
        return failed("invalid_request", True)
    models = list(dict.fromkeys([model, *candidates]))
    timeout = float(request.get("timeout", 180))
    if not math.isfinite(timeout) or timeout <= 0:
        return failed("invalid_request", True)
    task = Path(request["task_file"]).read_text(encoding="utf-8")
    env = dict(os.environ)
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps({"share": "disabled", "autoupdate": False, "permission": {"*": "ask"}})
    env["AIW_NESTED"] = "1"
    started = time.monotonic()
    attempts, calls = [], 0
    reason, fatal = "timeout", False
    for candidate in models:
        remaining = timeout - (time.monotonic() - started)
        if remaining < 5:
            break
        attempt_started = time.monotonic()
        text, count, reason, fatal = attempt(binary, candidate, task, output, env, remaining)
        calls += count
        attempts.append({"model": candidate, "reason": reason or "ok", "elapsed_s": round(time.monotonic() - attempt_started, 2)})
        if reason is None:
            elapsed = round(time.monotonic() - started, 2)
            metadata = {"transport": "opencode_cli", "elapsed_s": elapsed, "served_model": candidate, "actual_model": candidate, "tool_calls_rejected": calls}
            metadata["model_attempts"] = [
                {"requested_model": item["model"],
                 "state": "staged_for_review" if item["reason"] == "ok" else "failed",
                 **({"reason": item["reason"]} if item["reason"] != "ok" else {}),
                 "elapsed_s": item["elapsed_s"]}
                for item in attempts]
            metadata["model_switch_count"] = len(attempts) - 1
            status = {"state": "staged_for_review", "accepted": False, "requested_model": model, **metadata, "attempts": attempts}
            (output / "response.txt").write_text(text, encoding="utf-8")
            (output / "status.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
            response.update(state="staged_for_review", response=text, metadata=metadata)
            return response
        if fatal or reason == "interrupted":
            break
    if reason != "interrupted" and any(item["reason"] == "payment_required" for item in attempts):
        reason, fatal = "payment_required", False
    return failed(reason, fatal, "opencode-login" if reason == "credential_missing" else None)


def main():
    signal.signal(signal.SIGTERM, interrupted)
    try:
        raw = sys.stdin.buffer.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise ValueError("request too large")
        request = json.loads(raw)
        if not isinstance(request, dict) or request.get("protocol_version") != 1:
            raise ValueError("invalid protocol")
        result = generate(request)
    except (Exception, KeyboardInterrupt):
        result = {"protocol_version": 1, "state": "failed", "accepted": False, "reason": "invalid_request", "fatal": True}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["state"] == "staged_for_review" else 1


if __name__ == "__main__":
    sys.exit(main())
