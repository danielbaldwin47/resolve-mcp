"""Fake-tier tests for the ``Stop`` session-budget hook in ``.claude/hooks/``.

Like the guards, the hook is executable config: it runs as a subprocess with the
Stop payload on stdin and answers with a JSON decision on stdout. That process
boundary is the seam, so these tests drive the real script the way the harness
does, against a fixture transcript written per test — no import, no
monkeypatching.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[1] / ".claude" / "hooks" / "session-budget.py"

BUDGET_LINE = re.compile(r"turns=(\d+) peak=(\d+)k")


def run_hook_raw(stdin: str) -> subprocess.CompletedProcess[str]:
    """Invoke the hook exactly as the harness does: stdin in, JSON out.

    ``SYSTEMROOT`` is what a bare Python needs to start on Windows.
    """
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=stdin,
        capture_output=True,
        text=True,
        env={
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
            "PATH": "",
        },
    )


def run_hook(payload: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return run_hook_raw(json.dumps(payload))


def stop_event(transcript: Path, **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "session_id": "s1",
        "hook_event_name": "Stop",
        "transcript_path": str(transcript),
        "stop_hook_active": False,
    }
    payload.update(extra)
    return payload


def assistant_row(
    msg_id: str,
    block: dict[str, object],
    context: int,
    **extra: object,
) -> dict[str, object]:
    """One transcript line for an assistant message.

    The real transcript writes one line per content block, all sharing the
    message id, so a message with thinking + text + tool_use spans three rows.
    ``context`` is split across the three usage fields the way the API reports
    them, to prove the hook sums all three rather than reading one.
    """
    row: dict[str, object] = {
        "type": "assistant",
        "message": {
            "id": msg_id,
            "role": "assistant",
            "content": [block],
            "usage": {
                "input_tokens": context // 3,
                "cache_creation_input_tokens": context // 3,
                "cache_read_input_tokens": context - 2 * (context // 3),
                "output_tokens": 10,
            },
        },
    }
    row.update(extra)
    return row


def text(s: str) -> dict[str, object]:
    return {"type": "text", "text": s}


def tool_use() -> dict[str, object]:
    return {"type": "tool_use", "id": "t", "name": "Bash", "input": {}}


def write_transcript(path: Path, rows: list[dict[str, object]]) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


def closing_report(**extra: object) -> dict[str, object]:
    return assistant_row("m3", text("Landed it.\n\nresult: hook shipped\n"), 90_000, **extra)


def fixture_rows() -> list[dict[str, object]]:
    """Three turns; the second is split across rows and carries the peak."""
    return [
        {"type": "user", "message": {"role": "user", "content": "go"}},
        assistant_row("m1", tool_use(), 45_000),
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result"}]}},
        assistant_row("m2", {"type": "thinking", "thinking": "hm"}, 123_456),
        assistant_row("m2", tool_use(), 123_456),
        # A subagent's rows share the file but not the session's context.
        assistant_row("side", tool_use(), 900_000, isSidechain=True),
        {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result"}]}},
        closing_report(),
    ]


def test_blocks_once_with_turns_and_peak(tmp_path: Path) -> None:
    transcript = write_transcript(tmp_path / "t.jsonl", fixture_rows())
    result = run_hook(stop_event(transcript))
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)
    assert out["decision"] == "block"
    match = BUDGET_LINE.search(out["reason"])
    assert match is not None, out["reason"]
    # Three distinct message ids, not five assistant rows; peak is the max
    # summed context, in k, sidechain rows excluded.
    assert match.groups() == ("3", "123")


def test_reason_asks_for_the_result_line_to_be_kept(tmp_path: Path) -> None:
    transcript = write_transcript(tmp_path / "t.jsonl", fixture_rows())
    out = json.loads(run_hook(stop_event(transcript)).stdout)
    assert "result:" in out["reason"]
    assert "turns=3 peak=123k" in out["reason"]


def test_allows_when_report_already_carries_the_line(tmp_path: Path) -> None:
    rows = fixture_rows()[:-1] + [
        assistant_row("m3", text("result: done\nturns=3 peak=123k\n"), 90_000)
    ]
    transcript = write_transcript(tmp_path / "t.jsonl", rows)
    result = run_hook(stop_event(transcript))
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_allows_when_already_continued_once(tmp_path: Path) -> None:
    """``stop_hook_active`` is the loop guard: never block a second time."""
    transcript = write_transcript(tmp_path / "t.jsonl", fixture_rows())
    result = run_hook(stop_event(transcript, stop_hook_active=True))
    assert result.returncode == 0
    assert result.stdout.strip() == ""


def test_allows_a_stop_that_is_not_a_closing_report(tmp_path: Path) -> None:
    """A conversational reply gets no budget line — only report-shaped stops."""
    rows = fixture_rows()[:-1] + [assistant_row("m3", text("Which seam?"), 90_000)]
    transcript = write_transcript(tmp_path / "t.jsonl", rows)
    result = run_hook(stop_event(transcript))
    assert result.returncode == 0
    assert result.stdout.strip() == ""


@pytest.mark.parametrize("marker", ["needs input: auth token", "failed: wrong repo"])
def test_needs_input_and_failed_count_as_closing_reports(tmp_path: Path, marker: str) -> None:
    rows = fixture_rows()[:-1] + [assistant_row("m3", text(marker), 90_000)]
    transcript = write_transcript(tmp_path / "t.jsonl", rows)
    out = json.loads(run_hook(stop_event(transcript)).stdout)
    assert out["decision"] == "block"


def test_tolerates_missing_or_malformed_transcript(tmp_path: Path) -> None:
    result = run_hook(stop_event(tmp_path / "absent.jsonl"))
    assert result.returncode == 0
    assert result.stdout.strip() == ""

    broken = tmp_path / "b.jsonl"
    broken.write_text(
        "not json\n" + json.dumps(closing_report()) + "\n{\"type\": \"assistant\"}\n",
        encoding="utf-8",
    )
    out = json.loads(run_hook(stop_event(broken)).stdout)
    assert "turns=1 peak=90k" in out["reason"]


def test_ignores_other_events_and_bad_stdin(tmp_path: Path) -> None:
    transcript = write_transcript(tmp_path / "t.jsonl", fixture_rows())
    result = run_hook(stop_event(transcript, hook_event_name="SubagentStop"))
    assert result.returncode == 0 and result.stdout.strip() == ""

    garbage = run_hook_raw("{not json")
    assert garbage.returncode == 0 and garbage.stdout.strip() == ""
