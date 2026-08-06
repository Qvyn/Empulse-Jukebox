from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Signal, Qt
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget


class WaveformWidget(QWidget):
    rangeChanged = Signal(int, int)
    seekRequested = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(105)
        self.envelope: list[float] = []
        self.duration_ms = 0
        self.start_ms = 0
        self.end_ms = 0
        self._dragging = ""

    def set_waveform(self, envelope: list[float], duration_ms: int) -> None:
        self.envelope = envelope
        self.duration_ms = duration_ms
        self.update()

    def set_range(self, start_ms: int, end_ms: int) -> None:
        self.start_ms = max(0, start_ms)
        self.end_ms = max(0, end_ms)
        self.update()

    def _x_for_time(self, milliseconds: int) -> float:
        return self.width() * milliseconds / max(1, self.duration_ms)

    def _time_for_x(self, x: float) -> int:
        return round(max(0.0, min(float(self.width()), x)) * self.duration_ms / max(1, self.width()))

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#0b0e10"))
        if not self.envelope:
            painter.setPen(QColor("#737b81"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Select a track or analyze a song")
            return
        center = self.height() / 2
        path = QPainterPath(QPointF(0, center))
        for index, amplitude in enumerate(self.envelope):
            x = index * self.width() / max(1, len(self.envelope) - 1)
            height = amplitude * (self.height() * 0.43)
            path.lineTo(x, center - height)
            path.lineTo(x, center + height)
        painter.setPen(QPen(QColor("#81945c"), 1))
        painter.drawPath(path)
        start_x = self._x_for_time(self.start_ms)
        end_value = self.end_ms or self.duration_ms
        end_x = self._x_for_time(end_value)
        painter.fillRect(QRectF(start_x, 0, max(1, end_x - start_x), self.height()), QColor(199, 233, 120, 42))
        painter.setPen(QPen(QColor("#d9ff80"), 2))
        painter.drawLine(round(start_x), 0, round(start_x), self.height())
        painter.drawLine(round(end_x), 0, round(end_x), self.height())

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self.duration_ms:
            return
        start_x = self._x_for_time(self.start_ms)
        end_x = self._x_for_time(self.end_ms or self.duration_ms)
        if abs(event.position().x() - start_x) <= 9:
            self._dragging = "start"
        elif abs(event.position().x() - end_x) <= 9:
            self._dragging = "end"
        else:
            self.seekRequested.emit(self._time_for_x(event.position().x()))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self._dragging:
            return
        value = self._time_for_x(event.position().x())
        if self._dragging == "start":
            self.start_ms = min(value, (self.end_ms or self.duration_ms) - 100)
        else:
            self.end_ms = max(value, self.start_ms + 100)
        self.rangeChanged.emit(self.start_ms, self.end_ms)
        self.update()

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:
        self._dragging = ""
