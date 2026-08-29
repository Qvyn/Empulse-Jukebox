"""THE FINALS Jukebox prototype.

This deliberately reuses EMPULSE Jukebox's mature audio/UI implementation while
swapping only the game-specific detector and branding. It is a separate desktop
application and does not inject into, hook, read memory from, or modify THE FINALS.
"""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import sys

from PySide6.QtWidgets import QApplication, QCheckBox, QLabel

import empulse_jukebox as core
from finals_events import FinalsEventParser


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
CONTEXT_SLOTS = {"menu", "matchmaking", "pre_match", "in_match", "practice", "post_match"}


def config_dir() -> Path:
    root = os.environ.get("APPDATA") or str(Path.home())
    return Path(root) / "THE FINALS Jukebox"


def discovery_logs_dir() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / "Discovery" / "Saved" / "Logs"


def default_log_path() -> Path:
    return discovery_logs_dir() / "Discovery.log"


def _looks_like_game_log(path: Path) -> bool:
    """Reject CEF/browser logs and prefer Unreal/Discovery game logs."""
    name = path.name.lower()
    if name.startswith("cef") or "chromium" in name or "chrome" in name:
        return False
    if name.startswith("discovery"):
        return True
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            sample = handle.read(96 * 1024).lower()
    except OSError:
        return False
    unreal_markers = (
        "loginit:",
        "logload:",
        "lognet:",
        "logworld:",
        "logonline",
        "loggame",
        "loadmap:",
        "bringing world",
    )
    return any(marker in sample for marker in unreal_markers)


def newest_discovery_log() -> Path:
    folder = discovery_logs_dir()
    preferred = default_log_path()
    if preferred.exists() and _looks_like_game_log(preferred):
        return preferred
    if not folder.exists():
        return preferred
    candidates = [
        path for path in folder.glob("*.log")
        if path.is_file() and _looks_like_game_log(path)
    ]
    if not candidates:
        return preferred
    try:
        return max(candidates, key=lambda path: path.stat().st_mtime)
    except OSError:
        return preferred


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
    # Current builds launch one of the -d / -e Win64 binaries behind the
    # Discovery.exe bootstrapper. Keep all three for patch-to-patch changes.
    known = {"discovery.exe", "discovery-d.exe", "discovery-e.exe"}
    if any(name in known for name in names):
        return True
    # Harmless fallback: process-name text only. No process handle is opened.
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


class FinalsLogWatcher(core.LogWatcher):
    def start(self) -> None:
        # Migrate the prototype's bad cef*.log auto-selection and otherwise
        # choose only a file that looks like an Unreal/Discovery game log.
        configured = Path(self.path)
        in_default_folder = configured.parent == discovery_logs_dir()
        invalid_auto_log = in_default_folder and configured.exists() and not _looks_like_game_log(configured)
        missing_default_log = in_default_folder and not configured.exists()
        if invalid_auto_log or missing_default_log:
            self.path = newest_discovery_log()
        super().start()

    def _prime_from_existing_log(self) -> None:
        if not self.path.exists():
            # Log availability is separate from process availability. The core
            # EMPULSE watcher used one signal for both, which could mark a live
            # game offline merely because its expected log file was absent.
            self.connection_changed.emit(
                self._was_running,
                "THE FINALS detected; usable game log not found"
                if self._was_running
                else "Waiting for THE FINALS",
            )
            return
        super()._prime_from_existing_log()

    def check_process(self) -> None:
        if sys.platform != "win32":
            self.connection_changed.emit(self.path.exists(), "Test mode (non-Windows)")
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
        super().__init__()
        self._apply_finals_branding()

    def _apply_finals_branding(self) -> None:
        self.setWindowTitle(APP_NAME)
        self.tray.setToolTip(APP_NAME)
        self.connection_text.setText(
            "THE FINALS detected" if self.game_running else "Waiting for THE FINALS"
        )

        # The reusable core predates per-game branding hooks. Replace the few
        # literal labels after construction without forking the audio engine.
        for label in self.findChildren(QLabel):
            text = label.text()
            replacements = {
                "EMPULSE  //  JUKEBOX": "THE FINALS  //  JUKEBOX",
                "ORION LOG": "DISCOVERY LOG",
                "Waiting for EMPULSE": "Waiting for THE FINALS",
            }
            if text in replacements:
                label.setText(replacements[text])
        for checkbox in self.findChildren(QCheckBox):
            if checkbox.text() == "Follow EMPULSE automatically":
                checkbox.setText("Follow THE FINALS automatically")

    def _browse_log(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select THE FINALS log",
            self.log_path.text(),
            "Log (*.log);;All files (*)",
        )
        if path:
            self.log_path.setText(path)
            self.settings.data["log_path"] = path
            self.settings.save()
            self.watcher.set_path(path)

    def closeEvent(self, event) -> None:
        if self.to_tray.isChecked() and self.tray.isVisible():
            self.hide()
            event.ignore()
            self.tray.showMessage(APP_NAME, "Still following THE FINALS in the tray.")
        else:
            event.accept()
            QApplication.instance().quit()


# Patch only the game-specific seams before any Settings/Window instance exists.
core.APP_NAME = APP_NAME
core.SLOT_LABELS = SLOT_LABELS
core.CONTEXT_SLOTS = CONTEXT_SLOTS
core.config_dir = config_dir
core.default_log_path = default_log_path
core.EmpulseEventParser = FinalsEventParser
core.LogWatcher = FinalsLogWatcher


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = FinalsMainWindow()
    # Reflect an automatically selected rotated log in the text box.
    window.log_path.setText(str(window.watcher.path))
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
