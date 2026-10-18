"""Regenerate the help desk's shipped copy of the caller handbook.

Usage, from the repository root:

    python scripts/build_helpdesk_handbook.py

It strips `docs/NetBBS-User-Handbook.md` of its markup, splits it at its
headings and writes `src/netbbs/bots/handbook.json`, which the wheel ships
as package data (§16, issue #1382). Run it after every handbook change;
`tests/test_bot_helpdesk.py` fails while the copy is stale.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from netbbs.bots.helpdesk import CORPUS_FILE, HANDBOOK_SOURCE, corpus_document  # noqa: E402


def main() -> None:
    text = (ROOT / HANDBOOK_SOURCE).read_text(encoding="utf-8")
    target = ROOT / "src" / "netbbs" / "bots" / CORPUS_FILE
    target.write_text(corpus_document(text), encoding="utf-8", newline="\n")
    print(f"Wrote {target.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
