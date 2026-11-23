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
                                 ("cancelled", "interrupted")):
            with self.subTest(scenario=scenario):
                result = self.run_peer(scenario)
                self.assertEqual(result["reason"], reason)
                self.assertNotIn("sensitive diagnostic", json.dumps(result))
                if reason == "credential_missing":
                    self.assertEqual(result["missing_credential"], "opencode-login")

    def assert_child_stopped(self):
        pid = int(Path(str(self.trace) + ".pid").read_text())
        for _ in range(30):
            state = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
            if not state.stdout.strip() or state.stdout.lstrip().startswith("Z"):
                return
            time.sleep(0.02)
        self.fail(f"child process {pid} survived cleanup")

    def test_timeout_cancel_and_process_group_cleanup(self):
        started = time.monotonic()
        self.assertEqual(self.run_peer("timeout", timeout=0.7)["reason"], "timeout")
        self.assertLess(time.monotonic() - started, 4)
        self.assertTrue(any(m.get("method") == "session/cancel" for m in self.messages()))
        self.assert_child_stopped()

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
