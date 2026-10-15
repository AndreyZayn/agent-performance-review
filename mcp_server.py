#!/usr/bin/env python3
"""Local Agent Performance Review MCP server (stdio, standard library only).

stdout carries newline-delimited JSON-RPC messages only; diagnostics go to stderr.
Reviews are appended to reviews.jsonl beside this file, created on first save.
"""
import fcntl
import http.server
import json
import math
import secrets
import re
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from run_state import close_run

LEDGER = Path(__file__).resolve().parent / "reviews.jsonl"
SERVER_INFO = {"name": "agent-performance-review", "version": "0.4.0"}
PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
SCOPE = "local-only: this machine's reviews.jsonl; not community or benchmark data"

JOB_TYPES = ["writing", "research", "coding", "other"]
VERIFICATION = ["verified", "partial", "unverified", "unknown"]
RATINGS = ["overall", "quality", "speed", "resource_efficiency"]
CONTEXT_KEYS = ["runtime", "agent", "model", "connections", "loops", "roles"]
RESOURCE_KEYS = ["elapsed_time", "tokens", "cost", "tool_calls"]
COMPLETION = ["yes", "no"]
GAP_REASONS = ["incomplete", "missed_brief", "incorrect", "missing_tool"]
UI_DIR = Path(__file__).resolve().parent / "rating-popup"
RATING_LINK_TTL_SECONDS = 24 * 60 * 60
PENDING_REVIEWS = {}
PENDING_REVIEWS_LOCK = threading.Lock()
RATING_SERVER = None
RATING_SERVER_URL = None
SUMMARY_FIELDS = {"task": "What the user needed done.",
                  "action": "What the agent actually did.",
                  "result": "What was delivered."}
MAX_TEXT = 500
MAX_VALUE = 200
REVIEW_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

VALUE_SCHEMA = {"type": ["string", "number"], "description": "Known value, or the string 'unknown'."}
TOOLS = [
    {
        "name": "request_rating",
        "description": "Create or refresh a clickable local rating link for the active task/run. Repeated calls with the same review_id update the snapshot so links from earlier turns show the latest cumulative run summary. The page saves a submitted review locally.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["review_id", "job_type", "task", "action", "result", "verification"],
            "properties": {
                "review_id": {"type": "string", "pattern": REVIEW_ID.pattern},
                "job_type": {"type": "string", "enum": JOB_TYPES},
                **{k: {"type": "string", "minLength": 1, "maxLength": MAX_TEXT, "description": d}
                   for k, d in SUMMARY_FIELDS.items()},
                "verification": {"type": "string", "enum": VERIFICATION},
                "context": {"type": "object", "additionalProperties": False,
                            "properties": {k: VALUE_SCHEMA for k in CONTEXT_KEYS}},
                "resources": {"type": "object", "additionalProperties": False,
                              "properties": {k: VALUE_SCHEMA for k in RESOURCE_KEYS}},
            },
        },
    },
    {
        "name": "record_review",
        "description": "Save one local agent performance review with a yes/no outcome and four 1-5 star ratings. "
                       "Retrying the same review_id with identical content is safe.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["review_id", "job_type", "task", "action", "result", "verification",
                         "completion", "ratings"],
            "properties": {
                "review_id": {"type": "string", "pattern": REVIEW_ID.pattern,
                              "description": "Stable caller-supplied ID for this review."},
                "job_type": {"type": "string", "enum": JOB_TYPES},
                **{k: {"type": "string", "minLength": 1, "maxLength": MAX_TEXT, "description": d}
                   for k, d in SUMMARY_FIELDS.items()},
                "verification": {"type": "string", "enum": VERIFICATION},
                "completion": {"type": "string", "enum": COMPLETION},
                "gap_reasons": {"type": "array", "items": {"type": "string", "enum": GAP_REASONS},
                                "uniqueItems": True, "maxItems": 4},
                "context": {"type": "object", "additionalProperties": False,
                            "properties": {k: VALUE_SCHEMA for k in CONTEXT_KEYS}},
                "resources": {"type": "object", "additionalProperties": False,
                              "properties": {k: VALUE_SCHEMA for k in RESOURCE_KEYS}},
                "ratings": {"type": "object", "additionalProperties": False, "required": RATINGS,
                            "properties": {k: {"type": "integer", "minimum": 1, "maximum": 5}
                                           for k in RATINGS}},
            },
        },
    },
    {
        "name": "review_summary",
        "description": "Local-only count and mean ratings from this machine's review ledger, "
                       "grouped by job type. Not community data.",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"job_type": {"type": "string", "enum": JOB_TYPES}},
        },
    },
]


class ToolError(Exception):
    pass


class UnknownTool(Exception):
    pass


def log(message):
    print(f"agent-performance-review: {message}", file=sys.stderr, flush=True)


def check_keys(obj, name, allowed, required=()):
    if not isinstance(obj, dict):
        raise ToolError(f"{name} must be an object")
    extra = sorted(set(obj) - set(allowed))
    if extra:
        raise ToolError(f"{name} has unknown fields: {', '.join(extra)}")
    missing = [k for k in required if k not in obj]
    if missing:
        raise ToolError(f"{name} is missing: {', '.join(missing)}")


def check_values(obj, name, keys):
    obj = {} if obj is None else obj
    check_keys(obj, name, keys)
    out = {}
    for key in keys:
        value = obj.get(key, "unknown")
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise ToolError(f"{name}.{key} must be a string or number")
        if isinstance(value, float) and not math.isfinite(value):
            raise ToolError(f"{name}.{key} must be finite")
        if isinstance(value, (int, float)) and value < 0:
            raise ToolError(f"{name}.{key} cannot be negative")
        if isinstance(value, str):
            value = value.strip() or "unknown"
            if len(value) > MAX_VALUE:
                raise ToolError(f"{name}.{key} exceeds {MAX_VALUE} characters")
        out[key] = value
    return out


def validate_review_context(args):
    fields = ["review_id", "job_type", *SUMMARY_FIELDS, "verification", "context", "resources"]
    check_keys(args, "arguments", fields,
               ["review_id", "job_type", *SUMMARY_FIELDS, "verification"])
    review_id = args["review_id"]
    if not isinstance(review_id, str) or not REVIEW_ID.match(review_id):
        raise ToolError("review_id must be 1-128 characters of letters, digits, . _ : -")
    if args["job_type"] not in JOB_TYPES:
        raise ToolError(f"job_type must be one of: {', '.join(JOB_TYPES)}")
    if args["verification"] not in VERIFICATION:
        raise ToolError(f"verification must be one of: {', '.join(VERIFICATION)}")
    review = {"review_id": review_id, "job_type": args["job_type"]}
    for key in SUMMARY_FIELDS:
        value = args[key]
        if not isinstance(value, str) or not value.strip():
            raise ToolError(f"{key} must be a non-empty string")
        if len(value) > MAX_TEXT:
            raise ToolError(f"{key} exceeds {MAX_TEXT} characters; send a short summary, not a transcript")
        review[key] = value.strip()
    review["verification"] = args["verification"]
    review["context"] = check_values(args.get("context"), "context", CONTEXT_KEYS)
    review["resources"] = check_values(args.get("resources"), "resources", RESOURCE_KEYS)
    return review


def validate_review(args):
    fields = ["review_id", "job_type", *SUMMARY_FIELDS, "verification", "completion",
              "gap_reasons", "context", "resources", "ratings"]
    check_keys(args, "arguments", fields,
               ["review_id", "job_type", *SUMMARY_FIELDS, "verification", "completion", "ratings"])
    review = validate_review_context({k: args[k] for k in fields if k in args
                                      and k in {"review_id", "job_type", *SUMMARY_FIELDS,
                                                "verification", "context", "resources"}})
    if args["completion"] not in COMPLETION:
        raise ToolError("completion must be yes or no")
    review["completion"] = args["completion"]
    reasons = args.get("gap_reasons", [])
    if not isinstance(reasons, list) or len(reasons) > 4:
        raise ToolError("gap_reasons must be a list with at most four choices")
    if (any(not isinstance(reason, str) or reason not in GAP_REASONS for reason in reasons)
            or len(set(reasons)) != len(reasons)):
        raise ToolError(f"gap_reasons must be unique choices from: {', '.join(GAP_REASONS)}")
    if review["completion"] == "yes" and reasons:
        raise ToolError("gap_reasons are only valid when completion is no")
    review["gap_reasons"] = reasons
    ratings = args["ratings"]
    check_keys(ratings, "ratings", RATINGS, RATINGS)
    for key in RATINGS:
        value = ratings[key]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
            raise ToolError(f"ratings.{key} must be an integer from 1 to 5")
    review["ratings"] = {k: ratings[k] for k in RATINGS}
    return review


def pending_review(token):
    with PENDING_REVIEWS_LOCK:
        item = PENDING_REVIEWS.get(token)
        if item and item["expires_at"] <= time.time():
            del PENDING_REVIEWS[token]
            return None
        return item["review"] if item else None


def remove_pending_review(token):
    with PENDING_REVIEWS_LOCK:
        item = PENDING_REVIEWS.pop(token, None)
        if item:
            review_id = item["review"].get("review_id")
            for other_token in [key for key, value in PENDING_REVIEWS.items()
                                if value["review"].get("review_id") == review_id]:
                del PENDING_REVIEWS[other_token]
        return item


class RatingHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args):
        return

    def send_bytes(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; style-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def route(self):
        path = self.path.split("?", 1)[0].strip("/").split("/")
        if len(path) < 2 or path[0] != "review":
            return None, None
        return path[1], "/".join(path[2:])

    def local_request(self):
        expected_host = f"127.0.0.1:{self.server.server_port}"
        origin = self.headers.get("Origin")
        return (self.client_address[0] == "127.0.0.1"
                and self.headers.get("Host") == expected_host
                and (origin is None or origin == f"http://{expected_host}"))

    def do_GET(self):
        if not self.local_request():
            self.send_error(403)
            return
        token, suffix = self.route()
        review = pending_review(token) if token else None
        if review is None:
            self.send_error(404, "This rating link is invalid or has expired")
            return
        if suffix in ("", "index.html"):
            file_name, content_type = "index.html", "text/html; charset=utf-8"
        elif suffix == "styles.css":
            file_name, content_type = "styles.css", "text/css; charset=utf-8"
        elif suffix == "app.js":
            file_name, content_type = "app.js", "text/javascript; charset=utf-8"
        elif suffix == "api/review":
            body = json.dumps(review, ensure_ascii=False).encode("utf-8")
            self.send_bytes(200, "application/json; charset=utf-8", body)
            return
        else:
            self.send_error(404)
            return
        try:
            body = (UI_DIR / file_name).read_bytes()
        except OSError:
            self.send_error(503, "Rating page files are unavailable")
            return
        self.send_bytes(200, content_type, body)

    def do_POST(self):
        if not self.local_request():
            self.send_error(403)
            return
        token, suffix = self.route()
        review = pending_review(token) if token else None
        if suffix != "api/submit" or review is None:
            self.send_error(404, "This rating link is invalid or has expired")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 4096:
                raise ValueError("invalid size")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Invalid rating response")
            if payload == {"skip": True}:
                remove_pending_review(token)
                close_run(review["review_id"])
                result = {"status": "skipped", "review_id": review["review_id"]}
            else:
                if set(payload) - {"completion", "gap_reasons", "ratings"}:
                    raise ValueError("Unexpected rating fields")
                result = record_review({**review, **payload})
                remove_pending_review(token)
                result = {"status": "saved", "review_id": result["review_id"]}
        except (ValueError, TypeError, json.JSONDecodeError, ToolError) as exc:
            body = json.dumps({"error": str(exc)}).encode("utf-8")
            self.send_bytes(400, "application/json; charset=utf-8", body)
            return
        body = json.dumps(result).encode("utf-8")
        self.send_bytes(200, "application/json; charset=utf-8", body)


def ensure_rating_server():
    global RATING_SERVER, RATING_SERVER_URL
    if RATING_SERVER is not None:
        return RATING_SERVER_URL
    try:
        RATING_SERVER = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RatingHandler)
        RATING_SERVER.daemon_threads = True
        RATING_SERVER_URL = f"http://127.0.0.1:{RATING_SERVER.server_port}"
        thread = threading.Thread(target=RATING_SERVER.serve_forever, daemon=True)
        thread.start()
    except OSError as exc:
        RATING_SERVER = None
        RATING_SERVER_URL = None
        raise ToolError(f"Could not start the local rating page: {exc}") from exc
    return RATING_SERVER_URL


def request_rating(args):
    review = validate_review_context(args)
    if not (UI_DIR / "index.html").is_file():
        raise ToolError("The local rating page files are missing")
    base_url = ensure_rating_server()
    token = secrets.token_urlsafe(24)
    with PENDING_REVIEWS_LOCK:
        now = time.time()
        for old_token in [key for key, item in PENDING_REVIEWS.items()
                          if item["expires_at"] <= now]:
            del PENDING_REVIEWS[old_token]
        # Every turn offers a link to the same active run. Refresh existing links to
        # the newest cumulative card so even an earlier message opens current evidence.
        for item in PENDING_REVIEWS.values():
            if item["review"].get("review_id") == review["review_id"]:
                item["review"] = review
                item["expires_at"] = now + RATING_LINK_TTL_SECONDS
        PENDING_REVIEWS[token] = {
            "review": review,
            "expires_at": now + RATING_LINK_TTL_SECONDS,
        }
    return {
        "status": "ready",
        "review_id": review["review_id"],
        "rating_url": f"{base_url}/review/{token}/",
        "expires_in_seconds": RATING_LINK_TTL_SECONDS,
        "scope": SCOPE,
    }


def read_ledger(handle):
    handle.seek(0)
    records = []
    for number, line in enumerate(handle, 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
            if isinstance(record, dict):
                records.append(record)
                continue
        except json.JSONDecodeError:
            pass
        log(f"skipping malformed ledger line {number}")
    return records


def record_review(args):
    review = validate_review(args)
    with open(LEDGER, "a+", encoding="utf-8") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        for record in read_ledger(handle):
            if record.get("review_id") != review["review_id"]:
                continue
            stored = {k: record.get(k) for k in review}
            if stored == review:
                close_run(review["review_id"])
                return {"status": "already_recorded", "review_id": review["review_id"],
                        "recorded_at": record.get("recorded_at"), "scope": SCOPE}
            raise ToolError(f"review_id {review['review_id']} already exists with different content")
        record = {**review, "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "scope": "local"}
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        handle.flush()
    close_run(review["review_id"])
    return {"status": "recorded", "review_id": review["review_id"],
            "recorded_at": record["recorded_at"], "ledger": str(LEDGER), "scope": SCOPE}


def review_summary(args):
    check_keys(args, "arguments", ["job_type"])
    job_type = args.get("job_type")
    if job_type is not None and job_type not in JOB_TYPES:
        raise ToolError(f"job_type must be one of: {', '.join(JOB_TYPES)}")
    records = []
    if LEDGER.exists():
        with open(LEDGER, encoding="utf-8") as handle:
            fcntl.flock(handle, fcntl.LOCK_SH)
            records = read_ledger(handle)
    groups = {}
    for record in records:
        ratings = record.get("ratings")
        kind = record.get("job_type")
        if kind not in JOB_TYPES or (job_type and kind != job_type) or not isinstance(ratings, dict):
            continue
        if not all(isinstance(ratings.get(k), int) and not isinstance(ratings.get(k), bool)
                   for k in RATINGS):
            continue
        groups.setdefault(kind, []).append(record)

    def summarize(items):
        score_rows = [item["ratings"] for item in items]
        means = {k: round(sum(row[k] for row in score_rows) / len(score_rows), 2)
                 for k in RATINGS} if score_rows else None
        outcomes = [item.get("completion") for item in items if item.get("completion") in COMPLETION]
        completion_rate = round(outcomes.count("yes") / len(outcomes), 2) if outcomes else None
        return {"count": len(items), "completion_rate": completion_rate, "mean_ratings": means}

    every = [item for items in groups.values() for item in items]
    return {"scope": SCOPE, "job_type_filter": job_type or "all", **summarize(every),
            "by_job_type": {k: summarize(groups[k]) for k in JOB_TYPES if k in groups}}


HANDLERS = {"request_rating": request_rating, "record_review": record_review, "review_summary": review_summary}


def handle(method, params):
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": TOOLS}
    if method == "tools/call":
        name = params.get("name")
        if name not in HANDLERS:
            raise UnknownTool(f"Unknown tool: {name}")
        args = params.get("arguments") or {}
        try:
            data = HANDLERS[name](args)
        except ToolError as error:
            return {"content": [{"type": "text", "text": f"Error: {error}"}], "isError": True}
        return {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                "structuredContent": data, "isError": False}
    raise NotImplementedError(method)


def send(message):
    sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def error(msg_id, code, message):
    send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


def main():
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            error(None, -32700, "Parse error")
            continue
        if not isinstance(message, dict) or not isinstance(message.get("method"), str):
            if isinstance(message, dict) and "method" not in message and ("result" in message or "error" in message):
                continue  # response to a server request; this server sends none
            error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid Request")
            continue
        method, msg_id, is_request = message["method"], message.get("id"), "id" in message
        params = message.get("params") or {}
        if not is_request:
            if method not in ("notifications/initialized", "notifications/cancelled"):
                log(f"ignoring notification {method}")
            continue
        if not isinstance(params, dict):
            error(msg_id, -32602, "params must be an object")
            continue
        try:
            send({"jsonrpc": "2.0", "id": msg_id, "result": handle(method, params)})
        except NotImplementedError:
            error(msg_id, -32601, f"Method not found: {method}")
        except UnknownTool as exc:
            error(msg_id, -32602, str(exc))
        except Exception as exc:  # keep the server alive; details to stderr only
            log(f"internal error handling {method}: {exc!r}")
            error(msg_id, -32603, "Internal error")


if __name__ == "__main__":
    main()
