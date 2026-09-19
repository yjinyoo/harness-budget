"""link_lint -- every file path a harness document names must exist.

BORROWED FROM `K-Dense-AI/scientific-agent-skills` (MIT), whose CI keeps a
163-skill collection from rotting with a structural contract on every pull
request: frontmatter parses, scripts answer ``--help``, and **every link
resolves**.  The last one is the part we needed.

WHY.  A routing table in a rules file that loads on every single turn once
pointed at eight files, and four of them did not exist.  Nothing could tell: a path in a markdown table is prose until someone
follows it, and the person following it is usually mid-task and takes the dead
link as "this does not exist here" rather than "this line is stale".

WHAT COUNTS AS A PATH.  Both markdown links and the backticked paths these
documents actually use (`notes/known_issues.md`, `tools/render.py`).
A backticked token is a path when it holds a separator or ends in a document /
code extension, which keeps `plt.savefig` and `--stage` out of it.  Placeholders
(`MEMORY_<ID>.md`), URLs and globs are skipped and counted, so "nothing to
check" can never read as "everything checks out".

    python link_lint.py                 # the harness documents
    python link_lint.py PATH [PATH ...] # anything else
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SIM = Path(__file__).resolve().parent.parent
HOME_CLAUDE = Path.home() / ".claude"
MEM = HOME_CLAUDE / "projects" / "c--Users-YJ-Simulations" / "memory"

#: checked by default: the documents that load automatically, plus the harness
#: notes the routing table sends people to
DEFAULT_TARGETS = [
    SIM / "CLAUDE.md",
    HOME_CLAUDE / "CLAUDE.md",
    MEM / "MEMORY.md",
    *sorted((SIM / "harness").glob("*.md")),
]

MD_LINK = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
BACKTICK = re.compile(r"`([^`\n]+)`")
DOC_EXT = {".md", ".py", ".yaml", ".yml", ".json", ".csv", ".ipynb", ".txt",
           ".gds", ".toml", ".cfg", ".sh", ".ps1"}


def roots_for(doc: Path) -> list[Path]:
    return [doc.parent, SIM, HOME_CLAUDE, MEM.parent, MEM]


def looks_like_path(tok: str) -> bool:
    """A token worth resolving.

    Deliberately narrow.  The first version accepted any token holding a dot or
    a slash and reported 225 dead paths, nearly all of them conventions the
    documents describe rather than files they point at ("every project keeps a
    ``BET.md``") or paths on a different machine (``/u/muse/tsmc``).  A linter
    whose output is mostly noise is one nobody reads, which is the same outcome
    as not having it.

    So: a path must name a DIRECTORY, and either carry a known extension or end
    in a separator.  That keeps exactly the shape the routing table uses -- and
    the shape that went stale on 2026-07-17 -- and drops bare filenames,
    ``web.upload/run`` and ``plt.savefig`` alike.
    """
    tok = tok.strip()
    if not tok or " " in tok.rstrip("/"):
        return False
    if tok.startswith(("http://", "https://", "mailto:", "#", "/", "~")):
        return False
    tok = tok.replace("\\", "/")
    if "/" not in tok.rstrip("/"):
        return False
    return tok.endswith("/") or Path(tok).suffix.lower() in DOC_EXT


#: named from a project's own directory, so a harness document cannot resolve
#: them and their absence at the root means nothing
PER_PROJECT_ROOTS = {"results", "prescreen", "figures", "data"}


def is_placeholder(tok: str) -> bool:
    if any(c in tok for c in "<>*?{}[]") or tok.endswith("..."):
        return True
    if "YYYY" in tok or "MM-DD" in tok:          # log/YYYY-MM-DD.md is a shape
        return True
    return tok.replace("\\", "/").split("/")[0] in PER_PROJECT_ROOTS


def is_ours(doc: Path, tok: str) -> bool:
    """Whether the first segment names a directory in one of our trees.

    ``notes/known_issues.md`` is ours and a dead one is a defect.
    ``licensingclient/linx64/`` lives on the MTL machine and its absence here
    means nothing, so it is counted as external rather than reported.
    """
    head = tok.replace("\\", "/").split("/")[0]
    if head == "Simulations":
        return True
    return any((r / head).is_dir() for r in roots_for(doc))


def candidates(doc: Path, tok: str) -> list[Path]:
    """Where a path written in *doc* could reasonably resolve."""
    from urllib.parse import unquote

    # a markdown link to a directory with a space in it arrives percent-encoded
    # ("../my%20folder/"), and reporting that as missing is the linter's bug,
    # not the document's
    tok = unquote(tok.strip().strip("`").rstrip(",.;:"))
    tok = tok.replace("\\", "/").lstrip("/")
    out = [r / tok for r in roots_for(doc)]
    # documents often name the tree they live in by its own folder name, so
    # accept that prefixed form against the parent as well
    prefix = SIM.name + "/"
    if tok.startswith(prefix):
        out.append(SIM / tok[len(prefix):])
    return out


def check(doc: Path) -> tuple[list[str], int, int]:
    """``(dead links, checked, skipped)`` for one document."""
    try:
        text = doc.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ([f"{doc}: cannot read ({exc})"], 0, 0)

    seen: set[str] = set()
    dead, checked, skipped = [], 0, 0
    for line_no, line in enumerate(text.split("\n"), 1):
        toks = MD_LINK.findall(line) + BACKTICK.findall(line)
        for tok in toks:
            tok = tok.strip()
            if not looks_like_path(tok):
                continue
            if is_placeholder(tok) or not is_ours(doc, tok):
                skipped += 1
                continue
            if tok in seen:
                continue
            seen.add(tok)
            checked += 1
            if not any(c.exists() for c in candidates(doc, tok)):
                dead.append(f"{doc.name}:{line_no}  {tok}")
    return (dead, checked, skipped)


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    targets = [Path(a).resolve() for a in args] if args else DEFAULT_TARGETS
    targets = [t for t in targets if t.exists()]

    dead, checked, skipped = [], 0, 0
    for doc in targets:
        d, c, s = check(doc)
        dead += d
        checked += c
        skipped += s

    if dead:
        print(f"link_lint: {len(dead)} path(s) named but not on disk")
        for line in dead:
            print("  " + line)
    # say the size of what ran, always: a linter that checked nothing must not
    # be able to look like a linter that found nothing
    print(f"link_lint: {checked} path(s) checked across {len(targets)} "
          f"document(s), {skipped} placeholder(s) skipped")
    return 1 if dead else 0


if __name__ == "__main__":
    raise SystemExit(main())
