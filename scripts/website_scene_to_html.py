"""Turn a scene cast into what a website page embeds to play it.

A cast (`website_capture_door_scene.py`) is every frame's raw ANSI. Each frame
is painted into the same terminal emulator the still captures use
(`website_ansi_to_html.Screen`), so a scene and a screenshot render the same
way. Only the cells a frame changes are kept, in runs of one style:

    {"cols": 80, "rows": 24, "styles": ["<css>", ...],
     "frames": [[wait_ms, [[row, col, style, "text"], ...]], ...]}

The page's player draws the same one-`<span>`-per-cell grid the stills use and
applies the runs frame by frame. `--poster` also writes the final frame, the
scene's caption, as a still: what a page shows before the scene plays, with
scripts off, and to a caller who asked for reduced motion.

    PYTHONPATH=src python scripts/website_scene_to_html.py \
        web/shots/cast-voidrunner-jump.json web/shots/scene-voidrunner-jump.json \
        --poster web/shots/scene-voidrunner-jump.html
"""
from __future__ import annotations

import argparse
import copy
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from website_ansi_to_html import Screen  # noqa: E402


def convert(cast: dict) -> tuple[dict, str]:
    screen = Screen(cast["width"], cast["height"])
    styles: dict[str, int] = {}
    previous = copy.deepcopy(screen.cells)
    frames = []
    for frame in cast["frames"]:
        screen.feed(frame["ansi"])
        runs = []
        for r, (old, new) in enumerate(zip(previous, screen.cells)):
            c = 0
            while c < len(new):
                if old[c] == new[c]:
                    c += 1
                    continue
                style = new[c][1]
                start, text = c, []
                while c < len(new) and old[c] != new[c] and new[c][1] == style:
                    text.append(new[c][0])
                    c += 1
                css = style.css()
                index = styles.setdefault(css, len(styles))
                runs.append([r, start, index, "".join(text)])
        frames.append([round(frame["wait"] * 1000), runs])
        previous = copy.deepcopy(screen.cells)
    data = {"cols": cast["width"], "rows": cast["height"], "styles": list(styles), "frames": frames}
    return data, screen.to_html()


def embeddable(data: dict) -> str:
    """JSON that is safe inside `<script type="application/json">`."""
    return (json.dumps(data, ensure_ascii=False, separators=(",", ":"))
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("cast", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path, help="player data (JSON) to write")
    parser.add_argument("--poster", type=pathlib.Path, help="also write the final frame as HTML")
    args = parser.parse_args()
    data, poster = convert(json.loads(args.cast.read_bytes().decode("utf-8")))
    # Bytes, never text: see website_ansi_to_html.py.
    args.output.write_bytes(embeddable(data).encode("utf-8"))
    if args.poster:
        args.poster.write_bytes(poster.encode("utf-8"))
    cells = sum(len(run[3]) for _, runs in data["frames"] for run in runs)
    print(f"wrote {args.output}: {len(data['frames'])} frames, {cells} cell writes, "
          f"{len(data['styles'])} styles, {args.output.stat().st_size} bytes")


if __name__ == "__main__":
    main()
