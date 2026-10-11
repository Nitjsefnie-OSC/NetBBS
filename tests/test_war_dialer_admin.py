"""The standalone administration boundary uses real worlds and SQLite snapshots."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

DOOR = Path(__file__).resolve().parents[1] / "src/netbbs/doors/bundled/war_dialer.py"
spec = importlib.util.spec_from_file_location("war_dialer_admin_test", DOOR)
wd = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = wd
spec.loader.exec_module(wd)


@pytest.fixture
def world(tmp_path):
    path = tmp_path / "world.db"
    conn = wd.connect(path)
    now = wd.now_utc()
    wd.bind_world_owner(conn, "a" * 32)
    season = wd.current_world_season(conn, now)
    wd.ensure_exchanges_seeded(conn, season, now)
    wd.load_or_create_player(conn, 1, "Caller", now, season)
    wd.record_event(conn, 1, "Rival", "Keep this receipt", now)
    conn.close()
    return path


def test_status_is_read_only_and_missing_world_is_not_created(world, tmp_path):
    before = world.read_bytes()
    assert wd.administer_world(world, "status")["players"] == 1
    assert world.read_bytes() == before
    missing = tmp_path / "missing.db"
    with pytest.raises(sqlite3.Error):
        wd.administer_world(missing, "status")
    assert not missing.exists()


@pytest.mark.parametrize("command", ["close-season", "reset"])
def test_snapshot_contains_wal_and_precedes_change(world, command):
    wd.administer_world(world, "maintenance", maintenance=True)
    # Keep this connection open so the committed receipt is still in the WAL.
    conn = wd.connect(world)
    try:
        wd.record_event(conn, 1, "Rival", "Only in WAL", wd.now_utc())
        original = conn.execute("SELECT handle,created_at FROM players WHERE user_id=1").fetchone()
        result = wd.administer_world(world, command, confirm=world.name, reason="new contest")
        entry = result["recent_operations"][-1]
        saved = wd.inspect_world(Path(entry["snapshot"]))
        assert saved["events"] == 2
        assert saved["stored_season"] == "1"
        assert result["stored_season"] == "2"
        assert result["maintenance"] == "on"
        assert result["owner"] == "a" * 32
        assert result["events"] == 0 if command == "reset" else result["events"] >= 2
        assert tuple(conn.execute("SELECT handle,created_at FROM players WHERE user_id=1").fetchone()) == tuple(original)
    finally:
        conn.close()


def test_destructive_refusals_do_not_change_world(world, tmp_path):
    before = wd.inspect_world(world)
    with pytest.raises(wd.WorldStateError, match="maintenance"):
        wd.administer_world(world, "reset", confirm=world.name, reason="test")
    assert wd.inspect_world(world) == before
    wd.administer_world(world, "maintenance", maintenance=True)
    before = wd.inspect_world(world)
    with pytest.raises(wd.WorldStateError, match="Confirm"):
        wd.administer_world(world, "reset", confirm="wrong", reason="test")
    with pytest.raises(FileExistsError):
        wd.administer_world(world, "reset", confirm=world.name, reason="test", snapshot=world)
    with pytest.raises(OSError):
        wd.administer_world(world, "reset", confirm=world.name, reason="test", snapshot=tmp_path / "absent" / "save.db")
    assert wd.inspect_world(world) == before


def test_active_session_refuses_maintenance_and_reset(world):
    with wd.world_session(world):
        with pytest.raises(wd.WorldStateError, match="busy"):
            wd.administer_world(world, "maintenance", maintenance=True)
    wd.administer_world(world, "maintenance", maintenance=True)
    with wd.world_session(world):
        with pytest.raises(wd.WorldStateError, match="busy"):
            wd.administer_world(world, "reset", confirm=world.name, reason="test")
    assert not list(world.parent.glob("*.before-*.db"))


def test_reset_retains_latest_hundred_audit_entries(world):
    wd.administer_world(world, "maintenance", maintenance=True)
    conn = wd.connect(world)
    entries = [{"action": "old", "sequence": n} for n in range(110)]
    conn.execute("UPDATE meta SET value=? WHERE key='sysop_audit'", (json.dumps(entries),))
    conn.close()
    wd.administer_world(world, "reset", confirm=world.name, reason="test")
    with sqlite3.connect(world) as conn:
        saved = json.loads(conn.execute("SELECT value FROM meta WHERE key='sysop_audit'").fetchone()[0])
    assert len(saved) == 100 and saved[0]["sequence"] == 11
    assert saved[-1]["action"] == "reset competition"


def test_cli_runs_without_host_and_reports_json_errors(world, tmp_path):
    env = dict(os.environ, WAR_DIALER_DB_PATH=str(world), DOOR_SPLASH="0")
    env.pop("NETBBS_DOOR_INFO", None)
    def run(*args):
        return subprocess.run([sys.executable, "-I", str(DOOR), *args], env=env,
                              capture_output=True, text=True, timeout=15)
    status = run("--admin", "status", "--json")
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["players"] == 1
    maintenance = run("--admin", "maintenance", "on", "--json")
    assert maintenance.returncode == 0, maintenance.stderr
    assert json.loads(maintenance.stdout)["maintenance"] == "on"
    human = run("--admin=status")
    assert human.returncode == 0 and "stored_season: 1" in human.stdout
    error = run("--admin", "status", "--world", str(tmp_path / "absent.db"), "--json")
    assert error.returncode == 1 and json.loads(error.stdout)["error"]
