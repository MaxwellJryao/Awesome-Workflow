# (Maybe) Simple and Efficient Workflow Configuration

This project focuses on simplifying environment setup and collecting small tools that
make development workflows more efficient.

## Contents

- [`shell/`](shell/): shell setup and examples.
- [`git/`](git/): Git workflow notes.
- [`gpu/`](gpu/): GPU allocation and monitoring helpers.
- [`utils/`](utils/): general-purpose utilities.
- [`slack/codex-slack-bridge/`](slack/codex-slack-bridge/): forward allowlisted
  Slack DMs to a local Codex CLI session.
- [`slack/claude-slack-bridge/`](slack/claude-slack-bridge/): forward allowlisted
  Slack DMs to a local Claude Code session.

The Slack bridges intentionally exclude local `.env` files, credentials, virtual
environments, logs, uploaded attachments, and persisted session state. Start from each
bridge's `.env.example` when deploying it.
