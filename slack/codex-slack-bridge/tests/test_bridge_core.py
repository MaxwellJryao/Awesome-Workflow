from __future__ import annotations

import json
import os
import sqlite3
import stat
import time
from pathlib import Path

import pytest

from bridge_core import (
    AlreadyRunningError,
    BridgeConfig,
    ConfigurationError,
    InstanceLock,
    PersistentDeduplicator,
    SessionMapStore,
    SessionStore,
    append_undelivered,
    chunk_text,
    event_id,
    should_accept_event,
)


def valid_env(work_dir: Path, state_dir: Path) -> dict[str, str]:
    return {
        "SLACK_BOT_TOKEN": "xoxb-real-looking-token",
        "SLACK_APP_TOKEN": "xapp-real-looking-token",
        "SLACK_ALLOWED_USER_ID": "U123",
        "CODEX_CWD": str(work_dir),
        "BRIDGE_STATE_DIR": str(state_dir),
    }


def test_config_parses_safe_defaults_and_hides_tokens(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    config = BridgeConfig.from_env(valid_env(work_dir, tmp_path / "state"))
    assert config.allowed_user_ids == frozenset({"U123"})
    assert config.codex_sandbox == "workspace-write"
    assert config.codex_model == "gpt-5.6-sol"
    assert config.codex_reasoning_effort == "ultra"
    assert config.codex_service_tier == "fast"
    assert config.codex_fast_mode is True
    assert config.turn_timeout_seconds == 3600
    assert "xoxb-" not in repr(config)
    assert "xapp-" not in repr(config)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("SLACK_BOT_TOKEN", "xoxb-..."),
        ("SLACK_APP_TOKEN", "xapp-..."),
        ("CODEX_SANDBOX", "yolo"),
        ("CODEX_TURN_TIMEOUT_SECONDS", "later"),
        ("CODEX_SHOW_TOOL_DETAILS", "maybe"),
        ("CODEX_FAST_MODE", "maybe"),
        ("CODEX_TURN_TIMEOUT_SECONDS", "nan"),
        ("CODEX_INTERRUPT_GRACE_SECONDS", "inf"),
    ],
)
def test_config_rejects_invalid_values(tmp_path: Path, key: str, value: str) -> None:
    env = valid_env(tmp_path, tmp_path / "state")
    env[key] = value
    with pytest.raises(ConfigurationError):
        BridgeConfig.from_env(env)


def test_zero_timeout_means_unlimited(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    env = valid_env(work_dir, tmp_path / "state")
    env["CODEX_TURN_TIMEOUT_SECONDS"] = "0"
    assert BridgeConfig.from_env(env).turn_timeout_seconds is None


def test_config_rejects_multiple_users(tmp_path: Path) -> None:
    env = valid_env(tmp_path, tmp_path / "state")
    env["SLACK_ALLOWED_USER_ID"] = "U123,U456"
    with pytest.raises(ConfigurationError, match="一个"):
        BridgeConfig.from_env(env)


def test_config_blocks_writable_self_hosting_and_add_dirs(tmp_path: Path) -> None:
    target = tmp_path / "target"
    component = target / "bridge"
    component.mkdir(parents=True)
    env = valid_env(target, component / "state")
    with pytest.raises(ConfigurationError, match="可写范围"):
        BridgeConfig.from_env(env, component_dir=component)

    env["BRIDGE_ALLOW_UNSAFE_SELF_HOSTING"] = "true"
    assert BridgeConfig.from_env(env, component_dir=component).allow_unsafe_self_hosting

    outside_target = tmp_path / "outside-target"
    outside_target.mkdir()
    env = valid_env(outside_target, component / "state")
    env["CODEX_ADD_DIRS"] = str(tmp_path)
    with pytest.raises(ConfigurationError, match="可写范围"):
        BridgeConfig.from_env(env, component_dir=component)


def test_config_requires_opt_in_for_full_access(tmp_path: Path) -> None:
    target = tmp_path / "target"
    component = tmp_path / "bridge"
    target.mkdir()
    component.mkdir()
    env = valid_env(target, component / "state")
    env["CODEX_SANDBOX"] = "danger-full-access"
    with pytest.raises(ConfigurationError, match="可写范围"):
        BridgeConfig.from_env(env, component_dir=component)


def test_config_rejects_group_or_world_readable_env_file(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    component = tmp_path / "bridge"
    work_dir.mkdir()
    component.mkdir()
    env_file = component / ".env"
    env_file.write_text("SLACK_BOT_TOKEN=secret\n", encoding="utf-8")
    env_file.chmod(0o644)
    env = valid_env(work_dir, component / "state")
    env["BRIDGE_ENV_FILE"] = str(env_file)
    with pytest.raises(ConfigurationError, match="chmod 600"):
        BridgeConfig.from_env(env, component_dir=component)

    env_file.chmod(0o600)
    assert BridgeConfig.from_env(env, component_dir=component).work_dir == work_dir


def test_config_rejects_writable_codex_launcher(tmp_path: Path) -> None:
    target = tmp_path / "target"
    component = tmp_path / "bridge"
    target.mkdir()
    component.mkdir()
    launcher = target / "codex"
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o755)
    env = valid_env(target, component / "state")
    env["CODEX_BIN"] = str(launcher)
    with pytest.raises(ConfigurationError, match="可写范围"):
        BridgeConfig.from_env(env, component_dir=component)


def test_session_store_is_atomic_private_and_validates_uuid(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "session.txt"
    store = SessionStore(path)
    session_id = "0199a213-81c0-7800-8aa1-bbab2a035a53"
    store.save(session_id)
    assert store.load() == session_id
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.write_text("not-a-uuid", encoding="utf-8")
    assert store.load() is None
    assert not path.exists()


def test_session_map_store_crud_snapshot_and_private_atomic_file(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "sessions.json"
    store = SessionMapStore(path)
    first_id = "0199a213-81c0-7800-8aa1-bbab2a035a53"
    second_id = "0199a213-81c0-7800-8aa1-bbab2a035a54"

    assert store.get("thread-1") is None
    assert store.count() == 0
    store.save("thread-1", first_id)
    store.save("dm-D1", second_id.upper())

    assert store.get("thread-1") == first_id
    assert store.get("dm-D1") == second_id
    assert store.count() == 2
    snapshot = store.snapshot()
    assert snapshot == {"dm-D1": second_id, "thread-1": first_id}
    snapshot.clear()
    assert store.count() == 2
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(path.parent.glob(".*.tmp"))

    store.clear("thread-1")
    store.clear("missing")
    assert store.snapshot() == {"dm-D1": second_id}


def test_session_map_store_validates_and_repairs_persisted_values(tmp_path: Path) -> None:
    path = tmp_path / "sessions.json"
    session_id = "0199a213-81c0-7800-8aa1-bbab2a035a53"
    path.write_text(
        json.dumps({"valid": session_id.upper(), "bad": "not-a-uuid", "blank": 3}),
        encoding="utf-8",
    )
    store = SessionMapStore(path)

    assert store.snapshot() == {"valid": session_id}
    assert json.loads(path.read_text(encoding="utf-8")) == {"valid": session_id}
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(ValueError, match="session_key"):
        store.get("  ")
    with pytest.raises(ValueError):
        store.save("thread", "not-a-uuid")

    path.write_text("not json", encoding="utf-8")
    assert store.snapshot() == {}
    assert not path.exists()


def test_session_map_store_migrates_legacy_once_without_overwrite(tmp_path: Path) -> None:
    path = tmp_path / "sessions.json"
    legacy_path = tmp_path / "session_id.txt"
    store = SessionMapStore(path)
    legacy_id = "0199a213-81c0-7800-8aa1-bbab2a035a53"
    existing_id = "0199a213-81c0-7800-8aa1-bbab2a035a54"

    SessionStore(legacy_path).save(legacy_id)
    assert store.migrate_legacy(legacy_path, legacy_key="dm-D1") is True
    assert store.get("dm-D1") == legacy_id
    assert not legacy_path.exists()

    SessionStore(legacy_path).save(legacy_id)
    store.save("thread-2", existing_id)
    assert store.migrate_legacy(legacy_path, legacy_key="thread-2") is False
    assert store.get("thread-2") == existing_id
    assert not legacy_path.exists()
    assert store.migrate_legacy(legacy_path, legacy_key="missing") is False


def test_instance_lock_rejects_second_owner(tmp_path: Path) -> None:
    first = InstanceLock(tmp_path / "instance.lock")
    second = InstanceLock(tmp_path / "instance.lock")
    first.acquire()
    try:
        with pytest.raises(AlreadyRunningError):
            second.acquire()
    finally:
        first.release()


def test_persistent_dedup_survives_reopen_and_expires(tmp_path: Path) -> None:
    path = tmp_path / "events.sqlite3"
    dedup = PersistentDeduplicator(path, ttl_seconds=60, max_entries=100)
    assert dedup.claim("event-1") is True
    assert dedup.claim("event-1") is False
    dedup.close()

    reopened = PersistentDeduplicator(path, ttl_seconds=60, max_entries=100)
    assert reopened.claim("event-1") is False
    reopened._db.execute(  # noqa: SLF001 - controlled test setup
        "UPDATE seen_events SET seen_at = ?", (int(time.time()) - 120,)
    )
    reopened._db.commit()  # noqa: SLF001 - controlled test setup
    reopened.cleanup()
    assert reopened.claim("event-1") is True
    reopened.close()


def test_persistent_inbox_recovers_without_replaying_processing(tmp_path: Path) -> None:
    path = tmp_path / "events.sqlite3"
    inbox = PersistentDeduplicator(path, ttl_seconds=60, max_entries=100)
    assert inbox.claim(
        "pending-1",
        channel="D1",
        text="do work",
        reply_thread_ts="1.0",
        session_key="thread-1.0",
    )
    inbox.close()

    inbox = PersistentDeduplicator(path, ttl_seconds=60, max_entries=100)
    pending, interrupted = inbox.recover()
    assert [message.event_id for message in pending] == ["pending-1"]
    assert pending[0].session_key == "thread-1.0"
    assert interrupted == []

    inbox.mark_processing("pending-1")
    pending, interrupted = inbox.recover()
    assert pending == []
    assert [message.text for message in interrupted] == ["do work"]

    inbox.mark_done("pending-1")
    assert inbox.recover() == ([], [])
    row = inbox._db.execute(  # noqa: SLF001 - verify sensitive payload cleanup
        "SELECT status, channel, text, reply_thread_ts, session_key "
        "FROM seen_events WHERE event_id = ?",
        ("pending-1",),
    ).fetchone()
    assert row == ("done", None, None, None, None)
    inbox.close()


def test_persistent_inbox_migrates_old_dedup_schema(tmp_path: Path) -> None:
    path = tmp_path / "events.sqlite3"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE seen_events (event_id TEXT PRIMARY KEY, seen_at INTEGER NOT NULL)")
    db.execute("INSERT INTO seen_events VALUES ('old', ?)", (int(time.time()),))
    db.commit()
    db.close()

    inbox = PersistentDeduplicator(path, ttl_seconds=60, max_entries=100)
    assert inbox.claim("old") is False
    columns = {row[1] for row in inbox._db.execute("PRAGMA table_info(seen_events)")}  # noqa: SLF001
    assert {"status", "channel", "text", "reply_thread_ts", "session_key"} <= columns
    pending, interrupted = inbox.recover()
    assert pending == []
    assert interrupted == []
    inbox.close()


def test_persistent_inbox_migrates_pre_session_key_schema(tmp_path: Path) -> None:
    path = tmp_path / "events.sqlite3"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE seen_events ("
        "event_id TEXT PRIMARY KEY, seen_at INTEGER NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'done', channel TEXT, text TEXT, "
        "reply_thread_ts TEXT)"
    )
    db.execute(
        "INSERT INTO seen_events VALUES (?, ?, 'pending', ?, ?, ?)",
        ("old-pending", int(time.time()), "D1", "resume me", "1.0"),
    )
    db.commit()
    db.close()

    inbox = PersistentDeduplicator(path, ttl_seconds=60, max_entries=100)
    pending, interrupted = inbox.recover()
    assert interrupted == []
    assert len(pending) == 1
    assert pending[0].event_id == "old-pending"
    assert pending[0].session_key is None
    columns = {row[1] for row in inbox._db.execute("PRAGMA table_info(seen_events)")}  # noqa: SLF001
    assert "session_key" in columns
    inbox.close()


def test_event_filter_only_accepts_allowed_human_dm() -> None:
    event = {
        "channel_type": "im",
        "user": "U123",
        "text": "hello",
        "client_msg_id": "id-1",
    }
    assert should_accept_event(event, frozenset({"U123"}))
    assert event_id(event) == "id-1"
    assert not should_accept_event({**event, "channel_type": "channel"}, frozenset({"U123"}))
    assert not should_accept_event({**event, "bot_id": "B1"}, frozenset({"U123"}))
    assert not should_accept_event({**event, "user": "U999"}, frozenset({"U123"}))
    assert not should_accept_event({**event, "text": "  "}, frozenset({"U123"}))
    assert should_accept_event(
        {**event, "text": "", "subtype": "file_share", "files": [{"id": "F1"}]},
        frozenset({"U123"}),
    )
    assert not should_accept_event({**event, "subtype": "message_changed"}, frozenset({"U123"}))


def test_chunk_text_preserves_content_and_limits_chunks() -> None:
    text = "first line\n" + ("word " * 30) + "tail"
    chunks = chunk_text(text, 40)
    assert "".join(chunks) == text
    assert all(0 < len(chunk) <= 40 for chunk in chunks)
    assert chunk_text("", 40) == ["(无输出)"]


def test_undelivered_record_is_private_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "undelivered.jsonl"
    append_undelivered(path, {"channel": "D1", "text": "answer"})
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "channel": "D1",
        "text": "answer",
    }
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
