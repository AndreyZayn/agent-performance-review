#!/usr/bin/env python3
"""Small local-only store for a review run that can span several turns.

Only runtime/model identifiers, broad tool categories, and timestamps are kept.
Conversation content and raw tool names never enter this state file.
"""
import fcntl
import hashlib
import json
import os
import re
import secrets
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUN_DIR = Path(os.environ.get(
    "AGENT_REVIEW_RUN_DIR",
    Path.home() / ".codex" / "agent-performance-review" / "run_metadata",
))


def session_key(session_id, runtime="codex"):
    if not isinstance(session_id, str) or not session_id:
        return None
    material = session_id if runtime == "codex" else f"{runtime}:{session_id}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _path(key):
    return RUN_DIR / f"{key}.json"


def _closed_path(review_id):
    return RUN_DIR / "closed" / f"{review_id}.closed"


def _empty_state(session_id, generation=1, model="unknown", runtime="codex"):
    key = session_key(session_id, runtime)
    review_id = f"{runtime}-{key}-{generation}-{secrets.token_hex(4)}"
    return {
        "review_id": review_id,
        "generation": generation,
        "runtime": runtime,
        "agent": "unknown",
        "model": model if isinstance(model, str) and model.strip() else "unknown",
        "started_at": time.time(),
        "tool_categories": {},
        "tool_calls": 0,
    }


def _locked_state(session_id, model=None, update=None, runtime="codex", agent="unknown"):
    key = session_key(session_id, runtime)
    if not key:
        state = _empty_state(session_id, model=model or "unknown", runtime=runtime)
        if update:
            update(state)
        state["agent"] = agent or "unknown"
        return state

    RUN_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = _path(key)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "r+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            handle.seek(0)
            raw = handle.read()
            try:
                state = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                state = None
            if not isinstance(state, dict) or not isinstance(state.get("review_id"), str):
                state = _empty_state(session_id, model=model or "unknown", runtime=runtime)
            if _closed_path(state["review_id"]).exists():
                generation = int(state.get("generation", 0)) + 1
                state = _empty_state(session_id, generation, model or state.get("model", "unknown"), runtime)
            if model and isinstance(model, str) and model.strip() and state.get("model") == "unknown":
                state["model"] = model.strip()
            if agent and agent != "unknown":
                state["agent"] = agent
            if update:
                update(state)
            handle.seek(0)
            handle.truncate()
            json.dump(state, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
            return dict(state)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def record_tool(session_id, model, category, runtime="codex", agent="unknown"):
    if not isinstance(category, str) or not category:
        return read_run(session_id, model, runtime, agent)

    def add(state):
        counts = Counter(state.get("tool_categories", {}))
        counts[category] += 1
        state["tool_categories"] = dict(sorted(counts.items()))
        state["tool_calls"] = int(state.get("tool_calls", 0)) + 1

    return _locked_state(session_id, model, add, runtime, agent)


def read_run(session_id, model=None, runtime="codex", agent="unknown"):
    return _locked_state(session_id, model, runtime=runtime, agent=agent)


def close_run(review_id):
    if (not isinstance(review_id, str)
            or not re.fullmatch(r"(?:codex|claude-code|cursor|muse-code)-[a-f0-9]{32}-\d+-[a-f0-9]{8}", review_id)):
        return
    RUN_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    closed_dir = RUN_DIR / "closed"
    closed_dir.mkdir(mode=0o700, exist_ok=True)
    path = _closed_path(review_id)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(str(time.time()))


def metadata(session_id, model=None, runtime="codex", agent="unknown"):
    state = read_run(session_id, model, runtime, agent)
    elapsed = max(0, int(time.time() - float(state.get("started_at", time.time()))))
    return {
        "review_id": state["review_id"],
        "runtime": state.get("runtime", runtime),
        "agent": state.get("agent", agent),
        "model": state.get("model", "unknown"),
        "tool_categories": state.get("tool_categories", {}),
        "tool_calls": state.get("tool_calls", 0),
        "elapsed_time": elapsed,
        "loops": "unknown",
        "roles": "unknown",
        "tokens": "unknown",
        "cost": "unknown",
    }
