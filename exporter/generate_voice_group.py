#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple


@dataclass(frozen=True)
class Sample:
    symbol: str
    name: str
    bank: Optional[str] = None
    slug: Optional[str] = None


def load_directsound_db(db_path: Path) -> Tuple[Dict[str, Sample], Dict[str, Sample], Dict[str, Sample]]:
    """
    Returns three lookup maps:
      by_symbol: DirectSoundWaveData_* -> Sample
      by_slug:   slug -> Sample
      by_name:   lower(name) -> Sample

    Supports DB formats:
      - top-level list: [ {...}, {...} ]
      - wrapped dict: { "directsound": [ {...}, {...} ] }
    """
    data = json.loads(db_path.read_text(encoding="utf-8"))

    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("directsound", [])
    else:
        raise ValueError(f"Invalid DB JSON: expected list or dict, got {type(data).__name__}")

    if not isinstance(items, list):
        raise ValueError("Invalid DB JSON: expected entries to be a list")

    by_symbol: Dict[str, Sample] = {}
    by_slug: Dict[str, Sample] = {}
    by_name: Dict[str, Sample] = {}

    for it in items:
        if not isinstance(it, dict):
            continue
        sym = it.get("symbol")
        name = it.get("name")
        if not isinstance(sym, str) or not isinstance(name, str):
            continue
        sample = Sample(
            symbol=sym,
            name=name,
            bank=it.get("bank") if isinstance(it.get("bank"), str) else None,
            slug=it.get("slug") if isinstance(it.get("slug"), str) else None,
        )
        by_symbol[sym] = sample
        if sample.slug:
            by_slug[sample.slug] = sample
        by_name[name.strip().lower()] = sample

    if not by_symbol:
        raise ValueError("DB JSON contains no valid directsound entries")

    return by_symbol, by_slug, by_name


def resolve_sample(
    token: str, *, by_symbol: Dict[str, Sample], by_slug: Dict[str, Sample], by_name: Dict[str, Sample]
) -> Sample:
    t = token.strip()
    if not t:
        raise ValueError("Empty instrument token")

    # Try exact symbol
    if t in by_symbol:
        return by_symbol[t]

    # Try slug
    if t in by_slug:
        return by_slug[t]

    # Try case-insensitive name
    t_low = t.lower()
    if t_low in by_name:
        return by_name[t_low]

    # Try a forgiving name match (remove extra spaces)
    t_low_norm = " ".join(t_low.split())
    for k, v in by_name.items():
        if " ".join(k.split()) == t_low_norm:
            return v

    raise KeyError(f"Instrument not found in DB: {token}")


def make_voice_directsound_line(symbol: str, *, key: int, pan: int, a: int, b: int, c: int, d: int) -> str:
    # Matches your observed pattern:
    # voice_directsound 60, 0, DirectSoundWaveData_sc88pro_flute, 255, 127, 231, 127
    return f"\tvoice_directsound {key}, {pan}, {symbol}, {a}, {b}, {c}, {d}"


def write_voicegroup_file(
    out_path: Path,
    group_name: str,
    samples: List[Sample],
    *,
    pad_to_128: bool,
    pad_with_square: bool,
    key: int,
    pan: int,
    ds_params: Tuple[int, int, int, int],
) -> None:
    a, b, c, d = ds_params

    lines: List[str] = []
    # Your repo format example uses: voice_group route101
    lines.append(f"voice_group {group_name}")

    # Always reserve slot 0 for the RS drumset
    lines.append("\tvoice_keysplit_all voicegroup_rs_drumset")

    # Emit chosen samples after that (starting at slot 1)
    for s in samples:
        lines.append(make_voice_directsound_line(s.symbol, key=key, pan=pan, a=a, b=b, c=c, d=d))

    # Pad remaining slots to 128
    if pad_to_128:
        # +1 because drumset occupies slot 0
        remaining = 128 - (1 + len(samples))

        if remaining < 0:
            raise ValueError(f"Too many instruments ({len(samples)}). Max is 128.")

        if pad_with_square:
            # Safe placeholder patch: your common square line
            pad_line = "\tvoice_square_1 60, 0, 0, 2, 0, 0, 15, 0"
            lines.extend([pad_line] * remaining)
        else:
            # Or pad with repeats of first sample (valid)
            pad_sym = samples[0].symbol if samples else "DirectSoundWaveData_sc88pro_flute"
            lines.extend(
                [make_voice_directsound_line(pad_sym, key=key, pan=pan, a=a, b=b, c=c, d=d)] * remaining
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build a pokeemerald voicegroup .inc from a DirectSound JSON database."
    )
    ap.add_argument("--db", type=Path, required=True, help="Path to directsound_samples.json")
    ap.add_argument("--repo", type=Path, required=False, help="Path to pokeemerald repo root (optional if --out is used)")
    ap.add_argument("--name", required=True, help="Voicegroup name (e.g., voicegroup_custom_test)")
    ap.add_argument(
        "--pick",
        action="append",
        default=[],
        help="Pick an instrument by symbol, slug, or exact name. Repeat up to 127 times (slot 0 reserved).",
    )
    ap.add_argument("--pad", action="store_true", help="Pad to 128 entries")
    ap.add_argument("--pad-with-square", action="store_true", help="Pad using a square-wave placeholder patch")
    ap.add_argument("--key", type=int, default=60, help="Root key (default 60)")
    ap.add_argument("--pan", type=int, default=0, help="Pan (default 0)")
    ap.add_argument("--out", type=Path, default=None, help="Optional output .inc path")
    ap.add_argument(
        "--ds-params",
        type=int,
        nargs=4,
        metavar=("A", "B", "C", "D"),
        default=(255, 127, 231, 127),
        help="DirectSound params (4 ints). Default is a common flute-like tuple.",
    )
    args = ap.parse_args()

    if not args.pick:
        raise SystemExit("Provide at least one --pick (symbol, slug, or name).")

    by_symbol, by_slug, by_name = load_directsound_db(args.db)

    chosen: List[Sample] = []
    for tok in args.pick:
        chosen.append(resolve_sample(tok, by_symbol=by_symbol, by_slug=by_slug, by_name=by_name))

    max_picks = 127
    if len(chosen) > max_picks:
        raise SystemExit(
            f"Too many picks: {len(chosen)}. Max is {max_picks} (slot 0 reserved for drumset)."
        )

    if args.out:
        out_path = args.out.expanduser().resolve()
    else:
        if not args.repo:
            raise SystemExit("Missing --repo (required when --out is not provided).")
        out_path = (args.repo / "sound" / "voicegroups" / f"{args.name}.inc").resolve()

    write_voicegroup_file(
        out_path,
        args.name,
        chosen,
        pad_to_128=args.pad,
        pad_with_square=args.pad_with_square,
        key=args.key,
        pan=args.pan,
        ds_params=tuple(args.ds_params),
    )

    print(f"Wrote voicegroup: {out_path}")
    print("Chosen instruments:")
    for s in chosen:
        print(f" - {s.name} ({s.symbol})")


if __name__ == "__main__":
    main()
