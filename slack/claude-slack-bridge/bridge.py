#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "slack-bolt>=1.21",
#   "aiohttp",
#   "claude-agent-sdk>=0.1.0",
# ]
# ///
"""Slack DM -> Claude Code bridge (cw-dfw).

多会话私信桥：启用 Slack "Agents & AI Apps"（见 slack-app-manifest.yaml）后，
每个 New Chat 线程对应一个独立的 Claude Code 会话；thread_ts -> session_id
的映射持久化在 sessions.json，重启后按线程恢复上下文。未启用 AI Apps 时
退化为原来的单会话 DM 模式。

特殊指令（在任意线程内发送）:
  !new     -- 重置当前线程的会话（有 New Chat 按钮后基本用不上）
  !status  -- 查看队列长度和当前线程的 session id
  !<命令>  -- 其余 ! 开头的消息转发成 Claude Code 原生斜杠命令，如
              !goal / !compact / !context / !usage（Slack 输入框会拦截
              / 开头的消息，所以在 Slack 里用 ! 代替 /）

全局 slash 命令（slash payload 不带 thread_ts，只能做全局操作）:
  /bridge status -- 全局状态
  /bridge stop   -- 中断正在执行的回合并清空队列
  /bridge help   -- 帮助
"""

import asyncio
import json
import os
import traceback
from pathlib import Path

import aiohttp

# 确保子进程能找到 ~/.local/bin/claude
os.environ["PATH"] = f"{Path.home()}/.local/bin:" + os.environ.get("PATH", "")

from slack_bolt.app.async_app import AsyncApp
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ResultMessage,
)

BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
APP_TOKEN = os.environ["SLACK_APP_TOKEN"]
ALLOWED_USER = os.environ["SLACK_ALLOWED_USER_ID"]
WORK_DIR = os.environ.get("CLAUDE_CWD", str(Path.home()))
PERMISSION_MODE = os.environ.get("CLAUDE_PERMISSION_MODE", "bypassPermissions")
# 默认值与 ~/.zshrc 里 claude() 包装保持一致：fable-5 + xhigh effort + ultracode
MODEL = os.environ.get("CLAUDE_MODEL", "claude-fable-5")
EFFORT = os.environ.get("CLAUDE_EFFORT", "xhigh")
CLAUDE_SETTINGS = os.environ.get("CLAUDE_SETTINGS", '{"ultracode": true}')
LEGACY_SESSION_FILE = Path(__file__).with_name("session_id.txt")  # 旧单会话版遗留
SESSIONS_FILE = Path(__file__).with_name("sessions.json")
UPLOADS_DIR = Path(__file__).with_name("uploads")
MAX_CHUNK = 3500  # Slack 单条消息安全长度
MAX_FILE_SIZE = 50 * 1024 * 1024

app = AsyncApp(token=BOT_TOKEN)
queue: asyncio.Queue = asyncio.Queue()
seen_msg_ids: set = set()
sessions: dict[str, str] = {}  # thread_key -> claude session_id
# /bridge stop 需要从 slash 命令 handler 够到 worker 正在用的 client
runstate: dict = {"client": None, "tkey": None, "busy": False}


def load_sessions() -> dict[str, str]:
    try:
        return {k: v for k, v in json.loads(SESSIONS_FILE.read_text()).items() if v}
    except Exception:
        return {}


def save_sessions() -> None:
    tmp = SESSIONS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(sessions, indent=1))
    tmp.replace(SESSIONS_FILE)


def build_options(resume: str | None) -> ClaudeAgentOptions:
    kwargs = dict(
        cwd=WORK_DIR,
        permission_mode=PERMISSION_MODE,
        model=MODEL,
        effort=EFFORT,
        settings=CLAUDE_SETTINGS,
        # 不加这两行的话，SDK 默认不带 Claude Code 的系统提示词、
        # 也不读 CLAUDE.md / settings.json，行为会和终端里的 claude 不一致
        system_prompt={"type": "preset", "preset": "claude_code"},
        setting_sources=["user", "project"],
        resume=resume,
    )
    try:
        # claude 子进程的 stderr 写进 bridge.log，否则崩溃时只有 exit code 没有死因
        return ClaudeAgentOptions(
            **kwargs, stderr=lambda line: print(f"[claude-stderr] {line}", flush=True)
        )
    except TypeError:  # 旧版 SDK 没有 stderr 回调
        return ClaudeAgentOptions(**kwargs)


async def make_client(tkey: str) -> ClaudeSDKClient:
    """为指定线程建立 Claude 会话；连续失败 3 次后放弃 resume 开新会话。"""
    failures = 0
    while True:
        try:
            client = ClaudeSDKClient(options=build_options(sessions.get(tkey)))
            await client.connect()
            return client
        except Exception:
            failures += 1
            print(f"connect 失败 ({failures}):\n{traceback.format_exc()}", flush=True)
            if failures >= 3 and tkey in sessions:
                print("连续失败，放弃 resume 改开新会话", flush=True)
                sessions.pop(tkey, None)
                save_sessions()
            await asyncio.sleep(5)


def describe_tool(block: ToolUseBlock) -> str:
    inp = block.input or {}
    detail = str(
        inp.get("command")
        or inp.get("file_path")
        or inp.get("pattern")
        or inp.get("description")
        or ""
    )
    if len(detail) > 150:
        detail = detail[:150] + "…"
    return f"`{block.name}` {detail}"


async def save_files(files: list[dict]) -> list[str]:
    """把 Slack 附件下载到本地，返回保存路径（Claude 可用 Read 直接查看，含图片）。"""
    saved: list[str] = []
    UPLOADS_DIR.mkdir(exist_ok=True)
    async with aiohttp.ClientSession(
        headers={"Authorization": f"Bearer {BOT_TOKEN}"}
    ) as session:
        for f in files:
            url = f.get("url_private_download") or f.get("url_private")
            if not url or f.get("size", 0) > MAX_FILE_SIZE:
                continue
            name = (f.get("name") or "file").replace("/", "_")
            path = UPLOADS_DIR / f"{f.get('id', 'F')}-{name}"
            try:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    path.write_bytes(await resp.read())
                saved.append(str(path))
            except Exception as e:
                print(f"下载附件失败 {name}: {e}", flush=True)
    return saved


async def post_chunks(channel: str, thread_ts: str | None, text: str) -> None:
    for i in range(0, len(text), MAX_CHUNK):
        await app.client.chat_postMessage(
            channel=channel, thread_ts=thread_ts, text=text[i : i + MAX_CHUNK]
        )


async def update_status_msg(
    channel: str, thread_ts: str | None, ts: str | None, text: str
) -> str:
    if ts is None:
        resp = await app.client.chat_postMessage(
            channel=channel, thread_ts=thread_ts, text=text
        )
        return resp["ts"]
    await app.client.chat_update(channel=channel, ts=ts, text=text)
    return ts


async def set_native_status(channel: str, thread_ts: str | None, status: str) -> None:
    """AI Apps 线程顶部的原生状态条；非线程消息或未启用 AI Apps 时静默跳过。"""
    if not thread_ts:
        return
    try:
        await app.client.assistant_threads_setStatus(
            channel_id=channel, thread_ts=thread_ts, status=status
        )
    except Exception:
        pass


async def run_one_turn(
    client: ClaudeSDKClient, tkey: str, channel: str, thread_ts: str | None, text: str
) -> None:
    status_ts = None
    tool_count = 0
    fallback_parts: list = []

    await set_native_status(channel, thread_ts, "正在思考…")
    await client.query(text)
    async for msg in client.receive_response():
        if isinstance(msg, AssistantMessage):
            for block in msg.content:
                if isinstance(block, ToolUseBlock):
                    tool_count += 1
                    status_ts = await update_status_msg(
                        channel, thread_ts, status_ts,
                        f"⏳ [{tool_count}] {describe_tool(block)}",
                    )
                    await set_native_status(
                        channel, thread_ts, f"[{tool_count}] 正在运行 {block.name}…"
                    )
                elif isinstance(block, TextBlock) and block.text.strip():
                    fallback_parts.append(block.text)
        elif isinstance(msg, ResultMessage):
            if msg.session_id:
                sessions[tkey] = msg.session_id
                save_sessions()
            reply = msg.result or "\n\n".join(fallback_parts) or "(无输出)"
            if msg.is_error:
                reply = f"⚠️ 本轮出错:\n{reply}"
            if status_ts:
                await app.client.chat_update(
                    channel=channel, ts=status_ts, text=f"✅ 共 {tool_count} 次工具调用"
                )
            await post_chunks(channel, thread_ts, reply)


async def worker() -> None:
    global sessions
    sessions = load_sessions()
    legacy_session = (
        LEGACY_SESSION_FILE.read_text().strip()
        if LEGACY_SESSION_FILE.exists()
        else None
    )
    # 同一时刻只连一个 claude 子进程；换线程时断开重连（靠 resume 恢复上下文），
    # 避免多个 claude 进程同时吃掉计算节点的内存
    client: ClaudeSDKClient | None = None
    client_key: str | None = None

    async def drop_client() -> None:
        nonlocal client, client_key
        if client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass
        client, client_key = None, None

    while True:
        channel, thread_ts, text = await queue.get()
        tkey = thread_ts or f"dm-{channel}"
        # 旧单会话版的上下文迁移到顶层 DM 键
        if tkey.startswith("dm-") and tkey not in sessions and legacy_session:
            sessions[tkey] = legacy_session
        stripped = text.strip()
        cmd = stripped.split(maxsplit=1)[0].lower() if stripped else ""
        try:
            if cmd in ("!new", "/new"):
                if client_key == tkey:
                    await drop_client()
                sessions.pop(tkey, None)
                save_sessions()
                await post_chunks(channel, thread_ts, "🧹 本线程已重置，下一条消息将开全新会话。")
            elif cmd == "!status":
                await post_chunks(
                    channel, thread_ts,
                    f"队列中还有 {queue.qsize()} 条 | 本线程 session: {sessions.get(tkey, '(新)')} | "
                    f"已知线程 {len(sessions)} 个 | model: {MODEL} ({EFFORT}) | "
                    f"cwd: {WORK_DIR} | host: {os.uname().nodename}",
                )
            else:
                # Slack 输入框拦截 / 开头的消息，! 前缀等价转发成 Claude Code
                # 原生斜杠命令：!goal -> /goal、!compact -> /compact 等
                if stripped.startswith("!") and stripped[1:2].isalpha():
                    text = "/" + stripped[1:]
                first_turn = tkey not in sessions
                if client is None or client_key != tkey:
                    await drop_client()
                    await set_native_status(channel, thread_ts, "正在恢复会话…")
                    client = await make_client(tkey)
                    client_key = tkey
                if first_turn and thread_ts:
                    try:
                        await app.client.assistant_threads_setTitle(
                            channel_id=channel, thread_ts=thread_ts, title=text[:60]
                        )
                    except Exception:
                        pass
                runstate.update(client=client, tkey=tkey, busy=True)
                try:
                    await run_one_turn(client, tkey, channel, thread_ts, text)
                finally:
                    runstate.update(client=None, tkey=None, busy=False)
        except Exception:
            await post_chunks(
                channel, thread_ts,
                f"💥 bridge 异常:\n```{traceback.format_exc()[-2000:]}```",
            )
            # 会话进程可能已挂，丢弃连接，下一条消息会重建并尽量恢复上下文
            await drop_client()


BRIDGE_HELP = (
    "*/bridge status* — 全局状态（队列、正在处理的线程、模型）\n"
    "*/bridge stop* — 中断正在执行的回合并清空队列\n"
    "*/bridge help* — 本帮助\n"
    "线程内指令: `!new` 重置本线程会话 | `!status` 本线程状态\n"
    "其余 `!命令` 会转发成 Claude Code 原生斜杠命令: `!goal <目标>` `!compact` `!context` `!usage` …\n"
    "点 *New Chat* 开新线程 = 全新 Claude 会话；History 里的旧线程可继续聊"
)


@app.command("/bridge")
async def on_bridge_command(ack, command, respond):
    await ack()
    if command.get("user_id") != ALLOWED_USER:
        return
    sub = (command.get("text") or "status").strip().split()[0].lower()
    if sub == "stop":
        dropped = 0
        while not queue.empty():
            try:
                queue.get_nowait()
                dropped += 1
            except asyncio.QueueEmpty:
                break
        if runstate["busy"] and runstate["client"] is not None:
            try:
                await runstate["client"].interrupt()
                await respond(f"🛑 已向当前回合发送中断（线程 {runstate['tkey']}），丢弃排队消息 {dropped} 条。")
            except Exception as e:
                await respond(f"⚠️ 中断失败: {e}（已丢弃排队消息 {dropped} 条）")
        else:
            await respond(f"当前空闲，丢弃排队消息 {dropped} 条。")
    elif sub == "help":
        await respond(BRIDGE_HELP)
    else:
        busy = f"正在处理线程 {runstate['tkey']}" if runstate["busy"] else "空闲"
        await respond(
            f"{busy} | 队列 {queue.qsize()} 条 | 已知线程 {len(sessions)} 个 | "
            f"model: {MODEL} ({EFFORT}) | cwd: {WORK_DIR} | host: {os.uname().nodename}"
        )


@app.event("assistant_thread_started")
async def on_thread_started(event):
    at = event.get("assistant_thread") or {}
    if at.get("user_id") != ALLOWED_USER:
        return
    try:
        await app.client.assistant_threads_setSuggestedPrompts(
            channel_id=at["channel_id"],
            thread_ts=at["thread_ts"],
            prompts=[
                {"title": "查看 bridge 状态", "message": "!status"},
                {"title": "查看开发进度", "message": "总结一下当前工作目录的开发进度"},
            ],
        )
    except Exception as e:
        print(f"setSuggestedPrompts 失败: {e}", flush=True)


@app.event("assistant_thread_context_changed")
async def on_thread_context_changed(event):
    pass  # 只订阅不处理，避免 bolt 刷 unhandled request 警告


@app.event("message")
async def on_message(event, say):
    # 只处理私信、真人消息、且来自唯一允许的用户
    if event.get("channel_type") != "im":
        return
    if event.get("subtype") not in (None, "file_share"):  # 带附件的消息是 file_share
        return
    if event.get("bot_id"):
        return
    if event.get("user") != ALLOWED_USER:
        return
    text = event.get("text") or ""
    files = event.get("files") or []
    if not text and not files:
        return
    # Socket Mode 可能重投递事件，靠 client_msg_id 去重（YOLO 模式下重复执行命令很危险）
    msg_id = event.get("client_msg_id") or event.get("ts")
    if msg_id in seen_msg_ids:
        return
    seen_msg_ids.add(msg_id)
    if len(seen_msg_ids) > 5000:
        seen_msg_ids.clear()

    if files:
        paths = await save_files(files)
        if paths:
            listing = "\n".join(f"- {p}" for p in paths)
            text += f"\n\n[用户在 Slack 上传了附件，已保存到本地，可用 Read 工具查看]\n{listing}"

    # AI Apps 线程消息带 thread_ts（= 会话键）；旧式顶层 DM 没有
    await queue.put((event["channel"], event.get("thread_ts"), text))
    if queue.qsize() > 1:
        await say(
            text=f"📥 已排队（前面还有 {queue.qsize() - 1} 条）",
            thread_ts=event.get("thread_ts"),
        )


async def main() -> None:
    task = asyncio.get_running_loop().create_task(worker())
    # worker 意外死亡时整个进程退出，交给 supervisor 重启，避免僵尸 bridge
    task.add_done_callback(lambda t: os._exit(1))
    handler = AsyncSocketModeHandler(app, APP_TOKEN)
    await handler.start_async()


if __name__ == "__main__":
    asyncio.run(main())
