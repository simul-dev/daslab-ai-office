"""Collect Git metadata only; never upload files or read credentials/DBs."""
import argparse
import datetime
import json
from pathlib import Path
import subprocess
import tempfile


def git(root, *args):
    return subprocess.check_output(
        ["git", "-C", str(root), *args], encoding="utf-8", errors="replace"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, choices=["home", "office"])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    report = {
        "schema_version": 1,
        "pc_label": args.label,
        "collected_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "repository_path": str(root),
        "head": git(root, "rev-parse", "HEAD").strip(),
        "branch": git(root, "branch", "--show-current").strip(),
        "changes_porcelain_v1_nul": git(
            root, "status", "--porcelain=v1", "-z", "--untracked-files=all"
        ).split("\0")[:-1],
        "limits": "Metadata only. Ignored files, code contents, databases and account memory are not collected.",
    }
    if args.output:
        output = args.output.resolve()
    else:
        output = Path(tempfile.mkdtemp(prefix="daslab-sync-")) / (args.label + ".json")
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    print(output)


if __name__ == "__main__":
    main()
