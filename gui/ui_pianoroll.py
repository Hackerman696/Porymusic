from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict

from qtpy import QtCore, QtGui, QtWidgets

from midi_editor.models import MidiProject, NoteEvent
from midi_editor.config import DrumDef


@dataclass
class PianoRollMetrics:
    tick_px: float = 0.05     # horizontal zoom (time)
    key_px: float = 10.0      # vertical zoom (pitch)
    pitch_min: int = 24
    pitch_max: int = 96


class NoteItem(QtWidgets.QGraphicsRectItem):
    def __init__(self, note: NoteEvent, rect: QtCore.QRectF, color: QtGui.QColor):
        super().__init__(rect)
        self.note = note
        self.color = color
        self.setFlag(QtWidgets.QGraphicsItem.ItemIsSelectable, True)

    def paint(self, painter: QtGui.QPainter, option, widget=None):
        pen = QtGui.QPen(QtGui.QColor(40, 40, 40))
        if self.isSelected():
            pen.setWidth(2)
        painter.setPen(pen)
        painter.setBrush(QtGui.QBrush(self.color))
        painter.drawRect(self.rect())


class PianoRollView(QtWidgets.QGraphicsView):
    selection_changed = QtCore.Signal()

    NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

    @staticmethod
    def midi_note_name(n: int) -> str:
        # MIDI note 60 = C4
        octave = (n // 12) - 1
        name = PianoRollView.NOTE_NAMES[n % 12]
        return f"{name}{octave}"

    def __init__(self, drums_by_note: Dict[int, DrumDef], parent=None):
        super().__init__(parent)
        self._scene = QtWidgets.QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QtGui.QPainter.Antialiasing, False)
        self.setDragMode(QtWidgets.QGraphicsView.RubberBandDrag)

        self.metrics = PianoRollMetrics()
        self.project: Optional[MidiProject] = None
        self.drums_by_note = drums_by_note

        self._scene.selectionChanged.connect(self.selection_changed)

        # Small UX: let the view accept focus so keybinds work reliably
        self.setFocusPolicy(QtCore.Qt.StrongFocus)

    def set_project(self, project: Optional[MidiProject]) -> None:
        self.project = project
        self.redraw()

    def _color_for_channel(self, ch: int) -> QtGui.QColor:
        # Drums
        if ch == 9:
            return QtGui.QColor(220, 200, 120)

        # Stable HSV palette by channel index
        hue = (ch * 36) % 360
        c = QtGui.QColor()
        c.setHsv(hue, 140, 220)
        return c

    def wheelEvent(self, event: QtGui.QWheelEvent) -> None:
        mods = event.modifiers()
        delta = event.angleDelta().y()
        factor = 1.15 if delta > 0 else (1 / 1.15)

        if mods & QtCore.Qt.ControlModifier:
            # Horizontal zoom (time)
            self.metrics.tick_px = max(0.005, min(2.0, self.metrics.tick_px * factor))
            self.redraw()
            event.accept()
            return

        if mods & QtCore.Qt.AltModifier:
            # Vertical zoom (pitch)
            self.metrics.key_px = max(4.0, min(40.0, self.metrics.key_px * factor))
            self.redraw()
            event.accept()
            return

        super().wheelEvent(event)

    def _note_info_text(self, note: NoteEvent) -> str:
        pitch_name = self.midi_note_name(note.pitch)

        if note.channel == 9:
            dd = self.drums_by_note.get(note.pitch)
            if dd:
                extra = f" • {dd.category}" if getattr(dd, "category", None) else ""
                return f"Ch 9 Drums • {pitch_name} (MIDI {note.pitch}) • {dd.name}{extra}"
            return f"Ch 9 Drums • {pitch_name} (MIDI {note.pitch}) • Unmapped"
        else:
            return f"Ch {note.channel} • {pitch_name} (MIDI {note.pitch}) • vel {note.velocity}"

    def _note_item_at_view_pos(self, view_pos: QtCore.QPoint) -> Optional[NoteItem]:
        scene_pos = self.mapToScene(view_pos)
        items = self._scene.items(scene_pos)
        for it in items:
            if isinstance(it, NoteItem):
                return it
        return None

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        # Let selection work as normal
        super().mousePressEvent(event)

        # After selection, show the note info on click as well
        item = self._note_item_at_view_pos(event.pos())
        if not item:
            return

        text = self._note_info_text(item.note)

        # Show tooltip immediately on click
        QtWidgets.QToolTip.showText(event.globalPos(), text, self)

        # Also pin it in the main window status bar if available
        mw = self.window()
        if isinstance(mw, QtWidgets.QMainWindow) and mw.statusBar():
            mw.statusBar().showMessage(text, 8000)

    def redraw(self) -> None:
        self._scene.clear()
        if not self.project:
            return

        notes = self.project.notes
        if not notes:
            return

        m = self.metrics
        max_tick = max(n.end_tick for n in notes)
        width = max_tick * m.tick_px + 200
        height = (m.pitch_max - m.pitch_min + 1) * m.key_px + 40
        self._scene.setSceneRect(0, 0, width, height)

        # Grid lines
        grid_pen = QtGui.QPen(QtGui.QColor(230, 230, 230))
        for p in range(m.pitch_min, m.pitch_max + 1):
            y = (m.pitch_max - p) * m.key_px
            self._scene.addLine(0, y, width, y, grid_pen)

        # Notes
        for n in notes:
            if n.pitch < m.pitch_min or n.pitch > m.pitch_max:
                continue

            x = n.start_tick * m.tick_px
            w = max(1.0, (n.end_tick - n.start_tick) * m.tick_px)
            y = (m.pitch_max - n.pitch) * m.key_px
            h = m.key_px

            color = self._color_for_channel(n.channel)
            item = NoteItem(n, QtCore.QRectF(x, y, w, h), color)

            # Tooltip: include note name + drum sample if ch9
            item.setToolTip(self._note_info_text(n))

            self._scene.addItem(item)

    def delete_selected_notes(self) -> int:
        if not self.project:
            return 0
        selected_items = [it for it in self._scene.selectedItems() if isinstance(it, NoteItem)]
        if not selected_items:
            return 0

        selected_notes = {it.note for it in selected_items}
        before = len(self.project.notes)
        self.project.notes = [n for n in self.project.notes if n not in selected_notes]
        removed = before - len(self.project.notes)
        self.redraw()
        return removed
