"""Configuration, durable state, and pure helpers for the Slack bridge."""

from __future__ import annotations

import fcntl
import json
import math
import os
import shutil
import sqlite3
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, BinaryIO


class ConfigurationError(ValueError):
    """The bridge environment is incomplete or unsafe."""


class AlreadyRunningError(RuntimeError):
    """Another bridge process owns the state directory lock."""


@dataclass(frozen=True, slots=True)
class BridgeConfig:
    bot_token: str = field(repr=False)
    app_token: str = field(repr=False)
    allowed_user_ids: frozenset[str]
    work_dir: Path
    state_dir: Path
    upload_dir: Path | None = None
    codex_bin: str = "codex"
    codex_sandbox: str = "workspace-write"
    codex_model: str | None = "gpt-5.6-sol"
    codex_profile: str | None = None
    codex_reasoning_effort: str | None = "ultra"
    codex_service_tier: str | None = "fast"
    codex_fast_mode: bool = True
    codex_add_dirs: tuple[Path, ...] = ()
    codex_skip_git_repo_check: bool = False
    codex_strict_config: bool = False
    allow_unsafe_self_hosting: bool = False
    turn_timeout_seconds: float | None = 3600
    interrupt_grace_seconds: float = 8
    show_tool_details: bool = False
    reply_in_thread: bool = True
    max_prompt_chars: int = 20_000
    max_queue_size: int = 100
    max_slack_chunk: int = 3500
    max_files_per_message: int = 10
    max_file_bytes: int = 50 * 1024 * 1024
    download_timeout_seconds: float = 60
    progress_update_interval: float = 1.5
    dedup_ttl_seconds: int = 7 * 24 * 3600
    dedup_max_entries: int = 20_000

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        component_dir: Path | None = None,
    ) -> BridgeConfig:
        values = dict(env if env is not None else os.environ)
        component_dir = component_dir or Path(__file__).resolve().parent

        bot_token = _required(values, "SLACK_BOT_TOKEN")
        app_token = _required(values, "SLACK_APP_TOKEN")
        if not bot_token.startswith("xoxb-") or "..." in bot_token:
            raise ConfigurationError("SLACK_BOT_TOKEN 必须是有效的 xoxb- token")
        if not app_token.startswith("xapp-") or "..." in app_token:
            raise ConfigurationError("SLACK_APP_TOKEN 必须是有效的 xapp- token")

        raw_users = values.get("SLACK_ALLOWED_USER_IDS") or values.get("SLACK_ALLOWED_USER_ID", "")
        allowed_users = frozenset(
            item.strip() for item in raw_users.replace(";", ",").split(",") if item.strip()
        )
        if len(allowed_users) != 1:
            raise ConfigurationError("bridge 只支持一个 SLACK_ALLOWED_USER_ID")

        raw_work_dir = _required(values, "CODEX_CWD")
        work_dir = Path(raw_work_dir).expanduser().resolve()
        if not work_dir.is_dir():
            raise ConfigurationError(f"CODEX_CWD 不存在或不是目录：{work_dir}")

        state_dir = Path(values.get("BRIDGE_STATE_DIR", component_dir / "state")).expanduser()
        if not state_dir.is_absolute():
            state_dir = component_dir / state_dir
        state_dir = state_dir.resolve()
        upload_dir = Path(values.get("BRIDGE_UPLOAD_DIR", state_dir / "uploads")).expanduser()
        if not upload_dir.is_absolute():
            upload_dir = state_dir / upload_dir
        upload_dir = upload_dir.resolve()
        sandbox = values.get("CODEX_SANDBOX", "workspace-write").strip()
        if sandbox not in {"read-only", "workspace-write", "danger-full-access"}:
            raise ConfigurationError(f"无效的 CODEX_SANDBOX：{sandbox}")
        add_dirs = tuple(
            Path(item.strip()).expanduser().resolve()
            for item in values.get("CODEX_ADD_DIRS", "").split(os.pathsep)
            if item.strip()
        )
        raw_codex_bin = values.get("CODEX_BIN", "codex").strip() or "codex"
        resolved_codex_bin = shutil.which(raw_codex_bin)
        if resolved_codex_bin:
            codex_bin = str(Path(resolved_codex_bin).resolve())
            codex_binary_path: Path | None = Path(codex_bin)
        elif os.sep in raw_codex_bin:
            codex_bin = str(Path(raw_codex_bin).expanduser().resolve())
            codex_binary_path = Path(codex_bin)
        else:
            codex_bin = raw_codex_bin
            codex_binary_path = None
        allow_self_hosting = _bool(values, "BRIDGE_ALLOW_UNSAFE_SELF_HOSTING", False)
        resolved_component_dir = component_dir.expanduser().resolve()
        credential_file = Path(
            values.get("BRIDGE_ENV_FILE", resolved_component_dir / ".env")
        ).expanduser()
        if not credential_file.is_absolute():
            credential_file = resolved_component_dir / credential_file
        credential_file = credential_file.resolve()
        if credential_file.exists() and credential_file.stat().st_mode & 0o077:
            raise ConfigurationError(f"凭据文件权限过宽：{credential_file}；请执行 chmod 600")
        sensitive_paths = [resolved_component_dir, state_dir, credential_file]
        if codex_binary_path is not None:
            sensitive_paths.append(codex_binary_path)
        writable_roots = (work_dir, *add_dirs)
        unsafe_writable_layout = sandbox == "danger-full-access" or (
            sandbox == "workspace-write"
            and any(
                path.is_relative_to(root) or root.is_relative_to(path)
                for path in sensitive_paths
                for root in writable_roots
            )
        )
        if unsafe_writable_layout and not allow_self_hosting:
            raise ConfigurationError(
                "bridge 源码、状态或凭据位于 Codex 可写范围；请把它们部署到工作区外，"
                "或显式设置 BRIDGE_ALLOW_UNSAFE_SELF_HOSTING=true 承担自修改风险"
            )

        timeout = _float(values, "CODEX_TURN_TIMEOUT_SECONDS", 3600, minimum=0)
        timeout_or_none = None if timeout == 0 else timeout

        return cls(
            bot_token=bot_token,
            app_token=app_token,
            allowed_user_ids=allowed_users,
            work_dir=work_dir,
            state_dir=state_dir,
            upload_dir=upload_dir,
            codex_bin=codex_bin,
            codex_sandbox=sandbox,
            codex_model=_optional(values, "CODEX_MODEL") or "gpt-5.6-sol",
            codex_profile=_optional(values, "CODEX_PROFILE"),
            codex_reasoning_effort=(_optional(values, "CODEX_REASONING_EFFORT") or "ultra"),
            codex_service_tier=_optional(values, "CODEX_SERVICE_TIER") or "fast",
            codex_fast_mode=_bool(values, "CODEX_FAST_MODE", True),
            codex_add_dirs=add_dirs,
            codex_skip_git_repo_check=_bool(values, "CODEX_SKIP_GIT_REPO_CHECK", False),
            codex_strict_config=_bool(values, "CODEX_STRICT_CONFIG", False),
            allow_unsafe_self_hosting=allow_self_hosting,
            turn_timeout_seconds=timeout_or_none,
            interrupt_grace_seconds=_float(values, "CODEX_INTERRUPT_GRACE_SECONDS", 8, minimum=0.1),
            show_tool_details=_bool(values, "CODEX_SHOW_TOOL_DETAILS", False),
            reply_in_thread=_bool(values, "SLACK_REPLY_IN_THREAD", True),
            max_prompt_chars=_int(values, "BRIDGE_MAX_PROMPT_CHARS", 20_000, minimum=1),
            max_queue_size=_int(values, "BRIDGE_MAX_QUEUE_SIZE", 100, minimum=1),
            max_slack_chunk=_int(values, "BRIDGE_MAX_SLACK_CHUNK", 3500, minimum=500),
            max_files_per_message=_int(values, "BRIDGE_MAX_FILES_PER_MESSAGE", 10, minimum=1),
            max_file_bytes=_int(values, "BRIDGE_MAX_FILE_BYTES", 50 * 1024 * 1024, minimum=1),
            download_timeout_seconds=_float(
                values, "BRIDGE_DOWNLOAD_TIMEOUT_SECONDS", 60, minimum=0.1
            ),
            progress_update_interval=_float(
                values, "BRIDGE_PROGRESS_INTERVAL_SECONDS", 1.5, minimum=0.2
            ),
            dedup_ttl_seconds=_int(values, "BRIDGE_DEDUP_TTL_SECONDS", 604_800, minimum=60),
            dedup_max_entries=_int(values, "BRIDGE_DEDUP_MAX_ENTRIES", 20_000, minimum=100),
        )


def _required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ConfigurationError(f"缺少环境变量 {key}")
    return value


def _optional(env: Mapping[str, str], key: str) -> str | None:
    return env.get(key, "").strip() or None


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{key} 必须是 true/false，实际为 {raw!r}")


def _int(env: Mapping[str, str], key: str, default: int, *, minimum: int) -> int:
    raw = env.get(key)
    try:
        value = default if raw is None or not raw.strip() else int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{key} 必须是整数") from exc
    if value < minimum:
        raise ConfigurationError(f"{key} 不能小于 {minimum}")
    return value


def _float(env: Mapping[str, str], key: str, default: float, *, minimum: float) -> float:
    raw = env.get(key)
    try:
        value = default if raw is None or not raw.strip() else float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{key} 必须是数字") from exc
    if not math.isfinite(value) or value < minimum:
        raise ConfigurationError(f"{key} 必须是有限数字且不能小于 {minimum}")
    return value


class InstanceLock:
    """Advisory process-lifetime lock with a human-readable PID."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._file: BinaryIO | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        file = self.path.open("a+b")
        os.chmod(self.path, 0o600)
        try:
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            file.close()
            raise AlreadyRunningError(f"已有 bridge 实例持有锁：{self.path}") from exc
        file.seek(0)
        file.truncate()
        file.write(f"{os.getpid()}\n".encode())
        file.flush()
        self._file = file

    def release(self) -> None:
        if self._file is None:
            return
        fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        self._file.close()
        self._file = None

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


class SessionStore:
    """Atomically persist a Codex thread UUID with mode 0600."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> str | None:
        try:
            value = self.path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        try:
            return str(uuid.UUID(value))
        except ValueError:
            self.clear()
            return None

    def save(self, session_id: str) -> None:
        normalized = str(uuid.UUID(session_id))
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp_path = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write(normalized + "\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, self.path)
            os.chmod(self.path, 0o600)
        finally:
            temp_path.unlink(missing_ok=True)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


class SessionMapStore:
    """Atomically persist Slack session keys to Codex thread UUIDs.

    The JSON file is treated as private bridge state: every successful write is
    an atomic replacement with mode 0600. Invalid persisted entries are
    discarded so one damaged session cannot prevent the remaining sessions
    from being resumed.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def get(self, session_key: str) -> str | None:
        key = self._validate_key(session_key)
        return self._read().get(key)

    def save(self, session_key: str, session_id: str) -> None:
        key = self._validate_key(session_key)
        normalized = str(uuid.UUID(session_id))
        sessions = self._read()
        sessions[key] = normalized
        self._write(sessions)

    def clear(self, session_key: str) -> None:
        key = self._validate_key(session_key)
        sessions = self._read()
        if key not in sessions:
            return
        del sessions[key]
        self._write(sessions)

    def count(self) -> int:
        return len(self._read())

    def snapshot(self) -> dict[str, str]:
        """Return an independent, validated snapshot of all session mappings."""

        return self._read()

    def migrate_legacy(self, legacy_path: Path, *, legacy_key: str) -> bool:
        """Move a legacy single-session file under ``legacy_key`` once.

        An existing mapping always wins. The legacy file is removed only after
        the mapping is already present or has been atomically written, making
        repeated calls safe after an interrupted migration. The return value is
        true only when this call added the mapping.
        """

        key = self._validate_key(legacy_key)
        if legacy_path.resolve() == self.path.resolve():
            raise ValueError("legacy_path must differ from the session map path")
        legacy_store = SessionStore(legacy_path)
        session_id = legacy_store.load()
        if session_id is None:
            return False

        sessions = self._read()
        migrated = key not in sessions
        if migrated:
            sessions[key] = session_id
            self._write(sessions)
        legacy_store.clear()
        return migrated

    @staticmethod
    def _validate_key(session_key: str) -> str:
        if not isinstance(session_key, str) or not session_key.strip():
            raise ValueError("session_key must be a non-empty string")
        return session_key

    def _read(self) -> dict[str, str]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except UnicodeDecodeError:
            self.path.unlink(missing_ok=True)
            return {}

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self.path.unlink(missing_ok=True)
            return {}
        if not isinstance(payload, dict):
            self.path.unlink(missing_ok=True)
            return {}

        sessions: dict[str, str] = {}
        changed = False
        for key, value in payload.items():
            if not isinstance(key, str) or not key.strip() or not isinstance(value, str):
                changed = True
                continue
            try:
                normalized = str(uuid.UUID(value))
            except ValueError:
                changed = True
                continue
            sessions[key] = normalized
            changed = changed or normalized != value

        if changed:
            self._write(sessions)
        else:
            os.chmod(self.path, 0o600)
        return sessions

    def _write(self, sessions: Mapping[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp_path = self.path.with_name(f".{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(dict(sessions), file, ensure_ascii=False, sort_keys=True, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, self.path)
            os.chmod(self.path, 0o600)
        finally:
            temp_path.unlink(missing_ok=True)


@dataclass(frozen=True, slots=True)
class PersistedMessage:
    event_id: str
    channel: str
    text: str
    reply_thread_ts: str | None
    session_key: str | None = None


class PersistentDeduplicator:
    """Durable, bounded Slack event-id set plus a minimal inbox journal.

    Pending messages are safe to resume after a bridge restart. A message marked
    ``processing`` is never replayed automatically because Codex may already
    have made changes; callers surface it as interrupted instead.
    """

    def __init__(self, path: Path, *, ttl_seconds: int, max_entries: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = path
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._claims_since_cleanup = 0
        self._db = sqlite3.connect(path, timeout=5)
        os.chmod(path, 0o600)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS seen_events "
            "(event_id TEXT PRIMARY KEY, seen_at INTEGER NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'done', channel TEXT, text TEXT, "
            "reply_thread_ts TEXT, session_key TEXT)"
        )
        self._migrate_schema()
        self._db.commit()
        self.cleanup()

    def _migrate_schema(self) -> None:
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(seen_events)")}
        migrations = {
            "status": "ALTER TABLE seen_events ADD COLUMN status TEXT NOT NULL DEFAULT 'done'",
            "channel": "ALTER TABLE seen_events ADD COLUMN channel TEXT",
            "text": "ALTER TABLE seen_events ADD COLUMN text TEXT",
            "reply_thread_ts": "ALTER TABLE seen_events ADD COLUMN reply_thread_ts TEXT",
            "session_key": "ALTER TABLE seen_events ADD COLUMN session_key TEXT",
        }
        for column, statement in migrations.items():
            if column not in columns:
                self._db.execute(statement)

    def claim(
        self,
        event_id: str,
        *,
        channel: str | None = None,
        text: str | None = None,
        reply_thread_ts: str | None = None,
        session_key: str | None = None,
    ) -> bool:
        now = int(time.time())
        status = "pending" if channel is not None and text is not None else "done"
        with self._db:
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO seen_events"
                "(event_id, seen_at, status, channel, text, reply_thread_ts, session_key) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event_id, now, status, channel, text, reply_thread_ts, session_key),
            )
        self._claims_since_cleanup += 1
        if self._claims_since_cleanup >= 100:
            self.cleanup(now=now)
        return cursor.rowcount == 1

    def contains(self, event_id: str) -> bool:
        row = self._db.execute(
            "SELECT 1 FROM seen_events WHERE event_id = ? LIMIT 1", (event_id,)
        ).fetchone()
        return row is not None

    def mark_processing(self, event_id: str) -> None:
        self._set_status(event_id, "processing")

    def mark_pending(self, event_id: str) -> None:
        self._set_status(event_id, "pending")

    def mark_done(self, event_id: str) -> None:
        with self._db:
            self._db.execute(
                "UPDATE seen_events SET status = 'done', channel = NULL, text = NULL, "
                "reply_thread_ts = NULL, session_key = NULL WHERE event_id = ?",
                (event_id,),
            )

    def _set_status(self, event_id: str, status: str) -> None:
        with self._db:
            self._db.execute(
                "UPDATE seen_events SET status = ? WHERE event_id = ?", (status, event_id)
            )

    def recover(self) -> tuple[list[PersistedMessage], list[PersistedMessage]]:
        pending = self._messages_with_status("pending")
        interrupted = self._messages_with_status("processing")
        return pending, interrupted

    def _messages_with_status(self, status: str) -> list[PersistedMessage]:
        rows = self._db.execute(
            "SELECT event_id, channel, text, reply_thread_ts, session_key FROM seen_events "
            "WHERE status = ? AND channel IS NOT NULL AND text IS NOT NULL "
            "ORDER BY seen_at, rowid",
            (status,),
        )
        return [PersistedMessage(*row) for row in rows]

    def cleanup(self, *, now: int | None = None) -> None:
        now = int(time.time()) if now is None else now
        cutoff = now - self.ttl_seconds
        with self._db:
            self._db.execute(
                "DELETE FROM seen_events WHERE status = 'done' AND seen_at < ?", (cutoff,)
            )
            self._db.execute(
                "DELETE FROM seen_events WHERE event_id IN ("
                "SELECT event_id FROM seen_events WHERE status = 'done' "
                "ORDER BY seen_at DESC, rowid DESC "
                "LIMIT -1 OFFSET ?)",
                (self.max_entries,),
            )
        self._claims_since_cleanup = 0

    def close(self) -> None:
        self._db.close()


def should_accept_event(event: Mapping[str, Any], allowed_users: frozenset[str]) -> bool:
    """Return whether a Slack message event is an allowed human DM."""

    text = event.get("text")
    files = event.get("files")
    has_text = isinstance(text, str) and bool(text.strip())
    has_files = isinstance(files, list) and bool(files)
    return bool(
        event.get("channel_type") == "im"
        and event.get("subtype") in (None, "file_share")
        and not event.get("bot_id")
        and event.get("user") in allowed_users
        and (has_text or has_files)
    )


def event_id(event: Mapping[str, Any]) -> str | None:
    value = event.get("client_msg_id") or event.get("event_ts") or event.get("ts")
    return str(value) if value else None


def chunk_text(text: str, max_chars: int) -> list[str]:
    """Split Slack output near line/word boundaries without losing characters."""

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if not text:
        return ["(无输出)"]
    chunks: list[str] = []
    remaining = text
    while len(remaining) > max_chars:
        boundary = remaining.rfind("\n", 0, max_chars)
        if boundary < max_chars // 2:
            boundary = remaining.rfind(" ", 0, max_chars)
        if boundary < max_chars // 2:
            boundary = max_chars
        else:
            boundary += 1
        chunks.append(remaining[:boundary])
        remaining = remaining[boundary:]
    if remaining:
        chunks.append(remaining)
    return chunks


def append_undelivered(path: Path, payload: Mapping[str, Any]) -> None:
    """Preserve a final Slack reply locally when all network retries fail."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as file:
        file.write(json.dumps(dict(payload), ensure_ascii=False) + "\n")
    os.chmod(path, 0o600)
