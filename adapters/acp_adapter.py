#!/usr/bin/env python3
"""Adapt ACP v1 engines to agent-use; all results remain drafts.
Run: python3 -B adapters/acp_adapter.py < request.json
Requires Python 3.11+, POSIX and a separately installed ACP engine.
"""
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from opencode_adapter import classify as classify_upstream
from trajectory import Trajectory, write_trajectory

LIMIT = 4 * 1024 * 1024
COMMANDS = {
    "opencode": ["opencode", "acp"],
    "qwen": ["qwen", "--acp"],
    "agy": ["agy", "acp"],
    "gemini": ["gemini", "--acp"],
    "cursor": ["agent", "acp"],
    "grok": ["grok", "agent", "stdio"],
    "kiro": ["kiro-cli", "acp"],
}


class Failure(Exception):
    def __init__(self, reason, fatal=False, missing=None):
        super().__init__(reason)
        self.reason, self.fatal, self.missing = reason, fatal, missing


def interrupted(_signum, _frame):
    raise KeyboardInterrupt


def classify_error(text):
    # Qwen pauses instead of silently charging when its complimentary pool ends.
    if re.search(r"complimentary credits have been used up|will use your paid credits", text, re.I):
        return "payment_required", False
    return classify_upstream(text)


def error_text(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in error_text(item)]
    if isinstance(value, list):
        return [text for item in value for text in error_text(item)]
    return []


def error_failure(error, agent):
    if not isinstance(error, dict) or type(error.get("code")) is not int:
        return Failure("invalid_protocol")
    code = error["code"]
    # Only classify error payloads, never successful answer text.
    if code == -32000:
        return Failure("credential_missing", True, agent + "-login")
    if code in (-32600, -32601, -32602):
        return Failure("invalid_request", True)
    # RPC code 500 is not necessarily HTTP 500. Classify diagnostic strings,
    # including nested data, without letting the numeric envelope mask the cause.
    reason, fatal = classify_error("\n".join(error_text(error)))
    return Failure(reason, fatal, agent + "-login" if reason == "credential_missing" else None)


class Client:
    """One outstanding client request; duplex, deadline-bound, bounded pipe I/O."""

    def __init__(self, proc, deadline, access, agent, trajectory):
        self.proc, self.deadline = proc, deadline
        self.access, self.agent, self.trajectory = access, agent, trajectory
        self.session = None
        self.phase = "starting"
        self.sequence = 0
        self.selector = selectors.DefaultSelector()
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            os.set_blocking(stream.fileno(), False)
        self.selector.register(proc.stdout, selectors.EVENT_READ)
        self.selector.register(proc.stderr, selectors.EVENT_READ)
        self.outgoing = bytearray()
        self.buffer = bytearray()
        self.stderr = bytearray()
        self.bytes_read = 0
        self.stdout_eof = False
        self.cancelling = False
        self.text = []
        self.calls = {}
        self.available_commands = None

    def send(self, message):
        data = (json.dumps({"jsonrpc": "2.0", **message}, ensure_ascii=False) + "\n").encode()
        if len(data) + len(self.outgoing) > LIMIT:
            raise Failure("input_limit")
        if not self.outgoing:
            self.selector.register(self.proc.stdin, selectors.EVENT_WRITE)
        self.outgoing.extend(data)

    def pump(self, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Failure("timeout")
        for key, mask in self.selector.select(remaining):
            stream = key.fileobj
            if mask & selectors.EVENT_WRITE:
                try:
                    count = os.write(stream.fileno(), self.outgoing[:65536])
                except BlockingIOError:
                    continue
                except BrokenPipeError:
                    self.selector.unregister(stream)
                    self.outgoing.clear()
                    continue  # Drain the error stream before classifying the exit.
                del self.outgoing[:count]
                if not self.outgoing:
                    self.selector.unregister(stream)
            else:
                try:
                    data = os.read(stream.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not data:
                    self.selector.unregister(stream)
                    if stream is self.proc.stdout:
                        self.stdout_eof = True
                    continue
                self.bytes_read += len(data)
                if self.bytes_read > LIMIT:
                    raise Failure("output_too_large")
                if stream is self.proc.stdout:
                    self.buffer.extend(data)
                else:
                    self.stderr.extend(data)

    def receive(self, deadline=None):
        deadline = self.deadline if deadline is None else deadline
        while True:
            if time.monotonic() >= deadline:
                raise Failure("timeout")
            newline = self.buffer.find(b"\n")
            if newline >= 0:
                line = self.buffer[:newline]
                del self.buffer[:newline + 1]
                try:
                    message = json.loads(line)
                except (ValueError, UnicodeError):
                    raise Failure("invalid_protocol") from None
                if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                    raise Failure("invalid_protocol")
                return message
            if self.stdout_eof:
                # stderr may be ready in the next select batch, including auth errors.
                if self.proc.stderr in [key.fileobj for key in self.selector.get_map().values()]:
                    self.pump(deadline)
                    continue
                if self.buffer:
                    raise Failure("invalid_protocol")
                reason, fatal = classify_error(self.stderr.decode("utf-8", errors="replace"))
                raise Failure(reason, fatal, self.agent + "-login" if reason == "credential_missing" else None)
            self.pump(deadline)

    def tool(self, update, rejected=None):
        call_id = update.get("toolCallId")
        if not isinstance(call_id, str) or not call_id:
            raise Failure("invalid_protocol")
        previous = self.calls.get(call_id, {})
        for field in ("kind", "name", "title"):
            if isinstance(update.get(field), str):
                previous[field] = update[field][:2000]
        if update.get("rawInput") is not None:
            value = update["rawInput"]
            previous["input"] = (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))[:2000]
        if call_id not in self.calls and len(self.calls) >= 2000:
            raise Failure("output_too_large")
        self.calls[call_id] = previous
        tool = previous.get("name") or previous.get("kind") or "other"
        tool = re.sub(r"[\s\x00-\x1f]", "_", tool)[:128] or "other"
        row = {"ts": datetime.now(timezone.utc).isoformat(), "tool": tool,
               "input": previous.get("input", previous.get("title", call_id))}
        if previous.get("kind") in ("edit", "delete", "move"):
            row["edit"] = True
        if rejected is not None:
            row["rejected"] = rejected
        self.trajectory.append(row)
        return previous

    def handle(self, message):
        method, params = message.get("method"), message.get("params", {})
        if not isinstance(method, str) or not isinstance(params, dict):
            raise Failure("invalid_protocol")
        if "id" in message:
            if method != "session/request_permission":
                self.send({"id": message["id"], "error": {"code": -32601, "message": "Method not found"}})
                return
            outcome = {"outcome": "cancelled"}
            call, options = params.get("toolCall"), params.get("options")
            if not isinstance(call, dict) or not isinstance(options, list):
                raise Failure("invalid_protocol")
            known = self.calls.get(call.get("toolCallId"), {})
            kind = call.get("kind", known.get("kind"))
            allowed = (not self.cancelling and self.session is not None and params.get("sessionId") == self.session
                       and (self.access == "rw" or (self.access == "ro" and kind in ("read", "search"))))
            preferences = (["allow_once", "allow_always"] if allowed else []) + ["reject_once", "reject_always"]
            selected = None
            for preference in preferences:
                selected = next((o for o in options if isinstance(o, dict) and o.get("kind") == preference
                                 and isinstance(o.get("optionId"), str) and o["optionId"]), None)
                if selected is not None:
                    outcome = {"outcome": "selected", "optionId": selected["optionId"]}
                    break
            self.tool(call, rejected=selected is None or selected["kind"].startswith("reject"))
            self.send({"id": message["id"], "result": {"outcome": outcome}})
            return
        if method != "session/update":
            return  # Unknown notifications do not receive responses.
        if self.session is None or params.get("sessionId") != self.session:
            return
        update = params.get("update")
        if not isinstance(update, dict):
            raise Failure("invalid_protocol")
        variant = update.get("sessionUpdate")
        if variant == "available_commands_update":
            commands = update.get("availableCommands")
            if not isinstance(commands, list) or any(not isinstance(c, dict) or not isinstance(c.get("name"), str) for c in commands):
                raise Failure("invalid_protocol")
            self.available_commands = [c["name"] for c in commands]
        elif variant == "agent_message_chunk":
            content = update.get("content")
            if not isinstance(content, dict):
                raise Failure("invalid_protocol")
            if content.get("type") == "text":
                if not isinstance(content.get("text"), str):
                    raise Failure("invalid_protocol")
                self.text.append(content["text"])
        elif variant in ("tool_call", "tool_call_update"):
            self.tool(update)

    def request(self, method, params):
        self.phase = method
        self.sequence += 1
        request_id = self.sequence
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            message = self.receive()
            if "method" in message:
                self.handle(message)
                continue
            if type(message.get("id")) is not int or message["id"] != request_id:
                raise Failure("invalid_protocol")
            if ("error" in message) == ("result" in message):
                raise Failure("invalid_protocol")
            # A peer may pipeline a client request and the prompt result. Deliver
            # our permission/method-not-found reply before considering the turn done.
            while self.outgoing:
                self.pump(self.deadline)
            if "error" in message:
                raise error_failure(message["error"], self.agent)
            if not isinstance(message["result"], dict):
                raise Failure("invalid_protocol")
            return message["result"]

    def cancel(self):
        if self.session is None:
            return
        self.cancelling = True
        self.send({"method": "session/cancel", "params": {"sessionId": self.session}})
        grace = time.monotonic() + 0.3
        try:
            while time.monotonic() < grace:
                message = self.receive(grace)
                if "method" in message:
                    self.handle(message)
        except (Failure, OSError):
            pass  # Cancellation is best effort; process-group cleanup is unconditional.

    def close(self):
        self.selector.close()
        try:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(self.proc.pid, sig)
                except ProcessLookupError:
                    pass
                if sig == signal.SIGTERM:
                    try:
                        self.proc.wait(timeout=0.3)
                    except subprocess.TimeoutExpired:
                        pass
            self.proc.wait(timeout=1)
        finally:
            self.proc.poll()  # Reap an already-exited child even if killpg was denied.
            for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
                stream.close()


def choose_model(client, session, model):
    options = session.get("configOptions", [])
    if not isinstance(options, list):
        raise Failure("invalid_protocol")
    option = next((o for o in options if isinstance(o, dict) and o.get("category") == "model"
                   and o.get("type") == "select"), None)
    if not option or not isinstance(option.get("id"), str):
        raise Failure("invalid_request", True)
    choices = option.get("options", [])
    if not isinstance(choices, list):
        raise Failure("invalid_protocol")
    values = []
    for item in choices:
        if isinstance(item, dict):
            if "group" in item and isinstance(item.get("options"), list):
                values.extend(o.get("value") for o in item["options"] if isinstance(o, dict))
            else:
                values.append(item.get("value"))
    if model not in values:
        raise Failure("invalid_request", True)
    result = client.request("session/set_config_option", {
        "sessionId": client.session, "configId": option["id"], "value": model})
    updated = result.get("configOptions", [])
    if not isinstance(updated, list) or not any(isinstance(o, dict) and o.get("id") == option["id"]
                                               and o.get("currentValue") == model for o in updated):
        raise Failure("invalid_request", True)


def launch_config(agent, runtime, access):
    argv = COMMANDS[agent].copy()
    executable = os.environ.get("ACP_" + agent.upper() + "_BIN") or argv[0]
    binary = shutil.which(executable)
    if not binary:
        raise Failure("credential_missing", True, agent + "-cli")
    argv[0] = str(Path(binary).resolve())
    env = dict(os.environ, AIW_NESTED="1")
    if agent == "opencode":
        # Do not merge the user's or workspace's MCP/plugin/permission settings.
        for name in tuple(env):
            if name.startswith("OPENCODE_"):
                env.pop(name)
        home = runtime / "home"
        home.mkdir()
        # OpenCode 1.19.6 with the free model stalls under deny-only permissions.
        # Keep permission round trips enabled; the ACP client enforces access.
        permissions = {"*": "ask"}
        env.update(HOME=str(home), XDG_CONFIG_HOME=str(home / "config"),
                   XDG_DATA_HOME=str(home / "data"), XDG_CACHE_HOME=str(home / "cache"),
                   XDG_STATE_HOME=str(home / "state"), OPENCODE_DISABLE_PROJECT_CONFIG="1",
                   OPENCODE_DISABLE_AUTOUPDATE="1", OPENCODE_PURE="1",
                   OPENCODE_TEST_HOME=str(home), OPENCODE_DISABLE_EXTERNAL_SKILLS="1",
                   OPENCODE_DISABLE_CLAUDE_CODE="1",
                   OPENCODE_CONFIG_CONTENT=json.dumps({"share": "disabled", "autoupdate": False,
                                                      "permission": permissions, "mcp": {}}))
    elif agent == "qwen":
        argv.extend(["--setting-sources", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                     "--permission-mode", "default", "--disable-builtin-skills", "--settings", json.dumps({
                         "permissions": {"ask": ["*"]}, "skills": {"enabled": False},
                         "context": {"fileName": []}, "autoMemoryEnabled": False,
                         "disableAllHooks": True, "experimental": {"enableAgents": False}})])
        if access != "rw":
            argv.extend(["--tools", "" if access == "none" else "Read,Grep,Glob"])
    return argv, env


def generate(request):
    response = {"protocol_version": 1, "state": "failed", "accepted": False}
    trajectory = Trajectory()
    metadata = {"transport": "acp_stdio"}
    output, client, agent = None, None, None
    started = time.monotonic()
    try:
        if not isinstance(request, dict):
            raise Failure("invalid_request", True)
        if not isinstance(request.get("output"), str) or not request["output"]:
            raise Failure("invalid_request", True)
        output = Path(request["output"]).resolve()
        output.mkdir(parents=True, exist_ok=True)
        # Clear any stale answer immediately, including for a failed retry.
        (output / "response.txt").write_text("", encoding="utf-8")
        options, access = request.get("options", {}), request.get("access", "none")
        timeout, model, workdir = request.get("timeout", 120), request.get("model"), request.get("workdir")
        if (type(request.get("protocol_version")) is not int or request["protocol_version"] != 1
                or not isinstance(options, dict) or set(options) != {"agent"}
                or not isinstance(options["agent"], str) or options["agent"] not in COMMANDS
                or access not in ("none", "ro", "rw")
                or type(timeout) not in (float, int) or not math.isfinite(timeout) or timeout <= 0
                or (model is not None and (not isinstance(model, str) or not model.strip()))):
            raise Failure("invalid_request", True)
        agent = options["agent"]
        if access == "none":
            if workdir is not None:
                raise Failure("invalid_request", True)
        elif not isinstance(workdir, str) or not Path(workdir).is_absolute() or not Path(workdir).is_dir():
            raise Failure("invalid_request", True)
        task_path = request.get("task_file")
        if not isinstance(task_path, str):
            raise Failure("invalid_request", True)
        with open(task_path, "rb") as stream:
            task = stream.read(LIMIT + 1)
        if len(task) > LIMIT:
            raise Failure("input_limit")
        task = task.decode("utf-8")
        with tempfile.TemporaryDirectory(prefix="acp-adapter-") as temporary:
            runtime = Path(temporary)
            cwd = Path(workdir).resolve() if workdir else runtime / "workspace"
            if not workdir:
                cwd.mkdir()
            argv, env = launch_config(agent, runtime, access)
            metadata["isolation"] = {
                "opencode": {"configuration": "temporary_home_and_xdg", "external_skills": "disabled",
                             "user_project_instructions": "disabled", "mcp": "empty_config",
                             "permissions": "ask_then_acp_access_policy",
                             "limits": "Not an OS sandbox; managed system configuration is unverified."},
                "qwen": {"configuration": "empty_setting_sources", "skills": "disabled",
                         "mcp": "strict_empty_config", "instructions": "empty_context_file_names",
                         "limits": "User home retained for login. Global/managed instructions and plugin isolation are unverified."},
            }.get(agent, {"limits": "Engine-local configuration, skills, instructions and MCP isolation are unverified."})
            proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    cwd=cwd, env=env, start_new_session=True)
            client = Client(proc, started + timeout, access, agent, trajectory)
            primary_error = None
            try:
                hello = client.request("initialize", {"protocolVersion": 1, "clientCapabilities": {},
                    "clientInfo": {"name": "agent-use-acp", "version": "1.0.0"}})
                if type(hello.get("protocolVersion")) is not int or hello["protocolVersion"] != 1:
                    raise Failure("invalid_protocol")
                session = client.request("session/new", {"cwd": str(cwd), "mcpServers": []})
                if not isinstance(session.get("sessionId"), str) or not session["sessionId"]:
                    raise Failure("invalid_protocol")
                client.session = session["sessionId"]
                if model is not None:
                    choose_model(client, session, model)
                    metadata["selected_model"] = model
                result = client.request("session/prompt", {"sessionId": client.session,
                    "prompt": [{"type": "text", "text": task}]})
                stop = result.get("stopReason")
                if stop == "cancelled":
                    raise Failure("interrupted")
                if stop != "end_turn":
                    raise Failure("upstream_failed" if isinstance(stop, str) else "invalid_protocol")
                answer = "".join(client.text)
                if not answer.strip():
                    raise Failure("empty_response")
                response.update(state="staged_for_review", response=answer)
            except (Failure, KeyboardInterrupt) as error:
                primary_error = error
                if isinstance(error, KeyboardInterrupt) or error.reason in ("timeout", "interrupted"):
                    try:
                        client.cancel()
                    except (Failure, OSError) as cancel_error:
                        metadata["cancel_error"] = type(cancel_error).__name__
                raise
            finally:
                metadata["phase"] = client.phase
                if client.available_commands is not None:
                    metadata["available_commands"] = client.available_commands
                try:
                    client.close()
                except (OSError, subprocess.TimeoutExpired) as cleanup_error:
                    metadata["cleanup_error"] = type(cleanup_error).__name__
                    if primary_error is None:
                        raise Failure("adapter_io_failed") from None
    except Failure as error:
        response.update(reason=error.reason, fatal=error.fatal)
        if error.missing:
            response["missing_credential"] = error.missing
    except KeyboardInterrupt:
        response.update(reason="interrupted", fatal=False)
    except (ValueError, TypeError, KeyError, UnicodeError):
        response.update(reason="invalid_request", fatal=True)
    except OSError:
        response.update(reason="cli_unavailable" if client is None and agent else "adapter_io_failed", fatal=False)
    metadata.update(trajectory.metadata())
    metadata["elapsed_s"] = round(time.monotonic() - started, 3)
    response["metadata"] = metadata
    if len(json.dumps(response, ensure_ascii=False).encode("utf-8")) > LIMIT:
        response.pop("response", None)
        response.update(state="failed", reason="output_too_large", fatal=False)
    if "reason" in response:
        response["state"] = "failed"
        response.pop("response", None)
    if output:
        status = {k: v for k, v in response.items() if k not in ("metadata", "response")}
        status.update({k: v for k, v in metadata.items() if k != "trajectory"})
        write_trajectory(output, metadata, status)
        (output / "response.txt").write_text(response.get("response", ""), encoding="utf-8")
        (output / "status.json").write_text(json.dumps(status, ensure_ascii=False), encoding="utf-8")
    return response


def main():
    signal.signal(signal.SIGTERM, interrupted)
    try:
        raw = sys.stdin.buffer.read(LIMIT + 1)
        if len(raw) > LIMIT:
            raise Failure("input_limit")
        result = generate(json.loads(raw))
    except (Exception, KeyboardInterrupt) as error:
        reason = error.reason if isinstance(error, Failure) else "interrupted" if isinstance(error, KeyboardInterrupt) else "invalid_request"
        result = {"protocol_version": 1, "state": "failed", "accepted": False,
                  "reason": reason, "fatal": reason == "invalid_request"}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["state"] == "staged_for_review" else 1


if __name__ == "__main__":
    sys.exit(main())
