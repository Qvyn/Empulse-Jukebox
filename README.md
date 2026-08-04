# EMPULSE Jukebox v0.2.1

EMPULSE Jukebox plays downloaded music locally and changes tracks as EMPULSE
moves between its menu, matchmaking, live gameplay, practice range, and
post-match sequence. Other players never hear the music.

## Start

1. Extract the entire ZIP to a normal folder.
2. Double-click `Install_and_Run.bat` the first time.
3. Add songs to each slot in the app.
4. Select a song and set its event-specific Start and optional End position.
5. Start EMPULSE. The app automatically follows the game.
6. On later launches, use `Run_EMPULSE_Jukebox.bat`.

Python is the only prerequisite. The first-run script creates an isolated
environment inside the app folder and installs PySide6 there; it does not alter
EMPULSE or its Python-free game installation.

## Detected automatically

- Main menu
- Match found / server startup / loading
- Game mode and map
- Live match start
- Practice range
- End-of-match presentation
- Return to menu
- Your Double Kill, Triple Kill, Quad Kill, and 5 Kill Streak events

Each major context has a playlist. Kill events use a second audio channel, so a
short stinger can play over the main music instead of replacing it.

Music and stinger volume are controlled independently. Re-triggering a stinger
restarts its single channel, so rapid events do not stack multiple copies.

All music changes—including Skip and automatic end-of-segment advances—use the
configured fade duration.

## Song timestamps

Every song assignment has its own Start and End position. This means one long
album file can supply every event, with each event beginning at a different
point—matching the useful part of CS-Jukebox's workflow.

- Start accepts `M:SS`, `M:SS.mmm`, or `H:MM:SS`.
- End accepts the same formats. Leave it blank to play through the song's end.
- Select the song, enter the positions, and click `Save timestamps`.
- The song list displays the saved range beside every assignment.

## v0.2 fixes

- Replaced the unreliable exact `tasklist` filter with full Windows process
  enumeration, plus window-title and broad task-list fallbacks.
- Play/Pause can no longer display Playing without a loaded audio source.
- Pressing Play/Pause with no source now starts the selected event slot.
- Playback errors from Windows' media backend are shown directly in the status.
- Existing v0.1 song selections migrate automatically and begin at `0:00`.

## v0.2.1 hotfix

- Prevented a Windows/Qt multimedia hang when matchmaking switches between
  event segments that use the same audio file.
- End-of-segment advances now run outside multimedia callbacks, preventing
  re-entrant source reloads during map transitions.

## Safety model

The app reads newly appended text from:

`%LOCALAPPDATA%\Orion\Saved\Logs\Orion.log`

It does not inject a DLL, attach to the EMPULSE process, inspect memory, alter
game files, simulate input, or communicate with the game server. Process
detection uses Windows' ordinary `tasklist` command only.

## Current limitations

- EMPULSE does not log a clean local-player victory/defeat result.
- Multiplayer deaths produce effects for many players without identifying the
  local player reliably, so personal death music is intentionally not included.
- EMPULSE updates may rename log messages. The parser is isolated in
  `empulse_events.py` so rules can be updated without rewriting the player.
- Codec availability comes from Windows Media Foundation through Qt. MP3, WAV,
  FLAC, M4A/AAC, OGG, and WMA are accepted; actual support can vary with the
  Windows codecs installed.

## Troubleshooting

- If the status says `Waiting for Orion.log`, use Browse and select the current
  file from `%LOCALAPPDATA%\Orion\Saved\Logs`.
- Keep the music files in place after adding them. The app stores file paths,
  not copies of the songs.
- If setup fails, run `Install_and_Run.bat` again and copy the visible error.
- Settings are stored in `%APPDATA%\EMPULSE Jukebox\settings.json`.
