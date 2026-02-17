from __future__ import annotations

from pathlib import Path
import mido


def inject_init_events(
    in_mid: Path,
    out_mid: Path,
    *,
    tempo_bpm: int = 120,
    program_base: int = 1,          # ch0 -> program 1 so VOICE 1 (slot 0 reserved for drums)
    max_melodic_channels: int = 9,  # melodic channels are 0..8 when set to 9
    drum_midi_channel: int = 9,     # MIDI channel 9 == "channel 10" in DAW terms
) -> None:
    """
    Inject initialization events so mid2agb emits TEMPO + VOICE near the top of the .s output.

    What we inject at tick 0 (track 0):
      - set_tempo
      - program_change for melodic channels 0..max_melodic_channels-1:
            program = program_base + ch
      - program_change for drum channel (default 9):
            program = 0   (forces VOICE 0 on drums)

    Notes:
      - This does not move any notes between channels.
      - It tries to avoid duplicating existing set_tempo or program_change-at-time-0 messages.
    """
    mid = mido.MidiFile(str(in_mid))

    if len(mid.tracks) == 0:
        mid.tracks.append(mido.MidiTrack())

    t0 = mid.tracks[0]

    # Helper: detect whether a message already exists at time 0
    def has_meta_set_tempo_at_0(track: mido.MidiTrack) -> bool:
        return any(msg.time == 0 and msg.is_meta and msg.type == "set_tempo" for msg in track)

    def has_program_change_at_0(track: mido.MidiTrack, channel: int) -> bool:
        return any(
            msg.time == 0
            and (not msg.is_meta)
            and msg.type == "program_change"
            and getattr(msg, "channel", None) == channel
            for msg in track
        )

    inserts: list[mido.Message] = []

    # Tempo at time 0 (only if none exists at time 0)
    if not has_meta_set_tempo_at_0(t0):
        inserts.append(
            mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(int(tempo_bpm)), time=0)
        )

    # Melodic program changes at time 0
    for ch in range(int(max_melodic_channels)):
        if has_program_change_at_0(t0, ch):
            continue
        prog = int(program_base) + ch
        inserts.append(mido.Message("program_change", channel=ch, program=int(prog), time=0))

    # Drum channel program change at time 0 -> program 0 (VOICE 0)
    if drum_midi_channel is not None:
        drum_ch = int(drum_midi_channel)
        if 0 <= drum_ch <= 15 and not has_program_change_at_0(t0, drum_ch):
            inserts.append(mido.Message("program_change", channel=drum_ch, program=0, time=0))

    # Insert all at the start. Keep relative order: tempo first, then melodic PCs, then drum PC.
    # If we didn't insert tempo (already existed), we still insert PCs at the beginning.
    for i, msg in enumerate(inserts):
        t0.insert(i, msg)

    out_mid = Path(out_mid)
    out_mid.parent.mkdir(parents=True, exist_ok=True)
    mid.save(str(out_mid))
