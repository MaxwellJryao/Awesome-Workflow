#!/usr/bin/env node
// Stop-hook wrapper for the Codex review gate that only invokes Codex when the
// working tree's uncommitted change-set actually changed since the last review.
//
// Rationale: the codex plugin's built-in Stop gate spins up a Codex review on
// EVERY stop (even pure-conversation turns) and relies on the LLM to answer
// ALLOW. This wrapper does a cheap local git pre-check first, so Codex is only
// spawned after real code changes. Disable the plugin's own gate
// (stopReviewGate=false) so this is the only Stop reviewer (no double-fire).
//
// Contract (Claude Code Stop hook): print nothing + exit 0 to allow stopping;
// print {"decision":"block","reason":"..."} to keep Claude working.

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { createHash } from "node:crypto";
import { execFileSync, spawnSync } from "node:child_process";

const PLUGIN_GLOB_ROOT = path.join(os.homedir(), ".claude", "plugins", "cache", "openai-codex", "codex");
const PERSISTENT_PLUGIN_DATA = path.join(os.homedir(), ".claude", "plugins", "data", "codex-openai-codex");
const STATE_DIR = path.join(os.homedir(), ".claude", "codex-review-state");
const REVIEW_TIMEOUT_MS = 15 * 60 * 1000;

function readHookInput() {
  try {
    const raw = fs.readFileSync(0, "utf8").trim();
    return raw ? JSON.parse(raw) : {};
  } catch {
    return {};
  }
}

function note(message) {
  if (message) process.stderr.write(`[codex-review-on-change] ${message}\n`);
}

function allow() {
  process.exit(0);
}

function block(reason) {
  process.stdout.write(`${JSON.stringify({ decision: "block", reason })}\n`);
  process.exit(0);
}

function git(repo, args) {
  return execFileSync("git", ["-C", repo, ...args], {
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
    stdio: ["ignore", "pipe", "ignore"]
  });
}

function resolveRepo(cwd) {
  try {
    return git(cwd, ["rev-parse", "--show-toplevel"]).trim();
  } catch {
    return null;
  }
}

// Fingerprint of the full uncommitted delta: tracked changes vs HEAD (content)
// plus the content of untracked, non-ignored files. Empty string => clean tree.
function changeFingerprint(repo) {
  let tracked = "";
  try {
    tracked = git(repo, ["-c", "core.fileMode=false", "diff", "HEAD", "--"]);
  } catch {
    // No HEAD yet (fresh repo): fall back to diff of the index against empty.
    try {
      tracked = git(repo, ["-c", "core.fileMode=false", "diff", "--"]);
    } catch {
      tracked = "";
    }
  }

  let untrackedList = [];
  try {
    untrackedList = git(repo, ["ls-files", "--others", "--exclude-standard", "-z"])
      .split("\0")
      .filter(Boolean);
  } catch {
    untrackedList = [];
  }

  const h = createHash("sha256");
  h.update("tracked\0");
  h.update(tracked);
  for (const rel of untrackedList.sort()) {
    h.update(`\0untracked\0${rel}\0`);
    try {
      h.update(fs.readFileSync(path.join(repo, rel)));
    } catch {
      h.update("<unreadable>");
    }
  }

  if (!tracked && untrackedList.length === 0) return "";
  return h.digest("hex");
}

function stateFileFor(repo) {
  const key = createHash("sha256").update(repo).digest("hex").slice(0, 16);
  return path.join(STATE_DIR, `${key}.last`);
}

function readLast(repo) {
  try {
    return fs.readFileSync(stateFileFor(repo), "utf8").trim();
  } catch {
    return "";
  }
}

function writeLast(repo, fingerprint) {
  try {
    fs.mkdirSync(STATE_DIR, { recursive: true });
    fs.writeFileSync(stateFileFor(repo), `${fingerprint}\n`, "utf8");
  } catch (error) {
    note(`could not persist review fingerprint: ${error.message}`);
  }
}

function locatePlugin() {
  let versions = [];
  try {
    versions = fs
      .readdirSync(PLUGIN_GLOB_ROOT, { withFileTypes: true })
      .filter((d) => d.isDirectory())
      .map((d) => d.name)
      .sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  } catch {
    return null;
  }
  for (const v of versions.reverse()) {
    const script = path.join(PLUGIN_GLOB_ROOT, v, "scripts", "codex-companion.mjs");
    if (fs.existsSync(script)) {
      return { script, root: path.join(PLUGIN_GLOB_ROOT, v) };
    }
  }
  return null;
}

function buildPrompt(pluginRoot, lastAssistantMessage) {
  const templatePath = path.join(pluginRoot, "prompts", "stop-review-gate.md");
  const block = lastAssistantMessage
    ? `Previous Claude response:\n${lastAssistantMessage}`
    : "";
  let template;
  try {
    template = fs.readFileSync(templatePath, "utf8");
  } catch {
    template =
      "Review the uncommitted code changes in this repository. " +
      "First line must be exactly `ALLOW: <reason>` or `BLOCK: <reason>`.\n{{CLAUDE_RESPONSE_BLOCK}}";
  }
  return template.split("{{CLAUDE_RESPONSE_BLOCK}}").join(block);
}

function parseVerdict(rawOutput) {
  const text = String(rawOutput ?? "").trim();
  if (!text) return { ok: false, reason: null, unexpected: true };
  const first = text.split(/\r?\n/, 1)[0].trim();
  if (first.startsWith("ALLOW:")) return { ok: true };
  if (first.startsWith("BLOCK:")) {
    const reason = first.slice("BLOCK:".length).trim() || text;
    return { ok: false, reason };
  }
  return { ok: false, reason: null, unexpected: true };
}

function main() {
  const input = readHookInput();
  const cwd = input.cwd || process.env.CLAUDE_PROJECT_DIR || process.cwd();
  const repo = resolveRepo(cwd);
  if (!repo) allow(); // not a git repo -> nothing to gate on

  const fingerprint = changeFingerprint(repo);
  if (!fingerprint) allow(); // clean tree -> pure conversation, no review

  if (fingerprint === readLast(repo)) allow(); // unchanged since last review

  if (process.env.CODEX_REVIEW_ONCHANGE_DRYRUN) {
    note(`[dryrun] would run Codex review (fingerprint=${fingerprint.slice(0, 12)})`);
    writeLast(repo, fingerprint);
    allow();
  }

  const plugin = locatePlugin();
  if (!plugin) {
    note("codex plugin not found; skipping review (allowing stop).");
    allow();
  }

  const prompt = buildPrompt(plugin.root, String(input.last_assistant_message ?? "").trim());
  const result = spawnSync(
    process.execPath,
    [plugin.script, "task", "--json", prompt],
    {
      cwd: repo,
      env: {
        ...process.env,
        CLAUDE_PLUGIN_DATA: process.env.CLAUDE_PLUGIN_DATA || PERSISTENT_PLUGIN_DATA,
        ...(input.session_id ? { CODEX_COMPANION_SESSION_ID: input.session_id } : {})
      },
      encoding: "utf8",
      timeout: REVIEW_TIMEOUT_MS
    }
  );

  // Record that we reviewed this exact change-set so we don't re-review it.
  writeLast(repo, fingerprint);

  if (result.error) {
    note(`Codex review could not run (${result.error.code || result.error.message}); allowing stop.`);
    allow();
  }
  if (result.status !== 0) {
    note(`Codex review exited ${result.status}; allowing stop. ${String(result.stderr || "").trim()}`);
    allow();
  }

  let payload;
  try {
    payload = JSON.parse(result.stdout);
  } catch {
    note("Codex review returned non-JSON; allowing stop.");
    allow();
  }

  const verdict = parseVerdict(payload?.rawOutput);
  if (verdict.ok) allow();
  if (verdict.unexpected || !verdict.reason) {
    note("Codex review verdict was unexpected; allowing stop. Run /codex:review to inspect.");
    allow();
  }
  block(`Codex stop-time review found issues to fix before stopping: ${verdict.reason}`);
}

try {
  main();
} catch (error) {
  note(`internal error, allowing stop: ${error instanceof Error ? error.message : String(error)}`);
  process.exit(0);
}
