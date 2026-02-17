from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple


# Matches DirectSoundWaveData_sc88pro_flute etc
DS_SYMBOL_RE = re.compile(r"\b(DirectSoundWaveData_[A-Za-z0-9_]+)\b")


@dataclass
class SampleEntry:
    symbol: str
    name: str
    bank: Optional[str]
    slug: str
    sources: List[str]


def normalize_symbol(symbol: str) -> Tuple[Optional[str], str]:
    """
    DirectSoundWaveData_sc88pro_nylon_str_guitar -> (bank="sc88pro", base="nylon_str_guitar")
    DirectSoundWaveData_flute -> (bank=None, base="flute")
    """
    base = symbol.removeprefix("DirectSoundWaveData_")
    bank = None

    # common bank prefixes found in decomps (tweak freely)
    for b in ("sc88pro", "sc55", "sc88", "gm", "gs", "xg"):
        prefix = b + "_"
        if base.startswith(prefix):
            bank = b
            base = base[len(prefix):]
            break

    return bank, base


def pretty_name_from_base(base: str) -> str:
    """
    nylon_str_guitar -> Nylon String Guitar
    fretless_bass -> Fretless Bass
    """
    words = base.strip("_").split("_")

    replacements = {
        "str": "String",
        "gtr": "Guitar",
        "fx": "FX",
        "sfx": "SFX",
        "perc": "Percussion",
        "syn": "Synth",
    }

    out_words = []
    for w in words:
        wl = w.lower()
        out_words.append(replacements.get(wl, w))

    # Title case, but preserve all-caps tokens like FX/SFX if present
    titled = []
    for w in out_words:
        if w.isupper():
            titled.append(w)
        else:
            titled.append(w.capitalize())
    return " ".join(titled).strip()


def slugify(base: str) -> str:
    # stable id you can use in UI
    return re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")


def iter_text_files(repo_root: Path) -> List[Path]:
    """
    Collect likely text-ish files to scan fast.
    You can expand extensions as needed.
    """
    exts = {".inc", ".s", ".c", ".h", ".txt", ".cfg", ".json", ".asm"}
    paths: List[Path] = []

    # Focus on sound first for speed, then fallback to whole repo if needed
    sound_dir = repo_root / "sound"
    if sound_dir.exists():
        for p in sound_dir.rglob("*"):
            if p.is_file() and p.suffix.lower() in exts:
                paths.append(p)

    # Also include voicegroups or other folders if they exist outside sound
    vg_dir = repo_root / "sound" / "voicegroups"
    if vg_dir.exists():
        for p in vg_dir.rglob("*"):
            if p.is_file() and p.suffix.lower() in exts and p not in paths:
                paths.append(p)

    # If nothing found, scan repo for those exts
    if not paths:
        for p in repo_root.rglob("*"):
            if p.is_file() and p.suffix.lower() in exts:
                paths.append(p)

    return paths


def scan_directsound_symbols(repo_root: Path) -> Dict[str, Set[str]]:
    """
    Returns: symbol -> set(relative_source_paths)
    """
    files = iter_text_files(repo_root)
    hits: Dict[str, Set[str]] = {}

    for p in files:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        found = set(DS_SYMBOL_RE.findall(text))
        if not found:
            continue

        rel = str(p.relative_to(repo_root)) if p.is_relative_to(repo_root) else str(p)
        for sym in found:
            hits.setdefault(sym, set()).add(rel)

    return hits


def build_entries(symbol_sources: Dict[str, Set[str]]) -> List[SampleEntry]:
    entries: List[SampleEntry] = []
    for sym, sources in symbol_sources.items():
        bank, base = normalize_symbol(sym)
        name = pretty_name_from_base(base)
        slug = slugify(base if bank is None else f"{bank}-{base}")
        entries.append(
            SampleEntry(
                symbol=sym,
                name=name,
                bank=bank,
                slug=slug,
                sources=sorted(sources),
            )
        )
    # sort nicely by name then symbol
    entries.sort(key=lambda e: (e.name.lower(), e.symbol.lower()))
    return entries


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo_root", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=Path("directsound_samples.json"))
    args = ap.parse_args()

    repo_root = args.repo_root.resolve()
    symbol_sources = scan_directsound_symbols(repo_root)
    entries = build_entries(symbol_sources)

    out_obj = {
        "repo_root": str(repo_root),
        "directsound_count": len(entries),
        "directsound": [asdict(e) for e in entries],
        "synth_placeholders": [
            {"id": "square_wave", "name": "Square Wave"},
            {"id": "noise", "name": "Noise"},
            {"id": "noise_alt", "name": "Noise Alt"},
            {"id": "programmable_wave", "name": "Programmable Wave"},
        ],
    }

    args.out.write_text(json.dumps(out_obj, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {args.out} ({len(entries)} DirectSound samples).")


if __name__ == "__main__":
    main()
