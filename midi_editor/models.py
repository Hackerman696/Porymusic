from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class NoteEvent:
    start_tick: int
    end_tick: int
    pitch: int
    velocity: int
    channel: int
    track_index: int = 0

    def duration(self) -> int:
        return max(0, self.end_tick - self.start_tick)


@dataclass
class MidiProject:
    ticks_per_beat: int
    notes: List[NoteEvent]
    # channel -> directsound instrument id (not GM program)
    channel_instrument_id: Dict[int, int]

    # tempo used for exporting MIDI + preview injection
    tempo_bpm: int = 120

    # imported labels from MIDI track_name meta
    channel_track_name: Dict[int, str] = field(default_factory=dict)
    
    muted_channels: set[int] = field(default_factory=set)

    def used_channels(self) -> List[int]:
        return sorted({n.channel for n in self.notes})

    def notes_for_channel(self, ch: int) -> List[NoteEvent]:
        return [n for n in self.notes if n.channel == ch]

    def delete_channel(self, ch: int) -> None:
        self.notes = [n for n in self.notes if n.channel != ch]
        self.channel_instrument_id.pop(ch, None)
        self.channel_track_name.pop(ch, None)

    def merge_channel_into(self, src: int, dst: int) -> None:
        if src == dst:
            return
        for n in self.notes:
            if n.channel == src:
                n.channel = dst
        self.channel_instrument_id.pop(src, None)

        # carry label to dst if dst doesn't already have one
        if src in self.channel_track_name and dst not in self.channel_track_name:
            self.channel_track_name[dst] = self.channel_track_name[src]
        self.channel_track_name.pop(src, None)

    def swap_channels(self, a: int, b: int) -> None:
        if a == b:
            return

        for n in self.notes:
            if n.channel == a:
                n.channel = b
            elif n.channel == b:
                n.channel = a

        ida = self.channel_instrument_id.get(a)
        idb = self.channel_instrument_id.get(b)
        if ida is None:
            self.channel_instrument_id.pop(b, None)
        else:
            self.channel_instrument_id[b] = ida
        if idb is None:
            self.channel_instrument_id.pop(a, None)
        else:
            self.channel_instrument_id[a] = idb

        # swap labels too
        la = self.channel_track_name.get(a)
        lb = self.channel_track_name.get(b)
        if la is None:
            self.channel_track_name.pop(b, None)
        else:
            self.channel_track_name[b] = la
        if lb is None:
            self.channel_track_name.pop(a, None)
        else:
            self.channel_track_name[a] = lb
