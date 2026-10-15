#!/usr/bin/env python3
"""
organizer.py
------------
A command-line tool that scans a folder (e.g. your Downloads folder)
and sorts its files into subfolders by type -- Images, Documents,
Videos, Archives, and so on -- based on a configurable extension map.

Designed to be safe to run on a real, messy folder:
  * Only looks at top-level files (never touches subfolders), so it
    won't re-shuffle files it already sorted, or reach into folders
    you didn't ask it to touch.
  * --dry-run lets you preview exactly what would happen with zero
    changes to disk.
  * Every real run is logged to logs/, and --undo reverses the most
    recent run (or a specific one via --undo RUN_ID).
  * Never overwrites an existing file -- name collisions get a
    "(1)", "(2)", etc. suffix instead.

Usage:
    python organizer.py ~/Downloads                 # organize (asks to confirm)
    python organizer.py ~/Downloads --dry-run        # preview only, no changes
    python organizer.py ~/Downloads --yes            # skip confirmation prompt
    python organizer.py --list-runs                  # show past runs
    python organizer.py --undo                       # undo the most recent run
    python organizer.py --undo 20260902-201501        # undo a specific run
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = SCRIPT_DIR / "config.json"
LOGS_DIR = SCRIPT_DIR / "logs"


 
# Configuration

def load_config(config_path):
    """Load the extension -> category mapping from a JSON config file."""
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"Error: config file not found at {config_path}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: config file is not valid JSON ({e})")
        sys.exit(1)

    if "categories" not in config:
        print("Error: config file must contain a 'categories' key.")
        sys.exit(1)

    # Build a flat lookup
    ext_to_category = {}
    for category, extensions in config["categories"].items():
        for ext in extensions:
            ext_to_category[ext.lower().lstrip(".")] = category

    fallback = config.get("fallback_category", "Other")
    return ext_to_category, fallback


# Scanning & planning
def scan_files(target_dir, include_hidden=False):
    """
    List top-level files in target_dir (not subfolders, not the
    organizer's own logs/config if they happen to live inside it).
    """
    files = []
    for entry in target_dir.iterdir():
        if entry.is_dir():
            continue
        if not include_hidden and entry.name.startswith("."):
            continue
        files.append(entry)
    return sorted(files)


def category_for_file(file_path, ext_to_category, fallback):
    ext = file_path.suffix.lower().lstrip(".")
    if not ext:
        return fallback
    return ext_to_category.get(ext, fallback)


def resolve_collision(dest_path):
    """
    If dest_path already exists, append ' (1)', ' (2)', etc. before
    the extension until a free name is found. Never overwrites.
    """
    if not dest_path.exists():
        return dest_path

    stem, suffix = dest_path.stem, dest_path.suffix
    counter = 1
    while True:
        candidate = dest_path.with_name(f"{stem} ({counter}){suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def build_plan(files, target_dir, ext_to_category, fallback):
    """
    Return a list of (source_path, dest_path, category) tuples
    describing where each file would move to.
    """
    plan = []
    for file_path in files:
        category = category_for_file(file_path, ext_to_category, fallback)
        dest_dir = target_dir / category
        dest_path = resolve_collision(dest_dir / file_path.name)
        plan.append((file_path, dest_path, category))
    return plan


 
# Execution
 

def execute_plan(plan):
    """Actually move the files on disk. Returns a list of log entries."""
    log_entries = []
    for source_path, dest_path, category in plan:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_path), str(dest_path))
        log_entries.append({
            "source": str(source_path),
            "destination": str(dest_path),
            "category": category,
        })
    return log_entries


def print_plan_summary(plan, target_dir, dry_run):
    if not plan:
        print("No files to organize -- the folder is already tidy (or empty).")
        return

    counts = {}
    for _, _, category in plan:
        counts[category] = counts.get(category, 0) + 1

    verb = "Would move" if dry_run else "Will move"
    print(f"\n{verb} {len(plan)} file(s) in '{target_dir}':\n")
    for category, count in sorted(counts.items()):
        plural = "file" if count == 1 else "files"
        print(f"  {category:<15} {count} {plural}")

    print()
    for source_path, dest_path, _ in plan:
        rel_dest = dest_path.relative_to(target_dir)
        print(f"  {source_path.name}  ->  {rel_dest}")


 
# Logging & undo
 

def write_log(log_entries, target_dir):
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = LOGS_DIR / f"run_{run_id}.json"

    log_data = {
        "run_id": run_id,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "target_dir": str(target_dir),
        "moves": log_entries,
    }

    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log_data, f, indent=2)

    return run_id


def list_runs():
    if not LOGS_DIR.exists() or not any(LOGS_DIR.glob("run_*.json")):
        print("No past runs recorded yet.")
        return

    print("Past runs:\n")
    for log_path in sorted(LOGS_DIR.glob("run_*.json")):
        with open(log_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(
            f"  {data['run_id']}  |  {data['timestamp']}  |  "
            f"{data['target_dir']}  |  {len(data['moves'])} file(s)"
        )


def _find_log_path(run_id):
    if run_id:
        candidate = LOGS_DIR / f"run_{run_id}.json"
        if not candidate.exists():
            print(f"Error: no run found with id '{run_id}'.")
            sys.exit(1)
        return candidate

    runs = sorted(LOGS_DIR.glob("run_*.json")) if LOGS_DIR.exists() else []
    if not runs:
        print("No past runs to undo.")
        sys.exit(1)
    return runs[-1]


def undo_run(run_id=None):
    log_path = _find_log_path(run_id)

    with open(log_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    moves = data["moves"]
    if not moves:
        print("That run didn't move any files -- nothing to undo.")
        return

    print(f"Undoing run {data['run_id']} ({len(moves)} file(s))...\n")

    restored, skipped = 0, 0
    for move in reversed(moves):
        source = Path(move["source"])
        dest = Path(move["destination"])

        if not dest.exists():
            print(f"  Skipped (already moved/deleted): {dest.name}")
            skipped += 1
            continue

        source.parent.mkdir(parents=True, exist_ok=True)
        restore_path = resolve_collision(source)
        shutil.move(str(dest), str(restore_path))
        print(f"  Restored: {dest.name} -> {restore_path}")
        restored += 1

    print(f"\nDone. Restored {restored} file(s), skipped {skipped}.")

    # Clean up now-empty category folders created by this run.
    target_dir = Path(data["target_dir"])
    for category_dir in target_dir.iterdir():
        if category_dir.is_dir() and not any(category_dir.iterdir()):
            category_dir.rmdir()

# CLI
def parse_args():
    parser = argparse.ArgumentParser(
        description="Sort a messy folder into subfolders by file type."
    )
    parser.add_argument(
        "target", nargs="?",
        help="Path to the folder to organize (e.g. ~/Downloads)."
    )
    parser.add_argument(
        "--config", default=str(DEFAULT_CONFIG_PATH),
        help="Path to a custom config.json (default: config.json next to this script)."
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would happen without moving any files."
    )
    parser.add_argument(
        "--yes", "-y", action="store_true",
        help="Skip the confirmation prompt and organize immediately."
    )
    parser.add_argument(
        "--include-hidden", action="store_true",
        help="Also include hidden files (dotfiles) in the sort."
    )
    parser.add_argument(
        "--undo", nargs="?", const="__LAST__", metavar="RUN_ID",
        help="Undo the most recent run, or a specific run id if given."
    )
    parser.add_argument(
        "--list-runs", action="store_true",
        help="List past runs (with their run ids) and exit."
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.list_runs:
        list_runs()
        return

    if args.undo is not None:
        run_id = None if args.undo == "__LAST__" else args.undo
        undo_run(run_id)
        return

    if not args.target:
        print("Error: please provide a folder to organize, e.g.:")
        print("    python organizer.py ~/Downloads")
        sys.exit(1)

    target_dir = Path(args.target).expanduser().resolve()
    if not target_dir.exists() or not target_dir.is_dir():
        print(f"Error: '{target_dir}' is not a valid directory.")
        sys.exit(1)

    ext_to_category, fallback = load_config(args.config)
    files = scan_files(target_dir, include_hidden=args.include_hidden)
    plan = build_plan(files, target_dir, ext_to_category, fallback)

    print_plan_summary(plan, target_dir, dry_run=args.dry_run)

    if not plan:
        return

    if args.dry_run:
        print("\nDry run only -- no files were moved.")
        return

    if not args.yes:
        choice = input("\nProceed with organizing these files? (y/n): ").strip().lower()
        if not choice.startswith("y"):
            print("Cancelled -- no files were moved.")
            return

    log_entries = execute_plan(plan)
    run_id = write_log(log_entries, target_dir)

    print(f"\nDone! Moved {len(log_entries)} file(s).")
    print(f"Run id: {run_id}  (use `python organizer.py --undo {run_id}` to reverse this)")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nCancelled by user. No further changes made.")
        sys.exit(0)
