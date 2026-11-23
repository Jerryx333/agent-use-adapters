"""Exercise ACP over real pipes and subprocesses, with an entirely offline peer."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "adapters"))
import acp_adapter


class ACPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.task = self.root / "task.txt"
        self.task.write_text("只回复 PONG", encoding="utf-8")
        self.work = self.root / "work"
        self.work.mkdir()
        self.agent = self.root / "agent"
        self.agent.write_bytes((ROOT / "tests/fixtures/fake_acp_agent.py").read_bytes())
        self.agent.chmod(0o755)
        self.trace = self.root / "trace.jsonl"
        self.env = dict(os.environ, ACP_OPENCODE_BIN=str(self.agent), ACP_QWEN_BIN=str(self.agent), FAKE_ACP_TRACE=str(self.trace))

    def request(self, access="none", **extra):
        return {"protocol_version": 1, "task_file": str(self.task),
                "output": str(self.root / "out"), "options": {"agent": "opencode"},
                "access": access, "workdir": str(self.work) if access != "none" else None,
                "timeout": 5, **extra}

    def run_peer(self, scenario="normal", access="none", **extra):
        request = self.request(access, **extra)
        done = subprocess.run([sys.executable, "-B", str(ROOT / "adapters/acp_adapter.py")],
            input=json.dumps(request), capture_output=True, text=True, timeout=12,
            env={**self.env, "FAKE_ACP_SCENARIO": scenario})
        result = json.loads(done.stdout)
        self.assertEqual(done.returncode, 0 if result["state"] == "staged_for_review" else 1, done.stderr)
        self.assertIs(result["accepted"], False)
        status = json.loads((self.root / "out/status.json").read_text())
        self.assertEqual(status["state"], result["state"])
        if result["state"] == "failed":
            self.assertEqual((self.root / "out/response.txt").read_text(), "")
            self.assertEqual(status["reason"], result["reason"])
        else:
            self.assertEqual((self.root / "out/response.txt").read_text(), result["response"])
        return result

    def messages(self):
        return [json.loads(line) for line in self.trace.read_text().splitlines()]

    def test_normal_handshake_and_empty_directory(self):
        self.assertEqual(self.run_peer()["response"], "PONG")
        messages = self.messages()
        self.assertEqual([m.get("method") for m in messages], ["initialize", "session/new", "session/prompt"])
        self.assertEqual(messages[0]["params"]["protocolVersion"], 1)
        self.assertEqual(messages[0]["params"]["clientCapabilities"], {})
        self.assertEqual(messages[1]["params"]["mcpServers"], [])
        self.assertNotEqual(messages[1]["params"]["cwd"], str(self.work))
        self.assertFalse(Path(messages[1]["params"]["cwd"]).exists())

    def test_stream_and_model(self):
        result = self.run_peer("stream", model="test-model")
        self.assertEqual(result["response"], "PONG 中文")
        self.assertEqual(result["metadata"]["selected_model"], "test-model")
        selection = self.messages()[2]
        self.assertEqual(selection["method"], "session/set_config_option")
        self.assertEqual(selection["params"], {"sessionId": "session-1", "configId": "engine-model", "value": "test-model"})

    def test_grouped_models(self):
        self.assertEqual(self.run_peer("grouped", model="test-model")["response"], "PONG")

    def test_model_selection_never_silently_falls_back(self):
        for scenario, model in (("no_model", "test-model"), ("unsupported_model", "test-model"),
                                ("wrong_model", "test-model"), ("normal", "not-advertised")):
            with self.subTest(scenario=scenario):
                self.trace.unlink(missing_ok=True)
                self.assertEqual(self.run_peer(scenario, model=model)["reason"], "invalid_request")
                self.assertNotIn("session/prompt", [m.get("method") for m in self.messages()])

    def test_permissions_for_all_access_modes(self):
        for access, expected in (("none", "deny,deny,deny,deny,deny"),
                                 ("ro", "allow,allow,deny,deny,deny"),
                                 ("rw", "allow,allow,allow,allow,allow")):
            with self.subTest(access=access):
                result = self.run_peer("permission", access)
                self.assertEqual(result["response"], expected)
                rows = [json.loads(line) for line in (self.root / "out/trajectory.jsonl").read_text().splitlines()]
                self.assertEqual(len(rows), 5)
                self.assertTrue(any(row.get("edit") for row in rows))
                if access != "rw":
                    self.assertTrue(any(row.get("rejected") for row in rows))
                if access != "none":
                    self.assertTrue(any(m.get("method") == "session/new" and Path(m["params"]["cwd"]).resolve() == self.work.resolve() for m in self.messages()))

    def test_workspace_alias_is_resolved(self):
        alias = self.root / "work-alias"
        alias.symlink_to(self.work, target_is_directory=True)
        self.assertEqual(self.run_peer(access="ro", workdir=str(alias))["response"], "PONG")
        cwd = next(m["params"]["cwd"] for m in self.messages() if m.get("method") == "session/new")
        self.assertEqual(Path(cwd), self.work.resolve())

    def test_opencode_launch_keeps_permission_requests_available(self):
        for access in ("none", "ro", "rw"):
            with self.subTest(access=access):
                self.assertEqual(self.run_peer("opencode_deny_stall", access, timeout=1)["response"], "PONG")
                launch = json.loads(Path(str(self.trace) + ".launch.json").read_text())
                env = launch["env"]
                config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
                self.assertEqual(config, {"share": "disabled", "autoupdate": False, "permission": {"*": "ask"}, "mcp": {}})
                self.assertEqual(env["OPENCODE_TEST_HOME"], env["HOME"])
                self.assertEqual(env["OPENCODE_DISABLE_EXTERNAL_SKILLS"], "1")
                self.assertEqual(env["OPENCODE_DISABLE_CLAUDE_CODE"], "1")
                self.assertEqual(env["OPENCODE_DISABLE_PROJECT_CONFIG"], "1")
                self.assertEqual(env["OPENCODE_PURE"], "1")
                self.assertTrue(Path(env["XDG_DATA_HOME"]).is_relative_to(env["HOME"]))

    def test_qwen_isolation_flags(self):
        self.assertEqual(self.run_peer(options={"agent": "qwen"})["response"], "PONG")
        argv = json.loads(Path(str(self.trace) + ".launch.json").read_text())["argv"]
        self.assertEqual(argv[argv.index("--setting-sources") + 1], "")
        self.assertIn("--strict-mcp-config", argv)
        self.assertEqual(json.loads(argv[argv.index("--mcp-config") + 1]), {"mcpServers": {}})
        self.assertIn("--disable-builtin-skills", argv)
        settings = json.loads(argv[argv.index("--settings") + 1])
        self.assertIs(settings["skills"]["enabled"], False)
        self.assertEqual(settings["context"]["fileName"], [])
        status = json.loads((self.root / "out/status.json").read_text())
        self.assertIn("unverified", status["isolation"]["limits"])

    def test_batched_fragmented_stream_and_colliding_server_request_id(self):
        result = self.run_peer("duplex_stream")
        self.assertEqual(result["response"], "PONG 中文")
        self.assertEqual(result["metadata"]["available_commands"], ["builtin"])

    def test_rpc_errors_classify_diagnostic_text_not_envelope_code(self):
        for scenario, reason in (("paid", "payment_required"), ("paid_nested", "payment_required"),
                                 ("paid_stderr", "payment_required"), ("payment", "payment_required"),
                                 ("quota", "quota_exhausted"), ("network", "network_failed"),
                                 ("eperm", "upstream_failed")):
            with self.subTest(scenario=scenario):
                self.assertEqual(self.run_peer(scenario, options={"agent": "qwen"})["reason"], reason)
        self.assertEqual(self.run_peer("paid_answer")["state"], "staged_for_review")

    def test_missing_permission_choices(self):
        self.assertEqual(self.run_peer("permission_no_reject")["response"], ",".join(["cancelled"] * 5))
        self.assertEqual(self.run_peer("permission_no_allow", "rw")["response"], ",".join(["deny"] * 5))

    def test_client_fs_terminal_not_implemented(self):
        self.assertEqual(self.run_peer("client_methods")["response"], "PONG")

    def test_client_reply_is_flushed_before_pipelined_prompt_result(self):
        self.assertEqual(self.run_peer("pipelined_method")["response"], "PONG")
        reply = next(m for m in self.messages() if m.get("id") == "pipelined")
        self.assertEqual(reply["error"]["code"], -32601)

    def test_faults(self):
        for scenario, reason in (("version", "invalid_protocol"), ("bool_version", "invalid_protocol"),
                                 ("crash", "upstream_failed"), ("auth", "credential_missing"),
                                 ("login_crash", "credential_missing"), ("malformed", "invalid_protocol"),
                                 ("oversized", "output_too_large"), ("empty", "empty_response"),
                                 ("cancelled", "upstream_failed")):
            with self.subTest(scenario=scenario):
                result = self.run_peer(scenario)
                self.assertEqual(result["reason"], reason)
                self.assertNotIn("sensitive diagnostic", json.dumps(result))
                if reason == "credential_missing":
                    self.assertEqual(result["missing_credential"], "opencode-login")

    def assert_child_stopped(self):
        pid = int(Path(str(self.trace) + ".pid").read_text())
        for _ in range(30):
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.02)
        self.fail(f"child process {pid} survived cleanup")

    def test_timeout_cancel_and_process_group_cleanup(self):
        started = time.monotonic()
        self.assertEqual(self.run_peer("timeout", timeout=0.7)["reason"], "timeout")
        self.assertLess(time.monotonic() - started, 4)
        self.assertTrue(any(m.get("method") == "session/cancel" for m in self.messages()))
        self.assertTrue(any(m.get("id") == "pending" and m.get("result") == {
            "outcome": {"outcome": "cancelled"}} for m in self.messages()))
        self.assert_child_stopped()

    def permission_client(self):
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        client = acp_adapter.Client(proc, time.monotonic() + 3, "ro", "opencode",
                                    acp_adapter.Trajectory(), self.work)
        self.addCleanup(client.close)
        client.session = "s1"
        return client

    def decide(self, client, call, options=None):
        client.handle({"id": 99, "method": "session/request_permission", "params": {
            "sessionId": "s1", "toolCall": {"toolCallId": "r", **call},
            "options": options if options is not None else [
                {"kind": "allow_once", "optionId": "allow"}, {"kind": "reject_once", "optionId": "reject"}]}})
        reply = json.loads(client.outgoing.decode().splitlines()[-1])
        return reply["result"]["outcome"]

    def test_ro_checks_all_paths_and_fails_closed(self):
        (self.work / "outside-link").symlink_to(self.root)
        unsafe = [
            {"locations": [{"path": "/Users/jerry/.ssh/id_rsa"}]},
            {"locations": [{"path": "ok"}, {"path": "../task.txt"}]},
            {"rawInput": {"path": "outside-link/task.txt"}},
            {"rawInput": {"file_path": str(self.task)}},
            {"rawInput": {"nested": [{"filePath": str(self.task)}]}},
            {"rawInput": {"paths": ["ok", "../task.txt"]}},
            {"rawInput": {"directory": ".."}},
            {"rawInput": {"cwd": str(self.root)}},
            {"rawInput": {"path": None}}, {"rawInput": {"path": ""}},
            {"rawInput": {"path": "bad\x00path"}}, {"locations": [{}]},
            {"rawInput": {"query": "hello"}}, {},
            {"locations": [{"path": "ok"}], "rawInput": {"path": str(self.task)}},
        ]
        client = self.permission_client()
        for index, fields in enumerate(unsafe):
            for kind in ("read", "search"):
                with self.subTest(fields=fields, kind=kind):
                    self.assertEqual(self.decide(client, {"toolCallId": f"{index}-{kind}", "kind": kind, **fields}),
                                     {"outcome": "selected", "optionId": "reject"})
        for index, fields in enumerate(({"locations": [{"path": "ok"}]},
                                       {"rawInput": {"file_path": str(self.work / "ok")}},
                                       {"rawInput": {"path": ".", "pattern": "*.py"}})):
            self.assertEqual(self.decide(client, {"toolCallId": f"safe-{index}", "kind": "read", **fields}),
                             {"outcome": "selected", "optionId": "allow"})

    def test_permission_cannot_downgrade_kind_or_paths(self):
        client = self.permission_client()
        for index, (first, later) in enumerate((
            ({"kind": "edit", "rawInput": {"path": "ok"}}, {"kind": "read"}),
            ({"kind": "read", "rawInput": {"path": "ok"}}, {"kind": "execute"}),
            ({"kind": "other", "rawInput": {"path": "ok"}}, {"kind": "search"}),
            ({"kind": "read", "rawInput": {"path": str(self.task)}}, {"rawInput": {"path": "ok"}}),
            ({"kind": "read", "locations": [{"path": str(self.task)}]}, {"locations": [{"path": "ok"}]}),
        )):
            with self.subTest(first=first, later=later):
                client.tool({"toolCallId": str(index), **first})
                self.assertEqual(self.decide(client, {"toolCallId": str(index), **later}),
                                 {"outcome": "selected", "optionId": "reject"})
                client.tool({"toolCallId": str(index), "kind": "read", "status": "completed"})
                self.assertTrue(client.trajectory[index]["rejected"])
        self.assertEqual(len(client.trajectory), 5)

    def test_ro_never_selects_allow_always_and_cancellation_wins(self):
        client = self.permission_client()
        call = {"kind": "read", "rawInput": {"path": "ok"}}
        always = [{"kind": "allow_always", "optionId": "always"}]
        self.assertEqual(self.decide(client, call, always), {"outcome": "cancelled"})
        self.assertEqual(self.decide(client, call, always + [{"kind": "reject_once", "optionId": "reject"}]),
                         {"outcome": "selected", "optionId": "reject"})
        client.cancelling = True
        self.assertEqual(self.decide(client, call), {"outcome": "cancelled"})

    def test_ro_resolves_previous_paths_again_at_permission_time(self):
        client = self.permission_client()
        link = self.work / "link"
        link.symlink_to(self.work)
        client.tool({"toolCallId": "r", "kind": "read", "rawInput": {"path": "link/task.txt"}})
        link.unlink()
        link.symlink_to(self.root)
        self.assertEqual(self.decide(client, {}), {"outcome": "selected", "optionId": "reject"})

    def test_rejected_tool_without_body_and_peer_cancellation(self):
        for scenario in ("denied_empty", "denied_cancelled"):
            with self.subTest(scenario=scenario):
                self.trace.unlink(missing_ok=True)
                self.assertEqual(self.run_peer(scenario, "ro")["reason"], "tool_use_rejected")
                self.assertFalse(any(m.get("method") == "session/cancel" for m in self.messages()))

    def test_exit_with_inherited_pipes_is_classified_promptly(self):
        for scenario in ("exit_stdout", "exit_stderr"):
            with self.subTest(scenario=scenario):
                started = time.monotonic()
                self.assertEqual(self.run_peer(scenario)["reason"], "quota_exhausted")
                self.assertLess(time.monotonic() - started, 2)
                self.assert_child_stopped()

    def test_stderr_tail_and_separate_stdout_limits(self):
        self.assertEqual(self.run_peer("stderr_flood")["response"], "PONG")
        self.assertEqual(self.run_peer("stdout_large")["response"], "PONG")
        self.assertEqual(self.run_peer("stdout_total")["reason"], "output_too_large")
        self.assertEqual(self.run_peer("stale_stderr")["reason"], "upstream_failed")

    def test_stderr_memory_is_bounded(self):
        client = self.permission_client()
        with mock.patch.object(client.selector, "select", return_value=[
                (mock.Mock(fileobj=client.proc.stderr), acp_adapter.selectors.EVENT_READ)]), \
                mock.patch.object(acp_adapter.os, "read", return_value=b"x" * 65536):
            for _ in range(80):
                client.pump(client.deadline)
        self.assertEqual(len(client.stderr), 65536)

    def test_error_classification_regressions(self):
        import claude_adapter
        import opencode_adapter
        for diagnostic, expected in (
            ("getaddrinfo ENOTFOUND", ("network_failed", False)),
            ("read ECONNRESET", ("network_failed", False)),
            ("ECONNREFUSED", ("network_failed", False)),
            ("fetch failed", ("network_failed", False)),
            ("socket hang up", ("network_failed", False)),
            ("API key not valid", ("credential_missing", True)),
            ("INFO requestTimeout=30000", ("upstream_failed", False)),
            ("stack: at line 401", ("upstream_failed", False)),
        ):
            for classify in (acp_adapter.classify_error, opencode_adapter.classify,
                             lambda text: claude_adapter.classify(1, text)):
                with self.subTest(diagnostic=diagnostic, classify=classify):
                    self.assertEqual(classify(diagnostic), expected)

    def test_documented_reason_codes_and_security_contract(self):
        for name in ("guide.md", "guide.zh-CN.md"):
            doc = (ROOT / "docs" / name).read_text()
            for reason in ("invalid_protocol", "output_too_large", "input_limit", "adapter_io_failed", "cli_unavailable"):
                self.assertIn(f"| `{reason}` |", doc)
            for token in ("allow_once", "rawInput", "locations", "-32000", "-32602"):
                self.assertIn(token, doc)
            self.assertNotIn("not numeric RPC codes", doc)
        self.assertIn("external plugin\nisolation is **unverified**", (ROOT / "docs/guide.md").read_text())
        self.assertIn("rw permits arbitrary commands as the current user", (ROOT / "docs/guide.md").read_text())
        self.assertIn("以当前用户身份执行任意命令", (ROOT / "docs/guide.zh-CN.md").read_text())

    def test_success_cleans_up_descendants(self):
        self.assertEqual(self.run_peer("descendant")["response"], "PONG")
        self.assert_child_stopped()

    def test_sigterm_cancels_and_records_interruption(self):
        proc = subprocess.Popen([sys.executable, "-B", str(ROOT / "adapters/acp_adapter.py")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env={**self.env, "FAKE_ACP_SCENARIO": "interrupt"})
        try:
            proc.stdin.write(json.dumps(self.request()))
            proc.stdin.close()
            proc.stdin = None
            end = time.monotonic() + 3
            while not Path(str(self.trace) + ".pid").exists() and time.monotonic() < end:
                time.sleep(0.02)
            self.assertTrue(Path(str(self.trace) + ".pid").exists())
            proc.send_signal(signal.SIGTERM)
            stdout, _ = proc.communicate(timeout=5)
            self.assertEqual(json.loads(stdout)["reason"], "interrupted")
            self.assertTrue(any(m.get("method") == "session/cancel" for m in self.messages()))
            self.assert_child_stopped()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_invalid_request_before_spawn(self):
        for extra in ({"options": {}}, {"options": {"agent": "unknown"}}, {"timeout": True},
                      {"timeout": float("nan")}, {"protocol_version": True},
                      {"access": "bad"}, {"access": "ro", "workdir": None},
                      {"workdir": str(self.work)}, {"model": []}):
            with self.subTest(extra=extra):
                self.assertEqual(self.run_peer(**extra)["reason"], "invalid_request")
                self.assertFalse(self.trace.exists())

    def test_command_table(self):
        self.assertEqual(acp_adapter.COMMANDS, {
            "opencode": ["opencode", "acp"], "qwen": ["qwen", "--acp"],
            "agy": ["agy", "acp"], "gemini": ["gemini", "--acp"],
            "cursor": ["agent", "acp"], "grok": ["grok", "agent", "stdio"],
            "kiro": ["kiro-cli", "acp"]})


if __name__ == "__main__":
    unittest.main()
