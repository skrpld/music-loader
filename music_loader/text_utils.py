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


def clean_track_title(title: str) -> str:
    cleaned = title
    for pattern in _PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\.(mp3|flac|m4a|wav|opus|webm)$", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip(" -|")
