# harness-budget

Five checks for a coding-agent harness: measure what it costs, in context and in
wall clock, and verify that what it says is still true.

```
python budget_check.py          # over-budget files, and the linters below
python memory_lint.py           # integrity of a file-based memory store
python link_lint.py <dir>       # every path the documents name must exist
python memory_eval.py           # does the right memory come back for a question
python latency_check.py <tool>  # where the wall clock goes when an agent drives a tool
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

Counting is in **characters, newlines excluded, never bytes**. Outside ASCII a
character is commonly two to four bytes in UTF-8, so `wc -c` can report several
times what the context window actually sees.

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

`exclude` earns its place. An archive file is where the excess *goes*; it never
loads at session start, so it is supposed to grow. Counting it pushes whoever
reads the report to trim the very thing the budget exists to feed.

## memory_lint.py

Deterministic integrity checks on a directory of memory files: frontmatter that
parses, names that match their file, index entries that point at something,
near-duplicate entries that should be one. Tokenizing is Unicode-general, so a
store in any script scores the way it reads. The stop list ships English only;
drop a `stopwords.txt` beside the script to add your own.

## link_lint.py

Every file path a document names must exist. A stale path is quiet in a way a
wrong sentence is not: it reads as "this is not here" rather than "this line is
old", so nothing notices until someone follows it. Half the rows in one routing
table pointed at files that had been deleted, and it had been that way for
weeks.

## memory_eval.py

Does the right memory come back when it should? Each test case is a trigger
phrase paired with the memory that ought to surface for it. Every memory is
ranked against the phrase, and the score is how often the right one lands in the
top K.

It is worth measuring because nothing else does. A memory whose description
drifts vague stops surfacing silently: the file is still there, nothing errors,
and the first sign is the mistake the memory existed to prevent.

This is a lexical proxy for the real recall path, not the path itself. It is
useful for catching a description that has stopped being findable, not for
proving the live retriever works.

## latency_check.py

The other cost. Everything above measures context; this measures wall clock, by
mining the session transcripts Claude Code writes under `~/.claude/projects`.
Every tool call is split into the two things its elapsed time can be: TOOL time,
from the call to its result, which is the server actually working, and MODEL
time, from that result to the next call, which is the agent reading, thinking
and generating.

```
python latency_check.py mcp__some-server__
python latency_check.py ""            # every tool, ranked by total time
```

The split matters because the two have opposite fixes and are indistinguishable
from inside the loop: slow is slow. If TOOL time dominates, make the tool
faster. If MODEL time dominates, make fewer calls, and making the tool faster
buys almost nothing.

It was written to answer why figures through one MCP server took so long. The
answer was 11 percent tool, 89 percent round trip: a median of 0.35 s in the
tool against 6.4 s waiting on the model. The week before it was measured, the
effort had been going into making the server faster. The fix it pointed at
instead was a batch tool that takes N typed calls in one round trip.

Measure the split before optimizing anything an agent drives in a loop. The
ratio is not guessable.

## Requirements

Python 3.9+, standard library only.

## License

MIT. See `LICENSE`.
