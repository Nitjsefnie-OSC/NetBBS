"""The web client's bell (issue #1302): `installBell` in
`netbbs-terminal.js`, run under Node with a fake terminal, window and
document when Node is installed."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "src" / "netbbs" / "web" / "static" / "netbbs-terminal.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")

HARNESS = r"""
var made = 0, tones = 0;
function FakeAudio() {
  made++;
  this.state = "running";
  this.currentTime = 0;
  this.destination = {};
}
FakeAudio.prototype.createOscillator = function () {
  return { frequency: {}, connect: function () {}, start: function () { tones++; }, stop: function () {} };
};
FakeAudio.prototype.createGain = function () {
  var noop = function () {};
  return { gain: { setValueAtTime: noop, exponentialRampToValueAtTime: noop }, connect: noop };
};
function target() {
  var handlers = {};
  return {
    handlers: handlers,
    addEventListener: function (name, fn) { (handlers[name] = handlers[name] || []).push(fn); },
    fire: function (name) { (handlers[name] || []).forEach(function (fn) { fn({}); }); },
  };
}
var win = target();
win.AudioContext = FakeAudio;
var doc = target();
doc.title = "NetBBS";
doc.hidden = false;
var bell = null;
var term = { onBell: function (fn) { bell = fn; } };
installBell(term, win, doc);
var log = [];
function note(label) { log.push([label, made, tones, doc.title]); }
bell(); note("before any key");
doc.fire("keydown"); doc.fire("click"); bell(); note("after a key");
doc.hidden = true; bell(); bell(); note("hidden");
doc.hidden = false; doc.fire("visibilitychange"); note("shown");
doc.hidden = true; bell(); win.fire("focus"); note("focused");
console.log(JSON.stringify(log));
"""


def _run() -> list:
    source = SCRIPT.read_text(encoding="utf-8")
    function = re.search(r"function installBell\(term, win, doc\) \{.*?\n  \}", source, re.S).group(0)
    output = subprocess.run(
        ["node", "-e", f"{function}\n{HARNESS}"],
        capture_output=True, text=True, encoding="utf-8", check=True, timeout=60,
    )
    return json.loads(output.stdout)


def test_the_tone_waits_for_a_key_and_a_hidden_tab_marks_its_title():
    log = {label: (made, tones, title) for label, made, tones, title in _run()}
    # No sound before the page has been used: no AudioContext at all.
    assert log["before any key"] == (0, 0, "NetBBS")
    # The first key or click makes one, once, and a bell then sounds.
    assert log["after a key"] == (1, 1, "NetBBS")
    # Hidden: each bell sounds, and the title is marked once.
    assert log["hidden"] == (1, 3, "(!) NetBBS")
    # Shown again: the mark goes; it comes back on the next hidden bell
    # and goes again on focus.
    assert log["shown"][2] == "NetBBS"
    assert log["focused"][2] == "NetBBS"


def test_the_page_installs_the_bell_on_its_terminal():
    assert "installBell(term, window, document);" in SCRIPT.read_text(encoding="utf-8")
