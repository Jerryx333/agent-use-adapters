#!/usr/bin/env python3
"""Offline ACP peer, including deliberate faults and permission round trips.
Run through tests/test_acp_adapter.py (ACP_OPENCODE_BIN points here).
Requires Python 3.11 standard library only.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

SCENARIO = os.environ.get("FAKE_ACP_SCENARIO", "normal")
TRACE = Path(os.environ["FAKE_ACP_TRACE"])
Path(str(TRACE) + ".launch.json").write_text(json.dumps({"argv": sys.argv[1:], "env": {
    name: os.environ.get(name) for name in ("HOME", "XDG_DATA_HOME", "OPENCODE_CONFIG_CONTENT",
        "OPENCODE_TEST_HOME", "OPENCODE_DISABLE_EXTERNAL_SKILLS", "OPENCODE_DISABLE_CLAUDE_CODE",
        "OPENCODE_DISABLE_PROJECT_CONFIG", "OPENCODE_PURE")}}))
PAID = "Your complimentary credits have been used up, and the response has been paused. Continuing the response will use your paid credits."


def receive():
    line = sys.stdin.readline()
    if not line:
        sys.exit(0)
    item = json.loads(line)
    with TRACE.open("a") as f:
        f.write(json.dumps(item) + "\n")
    return item


def emit(item):
    print(json.dumps({"jsonrpc": "2.0", **item}), flush=True)


def reply(request, result):
    emit({"id": request["id"], "result": result})


def update(value, session="session-1"):
    emit({"method": "session/update", "params": {"sessionId": session, "update": value}})


def config(value="old-model"):
    values = [{"value": "test-model", "name": "Test"}]
    if SCENARIO == "grouped":
        values = [{"group": "local", "name": "Local", "options": values}]
    return [{"id": "engine-model", "category": "model", "type": "select",
             "name": "Model", "currentValue": value, "options": values}]


while True:
    request = receive()
    method = request.get("method")
    if method == "initialize":
        reply(request, {"protocolVersion": 2 if SCENARIO == "version" else
                        True if SCENARIO == "bool_version" else 1,
                        "agentCapabilities": {}, "authMethods": [{"id": "login"}]})
    elif method == "session/new":
        if SCENARIO == "auth":
            emit({"id": request["id"], "error": {"code": -32000, "message": "Auth required"}})
            continue
        result = {"sessionId": "session-1"}
        if SCENARIO != "no_model":
            result["configOptions"] = config()
        reply(request, result)
    elif method == "session/set_config_option":
        if SCENARIO == "unsupported_model":
            emit({"id": request["id"], "error": {"code": -32601, "message": "Method not found"}})
        else:
            reply(request, {"configOptions": config("old-model" if SCENARIO == "wrong_model"
                                                    else request["params"]["value"])})
    elif method == "session/prompt":
        if SCENARIO == "opencode_deny_stall":
            # Reproduce OpenCode's stalled no-tools path before any prompt result.
            if json.loads(os.environ["OPENCODE_CONFIG_CONTENT"])["permission"] != {"*": "ask"}:
                continue
        diagnostics = {"paid": PAID, "paid_nested": "Internal error", "payment": "HTTP 402 Payment required",
                       "quota": "Daily quota exhausted", "network": "Connection timed out", "eperm": "EPERM: mkdir denied"}
        if SCENARIO in diagnostics:
            error = {"code": 500, "message": diagnostics[SCENARIO]}
            if SCENARIO == "paid_nested":
                error["data"] = {"details": [{"message": PAID}]}
            emit({"id": request["id"], "error": error})
            continue
        if SCENARIO == "paid_stderr":
            print(PAID, file=sys.stderr, flush=True)
            sys.exit(1)
        if SCENARIO == "duplex_stream":
            # More than a pipe's capacity: a client ignoring stderr would deadlock.
            sys.stderr.write("diagnostic\n" * 30000)
            sys.stderr.flush()
            emit({"id": request["id"], "method": "session/request_permission", "params": {
                "sessionId": "session-1", "toolCall": {"toolCallId": "collision", "kind": "execute"},
                "options": [{"optionId": "deny", "kind": "reject_once"}]}})
            assert receive()["result"]["outcome"]["optionId"] == "deny"
            records = [{"method": "custom/notification", "params": {}},
                       {"method": "session/update", "params": {"sessionId": "session-1", "update": {
                           "sessionUpdate": "available_commands_update", "availableCommands": [{"name": "builtin"}]}}}]
            for text in ("P", "ONG 中文"):
                records.append({"method": "session/update", "params": {"sessionId": "session-1", "update": {
                    "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}}}})
            records.append({"id": request["id"], "result": {"stopReason": "end_turn"}})
            wire = "".join(json.dumps({"jsonrpc": "2.0", **r}, ensure_ascii=False) + "\n" for r in records).encode()
            split = wire.index("中".encode()) + 1
            os.write(sys.stdout.fileno(), wire[:split])
            time.sleep(0.01)
            os.write(sys.stdout.fileno(), wire[split:])
            continue
        if SCENARIO == "crash":
            print("deliberate crash; sensitive diagnostic must not be exposed", file=sys.stderr, flush=True)
            sys.exit(7)
        if SCENARIO == "login_crash":
            print("Authentication required: please run /login", file=sys.stderr, flush=True)
            sys.exit(1)
        if SCENARIO == "malformed":
            print("not JSON", flush=True)
            continue
        if SCENARIO == "oversized":
            print("x" * (4 * 1024 * 1024 + 1), flush=True)
            continue
        if SCENARIO in ("timeout", "interrupt", "descendant"):
            child = subprocess.Popen([sys.executable, "-c",
                "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"])
            Path(str(TRACE) + ".pid").write_text(str(child.pid))
            if SCENARIO != "descendant":
                continue
        if SCENARIO.startswith("permission"):
            decisions = []
            for index, kind in enumerate(("read", "search", "edit", "execute", None)):
                call = {"toolCallId": str(index), "title": "File operation",
                        "rawInput": {"path": "example.txt"}}
                if kind:
                    call["kind"] = kind
                update({"sessionUpdate": "tool_call", **call})
                # Permissions may contain only the ID: clients must retain the prior kind.
                options = [{"optionId": "allow", "kind": "allow_once", "name": "Allow"},
                           {"optionId": "deny", "kind": "reject_once", "name": "Deny"}]
                if SCENARIO == "permission_no_reject":
                    options = options[:1]
                if SCENARIO == "permission_no_allow":
                    options = options[1:]
                emit({"id": "permission-" + str(index), "method": "session/request_permission",
                      "params": {"sessionId": "session-1", "toolCall": {"toolCallId": str(index)},
                                 "options": options}})
                decision = receive()["result"]["outcome"]
                decisions.append(decision.get("optionId", decision["outcome"]))
                update({"sessionUpdate": "tool_call_update", "toolCallId": str(index),
                        "status": "completed" if decision.get("optionId") == "allow" else "failed"})
            update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": ",".join(decisions)}})
        elif SCENARIO == "client_methods":
            for index, name in enumerate(("fs/read_text_file", "fs/write_text_file", "terminal/create", "custom/method")):
                emit({"id": "client-" + str(index), "method": name, "params": {}})
                assert receive()["error"]["code"] == -32601
            update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "PONG"}})
        elif SCENARIO == "pipelined_method":
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            emit({"id": "pipelined", "method": "fs/read_text_file", "params": {}})
            update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "PONG"}})
            reply(request, {"stopReason": "end_turn"})
            assert receive()["error"]["code"] == -32601
            continue
        elif SCENARIO != "empty":
            update({"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "private thought"}})
            chunks = ["P", "ON", "G 中文"] if SCENARIO == "stream" else [PAID] if SCENARIO == "paid_answer" else ["PONG"]
            for chunk in chunks:
                update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": chunk}})
        reply(request, {"stopReason": "cancelled" if SCENARIO == "cancelled" else "end_turn"})
    elif method == "session/cancel":
        # Stay alive so the client must clean up the process group as well.
        continue
