# harness-budget

Four checks for a coding-agent harness that loads files into context
automatically: measure what they cost, and verify that what they say is still
true.

```
python budget_check.py          # over-budget files, and the linters below
python memory_lint.py           # integrity of a file-based memory store
python link_lint.py <dir>       # every path the documents name must exist
python memory_eval.py           # recall@K for that memory store
```

## Why measure it at all

A harness accumulates. Rules files, per-project memory, handoff notes: each one
is loaded whole at session start or on every turn, so every character in them
is paid for again on each request. Nothing in the loop reports that, and the
file that grew is usually the one nobody reads any more.

The numbers here used to be typed into the rules file by hand. They were wrong
for weeks. A byte count read as a character count put one file at "86 percent
over" when it was 12 percent, and nothing that read the number could tell. A
budget line that measures itself cannot go stale; one that is typed is only
right on the day it was typed.

Counting is in **characters, newlines excluded, never bytes**. Outside ASCII
that is not a detail: one Korean or Chinese character is three bytes in UTF-8,
so `wc -c` reports three times what the context window actually sees.

## budget_check.py

Reads `budgets.json`, reports only what is over, and says nothing otherwise. It
is built to run as a session-start hook, where a wall of text on every start is
worse than a missed warning, so it exits 0 even when it crashes.

```json
{
  "checks": [
    {"path": "~/proj/CLAUDE.md",        "limit": 100,   "unit": "lines"},
    {"root": "~/.claude/memory", "glob": "MEMORY_*.md",
     "exclude": "*_history.md",         "limit": 25000, "unit": "chars"}
  ],
  "subchecks": [
    {"name": "memory integrity", "script": "memory_lint.py", "ok_contains": "WARN: 0"}
  ]
}
```

`exclude` matters more than it looks. An archive file is where the excess
*goes*; it never loads at session start, so it is supposed to grow. Counting it
pushes whoever reads the report to trim the very thing the budget exists to
feed.

## memory_lint.py

Deterministic integrity checks on a directory of memory files: frontmatter that
parses, names that match their file, index entries that point at something,
near-duplicate entries that should be one. Tokenizes Korean and English
together, so a mixed-language store scores the way it reads.

## link_lint.py

Every file path a document names must exist. A stale path is quiet in a way a
wrong sentence is not: it reads as "this is not here" rather than "this line is
old", so nothing notices until someone follows it. Half the rows in one routing
table pointed at files that had been deleted, and it had been that way for
weeks.

## memory_eval.py

Recall@K for the memory store. A harness that retrieves memories bets its
behaviour on retrieval, and retrieval is never measured: a memory whose
description drifted vague stops surfacing silently, and the first sign is the
mistake it existed to prevent. Cases are trigger phrases paired with the memory
that should come back; the score is where it ranks.

This is a lexical proxy for the real recall path, not the path itself. It is
useful for catching a description that has stopped being findable, not for
proving the live retriever works.

## Requirements

Python 3.9+, standard library only.

## License

MIT. See `LICENSE`.
