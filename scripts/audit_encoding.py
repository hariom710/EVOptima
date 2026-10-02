"""Report tracked text files that are not valid UTF-8 (mojibake detector).

Files written under a different codec (typically latin-1 saved from Windows)
decode as U+FFFD under UTF-8 and show up as stray '?' / 'Â' characters in
templates and labels.

Usage:
    python scripts/audit_encoding.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEXT_SUFFIXES = {".py", ".html", ".css", ".js", ".md", ".txt", ".json", ".yml", ".yaml", ".cfg", ".toml"}

# Sequences that indicate UTF-8 bytes decoded as cp1252/latin-1 and re-saved.
MOJIBAKE_MARKERS = ("Â", "â€", "â†", "Ã©", "Ã¨", "ï¿½", "�")


def main() -> int:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8"
    )
    paths = [p for p in out.stdout.splitlines() if Path(p).suffix.lower() in TEXT_SUFFIXES]

    bad_bytes: list[str] = []
    bad_markers: list[str] = []

    for rel in paths:
        full = ROOT / rel
        if not full.is_file():
            continue
        raw = full.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            bad_bytes.append(f"{rel}: NOT valid UTF-8 -> {exc}")
            # Show what the offending region actually contains.
            start = max(0, exc.start - 20)
            snippet = raw[start : exc.start + 20]
            bad_bytes.append(f"    bytes: {snippet!r}")
            continue

        found = [m for m in MOJIBAKE_MARKERS if m in text]
        if found:
            lines = []
            for i, line in enumerate(text.splitlines(), 1):
                hits = [m for m in MOJIBAKE_MARKERS if m in line]
                if hits:
                    lines.append(f"    line {i}: {line.strip()[:100]}  markers={hits}")
            bad_markers.append(f"{rel}: {', '.join(sorted(set(found)))}")
            bad_markers.extend(lines)

    print(f"scanned {len(paths)} tracked text files\n")

    if bad_bytes:
        print("== NOT valid UTF-8 ==")
        for line in bad_bytes:
            print("  " + line)
        print()

    if bad_markers:
        print("== mojibake markers present ==")
        for line in bad_markers:
            print("  " + line)
        print()

    if not bad_bytes and not bad_markers:
        print("GATE: PASS (all tracked text files are clean UTF-8)")
        return 0

    print("GATE: FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
