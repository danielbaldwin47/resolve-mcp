"""Compute jobs that read the audio and write what they read to disk.

Every module here follows the same shape, and the shape is the point: the server measures,
Claude decides. A transcript is words with confidences and the gaps between them — not a
"flub detector"; music analysis is beats, energy and boundaries — not a "good cut here"
signal. A detector's opinion cannot be argued with in a review round, so no detector is
shipped where a reading will do.

The output is a file, not a return value. A concert's worth of words does not fit in a tool
result and would eat the client's output cap whole, so the job writes timestamped
LLM-friendly JSON — one record per line, greppable — and returns the path plus gist stats.
The agent reads the slices it needs.
"""
