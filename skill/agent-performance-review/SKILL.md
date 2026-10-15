---
name: agent-performance-review
description: "At the end of every turn, refresh a one-click local rating link for the active task/run. The user opens the compact rating page when ready."
---

# Agent Performance Review

Use at the end of every assistant turn when invoked by the Stop hook, and when directly requested by the user. The link always represents the full active task/run, including earlier turns and linked sub-agent work where visible.

## 1. Build a short evidence-based run card

Use visible conversation evidence for short, privacy-safe **Task**, **Action**, and **Result** summaries of the full active run. Keep each under 500 characters. Generalize names, customer details, URLs, file paths, and document-specific content. Do not read the transcript file, tool arguments or outputs, filenames, or document contents to fill gaps. If a detail is unavailable, use `unknown`; do not infer it.

Use only the hook's content-free metadata for automatic context:

- `model`: model slug supplied by Codex, or `unknown`.
- `connections`: broad tool-category counts supplied by the local PostToolUse hook. Never include raw tool or connection names.
- `tool_calls`: total observed tool-call count.
- `elapsed_time`: approximate wall-clock time from the first observed tool call in the active run through this turn. Label it approximate.
- `tokens` and `cost`: use runtime-reported usage only when available; otherwise `unknown`. Do not tokenize a summary and present it as total run usage.
- `loops` and `roles`: `unknown` unless they are explicitly visible in the conversation.
- `review_id`: stable ID supplied by the hook and reused across turns in the active run. The local MCP refreshes earlier links to the newest snapshot.

Do not inspect a transcript or collect user prompts, tool data, paths, or document contents. The hook stores only the model and broad tool category for each observed tool call; it ignores review-tool calls. No metadata or review is uploaded.

## 2. Create the one-click rating link

Call local MCP `request_rating` with `review_id`, `job_type`, the brief `task`, `action`, and `result`, `verification` (`verified`, `partial`, `unverified`, or `unknown`), and the known `context` and `resources` fields. Keep `job_type` to `writing`, `research`, `coding`, or `other`. Use cumulative metadata across turns and label elapsed time approximate. The tool creates a random, loopback-only URL and returns immediately; it does not open the browser or save a review.

End the assistant response with exactly one Markdown link, using the returned URL:

`[The task is done. Review my agent.](http://127.0.0.1:PORT/review/...)`

Do not show the run card or ask for scores in chat. The user clicks the link to open the rating page, which already contains the run summary and available context. The first question is whether the agent finished the job; No reveals optional reason chips. Then the page collects four 1–5 star ratings: overall, quality, speed, and resource efficiency. There are no text fields.

If link creation fails, give a short error instead of fabricating a link. A link is valid while the local MCP server is running and expires after 24 hours.

## 3. Save from the page only after submission

The rating page posts a submitted review to the local MCP server, which writes it to the local JSONL ledger. Skip saves nothing. Do not call `record_review` after creating a link; no rating exists until the user submits in the page.

Reusing a review ID with identical content is a safe retry; use a new ID for a different review.

## 4. Local summary only on request

Call `review_summary` only when the user asks about saved reviews. Describe results as a local-only count and averages from this computer. Do not present them as community ratings or infer why a setup scored better.

This prototype stores reviews locally only. No Clerk, network service, public feed, or user account is part of it.
