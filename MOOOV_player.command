#!/bin/zsh
# Двойной клик в Finder — запуск MOOOV_player
cd "$(dirname "$0")" && exec uv run --quiet MOOOV_player.py "$@"
