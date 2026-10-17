#!/usr/bin/env python3
"""Run both sides of the Link-v1 interoperability corpus."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "link_interop_vectors.json"


def run(label: str, command: list[str]) -> None:
    print(f"== {label} ==", flush=True)
    completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode:
        raise SystemExit(f"{label} failed with exit code {completed.returncode}")


def main() -> None:
    node = shutil.which("node")
    if node is None:
        raise SystemExit("Link interoperability check requires Node.js on PATH")

    run(
        "independent implementation",
        [node, str(ROOT / "scripts" / "link_interop.mjs"), str(CORPUS)],
    )
    run(
        "Python reference implementation",
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "tests/test_link_interop_vectors.py",
            "tests/test_link_canonical_vectors.py",
            "tests/test_content_id.py",
        ],
    )
    print("Link-v1 interoperability corpus passed in both implementations.")


if __name__ == "__main__":
    main()
