from __future__ import annotations

import json
import os
from pathlib import Path
import random
import subprocess
import sys
import ctypes

from PySide6.QtCore import QObject, QTimer, QUrl, Signal, Qt
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QStyle,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from empulse_events import EmpulseEventParser
from jukebox_tracks import format_time, normalize_entry, parse_time


APP_NAME = "EMPULSE Jukebox"
SLOT_LABELS = {
    "menu": "Main Menu",
    "pre_match": "Match Found / Loading",
    "in_match": "In Match",
    "practice": "Practice Range",
    "post_match": "End of Match",
    "double_kill": "Double Kill stinger",
    "triple_kill": "Triple Kill stinger",
    "quad_kill": "Quad Kill stinger",
    "five_kill": "5 Kill Streak stinger",
}
CONTEXT_SLOTS = {"menu", "pre_match", "in_match", "practice", "post_match"}
SUPPORTED_AUDIO = "Audio files (*.mp3 *.wav *.flac *.ogg *.m4a *.aac *.wma);;All files (*)"


def config_dir() -> Path:
    root = os.environ.get("APPDATA") or str(Path.home())
    return Path(root) / "EMPULSE Jukebox"


def default_log_path() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "Orion" / "Saved" / "Logs" / "Orion.log"
    return Path.home() / "AppData" / "Local" / "Orion" / "Saved" / "Logs" / "Orion.log"


def default_config() -> dict:
    return {
        "log_path": str(default_log_path()),
        "volume": 65,
        "stinger_volume": 80,
        "fade_ms": 800,
        "shuffle": True,
        "auto_play": True,
        "minimize_to_tray": True,
        "slots": {key: [] for key in SLOT_LABELS},
    }


class Settings:
    def __init__(self) -> None:
        self.path = config_dir() / "settings.json"
        self.data = default_config()
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
                self.data.update({k: v for k, v in loaded.items() if k != "slots"})
                for key, tracks in loaded.get("slots", {}).items():
                    if key in self.data["slots"] and isinstance(tracks, list):
                        self.data["slots"][key] = [
                            entry for entry in (normalize_entry(track) for track in tracks)
                            if entry["path"]
                        ]
            except (OSError, ValueError, TypeError):
                pass

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        temp.replace(self.path)


class LogWatcher(QObject):
    event = Signal(str, str)
    connection_changed = Signal(bool, str)

    def __init__(self, path: str) -> None:
        super().__init__()
        self.path = Path(path)
        self.position = 0
        self.parser = EmpulseEventParser()
        self._was_running = False
        self._process_checked = False
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.poll)
        self.process_timer = QTimer(self)
        self.process_timer.setInterval(2500)
        self.process_timer.timeout.connect(self.check_process)

    def start(self) -> None:
        self.position = 0
        self.parser = EmpulseEventParser()
        self._process_checked = False
        self.check_process()
        self._prime_from_existing_log()
        self.timer.start()
        self.process_timer.start()

    def set_path(self, path: str) -> None:
        self.path = Path(path)
        self.start()

    def _prime_from_existing_log(self) -> None:
        if not self.path.exists():
            self.connection_changed.emit(False, "Waiting for Orion.log")
            return
        try:
            # Parse the existing session so launching Jukebox mid-game still
            # discovers the current context. Stingers are intentionally ignored.
            text = self.path.read_text(encoding="utf-8", errors="replace")
            latest_state = None
            latest_map = ""
            latest_mode = ""
            for parsed in self.parser.feed_many(text.splitlines()):
                if parsed.kind == "state":
                    latest_state = parsed.value
                elif parsed.kind == "map":
                    latest_map = parsed.value
                elif parsed.kind == "mode":
                    latest_mode = parsed.value
            self.position = self.path.stat().st_size
            if latest_map:
                self.event.emit("map", latest_map)
            if latest_mode:
                self.event.emit("mode", latest_mode)
            if latest_state:
                self.event.emit("state", latest_state)
        except OSError as exc:
            self.connection_changed.emit(False, f"Log unavailable: {exc}")

    def poll(self) -> None:
        if not self.path.exists():
            return
        try:
            size = self.path.stat().st_size
            if size < self.position:
                self.position = 0
                self.parser = EmpulseEventParser()
            if size == self.position:
                return
            with self.path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(self.position)
                chunk = handle.read()
                self.position = handle.tell()
            for line in chunk.splitlines():
                for parsed in self.parser.feed(line):
                    self.event.emit(parsed.kind, parsed.value)
        except OSError:
            return

    def check_process(self) -> None:
        if sys.platform != "win32":
            self.connection_changed.emit(self.path.exists(), "Test mode (non-Windows)")
            return
        running = self._detect_windows_process() or self._detect_empulse_window()
        if not running:
            # Fallback for unusual Windows/Python combinations. Unlike v0.1,
            # this deliberately avoids tasklist's exact-name filter, which can
            # fail on EMPULSE's long Shipping executable name.
            try:
                flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
                result = subprocess.run(
                    ["tasklist", "/FO", "CSV", "/NH"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                    creationflags=flags,
                    check=False,
                )
                running = "orionclient" in result.stdout.lower()
            except (OSError, subprocess.SubprocessError):
                pass
        if not self._process_checked or running != self._was_running:
            self._process_checked = True
            self._was_running = running
            self.connection_changed.emit(
                running,
                "EMPULSE detected" if running else "EMPULSE is not running",
            )

    @staticmethod
    def _detect_windows_process() -> bool:
        """Enumerate full executable names through the ordinary Windows API."""
        try:
            from ctypes import wintypes

            class PROCESSENTRY32W(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260),
                ]

            kernel32 = ctypes.windll.kernel32
            kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
            kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
            kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
            kernel32.Process32FirstW.restype = wintypes.BOOL
            kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
            kernel32.Process32NextW.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel32.CloseHandle.restype = wintypes.BOOL
            snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
            if snapshot == ctypes.c_void_p(-1).value:
                return False
            entry = PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            try:
                ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
                while ok:
                    name = entry.szExeFile.lower()
                    if name.startswith("orionclient"):
                        return True
                    ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
            finally:
                kernel32.CloseHandle(snapshot)
        except (AttributeError, OSError, ValueError):
            return False
        return False

    @staticmethod
    def _detect_empulse_window() -> bool:
        """Window-title fallback; excludes this Jukebox's own window."""
        found = False
        try:
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def visit(hwnd, _lparam):
                nonlocal found
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0:
                    return True
                buffer = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buffer, length + 1)
                title = buffer.value.strip().lower()
                if title == "empulse" or (title.startswith("empulse ") and "jukebox" not in title):
                    found = True
                    return False
                return True

            user32.EnumWindows(callback_type(visit), 0)
        except (AttributeError, OSError, ValueError):
            return False
        return found


class AudioEngine(QObject):
    track_changed = Signal(str)
    playback_changed = Signal(str)

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        self.music_output = QAudioOutput(self)
        self.music_player = QMediaPlayer(self)
        self.music_player.setAudioOutput(self.music_output)
        self.stinger_output = QAudioOutput(self)
        self.stinger_player = QMediaPlayer(self)
        self.stinger_player.setAudioOutput(self.stinger_output)
        self.music_player.mediaStatusChanged.connect(self._media_status)
        self.stinger_player.mediaStatusChanged.connect(self._stinger_media_status)
        self.music_player.errorOccurred.connect(self._media_error)
        self.stinger_player.errorOccurred.connect(self._media_error)
        self.music_player.positionChanged.connect(self._music_position_changed)
        self.stinger_player.positionChanged.connect(self._stinger_position_changed)
        self.music_player.playbackStateChanged.connect(self._playback_state_changed)
        self.current_slot = ""
        self.pending_slot = ""
        self.current_entry: dict | None = None
        self.stinger_entry: dict | None = None
        self._music_seek_pending = 0
        self._stinger_seek_pending = 0
        # Qt can emit positionChanged synchronously while stop()/setSource() is
        # replacing a source.  Ignore those stale positions: the old song may
        # already be beyond the new entry's end marker, which would otherwise
        # re-enter _start_random_track and can hang the multimedia backend.
        self._music_transitioning = False
        self._music_advance_pending = False
        self._stinger_loading = False
        self._fade_direction = 0
        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(40)
        self._fade_timer.timeout.connect(self._fade_tick)
        self._apply_volumes()

    def _target_music_volume(self) -> float:
        return max(0.0, min(1.0, self.settings.data["volume"] / 100.0))

    def _apply_volumes(self) -> None:
        # Do not cancel an in-progress fade when another option is changed.
        if not self._fade_timer.isActive():
            self.music_output.setVolume(self._target_music_volume())
        self.stinger_output.setVolume(
            max(0.0, min(1.0, self.settings.data["stinger_volume"] / 100.0))
        )

    def context(self, slot: str, force: bool = False) -> None:
        if slot not in CONTEXT_SLOTS:
            return
        if slot == self.current_slot and not force and not self.pending_slot:
            return
        self._queue_music(slot)

    def _queue_music(self, slot: str) -> None:
        """Serialize every music change through the configured fade."""
        self.pending_slot = slot
        if self.music_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._fade_direction = -1
            self._fade_timer.start()
        else:
            self._start_pending()

    def _start_pending(self) -> None:
        slot = self.pending_slot
        self.pending_slot = ""
        if not slot:
            return
        self.current_slot = slot
        self.music_output.setVolume(0.0)
        if self._start_random_track(slot, self.music_player):
            self._fade_direction = 1
            self._fade_timer.start()
        else:
            self.current_entry = None
            self.track_changed.emit(f"No tracks assigned to {SLOT_LABELS[slot]}")

    def stinger(self, slot: str) -> None:
        self._start_random_track(slot, self.stinger_player, is_stinger=True)

    def _available_tracks(self, slot: str) -> list[dict]:
        result = []
        for raw in self.settings.data["slots"].get(slot, []):
            entry = normalize_entry(raw)
            if Path(entry["path"]).is_file():
                result.append(entry)
        return result

    def _choose_track(self, slot: str) -> dict | None:
        tracks = self._available_tracks(slot)
        if not tracks:
            return None
        current_path = self.current_entry["path"] if self.current_entry else ""
        if self.settings.data.get("shuffle", True):
            choices = [track for track in tracks if track["path"] != current_path] or tracks
            return random.choice(choices)
        for index, track in enumerate(tracks):
            if track["path"] == current_path:
                return tracks[(index + 1) % len(tracks)]
        return tracks[0]

    def _start_random_track(
        self, slot: str, player: QMediaPlayer, is_stinger: bool = False
    ) -> bool:
        entry = self._choose_track(slot)
        if not entry:
            self.playback_changed.emit(f"No playable file assigned to {SLOT_LABELS[slot]}")
            return False
        absolute_path = str(Path(entry["path"]).resolve())
        source = QUrl.fromLocalFile(absolute_path)
        if not is_stinger:
            self._music_transitioning = True
            self._music_advance_pending = False
        else:
            self._stinger_loading = True
        if is_stinger:
            self.stinger_entry = entry
            self._stinger_seek_pending = entry["start_ms"]
        else:
            self.current_entry = entry
            start = format_time(entry["start_ms"])
            self.track_changed.emit(f"{Path(absolute_path).name}  @ {start}")

        # Rebuilding Qt's FFmpeg pipeline for the exact source that is already
        # loaded can deadlock on Windows (observed with Qt 6.11).  This is also
        # the common case when one album file supplies several event segments.
        if player.source() == source:
            player.setPosition(entry["start_ms"])
            if not is_stinger:
                self._music_seek_pending = 0
                self._music_transitioning = False
            else:
                self._stinger_seek_pending = 0
                self._stinger_loading = False
            player.play()
            return True

        player.stop()
        if is_stinger:
            self._stinger_seek_pending = entry["start_ms"]
        else:
            self._music_seek_pending = entry["start_ms"]
        player.setSource(source)
        player.play()
        return True

    def _fade_tick(self) -> None:
        target = self._target_music_volume()
        fade_ms = max(100, int(self.settings.data.get("fade_ms", 800)))
        step = max(0.01, target * self._fade_timer.interval() / fade_ms)
        volume = self.music_output.volume()
        if self._fade_direction < 0:
            volume = max(0.0, volume - step)
            self.music_output.setVolume(volume)
            if volume <= 0.001:
                self._fade_timer.stop()
                self.music_player.stop()
                # Leave the multimedia call stack before seeking/restarting.
                # Qt/FFmpeg on Windows can deadlock when a stopped, already-
                # loaded source is restarted synchronously in this callback.
                QTimer.singleShot(0, self._start_pending)
        else:
            volume = min(target, volume + step)
            self.music_output.setVolume(volume)
            if volume >= target - 0.001:
                self._fade_timer.stop()

    def _media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if status in {
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        } and self._music_transitioning:
            if self._music_seek_pending:
                self.music_player.setPosition(self._music_seek_pending)
                self._music_seek_pending = 0
            self._music_transitioning = False
            self.music_player.play()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._music_transitioning = False
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self.current_slot:
            self._schedule_music_advance()

    def _stinger_media_status(self, status: QMediaPlayer.MediaStatus) -> None:
        if status in {
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        } and self._stinger_loading:
            if self._stinger_seek_pending:
                self.stinger_player.setPosition(self._stinger_seek_pending)
                self._stinger_seek_pending = 0
            self._stinger_loading = False
            self.stinger_player.play()

    def _music_position_changed(self, position: int) -> None:
        if not self.current_entry or self._music_transitioning:
            return
        end = self.current_entry.get("end_ms", 0)
        if end and position >= end:
            self._schedule_music_advance()

    def _schedule_music_advance(self) -> None:
        """Advance outside a Qt multimedia callback, once per boundary."""
        if self._music_advance_pending or not self.current_slot:
            return
        self._music_advance_pending = True
        QTimer.singleShot(0, self._advance_music)

    def _advance_music(self) -> None:
        self._music_advance_pending = False
        if self.current_slot:
            self._queue_music(self.current_slot)

    def _stinger_position_changed(self, position: int) -> None:
        if not self.stinger_entry:
            return
        end = self.stinger_entry.get("end_ms", 0)
        if end and position >= end:
            self.stinger_player.stop()
            self.stinger_entry = None

    def _media_error(self, _error, message: str) -> None:
        self.playback_changed.emit(f"Audio error: {message or 'Windows could not decode this file'}")

    def _playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        if not self.current_entry:
            return
        labels = {
            QMediaPlayer.PlaybackState.PlayingState: "Playing",
            QMediaPlayer.PlaybackState.PausedState: "Paused",
            QMediaPlayer.PlaybackState.StoppedState: "Stopped",
        }
        self.playback_changed.emit(labels.get(state, ""))

    def toggle_pause(self) -> bool:
        if not self.current_entry or self.music_player.source().isEmpty():
            self.playback_changed.emit("Choose a slot, then press Play this slot")
            return False
        if self.music_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.music_player.pause()
        else:
            self.music_player.play()
        return True

    def skip(self) -> None:
        if self.current_slot:
            self._queue_music(self.current_slot)

    def stop(self) -> None:
        self._fade_timer.stop()
        self.pending_slot = ""
        self.current_slot = ""
        self.current_entry = None
        self.stinger_entry = None
        self._music_transitioning = False
        self._music_advance_pending = False
        self._stinger_loading = False
        self.music_player.stop()
        self.stinger_player.stop()
        self.track_changed.emit("Nothing playing")


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = Settings()
        self.audio = AudioEngine(self.settings)
        self.watcher = LogWatcher(self.settings.data["log_path"])
        self.selected_slot = "menu"
        self.current_map = ""
        self.current_mode = ""
        self.current_state = "offline"
        self.game_running = False
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1060, 680)
        self.resize(1180, 760)
        self.setWindowIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaVolume))
        self._build_ui()
        self._build_tray()
        self._apply_style()
        self.watcher.event.connect(self._handle_event)
        self.watcher.connection_changed.connect(self._connection_changed)
        self.audio.track_changed.connect(self.now_playing.setText)
        self.audio.playback_changed.connect(self.detail_status.setText)
        self.watcher.start()

    def _build_ui(self) -> None:
        central = QWidget()
        central.setObjectName("appRoot")
        outer = QVBoxLayout(central)
        outer.setContentsMargins(28, 22, 28, 22)
        outer.setSpacing(18)

        title_row = QHBoxLayout()
        brand = QVBoxLayout()
        brand.setSpacing(1)
        title = QLabel("EMPULSE  //  JUKEBOX")
        title.setObjectName("title")
        subtitle = QLabel("ADAPTIVE AUDIO CONTROL")
        subtitle.setObjectName("eyebrow")
        brand.addWidget(title)
        brand.addWidget(subtitle)
        self.connection_dot = QLabel("●")
        self.connection_dot.setObjectName("offlineDot")
        self.connection_text = QLabel("Waiting for EMPULSE")
        self.connection_text.setObjectName("connectionText")
        title_row.addLayout(brand)
        title_row.addStretch()
        title_row.addWidget(self.connection_dot)
        title_row.addWidget(self.connection_text)
        outer.addLayout(title_row)

        log_panel = QFrame()
        log_panel.setObjectName("toolbar")
        path_row = QHBoxLayout(log_panel)
        path_row.setContentsMargins(14, 10, 14, 10)
        log_label = QLabel("ORION LOG")
        log_label.setObjectName("fieldLabel")
        path_row.addWidget(log_label)
        self.log_path = QLineEdit(self.settings.data["log_path"])
        browse_log = QPushButton("BROWSE")
        browse_log.clicked.connect(self._browse_log)
        path_row.addWidget(self.log_path, 1)
        path_row.addWidget(browse_log)
        outer.addWidget(log_panel)

        content = QHBoxLayout()
        content.setSpacing(16)
        nav_panel = QFrame()
        nav_panel.setObjectName("panel")
        nav_layout = QVBoxLayout(nav_panel)
        nav_layout.setContentsMargins(12, 16, 12, 12)
        nav_heading = QLabel("EVENT CHANNELS")
        nav_heading.setObjectName("eyebrow")
        nav_layout.addWidget(nav_heading)
        self.slot_list = QListWidget()
        self.slot_list.setMinimumWidth(270)
        self.slot_list.setObjectName("slotList")
        self.slot_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.slot_keys = list(SLOT_LABELS)
        self.slot_list.currentRowChanged.connect(self._slot_changed)
        nav_layout.addWidget(self.slot_list, 1)
        content.addWidget(nav_panel)

        library_panel = QFrame()
        library_panel.setObjectName("panel")
        right = QVBoxLayout(library_panel)
        right.setContentsMargins(20, 18, 20, 18)
        right.setSpacing(12)
        library_eyebrow = QLabel("PLAYLIST")
        library_eyebrow.setObjectName("eyebrow")
        right.addWidget(library_eyebrow)
        self.slot_title = QLabel()
        self.slot_title.setObjectName("sectionTitle")
        right.addWidget(self.slot_title)
        self.track_list = QListWidget()
        self.track_list.setObjectName("trackList")
        self.track_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.track_list.currentRowChanged.connect(self._track_selected)
        right.addWidget(self.track_list, 1)

        timestamp_row = QHBoxLayout()
        timestamp_row.addWidget(QLabel("Start position"))
        self.start_time = QLineEdit("0:00.000")
        self.start_time.setPlaceholderText("0:00.000")
        self.start_time.setToolTip("Where this event should begin inside the song")
        timestamp_row.addWidget(self.start_time)
        timestamp_row.addWidget(QLabel("End position"))
        self.end_time = QLineEdit("")
        self.end_time.setPlaceholderText("blank = song end")
        self.end_time.setToolTip("Optional point where this event should stop or advance")
        timestamp_row.addWidget(self.end_time)
        save_times = QPushButton("SAVE RANGE")
        save_times.setObjectName("accentButton")
        save_times.clicked.connect(self._save_timestamps)
        timestamp_row.addWidget(save_times)
        right.addLayout(timestamp_row)

        track_buttons = QHBoxLayout()
        add_button = QPushButton("+  ADD MUSIC")
        remove_button = QPushButton("REMOVE")
        clear_button = QPushButton("CLEAR")
        preview_button = QPushButton("▶  PLAY CHANNEL")
        preview_button.setObjectName("accentButton")
        add_button.clicked.connect(self._add_tracks)
        remove_button.clicked.connect(self._remove_tracks)
        clear_button.clicked.connect(self._clear_tracks)
        preview_button.clicked.connect(self._preview_slot)
        for button in (add_button, remove_button, clear_button, preview_button):
            track_buttons.addWidget(button)
        right.addLayout(track_buttons)

        options = QHBoxLayout()
        options.addWidget(QLabel("Music volume"))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(self.settings.data["volume"])
        self.volume.valueChanged.connect(self._options_changed)
        options.addWidget(self.volume, 1)
        options.addWidget(QLabel("Stinger volume"))
        self.stinger_volume = QSlider(Qt.Orientation.Horizontal)
        self.stinger_volume.setRange(0, 100)
        self.stinger_volume.setValue(self.settings.data["stinger_volume"])
        self.stinger_volume.valueChanged.connect(self._options_changed)
        options.addWidget(self.stinger_volume, 1)
        options.addWidget(QLabel("Fade ms"))
        self.fade = QSpinBox()
        self.fade.setRange(100, 5000)
        self.fade.setSingleStep(100)
        self.fade.setValue(self.settings.data["fade_ms"])
        self.fade.valueChanged.connect(self._options_changed)
        options.addWidget(self.fade)
        right.addLayout(options)

        toggles = QHBoxLayout()
        self.shuffle = QCheckBox("Shuffle playlists")
        self.shuffle.setChecked(self.settings.data["shuffle"])
        self.auto_play = QCheckBox("Follow EMPULSE automatically")
        self.auto_play.setChecked(self.settings.data["auto_play"])
        self.to_tray = QCheckBox("Minimize to tray")
        self.to_tray.setChecked(self.settings.data["minimize_to_tray"])
        for toggle in (self.shuffle, self.auto_play, self.to_tray):
            toggle.toggled.connect(self._options_changed)
            toggles.addWidget(toggle)
        right.addLayout(toggles)
        content.addWidget(library_panel, 1)
        outer.addLayout(content, 1)

        player_panel = QFrame()
        player_panel.setObjectName("playerBar")
        player = QHBoxLayout(player_panel)
        player.setContentsMargins(18, 13, 14, 13)
        self.state_label = QLabel("State: Offline")
        self.detail_status = QLabel("")
        self.now_playing = QLabel("Nothing playing")
        self.now_playing.setObjectName("nowPlaying")
        pause = QPushButton("▶  PLAY / PAUSE")
        pause.setObjectName("accentButton")
        skip = QPushButton("SKIP  ›")
        pause.clicked.connect(self._toggle_playback)
        skip.clicked.connect(self.audio.skip)
        player.addWidget(self.state_label)
        player.addWidget(self.detail_status)
        player.addStretch()
        player.addWidget(self.now_playing)
        player.addWidget(pause)
        player.addWidget(skip)
        outer.addWidget(player_panel)

        self.setCentralWidget(central)
        self._refresh_slots()
        self.slot_list.setCurrentRow(0)

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self.windowIcon())
        self.tray.setToolTip(APP_NAME)
        menu = self.menuBar().addMenu("App")
        show_action = QAction("Show", self)
        quit_action = QAction("Quit", self)
        show_action.triggered.connect(self.showNormal)
        quit_action.triggered.connect(QApplication.instance().quit)
        menu.addAction(show_action)
        menu.addAction(quit_action)
        tray_menu = menu
        self.tray.setContextMenu(tray_menu)
        self.menuBar().hide()
        self.tray.activated.connect(
            lambda reason: self.showNormal()
            if reason == QSystemTrayIcon.ActivationReason.Trigger
            else None
        )
        self.tray.show()

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow { background: #090b0d; }
            QWidget#appRoot {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #171b1f, stop:0.52 #0f1317, stop:1 #090b0d);
                color: #e8ebed; font-family: "Segoe UI"; font-size: 13px;
            }
            QWidget { color: #e8ebed; font-family: "Segoe UI"; font-size: 13px; }
            QLabel#title { font-size: 25px; font-weight: 800; letter-spacing: 2px; color: #f5f7f8; }
            QLabel#eyebrow, QLabel#fieldLabel { color: #9ba3a9; font-size: 10px; font-weight: 700; letter-spacing: 2px; }
            QLabel#sectionTitle { font-size: 24px; font-weight: 300; color: #d9ff80; }
            QLabel#nowPlaying { color: #d9ff80; font-size: 14px; font-weight: 600; }
            QLabel#connectionText { color: #bcc2c6; font-weight: 600; }
            QLabel#onlineDot { color: #cfff6b; font-size: 18px; }
            QLabel#offlineDot { color: #f06d72; font-size: 18px; }
            QFrame#panel, QFrame#toolbar, QFrame#playerBar {
                background: rgba(17, 20, 23, 225);
                border: 1px solid #2a2f33;
            }
            QFrame#panel { border-radius: 3px; }
            QFrame#toolbar { background: rgba(10, 12, 14, 190); border-radius: 3px; }
            QFrame#playerBar { background: #090b0c; border-top: 1px solid #303438; }
            QListWidget { background: transparent; border: none; outline: none; }
            QListWidget#trackList { background: #0d1012; border: 1px solid #292e32; padding: 5px; }
            QListWidget::item { padding: 11px 10px; border-bottom: 1px solid #24292d; color: #c7ccd0; }
            QListWidget::item:hover { background: #20252a; color: white; }
            QListWidget::item:selected { background: #30373a; color: #d9ff80; border-left: 3px solid #d9ff80; }
            QLineEdit, QSpinBox {
                background: #0b0e10; border: 1px solid #353b40; border-radius: 2px; padding: 8px; color: #f0f2f3;
                selection-background-color: #bfe85f; selection-color: #080a0b;
            }
            QLineEdit:focus, QSpinBox:focus { border-color: #a9ca61; }
            QPushButton {
                background: #292e32; border: 1px solid #40464b; border-radius: 2px;
                padding: 9px 15px; color: #eef0f1; font-size: 11px; font-weight: 700; letter-spacing: 1px;
            }
            QPushButton:hover { background: #3a4146; border-color: #687177; }
            QPushButton:pressed { background: #181c1f; }
            QPushButton#accentButton { background: #c7e978; color: #0c0f10; border-color: #d9ff80; }
            QPushButton#accentButton:hover { background: #dcff8d; }
            QCheckBox { spacing: 7px; color: #b8bec2; }
            QCheckBox::indicator { width: 14px; height: 14px; border: 1px solid #596167; background: #0b0e10; }
            QCheckBox::indicator:checked { background: #c7e978; border-color: #d9ff80; }
            QSlider::groove:horizontal { height: 3px; background: #353b40; }
            QSlider::sub-page:horizontal { background: #b8d86f; }
            QSlider::handle:horizontal { width: 12px; margin: -5px 0; background: #d9ff80; border-radius: 6px; }
            QMenuBar { background: #090b0d; color: #8f979d; }
            QMenuBar::item:selected { background: #292e32; }
            """
        )

    def _refresh_slots(self) -> None:
        current = self.slot_list.currentRow()
        self.slot_list.clear()
        for key in self.slot_keys:
            count = len(self.settings.data["slots"][key])
            self.slot_list.addItem(f"{SLOT_LABELS[key]}  ({count})")
        if current >= 0:
            self.slot_list.setCurrentRow(current)

    def _slot_changed(self, row: int) -> None:
        if row < 0:
            return
        self.selected_slot = self.slot_keys[row]
        self.slot_title.setText(SLOT_LABELS[self.selected_slot])
        self.track_list.clear()
        for raw in self.settings.data["slots"][self.selected_slot]:
            entry = normalize_entry(raw)
            start = format_time(entry["start_ms"])
            end = format_time(entry["end_ms"], blank_zero=True) or "end"
            self.track_list.addItem(f"{Path(entry['path']).name}   [{start} → {end}]")
        if self.track_list.count():
            self.track_list.setCurrentRow(0)
        else:
            self._track_selected(-1)

    def _track_selected(self, row: int) -> None:
        tracks = self.settings.data["slots"].get(self.selected_slot, [])
        if not (0 <= row < len(tracks)):
            self.start_time.setText("0:00.000")
            self.end_time.clear()
            return
        entry = normalize_entry(tracks[row])
        self.start_time.setText(format_time(entry["start_ms"]))
        self.end_time.setText(format_time(entry["end_ms"], blank_zero=True))

    def _save_timestamps(self) -> None:
        row = self.track_list.currentRow()
        tracks = self.settings.data["slots"].get(self.selected_slot, [])
        if not (0 <= row < len(tracks)):
            QMessageBox.information(self, APP_NAME, "Select a song first.")
            return
        try:
            start = parse_time(self.start_time.text())
            end = parse_time(self.end_time.text(), allow_blank=True)
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid timestamp", str(exc))
            return
        if end and end <= start:
            QMessageBox.warning(self, "Invalid timestamp", "End position must be after start position.")
            return
        entry = normalize_entry(tracks[row])
        entry["start_ms"] = start
        entry["end_ms"] = end
        tracks[row] = entry
        self._save_and_refresh(selected_track=row)

    def _add_tracks(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Add downloaded music", "", SUPPORTED_AUDIO)
        if not files:
            return
        tracks = self.settings.data["slots"][self.selected_slot]
        for path in files:
            if not any(normalize_entry(track)["path"] == path for track in tracks):
                tracks.append({"path": path, "start_ms": 0, "end_ms": 0})
        self._save_and_refresh()

    def _remove_tracks(self) -> None:
        rows = sorted({item.row() for item in self.track_list.selectedIndexes()}, reverse=True)
        tracks = self.settings.data["slots"][self.selected_slot]
        for row in rows:
            if 0 <= row < len(tracks):
                tracks.pop(row)
        self._save_and_refresh()

    def _clear_tracks(self) -> None:
        self.settings.data["slots"][self.selected_slot] = []
        self._save_and_refresh()

    def _preview_slot(self) -> None:
        if self.selected_slot in CONTEXT_SLOTS:
            self.audio.context(self.selected_slot, force=True)
        else:
            self.audio.stinger(self.selected_slot)

    def _toggle_playback(self) -> None:
        if not self.audio.toggle_pause():
            self._preview_slot()

    def _browse_log(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Select Orion.log", self.log_path.text(), "Log (*.log)")
        if path:
            self.log_path.setText(path)
            self.settings.data["log_path"] = path
            self.settings.save()
            self.watcher.set_path(path)

    def _options_changed(self) -> None:
        self.settings.data.update(
            {
                "volume": self.volume.value(),
                "stinger_volume": self.stinger_volume.value(),
                "fade_ms": self.fade.value(),
                "shuffle": self.shuffle.isChecked(),
                "auto_play": self.auto_play.isChecked(),
                "minimize_to_tray": self.to_tray.isChecked(),
            }
        )
        self.audio._apply_volumes()
        self.settings.save()

    def _save_and_refresh(self, selected_track: int = -1) -> None:
        row = self.slot_list.currentRow()
        self.settings.save()
        self._refresh_slots()
        self.slot_list.setCurrentRow(max(0, row))
        self._slot_changed(max(0, row))
        if selected_track >= 0 and selected_track < self.track_list.count():
            self.track_list.setCurrentRow(selected_track)

    def _handle_event(self, kind: str, value: str) -> None:
        if kind == "map":
            self.current_map = value
        elif kind == "mode":
            self.current_mode = value
        elif kind == "state":
            self.current_state = value
            friendly = SLOT_LABELS.get(value, value.replace("_", " ").title())
            details = " / ".join(part for part in (self.current_mode, self.current_map) if part)
            self.state_label.setText(f"State: {friendly}")
            self.detail_status.setText(details)
            if self.auto_play.isChecked() and self.game_running:
                self.audio.context(value)
        elif kind == "stinger" and self.auto_play.isChecked() and self.game_running:
            self.audio.stinger(value)

    def _connection_changed(self, online: bool, message: str) -> None:
        was_running = self.game_running
        self.game_running = online
        self.connection_dot.setObjectName("onlineDot" if online else "offlineDot")
        self.connection_dot.style().unpolish(self.connection_dot)
        self.connection_dot.style().polish(self.connection_dot)
        self.connection_text.setText(message)
        if online and not was_running and self.auto_play.isChecked():
            self.audio.context(self.current_state)
        elif not online and was_running:
            self.audio.stop()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.to_tray.isChecked() and self.tray.isVisible():
            self.hide()
            event.ignore()
            self.tray.showMessage(APP_NAME, "Still following EMPULSE in the tray.")
        else:
            event.accept()
            QApplication.instance().quit()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
