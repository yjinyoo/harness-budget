"""memory_eval -- recall@K diagnostic for the file-based memory store.

Adapted from gbrain-evals. The harness bets its whole behaviour on recall: the
right `feedback_*` / `project_*` memory must surface when a matching situation
appears in a user message. This never gets measured -- a memory with a vague
`description:` silently stops firing and you only notice when Claude repeats a
mistake the memory was meant to prevent.

This is a LEXICAL PROXY for that retrieval, not the live recall path. For each
sealed trigger phrase in memory_eval_cases.json it scores every memory by
IDF-weighted token overlap of the phrase against that memory's name+description
(the fields the real recall reads), ranks them, and checks whether the expected
target lands in the top K. Reports recall@1/3/5 and -- in the gbrain-evals
spirit of honest weakness reporting -- every miss with the rank the target
actually got and what outranked it.

A miss does NOT mean the rule is wrong. It means the target memory's
`description:` is not discriminative for its own trigger -> rewrite the
description (add the trigger's distinctive tokens), then re-run. The scorer is
deliberately dumb so that the fix is always "write a better description", never
"game the metric".

Pure stdlib. Read-only.

  python memory_eval.py                       # run the sealed suite
  python memory_eval.py --k 3                 # change the headline cutoff
  python memory_eval.py --show-top 5          # print top-5 for every case
  python memory_eval.py --json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from pathlib import Path

try:  # Windows console defaults to cp1252; memory triggers contain Korean
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

DEFAULT_DIR = Path(os.path.expanduser(os.environ.get("AGENT_MEMORY_DIR", "~/.claude/memory")))
CASES = Path(__file__).with_name("memory_eval_cases.json")
FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
STOP = {
    "the", "a", "an", "to", "of", "for", "in", "on", "and", "or", "not", "is",
    "be", "before", "after", "with", "no", "use", "전", "후", "시", "꼭", "할",
    "것", "및", "등", "이", "그", "수", "때", "더", "안", "는", "을", "를", "로",
}


def field(block: str, key: str) -> str:
    m = re.search(rf"^{key}:\s*(.+)$", block, re.MULTILINE)
    return m.group(1).strip().strip('"').strip("'") if m else ""


def doc_text(text: str) -> str:
    """name + description -- the fields the real recall ranks on."""
    m = FRONT_RE.match(text)
    if not m:
        return ""
    block = m.group(1)
    return f"{field(block, 'name')} {field(block, 'description')}"


def toks(s: str) -> list[str]:
    return [t for t in re.findall(r"[A-Za-z0-9가-힣]+", s.lower()) if t not in STOP and len(t) > 1]


def build_index(memory_dir: Path):
    docs: dict[str, list[str]] = {}
    for p in memory_dir.glob("*.md"):
        if p.name == "MEMORY.md" or p.stem.startswith("MEMORY_"):
            continue
        bag = toks(doc_text(p.read_text(encoding="utf-8", errors="replace")))
        if bag:
            docs[p.stem] = bag
    N = len(docs)
    df: dict[str, int] = {}
    for bag in docs.values():
        for t in set(bag):
            df[t] = df.get(t, 0) + 1
    idf = {t: math.log((N + 1) / (c + 0.5)) for t, c in df.items()}
    return docs, idf


def rank(query: str, docs, idf):
    q = set(toks(query))
    scored = []
    for stem, bag in docs.items():
        present = set(bag) & q
        if not present:
            continue
        scored.append((sum(idf.get(t, 0.0) for t in present), stem))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored


def main(argv=None):
    ap = argparse.ArgumentParser(description="recall@K diagnostic for file memory.")
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--cases", type=Path, default=CASES)
    ap.add_argument("--k", type=int, default=5, help="headline recall cutoff")
    ap.add_argument("--show-top", type=int, default=0)
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)

    docs, idf = build_index(args.dir)
    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]

    # sanity: every expected target must exist on disk, else the key is stale
    stale = sorted({e for c in cases for e in c["expect"] if e not in docs})
    if stale:
        print("STALE ANSWER KEY -- expected targets not found as memory files:", file=sys.stderr)
        for s in stale:
            print(f"  - {s}", file=sys.stderr)
        return 2

    hits = {1: 0, 3: 0, args.k: 0}
    mrr = 0.0
    misses = []
    detail = []
    for c in cases:
        ranked = rank(c["q"], docs, idf)
        order = [stem for _, stem in ranked]
        best = min((order.index(e) for e in c["expect"] if e in order), default=None)
        rr = 1.0 / (best + 1) if best is not None else 0.0
        mrr += rr
        for kk in hits:
            if best is not None and best < kk:
                hits[kk] += 1
        detail.append({"q": c["q"], "expect": c["expect"], "rank": (best + 1) if best is not None else None,
                       "top": order[:max(args.show_top, args.k)]})
        if best is None or best >= args.k:
            outranked = order[:3]
            misses.append({"q": c["q"], "expect": c["expect"],
                           "rank": (best + 1) if best is not None else None,
                           "outranked_by": outranked})

    n = len(cases)
    summary = {kk: hits[kk] / n for kk in sorted(hits)}
    mrr /= n

    if args.as_json:
        print(json.dumps({"n": n, "recall": summary, "mrr": round(mrr, 3),
                          "misses": misses, "detail": detail}, ensure_ascii=False, indent=2))
        return 0

    print(f"memory_eval: {n} sealed cases vs {len(docs)} memories\n")
    for kk in sorted(hits):
        print(f"  recall@{kk}: {hits[kk]}/{n} = {summary[kk]:.0%}")
    print(f"  MRR:       {mrr:.3f}\n")
    if misses:
        print(f"  {len(misses)} miss(es) (target outside top {args.k}) -- description needs distinctive tokens:")
        for m in misses:
            r = m["rank"] if m["rank"] is not None else "unranked"
            print(f"    X  rank={r}  expect {m['expect']}")
            print(f"         trigger: {m['q']}")
            print(f"         outranked by: {m['outranked_by']}")
    else:
        print(f"  all targets within top {args.k}.")
    if args.show_top:
        print("\n  per-case top results:")
        for d in detail:
            mark = "ok " if (d["rank"] and d["rank"] <= args.k) else "MISS"
            print(f"    [{mark}] {d['q'][:50]}")
            for i, stem in enumerate(d["top"][:args.show_top]):
                flag = " <-" if stem in d["expect"] else ""
                print(f"          {i+1}. {stem}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
