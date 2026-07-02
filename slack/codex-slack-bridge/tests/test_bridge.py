from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from slack_sdk.errors import SlackApiError
from slack_sdk.web.slack_response import SlackResponse

from bridge import CodexSlackBridge, SlackMessenger
from bridge_core import BridgeConfig
from codex_runner import CodexRunResult

THREAD_ID = "0199a213-81c0-7800-8aa1-bbab2a035a53"
THREAD_ID_2 = "0299a213-81c0-7800-8aa1-bbab2a035a54"


class FakeMessenger:
    def __init__(self) -> None:
        self.posts: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []

    async def post(
        self,
        channel: str,
        text: str,
        *,
        thread_ts: str | None = None,
        durable_path: Path | None = None,
    ) -> list[str]:
        self.posts.append(
            {
                "channel": channel,
                "text": text,
                "thread_ts": thread_ts,
                "durable_path": durable_path,
            }
        )
        return [f"status-{len(self.posts)}"]

    async def update(self, channel: str, timestamp: str, text: str) -> None:
        self.updates.append({"channel": channel, "timestamp": timestamp, "text": text})


class FlakyStartMessenger(FakeMessenger):
    def __init__(self) -> None:
        super().__init__()
        self.failed_once = False

    async def post(
        self,
        channel: str,
        text: str,
        *,
        thread_ts: str | None = None,
        durable_path: Path | None = None,
    ) -> list[str]:
        if text == "⏳ Codex 正在处理…" and not self.failed_once:
            self.failed_once = True
            raise ValueError("temporary Slack outage")
        return await super().post(channel, text, thread_ts=thread_ts, durable_path=durable_path)


class BlockingStartMessenger(FakeMessenger):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def post(
        self,
        channel: str,
        text: str,
        *,
        thread_ts: str | None = None,
        durable_path: Path | None = None,
    ) -> list[str]:
        self.started.set()
        await asyncio.Future()
        raise AssertionError("unreachable")


class PermanentFailureMessenger(FakeMessenger):
    def __init__(self) -> None:
        super().__init__()
        self.response = SlackResponse(
            client=None,
            http_verb="POST",
            api_url="https://slack.test",
            req_args={},
            data={"ok": False, "error": "invalid_auth"},
            headers={},
            status_code=200,
        )

    async def post(
        self,
        channel: str,
        text: str,
        *,
        thread_ts: str | None = None,
        durable_path: Path | None = None,
    ) -> list[str]:
        raise SlackApiError("invalid auth", self.response)


class FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.is_running = False
        self.new_thread_count = 0

    async def run(self, prompt: str, *, session_id: str | None, on_event: Any) -> Any:
        self.calls.append((prompt, session_id))
        self.is_running = True
        if session_id is None:
            thread_ids = (THREAD_ID, THREAD_ID_2)
            thread_id = thread_ids[min(self.new_thread_count, len(thread_ids) - 1)]
            self.new_thread_count += 1
        else:
            thread_id = session_id
        await on_event({"type": "thread.started", "thread_id": thread_id})
        await on_event(
            {
                "type": "item.started",
                "item": {"id": "cmd-1", "type": "command_execution", "command": "pwd"},
            }
        )
        self.is_running = False
        return CodexRunResult(
            thread_id=thread_id,
            final_response="done xoxb-secret-value",
            usage={"input_tokens": 2, "output_tokens": 1},
        )

    async def cancel_current(self) -> bool:
        return False


def bridge_config(tmp_path: Path) -> BridgeConfig:
    return BridgeConfig(
        bot_token="xoxb-test-token",
        app_token="xapp-test-token",
        allowed_user_ids=frozenset({"U1"}),
        work_dir=tmp_path,
        state_dir=tmp_path / "state",
        reply_in_thread=True,
        progress_update_interval=0.01,
    )


@pytest.mark.asyncio
async def test_message_routes_to_worker_once_and_uses_root_thread(tmp_path: Path) -> None:
    bridge = CodexSlackBridge(bridge_config(tmp_path))
    messenger = FakeMessenger()
    runner = FakeRunner()
    bridge.messenger = messenger  # type: ignore[assignment]
    bridge.runner = runner  # type: ignore[assignment]
    event = {
        "channel_type": "im",
        "channel": "D1",
        "user": "U1",
        "text": "do it",
        "ts": "2.0",
        "thread_ts": "1.0",
        "client_msg_id": "event-1",
    }

    await bridge.on_message(event)
    await bridge.on_message(event)
    assert bridge.queue.qsize() == 1

    worker = asyncio.create_task(bridge.worker())
    await asyncio.wait_for(bridge.queue.join(), 2)
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)

    assert runner.calls == [("do it", None)]
    assert bridge.current_session_id == THREAD_ID
    assert all(post["thread_ts"] == "1.0" for post in messenger.posts)
    assert messenger.posts[0]["text"] == "⏳ Codex 正在处理…"
    assert messenger.posts[-1]["text"] == "done [REDACTED]"
    assert messenger.updates[-1]["text"].startswith("✅ 完成")
    assert bridge.deduplicator.recover() == ([], [])
    bridge.deduplicator.close()


@pytest.mark.asyncio
async def test_initial_status_retry_preserves_fifo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bridge = CodexSlackBridge(bridge_config(tmp_path))
    messenger = FlakyStartMessenger()
    runner = FakeRunner()
    bridge.messenger = messenger  # type: ignore[assignment]
    bridge.runner = runner  # type: ignore[assignment]

    async def no_delay(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", no_delay)
    base = {"channel_type": "im", "channel": "D1", "user": "U1"}
    await bridge.on_message({**base, "text": "first", "ts": "1.0", "client_msg_id": "event-a"})
    await bridge.on_message({**base, "text": "second", "ts": "2.0", "client_msg_id": "event-b"})

    worker = asyncio.create_task(bridge.worker())
    await asyncio.wait_for(bridge.queue.join(), 2)
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)

    assert [prompt for prompt, _session in runner.calls] == ["first", "second"]
    assert bridge.deduplicator.recover() == ([], [])
    bridge.deduplicator.close()


@pytest.mark.asyncio
async def test_slack_threads_keep_independent_codex_sessions(tmp_path: Path) -> None:
    bridge = CodexSlackBridge(bridge_config(tmp_path))
    messenger = FakeMessenger()
    runner = FakeRunner()
    bridge.messenger = messenger  # type: ignore[assignment]
    bridge.runner = runner  # type: ignore[assignment]
    base = {"channel_type": "im", "channel": "D1", "user": "U1"}
    events = [
        {**base, "text": "thread one", "thread_ts": "1.0", "client_msg_id": "m1"},
        {**base, "text": "thread two", "thread_ts": "2.0", "client_msg_id": "m2"},
        {**base, "text": "thread one again", "thread_ts": "1.0", "client_msg_id": "m3"},
    ]
    for event in events:
        await bridge.on_message(event)

    worker = asyncio.create_task(bridge.worker())
    await asyncio.wait_for(bridge.queue.join(), 2)
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)

    assert runner.calls == [
        ("thread one", None),
        ("thread two", None),
        ("thread one again", THREAD_ID),
    ]
    assert bridge.session_store.get("1.0") == THREAD_ID
    assert bridge.session_store.get("2.0") == THREAD_ID_2
    assert {post["thread_ts"] for post in messenger.posts} == {"1.0", "2.0"}
    bridge.deduplicator.close()


@pytest.mark.asyncio
async def test_new_only_resets_current_slack_thread(tmp_path: Path) -> None:
    bridge = CodexSlackBridge(bridge_config(tmp_path))
    bridge.messenger = FakeMessenger()  # type: ignore[assignment]
    bridge.runner = FakeRunner()  # type: ignore[assignment]
    bridge.session_store.save("1.0", THREAD_ID)
    bridge.session_store.save("2.0", THREAD_ID_2)
    await bridge.on_message(
        {
            "channel_type": "im",
            "channel": "D1",
            "user": "U1",
            "text": "!new",
            "thread_ts": "1.0",
            "client_msg_id": "reset-1",
        }
    )

    worker = asyncio.create_task(bridge.worker())
    await asyncio.wait_for(bridge.queue.join(), 2)
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)

    assert bridge.session_store.get("1.0") is None
    assert bridge.session_store.get("2.0") == THREAD_ID_2
    bridge.deduplicator.close()


@pytest.mark.asyncio
async def test_file_share_paths_are_added_to_persisted_prompt(tmp_path: Path) -> None:
    bridge = CodexSlackBridge(bridge_config(tmp_path))
    bridge.messenger = FakeMessenger()  # type: ignore[assignment]

    async def fake_download(
        _files: list[dict[str, Any]],
    ) -> tuple[list[str], list[str]]:
        return ["/private/uploads/F1-notes.txt"], []

    bridge._download_files = fake_download  # type: ignore[method-assign]
    await bridge.on_message(
        {
            "channel_type": "im",
            "subtype": "file_share",
            "channel": "D1",
            "user": "U1",
            "text": "",
            "files": [{"id": "F1", "name": "notes.txt"}],
            "thread_ts": "3.0",
            "client_msg_id": "file-1",
        }
    )

    message = bridge.queue.get_nowait()
    assert message.session_key == "3.0"
    assert "/private/uploads/F1-notes.txt" in message.text
    pending, _interrupted = bridge.deduplicator.recover()
    assert pending[0].session_key == "3.0"
    bridge.queue.task_done()
    bridge.deduplicator.mark_done(message.event_id)
    bridge.deduplicator.close()


@pytest.mark.asyncio
async def test_bridge_stop_drains_persisted_queue(tmp_path: Path) -> None:
    bridge = CodexSlackBridge(bridge_config(tmp_path))
    bridge.messenger = FakeMessenger()  # type: ignore[assignment]
    runner = FakeRunner()
    bridge.runner = runner  # type: ignore[assignment]
    base = {"channel_type": "im", "channel": "D1", "user": "U1"}
    await bridge.on_message({**base, "text": "one", "client_msg_id": "q1"})
    await bridge.on_message({**base, "text": "two", "client_msg_id": "q2"})
    acked = False
    responses: list[str] = []

    async def ack() -> None:
        nonlocal acked
        acked = True

    async def respond(text: str) -> None:
        responses.append(text)

    await bridge.on_bridge_command(ack, {"user_id": "U1", "text": "stop"}, respond)
    assert acked
    assert bridge.queue.empty()
    assert "2 条" in responses[0]
    assert bridge.deduplicator.recover() == ([], [])
    bridge.deduplicator.close()


@pytest.mark.asyncio
async def test_shutdown_during_initial_status_keeps_message_pending(tmp_path: Path) -> None:
    bridge = CodexSlackBridge(bridge_config(tmp_path))
    messenger = BlockingStartMessenger()
    runner = FakeRunner()
    bridge.messenger = messenger  # type: ignore[assignment]
    bridge.runner = runner  # type: ignore[assignment]
    await bridge.on_message(
        {
            "channel_type": "im",
            "channel": "D1",
            "user": "U1",
            "text": "do not lose me",
            "ts": "1.0",
            "client_msg_id": "event-pending",
        }
    )

    worker = asyncio.create_task(bridge.worker())
    await asyncio.wait_for(messenger.started.wait(), 1)
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)

    pending, interrupted = bridge.deduplicator.recover()
    assert [message.event_id for message in pending] == ["event-pending"]
    assert interrupted == []
    assert runner.calls == []
    bridge.deduplicator.close()


@pytest.mark.asyncio
async def test_permanent_slack_error_dead_letters_prompt_without_blocking(
    tmp_path: Path,
) -> None:
    bridge = CodexSlackBridge(bridge_config(tmp_path))
    bridge.messenger = PermanentFailureMessenger()  # type: ignore[assignment]
    runner = FakeRunner()
    bridge.runner = runner  # type: ignore[assignment]
    await bridge.on_message(
        {
            "channel_type": "im",
            "channel": "D1",
            "user": "U1",
            "text": "preserve this prompt",
            "ts": "1.0",
            "client_msg_id": "event-terminal",
        }
    )

    worker = asyncio.create_task(bridge.worker())
    await asyncio.wait_for(bridge.queue.join(), 1)
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)

    rows = [
        json.loads(line)
        for line in (tmp_path / "state" / "undelivered.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert rows[-1]["kind"] == "prompt_not_started"
    assert rows[-1]["text"] == "preserve this prompt"
    assert runner.calls == []
    assert bridge.deduplicator.recover() == ([], [])
    bridge.deduplicator.close()


class FailingClient:
    async def chat_postMessage(self, **_kwargs: Any) -> Any:
        raise ValueError("offline")


class FailingApp:
    client = FailingClient()


@pytest.mark.asyncio
async def test_multichunk_delivery_failure_persists_every_remaining_part(
    tmp_path: Path,
) -> None:
    messenger = SlackMessenger(FailingApp(), max_chunk=5)  # type: ignore[arg-type]
    outbox = tmp_path / "outbox.jsonl"
    with pytest.raises(ValueError, match="offline"):
        await messenger.post("D1", "abcdefghijk", durable_path=outbox)
    rows = [json.loads(line) for line in outbox.read_text(encoding="utf-8").splitlines()]
    assert [row["text"] for row in rows] == ["abcde", "fghij", "k"]
    assert [row["part"] for row in rows] == [1, 2, 3]


class BlockingSecondChunkClient:
    def __init__(self) -> None:
        self.calls = 0
        self.blocked = asyncio.Event()

    async def chat_postMessage(self, **_kwargs: Any) -> dict[str, str]:
        self.calls += 1
        if self.calls == 1:
            return {"ts": "1.0"}
        self.blocked.set()
        await asyncio.Future()
        raise AssertionError("unreachable")


class BlockingSecondChunkApp:
    def __init__(self) -> None:
        self.client = BlockingSecondChunkClient()


@pytest.mark.asyncio
async def test_shutdown_during_final_delivery_persists_unsent_tail(tmp_path: Path) -> None:
    app = BlockingSecondChunkApp()
    messenger = SlackMessenger(app, max_chunk=5)  # type: ignore[arg-type]
    outbox = tmp_path / "outbox.jsonl"
    task = asyncio.create_task(messenger.post("D1", "abcdefghijk", durable_path=outbox))
    await asyncio.wait_for(app.client.blocked.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    rows = [json.loads(line) for line in outbox.read_text(encoding="utf-8").splitlines()]
    assert [row["text"] for row in rows] == ["fghij", "k"]
    assert all("cancelled" in row["error"] for row in rows)


@pytest.mark.asyncio
async def test_rate_limit_is_retried() -> None:
    calls = 0
    response = SlackResponse(
        client=None,
        http_verb="POST",
        api_url="https://slack.test",
        req_args={},
        data={"ok": False, "error": "ratelimited"},
        headers={"Retry-After": "0"},
        status_code=429,
    )

    async def flaky(**_kwargs: Any) -> str:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise SlackApiError("rate limited", response)
        return "ok"

    assert await SlackMessenger._retry(flaky) == "ok"
    assert calls == 3
