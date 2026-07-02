# Codex Slack Bridge

把指定 Slack 用户的私信串行转发给本机 `codex exec`，并把进度和最终回答发回
Slack。Slack AI App 的每个 **New Chat** 映射到独立 Codex thread；映射会落盘，
因此 bridge 或 Slurm job 重启后仍能分别继续上下文。没有启用 AI App 视图时，普通
顶层 DM 会退化为一个按 DM channel 持久化的会话。

OpenAI 也提供官方 Codex Slack 集成，它创建的是 Codex cloud task。本项目适合必须在
已有集群节点、挂载盘、用户配置和本地仓库中运行 Codex 的场景。

## 行为

- 只处理 App Home / DM 中来自唯一 allowlist 用户的真人文本与 `file_share` 消息。
- 所有消息进入一个有限 FIFO，同一工作区同一时间只运行一个 Codex turn。
- `thread_ts -> Codex thread UUID` 以 mode 0600 原子保存；不同 New Chat 不会串上下文。
- 第一轮运行 `codex exec --json -`；后续使用明确的 thread id 执行
  `codex exec resume --json <id> -`。
- Slack 文本从 stdin 传给 Codex，不经过 shell 拼接。
- 附件按实际读取字节数限流后保存到私有 uploads 目录，绝对路径随 prompt 传给 Codex。
- 默认 `workspace-write` sandbox + `--ask-for-approval never`。非交互任务无法弹出
  审批，需要越权的动作会失败并反馈给模型。
- 在 Codex 可能修改工作区前把 inbox 标成 processing；`thread.started` 一到就保存映射。
- Socket Mode 重投事件用 SQLite 持久去重；进程锁和 supervisor 锁阻止多实例。
- 已接收但未开始的消息会持久化；开始状态投递失败时原位退避重试，不打乱 FIFO。
- Slack token 不会传给 Codex 子进程。工具详情默认隐藏，状态更新会节流。
- 最终回复投递失败时保存在 `state/undelivered.jsonl`，避免完全丢失结果。

控制命令：

- `!status`：立即查看运行状态、队列、短 session id、模型和 sandbox。
- `!stop`：SIGINT 当前 Codex 进程组，超时后升级为 SIGTERM / SIGKILL。
- `!new`：轮到这条命令时只清掉当前 Slack 线程的 session 指针；不会删除本地历史。
- `!help`：显示命令帮助。
- `/bridge status|stop|help`：全局状态；停止当前任务并清空等待队列；帮助。

Codex TUI 的 `/compact`、`/goal` 等斜杠命令不是模型 prompt，bridge 不会把未知
`!command` 伪装成对应功能。

## 快速开始

前置条件：Python 3.11+、较新的 Bash、`flock`（util-linux）、`uv`（推荐）或
`venv/pip`，以及已安装并登录的 Codex CLI。

bridge 目录必须位于目标仓库之外。否则 workspace-write Codex 能修改 bridge 代码，
再由 supervisor 带着 Slack 凭据重启修改后的代码。建议像 Claude bridge 一样直接放在
目标仓库的同级目录：

```bash
codex --version
codex login status
cd /path/to/codex-slack-bridge
cp .env.example .env
chmod 600 .env
```

1. 在 [Slack API](https://api.slack.com/apps) 中选择 **Create New App → From a
   manifest**，粘贴 `slack-app-manifest.yaml`。
2. 在 **Basic Information → App-Level Tokens** 创建带 `connections:write` scope 的
   `xapp-` token。
3. 安装 App 到 workspace，取得 `xoxb-` bot token。
4. 从 Slack 个人资料复制唯一允许用户的 member ID，填写 `.env`。
5. 明确填写 Codex 要操作的 `CODEX_CWD`，然后运行：

如果是升级已有 Slack App，manifest 新增了 `assistant:write`、`commands` 和
`files:read`，保存 manifest 后需要 **Reinstall to Workspace**。重开 App 私信页后应能
看到 New Chat 与 History。

```bash
./scripts/install.sh
./test_node.sh
./scripts/run-once.sh
```

`run-once.sh` 适合前台调试；`run.sh` 是带单例锁和指数退避的常驻 supervisor，日志
写入 `state/bridge.log`。

## 配置重点

完整示例见 `.env.example`。

| 变量 | 默认值 | 说明 |
|---|---:|---|
| `SLACK_ALLOWED_USER_ID` | 必填 | 唯一允许控制该 bridge 的 Slack 用户 ID |
| `CODEX_CWD` | 必填 | Codex 工作区 |
| `CODEX_SANDBOX` | `workspace-write` | `read-only` / `workspace-write` / `danger-full-access` |
| `CODEX_MODEL` | `gpt-5.6-sol` | Slack bridge 默认模型 |
| `CODEX_REASONING_EFFORT` | `ultra` | 默认推理强度 |
| `CODEX_SERVICE_TIER` | `fast` | 使用 Fast 服务层级 |
| `CODEX_FAST_MODE` | `true` | 启用 Codex Fast mode 功能 |
| `CODEX_TURN_TIMEOUT_SECONDS` | `3600` | 单轮墙钟超时；`0` 表示不限制 |
| `CODEX_SHOW_TOOL_DETAILS` | `false` | 是否把脱敏后的命令/路径摘要发回 Slack |
| `SLACK_REPLY_IN_THREAD` | `true` | 有 `thread_ts` 时在原 AI App thread 回复 |
| `BRIDGE_ALLOW_UNSAFE_SELF_HOSTING` | `false` | 是否明确允许 Codex 写 bridge/state/凭据路径 |
| `BRIDGE_MAX_QUEUE_SIZE` | `100` | 内存 FIFO 上限 |
| `BRIDGE_MAX_FILES_PER_MESSAGE` | `10` | 单条 Slack 消息最多下载的附件数 |
| `BRIDGE_MAX_FILE_BYTES` | `52428800` | 单附件 metadata 与实际下载字节上限 |
| `BRIDGE_STATE_DIR` | `./state` | session、去重数据库、锁和日志目录 |
| `BRIDGE_UPLOAD_DIR` | `state/uploads` | mode 0700 附件目录；文件为 mode 0600 |

bridge 不使用 `--ephemeral`，因为 ephemeral thread 无法在下一条 Slack 消息中恢复。
它也不使用 `--last`，避免误接到用户在同一台机器上的其他 Codex 会话。

## Slurm 常驻

`keepalive.sh` 默认优先用 `srun --overlap` 挂到同分区剩余时间超过一小时的已有 job，
避免触发集群的每用户节点上限；没有合适宿主时才提交独立 job。若不希望复用已有
allocation，设置 `SLURM_ATTACH_TO_EXISTING_JOB=false`。先在 `.env` 设置
`SLURM_ACCOUNT`、`SLURM_PARTITION` 等变量，然后手动验证：

```bash
./keepalive.sh
squeue --name=codex-slack-bridge
```

确认后可在登录节点安装 cron，例如每 10 分钟执行一次：

```cron
*/10 * * * * /absolute/path/codex-slack-bridge/keepalive.sh
```

`keepalive.sh` 检查 supervisor 文件锁、活动的 overlap `srun` 和现有 Slurm job 后才
启动。独立 job 的资源参数通过 `sbatch` 命令传入，`bridge.sbatch` 本身不硬编码用户
路径、account 或 partition。脚本会补入本集群
常用的 `~/local/bin`、`~/.local/bin`、最新可用 NVM Node 和 Slurm 路径，并把同一 PATH
白名单传给 job；如果安装位置不同，请在 `.env` 中把 `CODEX_BIN` 设为绝对路径。

## 安全边界

这个 bridge 等价于给 allowlist 中的 Slack 账户一个受 sandbox 限制的远程 coding
入口。建议：

- 不要把 bot 加入频道；manifest 只订阅 DM/assistant 事件，代码也再次校验 DM 和用户 ID。
- 保持 `CODEX_SANDBOX=workspace-write` 或 `read-only`。只有在额外容器/VM 隔离后才考虑
  `danger-full-access`。
- `.env` 必须保持 mode 0600 且绝不提交。bridge 默认拒绝把源码、state 或凭据放入
  workspace-write 的 `CODEX_CWD`/`CODEX_ADD_DIRS`；`danger-full-access` 也必须显式
  opt-in。把运行副本移到工作区外可以阻止自修改，但同一 Unix UID 仍可能读取这些
  文件；真正隔离 Slack token 需要独立 Unix 用户、容器或 VM。
- 若用 `CODEX_API_KEY`，同样要把它视为可能被工作区命令读取的 secret。优先使用专用、
  最小权限凭据和隔离 runner。
- Slack 最终回答可能包含仓库信息。敏感仓库需要同时评估 Slack workspace 的数据策略。
- 定期轮换 Slack token，并检查 `state/undelivered.jsonl` 和日志权限。
- `undelivered.jsonl` 不会自动重放；人工确认后重发需要的内容，并为 outbox、bridge log
  和 Slurm log 配置站点自己的轮转/清理策略。

## 开发与验证

测试使用 fake Codex executable，不连接 Slack、不调用模型：

```bash
./scripts/check.sh
```

它按 `uv.lock` 安装锁定依赖，检查 shell 语法（本机有 ShellCheck 时也运行它），并运行
Ruff 和 pytest，覆盖 argv 构造、stdin prompt、
JSONL 解析、resume、失败、超时、取消、子进程 secret 隔离、配置校验、原子 session、
持久 inbox/去重、多 Slack 线程隔离、附件路由、全局 stop 和消息过滤。

Codex CLI 的接口选择依据官方的
[non-interactive mode](https://developers.openai.com/codex/noninteractive) 与
[CLI reference](https://developers.openai.com/codex/cli/reference)；官方云端 Slack 方案见
[Use Codex in Slack](https://developers.openai.com/codex/integrations/slack)。
