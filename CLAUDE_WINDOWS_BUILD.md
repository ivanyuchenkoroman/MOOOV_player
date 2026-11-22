# Задание для Claude на Windows-ПК: собрать MOOOV_player 1.1.0

Ты на Windows-машине. Нужно собрать Windows-версию плеера MOOOV_player и проверить её. Код уже готов и проверен на Маке. **Код не переписывай**: правь его, только если сборка или запуск падают из-за Windows, и каждую такую правку опиши в отчёте.

## Что есть
Пользователь положил на этот ПК архив `MOOOV_player-1.1.0-windows-build-kit.zip`. В архиве:
- `MOOOV_player.py`, `annotations.py`, `playlist.py`, `ui_theme.py` — код на PySide6 + OpenCV + numpy + OpenEXR;
- `pyproject.toml`, `uv.lock` — зависимости, ставятся через uv, Python ≥ 3.11;
- `packaging\MOOOV_player.spec` — сборка PyInstaller в режиме onedir, иконка `packaging\MOOOV_player.ico`;
- `packaging\windows\build_windows.bat` — делает всё по шагам ниже;
- `packaging\windows\installer.iss` — установщик Inno Setup 6;
- `BUILD.md`, `RELEASE_NOTES.md`.

## Шаги
1. Распакуй архив в `C:\build\MOOOV_player` (короткий путь без пробелов и кириллицы).
2. Проверь, что есть `uv`. Если нет: `powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"`, затем добавь `%USERPROFILE%\.local\bin` в PATH текущей сессии.
3. Из корня проекта выполни:
   ```
   uv sync
   uv run python packaging\make_icons.py
   uv run pyinstaller --noconfirm --clean packaging\MOOOV_player.spec
   ```
   Результат: `dist\MOOOV_player\MOOOV_player.exe`.
4. Установщик: проверь `"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"`. Если его нет, спроси пользователя, можно ли поставить Inno Setup 6 (`winget install JRSoftware.InnoSetup`, бесплатный). Затем:
   ```
   "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" packaging\windows\installer.iss
   ```
   Результат: `dist\MOOOV_player-1.1.0-win-setup.exe`.
5. Если Inno Setup ставить не разрешили, сделай zip: `powershell -c "Compress-Archive -Force dist\MOOOV_player dist\MOOOV_player-1.1.0-win.zip"`.

## Проверка (обязательно)
1. **Запуск без окна.** Сделай короткое видео: `uv run python -c "import cv2,numpy as np; w=cv2.VideoWriter('test.mp4',cv2.VideoWriter_fourcc(*'mp4v'),24,(640,360)); [w.write(np.full((360,640,3),i*5%255,np.uint8)) for i in range(48)]; w.release()"`.
   Затем выполни `set MOOOV_SNAPSHOT=snap.png` и `dist\MOOOV_player\MOOOV_player.exe test.mp4`. exe собран без консоли, поэтому командная строка вернётся сразу. Подожди около 6 секунд: окно откроется, через 4 секунды сохранит `snap.png` с кадром и закроется само. В PowerShell удобнее так: `$env:MOOOV_SNAPSHOT="$PWD\snap.png"; Start-Process dist\MOOOV_player\MOOOV_player.exe test.mp4 -Wait`. Открой `snap.png` и посмотри на него.
2. **Последовательность кадров.** Сохрани 10 PNG `seq.0001.png … seq.0010.png` и сделай такой же snapshot, передав путь к первому кадру.
3. **Обычный запуск.** Запусти `MOOOV_player.exe` и проверь:
   - окно открывается без чёрного окна консоли;
   - работают меню, плейлист и рисование;
   - при открытии плашки **i** не мелькает консоль.
3a. **Слои EXR.** Сделай EXR с каналами `R G B A diffuse.R diffuse.G diffuse.B Z` (через `OpenEXR.File(header, channels).write(...)`), открой: справа от EV появится список слоёв, переключение на diffuse и Z меняет картинку.
4. **Слои.** Открой `test.mp4`, затем перетащи PNG-секвенцию из пункта 2 на окно в зону «Add as layer» (или File → Add Layers…). На таймлайне появятся полоски слоёв. Проверь:
   - на полоске работают режим (клик → список), ползунок прозрачности, глаз и ×;
   - полоска перетаскивается вверх и вниз; если утащить нижнюю (A) наверх, основным клипом становится другой;
   - ≡ в центре и правый клик открывают меню слоя.
   - полоска тянется влево и вправо — слой сдвигается по времени (до начала его не видно);
   - иконки на полоске: **i** — метаданные, полукруг — шторка (новый слой приходит без неё), солнце — яркость, прозрачность, режим, сдвиг.
5. **Установщик.** Если собрался, поставь его и проверь:
   - ярлык в «Пуск»;
   - пункт «Открыть с помощью → MOOOV_player» на .mp4;
   - пункт «Open in MOOOV_player» в меню папки.

   Потом удали программу через деинсталлятор.

## Если что-то упало
- **Ошибки импорта в собранном exe** (`ModuleNotFoundError`, DLL OpenCV или OpenEXR): добавь `hiddenimports` или `collect_dynamic_libs` в `packaging\MOOOV_player.spec`, а не трогай код.
- **Ошибки OpenGL** (QOpenGLWidget) на старте: проверь драйвер, а в спеке оставь всё как есть. Опиши ошибку пользователю.
- **Пересобирать:** всегда с `--clean`.

## Отчёт пользователю
Сообщи:
- пути к готовым файлам: `dist\MOOOV_player-1.1.0-win-setup.exe` и/или zip;
- их размеры;
- что проверено и что нет;
- все правки в коде или спеке с причиной (diff).

Изменённые файлы пусть пользователь перенесёт обратно на Мак, чтобы правки попали в основной проект.
