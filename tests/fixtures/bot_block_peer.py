"""A third-party-style bot that answers with titled blocks, for issue #1373's
tests. It speaks the raw protocol, without botkit.

`block <x>` replies with a small block, `private <x>` answers privately with
one, `tall` replies with the largest block the host takes, and `hello`
says what the hello offered. `both`, `bad` and `say-block` send what the
host refuses; every refusal is then replied as a plain line, `refused: ...`.
"""
import json
import sys


def send(**value):
    print(json.dumps(value), flush=True)


hello = json.loads(sys.stdin.readline())
send(type="ready", version=1)
waiting = {}
for line in sys.stdin:
    event = json.loads(line)
    if event["type"] == "message":
        request, text = event["request"], event["text"]
        command, _, rest = text.partition(" ")
        rows = [{"label": "Online", "text": "4 people"}, {"label": "Uptime", "text": "3h 12m"}]
        if command == "block":
            send(id="b:" + request, action="reply", request=request,
                 block={"title": "Node stats", "rows": rows + [{"text": rest}]})
        elif command == "private":
            send(id="p:" + request, action="reply", request=request, private=True,
                 block={"title": "Your reminders", "rows": [{"label": "#1", "text": rest}]})
        elif command == "tall":
            send(id="t:" + request, action="reply", request=request,
                 block={"title": "Everything", "rows": [{"label": f"row {i}", "text": f"{i} " + ("word " * 39).strip()}
                                                       for i in range(1, 13)]})
        elif command == "hello":
            send(id="h:" + request, action="reply", request=request, text=f"blocks={hello.get('blocks')}")
        elif command == "both":
            waiting["x:" + request] = request
            send(id="x:" + request, action="reply", request=request, text="t", block={"title": "T", "rows": rows})
        elif command == "bad":
            waiting["x:" + request] = request
            send(id="x:" + request, action="reply", request=request,
                 block={"title": "T", "rows": [{"text": "r"}] * 13})
        elif command == "say-block":
            waiting["x:" + request] = request
            send(id="x:" + request, action="say", channel=event["channel"], block={"title": "T", "rows": rows})
    elif event["type"] == "result":
        request = waiting.pop(event["id"], None)
        if request is not None and not event["ok"]:
            send(id="r:" + request, action="reply", request=request, text="refused: " + event["error"])
