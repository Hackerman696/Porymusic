from __future__ import annotations

from pathlib import Path
import mido

def inject_init_events(
    in_mid: Path,
    out_mid: Path,
    *,
    tempo_bpm: int = 120,
    program_base: int = 1,   # 1 so channel 0 becomes VOICE 1 (slot 1), not drums
    max_melodic_channels: int = 9,  # channels 0..8
) -> None:
    mid = mido.MidiFile(str(in_mid))

    # Ensure there is at least one track
    if len(mid.tracks) == 0:
        mid.tracks.append(mido.MidiTrack())

    # 1) Inject TEMPO at absolute time 0 into track 0
    tempo_msg = mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(tempo_bpm), time=0)
    mid.tracks[0].insert(0, tempo_msg)

    # 2) Inject Program Change for channels 0..8 at time 0 into track 0
    # This is simplest and most compatible with converters that scan global events.
    # Program mapping: ch0->1, ch1->2 ... ch8->9
    for ch in range(max_melodic_channels):
        prog = program_base + ch
        pc = mido.Message("program_change", channel=ch, program=prog, time=0)
        mid.tracks[0].insert(1 + ch, pc)

    # 3) Safety: if any note_on at time 0 appears before these inserts within track 0,
    # inserting at the start already guarantees init comes first in track 0.
    # Other tracks might still begin with notes at time 0, but converters typically
    # still pick up tempo/program from track 0.

    out_mid.parent.mkdir(parents=True, exist_ok=True)
    mid.save(str(out_mid))
