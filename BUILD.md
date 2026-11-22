# Building MOOOV_player

## Mac
```bash
packaging/build_mac.sh
```
Output:
- `dist/MOOOV_player.app`: the application.
- `dist/MOOOV_player-<version>-mac-arm64.dmg`: the file to send to others. Open it and drag the app into Applications.

**First launch on another Mac.** The app is not signed by Apple. On the first launch macOS will say it cannot verify the developer. To get past this:
1. Try to open the app once.
2. Open **System Settings → Privacy & Security** and click **Open Anyway** at the bottom.
3. After that the app opens normally.

On macOS 14 and earlier, you can instead **right-click → Open**.

The build only runs on Macs with M1–M4 chips (arm64). An Intel Mac version has to be built on an Intel Mac.

**Opening files.**
- Double-click a file or use **Open With → MOOOV_player** in Finder.
- Drag a file onto the Dock icon.
- Drag a file into the app window.

To make it the default player for a type (for example .mov): select the file → ⌘I → "Open with" → MOOOV_player → **Change All…**.

## Windows
On a Windows PC, copy the whole project folder and double-click `packaging\windows\build_windows.bat`.
The script installs everything it needs itself (uv and Python), so nothing has to be set up in advance.

Output:
- `dist\MOOOV_player\MOOOV_player.exe`: the program. The whole `dist\MOOOV_player` folder can be zipped and sent.
- `dist\MOOOV_player-<version>-win-setup.exe`: the installer. It is only built if [Inno Setup 6](https://jrsoftware.org/isdl.php) is installed (free).
  It adds:
  - a Start menu shortcut;
  - **Open with → MOOOV_player** for video and frames;
  - **Open in MOOOV_player** when you right-click a folder with a sequence;
  - an uninstaller.

On first launch Windows SmartScreen may warn about an unknown publisher. Click "More info" → "Run anyway".

## Notes
- Version: the `version` line in `pyproject.toml` and `VERSION` in `packaging/MOOOV_player.spec` / `AppVersion` in `packaging/windows/installer.iss`.
- The app icon is generated from the design system (`packaging/make_icons.py`).
- Codec details in the **i** panel come from `ffprobe` if it is installed on the system. Without it the app shows a short version from OpenCV.
- Build check without a screen: `MOOOV_SNAPSHOT=out.png <path to the binary> <file>` saves the frame to `out.png` and quits.
