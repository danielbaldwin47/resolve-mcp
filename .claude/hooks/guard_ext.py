"""The one guarded-extension list both context hooks import.

Text formats where a whole-file pull is the thing being rationed — by the
Read guard's size rule and by the context guard's whole-file-dump rules
(`cat`, `sed -n p`, `head`/`tail` with no count, `Get-Content`, `type`).
Anything unlisted (images, PDFs, notebooks, extensionless files) passes: a
line count is meaningless there.

Markdown is in the list for both hooks — `cat CONTEXT.md` is a whole-file
pull like any other, and since #247 the Read guard's size rule counts it too
(CONTEXT.md was the most expensive Read measured); only CLAUDE.md, read whole
at session start by design, is exempt there by name.

Defined once here so the two hooks cannot drift again (#248: `.toml` was
guarded by one hook and not the other).
"""

GUARDED_EXT = frozenset(
    {
        ".md",
        ".py",
        ".pyi",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".json",
        ".yml",
        ".yaml",
        ".toml",
        ".sh",
        ".ini",
        ".cfg",
        ".conf",
        ".txt",
        ".log",
    }
)
