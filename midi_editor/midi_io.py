from __future__ import annotations

from pathlib import Path
from typing import Optional, Dict, List, Tuple

import mido

from midi_editor.models import MidiProject, NoteEvent


def _extract_tempo_bpm(mid: mido.MidiFile) -> int:
    """
    Return the first tempo found as BPM (clamped 30..300). Defaults to 120.
    """
    for tr in mid.tracks:
        for msg in tr:
            if msg.type == "set_tempo":
                bpm = int(round(mido.tempo2bpm(msg.tempo)))
                return max(30, min(300, bpm))
    return 120


def _extract_channel_track_names(mid: mido.MidiFile) -> Dict[int, str]:
    """
    Best-effort:
    - If a track has a track_name meta, assign it to any MIDI channels used in that track.
    - First name wins per channel.
    """
    out: Dict[int, str] = {}

    for tr in mid.tracks:
        track_name: Optional[str] = None
        channels_in_track: set[int] = set()

        for msg in tr:
            if msg.is_meta and msg.type == "track_name":
                name = getattr(msg, "name", None)
                if isinstance(name, str) and not track_name:
                    cleaned = name.strip()
                    track_name = cleaned if cleaned else None
                continue

            ch = getattr(msg, "channel", None)
            if isinstance(ch, int):
                channels_in_track.add(ch)

        if track_name:
            for ch in sorted(channels_in_track):
                out.setdefault(ch, track_name)

    return out


def load_midi_as_notes(midi_path: Path) -> MidiProject:
    mid = mido.MidiFile(str(midi_path))
    tempo_bpm = _extract_tempo_bpm(mid)
    track_names = _extract_channel_track_names(mid)

    notes: List[NoteEvent] = []

    for t_idx, track in enumerate(mid.tracks):
        abs_tick = 0

        # active[(channel, pitch)] = list[(start_tick, velocity)]
        active: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}

        for msg in track:
            abs_tick += msg.time

            if msg.type == "note_on" and getattr(msg, "velocity", 0) > 0:
                key = (int(msg.channel), int(msg.note))
                active.setdefault(key, []).append((abs_tick, int(msg.velocity)))

            elif msg.type == "note_off" or (msg.type == "note_on" and getattr(msg, "velocity", 0) == 0):
                ch = int(msg.channel)
                pitch = int(msg.note)
                key = (ch, pitch)
                if key in active and active[key]:
                    start_tick, vel = active[key].pop(0)
                    if abs_tick > start_tick:
                        notes.append(
                            NoteEvent(
                                start_tick=start_tick,
                                end_tick=abs_tick,
                                pitch=pitch,
                                velocity=vel,
                                channel=ch,
                                track_index=t_idx,
                            )
                        )

        # Close any stuck notes at end of track
        for (ch, pitch), stack in active.items():
            for start_tick, vel in stack:
                end_tick = max(start_tick + 1, abs_tick)
                notes.append(
                    NoteEvent(
                        start_tick=start_tick,
                        end_tick=end_tick,
                        pitch=pitch,
                        velocity=vel,
                        channel=ch,
                        track_index=t_idx,
                    )
                )

    project = MidiProject(
        ticks_per_beat=mid.ticks_per_beat,
        notes=notes,
        channel_instrument_id={},
        tempo_bpm=tempo_bpm,
        channel_track_name=track_names,
    )
    return project


def save_project_to_midi(
    project: MidiProject,
    out_path: Path,
    *,
    normalize_to_channels_0_9: bool = True,
    drop_channels_over_9: bool = True,
    force_programs_at_start: bool = False,
    write_tempo: bool = True,
) -> List[str]:
    """
    Writes a simple single-track MIDI containing:
      - optional tempo meta at tick 0 (project.tempo_bpm)
      - optional program changes at tick 0 (from project.channel_instrument_id)
      - note_on/note_off events
    Returns warnings.
    """
    warnings: List[str] = []

    notes = project.notes

    if normalize_to_channels_0_9:
        over = sorted({n.channel for n in notes if n.channel > 9})
        if over:
            warnings.append(f"Channels over 9 present: {over}. Exportable channels are 0–9.")
            if drop_channels_over_9:
                notes = [n for n in notes if n.channel <= 9]
                warnings.append("Dropped notes on channels > 9 during export.")

    mid = mido.MidiFile(ticks_per_beat=project.ticks_per_beat)
    track0 = mido.MidiTrack()
    mid.tracks.append(track0)

    # Tempo at tick 0
    if write_tempo:
        bpm = int(getattr(project, "tempo_bpm", 120))
        bpm = max(30, min(300, bpm))
        track0.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm), time=0))

    # Collect absolute-time events
    events: List[Tuple[int, mido.Message]] = []

    # Program changes at tick 0 (optional)
    if force_programs_at_start:
        for ch, program in sorted(project.channel_instrument_id.items()):
            ch_i = int(ch)
            if normalize_to_channels_0_9 and ch_i > 9:
                continue
            events.append((0, mido.Message("program_change", channel=ch_i, program=int(program), time=0)))

    # Notes
    for n in notes:
        events.append(
            (int(n.start_tick), mido.Message("note_on", channel=int(n.channel), note=int(n.pitch), velocity=int(n.velocity), time=0))
        )
        events.append(
            (int(n.end_tick), mido.Message("note_off", channel=int(n.channel), note=int(n.pitch), velocity=0, time=0))
        )

    # Sort by tick; ensure note_off happens before note_on if same tick
    def sort_key(item: Tuple[int, mido.Message]) -> Tuple[int, int]:
        tick, msg = item
        pri = 0
        if msg.type == "note_off":
            pri = -1
        return (tick, pri)

    events.sort(key=sort_key)

    # Convert absolute -> delta times and append
    last_tick = 0
    for tick, msg in events:
        delta = max(0, int(tick) - int(last_tick))
        msg.time = delta
        track0.append(msg)
        last_tick = tick

    track0.append(mido.MetaMessage("end_of_track", time=0))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mid.save(str(out_path))
    return warnings
