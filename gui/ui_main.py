from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

from qtpy import QtCore, QtGui, QtWidgets

from midi_editor.midi_init_injector import inject_init_events
from midi_editor.config import AppConfig
from midi_editor.models import MidiProject
from midi_editor.midi_io import load_midi_as_notes, save_project_to_midi
from gui.ui_pianoroll import PianoRollView

class SearchableComboBox(QtWidgets.QComboBox):
    """
    Normal-looking combobox, but you can type to search.
    Keeps the old dropdown aesthetic: the displayed text stays as the current selection.
    """
    def __init__(self, parent=None):
        super().__init__(parent)

        # We use an internal lineEdit for completer, but keep it read-only so it looks like a normal combo.
        self.setEditable(True)
        le = self.lineEdit()
        if le:
            le.setReadOnly(True)
            le.setCursor(QtCore.Qt.ArrowCursor)  # avoid text cursor vibe

        self.setInsertPolicy(QtWidgets.QComboBox.NoInsert)

        self._search = ""
        self._reset_timer = QtCore.QTimer(self)
        self._reset_timer.setSingleShot(True)
        self._reset_timer.timeout.connect(self._reset_search)

        comp = QtWidgets.QCompleter(self)
        comp.setModel(self.model())
        comp.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        comp.setFilterMode(QtCore.Qt.MatchContains)
        comp.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
        comp.activated[str].connect(self._on_completer_activated)
        self.setCompleter(comp)

    def _reset_search(self) -> None:
        self._search = ""
        c = self.completer()
        if c:
            c.setCompletionPrefix("")
            c.popup().hide()

    def _on_completer_activated(self, text: str) -> None:
        idx = self.findText(text, QtCore.Qt.MatchExactly)
        if idx >= 0:
            self.setCurrentIndex(idx)
        self._reset_search()

    def keyPressEvent(self, event: QtGui.QKeyEvent) -> None:
        key = event.key()
        txt = event.text()

        # Let navigation keys behave normally
        if key in (
            QtCore.Qt.Key_Up,
            QtCore.Qt.Key_Down,
            QtCore.Qt.Key_PageUp,
            QtCore.Qt.Key_PageDown,
            QtCore.Qt.Key_Home,
            QtCore.Qt.Key_End,
            QtCore.Qt.Key_Return,
            QtCore.Qt.Key_Enter,
            QtCore.Qt.Key_Escape,
            QtCore.Qt.Key_Tab,
            QtCore.Qt.Key_Backtab,
        ):
            super().keyPressEvent(event)
            return

        # Build search buffer
        if key == QtCore.Qt.Key_Backspace:
            self._search = self._search[:-1]
        elif txt and not event.modifiers() & (QtCore.Qt.ControlModifier | QtCore.Qt.AltModifier | QtCore.Qt.MetaModifier):
            # Add printable chars
            if txt.isprintable():
                self._search += txt

        if not self._search:
            super().keyPressEvent(event)
            return

        # Show completer popup with "contains" matching
        c = self.completer()
        if c:
            c.setCompletionPrefix(self._search)
            # Ensure popup shows something useful
            c.complete()

        # Reset buffer after a short pause
        self._reset_timer.start(900)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, cfg: AppConfig):
        super().__init__()
        self.cfg = cfg
        self.project: Optional[MidiProject] = None
        self.current_midi_path: Optional[Path] = None

        self.setWindowTitle("MIDI Editor (Preview + Export)")
        self.resize(1200, 700)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)

        layout = QtWidgets.QHBoxLayout(central)

        # Left: channel controls
        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)

        self.lbl_warning = QtWidgets.QLabel("")
        self.lbl_warning.setWordWrap(True)
        left_layout.addWidget(self.lbl_warning)

        # Columns:
        # 0: Channel (with color swatch)
        # 1: Mute checkbox
        # 2: Role (with imported track label)
        # 3: Instrument
        # 4: Notes
        self.channel_table = QtWidgets.QTableWidget(0, 5)
        self.channel_table.setHorizontalHeaderLabels(["Ch", "Mute", "Role", "Instrument", "Notes"])
        self.channel_table.horizontalHeader().setStretchLastSection(True)
        self.channel_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        left_layout.addWidget(self.channel_table)

        btn_row = QtWidgets.QHBoxLayout()
        self.btn_delete_channel = QtWidgets.QPushButton("Delete Channel Contents")
        self.btn_swap = QtWidgets.QPushButton("Swap Channels")
        self.btn_merge = QtWidgets.QPushButton("Merge Channels")
        btn_row.addWidget(self.btn_delete_channel)
        btn_row.addWidget(self.btn_swap)
        btn_row.addWidget(self.btn_merge)
        left_layout.addLayout(btn_row)

        #Drum Remap
        self.btn_remap_drums = QtWidgets.QPushButton("Auto Remap Drums (GM → RS)")
        left_layout.addWidget(self.btn_remap_drums)
        self.btn_remap_drums.clicked.connect(self.auto_remap_drums)
        self.btn_manual_remap_drums = QtWidgets.QPushButton("Manual Remap Drums…")
        left_layout.addWidget(self.btn_manual_remap_drums)
        self.btn_manual_remap_drums.clicked.connect(self.manual_remap_drums)

        # Preview settings
        self.spin_volume = QtWidgets.QSpinBox()
        self.spin_volume.setRange(0, 100)
        self.spin_volume.setValue(80)

        self.spin_reverb = QtWidgets.QSpinBox()
        self.spin_reverb.setRange(0, 100)
        self.spin_reverb.setValue(50)

        self.spin_priority = QtWidgets.QSpinBox()
        self.spin_priority.setRange(0, 100)
        self.spin_priority.setValue(0)

        # BPM control (also saved to exported MIDI via project.tempo_bpm)
        self.spin_bpm = QtWidgets.QSpinBox()
        self.spin_bpm.setRange(30, 300)
        self.spin_bpm.setValue(120)
        self.spin_bpm.valueChanged.connect(self.on_bpm_changed)

        form = QtWidgets.QFormLayout()
        form.addRow("Volume", self.spin_volume)
        form.addRow("Reverb", self.spin_reverb)
        form.addRow("Priority", self.spin_priority)
        form.addRow("Tempo (BPM)", self.spin_bpm)
        left_layout.addLayout(form)

        self.btn_preview = QtWidgets.QPushButton("Preview Full Song")
        self.btn_export = QtWidgets.QPushButton("Export MIDI + INC")
        left_layout.addWidget(self.btn_preview)
        left_layout.addWidget(self.btn_export)

        layout.addWidget(left, 0)

        # Right: piano roll
        self.pianoroll = PianoRollView(drums_by_note=self.cfg.drums_by_note)
        layout.addWidget(self.pianoroll, 1)

        # Menu
        open_action = QtWidgets.QAction("Open MIDI", self)
        open_action.triggered.connect(self.open_midi)

        save_action = QtWidgets.QAction("Save Project As MIDI", self)
        save_action.triggered.connect(self.save_project_as_midi)

        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")
        file_menu.addAction(open_action)
        file_menu.addAction(save_action)

        # Connections
        self.btn_delete_channel.clicked.connect(self.delete_selected_channel_contents)
        self.btn_swap.clicked.connect(self.swap_channels_dialog)
        self.btn_merge.clicked.connect(self.merge_channels_dialog)
        self.btn_preview.clicked.connect(self.preview_full_song)
        self.btn_export.clicked.connect(self.export_assets_dialog)
        self.channel_table.cellChanged.connect(self.on_channel_cell_changed)

        # Keybind for deleting notes
        delete_shortcut = QtWidgets.QShortcut(QtCore.Qt.Key_Delete, self)
        delete_shortcut.activated.connect(self.on_delete_key)

    def _prompt_unmapped_drums(self, unmapped: set[int]) -> dict[int, int]:
        """
        Ask user how to remap unmapped drum pitches (channel 9).
        Returns mapping old_pitch -> new_pitch.
        Empty dict means user cancelled or chose nothing.
        """
        from midi_editor.drum_remap import GM_NOTE_TO_NAME
        from gui.ui_pianoroll import PianoRollView

        # Build RS choices from rs_drums.json (cfg.drums_by_note)
        rs_items: list[tuple[int, str]] = []
        for k, dd in self.cfg.drums_by_note.items():
            try:
                rs_note = int(k)
            except Exception:
                continue

            name = (getattr(dd, "name", "") or "").strip()
            cat = (getattr(dd, "category", "") or "").strip()

            if not name or name.lower() == "empty slot" or cat == "empty":
                continue

            label = f"{rs_note} ({PianoRollView.midi_note_name(rs_note)}) - {name}"
            if cat:
                label += f" [{cat}]"
            rs_items.append((rs_note, label))

        rs_items.sort(key=lambda t: t[0])

        if not rs_items:
            QtWidgets.QMessageBox.warning(self, "Manual Drum Remap", "No RS drum definitions available.")
            return {}

        # Create a dialog with one dropdown per unmapped pitch
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Manual Drum Remap")
        layout = QtWidgets.QVBoxLayout(dlg)

        info = QtWidgets.QLabel(
            "These channel 9 pitches could not be auto-mapped.\n"
            "Choose an RS drum for each, or set to Skip to leave unchanged."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QtWidgets.QFormLayout()
        layout.addLayout(form)

        combos: dict[int, QtWidgets.QComboBox] = {}

        # Add a Skip option at top
        skip_label = "Skip (leave unchanged)"
        for pitch in sorted(unmapped):
            gm_name = GM_NOTE_TO_NAME.get(int(pitch), "")
            pitch_label = f"{pitch} ({PianoRollView.midi_note_name(int(pitch))})"
            if gm_name:
                pitch_label += f" - GM: {gm_name}"

            combo = QtWidgets.QComboBox()
            combo.addItem(skip_label, userData=None)
            combo.addItem("Delete notes with this pitch", userData="__DELETE__")
            for rs_note, label in rs_items:
                combo.addItem(label, userData=int(rs_note))

            combos[int(pitch)] = combo
            form.addRow(pitch_label, combo)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        layout.addWidget(btns)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)

        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return {}

        mapping: dict[int, object] = {}
        for old_pitch, combo in combos.items():
            new_pitch = combo.currentData()

            # Skip
            if new_pitch is None:
                continue

            # Delete option
            if new_pitch == "__DELETE__":
                mapping[int(old_pitch)] = "__DELETE__"
                continue

            # Normal remap (RS pitch or MIDI pitch)
            mapping[int(old_pitch)] = int(new_pitch)

        return mapping
    def _make_searchable_instrument_combo(
        self,
        *,
        ch: int,
        display_labels: list[str],
        id_by_label: dict[str, int],
        label_by_id: dict[int, str],
        default_inst_id: int,
        default_label: str,
    ) -> QtWidgets.QComboBox:
        """
        Create a searchable instrument combobox:
        - editable, so user can type
        - completer popup with case-insensitive contains matching
        - keeps selection synced with channel_instrument_id
        """
        combo = QtWidgets.QComboBox()
        combo.setEditable(True)
        combo.addItems(display_labels)

        # Make typing behave like search, not like "edit the current item"
        line = combo.lineEdit()
        if line:
            line.setPlaceholderText("Search…")
            line.setClearButtonEnabled(False)
    
        # Completer with popup filtering
        completer = QtWidgets.QCompleter(display_labels, combo)
        completer.setCaseSensitivity(QtCore.Qt.CaseInsensitive)
        completer.setFilterMode(QtCore.Qt.MatchContains)
        completer.setCompletionMode(QtWidgets.QCompleter.PopupCompletion)
        combo.setCompleter(completer)

        # Make it feel like a search box: on focus/click, highlight so typing replaces the current value.
        if line:
            line.setPlaceholderText("Search… (type to filter)")
            line.installEventFilter(self)

        # Ensure model has an instrument id for this channel
        assert self.project is not None
        self.project.channel_instrument_id.setdefault(ch, default_inst_id)
        current_id = self.project.channel_instrument_id.get(ch, default_inst_id)

        # Set current selection
        combo.setCurrentText(label_by_id.get(current_id, default_label))

        def _apply_label(label: str) -> None:
            # If user typed something that is not an exact label, snap back
            if label not in id_by_label:
                combo.blockSignals(True)
                combo.setCurrentText(label_by_id.get(current_id, default_label))
                combo.blockSignals(False)
                return
            self.set_channel_instrument_id(ch, id_by_label.get(label, default_inst_id))

        # Trigger when user picks from list OR confirms typed text
        combo.currentTextChanged.connect(_apply_label)
        if line:
            line.editingFinished.connect(lambda: _apply_label(combo.currentText()))

        return combo

    def eventFilter(self, obj, event):
        if event.type() == QtCore.QEvent.FocusIn:
            # Select all so typing instantly replaces the current instrument name
            if isinstance(obj, QtWidgets.QLineEdit):
                QtCore.QTimer.singleShot(0, obj.selectAll)
        return super().eventFilter(obj, event)

    def manual_remap_drums(self) -> None:
        if not self.project:
            return

        # Collect all drum pitches currently used on channel 9
        used = sorted({int(n.pitch) for n in self.project.notes if n.channel == 9})
        if not used:
            QtWidgets.QMessageBox.information(self, "Manual Remap Drums", "No notes found on channel 9.")
            return

        from gui.ui_pianoroll import PianoRollView
        from midi_editor.drum_remap import GM_NOTE_TO_NAME

        # Build RS drum choices (plus allow arbitrary target note)
        rs_notes = sorted(int(k) for k in self.cfg.drums_by_note.keys())
        rs_choices = []
        for rn in rs_notes:
            dd = self.cfg.drums_by_note.get(rn)
            name = (getattr(dd, "name", "") or "").strip() if dd else ""
            cat = (getattr(dd, "category", "") or "").strip() if dd else ""
            macro = (getattr(dd, "macro", "") or "").strip() if dd else ""

            # Keep empties, because they can be square placeholders, but label them honestly
            if name.lower() == "empty slot" and macro.lower().startswith("voice_square"):
                name = "Square wave (placeholder)"

            label = f"{rn} ({PianoRollView.midi_note_name(rn)})"
            if name:
                label += f" - {name}"
            if cat:
                label += f" [{cat}]"
            rs_choices.append((rn, label))

        # Dialog UI
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Manual Remap Drums")
        dlg.resize(700, 450)
        layout = QtWidgets.QVBoxLayout(dlg)

        top = QtWidgets.QLabel(
            "For each channel 9 drum pitch, choose:\n"
            "• Leave unchanged\n"
            "• Remap to another pitch (RS or any MIDI note)\n"
            "• Delete all notes using that pitch"
        )
        top.setWordWrap(True)
        layout.addWidget(top)

        table = QtWidgets.QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["Source", "Action", "Target (RS)", "Target (Any MIDI)"])
        table.horizontalHeader().setStretchLastSection(True)
        table.setRowCount(len(used))
        layout.addWidget(table)

        action_by_pitch = {}
        rs_combo_by_pitch = {}
        midi_spin_by_pitch = {}

        for row, src in enumerate(used):
            gm_name = GM_NOTE_TO_NAME.get(int(src), "")
            src_label = f"{src} ({PianoRollView.midi_note_name(src)})"
            if gm_name:
                src_label += f" - GM: {gm_name}"

            item = QtWidgets.QTableWidgetItem(src_label)
            item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
            table.setItem(row, 0, item)

            action = QtWidgets.QComboBox()
            action.addItem("Leave unchanged", userData="leave")
            action.addItem("Remap to RS pitch", userData="rs")
            action.addItem("Remap to any MIDI pitch", userData="midi")
            action.addItem("Delete notes with this pitch", userData="delete")
            table.setCellWidget(row, 1, action)

            rs_combo = QtWidgets.QComboBox()
            for rn, label in rs_choices:
                rs_combo.addItem(label, userData=int(rn))
            table.setCellWidget(row, 2, rs_combo)

            midi_spin = QtWidgets.QSpinBox()
            midi_spin.setRange(0, 127)
            midi_spin.setValue(int(src))  # default to itself
            table.setCellWidget(row, 3, midi_spin)

            action_by_pitch[src] = action
            rs_combo_by_pitch[src] = rs_combo
            midi_spin_by_pitch[src] = midi_spin

            # Enable/disable target widgets based on action
            def _sync_widgets(_idx: int, src_pitch=src) -> None:
                mode = action_by_pitch[src_pitch].currentData()
                rs_combo_by_pitch[src_pitch].setEnabled(mode == "rs")
                midi_spin_by_pitch[src_pitch].setEnabled(mode == "midi")

            action.currentIndexChanged.connect(_sync_widgets)
            _sync_widgets(0)

        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        layout.addWidget(btns)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)

        if dlg.exec_() != QtWidgets.QDialog.Accepted:
            return

        # Build operations
        delete_set = set()
        remap_map = {}

        for src in used:
            mode = action_by_pitch[src].currentData()
            if mode == "leave":
                continue
            if mode == "delete":
                delete_set.add(int(src))
                continue
            if mode == "rs":
                remap_map[int(src)] = int(rs_combo_by_pitch[src].currentData())
                continue
            if mode == "midi":
                remap_map[int(src)] = int(midi_spin_by_pitch[src].value())
                continue

        if not delete_set and not remap_map:
            QtWidgets.QMessageBox.information(self, "Manual Remap Drums", "No changes selected.")
            return

        # Apply delete first
        if delete_set:
            self.project.notes = [
                n for n in self.project.notes
                if not (n.channel == 9 and int(n.pitch) in delete_set)
            ]

        # Apply remap
        changed = 0
        for n in self.project.notes:
            if n.channel != 9:
                continue
            old = int(n.pitch)
            new = remap_map.get(old)
            if new is None:
                continue
            if int(new) != old:
                n.pitch = int(new)
                changed += 1

        self.pianoroll.redraw()
        self.refresh_channel_table()

        QtWidgets.QMessageBox.information(
            self,
            "Manual Remap Drums",
            f"Done.\nRemapped notes: {changed}\nDeleted pitches: {sorted(delete_set) if delete_set else 'None'}",
        )



    def on_bpm_changed(self, value: int) -> None:
        if not self.project:
            return
        self.project.tempo_bpm = int(value)

    def on_delete_key(self) -> None:
        if not self.project:
            return
        removed = self.pianoroll.delete_selected_notes()
        if removed:
            self.refresh_channel_table()

    def open_midi(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Open MIDI", "", "MIDI Files (*.mid *.midi)"
        )
        if not path:
            return

        midi_path = Path(path)
        self.current_midi_path = midi_path
        self.project = load_midi_as_notes(midi_path)

        # Populate BPM from imported MIDI tempo (or default)
        self.spin_bpm.setValue(int(getattr(self.project, "tempo_bpm", 120)))

        # Ensure muted_channels exists even for older projects
        if not hasattr(self.project, "muted_channels") or self.project.muted_channels is None:
            self.project.muted_channels = set()

        # Default instrument selections for channels that appear (except drums on 9).
        default_id = self.cfg.instruments[0].id if self.cfg.instruments else 0
        for ch in self.project.used_channels():
            if ch == 9:
                continue
            self.project.channel_instrument_id.setdefault(ch, default_id)

        self.pianoroll.set_project(self.project)
        self.refresh_channel_table()

    def _color_for_channel(self, ch: int) -> QtGui.QColor:
        if ch == 9:
            return QtGui.QColor(220, 200, 120)
        hue = (ch * 36) % 360
        c = QtGui.QColor()
        c.setHsv(hue, 140, 220)
        return c

    def _channel_cell_widget(self, ch: int) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        swatch = QtWidgets.QLabel()
        swatch.setFixedSize(12, 12)
        swatch.setStyleSheet(
            f"background-color: {self._color_for_channel(ch).name()}; border: 1px solid #444;"
        )

        lbl = QtWidgets.QLabel(str(ch))
        lbl.setAlignment(QtCore.Qt.AlignVCenter | QtCore.Qt.AlignLeft)

        lay.addWidget(swatch)
        lay.addWidget(lbl)
        lay.addStretch(1)
        return w

    def _on_mute_changed(self, state: int) -> None:
        """
        Stable slot: reads channel from sender().property("channel").
        Avoids lambda capture issues during table rebuilds.
        """
        if not self.project:
            return
        cb = self.sender()
        if not isinstance(cb, QtWidgets.QCheckBox):
            return
        ch = cb.property("channel")
        if not isinstance(ch, int):
            return

        if not hasattr(self.project, "muted_channels") or self.project.muted_channels is None:
            self.project.muted_channels = set()

        if state == QtCore.Qt.Checked:
            self.project.muted_channels.add(ch)
        else:
            self.project.muted_channels.discard(ch)

    def _filtered_project_copy(self) -> MidiProject:
        """
        Return a safe copy of the project with muted channels removed.
        Source of truth for mute is the UI table checkboxes (not project.muted_channels),
        so mute works even if Qt signals are flaky.
        """
        assert self.project is not None

        muted = self._muted_channels_from_table()

        # Keep project.muted_channels in sync for future use/debugging
        try:
            self.project.muted_channels = set(muted)
        except Exception:
            pass

        filtered_notes = []
        for n in self.project.notes:
            if n.channel in muted:
                continue
            filtered_notes.append(type(n)(
                start_tick=n.start_tick,
                end_tick=n.end_tick,
                pitch=n.pitch,
                velocity=n.velocity,
                channel=n.channel,
                track_index=getattr(n, "track_index", 0),
            ))

        return MidiProject(
            ticks_per_beat=self.project.ticks_per_beat,
            notes=filtered_notes,
            channel_instrument_id=dict(self.project.channel_instrument_id),
            tempo_bpm=int(getattr(self.project, "tempo_bpm", 120)),
            channel_track_name=dict(getattr(self.project, "channel_track_name", {})),
            muted_channels=set(muted),
        )


        return MidiProject(
            ticks_per_beat=self.project.ticks_per_beat,
            notes=filtered_notes,
            channel_instrument_id=dict(self.project.channel_instrument_id),
            tempo_bpm=int(getattr(self.project, "tempo_bpm", 120)),
            channel_track_name=dict(getattr(self.project, "channel_track_name", {})),
            muted_channels=set(muted),
        )

    def _channel_number_from_row(self, row: int) -> Optional[int]:
        w = self.channel_table.cellWidget(row, 0)
        if not w:
            return None
        for lbl in w.findChildren(QtWidgets.QLabel):
            t = lbl.text().strip()
            if t.isdigit():
                return int(t)
        return None


    def _muted_channels_from_table(self) -> set[int]:
        muted: set[int] = set()
        for row in range(self.channel_table.rowCount()):
            cb = self.channel_table.cellWidget(row, 1)
            if not isinstance(cb, QtWidgets.QCheckBox):
                continue
            if not cb.isChecked():
                continue
            ch = self._channel_number_from_row(row)
            if ch is not None:
                muted.add(ch)
        return muted


    def refresh_channel_table(self) -> None:
        self.channel_table.blockSignals(True)
        self.channel_table.setRowCount(0)

        if not self.project:
            self.lbl_warning.setText("")
            self.channel_table.blockSignals(False)
            return

        used = self.project.used_channels()
        has_overflow = any(c > 9 for c in used)

        warning_lines: list[str] = []
        if has_overflow:
            warning_lines.append("Warning: MIDI uses channels above 9. Preview/Export cuts to channels 0–9.")
        self.lbl_warning.setText("\n".join(warning_lines))

        instruments = self.cfg.instruments

        # Build display labels once per refresh
        display_labels: list[str] = []
        id_by_label: dict[str, int] = {}
        label_by_id: dict[int, str] = {}

        for inst in instruments:
            label = f"{inst.name} [{inst.bank}]" if inst.bank else inst.name
            display_labels.append(label)
            id_by_label[label] = inst.id
            label_by_id[inst.id] = label

        default_inst_id = instruments[0].id if instruments else 0
        default_label = display_labels[0] if display_labels else "No instruments loaded"

        muted = getattr(self.project, "muted_channels", set()) or set()

        for ch in used:
            row = self.channel_table.rowCount()
            self.channel_table.insertRow(row)

            # Column 0: Channel + color swatch
            self.channel_table.setCellWidget(row, 0, self._channel_cell_widget(ch))

            # Column 1: Mute checkbox (property-based channel id)
            mute = QtWidgets.QCheckBox()
            mute.setProperty("channel", ch)
            mute.blockSignals(True)
            mute.setChecked(ch in muted)
            mute.blockSignals(False)
            mute.stateChanged.connect(self._on_mute_changed)
            self.channel_table.setCellWidget(row, 1, mute)

            # Column 2: Role + imported track label
            role = "Drums" if ch == 9 else "Melodic"
            trk_label = (getattr(self.project, "channel_track_name", {}).get(ch) or "").strip()
            if trk_label:
                role = f"{role} ({trk_label})"
            item_role = QtWidgets.QTableWidgetItem(role)
            item_role.setFlags(item_role.flags() & ~QtCore.Qt.ItemIsEditable)
            self.channel_table.setItem(row, 2, item_role)

            # Column 3: Instrument combo (normal-looking, type-to-search)
            if ch == 9:
                combo = QtWidgets.QComboBox()
                combo.addItem("Drums (channel 9)")
                combo.setEnabled(False)
            else:
                combo = SearchableComboBox()
                combo.addItems(display_labels)

                # Ensure we have a selection stored for this channel
                self.project.channel_instrument_id.setdefault(ch, default_inst_id)
                current_id = self.project.channel_instrument_id.get(ch, default_inst_id)

                # Set shown selection
                combo.blockSignals(True)
                combo.setCurrentText(label_by_id.get(current_id, default_label))
                combo.blockSignals(False)

                # Update mapping when selection changes
                def _on_changed(label: str, ch=ch) -> None:
                    inst_id = id_by_label.get(label)
                    if inst_id is None:
                        return
                    self.set_channel_instrument_id(ch, inst_id)

                combo.currentTextChanged.connect(_on_changed)

            self.channel_table.setCellWidget(row, 3, combo)

            # Column 4: Notes count
            ncount = len(self.project.notes_for_channel(ch))
            item_notes = QtWidgets.QTableWidgetItem(str(ncount))
            item_notes.setFlags(item_notes.flags() & ~QtCore.Qt.ItemIsEditable)
            self.channel_table.setItem(row, 4, item_notes)

        self.channel_table.blockSignals(False)

    def set_channel_instrument_id(self, ch: int, inst_id: int) -> None:
        if not self.project or ch == 9:
            return
        self.project.channel_instrument_id[ch] = int(inst_id)

    def on_channel_cell_changed(self, row: int, col: int) -> None:
        pass

    def selected_channel(self) -> Optional[int]:
        rows = self.channel_table.selectionModel().selectedRows()
        if not rows:
            return None
        row = rows[0].row()

        w = self.channel_table.cellWidget(row, 0)
        if not w:
            return None
        labels = w.findChildren(QtWidgets.QLabel)
        for lbl in labels:
            txt = lbl.text().strip()
            if txt.isdigit():
                return int(txt)
        return None

    def build_pick_names_for_channels_0_8(self) -> list[str]:
        assert self.project is not None
        instruments = self.cfg.instruments
        if not instruments:
            return [""] * 9

        name_by_id = {inst.id: inst.name for inst in instruments}
        default_id = instruments[0].id

        picks: list[str] = []
        for ch in range(0, 9):
            inst_id = self.project.channel_instrument_id.get(ch, default_id)
            picks.append(name_by_id.get(inst_id, instruments[0].name))
        return picks

    def delete_selected_channel_contents(self) -> None:
        if not self.project:
            return
        ch = self.selected_channel()
        if ch is None:
            return
        self.project.delete_channel(ch)
        self.pianoroll.redraw()
        self.refresh_channel_table()

    def swap_channels_dialog(self) -> None:
        if not self.project:
            return
        used = self.project.used_channels()
        if len(used) < 2:
            return

        a, ok = QtWidgets.QInputDialog.getInt(
            self, "Swap Channels", "First channel:", value=used[0], min=0, max=127
        )
        if not ok:
            return
        b, ok = QtWidgets.QInputDialog.getInt(
            self, "Swap Channels", "Second channel:", value=used[1], min=0, max=127
        )
        if not ok:
            return

        self.project.swap_channels(a, b)
        self.pianoroll.redraw()
        self.refresh_channel_table()

    def merge_channels_dialog(self) -> None:
        if not self.project:
            return
        used = self.project.used_channels()
        if len(used) < 2:
            return

        src, ok = QtWidgets.QInputDialog.getInt(
            self,
            "Merge Channels",
            "Source channel (move notes from):",
            value=used[0],
            min=0,
            max=127,
        )
        if not ok:
            return
        dst, ok = QtWidgets.QInputDialog.getInt(
            self,
            "Merge Channels",
            "Destination channel (move notes into):",
            value=used[1],
            min=0,
            max=127,
        )
        if not ok:
            return

        self.project.merge_channel_into(src, dst)
        self.pianoroll.redraw()
        self.refresh_channel_table()

    def save_project_as_midi(self) -> None:
        if not self.project:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save MIDI As", "", "MIDI Files (*.mid)"
        )
        if not path:
            return
        out_path = Path(path)

        proj_out = self._filtered_project_copy()
        warnings = save_project_to_midi(
            proj_out,
            out_path,
            normalize_to_channels_0_9=False,
            force_programs_at_start=False,
        )
        if warnings:
            QtWidgets.QMessageBox.information(self, "Saved with notes", "\n".join(warnings))

    def _run_cmd(self, cmd: list[str], title: str) -> bool:
        res = subprocess.run(
            cmd,
            cwd=str(self.cfg.project_root),
            text=True,
            capture_output=True,
        )
        if res.returncode != 0:
            msg = (res.stderr or res.stdout or "Unknown error").strip()
            QtWidgets.QMessageBox.critical(self, title, msg)
            return False
        return True

    def preview_full_song(self) -> None:
        if not self.project:
            return

        self.cfg.resources_midi_dir.mkdir(parents=True, exist_ok=True)

        proj_out = self._filtered_project_copy()

        warnings = save_project_to_midi(
            proj_out,
            self.cfg.temp_preview_midi_path,
            normalize_to_channels_0_9=True,
            drop_channels_over_9=True,
            force_programs_at_start=False,
        )

        if warnings:
            resp = QtWidgets.QMessageBox.warning(
                self,
                "Preview warning",
                "\n".join(warnings) + "\n\nContinue preview (channels > 9 will be cut)?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            )
            if resp != QtWidgets.QMessageBox.Yes:
                return

        injected_mid = self.cfg.resources_midi_dir / "mus_preview_init.mid"
        bpm = int(self.spin_bpm.value())

        inject_init_events(
            Path(self.cfg.temp_preview_midi_path),
            Path(injected_mid),
            tempo_bpm=bpm,
            program_base=1,
            max_melodic_channels=9,
        )

        picks = self.build_pick_names_for_channels_0_8()

        voicegroup_name = "test"
        inc_out = Path(self.cfg.preview_repo) / "sound" / "voicegroups" / f"{voicegroup_name}.inc"

        gen_cmd = [
            "python3",
            "exporter/generate_voice_group.py",
            "--db",
            self.cfg.db_path,
            "--name",
            voicegroup_name,
        ]
        for p in picks:
            gen_cmd += ["--pick", p]
        gen_cmd += ["--pad", "--pad-with-square", "--out", str(inc_out)]

        if not self._run_cmd(gen_cmd, "Voicegroup generation failed"):
            return

        vol = int(self.spin_volume.value())
        rev = int(self.spin_reverb.value())
        pri = int(self.spin_priority.value())

        prev_cmd = [
            "python3",
            "preview_engine/preview_runner.py",
            "--repo",
            self.cfg.preview_repo,
            "--midi",
            str(injected_mid),
            "--mgba",
            self.cfg.mgba_path,
            "--voicegroup",
            voicegroup_name,
            "--volume",
            str(vol),
            "--reverb",
            str(rev),
            "--priority",
            str(pri),
        ]

        self._run_cmd(prev_cmd, "Preview failed")

    def auto_remap_drums(self) -> None:
        if not self.project:
            return

        # If no channel 9 notes, no-op
        has_drums = any(n.channel == 9 for n in self.project.notes)
        if not has_drums:
            QtWidgets.QMessageBox.information(self, "Auto Remap Drums", "No notes found on channel 9.")
            return

        resp = QtWidgets.QMessageBox.question(
            self,
            "Auto Remap Drums",
            "This will remap channel 9 drum notes from common MIDI drum layout (GM) into your RS drumset.\n\nContinue?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
        )
        if resp != QtWidgets.QMessageBox.Yes:
            return

        from midi_editor.drum_remap import remap_channel_9_notes_in_place, GM_NOTE_TO_NAME

        changed_auto, unmapped = remap_channel_9_notes_in_place(
            self.project.notes,
            self.cfg.drums_by_note,
            keep_unmapped=True,
        )

        changed_manual = 0
        deleted_manual = 0

        if unmapped:
            mapping = self._prompt_unmapped_drums(unmapped)
            if mapping:
                # 1) Delete requests
                delete_pitches = {int(k) for k, v in mapping.items() if v == "__DELETE__"}
                if delete_pitches:
                    before = len(self.project.notes)
                    self.project.notes = [
                        n for n in self.project.notes
                        if not (n.channel == 9 and int(n.pitch) in delete_pitches)
                    ]
                    deleted_manual = before - len(self.project.notes)

                # 2) Remap requests
                for n in self.project.notes:
                    if n.channel != 9:
                        continue
                    old = int(n.pitch)
                    new = mapping.get(old)
                    if new is None or new == "__DELETE__":
                        continue
                    new_i = int(new)
                    if new_i != old:
                        n.pitch = new_i
                        changed_manual += 1

                # 3) Recompute unmapped after manual changes:
                # pitches still not in RS drumset
                rs_valid = set(int(k) for k in self.cfg.drums_by_note.keys())
                unmapped = {
                    int(n.pitch)
                    for n in self.project.notes
                    if n.channel == 9 and int(n.pitch) not in rs_valid
                }

        self.pianoroll.redraw()
        self.refresh_channel_table()

        msg = [
            "Remap complete.",
            f"Auto changed notes: {changed_auto}",
            f"Manual changed notes: {changed_manual}",
        ]
        if deleted_manual:
            msg.append(f"Manual deleted notes: {deleted_manual}")

        if unmapped:
            show = sorted(list(unmapped))[:24]
            pretty = []
            for p in show:
                nm = GM_NOTE_TO_NAME.get(int(p))
                pretty.append(f"{p} ({nm})" if nm else str(p))

            msg.append("")
            msg.append("Unmapped drum pitches (left unchanged):")
            msg.append(", ".join(pretty))
            if len(unmapped) > len(show):
                msg.append(f"... and {len(unmapped) - len(show)} more.")

        QtWidgets.QMessageBox.information(self, "Auto Remap Drums", "\n".join(msg))


    def export_assets_dialog(self) -> None:
        if not self.project:
            return

        folder = QtWidgets.QFileDialog.getExistingDirectory(self, "Choose export folder")
        if not folder:
            return
        out_dir = Path(folder)

        name, ok = QtWidgets.QInputDialog.getText(
            self, "Song name", "Song name (used for files and --name):"
        )
        if not ok or not name.strip():
            return
        name = name.strip()

        out_mid = out_dir / f"{name}.mid"
        out_inc = out_dir / f"{name}.inc"

        proj_out = self._filtered_project_copy()

        warnings = save_project_to_midi(
            proj_out,
            out_mid,
            normalize_to_channels_0_9=True,
            drop_channels_over_9=True,
            force_programs_at_start=False,
        )

        if warnings:
            resp = QtWidgets.QMessageBox.warning(
                self,
                "Export warning",
                "\n".join(warnings) + "\n\nContinue export (channels > 9 will be cut)?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            )
            if resp != QtWidgets.QMessageBox.Yes:
                return

        picks = self.build_pick_names_for_channels_0_8()
        gen_cmd = [
            "python3",
            "exporter/generate_voice_group.py",
            "--db",
            self.cfg.db_path,
            "--name",
            name,
        ]
        for p in picks:
            gen_cmd += ["--pick", p]
        gen_cmd += ["--pad", "--pad-with-square", "--out", str(out_inc)]

        if not self._run_cmd(gen_cmd, "Voicegroup generation failed"):
            return

        QtWidgets.QMessageBox.information(
            self,
            "Export complete",
            f"Saved:\n{out_mid}\n{out_inc}",
        )
