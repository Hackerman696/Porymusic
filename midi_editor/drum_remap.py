from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

GM_NOTE_TO_NAME: Dict[int, str] = {
    27: "High Q",
    35: "Acoustic Bass Drum",
    36: "Bass Drum 1",
    38: "Acoustic Snare",
    39: "Hand Clap",
    40: "Electric Snare",
    42: "Closed Hi-Hat",
    46: "Open Hi-Hat",
    49: "Crash Cymbal 1",
    55: "Splash/Crash (alt)",
    57: "Crash Cymbal 2",
}

# GM drum note -> category we want in RS
GM_NOTE_TO_CATEGORY: Dict[int, str] = {
    35: "kick",
    36: "kick",
    38: "snare",
    40: "snare",
    39: "clap",
    42: "hihat",
    46: "hihat",
    49: "crash",
    55: "crash",
    57: "crash",
}


def _norm(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"\([^)]*\)", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def _get(info: Any, key: str, default: Any = None) -> Any:
    # dict or DrumDef-like object
    if isinstance(info, dict):
        return info.get(key, default)
    return getattr(info, key, default)


def _build_rs_category_to_notes(rs_drums_by_note: Dict[Any, Any]) -> Dict[str, List[int]]:
    cat_to_notes: Dict[str, List[int]] = {}
    for k, info in rs_drums_by_note.items():
        try:
            midi_note = int(k)
        except Exception:
            continue

        name = str(_get(info, "name", "") or "")
        cat = str(_get(info, "category", "") or "")
        if not name or not cat:
            continue
        if cat == "empty" or _norm(name) == "empty slot":
            continue

        cat_to_notes.setdefault(cat, []).append(midi_note)

    for cat in cat_to_notes:
        cat_to_notes[cat].sort()
    return cat_to_notes


def _preferred_rs_note(cat: str, cat_to_notes: Dict[str, List[int]]) -> Optional[int]:
    notes = cat_to_notes.get(cat) or []
    if not notes:
        return None

    preferred = {
        "kick": 36,
        "snare": 38,
        "hihat": 42,
        "crash": 55,
        "clap": 39,
        "tom": 45,
        "bell": 53,
        "shaker": 54,
    }.get(cat)

    if preferred is not None and preferred in notes:
        return preferred
    return notes[0]


def _build_gm_to_rs(rs_drums_by_note: Dict[Any, Any]) -> Dict[int, int]:
    cat_to_notes = _build_rs_category_to_notes(rs_drums_by_note)
    cat_to_pref: Dict[str, int] = {}
    for cat in cat_to_notes.keys():
        p = _preferred_rs_note(cat, cat_to_notes)
        if p is not None:
            cat_to_pref[cat] = p

    gm_to_rs: Dict[int, int] = {}
    for gm_note, cat in GM_NOTE_TO_CATEGORY.items():
        rs_note = cat_to_pref.get(cat)
        if rs_note is not None:
            gm_to_rs[gm_note] = rs_note
    return gm_to_rs


def remap_channel_9_notes_in_place(
    notes: List[object],
    rs_drums_by_note: Dict[Any, Any],
    *,
    keep_unmapped: bool = True,
) -> Tuple[int, Set[int]]:
    """
    Mutates NoteEvent.pitch for channel 9 notes.
    Returns: (changed_count, unmapped_original_pitches)
    """
    gm_to_rs = _build_gm_to_rs(rs_drums_by_note)

    rs_valid: Set[int] = set()
    for k in rs_drums_by_note.keys():
        try:
            rs_valid.add(int(k))
        except Exception:
            pass

    changed = 0
    unmapped: Set[int] = set()

    for n in notes:
        if getattr(n, "channel", None) != 9:
            continue
        pitch = int(getattr(n, "pitch"))

        if pitch in gm_to_rs:
            new_pitch = int(gm_to_rs[pitch])
            if new_pitch != pitch:
                setattr(n, "pitch", new_pitch)
                changed += 1
            continue

        if pitch in rs_valid:
            continue

        unmapped.add(pitch)
        if not keep_unmapped:
            pass

    return changed, unmapped
