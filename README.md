# Agent Performance Review — Local Integrations

Public source for the local runtime integrations and review flow. The code is experimental; it is published for inspection and development, not as a supported or operational release.

## Contents

- `capture_metadata_hook.py`, `runtime_hook.py`, `stop_hook.py`, `run_state.py`, and `hooks.json` — prototype runtime capture and hook adapters.
- `mcp_server.py` — local MCP server and loopback-only review page server.
- `rating-popup/` — local rating form.
- `skill/agent-performance-review/` — Codex skill source.

## Current status

The Codex review hooks were removed from the active user configuration. This checkout is not installed in any runtime. Repeated end-of-turn links, cumulative metadata capture, and cross-runtime setup have not passed real end-to-end validation. Do not treat the hook adapters as operational.

The prototype is designed to keep reviews on the user's computer. It must not collect prompts, responses, tool arguments or outputs, file paths, or document contents. Runtime-reported token and cost data remain unknown when the host does not provide reliable measurements.

## Local MCP setup

The MCP server uses Python's standard library. Configure an MCP-capable host to launch `mcp_server.py` as a stdio server, using the absolute path to this checkout. Host configuration formats differ; this repository does not yet provide a verified installer for each runtime. The review page is served on loopback while the MCP process is running.

Example command and argument values for an MCP client configuration:

```json
{
  "command": "python3",
  "args": ["/absolute/path/to/agent-performance-review/mcp_server.py"]
}
```

Hook adapters require runtime-specific configuration and explicit validation before use. `hooks.json` is a prototype reference, not an active Codex registration.

## Privacy and local data

Submitted reviews are written to `reviews.jsonl` beside the server and are ignored by Git. Never commit review ledgers, runtime state, credentials, prompts, responses, tool arguments or outputs, paths, or document contents. Sharing is not implemented by this local prototype.

## Release decisions

This project is distributed under the MIT License. Copyright (c) 2026 Andrey Zaynulin. See `LICENSE` for the full terms.
