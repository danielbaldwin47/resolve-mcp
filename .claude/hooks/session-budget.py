#!/usr/bin/env python3
"""Stop hook that puts a session's cost in its closing report (#247).

Reads the session transcript and, when the model has just written a closing
report (a message carrying a `result:` / `failed:` / `needs input:` line) that
lacks a `turns=N peak=Nk` line, blocks the stop once with the computed line so
the model appends it. Everything else — conversational replies, a report that
already carries the line, the second stop after a block (`stop_hook_active`),
an unreadable transcript — passes silently.

  turns  distinct assistant messages (one API call each; the transcript writes
         one row per content block, so rows over-count)
  peak   max context on any assistant call: input + cache-creation + cache-read
         tokens, in k. Sidechain (subagent) rows are excluded — they are the
         subagent's context, not the session's.

Measured motivation (2026-08 workflow audit): only 49% of sessions peaked at
or under 120k; peaks track turn count and nothing recorded either where the
human reads the outcome.
"""
import json
import re
import sys

BUDGET_LINE = re.compile(r"\bturns=\d+ peak=\d+k\b")
CLOSING_MARKER = re.compile(r"^(?:result|failed|needs input):", re.MULTILINE)


def read_transcript(path):
    """Yield the parsed rows of a JSONL transcript, skipping malformed lines."""
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except ValueError:
                continue


def measure(rows):
    """Return (turns, peak_tokens, last_assistant_text) for the main session."""
    ids = set()
    peak = 0
    last_id = None
    last_text = []
    for row in rows:
        if not isinstance(row, dict) or row.get("type") != "assistant":
            continue
        if row.get("isSidechain"):
            continue
        message = row.get("message")
        if not isinstance(message, dict) or not message:
            continue
        # A message with no id is a broken row; still an assistant turn.
        msg_id = message.get("id") or id(row)
        ids.add(msg_id)
        usage = message.get("usage") or {}
        if isinstance(usage, dict):
            context = sum(
                int(usage.get(k) or 0)
                for k in (
                    "input_tokens",
                    "cache_creation_input_tokens",
                    "cache_read_input_tokens",
                )
            )
            peak = max(peak, context)
        if msg_id != last_id:
            last_id = msg_id
            last_text = []
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    last_text.append(str(block.get("text") or ""))
        elif isinstance(content, str):
            last_text.append(content)
    return len(ids), peak, "\n".join(last_text)


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    if not isinstance(data, dict) or data.get("hook_event_name") != "Stop":
        return
    if data.get("stop_hook_active"):
        return
    path = data.get("transcript_path")
    if not path:
        return
    try:
        turns, peak, last_text = measure(read_transcript(path))
    except OSError:
        return
    if turns == 0:
        return
    if not CLOSING_MARKER.search(last_text) or BUDGET_LINE.search(last_text):
        return
    line = f"turns={turns} peak={round(peak / 1000)}k"
    reason = (
        "Session budget (CLAUDE.md): your closing report is missing its cost "
        f"line. Reply with exactly two lines and stop: your report's `result:` "
        "/ `failed:` / `needs input:` line repeated verbatim, then "
        f"`{line}`."
    )
    json.dump({"decision": "block", "reason": reason}, sys.stdout)


if __name__ == "__main__":
    main()
