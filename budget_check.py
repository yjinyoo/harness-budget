#!/usr/bin/env python3
"""Measure the files an agent loads automatically, and speak only when one is
over budget.

Why this exists: those numbers used to be written into the rules file by hand.
They were wrong for weeks. A byte count read as a character count put one file
at "86 percent over" when it was 12 percent, and nothing that read the number
could tell. A budget line that measures itself cannot go stale; one that is
typed is only right on the day it was typed.

Counting is in characters, not bytes, and newlines are excluded. The
distinction matters outside ASCII, where one character is commonly two to four
bytes in UTF-8, so `wc -c` can report several times the number a context window
actually sees.

    python budget_check.py                 # uses budgets.json beside this file
    python budget_check.py --config x.json
    python budget_check.py --verbose       # print every check, not just failures

Exits 0 always, prints nothing when everything is inside budget. It is built to
run as a session-start hook, where a crash or a wall of text on every start is
worse than a missed warning.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def expand(p: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(p)))


def count_lines(p: Path) -> int:
    return len(p.read_text(encoding="utf-8", errors="replace").split("\n"))


def count_chars(p: Path) -> int:
    """Characters, newlines excluded. Never bytes: see the module docstring."""
    return sum(len(l) for l in p.read_text(encoding="utf-8", errors="replace").split("\n"))


COUNTERS = {"lines": count_lines, "chars": count_chars}


def targets(check: dict) -> list[Path]:
    """The files one check covers: a single path, or everything a glob finds."""
    if "path" in check:
        p = expand(check["path"])
        return [p] if p.exists() else []
    root = expand(check.get("root", "."))
    found = sorted(root.glob(check["glob"]))
    skip = check.get("exclude")
    if skip:
        found = [p for p in found if not p.match(skip)]
    return found


def run_checks(config: dict, verbose: bool) -> list[str]:
    over: list[str] = []
    max_rows = int(config.get("max_rows_per_group", 5))

    for check in config.get("checks", []):
        unit = check.get("unit", "chars")
        count = COUNTERS[unit]
        limit = int(check["limit"])
        note = check.get("note", "")
        hits = []
        for p in targets(check):
            n = count(p)
            if verbose:
                print(f"  {'OVER' if n > limit else 'ok  '} {p.name}: {n:,} {unit} / {limit:,}")
            if n > limit:
                hits.append((n, p))
        for n, p in sorted(hits, reverse=True)[:max_rows]:
            label = check.get("label") or (p.name if "path" in check
                                           else f"{p.parent.name}/{p.name}")
            tail = f" ({n - limit:,} over)" if unit == "chars" else ""
            over.append(f"  {label}: {n:,} {unit} / {limit:,}{tail}"
                        + (f"  {note}" if note else ""))
        if len(hits) > max_rows:
            over.append(f"  ({len(hits) - max_rows} more over the same budget)")
    return over


def run_subchecks(config: dict) -> list[str]:
    """Other linters, run only to decide whether they have something to say.

    Kept separate from the size budgets because a size is a number and these
    are verdicts: a stale path in a routing table reads as "this is not here"
    rather than "this line is old", so nothing notices until someone follows it.
    """
    lines = []
    for sub in config.get("subchecks", []):
        script = expand(sub["script"])
        if not script.is_absolute():
            script = HERE / script
        if not script.exists():
            continue
        try:
            r = subprocess.run([sys.executable, str(script), *sub.get("args", [])],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=int(sub.get("timeout", 60)))
        except Exception:
            continue
        out = r.stdout or ""
        ok = r.returncode == 0
        if ok and sub.get("ok_contains"):
            ok = sub["ok_contains"] in out
        if ok:
            continue
        first = next((l.strip() for l in out.splitlines()
                      if sub.get("report_marker", "") in l and l.strip()), "")
        lines.append(f"  {sub['name']}: {first or 'reported a problem'}"
                     f"  ->  python {script.name}")
    return lines


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(HERE / "budgets.json"))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    cfg_path = expand(args.config)
    if not cfg_path.exists():
        return
    config = json.loads(cfg_path.read_text(encoding="utf-8"))

    over = run_checks(config, args.verbose)
    subs = run_subchecks(config)
    if not over and not subs:
        return

    print(config.get("header", "Context budget (session start):"))
    for row in over + subs:
        print(row)
    if over and config.get("footer"):
        print("  " + config["footer"])


if __name__ == "__main__":
    # A hook must not break the session it runs in: any failure is silent.
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
