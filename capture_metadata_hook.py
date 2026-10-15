#!/usr/bin/env python3
"""Capture broad, content-free tool categories across turns in one active run."""
import json
import sys

from run_state import record_tool


def tool_category(name):
    value = name.lower() if isinstance(name, str) else ""
    if value.startswith(("mcp__agent_performance_review", "mcp__agent-performance-review")):
        return None
    if value in {"bash", "exec_command", "write_stdin"}:
        return "local actions"
    if value in {"apply_patch", "edit", "write"}:
        return "file changes"
    if "search" in value or "browser" in value or value.startswith("web"):
        return "research"
    if "calendar" in value:
        return "calendar"
    if any(word in value for word in ("drive", "docs", "sheets", "notion")):
        return "documents"
    if any(word in value for word in ("slack", "gmail", "email", "teams")):
        return "messaging"
    if any(word in value for word in ("github", "gitlab", "repository")):
        return "code repository"
    if "collaboration" in value or value == "agent":
        return "agent delegation"
    if value.startswith("mcp__"):
        return "connected apps"
    return "other tools"


def capture_event(event):
    if not isinstance(event, dict) or event.get("hook_event_name") != "PostToolUse":
        return
    category = tool_category(event.get("tool_name"))
    if category is None:
        return
    record_tool(event.get("session_id"), event.get("model"), category)


def main():
    try:
        capture_event(json.load(sys.stdin))
    except (json.JSONDecodeError, OSError, TypeError):
        return


if __name__ == "__main__":
    main()
