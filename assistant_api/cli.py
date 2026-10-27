import argparse
from pathlib import Path
from .config import Settings
from .storage import Store


def main() -> None:
    parser = argparse.ArgumentParser(description="Mr Smith local document tools")
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("ingest", help="Register Markdown/text files in the active document store")
    ingest.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.command == "ingest":
        store = Store(Settings.from_env().data_dir)
        count = 0
        for path in args.directory.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".txt", ".md"}:
                identity, created = store.add_document(path.name, "general", "working_note", str(path), path.read_text(encoding="utf-8", errors="ignore"))
                count += int(created)
                print(f"{path}: document:{identity} ({'created' if created else 'already indexed'})")
        print(f"Registered {count} new document(s)")


if __name__ == "__main__": main()
