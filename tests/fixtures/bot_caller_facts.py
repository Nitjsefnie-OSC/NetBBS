"""Answers every request with the hello's and the event's issue #1379 facts."""
import json
import sys

hello = json.loads(sys.stdin.readline())
print(json.dumps({"type": "ready", "version": 1}), flush=True)
for line in sys.stdin:
    event = json.loads(line)
    if event["type"] == "message":
        facts = {"moderator": event["caller"].get("moderator"), "guest": event["caller"].get("guest"),
                 "linked": hello.get("linked_channels"), "node": hello.get("node"),
                 "quotes": hello.get("quotes"), "polls": hello.get("polls")}
        print(json.dumps({"id": "facts", "action": "reply", "request": event["request"],
                          "text": "facts " + json.dumps(facts, sort_keys=True)}), flush=True)
