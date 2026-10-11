"""Local SysOp commands for owned War Dialer worlds; no service activation."""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from netbbs import backup
from netbbs.doors.bundled import war_dialer as wd
from netbbs.rendering.reflow import print_wrapped, terminal_wrapped

def _target(db_path: Path, world: Path) -> Path:
    world = world.expanduser().resolve()
    if world not in backup._war_dialer_sources(db_path):
        raise backup.BackupError("World is not a configured path for this node. Check the service/profile override.")
    backup._inspect_war_dialer(world, backup._war_dialer_owner(db_path))
    return world


def _plain(value: str) -> str:
    return "".join(ch if ch.isprintable() else " " for ch in value)


def world_status(db_path: Path, world: Path) -> dict:
    """Read stored state through the door's public administration boundary."""
    return wd.administer_world(_target(db_path, world), "status")


def set_maintenance(db_path: Path, world: Path, enabled: bool, *, operator: str | None = None) -> None:
    """Close or reopen the configured world through the door's own audit/guard."""
    try:
        wd.administer_world(_target(db_path, world), "maintenance", maintenance=enabled, operator=operator)
    except (wd.WorldStateError, sqlite3.Error, OSError, ValueError) as exc:
        raise backup.BackupError(str(exc)) from exc


def change_competition(db_path: Path, world: Path, *, identity_dir: Path, backup_to: Path,
                       confirm: str, reason: str, reset: bool = False, operator: str | None = None,
                       require_stopped_node: bool = True) -> dict:
    """Start the next season, or also clear receipts, behind a verified backup.

    The CLI keeps its stopped-node rule. The SysOp console passes
    `require_stopped_node=False` (issue #726): maintenance already keeps
    callers out, the world session guard refuses anyone still inside, and the
    backup below is the same live backup the console's Backup screen takes.
    """
    world = _target(db_path, world)
    if confirm != world.name or not reason.strip() or len(reason) > 240:
        raise backup.BackupError("Confirm the exact world filename and supply a reason of 1-240 characters.")
    if require_stopped_node:
        backup._require_node_not_running(db_path)
    # Maintenance is an explicit preceding operation, and remains on afterward.
    if world_status(db_path, world)["maintenance"] != "on":
        raise backup.BackupError("Enable maintenance first and close game sessions; no season was changed.")
    if not identity_dir.is_dir():
        raise backup.BackupError(f"Node identity directory {identity_dir} is unavailable; no season was changed.")
    backup.create_backup(db_path=db_path, identity_dir=identity_dir, destination=backup_to)
    backup._validate_backup_source(backup_to, allow_migrate=False)
    try:
        return wd.administer_world(
            world, "reset" if reset else "close-season", confirm=confirm, reason=reason,
            operator=operator, node_backup={"backup": str(backup_to.resolve()),
                                          "manifest_sha256": backup._sha256_of_file(backup_to / "manifest.json")})
    except (wd.WorldStateError, sqlite3.Error, OSError, ValueError) as exc:
        raise backup.BackupError(str(exc)) from exc



def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description="Inspect or maintain an owned War Dialer world.")
    parser.add_argument("--db", type=Path, required=True, help="owning node database")
    parser.add_argument("--world", type=Path, required=True, help="configured world database")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="read-only status and the ten latest operations")
    maintenance = commands.add_parser("maintenance", help="exclude new sessions; refuses active sessions")
    maintenance.add_argument("state", choices=("on", "off"))
    for name, help_text in (("season", "start the next season, retaining receipts"),
                            ("reset", "reset competition and receipts, retaining identities and account age")):
        command = commands.add_parser(name, help=help_text + "; requires a verified complete node backup")
        command.add_argument("--identity-dir", type=Path, required=True)
        command.add_argument("--backup-to", type=Path, required=True, help="fresh node backup directory")
        command.add_argument("--reason", required=True)
        command.add_argument("--confirm", required=True, help="exact world filename, including extension")
    args = parser.parse_args(argv)
    try:
        if args.command == "maintenance":
            set_maintenance(args.db, args.world, args.state == "on")
        elif args.command in ("season", "reset"):
            change_competition(args.db, args.world, identity_dir=args.identity_dir, backup_to=args.backup_to,
                               confirm=args.confirm, reason=args.reason, reset=args.command == "reset")
        status = world_status(args.db, args.world)
        for key, value in status.items():
            rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, list) else str(value)
            print_wrapped(f"{key}: {_plain(rendered)}")
    except (backup.BackupError, wd.WorldStateError, OSError, sqlite3.Error, ValueError) as exc:
        raise SystemExit(terminal_wrapped(f"War Dialer operation failed: {_plain(str(exc))[:500]}",
                                         stream=sys.stderr)) from exc


if __name__ == "__main__":
    main()
