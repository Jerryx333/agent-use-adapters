#!/usr/bin/env python3
"""Run the three opt-in ACP smoke checks and retain requests/results locally.
Run: python3 -B scripts/test_acp_live.py --output /tmp/acp-smoke
Requires installed engines with existing authentication; never installs or logs in.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run_case(root, engine, access, timeout, model, expect_qwen_payment_required=False):
    directory = root / (engine + "-" + access)
    directory.mkdir(parents=True, exist_ok=True)
    task = "只回复 PONG"
    workdir = None
    before = {}
    if access == "ro":
        workdir = directory / "work"
        workdir.mkdir(exist_ok=True)
        before = {"visible.txt": b"read-only sentinel\n", "protected.txt": b"unchanged\n"}
        for name, value in before.items():
            (workdir / name).write_bytes(value)
        task = ("使用只读文件工具列出工作目录的文件，再尝试把 protected.txt 改为 changed；"
                "写操作应被拒绝。不要使用 shell。回答实际文件名和写操作被阻止的结果。")
    task_file = directory / "task.txt"
    task_file.write_text(task, encoding="utf-8")
    output = directory / "output"
    request = {"protocol_version": 1, "task_file": str(task_file), "output": str(output),
               "options": {"agent": engine}, "access": access, "timeout": timeout,
               "workdir": str(workdir) if workdir else None, "model": model}
    (directory / "request.json").write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
    try:
        proc = subprocess.run([sys.executable, "-B", str(ROOT / "adapters/acp_adapter.py")],
            input=json.dumps(request), text=True, capture_output=True, timeout=timeout + 8)
        result = json.loads(proc.stdout)
        (directory / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        status = json.loads((output / "status.json").read_text(encoding="utf-8"))
        response = (output / "response.txt").read_text(encoding="utf-8")
        ok = proc.returncode == 0 and status.get("state") == "staged_for_review" and result.get("state") == "staged_for_review"
        if access == "none":
            ok = ok and "PONG" in response
        else:
            unchanged = {p.name: p.read_bytes() for p in workdir.iterdir() if p.is_file()} == before
            trace = output / "trajectory.jsonl"
            rows = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
            # Do not accept the model merely claiming a refusal: require an observed
            # rejected operation as well as an unchanged workspace and actual listing.
            denied = any(row.get("rejected") and (row.get("edit") or row.get("tool") == "execute") for row in rows)
            ok = ok and unchanged and all(name in response for name in before) and denied
        if engine == "opencode":
            # Built-ins observed in 1.19.6. Unknown commands (including user skills)
            # fail the smoke check; an absent notification is not proof of isolation.
            commands = status.get("available_commands")
            ok = ok and isinstance(commands, list) and set(commands) <= {"customize-opencode", "init", "review"}
        expected_reason = "payment_required" if engine == "qwen" and expect_qwen_payment_required else None
        if expected_reason:
            ok = (proc.returncode == 1 and result.get("state") == status.get("state") == "failed"
                  and result.get("reason") == status.get("reason") == expected_reason and response == "")
            if workdir:
                ok = ok and {p.name: p.read_bytes() for p in workdir.iterdir() if p.is_file()} == before
        summary = {"case": directory.name, "passed": bool(ok), "state": result.get("state"),
                   "reason": result.get("reason"), "expected_reason": expected_reason,
                   "available_commands": status.get("available_commands"), "output": str(output)}
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        summary = {"case": directory.name, "passed": False, "error_type": type(error).__name__}
    print(json.dumps(summary, ensure_ascii=False), flush=True)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--engine", choices=("all", "opencode", "qwen"), default="all")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--expect-qwen-payment-required", action="store_true",
                        help="Require payment_required for both Qwen cases instead of successful generation")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    root = args.output.resolve()
    root.mkdir(parents=True, exist_ok=True)
    config = json.loads((ROOT / "config/dispatcher.json").read_text())
    summaries = []
    for engine, access in (("opencode", "none"), ("qwen", "none"), ("qwen", "ro")):
        if args.engine not in ("all", engine):
            continue
        model = config["providers"][engine + "-acp"]["default_model"]
        summaries.append(run_case(root, engine, access, args.timeout, model, args.expect_qwen_payment_required))
    (root / "summary.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if all(s["passed"] for s in summaries) else 1


if __name__ == "__main__":
    sys.exit(main())
