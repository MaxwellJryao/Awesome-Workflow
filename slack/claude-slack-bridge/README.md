# Claude Slack Bridge

将唯一允许的 Slack 用户私信串行转发给本机 Claude Code，并把工具进度和最终回答
发回 Slack。启用 Slack Agents & AI Apps 后，每个 **New Chat** 都会映射到独立的
Claude 会话；会话映射保存在本地且不会提交到 Git。

## 快速开始

前置条件：Python 3.11+、`uv`、已安装并登录的 Claude Code CLI，以及可访问 Slack
和 Anthropic API 的运行节点。

1. 在 Slack API 中通过 `slack-app-manifest.yaml` 创建或更新 App。
2. 复制配置并填写 token、唯一允许用户和 Claude 工作目录：

   ```bash
   cp .env.example .env
   chmod 600 .env
   ```

3. 前台启动 supervisor：

   ```bash
   ./run.sh
   ```

集群环境可先运行 `./test_node.sh` 检查依赖和网络，再用 `./keepalive.sh` 复用已有
Slurm allocation 或提交独立 job。`test_sdk.py` 会实际启动 Claude 会话并产生 API
调用，只应在需要诊断 SDK 时手动运行。

## 安全提示

- `.env`、Slack 附件、日志和会话映射均为本地敏感数据，已由 `.gitignore` 排除。
- 默认 `CLAUDE_PERMISSION_MODE=bypassPermissions`，等价于跳过权限确认。仅应在受信任、
  隔离的工作目录中使用；需要更保守的行为时改成 `acceptEdits`。
- 部署 bridge 时应让它位于 `CLAUDE_CWD` 之外，避免 Claude 修改下一次重启会执行的
  bridge 源码。
