from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

DS_SYMBOL_RE = re.compile(r"\b(DirectSoundWaveData_[A-Za-z0-9_]+)\b")

@dataclass
class SampleEntry:
    id: int
    symbol: str
    name: str
    bank: Optional[str]
    slug: str
    sources: List[str]


def normalize_symbol(symbol: str) -> Tuple[Optional[str], str]:
    base = symbol
    if base.startswith("DirectSoundWaveData_"):
        base = base[len("DirectSoundWaveData_"):]
    bank = None
    for b in ("sc88pro", "sc55", "sc88", "gm", "gs", "xg"):
        pref = b + "_"
        if base.startswith(pref):
            bank = b
            base = base[len(pref):]
            break
    return bank, base


def pretty_name_from_base(base: str) -> str:
    words = base.strip("_").split("_")
    replacements = {
        "str": "String",
        "gtr": "Guitar",
        "fx": "FX",
        "sfx": "SFX",
        "perc": "Percussion",
        "syn": "Synth",
        "rnd": "Rnd",
        "tr": "TR",
    }

    out = []
    for w in words:
        wl = w.lower()
        out.append(replacements.get(wl, w))

    titled = []
    for w in out:
        if w in ("FX", "SFX", "TR"):
            titled.append(w)
        else:
            titled.append(w.capitalize())
    return " ".join(titled).strip()


def slugify(bank: Optional[str], base: str) -> str:
    raw = f"{bank}-{base}" if bank else base
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")


def iter_text_files(repo_root: Path) -> List[Path]:
    exts = {".inc", ".s", ".c", ".h", ".txt", ".cfg", ".json", ".asm"}
    paths: List[Path] = []

    # Prefer sound/ but also allow whole-repo scan
    sound_dir = repo_root / "sound"
    if sound_dir.exists():
        for p in sound_dir.rglob("*"):
            if p.is_file() and p.suffix.lower() in exts:
                paths.append(p)
    else:
        for p in repo_root.rglob("*"):
            if p.is_file() and p.suffix.lower() in exts:
                paths.append(p)

    return paths


def scan_directsound_symbols(repo_root: Path, *, debug: bool = False) -> Dict[str, Set[str]]:
    files = iter_text_files(repo_root)
    if debug:
        print(f"Scanning {len(files)} files under: {repo_root}")

    hits: Dict[str, Set[str]] = {}
    for p in files:
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue

        found = DS_SYMBOL_RE.findall(text)
        if not found:
            continue

        rel = str(p.relative_to(repo_root))
        for sym in set(found):
            hits.setdefault(sym, set()).add(rel)

    if debug:
        print(f"Found {len(hits)} unique DirectSoundWaveData_* symbols.")
    return hits


def build_entries(symbol_sources: Dict[str, Set[str]]) -> List[SampleEntry]:
    items: List[Tuple[str, str, str, Optional[str], str, List[str]]] = []
    for sym, sources in symbol_sources.items():
        bank, base = normalize_symbol(sym)
        name = pretty_name_from_base(base)
        slug = slugify(bank, base)
        items.append((name.lower(), sym.lower(), sym, bank, slug, sorted(sources)))

    # stable ordering: name then symbol
    items.sort(key=lambda t: (t[0], t[1]))

    out: List[SampleEntry] = []
    for i, (_nk, _sk, sym, bank, slug, sources) in enumerate(items):
        bank2, base2 = normalize_symbol(sym)
        out.append(
            SampleEntry(
                id=i,
                symbol=sym,
                name=pretty_name_from_base(base2),
                bank=bank2,
                slug=slugify(bank2, base2),
                sources=sources,
            )
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo_root", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=Path("directsound_samples.json"))
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    repo_root = args.repo_root.expanduser().resolve()
    if not repo_root.exists() or not repo_root.is_dir():
        raise SystemExit(f"--repo_root is not a directory: {repo_root}")

    symbol_sources = scan_directsound_symbols(repo_root, debug=args.debug)
    entries = build_entries(symbol_sources)

    # Flat array output, exactly like your example
    args.out.write_text(json.dumps([asdict(e) for e in entries], indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {args.out} ({len(entries)} samples).")


if __name__ == "__main__":
    main()
