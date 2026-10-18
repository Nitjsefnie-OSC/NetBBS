"""A third-party-style bot that answers privately, for issue #1372's tests.

Every answer names the `message` event's flags, so a test can see what the
host told the bot. `public <x>` replies publicly; `try-public <x>` tries a
public reply first (the host refuses it on a privately asked request) and
then answers privately with the refusal; anything else is answered
privately, falling back to a public reply when the host refuses the private
answer because it cannot reach the caller.
"""
import json
import sys


def send(**value):
    print(json.dumps(value), flush=True)


json.loads(sys.stdin.readline())
send(type="ready", version=1)
waiting = {}
for line in sys.stdin:
    event = json.loads(line)
    if event["type"] == "message":
        request, text = event["request"], event["text"]
        flags = f"private={event['private']} asked={event['asked_privately']}"
        if text.startswith("public "):
            send(id="public:" + request, action="reply", request=request, text=f"{text} ({flags})")
        elif text.startswith("try-public "):
            waiting["try:" + request] = (request, text)
            send(id="try:" + request, action="reply", request=request, private=False, text="leaked " + text)
        else:
            waiting["private:" + request] = (request, text)
            send(id="private:" + request, action="reply", request=request, private=True,
                 text=f"secret {text} ({flags})")
    elif event["type"] == "result":
        pending = waiting.pop(event["id"], None)
        if pending is None or event["ok"]:
            continue
        request, text = pending
        if event["id"].startswith("try:"):
            send(id="after:" + request, action="reply", request=request, private=True,
                 text="refused public: " + event["error"])
        else:
            send(id="fallback:" + request, action="reply", request=request,
                 text=f"fallback {text}: {event['error']}")
