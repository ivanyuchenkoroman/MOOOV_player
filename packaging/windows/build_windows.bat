@echo off
rem ===== MOOOV_player: Windows build =====
rem Run it by double-clicking it on a Windows PC, inside the project folder (packaging\windows).
rem Result:
rem   dist\MOOOV_player\MOOOV_player.exe   - the program (folder, can be zipped and sent)
rem   dist\MOOOV_player-<ver>-win-setup.exe - installer (if Inno Setup is installed)
setlocal
cd /d "%~dp0\..\.."

where uv >nul 2>nul
if errorlevel 1 (
  echo Installing uv ^(Python package manager^)...
  powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
  set "PATH=%USERPROFILE%\.local\bin;%PATH%"
)

echo Installing dependencies...
uv sync || goto :error
uv run python packaging\make_icons.py || goto :error

echo Building the program...
uv run pyinstaller --noconfirm --clean packaging\MOOOV_player.spec || goto :error

set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ISCC%" (
  echo Building the installer...
  "%ISCC%" packaging\windows\installer.iss || goto :error
) else (
  echo.
  echo Inno Setup not found - installer skipped.
  echo To get a setup.exe, install it from https://jrsoftware.org/isdl.php and run this script again.
  powershell -c "Compress-Archive -Force dist\MOOOV_player dist\MOOOV_player-win.zip"
  echo Made a zip instead: dist\MOOOV_player-win.zip
)
echo.
echo Done. The program is in dist\MOOOV_player\MOOOV_player.exe
pause
exit /b 0

:error
echo.
echo BUILD FAILED - see the messages above.
pause
exit /b 1
