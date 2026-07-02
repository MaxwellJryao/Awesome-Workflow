#!/usr/bin/env python3
"""最小复现：用与 bridge.py 相同的 options 起一个 SDK 会话，抓 claude 子进程 stderr。"""
import asyncio
import os
import sys
from pathlib import Path

os.environ["PATH"] = f"{Path.home()}/.local/bin:" + os.environ.get("PATH", "")

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    ResultMessage,
)

WORK_DIR = os.environ.get("CLAUDE_CWD", str(Path.cwd()))


def on_stderr(line: str) -> None:
    print(f"[claude-stderr] {line}", flush=True)


async def main() -> None:
    kwargs = dict(
        cwd=WORK_DIR,
        permission_mode="bypassPermissions",
        system_prompt={"type": "preset", "preset": "claude_code"},
        setting_sources=["user", "project"],
    )
    try:
        options = ClaudeAgentOptions(**kwargs, stderr=on_stderr)
    except TypeError:
        print("(此 SDK 版本不支持 stderr 回调)", flush=True)
        options = ClaudeAgentOptions(**kwargs)

    client = ClaudeSDKClient(options=options)
    print("connecting...", flush=True)
    await client.connect()
    print("connected, querying...", flush=True)
    await client.query("reply with just: ok")
    async for msg in client.receive_response():
        if isinstance(msg, ResultMessage):
            print(f"RESULT: {msg.result!r} session={msg.session_id}", flush=True)
    await client.disconnect()
    print("PASS", flush=True)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
