from __future__ import annotations

import sys
from pathlib import Path

from qtpy import QtWidgets

from gui.ui_main import MainWindow
from midi_editor.config import (
    AppConfig,
    load_directsound_samples_json,
    load_rs_drums_json,
    load_runtime_config_json,
)


def resolve(root: Path, p: str) -> str:
    """
    Resolve a path from config.json.
    - Absolute paths are returned as-is.
    - Relative paths are resolved relative to repo root.
    - Leading "./" is stripped.
    """
    p = p.strip()
    if p.startswith("./"):
        p = p[2:]
    if Path(p).is_absolute():
        return p
    return str((root / p).resolve())


def main() -> int:
    # Repo root (contains midi_editor/, gui/, exporter/, preview_engine/, resources/)
    root = Path(__file__).resolve().parent.parent

    # Data folder lives inside midi_editor/data/
    data_dir = root / "midi_editor" / "data"
    runtime_cfg_path = data_dir / "config.json"

    if not runtime_cfg_path.exists():
        raise FileNotFoundError(f"Missing config.json: {runtime_cfg_path}")

    runtime_cfg = load_runtime_config_json(runtime_cfg_path)

    preview_repo = resolve(root, runtime_cfg["repo"])
    mgba_path = resolve(root, runtime_cfg["mgba"])
    db_path = resolve(root, runtime_cfg["db"])

    instruments_path = data_dir / "directsound_samples.json"
    drums_path = data_dir / "rs_drums.json"

    if not instruments_path.exists():
        raise FileNotFoundError(f"Missing directsound_samples.json: {instruments_path}")
    if not drums_path.exists():
        raise FileNotFoundError(f"Missing rs_drums.json: {drums_path}")

    cfg = AppConfig(
        project_root=root,
        instruments=load_directsound_samples_json(instruments_path),
        drums_by_note=load_rs_drums_json(drums_path),
        preview_repo=preview_repo,
        mgba_path=mgba_path,
        db_path=db_path,
    )

    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow(cfg)
    w.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
