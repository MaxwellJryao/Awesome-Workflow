"""Safe, asynchronous wrapper around ``codex exec --json``.

The bridge intentionally uses Codex's stable non-interactive CLI instead of the
experimental app-server protocol. Each Slack message is one Codex turn; the
thread id emitted by Codex is persisted by the caller and explicitly resumed on
the next turn.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import signal
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

JsonObject = dict[str, Any]
EventCallback = Callable[[JsonObject], Awaitable[None]]

_SECRET_PATTERNS = (
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]+"),
    re.compile(r"\bxapp-[A-Za-z0-9-]+"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}"),
)


def redact_text(text: str, limit: int | None = None) -> str:
    """Remove common credential shapes before text can reach Slack or logs."""

    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    if limit is not None and len(redacted) > limit:
        return redacted[:limit].rstrip() + "…"
    return redacted


def child_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build the Codex environment without broker credentials.

    Codex needs the normal user environment for PATH, authentication, cluster
    tooling, and project-specific variables. Slack credentials are never needed
    by Codex or commands it launches, so all SLACK_* variables are removed.
    """

    env = dict(source if source is not None else os.environ)
    for key in tuple(env):
        if key.startswith("SLACK_") or key == "BRIDGE_ENV_FILE":
            env.pop(key, None)
    return env


@dataclass(frozen=True, slots=True)
class CodexRunnerConfig:
    codex_bin: str
    work_dir: Path
    sandbox: str = "workspace-write"
    model: str | None = None
    profile: str | None = None
    reasoning_effort: str | None = None
    service_tier: str | None = None
    fast_mode: bool | None = None
    add_dirs: tuple[Path, ...] = ()
    skip_git_repo_check: bool = False
    strict_config: bool = False
    timeout_seconds: float | None = 3600
    interrupt_grace_seconds: float = 8

    def __post_init__(self) -> None:
        if self.sandbox not in {"read-only", "workspace-write", "danger-full-access"}:
            raise ValueError(f"unsupported Codex sandbox: {self.sandbox}")
        if self.timeout_seconds is not None and (
            not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be positive or None")
        if not math.isfinite(self.interrupt_grace_seconds) or self.interrupt_grace_seconds <= 0:
            raise ValueError("interrupt_grace_seconds must be positive")


@dataclass(frozen=True, slots=True)
class CodexRunResult:
    thread_id: str
    final_response: str
    usage: dict[str, int] = field(default_factory=dict)
    event_count: int = 0


class CodexRunError(RuntimeError):
    """A Codex turn failed before producing a valid completed response."""

    def __init__(self, message: str, *, returncode: int | None = None) -> None:
        super().__init__(redact_text(message, 2000))
        self.returncode = returncode


class CodexTurnCancelled(CodexRunError):
    """The active turn was interrupted through ``cancel_current``."""


class CodexTurnTimedOut(CodexRunError):
    """The active turn exceeded its configured wall-clock timeout."""


class CodexRunner:
    """Run at most one Codex turn at a time and expose progress events."""

    def __init__(self, config: CodexRunnerConfig) -> None:
        self.config = config
        self._run_lock = asyncio.Lock()
        self._active_process: asyncio.subprocess.Process | None = None
        self._cancel_requested = False

    @property
    def is_running(self) -> bool:
        process = self._active_process
        return process is not None and process.returncode is None

    def build_command(self, session_id: str | None) -> list[str]:
        """Return argv for a new or resumed Codex turn (never a shell string)."""

        command = [
            self.config.codex_bin,
            "--cd",
            str(self.config.work_dir),
            "--sandbox",
            self.config.sandbox,
            "--ask-for-approval",
            "never",
        ]
        if self.config.model:
            command.extend(("--model", self.config.model))
        if self.config.profile:
            command.extend(("--profile", self.config.profile))
        if self.config.reasoning_effort:
            value = json.dumps(self.config.reasoning_effort)
            command.extend(("--config", f"model_reasoning_effort={value}"))
        if self.config.service_tier:
            value = json.dumps(self.config.service_tier)
            command.extend(("--config", f"service_tier={value}"))
        if self.config.fast_mode is not None:
            enabled = "true" if self.config.fast_mode else "false"
            command.extend(("--config", f"features.fast_mode={enabled}"))
        for directory in self.config.add_dirs:
            command.extend(("--add-dir", str(directory)))
        if self.config.strict_config:
            command.append("--strict-config")

        command.append("exec")
        if session_id:
            command.extend(("resume", "--json"))
            if self.config.skip_git_repo_check:
                command.append("--skip-git-repo-check")
            command.extend((session_id, "-"))
        else:
            command.extend(("--json", "--color", "never"))
            if self.config.skip_git_repo_check:
                command.append("--skip-git-repo-check")
            command.append("-")
        return command

    async def run(
        self,
        prompt: str,
        *,
        session_id: str | None,
        on_event: EventCallback | None = None,
    ) -> CodexRunResult:
        """Execute one turn, parsing stdout as JSONL and stderr separately."""

        async with self._run_lock:
            return await self._run_locked(prompt, session_id=session_id, on_event=on_event)

    async def _run_locked(
        self,
        prompt: str,
        *,
        session_id: str | None,
        on_event: EventCallback | None,
    ) -> CodexRunResult:
        command = self.build_command(session_id)
        LOGGER.info("starting Codex turn (resume=%s)", bool(session_id))
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=child_environment(),
                # A dedicated process group lets !stop reach Codex and any
                # command it spawned without detaching a full login session.
                process_group=0,
                limit=64 * 1024 * 1024,
            )
        except OSError as exc:
            raise CodexRunError(f"无法启动 Codex：{exc}") from exc

        self._active_process = process
        self._cancel_requested = False
        stderr_task = asyncio.create_task(self._collect_stderr(process.stderr))

        state: dict[str, Any] = {
            "thread_id": session_id,
            "final_response": "",
            "usage": {},
            "event_count": 0,
            "turn_completed": False,
            "failures": [],
        }

        async def execute_turn() -> int:
            assert process.stdin is not None
            try:
                process.stdin.write(prompt.encode("utf-8"))
                await process.stdin.drain()
            except (BrokenPipeError, ConnectionResetError):
                # Argument/config/auth preflight can fail before Codex reads
                # stdin. Continue draining stdout/stderr to report that cause.
                pass
            finally:
                process.stdin.close()
                try:
                    await process.stdin.wait_closed()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            await self._consume_stdout(process.stdout, state, on_event)
            return await _await_returncode(process)

        try:
            if self.config.timeout_seconds is None:
                returncode = await execute_turn()
            else:
                try:
                    async with asyncio.timeout(self.config.timeout_seconds):
                        returncode = await execute_turn()
                except TimeoutError as exc:
                    await self._stop_process(process)
                    raise CodexTurnTimedOut(
                        f"Codex 本轮超过 {self.config.timeout_seconds:g} 秒，已中止。"
                    ) from exc
        except asyncio.CancelledError:
            await self._stop_process(process)
            raise
        finally:
            if process.returncode is None:
                await self._stop_process(process)
            stderr = await stderr_task
            self._active_process = None

        if self._cancel_requested:
            raise CodexTurnCancelled("Codex 本轮已按请求停止。", returncode=returncode)

        failures = [str(item) for item in state["failures"] if item]
        if returncode != 0 or failures or not state["turn_completed"]:
            details = "；".join(failures)
            if not details:
                details = stderr.strip() or "Codex 未返回完整的 turn.completed 事件"
            raise CodexRunError(details, returncode=returncode)

        thread_id = str(state["thread_id"] or "")
        if not thread_id:
            raise CodexRunError("Codex 完成了本轮，但没有返回 thread id。")
        return CodexRunResult(
            thread_id=thread_id,
            final_response=str(state["final_response"]),
            usage=dict(state["usage"]),
            event_count=int(state["event_count"]),
        )

    async def _consume_stdout(
        self,
        stream: asyncio.StreamReader | None,
        state: dict[str, Any],
        on_event: EventCallback | None,
    ) -> None:
        if stream is None:
            return
        try:
            async for raw_line in stream:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    LOGGER.warning("ignoring non-JSON Codex stdout: %s", redact_text(line, 300))
                    continue
                if not isinstance(event, dict):
                    continue

                state["event_count"] += 1
                event_type = event.get("type")
                if event_type == "thread.started" and event.get("thread_id"):
                    state["thread_id"] = str(event["thread_id"])
                elif event_type == "item.completed":
                    item = event.get("item") or {}
                    if item.get("type") == "agent_message" and isinstance(item.get("text"), str):
                        state["final_response"] = item["text"]
                elif event_type == "turn.completed":
                    state["turn_completed"] = True
                    usage = event.get("usage")
                    if isinstance(usage, dict):
                        state["usage"] = {
                            str(key): int(value)
                            for key, value in usage.items()
                            if isinstance(value, int)
                        }
                elif event_type in {"turn.failed", "error"}:
                    state["failures"].append(_event_error(event))

                if on_event is not None:
                    await on_event(event)
        except ValueError as exc:
            raise CodexRunError("Codex JSONL 单个事件超过 64 MiB，已停止本轮。") from exc

    @staticmethod
    async def _collect_stderr(stream: asyncio.StreamReader | None) -> str:
        if stream is None:
            return ""
        tail = ""
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace")
            LOGGER.debug("codex stderr: %s", redact_text(text.rstrip(), 1000))
            tail = (tail + text)[-16_000:]
        return redact_text(tail)

    async def cancel_current(self) -> bool:
        """Interrupt the active process group; return False when idle."""

        process = self._active_process
        if process is None or process.returncode is not None:
            return False
        self._cancel_requested = True
        await self._stop_process(process)
        return True

    async def _stop_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        self._signal_process_group(process, signal.SIGINT)
        if await _wait_for_exit(process, self.config.interrupt_grace_seconds):
            return
        self._signal_process_group(process, signal.SIGTERM)
        if await _wait_for_exit(process, 3):
            return
        self._signal_process_group(process, signal.SIGKILL)
        await _wait_for_exit(process, 5)

    @staticmethod
    def _signal_process_group(process: asyncio.subprocess.Process, sig: signal.Signals) -> None:
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        except OSError:
            try:
                process.send_signal(sig)
            except ProcessLookupError:
                pass


async def _await_returncode(process: asyncio.subprocess.Process) -> int:
    # ``Process.wait()`` can miss a very-fast interpreted child exit with some
    # asyncio child watchers. The transport always updates ``returncode``; a
    # short cooperative poll avoids that lost-wakeup edge case.
    while process.returncode is None:
        await asyncio.sleep(0.02)
    return process.returncode


async def _wait_for_exit(process: asyncio.subprocess.Process, timeout: float) -> bool:
    try:
        await asyncio.wait_for(_await_returncode(process), timeout)
        return True
    except TimeoutError:
        return False


def _event_error(event: JsonObject) -> str:
    for key in ("message", "error"):
        value = event.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for nested_key in ("message", "detail", "code"):
                if value.get(nested_key):
                    return str(value[nested_key])
    return str(event.get("type") or "Codex turn failed")


def describe_progress(event: JsonObject, *, show_details: bool = False) -> str | None:
    """Convert a machine-readable item event to a safe Slack status label."""

    if event.get("type") not in {"item.started", "item.completed"}:
        return None
    item = event.get("item")
    if not isinstance(item, dict):
        return None
    item_type = str(item.get("type") or "")
    labels = {
        "command_execution": "运行命令",
        "file_change": "修改文件",
        "mcp_tool_call": "调用 MCP 工具",
        "web_search": "搜索网页",
        "plan_update": "更新计划",
        "todo_list": "更新计划",
        "collab_tool_call": "运行协作任务",
        "image_generation": "生成图片",
    }
    label = labels.get(item_type)
    if label is None:
        if item_type in {"agent_message", "reasoning", ""}:
            return None
        label = f"执行 {item_type.replace('_', ' ')}"
    if not show_details:
        return label

    detail = _item_detail(item_type, item)
    if not detail:
        return label
    detail = redact_text(detail.replace("`", "'").replace("\n", " "), 160)
    return f"{label} · `{detail}`"


def _item_detail(item_type: str, item: JsonObject) -> str:
    if item_type == "command_execution":
        return str(item.get("command") or "")
    if item_type == "web_search":
        return str(item.get("query") or "")
    if item_type == "mcp_tool_call":
        server = item.get("server") or item.get("server_name") or ""
        tool = item.get("tool") or item.get("tool_name") or item.get("name") or ""
        return "/".join(str(part) for part in (server, tool) if part)
    if item_type == "file_change":
        for key in ("path", "file_path"):
            if item.get(key):
                return str(item[key])
        changes = item.get("changes")
        if isinstance(changes, list):
            paths = [str(change.get("path")) for change in changes if isinstance(change, dict)]
            return ", ".join(path for path in paths if path)
    return str(item.get("name") or item.get("description") or "")
