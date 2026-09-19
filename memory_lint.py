"""memory_lint -- deterministic integrity lint for the file-based memory store.

The MECHANICAL half of the memory-quality system. It does NOT judge whether a
memory is *correct*, *stale*, or *worth keeping* -- that stays a human/Claude
call (and the session-end harness health check). It catches only what a script
can decide in milliseconds, so drift in a 250-file store stops being invisible:

  * broken [[link]]    -- a [[X]] in any body that resolves to neither a file
                          stem (X.md) nor a `name:` slug of some file. Dead
                          cross-reference. WARN (links to not-yet-written
                          memories are allowed by the harness, but a typo'd
                          link to an existing-looking slug is almost always a
                          mistake -- we surface every unresolved one so the
                          user can tell the two apart).
  * MEMORY.md drift    -- a (file.md) pointer in the core index that points at a
                          missing file (HARD), or a feedback_*/reference_* file
                          that no index / link references anywhere (orphan WARN;
                          project_* live in MEMORY_<ID>.md so they are checked
                          against those too).
  * frontmatter        -- missing name/description/type, or a `name:` slug used
                          by more than one file (duplicate identity -- breaks
                          [[link]] resolution). HARD.
  * near-duplicate     -- two files whose `description:` share a high token
                          overlap. Soft signal that a topic was written twice.
                          WARN, conservative threshold.

Pure stdlib, no deps, Windows-friendly. Read-only -- never edits a memory.

  python memory_lint.py                      # lint the default memory dir
  python memory_lint.py --dir <path>         # lint another store
  python memory_lint.py --json               # machine-readable report
  python memory_lint.py --dup-threshold 0.6  # tune near-duplicate sensitivity

Exit code: 0 = clean, 1 = at least one HARD finding (CI-gate friendly).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

try:  # Windows console defaults to cp1252; some memory text is Korean
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

DEFAULT_DIR = Path(
    os.path.expanduser(os.environ.get("AGENT_MEMORY_DIR", "~/.claude/memory"))
)

LINK_RE = re.compile(r"\[\[([^\]]+?)\]\]")
# markdown link target ending in .md, e.g. [Title](feedback_x.md) or (file.md#frag)
MD_LINK_RE = re.compile(r"\(([A-Za-z0-9_./-]+?\.md)(?:#[^)]*)?\)")
FRONT_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
STOP = {
    "the", "a", "an", "to", "of", "for", "in", "on", "and", "or", "not",
    "is", "be", "before", "after", "with", "no", "use", "전", "후", "시",
    "꼭", "할", "것", "및", "등", "이", "그", "수", "때", "더",
}


def parse_front(text: str) -> dict:
    """Pull name/description/type out of YAML-ish frontmatter without a YAML dep."""
    m = FRONT_RE.match(text)
    out = {"name": None, "description": None, "type": None}
    if not m:
        return out
    block = m.group(1)
    for key in ("name", "description"):
        km = re.search(rf"^{key}:\s*(.+)$", block, re.MULTILINE)
        if km:
            out[key] = km.group(1).strip().strip('"').strip("'")
    tm = re.search(r"^\s*type:\s*(.+)$", block, re.MULTILINE)
    if tm:
        out["type"] = tm.group(1).strip()
    return out


def tokenize(s: str) -> set[str]:
    toks = re.findall(r"[A-Za-z0-9가-힣]+", (s or "").lower())
    return {t for t in toks if t not in STOP and len(t) > 1}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def norm(slug: str) -> str:
    """Separator-insensitive key. The harness uses '-' (name: slugs) and '_'
    (file stems) interchangeably in [[links]], so treat them as one alphabet
    and drop whitespace (catches links wrapped across a line)."""
    return re.sub(r"[\s_-]+", "", slug.strip().lower())


def lint(memory_dir: Path, dup_threshold: float):
    hard: list[str] = []
    warn: list[str] = []

    files = sorted(p for p in memory_dir.glob("*.md") if p.name != "MEMORY.md")
    core = memory_dir / "MEMORY.md"

    stems = {p.stem for p in files}
    fronts: dict[str, dict] = {}
    bodies: dict[str, str] = {}
    name_to_files: dict[str, list[str]] = {}

    for p in files:
        text = p.read_text(encoding="utf-8", errors="replace")
        fm = parse_front(text)
        fronts[p.stem] = fm
        bodies[p.stem] = text
        if fm["name"]:
            name_to_files.setdefault(fm["name"], []).append(p.stem)

    name_slugs = set(name_to_files)

    # 1. frontmatter hygiene -------------------------------------------------
    for stem in stems:
        fm = fronts[stem]
        if stem.startswith("MEMORY_") or stem == "TOPICS":
            continue  # index files (per-project, and the situational TOPICS
            # index added 2026-08-13) have their own shape, not a memory's
        missing = [k for k in ("name", "description", "type") if not fm[k]]
        if missing:
            hard.append(f"[frontmatter] {stem}.md missing: {', '.join(missing)}")
    for name, owners in sorted(name_to_files.items()):
        if len(owners) > 1:
            hard.append(
                f"[duplicate name] slug '{name}' claimed by: "
                + ", ".join(f"{o}.md" for o in owners)
                + "  (breaks [[link]] resolution)"
            )

    # 2. broken [[link]] -----------------------------------------------------
    # A link resolves if its separator-insensitive key matches a file stem or a
    # name: slug. '-'/'_' are interchangeable in practice, so only links that
    # match NOTHING even after normalisation are surfaced (real typos / never-
    # written targets / links wrapped across a newline).
    resolvable_norm = {norm(s) for s in stems} | {norm(s) for s in name_slugs}
    for stem in sorted(stems):
        seen = set()
        for raw in LINK_RE.findall(bodies[stem]):
            key = norm(raw)
            if key in seen:
                continue
            seen.add(key)
            if key not in resolvable_norm:
                warn.append(
                    f"[broken link] {stem}.md -> [[{raw.strip()}]] (unresolved)"
                )

    # 3. MEMORY.md index drift ----------------------------------------------
    indexed: set[str] = set()
    if core.exists():
        core_text = core.read_text(encoding="utf-8", errors="replace")
        for target in MD_LINK_RE.findall(core_text):
            tgt_stem = Path(target).stem
            indexed.add(tgt_stem)
            if tgt_stem not in stems:
                hard.append(f"[index drift] MEMORY.md -> ({target}) missing on disk")
    else:
        warn.append("[index] MEMORY.md not found -- cannot check pointer drift")

    # TOPICS.md (2026-08-13) is the SECOND index: MEMORY.md keeps what fires
    # every turn and hands everything situational to TOPICS.md. A memory listed
    # there is indexed, not recall-only. Not teaching this lint about it is how
    # 86 correctly-filed memories reported as orphans for three weeks.
    topics = memory_dir / "TOPICS.md"
    if topics.exists():
        for target in MD_LINK_RE.findall(topics.read_text(encoding="utf-8", errors="replace")):
            tgt_stem = Path(target).stem
            indexed.add(tgt_stem)
            if tgt_stem not in stems:
                hard.append(f"[index drift] TOPICS.md -> ({target}) missing on disk")

    # also collect pointers from per-project MEMORY_<ID>.md indexes, and check
    # their drift too -- only MEMORY.md was checked until 2026-09-04, so 13
    # pointers to memories deleted in the 07-17 consolidation sat unreported.
    proj_indexed: set[str] = set()
    for p in files:
        if not p.stem.startswith("MEMORY_"):
            continue
        for target in MD_LINK_RE.findall(bodies[p.stem]):
            tgt_stem = Path(target).stem
            proj_indexed.add(tgt_stem)
            if tgt_stem not in stems:
                hard.append(f"[index drift] {p.name} -> ({target}) missing on disk")

    # every body that links out contributes inbound references (normalised, so a
    # [[hyphen-slug]] link counts as covering the underscore-stem file it means)
    name_norm_to_files: dict[str, list[str]] = {}
    for nm, owners in name_to_files.items():
        name_norm_to_files.setdefault(norm(nm), []).extend(owners)
    linked_norm: set[str] = set()
    for stem in stems:
        for raw in LINK_RE.findall(bodies[stem]):
            k = norm(raw)
            linked_norm.add(k)
            for owner in name_norm_to_files.get(k, []):
                linked_norm.add(norm(owner))

    # Reachability from an INDEX and reachability from any sibling [[link]] are
    # different things, and conflating them is how 26 rules went missing from
    # MEMORY.md while this lint reported clean (2026-07-17). A cluster that only
    # links to itself (a tight cluster of project notes) is reachable from its members
    # but unreachable from the index root, so it loads only via recall -- i.e.
    # unpredictably -- and no one can see it is missing.
    indexed_norm = {norm(s) for s in indexed} | {norm(s) for s in proj_indexed}
    referenced_norm = indexed_norm | linked_norm
    for stem in sorted(stems):
        if stem.startswith("MEMORY_") or stem == "TOPICS":
            continue  # an index is not a memory; it has no inbound links by design
        key = norm(stem)
        if key not in referenced_norm:
            warn.append(
                f"[orphan] {stem}.md -- not in MEMORY.md, not in any "
                f"MEMORY_<ID>.md, no inbound [[link]]"
            )
        elif key not in indexed_norm:
            warn.append(
                f"[index-orphan] {stem}.md -- reachable only via a sibling "
                f"[[link]]; absent from MEMORY.md, TOPICS.md and every MEMORY_<ID>.md "
                f"(recall-only: loads unpredictably, invisible to the index)"
            )

    # 4. near-duplicate descriptions ----------------------------------------
    desc_toks = {
        stem: tokenize(fronts[stem]["description"])
        for stem in stems
        if fronts[stem]["description"] and not stem.startswith("MEMORY_")
    }
    items = sorted(desc_toks.items())
    for i, (s1, t1) in enumerate(items):
        for s2, t2 in items[i + 1:]:
            sim = jaccard(t1, t2)
            if sim >= dup_threshold:
                warn.append(
                    f"[near-dup] {s1}.md ~ {s2}.md  (desc overlap {sim:.0%})"
                )

    return hard, warn, len(files)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Integrity lint for file-based memory.")
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--dup-threshold", type=float, default=0.65)
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)

    if not args.dir.exists():
        print(f"memory dir not found: {args.dir}", file=sys.stderr)
        return 2

    hard, warn, n = lint(args.dir, args.dup_threshold)

    if args.as_json:
        print(json.dumps({"hard": hard, "warn": warn, "files": n}, ensure_ascii=False, indent=2))
    else:
        print(f"memory_lint: {n} files scanned in {args.dir}")
        print(f"  HARD: {len(hard)}   WARN: {len(warn)}\n")
        for line in hard:
            print("  X  " + line)
        if hard and warn:
            print()
        for line in warn:
            print("  !  " + line)
        if not hard and not warn:
            print("  clean.")
    return 1 if hard else 0


if __name__ == "__main__":
    raise SystemExit(main())
