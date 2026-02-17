from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import json


@dataclass(frozen=True)
class InstrumentDef:
    id: int                 # your directsound id
    name: str
    symbol: str | None = None
    bank: str | None = None
    slug: str | None = None


@dataclass(frozen=True)
class DrumDef:
    midi_note: int
    name: str
    category: str | None = None
    symbol: str | None = None
    index: int | None = None


@dataclass
class AppConfig:
    project_root: Path
    instruments: list[InstrumentDef]
    drums_by_note: dict[int, DrumDef]  # midi_note -> DrumDef

    preview_repo: str
    mgba_path: str
    db_path: str    

    @property
    def resources_dir(self) -> Path:
        return self.project_root / "resources"

    @property
    def temp_preview_midi_path(self) -> Path:
        return self.resources_dir / "mus_preview.mid"

    @property
    def resources_midi_dir(self) -> Path:
        return self.project_root / "resources" / "midi" 

    @property
    def temp_preview_midi_path(self) -> Path:
        return self.resources_midi_dir / "mus_preview.mid"

def load_directsound_samples_json(path: Path) -> list[InstrumentDef]:
    data = json.loads(path.read_text(encoding="utf-8"))

    # Your file is a top-level list: [ {id, name, ...}, ... ]
    if isinstance(data, list):
        ds = data
    elif isinstance(data, dict):
        ds = data.get("directsound", [])
    else:
        raise ValueError(f"Unexpected JSON root type in {path}: {type(data).__name__}")

    out: list[InstrumentDef] = []
    for item in ds:
        out.append(
            InstrumentDef(
                id=int(item["id"]),
                name=str(item.get("name", f"Instrument {item['id']}")),
                symbol=item.get("symbol"),
                bank=item.get("bank"),
                slug=item.get("slug"),
            )
        )
    out.sort(key=lambda x: (x.bank or "", x.name.lower(), x.id))
    return out


def load_runtime_config_json(path: Path) -> dict[str, str]:
    data = json.loads(path.read_text(encoding="utf-8"))

    # You said your JSON has repo, mgba, db keys.
    # We resolve relative paths relative to project root in app.py (below).
    return {
        "repo": str(data["repo"]),
        "mgba": str(data["mgba"]),
        "db": str(data["db"]),
    }


def load_rs_drums_json(path: Path) -> dict[int, DrumDef]:
    data = json.loads(path.read_text(encoding="utf-8"))
    notes = data.get("notes", [])
    out: dict[int, DrumDef] = {}
    for item in notes:
        midi_note = int(item["midi_note"])
        out[midi_note] = DrumDef(
            midi_note=midi_note,
            name=str(item.get("name", f"Drum {midi_note}")),
            category=item.get("category"),
            symbol=item.get("symbol"),
            index=item.get("index"),
        )
    return out
