"""A native test door that speaks door API 5 (issue #1408).

It reads `door_info.json`, and when `outbound.events` is there it reports one
event of every type for the playing caller -- a rank, an achievement, a
capture, a perfect round -- then a season's medals and a standings snapshot
naming the player and the account ids passed as arguments, in that order.
Each request is written as `<name>.part` and renamed to `<name>.json`, as the
door guide asks. It is what the bundled games emit from their own
repositories, in the shape NetBBS fixes.

Usage: door_events_peer.py [other-user-id ...]
"""

import json
import os
import pathlib
import sys


def drop(directory, name, payload):
    part = directory / (name + ".part")
    part.write_text(json.dumps(payload), encoding="utf-8")
    part.replace(directory / (name + ".json"))


def main():
    info_path = pathlib.Path(os.environ["NETBBS_DOOR_INFO"])
    info = json.loads(info_path.read_text(encoding="utf-8"))
    outbound = info.get("outbound") or {}
    if info.get("door_api", 0) < 5 or "events" not in outbound:
        print("Game events are off for this door.")
        return
    directory = info_path.parent / outbound["directory"]
    player = info["user_id"]
    others = [int(arg) for arg in sys.argv[1:]]
    drop(directory, "rank", {"event": {"type": "rank", "user_id": player, "title": "Merchant Captain"}})
    drop(directory, "achievement", {"event": {"type": "achievement", "user_id": player, "title": "Untouched"}})
    drop(directory, "capture", {"event": {"type": "capture", "user_id": player, "title": "the Kowloon exchange"}})
    drop(directory, "perfect", {"event": {"type": "perfect", "user_id": player, "title": "Arcade Classics"}})
    drop(directory, "medal", {"event": {"type": "medal", "user_ids": [player, *others][:3], "title": "Season 4"}})
    rows = [{"user_id": user_id, "value": f"{(10 - place) * 1000:,} cr", "label": "Void Baron" if place == 0 else ""}
            for place, user_id in enumerate([player, *others])]
    drop(directory, "standings", {"standings": {"title": "Hall of Fame", "rows": rows}})
    print("Reported.")


if __name__ == "__main__":
    main()
