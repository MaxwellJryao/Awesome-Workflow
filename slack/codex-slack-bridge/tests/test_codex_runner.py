from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest

from codex_runner import (
    CodexRunError,
    CodexRunner,
    CodexRunnerConfig,
    CodexTurnCancelled,
    CodexTurnTimedOut,
    child_environment,
    describe_progress,
    redact_text,
)

THREAD_ID = "0199a213-81c0-7800-8aa1-bbab2a035a53"


def make_fake_codex(tmp_path: Path) -> Path:
    path = tmp_path / "fake-codex"
    path.write_text(
        """#!/usr/bin/python3
import json
import os
import subprocess
import sys
import time

prompt = sys.stdin.read()
capture = os.environ.get("CAPTURE_FILE")
if capture:
    with open(capture, "w", encoding="utf-8") as file:
        json.dump({"argv": sys.argv[1:], "stdin": prompt}, file)

mode = os.environ.get("FAKE_CODEX_MODE", "success")
if mode == "sleep":
    time.sleep(30)
if mode == "child":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    with open(os.environ["GRANDCHILD_PID_FILE"], "w", encoding="utf-8") as file:
        file.write(str(child.pid))
    time.sleep(30)
if mode == "fail":
    print("authentication failed for sk-super-secret-value", file=sys.stderr)
    raise SystemExit(7)

thread_id = "0199a213-81c0-7800-8aa1-bbab2a035a53"
print(json.dumps({"type": "thread.started", "thread_id": thread_id}), flush=True)
print(json.dumps({"type": "turn.started"}), flush=True)
print(json.dumps({
    "type": "item.started",
    "item": {"id": "cmd-1", "type": "command_execution", "command": "pwd"},
}), flush=True)
secret_present = any(key.startswith("SLACK_") for key in os.environ)
answer = f"answer:{prompt}:slack_secret={secret_present}"
print(json.dumps({
    "type": "item.completed",
    "item": {"id": "msg-1", "type": "agent_message", "text": answer},
}), flush=True)
print(json.dumps({
    "type": "turn.completed",
    "usage": {"input_tokens": 12, "cached_input_tokens": 2, "output_tokens": 3},
}), flush=True)
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def runner_config(tmp_path: Path, fake: Path, **kwargs: object) -> CodexRunnerConfig:
    defaults: dict[str, object] = {
        "codex_bin": str(fake),
        "work_dir": tmp_path,
        "sandbox": "workspace-write",
        "timeout_seconds": 5,
        "interrupt_grace_seconds": 0.1,
    }
    defaults.update(kwargs)
    return CodexRunnerConfig(**defaults)  # type: ignore[arg-type]


def test_build_command_uses_global_safety_flags_and_stdin(tmp_path: Path) -> None:
    runner = CodexRunner(
        CodexRunnerConfig(
            codex_bin="codex",
            work_dir=tmp_path,
            model="gpt-test",
            reasoning_effort="high",
            service_tier="fast",
            fast_mode=True,
        )
    )
    new = runner.build_command(None)
    resumed = runner.build_command(THREAD_ID)
    assert new[-1] == "-"
    assert resumed[-2:] == [THREAD_ID, "-"]
    assert new.index("--ask-for-approval") < new.index("exec")
    assert new[new.index("--ask-for-approval") + 1] == "never"
    assert new[new.index("--sandbox") + 1] == "workspace-write"
    assert "resume" not in new
    assert "resume" in resumed
    assert 'model_reasoning_effort="high"' in new
    assert 'service_tier="fast"' in new
    assert "features.fast_mode=true" in new


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_runner_config_rejects_non_finite_timeouts(tmp_path: Path, value: float) -> None:
    with pytest.raises(ValueError):
        CodexRunnerConfig(codex_bin="codex", work_dir=tmp_path, timeout_seconds=value)
    with pytest.raises(ValueError):
        CodexRunnerConfig(codex_bin="codex", work_dir=tmp_path, interrupt_grace_seconds=value)


def test_child_environment_strips_slack_secrets_only() -> None:
    env = child_environment(
        {
            "PATH": "/bin",
            "SLACK_BOT_TOKEN": "xoxb-secret",
            "SLACK_APP_TOKEN": "xapp-secret",
            "CODEX_API_KEY": "needed-by-codex",
        }
    )
    assert env == {"PATH": "/bin", "CODEX_API_KEY": "needed-by-codex"}


@pytest.mark.asyncio
async def test_successful_jsonl_run_and_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = make_fake_codex(tmp_path)
    runner = CodexRunner(runner_config(tmp_path, fake))
    events: list[dict[str, object]] = []
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-must-not-leak")

    async def collect(event: dict[str, object]) -> None:
        events.append(event)

    result = await runner.run("hello", session_id=None, on_event=collect)  # type: ignore[arg-type]
    assert result.thread_id == THREAD_ID
    assert result.final_response == "answer:hello:slack_secret=False"
    assert result.usage["input_tokens"] == 12
    assert [event["type"] for event in events] == [
        "thread.started",
        "turn.started",
        "item.started",
        "item.completed",
        "turn.completed",
    ]


@pytest.mark.asyncio
async def test_prompt_is_stdin_and_resume_id_is_explicit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = make_fake_codex(tmp_path)
    capture = tmp_path / "capture.json"
    monkeypatch.setenv("CAPTURE_FILE", str(capture))
    runner = CodexRunner(runner_config(tmp_path, fake))
    await runner.run("-prompt with spaces", session_id=THREAD_ID)
    data = json.loads(capture.read_text(encoding="utf-8"))
    assert data["stdin"] == "-prompt with spaces"
    assert data["argv"][-3:] == ["--json", THREAD_ID, "-"]


@pytest.mark.asyncio
async def test_nonzero_preflight_uses_redacted_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = make_fake_codex(tmp_path)
    monkeypatch.setenv("FAKE_CODEX_MODE", "fail")
    runner = CodexRunner(runner_config(tmp_path, fake))
    with pytest.raises(CodexRunError) as caught:
        await runner.run("hello", session_id=None)
    assert caught.value.returncode == 7
    assert "sk-super-secret-value" not in str(caught.value)
    assert "[REDACTED]" in str(caught.value)


@pytest.mark.asyncio
async def test_timeout_stops_process_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = make_fake_codex(tmp_path)
    monkeypatch.setenv("FAKE_CODEX_MODE", "sleep")
    runner = CodexRunner(
        runner_config(tmp_path, fake, timeout_seconds=0.05, interrupt_grace_seconds=0.05)
    )
    with pytest.raises(CodexTurnTimedOut):
        await runner.run("hello", session_id=None)
    assert not runner.is_running


@pytest.mark.asyncio
async def test_cancel_current_marks_turn_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = make_fake_codex(tmp_path)
    monkeypatch.setenv("FAKE_CODEX_MODE", "sleep")
    runner = CodexRunner(runner_config(tmp_path, fake, timeout_seconds=10))
    task = asyncio.create_task(runner.run("hello", session_id=None))
    for _ in range(100):
        if runner.is_running:
            break
        await asyncio.sleep(0.005)
    assert await runner.cancel_current() is True
    with pytest.raises(CodexTurnCancelled):
        await task
    assert await runner.cancel_current() is False


@pytest.mark.asyncio
async def test_cancel_terminates_spawned_process_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = make_fake_codex(tmp_path)
    pid_file = tmp_path / "grandchild.pid"
    monkeypatch.setenv("FAKE_CODEX_MODE", "child")
    monkeypatch.setenv("GRANDCHILD_PID_FILE", str(pid_file))
    runner = CodexRunner(runner_config(tmp_path, fake, timeout_seconds=10))
    task = asyncio.create_task(runner.run("hello", session_id=None))
    for _ in range(200):
        if pid_file.exists():
            break
        await asyncio.sleep(0.005)
    grandchild_pid = int(pid_file.read_text(encoding="utf-8"))
    assert await runner.cancel_current() is True
    with pytest.raises(CodexTurnCancelled):
        await task

    for _ in range(200):
        stat_path = Path(f"/proc/{grandchild_pid}/stat")
        if not stat_path.exists() or stat_path.read_text().split()[2] == "Z":
            break
        await asyncio.sleep(0.005)
    else:
        os.kill(grandchild_pid, 0)
        pytest.fail("grandchild survived process-group cancellation")


def test_progress_description_is_optional_and_redacted() -> None:
    event = {
        "type": "item.started",
        "item": {
            "id": "1",
            "type": "command_execution",
            "command": "curl -H 'token: xoxb-secret-value' example.com",
        },
    }
    assert describe_progress(event) == "运行命令"
    detailed = describe_progress(event, show_details=True)
    assert detailed is not None
    assert "xoxb-secret-value" not in detailed
    assert "[REDACTED]" in detailed
    assert (
        describe_progress(
            {"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}
        )
        is None
    )


def test_redact_text_limits_output() -> None:
    output = redact_text("secret sk-abcdefghijklmnop and more text", 20)
    assert "sk-abcdefghijklmnop" not in output
    assert len(output) <= 21
