# Music Loader

Скачивает музыку с метаданными из Spotify (через spotDL) и SoundCloud
(через yt-dlp), ищет синхронизированные тексты (.lrc) и готовит библиотеку
для Symfonium. Прогресс, скорость и статистика скачивания показываются в
живом терминальном интерфейсе (на базе `rich`).

## Структура проекта

```
music-loader/
├── pyproject.toml          # зависимости и точка входа
├── README.md
└── music_loader/           # исходный код пакета
    ├── __main__.py         # python -m music_loader
    ├── cli.py               # аргументы командной строки, главный цикл
    ├── config.py             # пути и константы
    ├── deps.py               # проверка ffmpeg / spotdl / yt-dlp
    ├── process.py            # запуск подпроцессов с потоковым парсингом вывода
    ├── spotify.py             # логика скачивания со Spotify
    ├── soundcloud.py          # логика скачивания с SoundCloud
    ├── lyrics.py              # поиск .lrc текстов
    ├── playlist.py            # обновление .m3u8 плейлиста SoundCloud
    ├── text_utils.py          # очистка названий треков
    └── ui.py                  # живой дашборд (rich): прогресс-бары, лог, статистика
```

Код разложен по файлам по одной причине — так проще редактировать/чинить
отдельную часть (например, только парсинг вывода yt-dlp), не трогая всё
остальное. Всё лежит в поддиректории `music_loader/`, потому что это
устанавливаемый Python-пакет (так его можно поставить через `pip install -e .`
и вызывать командой `music-loader` откуда угодно, а не только из папки
со скриптом).

## Установка

```bash
cd music-loader
pip install -e .
```

Также потребуется системный `ffmpeg` (не ставится через pip):

```bash
# Debian/Ubuntu
sudo apt install ffmpeg
# macOS
brew install ffmpeg
```

После установки `-e .` появится команда `music-loader` (см. `pyproject.toml`,
секция `[project.scripts]`).

## Использование

Путь к ссылкам (или сама ссылка) и целевая папка Music указываются сразу
при запуске:

```bash
# Одна ссылка
music-loader "https://open.spotify.com/album/..." -o /path/to/Music

# Файл со списком ссылок (по одной на строку)
music-loader links.txt -o /path/to/Music

# Несколько источников сразу
music-loader links.txt "https://soundcloud.com/..." -o /path/to/Music
```

Если не указать ссылку или папку — скрипт спросит их интерактивно при
запуске (один раз, до начала загрузки), но их всё равно нужно будет ввести
до старта работы.

Альтернативный запуск без установки пакета:

```bash
pip install -r <(python3 -c "import tomllib;print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))")
python3 -m music_loader links.txt -o ./Music
```

## Замечание про парсинг прогресса

Прогресс/скорость для SoundCloud (yt-dlp) парсятся из стандартного формата
вывода `[download] 45.2% of 3.45MiB at 1.23MiB/s ETA 00:02` — он стабилен.
Прогресс для Spotify (spotdl) определяется по числам-процентам в выводе
best-effort — формат вывода spotdl может немного отличаться между версиями,
поэтому если процент не удаётся распознать, бар показывает индикатор
активности, а точные сообщения всё равно видно в панели "Activity".

### SoundCloud pipeline and duplicate detection

SoundCloud downloads use a producer/worker pipeline: yt-dlp downloads only the source audio, then a configurable pool performs MP3 conversion, metadata and thumbnail embedding. The default is 4 post-processing workers and 2 lyrics workers; configure them with `--soundcloud-workers N` and `--lyrics-workers N`. Lyrics are queued independently and do not block subsequent audio downloads.

Track discovery for a playlist/set/discography link uses yt-dlp's `--flat-playlist` listing, which only fetches track ids and URLs instead of visiting every track's page for full metadata upfront. This is what keeps the initial "resolving tracks" step fast even for large playlists. Full metadata (artist, duration, cover art, description) needed for tagging is instead fetched per-track, right when that track downloads, via `--write-info-json`.

A hidden `.sc_index.json` in the SoundCloud directory maps the SoundCloud track ID to the actual local file. The downloader also performs a legacy compatibility scan using embedded artist/title/duration metadata, so files created before the index existed can be recognized without relying only on filenames or `--no-overwrites`. Recognized legacy files are promoted into the exact ID mapping and the existing `.sc_archive.txt` is updated only after a successful post-processing step. Track statistics are counted once per discovered track as `downloaded / already existed / failed`.
