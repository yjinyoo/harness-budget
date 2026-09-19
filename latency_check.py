"""Where does the wall clock actually go when an agent drives a tool?

Mines Claude Code session transcripts (`~/.claude/projects/<dir>/*.jsonl`) and
splits the elapsed time of every tool call into the two things it can be:

  * TOOL time    - tool_use -> its tool_result (the server or app actually
                   working)
  * MODEL time   - tool_result -> the next tool_use (the agent thinking, which
                   includes prompt processing and generation)

The split is the point, because the two have opposite fixes and they are
indistinguishable from inside the loop: the work feels slow either way.

Written to answer "why do figures through this MCP server take so long?". The
answer was 11 percent tool, 89 percent round trip, which pointed at the number
of calls rather than at the server, and the fix was a batch tool that takes N
typed calls in one round trip. Nobody would have guessed that ratio, and the
week before it was measured the effort was going into making the server faster.

    python latency_check.py mcp__some-server__
    python latency_check.py mcp__some-server__ --top 15
    python latency_check.py ""          # every tool, ranked

Read the output like this. If TOOL time dominates, make the tool faster. If
MODEL time dominates, make FEWER CALLS: batch them, reuse templates, stop
re-reading intermediate output. Those are different projects, and the split
tells you which one you are on.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics as st
from collections import defaultdict
from datetime import datetime

# Claude Code writes one directory per project, named after its path. Default to
# all of them: a tool worth profiling is usually driven from more than one.
DEFAULT_PROJECT_DIR = os.path.expanduser(os.path.join("~", ".claude", "projects"))


def _ts(raw: str) -> float:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()


def load_events(project_dir: str) -> dict:
    """{transcript path: [(t, kind, tool_use_id, tool_name)]}, time-sorted.

    Only the two record shapes that carry timing are read, so a large transcript
    costs one pass and no JSON parse of unrelated lines.
    """
    events: dict = defaultdict(list)
    # Accept either a single project directory or the parent that holds them all.
    paths = (glob.glob(os.path.join(project_dir, "*.jsonl"))
             + glob.glob(os.path.join(project_dir, "*", "*.jsonl")))
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"tool_use"' not in line and '"tool_result"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                stamp = rec.get("timestamp")
                content = (rec.get("message") or {}).get("content")
                if not stamp or not isinstance(content, list):
                    continue
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "tool_use":
                        events[path].append(
                            (_ts(stamp), "use", item.get("id"),
                             str(item.get("name", ""))))
                    elif item.get("type") == "tool_result":
                        events[path].append(
                            (_ts(stamp), "res", item.get("tool_use_id"), None))
    for evs in events.values():
        evs.sort(key=lambda e: e[0])
    return events


def analyze(events: dict, prefix: str, cap: float = 600.0) -> dict:
    """Split tool time from model time for calls whose name starts with prefix."""
    per_tool: dict = defaultdict(list)
    gaps: list = []
    for evs in events.values():
        starts = {i: (t, n) for t, k, i, n in evs if k == "use"}
        ends = {i: t for t, k, i, _ in evs if k == "res"}
        for uid, (t0, name) in starts.items():
            if not name.startswith(prefix) or uid not in ends:
                continue
            dt = ends[uid] - t0
            if 0 <= dt < cap:
                per_tool[name].append(dt)
        for a, b in zip(evs, evs[1:]):
            if a[1] == "res" and b[1] == "use" and b[3].startswith(prefix):
                gap = b[0] - a[0]
                if 0 <= gap < cap:
                    gaps.append(gap)
    return {"per_tool": per_tool, "gaps": gaps}


def report(result: dict, prefix: str, top: int) -> None:
    per_tool, gaps = result["per_tool"], result["gaps"]
    durations = [d for v in per_tool.values() for d in v]
    if not durations:
        print(f"No completed calls matching {prefix!r}.")
        return
    tool_total, model_total = sum(durations), sum(gaps)
    both = tool_total + model_total
    print(f"{len(durations)} calls matching {prefix!r}\n")
    print(f"  TOOL  time : {tool_total/60:7.1f} min  "
          f"({100*tool_total/both:4.1f}%)  median {st.median(durations):.2f}s")
    if gaps:
        print(f"  MODEL time : {model_total/60:7.1f} min  "
              f"({100*model_total/both:4.1f}%)  median {st.median(gaps):.2f}s"
              f"  p90 {sorted(gaps)[int(.9*len(gaps))]:.2f}s")
    print(f"\n  slowest tools by TOTAL time:")
    for name, vals in sorted(per_tool.items(), key=lambda kv: -sum(kv[1]))[:top]:
        print(f"    {sum(vals)/60:6.1f} min  n={len(vals):4d}  "
              f"med={st.median(vals):5.2f}s  max={max(vals):6.1f}s  "
              f"{name.split('__')[-1]}")
    if gaps and model_total > tool_total:
        print("\n  => MODEL time dominates: cut the NUMBER of calls "
              "(batch them, reuse templates), not the tool's speed.")
    elif durations:
        print("\n  => TOOL time dominates: the tool itself is the bottleneck.")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("prefix", nargs="?", default="",
                    help="tool-name prefix, e.g. mcp__origin-pro__")
    ap.add_argument("--project-dir", default=DEFAULT_PROJECT_DIR)
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args()
    events = load_events(args.project_dir)
    if not events:
        print(f"No transcripts under {args.project_dir}")
        return
    report(analyze(events, args.prefix), args.prefix, args.top)


if __name__ == "__main__":
    main()
