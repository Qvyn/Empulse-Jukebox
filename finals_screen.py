"""Lightweight whole-screen visual state detection for THE FINALS.

The detector never opens or hooks the game process. It asks Qt/Windows for the
visible desktop image, immediately downsizes it to a tiny grayscale fingerprint,
and compares that fingerprint with user-captured state references.

This is intentionally simple for the prototype: full-screen input, tiny working
representation, one scan per second, no OCR, OpenCV, ML model, or frame history.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QObject, QRect, QTimer, Qt, Signal
from PySide6.QtGui import QGuiApplication, QImage, QPainter


FINGERPRINT_WIDTH = 96
FINGERPRINT_HEIGHT = 54
MAX_REFERENCES_PER_STATE = 6
DEFAULT_INTERVAL_MS = 1000
DEFAULT_MIN_SIMILARITY = 0.70


@dataclass(frozen=True)
class MatchResult:
    state: str
    similarity: float


def _mean_similarity(left: list[int], right: list[int]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    difference = sum(abs(a - b) for a, b in zip(left, right))
    return max(0.0, 1.0 - difference / (255.0 * len(left)))


class ScreenStateDetector(QObject):
    state_changed = Signal(str, float)
    status_changed = Signal(str)

    def __init__(
        self,
        settings,
        ignore_rect: Callable[[], QRect | None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.settings = settings
        self.ignore_rect = ignore_rect
        self.timer = QTimer(self)
        self.timer.setInterval(DEFAULT_INTERVAL_MS)
        self.timer.timeout.connect(self.scan)
        self.current_state = ""
        self._candidate_state = ""
        self._candidate_hits = 0
        self._last_mask_rect: QRect | None = None
        self.settings.data.setdefault("screen_refs", {})
        self.settings.data.setdefault("screen_min_similarity", DEFAULT_MIN_SIMILARITY)

    def start(self) -> None:
        if not self.timer.isActive():
            self.timer.start()
        self.status_changed.emit("Whole-screen scan active")
        QTimer.singleShot(0, self.scan)

    def stop(self) -> None:
        self.timer.stop()
        self._candidate_state = ""
        self._candidate_hits = 0
        self.status_changed.emit("Screen scan stopped")

    def reference_count(self, state: str) -> int:
        refs = self.settings.data.get("screen_refs", {}).get(state, [])
        return len(refs) if isinstance(refs, list) else 0

    def clear_references(self, state: str) -> None:
        refs = self.settings.data.setdefault("screen_refs", {})
        refs[state] = []
        self.settings.save()
        self.status_changed.emit(f"Cleared screen references for {state.replace('_', ' ').title()}")

    def capture_reference(self, state: str) -> bool:
        fingerprint = self._capture_fingerprint()
        if not fingerprint:
            self.status_changed.emit("Screen capture failed")
            return False
        refs = self.settings.data.setdefault("screen_refs", {}).setdefault(state, [])
        if not isinstance(refs, list):
            refs = []
            self.settings.data["screen_refs"][state] = refs
        refs.append(fingerprint)
        if len(refs) > MAX_REFERENCES_PER_STATE:
            del refs[:-MAX_REFERENCES_PER_STATE]
        self.settings.save()
        self.status_changed.emit(
            f"Learned {state.replace('_', ' ').title()} screen ({len(refs)} reference{'s' if len(refs) != 1 else ''})"
        )
        return True

    def scan(self) -> None:
        refs_by_state = self.settings.data.get("screen_refs", {})
        if not any(isinstance(refs, list) and refs for refs in refs_by_state.values()):
            self.status_changed.emit("Whole-screen scan active — capture state references")
            return

        fingerprint = self._capture_fingerprint()
        if not fingerprint:
            self.status_changed.emit("Whole-screen capture unavailable")
            return

        match = self._best_match(fingerprint)
        if not match.state:
            self.status_changed.emit("Screen state unknown")
            return

        minimum = float(self.settings.data.get("screen_min_similarity", DEFAULT_MIN_SIMILARITY))
        if match.similarity < minimum:
            self._candidate_state = ""
            self._candidate_hits = 0
            self.status_changed.emit(
                f"Screen state uncertain ({match.state.replace('_', ' ')} {match.similarity:.0%})"
            )
            return

        self.status_changed.emit(
            f"Screen: {match.state.replace('_', ' ').title()} ({match.similarity:.0%})"
        )

        # Require two consecutive scans before switching music. This prevents a
        # single transition frame or desktop notification from flipping state.
        if match.state == self.current_state:
            self._candidate_state = ""
            self._candidate_hits = 0
            return
        if match.state == self._candidate_state:
            self._candidate_hits += 1
        else:
            self._candidate_state = match.state
            self._candidate_hits = 1
        if self._candidate_hits >= 2:
            self.current_state = match.state
            self._candidate_state = ""
            self._candidate_hits = 0
            self.state_changed.emit(match.state, match.similarity)

    def _best_match(self, fingerprint: list[int]) -> MatchResult:
        best_state = ""
        best_similarity = 0.0
        for state, references in self.settings.data.get("screen_refs", {}).items():
            if not isinstance(references, list):
                continue
            # A state may have several samples so animated menu/background
            # content does not have to resemble one exact frame.
            state_score = 0.0
            for reference in references:
                if isinstance(reference, list):
                    state_score = max(state_score, _mean_similarity(fingerprint, reference))
            if state_score > best_similarity:
                best_state = state
                best_similarity = state_score
        return MatchResult(best_state, best_similarity)

    def _capture_fingerprint(self) -> list[int]:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return []
        pixmap = screen.grabWindow(0)
        if pixmap.isNull():
            return []
        image = pixmap.toImage().convertToFormat(QImage.Format.Format_Grayscale8)

        # The jukebox itself can sit over THE FINALS while debugging. Mask its
        # last known rectangle so opening/closing the control window does not
        # become part of state classification.
        mask = self.ignore_rect() if self.ignore_rect else None
        if mask is not None and not mask.isNull() and mask.isValid():
            self._last_mask_rect = QRect(mask)
        elif self._last_mask_rect is not None:
            mask = self._last_mask_rect
        if mask is not None and mask.isValid():
            origin = screen.geometry().topLeft()
            local = mask.translated(-origin.x(), -origin.y())
            painter = QPainter(image)
            painter.fillRect(local, Qt.GlobalColor.black)
            painter.end()

        tiny = image.scaled(
            FINGERPRINT_WIDTH,
            FINGERPRINT_HEIGHT,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        # Width 96 is DWORD aligned for Grayscale8, so bytesPerLine == width on
        # normal Qt builds. Handle any padding defensively anyway.
        raw = bytes(tiny.constBits())
        stride = tiny.bytesPerLine()
        result: list[int] = []
        for y in range(FINGERPRINT_HEIGHT):
            start = y * stride
            result.extend(raw[start:start + FINGERPRINT_WIDTH])
        return result
