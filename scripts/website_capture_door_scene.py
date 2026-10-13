"""Capture one animated scene of a bundled game for the website.

The games play short full-screen scenes during play: Voidrunner's jumps,
dockings and fights, War Dialer's raids, jobs and busts, and the finale Retro
Trivia plays at the end of a round. A still screenshot cannot show one, so
this records a scene as a *cast*: every frame's raw ANSI, exactly as the door
wrote it, with the time the door waits after drawing it.

The scene is played by the door's own `play_*` function, the one the game
calls, at 80x24 in truecolor. Only the terminal around it is replaced:

* **stdout** is a recorder, so the bytes are the door's real output;
* **the frame wait** is where the door would poll for a key. It records the
  frame boundary and the wait the door asked for, and reports that no key
  came, so the scene plays to its end;
* **the "is anyone watching" check** says yes. It looks for a live terminal,
  and a capture has none.

Each scene's arguments name what it is about -- a hull, a destination, a
score -- the way the game passes them from a career or a world. They are the
same kind of values the games' own scene tests use, chosen to read well.

    python scripts/website_capture_door_scene.py voidrunner jump \
        web/shots/cast-voidrunner-jump.json

`--list` names every scene. `website_scene_to_html.py` turns a cast into the
player data and the still a page embeds.
"""
from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WIDTH, HEIGHT = 80, 24


class Recorder:
    """A stdout that cuts what the door writes into frames."""

    def __init__(self) -> None:
        self.pending: list[str] = []
        self.frames: list[dict] = []

    def write(self, text: str) -> int:
        self.pending.append(text)
        return len(text)

    def flush(self) -> None:
        pass

    def frame(self, seconds: float) -> None:
        self.frames.append({"wait": round(seconds, 3), "ansi": "".join(self.pending)})
        self.pending.clear()


def _door(name: str):
    return importlib.import_module(f"netbbs.doors.bundled.{name}")


# -- Voidrunner ---------------------------------------------------------------

def _voidrunner(make) -> Recorder:
    vr = _door("voidrunner")
    vr._OUTPUT_STYLE, vr._PALETTE = "auto", vr.Palette(truecolor=True)
    vr._OUTPUT_WIDTH, vr._OUTPUT_HEIGHT = WIDTH, HEIGHT
    recorder = Recorder()

    def pause(seconds: float) -> bool:
        recorder.frame(seconds)
        return True

    with _patched(vr, motion_interrupted=lambda: False, motion_pause=pause, _RESIZE_PENDING=False), \
            contextlib.redirect_stdout(recorder):
        if not vr.play_scene(lambda w, h: make(vr, w, h)):
            raise SystemExit("Voidrunner did not play the scene; is DOOR_SCENES=0 set?")
    return recorder


VOIDRUNNER = {
    "jump": lambda vr, w, h: vr._JumpScene(
        w, h, hull="Freighter", destination="Kepler Reach", burn=3, fuel=9, day=41, seed="site-jump"),
    "dock": lambda vr, w, h: vr._DockScene(
        w, h, economy="Tech", station="Harbor Lights", system="Kepler Reach", charted=True,
        seed="site-dock"),
    "contact": lambda vr, w, h: vr._ContactScene(
        w, h, hull="Freighter", kind="squadron", names=["Red Jackal", "Gutter Saint"], target=1,
        tier=3, seed="site-contact"),
    "kill": lambda vr, w, h: vr._KillScene(
        w, h, hull="Cutter", patrol=False, name="Red Jackal", loot=220, seed="site-kill"),
    "landmark": lambda vr, w, h: vr._SalvageScene(
        w, h, hull="Carrier", credits=2400, site="the Silent Cathedral", seed="site-landmark"),
    "refit": lambda vr, w, h: vr._RefitScene(
        w, h, old="Freighter", new="Carrier", hull_hp=180, seed="site-refit"),
    "promotion": lambda vr, w, h: vr._EmblemScene(
        w, h, kind="rank", rank=4, title="Promoted  ·  Legend of the Frontier",
        detail="rank retained for this career", seed="site-rank"),
}


# -- War Dialer ---------------------------------------------------------------

def _war_dialer(compose) -> Recorder:
    wd = _door("war_dialer")
    palette = wd.Palette(truecolor=True, color_depth="truecolor")
    recorder = Recorder()

    def beat(seconds: float, *, hand_back: bool = True) -> bool:
        recorder.frame(seconds)
        return False

    with _patched(wd, _splash_ready=lambda p: True, _beat=beat), contextlib.redirect_stdout(recorder):
        outcome = wd.play_scene(palette, WIDTH, HEIGHT, lambda: compose(wd, palette))
        if outcome != wd.SCENE_SHOWN:
            raise SystemExit(f"War Dialer did not play the scene ({outcome}); is DOOR_SCENES=0 set?")
    return recorder


_RING = ["mine", "rival", "npc", None, "rival", "npc", None, "mine", "rival", None]

WAR_DIALER = {
    "root": lambda wd, p: wd.root_scene_frames(
        p, WIDTH, HEIGHT, handle="nova", exchange_name="415-555 Bay Exchange", role="carrier",
        defender="rival", kinds=_RING, index=4, success=True,
        caption="You root 415-555 Bay Exchange. It's yours now.", sub="+50 Rank, $12/hour", seed=7),
    "raid": lambda wd, p: wd.raid_scene_frames(
        p, WIDTH, HEIGHT, handle="nova", target="mallory", success=True, amount=340, crew=6,
        caption="You hit mallory; gross take $340.", sub="+25 Rank", seed=7),
    "job": lambda wd, p: wd.hack_scene_frames(
        p, WIDTH, HEIGHT, handle="nova", contract=2, approach=1, success=True, payout=420, crew_lost=0,
        caption="Success! Gross payout $420.", seed=7),
    "bust": lambda wd, p: wd.bust_scene_frames(
        p, WIDTH, HEIGHT, cash_before=1200, cash_after=900, crew_before=10, crew_after=8,
        caption="*** BUSTED *** Heat reset to 0", sub="Job failed. Recovery payout $0.", seed=7),
    "tier": lambda wd, p: wd.tier_scene_frames(
        p, WIDTH, HEIGHT, handle="nova", old_tier="Wannabe", new_tier="Script Kiddie", rank=312,
        caption="Promoted: Script Kiddie", sub="Rank 312, up from Wannabe", seed=7),
    "cracktro": lambda wd, p: wd.cracktro_scene_frames(
        p, WIDTH, HEIGHT, season_number=4,
        podium=[("Gold", "nova", 3100), ("Silver", "mallory", 2200), ("Bronze", "eve", 900)],
        handle="nova", placement=1, caption="Season 4: you placed #1, Gold.", seed=7),
}


# -- Retro Trivia -------------------------------------------------------------

class _Watching:
    """The key poll a live terminal would give the finale: no key, ever."""

    def __init__(self, recorder: Recorder) -> None:
        self.recorder = recorder

    def waiting(self, seconds: float) -> bool:
        return False

    def take(self, seconds: float) -> bool:
        self.recorder.frame(seconds)
        return False


def _retro_trivia(score: int, total: int) -> Recorder:
    rt = _door("retro_trivia")
    recorder = Recorder()
    info = {"node_name": "Harbor Lights", "handle": "nova"}
    with _patched(rt, _stdin_holds_typing=lambda: False, _PLAIN=False), contextlib.redirect_stdout(recorder):
        if not rt.play_finale(rt.Palette(truecolor=True), info, score, total, WIDTH, HEIGHT,
                              poll=_Watching(recorder)):
            raise SystemExit("Retro Trivia did not play the finale; is DOOR_SPLASH=0 set?")
    return recorder


RETRO_TRIVIA = {
    "jackpot": (8, 8),
    "applause": (6, 8),
    "lights-out": (2, 8),
}


@contextlib.contextmanager
def _patched(module, **values):
    saved = {name: getattr(module, name) for name in values}
    for name, value in values.items():
        setattr(module, name, value)
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(module, name, value)


SCENES = {
    "voidrunner": {name: (lambda make=make: _voidrunner(make)) for name, make in VOIDRUNNER.items()},
    "war_dialer": {name: (lambda compose=compose: _war_dialer(compose)) for name, compose in WAR_DIALER.items()},
    "retro_trivia": {name: (lambda st=st: _retro_trivia(*st)) for name, st in RETRO_TRIVIA.items()},
}


def capture(door: str, scene: str) -> dict:
    recorder = SCENES[door][scene]()
    # What is left after the last wait is the door's own teardown: the clear
    # that hands the screen to whatever comes next. A looping player has no
    # next screen, so it is not part of the cast.
    if not recorder.frames:
        raise SystemExit(f"{door} {scene} drew no frames")
    return {"door": door, "scene": scene, "width": WIDTH, "height": HEIGHT, "frames": recorder.frames}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("door", nargs="?", choices=sorted(SCENES))
    parser.add_argument("scene", nargs="?")
    parser.add_argument("output", nargs="?", type=pathlib.Path)
    parser.add_argument("--list", action="store_true", help="name every scene and exit")
    args = parser.parse_args()
    if args.list:
        for door in sorted(SCENES):
            for scene in SCENES[door]:
                print(door, scene)
        return
    if not (args.door and args.scene and args.output):
        parser.error("give a door, a scene and an output file, or --list")
    if args.scene not in SCENES[args.door]:
        raise SystemExit(f"{args.door} has no scene {args.scene!r}; try --list")
    cast = capture(args.door, args.scene)
    # Bytes, never text: Windows text mode would turn the doors' CR LF into CR CR LF.
    args.output.write_bytes((json.dumps(cast, indent=1) + "\n").encode("utf-8"))
    seconds = sum(frame["wait"] for frame in cast["frames"])
    print(f"wrote {args.output}: {len(cast['frames'])} frames, {seconds:.1f} s")


if __name__ == "__main__":
    main()
