"""Text helpers for turning noisy file titles into clean lyric-search queries."""
import re

_JUNK_WORDS = (
    r"official|audio|video|lyric|lyrics|prod\.?|remix|out now|"
    r"free download|premiere|hq|hd|visualizer|explicit|clean|"
    r"monstercat|nocopyrightsounds|ncs release"
)
_PATTERNS = [
    rf"[\(\[\{{][^\(\)\[\]\{{\}}]*(?:{_JUNK_WORDS})[^\(\)\[\]\{{\}}]*[\)\]\}}]",
    r"[\(\[\{]\s*[\)\]\}]",  # empty brackets left after cleaning
    r"\s[-|]\s*$",           # trailing dash/pipe
]
_EXTENSION_RE = re.compile(r"\.(mp3|flac|m4a|wav|opus|webm|ogg)$", re.IGNORECASE)


def clean_track_title(title: str) -> str:
    cleaned = _EXTENSION_RE.sub("", title or "")
    for pattern in _PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.strip(" -|_")
    # A title made up entirely of junk markers would otherwise become an empty
    # query; fall back to the original text so a search is still possible.
    if not cleaned:
        cleaned = re.sub(r"\s+", " ", _EXTENSION_RE.sub("", title or "")).strip()
    return cleaned
