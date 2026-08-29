"""Lightweight whole-screen visual state detection for THE FINALS.

The detector never opens or hooks the game process. Qt captures the visible
primary screen once per second, downsizes it to a 96x54 grayscale fingerprint,
and immediately discards the full-size image.

Classification is deliberately dependency-free but no longer treats every pixel
as equally useful. Learned states are compared with discriminative pixel weights,
edge structure, a runner-up confidence margin, state stickiness, and a one-way
match-flow guard. This keeps visually similar menu/queue/loading screens from
bouncing the jukebox back and forth.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable

from PySide6.QtCore import QObject, QRect, QTimer, Qt, Signal
from PySide6.QtGui import QGuiApplication, QImage, QPainter


FINGERPRINT_WIDTH = 96
FINGERPRINT_HEIGHT = 54
FINGERPRINT_SIZE = FINGERPRINT_WIDTH * FINGERPRINT_HEIGHT
MAX_REFERENCES_PER_STATE = 6
DEFAULT_INTERVAL_MS = 1000
DEFAULT_MIN_SIMILARITY = 0.70
DEFAULT_MIN_MARGIN = 0.035
CURRENT_STATE_STICKINESS = 0.055

# Transitions are intentionally directional. Forward skips are allowed because
# a one-second scanner can legitimately miss a brief loading frame; backwards
# jumps such as In Match -> Matchmaking are not valid and caused the prototype's
# most obvious audio loops.
ALLOWED_TRANSITIONS = {
    "": {"menu", "matchmaking", "pre_match", "in_match", "practice", "post_match"},
    "menu": {"menu", "matchmaking", "pre_match", "practice"},
    "matchmaking": {"matchmaking", "pre_match", "in_match", "menu"},
    "pre_match": {"pre_match", "in_match", "post_match", "menu"},
    "in_match": {"in_match", "post_match", "menu"},
    "post_match": {"post_match", "menu", "matchmaking"},
    "practice": {"practice", "menu"},
}


@dataclass(frozen=True)
class MatchResult:
    state: str
    similarity: float
    runner_up_state: str = ""
    runner_up_similarity: float = 0.0
    current_similarity: float = 0.0

    @property
    def margin(self) -> float:
        return self.similarity - self.runner_up_similarity


def _valid_reference(value) -> bool:
    return isinstance(value, list) and len(value) == FINGERPRINT_SIZE


def _gradient(values: list[int]) -> list[int]:
    """Cheap edge descriptor derived from the already tiny grayscale frame."""
    if len(values) != FINGERPRINT_SIZE:
        return []
    result = [0] * FINGERPRINT_SIZE
    width = FINGERPRINT_WIDTH
    height = FINGERPRINT_HEIGHT
    for y in range(height - 1):
        row = y * width
        next_row = row + width
        for x in range(width - 1):
            index = row + x
            dx = abs(values[index + 1] - values[index])
            dy = abs(values[next_row + x] - values[index])
            result[index] = min(255, (dx + dy) // 2)
    return result


def _weighted_similarity(left: list[int], right: list[int], weights: list[float]) -> float:
    if len(left) != FINGERPRINT_SIZE or len(right) != FINGERPRINT_SIZE:
        return 0.0
    if len(weights) != FINGERPRINT_SIZE:
        weights = [1.0] * FINGERPRINT_SIZE
    total_weight = sum(weights)
    if total_weight <= 0.0:
        return 0.0
    difference = sum(
        weight * abs(a - b)
        for a, b, weight in zip(left, right, weights)
    )
    return max(0.0, 1.0 - difference / (255.0 * total_weight))


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
        self._weights_cache: list[float] | None = None
        self.settings.data.setdefault("screen_refs", {})
        self.settings.data.setdefault("screen_min_similarity", DEFAULT_MIN_SIMILARITY)
        self.settings.data.setdefault("screen_min_margin", DEFAULT_MIN_MARGIN)

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

    def set_current_state(self, state: str) -> None:
        if state in ALLOWED_TRANSITIONS:
            self.current_state = state
            self._candidate_state = ""
            self._candidate_hits = 0

    def reference_count(self, state: str) -> int:
        refs = self.settings.data.get("screen_refs", {}).get(state, [])
        return sum(1 for ref in refs if _valid_reference(ref)) if isinstance(refs, list) else 0

    def clear_references(self, state: str) -> None:
        refs = self.settings.data.setdefault("screen_refs", {})
        refs[state] = []
        self._weights_cache = None
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
        self._weights_cache = None
        self.settings.save()
        count = self.reference_count(state)
        self.status_changed.emit(
            f"Learned {state.replace('_', ' ').title()} screen ({count} reference{'s' if count != 1 else ''})"
        )
        return True

    def scan(self) -> None:
        refs_by_state = self.settings.data.get("screen_refs", {})
        if not any(
            isinstance(refs, list) and any(_valid_reference(ref) for ref in refs)
            for refs in refs_by_state.values()
        ):
            self.status_changed.emit("Whole-screen scan active — capture state references")
            return

        fingerprint = self._capture_fingerprint()
        if not fingerprint:
            self.status_changed.emit("Whole-screen capture unavailable")
            return

        match = self._best_match(fingerprint)
        if not match.state:
            self._reset_candidate()
            self.status_changed.emit("Screen state unknown")
            return

        minimum = float(self.settings.data.get("screen_min_similarity", DEFAULT_MIN_SIMILARITY))
        min_margin = float(self.settings.data.get("screen_min_margin", DEFAULT_MIN_MARGIN))
        best_name = match.state.replace("_", " ").title()
        runner_name = match.runner_up_state.replace("_", " ").title() if match.runner_up_state else "none"

        if match.similarity < minimum:
            self._reset_candidate()
            self.status_changed.emit(
                f"Uncertain: {best_name} {match.similarity:.0%} (below threshold)"
            )
            return

        # Prefer staying in the current state when another class only edges it
        # out by a few percent. Animated backgrounds otherwise cause constant
        # winner swapping even though the persistent HUD has not changed.
        if (
            self.current_state
            and match.state != self.current_state
            and match.current_similarity >= minimum
            and match.similarity - match.current_similarity < CURRENT_STATE_STICKINESS
        ):
            self._reset_candidate()
            self.status_changed.emit(
                f"Holding {self.current_state.replace('_', ' ').title()} — "
                f"{best_name} only +{match.similarity - match.current_similarity:.1%}"
            )
            return

        if match.margin < min_margin:
            self._reset_candidate()
            self.status_changed.emit(
                f"Ambiguous: {best_name} {match.similarity:.0%} vs {runner_name} "
                f"{match.runner_up_similarity:.0%}"
            )
            return

        allowed = ALLOWED_TRANSITIONS.get(self.current_state, ALLOWED_TRANSITIONS[""])
        if match.state not in allowed:
            self._reset_candidate()
            self.status_changed.emit(
                f"Ignored impossible transition: "
                f"{self.current_state.replace('_', ' ').title()} → {best_name}"
            )
            return

        self.status_changed.emit(
            f"Screen: {best_name} {match.similarity:.0%} "
            f"(next {runner_name} {match.runner_up_similarity:.0%})"
        )

        if match.state == self.current_state:
            self._reset_candidate()
            return
        if match.state == self._candidate_state:
            self._candidate_hits += 1
        else:
            self._candidate_state = match.state
            self._candidate_hits = 1

        # Close calls need one extra confirmation; obvious matches remain quick.
        required_hits = 3 if match.margin < 0.08 else 2
        if self._candidate_hits >= required_hits:
            self.current_state = match.state
            self._reset_candidate()
            self.state_changed.emit(match.state, match.similarity)

    def _reset_candidate(self) -> None:
        self._candidate_state = ""
        self._candidate_hits = 0

    def _references_by_state(self) -> dict[str, list[list[int]]]:
        result: dict[str, list[list[int]]] = {}
        for state, references in self.settings.data.get("screen_refs", {}).items():
            if not isinstance(references, list):
                continue
            valid = [ref for ref in references if _valid_reference(ref)]
            if valid:
                result[state] = valid
        return result

    def _discriminative_weights(self) -> list[float]:
        if self._weights_cache is not None:
            return self._weights_cache

        refs_by_state = self._references_by_state()
        if len(refs_by_state) < 2:
            self._weights_cache = [1.0] * FINGERPRINT_SIZE
            return self._weights_cache

        state_means: list[list[float]] = []
        state_refs: list[list[list[int]]] = []
        for references in refs_by_state.values():
            count = len(references)
            means = [
                sum(ref[index] for ref in references) / count
                for index in range(FINGERPRINT_SIZE)
            ]
            state_means.append(means)
            state_refs.append(references)

        weights: list[float] = []
        state_count = len(state_means)
        for index in range(FINGERPRINT_SIZE):
            means_here = [means[index] for means in state_means]
            grand = sum(means_here) / state_count
            between = sum((value - grand) ** 2 for value in means_here) / state_count

            within_total = 0.0
            for means, references in zip(state_means, state_refs):
                mean = means[index]
                within_total += sum((ref[index] - mean) ** 2 for ref in references) / len(references)
            within = within_total / state_count

            # Fisher-like signal/noise weighting. The baseline keeps the whole
            # screen represented; the cap prevents a handful of pixels from
            # completely dominating a classification.
            signal = math.sqrt(between)
            noise = math.sqrt(within) + 12.0
            weight = 0.25 + min(3.75, 2.0 * signal / noise)
            weights.append(weight)

        # Normalize to mean weight 1 so similarity percentages remain intuitive.
        mean_weight = sum(weights) / len(weights)
        if mean_weight > 0:
            weights = [weight / mean_weight for weight in weights]
        self._weights_cache = weights
        return weights

    def _state_scores(self, fingerprint: list[int]) -> dict[str, float]:
        weights = self._discriminative_weights()
        current_edges = _gradient(fingerprint)
        scores: dict[str, float] = {}

        for state, references in self._references_by_state().items():
            reference_scores: list[float] = []
            for reference in references:
                pixel_score = _weighted_similarity(fingerprint, reference, weights)
                edge_score = _weighted_similarity(current_edges, _gradient(reference), weights)
                # Pixel layout remains the primary signal; edges make persistent
                # HUD/text structure matter more than changing scenery/brightness.
                reference_scores.append(pixel_score * 0.68 + edge_score * 0.32)

            reference_scores.sort(reverse=True)
            # One lucky reference should not dominate once multiple examples of
            # a state have been learned. Average the two closest examples.
            take = min(2, len(reference_scores))
            scores[state] = sum(reference_scores[:take]) / take

        return scores

    def _best_match(self, fingerprint: list[int]) -> MatchResult:
        scores = self._state_scores(fingerprint)
        if not scores:
            return MatchResult("", 0.0)

        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        best_state, best_similarity = ranked[0]
        if len(ranked) > 1:
            runner_state, runner_similarity = ranked[1]
        else:
            runner_state, runner_similarity = "", 0.0
        current_similarity = scores.get(self.current_state, 0.0)
        return MatchResult(
            best_state,
            best_similarity,
            runner_state,
            runner_similarity,
            current_similarity,
        )

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
        raw = bytes(tiny.constBits())
        stride = tiny.bytesPerLine()
        result: list[int] = []
        for y in range(FINGERPRINT_HEIGHT):
            start = y * stride
            result.extend(raw[start:start + FINGERPRINT_WIDTH])
        return result
