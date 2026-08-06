"""Dependency-light waveform decoding and automatic segment suggestions."""

from __future__ import annotations

from array import array
from dataclasses import dataclass
import math

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat


STINGER_SLOTS = [
    "double_kill", "triple_kill", "quad_kill", "penta_kill", "hexa_kill",
    "five_kill", "ten_kill", "fifteen_kill", "twenty_kill",
]


@dataclass(frozen=True)
class AnalysisResult:
    path: str
    envelope: list[float]
    duration_ms: int
    assignments: dict[str, tuple[int, int]]


def _window_scores(envelope: list[float], duration_ms: int, window_ms: int) -> list[tuple[float, int]]:
    if not envelope or duration_ms <= 0:
        return []
    bins = max(1, round(len(envelope) * window_ms / duration_ms))
    result = []
    for start in range(0, max(1, len(envelope) - bins + 1)):
        values = envelope[start:start + bins]
        energy = sum(values) / len(values)
        attack = max(values) - values[0]
        score = energy + max(0.0, attack) * 0.35
        result.append((score, round(start * duration_ms / len(envelope))))
    return result


def suggest_segments(envelope: list[float], duration_ms: int) -> dict[str, tuple[int, int]]:
    """Suggest useful regions from energy and attack; results remain user-editable."""
    if duration_ms <= 0:
        return {}

    def clamp_segment(start: int, length: int) -> tuple[int, int]:
        length = min(length, duration_ms)
        start = max(0, min(start, duration_ms - length))
        return start, start + length

    long_ms = min(60_000, max(8_000, duration_ms // 3))
    medium_ms = min(35_000, max(6_000, duration_ms // 5))
    long_scores = _window_scores(envelope, duration_ms, long_ms)
    medium_scores = _window_scores(envelope, duration_ms, medium_ms)
    quiet = min(medium_scores, default=(0.0, 0), key=lambda item: item[0])[1]
    energetic = max(long_scores, default=(0.0, 0), key=lambda item: item[0])[1]
    middle_ranked = sorted(medium_scores, key=lambda item: item[0])
    middle = middle_ranked[len(middle_ranked) // 2][1] if middle_ranked else 0

    assignments = {
        "menu": clamp_segment(quiet, medium_ms),
        "pre_match": clamp_segment(max(0, energetic - 10_000), min(20_000, medium_ms)),
        "in_match": clamp_segment(energetic, long_ms),
        "practice": clamp_segment(middle, medium_ms),
        "post_match": clamp_segment(max(0, duration_ms - medium_ms), medium_ms),
    }

    # Rank distinct short peaks from least to most intense, assigning stronger
    # moments to progressively rarer events. Every stinger gets a finite end.
    stinger_ms = min(7_000, max(2_500, duration_ms // 30))
    candidates = sorted(_window_scores(envelope, duration_ms, stinger_ms), reverse=True)
    picked: list[tuple[float, int]] = []
    for candidate in candidates:
        if all(abs(candidate[1] - previous[1]) >= stinger_ms for previous in picked):
            picked.append(candidate)
        if len(picked) == len(STINGER_SLOTS):
            break
    if not picked:
        picked = [(0.0, 0)]
    while len(picked) < len(STINGER_SLOTS):
        picked.append(picked[-1])
    picked.sort(key=lambda item: item[0])
    for slot, (_score, start) in zip(STINGER_SLOTS, picked):
        assignments[slot] = clamp_segment(start, stinger_ms)
    return assignments


class AudioAnalyzer(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.decoder = QAudioDecoder(self)
        self.decoder.bufferReady.connect(self._read_buffer)
        self.decoder.finished.connect(self._complete)
        self.decoder.error.connect(self._error)
        self._path = ""
        self._samples: list[float] = []
        self._sample_rate = 0
        self._channels = 1
        self._duration_us = 0

    def analyze(self, path: str) -> None:
        self.decoder.stop()
        self._path = path
        self._samples = []
        self._sample_rate = 0
        self._channels = 1
        self._duration_us = 0
        self.decoder.setSource(QUrl.fromLocalFile(path))
        self.decoder.start()

    def _read_buffer(self) -> None:
        buffer = self.decoder.read()
        fmt = buffer.format()
        self._duration_us += max(0, buffer.duration())
        self._sample_rate = fmt.sampleRate()
        self._channels = max(1, fmt.channelCount())
        raw = bytes(buffer.data())
        sample_format = fmt.sampleFormat()
        try:
            if sample_format == QAudioFormat.SampleFormat.UInt8:
                values = [(value - 128) / 128.0 for value in raw]
            elif sample_format == QAudioFormat.SampleFormat.Int16:
                data = array("h"); data.frombytes(raw)
                values = [value / 32768.0 for value in data]
            elif sample_format == QAudioFormat.SampleFormat.Int32:
                data = array("i"); data.frombytes(raw)
                values = [value / 2147483648.0 for value in data]
            elif sample_format == QAudioFormat.SampleFormat.Float:
                data = array("f"); data.frombytes(raw)
                values = [max(-1.0, min(1.0, value)) for value in data if math.isfinite(value)]
            else:
                return
        except (BufferError, ValueError, OverflowError):
            return
        # Keep analysis bounded while preserving the full timeline.
        frame_step = max(1, self._sample_rate // 200) * self._channels
        for offset in range(0, len(values), frame_step):
            frame = values[offset:offset + frame_step]
            if frame:
                self._samples.append(math.sqrt(sum(v * v for v in frame) / len(frame)))

    def _complete(self) -> None:
        duration_ms = round(self._duration_us / 1000)
        if duration_ms <= 0 and self._sample_rate:
            duration_ms = round(len(self._samples) * 1000 / 200)
        if not self._samples or duration_ms <= 0:
            self.failed.emit("Windows could not decode enough audio to analyze this file.")
            return
        target_bins = 1200
        group = max(1, math.ceil(len(self._samples) / target_bins))
        envelope = [
            max(self._samples[index:index + group])
            for index in range(0, len(self._samples), group)
        ]
        peak = max(envelope) or 1.0
        envelope = [value / peak for value in envelope]
        self.finished.emit(AnalysisResult(
            self._path, envelope, duration_ms,
            suggest_segments(envelope, duration_ms),
        ))

    def _error(self, *_args) -> None:
        self.failed.emit(self.decoder.errorString() or "Audio analysis failed.")
