"""THE FINALS Jukebox prototype.

This version keeps the app fully external. Game presence is detected with normal
Windows process enumeration; context changes are detected from low-frequency
whole-screen captures. It does not inject, hook, inspect game memory, modify game
files, simulate input, or communicate with game servers.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import sys

from PySide6.QtCore import QRect
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QLabel,
    QMessageBox,
    QPushButton,
)

import empulse_jukebox as core
from finals_screen import ScreenStateDetector


APP_NAME = "THE FINALS Jukebox"
SLOT_LABELS = {
    "menu": "Main Menu",
    "matchmaking": "Matchmaking / Queue",
    "pre_match": "Match Found / Loading",
    "in_match": "In Match",
    "practice": "Practice Range",
    "post_match": "End of Match",
    "double_kill": "Double Kill stinger",
    "triple_kill": "Triple Kill stinger",
    "quad_kill": "Quad Kill stinger",
    "penta_kill": "Penta Kill stinger",
    "hexa_kill": "Hexa Kill stinger",
    "five_kill": "5 Kill Streak stinger",
    "ten_kill": "10 Kill Streak stinger",
    "fifteen_kill": "15 Kill Streak stinger",
    "twenty_kill": "20 Kill Streak stinger",
}
CONTEXT_SLOTS = {
    "menu",
    "matchmaking",
    "pre_match",
    "in_match",
    "practice",
    "post_match",
}


def config_dir() -> Path:
    root = os.environ.get("APPDATA") or str(Path.home())
    return Path(root) / "THE FINALS Jukebox"


def default_log_path() -> Path:
    # The shared EMPULSE settings object still contains a legacy log_path key.
    # THE FINALS does not use it; keep it harmless and hidden from the UI.
    return config_dir() / "unused.log"


def _enumerate_process_names() -> list[str]:
    if sys.platform != "win32":
        return []
    names: list[str] = []
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
            return names
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        try:
            ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                names.append(entry.szExeFile.lower())
                ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
    except (AttributeError, OSError, ValueError):
        pass
    return names


def finals_is_running() -> bool:
    names = _enumerate_process_names()
    known = {"discovery.exe", "discovery-d.exe", "discovery-e.exe"}
    if any(name in known for name in names):
        return True
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
        low = result.stdout.lower()
        return any(f'"{name}"' in low for name in known)
    except (OSError, subprocess.SubprocessError):
        return False


class FinalsProcessWatcher(core.LogWatcher):
    """Reuse the core process-status signal without doing any log polling."""

    def start(self) -> None:
        self._process_checked = False
        self.check_process()
        self.timer.stop()
        self.process_timer.start()

    def set_path(self, _path: str) -> None:
        return

    def poll(self) -> None:
        return

    def _prime_from_existing_log(self) -> None:
        return

    def check_process(self) -> None:
        if sys.platform != "win32":
            self.connection_changed.emit(False, "THE FINALS detection requires Windows")
            return
        running = finals_is_running()
        if not self._process_checked or running != self._was_running:
            self._process_checked = True
            self._was_running = running
            self.connection_changed.emit(
                running,
                "THE FINALS detected" if running else "THE FINALS is not running",
            )


class FinalsMainWindow(core.MainWindow):
    def __init__(self) -> None:
        self.detector: ScreenStateDetector | None = None
        super().__init__()
        self._apply_finals_branding()
        self._build_screen_detector_controls()

        self.detector = ScreenStateDetector(
            self.settings,
            ignore_rect=self._jukebox_screen_rect,
            parent=self,
        )
        self.detector.state_changed.connect(self._screen_state_changed)
        self.detector.status_changed.connect(self._screen_status_changed)
        self._refresh_reference_button_text()
        if self.game_running:
            # The audio UI may be using Main Menu as a harmless startup fallback,
            # but the visual classifier stays uncommitted until it sees a real
            # confident state. This also lets the app be launched mid-match.
            self.detector.set_current_state("")
            self.detector.start()

    def _apply_finals_branding(self) -> None:
        self.setWindowTitle(APP_NAME)
        self.tray.setToolTip(APP_NAME)
        self.connection_text.setText(
            "THE FINALS detected" if self.game_running else "Waiting for THE FINALS"
        )

        for label in self.findChildren(QLabel):
            text = label.text()
            replacements = {
                "EMPULSE  //  JUKEBOX": "THE FINALS  //  JUKEBOX",
                "ORION LOG": "SCREEN DETECTOR",
                "Waiting for EMPULSE": "Waiting for THE FINALS",
            }
            if text in replacements:
                label.setText(replacements[text])
        for checkbox in self.findChildren(QCheckBox):
            if checkbox.text() == "Follow EMPULSE automatically":
                checkbox.setText("Follow THE FINALS automatically")

    def _build_screen_detector_controls(self) -> None:
        toolbar = self.log_path.parentWidget()
        layout = toolbar.layout() if toolbar else None
        self.log_path.hide()
        if toolbar:
            for button in toolbar.findChildren(QPushButton):
                if button.text().strip().upper() == "BROWSE":
                    button.hide()
            for label in toolbar.findChildren(QLabel):
                if label.text() in {"ORION LOG", "DISCOVERY LOG"}:
                    label.setText("SCREEN DETECTOR")

        self.screen_scan_status = QLabel("Whole-screen detector ready")
        self.screen_scan_status.setObjectName("connectionText")
        self.learn_screen_button = QPushButton("LEARN CURRENT SCREEN")
        self.learn_screen_button.setObjectName("accentButton")
        self.clear_screen_button = QPushButton("CLEAR STATE REFS")
        self.learn_screen_button.clicked.connect(self._learn_current_screen)
        self.clear_screen_button.clicked.connect(self._clear_current_screen_refs)
        if layout:
            layout.addWidget(self.screen_scan_status, 1)
            layout.addWidget(self.learn_screen_button)
            layout.addWidget(self.clear_screen_button)

        self.slot_list.currentRowChanged.connect(
            lambda _row: self._refresh_reference_button_text()
        )

    def _jukebox_screen_rect(self) -> QRect | None:
        if self.isVisible() and not self.isMinimized():
            return QRect(self.frameGeometry())
        return None

    def _learn_current_screen(self) -> None:
        if not self.detector:
            return
        state = self.selected_slot
        if state not in CONTEXT_SLOTS:
            QMessageBox.information(
                self,
                APP_NAME,
                "Whole-screen learning currently supports the six main context channels, not kill stingers.",
            )
            return
        if self.detector.capture_reference(state):
            self._refresh_reference_button_text()

    def _clear_current_screen_refs(self) -> None:
        if not self.detector:
            return
        state = self.selected_slot
        if state not in CONTEXT_SLOTS:
            return
        self.detector.clear_references(state)
        self._refresh_reference_button_text()

    def _refresh_reference_button_text(self) -> None:
        if not hasattr(self, "learn_screen_button"):
            return
        state = self.selected_slot
        if state in CONTEXT_SLOTS and self.detector:
            count = self.detector.reference_count(state)
            self.learn_screen_button.setText(f"LEARN CURRENT SCREEN  ({count}/6)")
            self.learn_screen_button.setEnabled(True)
            self.clear_screen_button.setEnabled(count > 0)
        else:
            self.learn_screen_button.setText("LEARN CURRENT SCREEN")
            self.learn_screen_button.setEnabled(False)
            self.clear_screen_button.setEnabled(False)

    def _screen_status_changed(self, message: str) -> None:
        self.screen_scan_status.setText(message)

    def _screen_state_changed(self, state: str, similarity: float) -> None:
        if state not in CONTEXT_SLOTS:
            return
        self._handle_event("state", state)
        friendly = SLOT_LABELS[state]
        self.detail_status.setText(f"Visual match: {friendly} ({similarity:.0%})")

    def _connection_changed(self, online: bool, message: str) -> None:
        had_no_context = self.current_state == "offline"
        core.MainWindow._connection_changed(self, online, message)

        if self.detector:
            if online:
                # A new game process gets a fresh visual state. Do not constrain
                # the first match based on whatever context the previous run used.
                self.detector.set_current_state("")
                self.detector.start()
            else:
                self.detector.stop()

        if online and had_no_context and self.current_state == "offline":
            self.current_state = "menu"
            self.state_label.setText("State: Main Menu")
            if self.auto_play.isChecked() and self.game_running:
                self.audio.context("menu")

    def closeEvent(self, event) -> None:
        if self.to_tray.isChecked() and self.tray.isVisible():
            self.hide()
            event.ignore()
            self.tray.showMessage(APP_NAME, "Still following THE FINALS in the tray.")
        else:
            event.accept()
            QApplication.instance().quit()


core.APP_NAME = APP_NAME
core.SLOT_LABELS = SLOT_LABELS
core.CONTEXT_SLOTS = CONTEXT_SLOTS
core.config_dir = config_dir
core.default_log_path = default_log_path
core.LogWatcher = FinalsProcessWatcher


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = FinalsMainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
