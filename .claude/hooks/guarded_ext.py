"""The one guarded-extension list both context hooks import (#249).

Before this module each hook carried its own list and they had drifted (`.toml`
in one, not the other). A file with one of these extensions is what the rules
ration: a whole-file dump of it — by any reader, from any shell tool — is the
thing being blocked.

GUARDED_EXT is the union. Markdown sits in it because catting a doc into
context is still a whole-file read; the *size* rule in read-guard.py exempts it
(reading a whole ADR or CONTEXT.md is the intended use), and names that
exemption here so both hooks read from one place.
"""

GUARDED_EXT = {
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
    ".md",
}

# Extensions the big-file size rule (read-guard.py) leaves alone even when guarded.
SIZE_RULE_EXEMPT = {".md"}

# Regex alternation of the guarded extensions (without the dot), for command scanners.
GUARDED_EXT_RE = "|".join(sorted(e.lstrip(".") for e in GUARDED_EXT))
