from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
from pathlib import Path


def assert_valid_midi(midi_path: Path) -> None:
    with midi_path.open("rb") as f:
        header = f.read(4)
    if header != b"MThd":
        raise ValueError(f"Not a valid MIDI file (missing MThd header): {midi_path}")


def update_midi_cfg(midi_cfg_path: Path, *, voicegroup: str, volume: int, reverb: int, priority: int) -> None:
    """
    Updates/creates the line for mus_preview.mid in midi.cfg.

    Format (based on your repo):
      mus_preview.mid: -E -R50 -G_route110 -V090
    """
    if not (0 <= volume <= 127 and 0 <= reverb <= 127 and 0 <= priority <= 15):
        raise ValueError("volume/reverb must be 0..127 and priority typically 0..15")

    target_key = "mus_preview.mid:"
    new_line = f"mus_preview.mid: -E -R{reverb:02d} -G_{voicegroup} -V{volume:03d} -P{priority}\n"

    lines = midi_cfg_path.read_text(encoding="utf-8").splitlines(keepends=True)
    pattern = re.compile(r"^\s*mus_preview\.mid:\s+.*$", re.IGNORECASE)

    replaced = False
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = new_line
            replaced = True
            break

    if not replaced:
        # Append at end with a newline separator if needed
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append(new_line)

    midi_cfg_path.write_text("".join(lines), encoding="utf-8")


def run_make(repo_root: Path) -> None:
    # -j1 makes errors readable; later we can speed up once stable
    subprocess.run(["make", "-j8", "pokeemerald.gba"], cwd=repo_root, check=True)


def launch_mgba(mgba_path: Path, rom_path: Path) -> None:
    subprocess.Popen([str(mgba_path), str(rom_path)])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="Path to preview repo (pokeemeraldtest)")
    ap.add_argument("--midi", required=True, help="Path to the MIDI to preview")
    ap.add_argument("--mgba", default="mgba", help="Path to mgba executable (default: mgba in PATH)")
    ap.add_argument("--voicegroup", default="route101", help="Voicegroup suffix used by midi.cfg, e.g. route101")
    ap.add_argument("--volume", type=int, default=90)
    ap.add_argument("--reverb", type=int, default=50)
    ap.add_argument("--priority", type=int, default=0)
    args = ap.parse_args()

    repo = Path(args.repo).expanduser().resolve()
    midi_in = Path(args.midi).expanduser().resolve()
    mgba = Path(args.mgba).expanduser()

    assert repo.exists(), f"Repo not found: {repo}"
    assert midi_in.exists(), f"MIDI not found: {midi_in}"

    # Validate input midi
    assert_valid_midi(midi_in)

    # Copy to preview slot (your repo structure)
    dest_mid = repo / "sound" / "songs" / "midi" / "mus_preview.mid"
    dest_mid.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(midi_in, dest_mid)

    # Update midi.cfg
    midi_cfg = repo / "sound" / "songs" / "midi" / "midi.cfg"
    update_midi_cfg(
        midi_cfg,
        voicegroup=args.voicegroup,
        volume=args.volume,
        reverb=args.reverb,
        priority=args.priority,
    )

    # Build
    run_make(repo)

    # Launch emulator
    rom = repo / "pokeemerald.gba"
    if not rom.exists():
        raise FileNotFoundError(f"ROM not found after build: {rom}")
    launch_mgba(mgba, rom)


if __name__ == "__main__":
    main()
