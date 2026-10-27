"""Create, validate, or safely restore an offline Mr Smith SQLite backup."""

import argparse
import json
from pathlib import Path

from assistant_api.recovery import RecoveryError, create_backup, restore_backup, verify_backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    export = commands.add_parser("export", help="Create a consistent, versioned SQLite backup archive")
    export.add_argument("--database", type=Path, default=Path("data") / "mr_smith.sqlite3")
    export.add_argument("--output", type=Path, required=True)

    verify = commands.add_parser("verify", help="Verify a backup archive without changing data")
    verify.add_argument("--backup", type=Path, required=True)

    restore = commands.add_parser("restore", help="Restore into a directory with no existing Mr Smith DB")
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--data-dir", type=Path, required=True)

    arguments = parser.parse_args()
    try:
        if arguments.command == "export":
            result = create_backup(arguments.database, arguments.output)
        elif arguments.command == "verify":
            result = verify_backup(arguments.backup)
        else:
            result = restore_backup(arguments.backup, arguments.data_dir)
    except RecoveryError as exc:
        parser.exit(2, f"Recovery failed: {exc}\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
