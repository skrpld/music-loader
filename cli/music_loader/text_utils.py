"""Text helpers: SoundCloud title parsing, name normalization and the
version markers ("sped up", "remix", ...) that lyrics matching relies on.

SoundCloud has no structured artist/title fields for most uploads; the real
credits live inside the title, in many shapes (examples from the DOOM RUSHAZ
collective):

    TARABANDZ + platov + WHITENER - under heaven [prod. haru matsui]
    WHITENER, platov - heroin chic (feat aquakey) hexd
    sip doomstation ft. benjamingotbenz, haru matsui, sg, platov
    09. Pitstop W Aquakey & Platov
    haru matsui - godline (prod. hm9600) *music video in description*
    I Shot The Sheriff (p. systematik)
    WHITENER+BENJAMINBENZ+AQUAKEY+TARABANDZ-STONE COLD(Prod SPACE NIKExQIO)

Smaller scenes have their own shapes too (from a debug log of real runs):

    yung millionaire ((haru matsui + jack13))       "((...))": producers
    thraxx interlude ((me ft. haru matsui))
    Морозы (+elya14062001)                          "(+ X)": a guest
    Mudstock feat. CODE80 (mxstrakh)
    1million ((cryst)) ALBUM OTW                    trailing promo words
    diesel (playasin)                               a producer, when the
                                                    description says "prod by playasin"

`parse_soundcloud_title` turns these into main artists, a clean title (with
a normalized "(feat. ...)" part, like Spotify writes it), producers and a
track number, and drops promotional noise. `format_title` then applies the
formatting settings (where the featured artists go, producer credits,
version markers).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable, Iterable

KnownArtist = Callable[[str], bool]

# -- promotional noise -------------------------------------------------------
_FREE_DL = r"free\s*(?:dl|download)"
_BUY_FREE_DL = rf"buy\s*[=/]\s*{_FREE_DL}"
_JUNK_WORDS = (
    r"official|\bvideo\b|\baudio\b|lyric video|visuali[sz]er|free ?download|free ?dl|"
    r"out now|premiere|in description|in desc|link in bio|" + _BUY_FREE_DL + r"|buy now|"
    r"\brelease\b|клип|премьера|скачать|"
    r"monstercat|nocopyrightsounds|\bncs\b"
)
_JUNK_RE = re.compile(_JUNK_WORDS, re.IGNORECASE)
# Only junk when it is the whole bracket: "(Clean)" yes, "(Clean Bandit Remix)" no.
_JUNK_WHOLE_RE = re.compile(
    r"^\s*(?:(?:clean|explicit|dirty)(?: version)?|hq|hd|4k|mv|m/v|(?:19|20)\d\d)\s*$", re.IGNORECASE
)
# "#phonk #drift": hashtags are promotion, "#1" or "No.#5" are not. A
# hashtag that starts the text ("#J20 XTREME SPORT") is part of the name.
_HASHTAG_RE = re.compile(r"(?<!\S)#[^\W\d_][\w-]*!*")
_LEADING_HASHTAG_RE = re.compile(r"#[^\W\d_][\w-]*(?=\s+\S)")
# A trailing "OUT NOW" / "BUY = FREE DL" outside of brackets.
_JUNK_TAIL_RE = re.compile(rf"\s+(?:out now|{_BUY_FREE_DL}|{_FREE_DL})\s*!*\s*$", re.IGNORECASE)
# "mv in desc", "ALBUM OTW", "new mixtape otw", "mv on yt" after the title.
# Only with a "what" word in front: "Help Is On The Way" stays.
_PROMO_TAIL_RE = re.compile(
    r"\s+(?:new\s+)?(?:mv|m/v|music video|video|clip|album|mixtape|tape|ep|single|snippet)\s+"
    r"(?:in (?:the )?desc(?:ription)?|otw|on yt|on youtube|out now)\s*!*\s*$",
    re.IGNORECASE,
)
# "platov  (@platovoplomo)": SoundCloud display names that carry the handle.
_HANDLE_RE = re.compile(r"\s*[\(\[]\s*@[\w.\-]+\s*[\)\]]")
# "Song | Some Label": a short last part after a pipe that names a label. A
# subtitle ("| Live in Paris", "| Part Two") must stay.
_LABEL_TAIL_RE = re.compile(
    r"^(?:\S+\s+){0,3}(?:records|recordings|recs?|music|label|entertainment|productions?|media|network)$",
    re.IGNORECASE,
)

_BRACKETS = {"(": ")", "[": "]", "{": "}"}

# Producer credits: "(prod. X)", "[PROD. BY X]", "(Prod X)", "(p. X)", "(produced by X)".
_PROD_HEAD_RE = re.compile(r"^\s*(?:prod(?:uced)?\.?(?:\s*by)?|p\.)\s*[:\-]?\s*", re.IGNORECASE)
_PROD_TAIL_RE = re.compile(r"\s+prod(?:uced)?\.?\s*(?:by\s+)?(.+)$", re.IGNORECASE)
# A "prod by X, Y" line in the track description.
_PROD_LINE_RE = re.compile(r"^\s*prod(?:uced)?\b\.?\s*(?:by\b)?\s*[:\-]?\s*(.+)$", re.IGNORECASE)

# Featured artists: "(feat. X)", "[ft X]", "(w/ X)", "(+ X)", "feat X" at the end.
_FEAT_HEAD_RE = re.compile(r"^\s*(?:featuring|feat\b\.?|ft\b\.?|w/|\+)\s*", re.IGNORECASE)
_FEAT_TAIL_RE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring|w/)\s+(.+)$", re.IGNORECASE)
# Bare " W " ("Pitstop W Aquakey & Platov"), only trusted for known artists.
_BARE_WITH_RE = re.compile(r"\s+[Ww]\s+(.+)$")

# Placeholder keeping the position of a bracketed feat credit.
_FEAT_SLOT = "\x00feat\x00"

_TRACK_NUMBER_RE = re.compile(r"^\s*(\d{1,2})\s*[.)]\s+(?=\S)")

# "Artist - Song". A colon or a pipe is not used: "Time: The Musical" is a
# title, not the artist "Time".
_DASH_SEPARATOR_RE = re.compile(r"\s+[-–—]{1,2}\s+")
# "A+B+C-SONG": a "+"-joined artist list followed by a dash without spaces.
_PLUS_DASH_RE = re.compile(r"^([^-–—]+\+[^-–—]+?)\s*[-–—]\s*(\S.*)$")

# A right-hand side that is only one of these is a version/part suffix of
# the title ("Right Now! - Outro"), not a song after an artist name.
_SUFFIX_ONLY_RE = re.compile(
    r"^(?:intro|outro|interlude|skit|remix|live|demo|freestyle|bonus(?: track)?|"
    r"extended(?: mix)?|instrumental|acapella|a cappella|sped up|slowed(?: \+ reverb)?|"
    r"remaster(?:ed)?(?: \d{4})?|radio edit|edit|vip|(?:pt|part)\.? ?\d+|\d{4} remaster)$",
    re.IGNORECASE,
)

# Separators between several artist names.
_ARTIST_SPLIT_RE = re.compile(r"\s*\+\s*|\s*,\s*|\s+&\s+|\s+[xх×]\s+|\s+и\s+|\s+and\s+", re.IGNORECASE)
_FEAT_SPLIT_RE = re.compile(r"\s*\+\s*|\s*,\s*|\s+&\s+|\s+[xх×]\s+|\s+и\s+|\s+and\s+", re.IGNORECASE)

# Version markers. They are kept in the title (a sped-up edit is a different
# recording), and lyrics matching requires them to agree on both sides.
_VARIANT_PATTERNS = [
    ("remix", r"\bre-?mix\b|\brmx\b"),
    ("sped up", r"\bsped[\s-]*up\b|\bspeed[\s-]*up\b|\bnightcore\b|\bfast(?:er)? version\b"),
    ("slowed", r"\bslowed\b|\breverb\b|\bdaycore\b|\bchopped\b|\bscrewed\b"),
    ("instrumental", r"\binstrumental\b|\binst\.?\b|\bminus\b|\bминус\b|\bbeat\b|\btype beat\b"),
    ("acapella", r"\ba ?cap+el+a\b"),
    ("live", r"\blive\b|\bконцерт\b"),
    ("acoustic", r"\bacoustic\b|\bunplugged\b"),
    ("cover", r"\bcover\b|\bкавер\b"),
    ("edit", r"\bedit\b"),
    ("extended", r"\bextended\b"),
    ("vip", r"\bvip\b"),
    ("bootleg", r"\bbootleg\b|\bflip\b|\brework\b|\bmashup\b|\bmash-up\b|\bblend\b"),
    ("hexd", r"\bhex(?:e)?d\b"),
    ("snippet", r"\bsnippet\b|\bpreview\b|\bteaser\b"),
    ("demo", r"\bdemo\b"),
    ("8d", r"\b8d\b"),
    ("bass boosted", r"\bbass ?boost(?:ed)?\b"),
    ("version", r"\bversion\b|\bверсия\b"),
]
_VARIANT_RES = [(name, re.compile(pattern, re.IGNORECASE)) for name, pattern in _VARIANT_PATTERNS]

# Uploads that are DJ sets, mixes or beats carry no singable lyrics.
_NO_LYRICS_RE = re.compile(
    r"\b(?:dj ?set|live set|full set|mixtape mix|guest mix|mix ?#?\d+|podcast|episode|ep\.? ?\d+|"
    r"type beat|beat tape|instrumental|boiler room)\b",
    re.IGNORECASE,
)


@dataclass
class ParsedTitle:
    artists: list[str] = field(default_factory=list)      # main artists from the title
    title: str = ""                                       # clean title incl. "(feat. ...)"
    base_title: str = ""                                  # clean title without the feat part
    featured: list[str] = field(default_factory=list)
    producers: list[str] = field(default_factory=list)
    track_number: int | None = None


# -- generic helpers ---------------------------------------------------------
def collapse(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


_ZERO_WIDTH_RE = re.compile("[\u00ad\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff]")
_QUOTE_TRANSLATION = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'", "\u02bc": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u00ab": '"', "\u00bb": '"',
    "\u2033": '"', "\u2013": "-", "\u2014": "-", "\u2212": "-",
})
# Symbols that carry meaning in a name ("30°", "C♯ minor") and stay.
_KEPT_SYMBOLS = "°♯♭"


def normalize_unicode(text: str | None) -> str:
    """NFKC text without zero-width characters, with straight quotes and
    dashes and full-width forms ("Ｔｉｔｌｅ") turned into plain ones.
    Cyrillic, CJK and accented letters are untouched."""
    value = unicodedata.normalize("NFKC", text or "")
    value = _ZERO_WIDTH_RE.sub("", value).translate(_QUOTE_TRANSLATION)
    return value


@lru_cache(maxsize=None)
def _is_decorative(char: str) -> bool:
    if char in _KEPT_SYMBOLS:
        return False
    return (unicodedata.category(char) in {"So", "Cf", "Co", "Cn", "Cs"}
            or char in "\ufe0e\ufe0f" or "\U0001f3fb" <= char <= "\U0001f3ff")


def strip_symbols(text: str | None) -> str:
    """Removes emoji and decorative symbols (✦ ★ ☆ ♥ 🔥) anywhere in the
    text; math signs and punctuation stay."""
    value = text or ""
    if not value.isascii():
        value = "".join(" " if _is_decorative(char) else char for char in value)
    return collapse(value)


def _strip_edges(text: str) -> str:
    return collapse(text).strip(" -–—|:")


def strip_decorations(name: str | None) -> str:
    """Removes decorative symbols around a display name ("✦ platov ✦") and
    a SoundCloud handle after it ("platov (@platovoplomo)")."""
    text = collapse(_HANDLE_RE.sub(" ", unicodedata.normalize("NFC", name or "")))

    def decorative(char: str) -> bool:
        category = unicodedata.category(char)
        return category in {"So", "Sm", "Sk", "Zs", "Cf", "Co"} or char in "•·・*~_|"

    start, end = 0, len(text)
    while start < end and decorative(text[start]):
        start += 1
    while end > start and decorative(text[end - 1]):
        end -= 1
    return collapse(text[start:end])


def normalize_name(text: str | None) -> str:
    """Comparison key: case-, accent- and punctuation-insensitive."""
    value = unicodedata.normalize("NFKC", text or "").casefold()
    value = value.replace("ё", "е").replace("&", " and ").replace("_", " ")
    value = "".join(
        char if unicodedata.category(char)[0] in {"L", "N"} else " "
        for char in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(char)
    )
    return collapse(value)


def unique(items: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        item = collapse(item)
        key = normalize_name(item)
        if item and key and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def split_artist_names(text: str, known: KnownArtist | None = None, feat: bool = False) -> list[str]:
    """Splits "A + B, C & D" into names. A full string that is a known artist
    ("Simon & Garfunkel") is kept in one piece."""
    text = _strip_edges(text)
    if not text:
        return []
    if known is not None and known(text):
        return [strip_decorations(text)]
    pattern = _FEAT_SPLIT_RE if feat else _ARTIST_SPLIT_RE
    return unique(strip_decorations(part) for part in pattern.split(text))


# -- bracket handling ----------------------------------------------------------
def _bracket_groups(text: str) -> list[tuple[int, int, str]]:
    """Top-level (), [], {} and *...* groups: (start, end, inner text)."""
    groups: list[tuple[int, int, str]] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char in _BRACKETS:
            close = _BRACKETS[char]
            depth, end = 0, index
            while end < len(text):
                if text[end] == char:
                    depth += 1
                elif text[end] == close:
                    depth -= 1
                    if depth == 0:
                        break
                end += 1
            if end < len(text):
                groups.append((index, end + 1, text[index + 1:end]))
                index = end + 1
                continue
        elif char == "*":
            end = text.find("*", index + 1)
            if end > index + 1:
                groups.append((index, end + 1, text[index + 1:end]))
                index = end + 1
                continue
        index += 1
    return groups


def _remove_spans(text: str, spans: list[tuple[int, int]]) -> str:
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + " " + text[end:]
    return collapse(text)


def _is_junk(inner: str) -> bool:
    if _FEAT_HEAD_RE.match(inner) or _PROD_HEAD_RE.match(inner):
        return False      # "(feat. Audio Bullys)" is a credit, not noise
    return bool(_JUNK_RE.search(inner) or _JUNK_WHOLE_RE.match(inner))


def clean_promo(text: str) -> str:
    """Drops promotional brackets, hashtags, emoji and "| FREE DOWNLOAD" /
    "| Label" style tails."""
    text = strip_symbols(text)
    spans = [(start, end) for start, end, inner in _bracket_groups(text) if _is_junk(inner)]
    text = _remove_spans(text, spans)
    parts = re.split(r"\s+\|\s+|\s+//\s+", text)
    parts = [part for index, part in enumerate(parts) if index == 0 or not _JUNK_RE.search(part)]
    if len(parts) > 1 and _LABEL_TAIL_RE.match(parts[-1].strip()) and not variant_markers(parts[-1]):
        parts.pop()
    text = " | ".join(parts)
    text = _JUNK_TAIL_RE.sub("", text)
    if "#" in text:
        lead = _LEADING_HASHTAG_RE.match(text)
        keep = lead.group(0) if lead else ""
        text = keep + _HASHTAG_RE.sub(" ", text[len(keep):])
    text = _PROMO_TAIL_RE.sub("", collapse(text))
    text = re.sub(r"[\(\[\{]\s*[\)\]\}]", " ", text)
    return _strip_edges(tidy_brackets(text))


def tidy_brackets(text: str) -> str:
    """"( whyjuzo )" -> "(whyjuzo)"; a bracket left open at the very end
    ("CAST A SPELL (" once its emoji is gone) is dropped."""
    text = re.sub(r"([\(\[\{])\s+", r"\1", collapse(text))
    text = re.sub(r"\s+([\)\]\}])", r"\1", text)
    return re.sub(r"\s*[\(\[\{]+$", "", text)


def description_producers(description: str | None) -> list[str]:
    """Producers named on a "prod by X, Y" line of a track description."""
    names: list[str] = []
    for line in (description or "").splitlines():
        match = _PROD_LINE_RE.match(line)
        if match:
            names.extend(split_artist_names(match.group(1)))
    return unique(names)


def _extract_bracketed(text: str, head_re: re.Pattern, slot: str = "") -> tuple[str, list[str]]:
    """Removes bracket groups starting with `head_re`; the first one is
    replaced by `slot` when given, so its position can be restored."""
    found: list[str] = []
    spans: list[tuple[int, int]] = []
    for start, end, inner in _bracket_groups(text):
        if text[start] == "*":
            continue
        match = head_re.match(inner)
        if match and inner[match.end():].strip():
            found.append(inner[match.end():].strip())
            spans.append((start, end))
    if not slot or not spans:
        return _remove_spans(text, spans), found
    first = min(spans)
    text = _remove_spans(text, [span for span in spans if span != first])
    # Positions moved after removing the other spans; locate the first one again.
    for start, end, inner in _bracket_groups(text):
        match = head_re.match(inner)
        if text[start] != "*" and match and inner[match.end():].strip():
            return collapse(text[:start] + f" {slot} " + text[end:]), found
    return text, found


def _extract_double_brackets(text: str) -> tuple[str, list[str], list[str]]:
    """"((haru matsui + jack13))" is a producer credit; "((me ft. X))"
    also names a guest. Returns the text without them, the producers and
    the guests."""
    producers: list[str] = []
    featured: list[str] = []
    spans: list[tuple[int, int]] = []
    for start, end, inner in _bracket_groups(text):
        if text[start] != "(" or not (inner.startswith("(") and inner.endswith(")")):
            continue
        groups = _bracket_groups(inner)
        if len(groups) != 1 or groups[0][:2] != (0, len(inner)):
            continue
        credit = _PROD_HEAD_RE.sub("", groups[0][2].strip(), count=1)
        guest = _FEAT_TAIL_RE.search(credit)
        if guest:
            featured.append(guest.group(1))
            credit = credit[:guest.start()]
        if credit.strip():
            producers.append(credit)
        spans.append((start, end))
    return _remove_spans(text, spans), producers, featured


def _extract_hinted_producers(text: str, hints: Iterable[str]) -> tuple[str, list[str]]:
    """Brackets that only name producers known from elsewhere: "diesel
    (playasin)" when the description says "prod by playasin"."""
    keys = {normalize_name(name) for name in hints if normalize_name(name)}
    if not keys:
        return text, []
    producers: list[str] = []
    spans: list[tuple[int, int]] = []
    for start, end, inner in _bracket_groups(text):
        if text[start] not in "([":
            continue
        names = split_artist_names(inner)
        if names and all(normalize_name(name) in keys for name in names):
            producers.append(inner)
            spans.append((start, end))
    return _remove_spans(text, spans), producers


# -- SoundCloud title parsing ----------------------------------------------------
def _looks_like_song_suffix(text: str) -> bool:
    return bool(_SUFFIX_ONLY_RE.match(_strip_edges(re.sub(r"[\(\)\[\]]", " ", text))))


def _split_artist_part(title: str) -> tuple[str, str] | None:
    parts = _DASH_SEPARATOR_RE.split(title, maxsplit=1)
    if len(parts) == 2 and _strip_edges(parts[0]) and _strip_edges(parts[1]):
        return _strip_edges(parts[0]), _strip_edges(parts[1])
    match = _PLUS_DASH_RE.match(title)
    if match:
        return _strip_edges(match.group(1)), _strip_edges(match.group(2))
    return None


def _names_match(names: list[str], others: list[str]) -> bool:
    keys = {normalize_name(name) for name in others if normalize_name(name)}
    return any(normalize_name(name) in keys for name in names)


def parse_soundcloud_title(
    raw_title: str,
    uploader: str = "",
    official_artists: list[str] | None = None,
    known: KnownArtist | None = None,
    canonical: Callable[[str], str] | None = None,
    producer_hints: Iterable[str] = (),
    strip_promo: bool = True,
) -> ParsedTitle:
    """Splits a SoundCloud title into credits and a clean song title.

    Artist priority: SoundCloud's own artist field (filled for label
    releases) -> "Artist - Song" from the title -> the uploader.

    `producer_hints` are producers named elsewhere (the description): a
    bracket holding only those names is a producer credit. With
    `strip_promo` off, promotional noise and emoji stay in the title.
    """
    result = ParsedTitle()
    title = collapse(unicodedata.normalize("NFC", raw_title or ""))
    uploader = strip_decorations(uploader)
    official = unique(official_artists or [])

    def is_known(name: str) -> bool:
        key = normalize_name(name)
        if not key:
            return False
        if uploader and key == normalize_name(uploader):
            return True
        if any(key == normalize_name(item) for item in official):
            return True
        return bool(known and known(name))

    number = _TRACK_NUMBER_RE.match(title)
    if number:
        result.track_number = int(number.group(1))
        title = title[number.end():]

    title = clean_promo(title) if strip_promo else tidy_brackets(title)
    title, producers = _extract_bracketed(title, _PROD_HEAD_RE)
    title, double_producers, featured = _extract_double_brackets(title)
    producers.extend(double_producers)
    title, hinted = _extract_hinted_producers(title, producer_hints)
    producers.extend(hinted)
    tail = _PROD_TAIL_RE.search(title)
    if tail and not _bracket_groups(tail.group(1)):
        producers.append(tail.group(1))
        title = title[:tail.start()]
    result.producers = unique(
        name for item in producers for name in split_artist_names(item, is_known)
    )

    artists_from_title: list[str] = []
    split = _split_artist_part(title)
    if split is not None:
        left, right = split
        left_names = split_artist_names(left, is_known)
        accept = True
        if official:
            # With an official artist field the title prefix is only dropped
            # when it repeats those artists.
            accept = _names_match(left_names, official)
        elif _looks_like_song_suffix(right) and not any(is_known(name) for name in left_names):
            accept = False
        if accept:
            artists_from_title = left_names
            title = right

    title, bracketed = _extract_bracketed(title, _FEAT_HEAD_RE, _FEAT_SLOT)
    featured = bracketed + featured
    feat_tail = _FEAT_TAIL_RE.search(title)
    if feat_tail:
        names = feat_tail.group(1)
        bracket = re.search(r"[\(\[\{\)\]\}]", names)
        if bracket is None:
            featured.append(names)
            title = title[:feat_tail.start()]
        elif names[:bracket.start()].strip():
            # "Mudstock feat. CODE80 (mxstrakh)": the names end where a
            # bracket starts, which stays in the title after the credit;
            # "(me ft. X)" closes its own bracket first.
            featured.append(names[:bracket.start()])
            rest = names[bracket.start():]
            close = rest[0] if rest[0] in ")]}" else ""
            title = f"{title[:feat_tail.start()]}{close} {_FEAT_SLOT} {rest[len(close):]}"
    else:
        bare = _BARE_WITH_RE.search(title)
        if bare:
            names = split_artist_names(bare.group(1), is_known, feat=True)
            if names and all(is_known(name) for name in names):
                featured.append(bare.group(1))
                title = title[:bare.start()]
    featured_names: list[str] = []
    for item in featured:
        featured_names.extend(split_artist_names(item, is_known, feat=True))

    # "35 hp + хестон + platov": guests appended with "+"/" x " to a title
    # without an artist part; trusted only when every guest is a known artist.
    if not artists_from_title and not featured_names:
        pieces = re.split(r"\s+\+\s+|\s+[xх×]\s+", title)
        if len(pieces) > 1:
            guests = [strip_decorations(piece) for piece in pieces[1:]]
            if all(is_known(guest) for guest in guests):
                title = pieces[0]
                featured_names = guests

    main = official or artists_from_title or ([uploader] if uploader else [])
    if canonical is not None:
        main = [canonical(name) for name in main]
        featured_names = [canonical(name) for name in featured_names]
    main = unique(main)
    main_keys = {normalize_name(name) for name in main}
    featured_names = [name for name in unique(featured_names) if normalize_name(name) not in main_keys]

    # The credit goes back where it was written ("heroin chic (feat. aquakey)
    # hexd"), or to the end when it was a trailing "ft. ..." part.
    feat_text = f"(feat. {', '.join(featured_names)})" if featured_names else ""
    base = _strip_edges(title.replace(_FEAT_SLOT, " "))
    if not base:
        base = _strip_edges(clean_promo(raw_title)) or collapse(raw_title)
    if feat_text and _FEAT_SLOT in title:
        full = _strip_edges(title.replace(_FEAT_SLOT, f" {feat_text} ", 1).replace(_FEAT_SLOT, " "))
    else:
        full = f"{base} {feat_text}".strip()

    result.artists = main
    result.featured = featured_names
    result.base_title = base
    result.title = full
    return result


def format_title(parsed: ParsedTitle, feat_in_title: bool = True, keep_producers: bool = False,
                 keep_versions: bool = True) -> str:
    """The title as the formatting settings want it written: with or
    without "(feat. ...)", version markers and "(prod. ...)"."""
    text = parsed.title if feat_in_title else parsed.base_title
    if not keep_versions:
        text = strip_variants(text) or text
    if keep_producers and parsed.producers:
        text = f"{text} (prod. {', '.join(parsed.producers)})"
    return text


def with_featured(base_title: str, featured: list[str]) -> str:
    """A title with its featured artists as "(feat. A, B)" at the end."""
    return f"{base_title} (feat. {', '.join(featured)})" if featured else base_title


# -- lyrics helpers ----------------------------------------------------------------
def variant_markers(title: str | None) -> frozenset[str]:
    """Version markers of a title ("sped up", "remix", ...)."""
    text = (title or "").replace("_", " ")
    return frozenset(name for name, pattern in _VARIANT_RES if pattern.search(text))


def has_no_lyrics_hint(title: str | None) -> bool:
    """DJ sets, mixes and beats: nothing to sing along to."""
    return bool(_NO_LYRICS_RE.search((title or "").replace("_", " ")))


def strip_feat(title: str | None) -> str:
    """Title without "(feat. ...)" / "ft. ..." parts."""
    text, _ = _extract_bracketed(collapse(title), _FEAT_HEAD_RE)
    tail = _FEAT_TAIL_RE.search(text)
    if tail and not _bracket_groups(tail.group(1)):
        text = text[:tail.start()]
    return _strip_edges(text)


def strip_variants(title: str | None) -> str:
    """Title without bracketed / dash-separated version parts."""
    text = collapse(title)
    spans = [
        (start, end) for start, end, inner in _bracket_groups(text)
        if text[start] != "*" and variant_markers(inner)
    ]
    text = _remove_spans(text, spans)
    parts = _DASH_SEPARATOR_RE.split(text)
    if len(parts) > 1 and variant_markers(parts[-1]):
        text = " - ".join(parts[:-1])
    return _strip_edges(text)


def title_key(title: str | None) -> str:
    """Comparison key for a song title: feat parts, promo noise and
    remaster notes do not count, version markers do."""
    text = clean_promo(strip_feat(title))
    text, _ = _extract_bracketed(text, _PROD_HEAD_RE)
    text = re.sub(r"[\(\[]?\s*-?\s*\b(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?(?:\s+version)?\s*[\)\]]?",
                  " ", text, flags=re.IGNORECASE)
    return normalize_name(text.replace("#", " "))


# -- lyrics query helpers ----------------------------------------------------------
# 「prod. X」 / 【X】: CJK brackets are read like round ones.
_CJK_BRACKET_RE = re.compile(r"[「『【〔〖]([^「『【〔〖」』】〕〗]*)[」』】〕〗]")
# Brackets that only credit people or label the upload, never part of the
# song's name: "*prod playasin*", "[生产. THORNS]", "*由主办 HARUMATSUI*",
# "(hosted by X)", "*VERY RARE*", "#UNRELEASED!".
_CREDIT_GROUP_RE = re.compile(
    r"^\s*#?\s*(?:prod(?:uced)?\b|p\.\s|生产|由主办|hosted\b|host(?:ed)? by\b|mixed by\b|"
    r"(?:very\s+)?rare\b|unreleased\b|leak(?:ed)?\b)",
    re.IGNORECASE,
)
# A token of symbols only: "###", "**", ":(", "!!!".
# "&", "+", "-" and "/" stay: they join words ("FAITH & HOPE").
_SYMBOL_TOKEN_RE = re.compile(r"(?<!\S)[^\w\s&+\-–—/]+(?!\S)")


def lyrics_clean_title(title: str | None) -> str:
    """The song's name for lyrics searches and comparisons: no "(feat. ...)",
    no producer or "hosted by" credits in any bracket style, no promo noise
    and no symbol-only tokens. Version markers stay."""
    text = strip_feat(title)
    text = _CJK_BRACKET_RE.sub(lambda match: f" ({match.group(1)}) ", text)
    text = re.sub(r"\*{2,}", "*", text)
    spans = [
        (start, end) for start, end, inner in _bracket_groups(text)
        if _CREDIT_GROUP_RE.match(inner) or not normalize_name(inner)
    ]
    text = clean_promo(_remove_spans(text, spans))
    text = _SYMBOL_TOKEN_RE.sub(" ", text)
    return _strip_edges(tidy_brackets(text)) or collapse(title)


def lyrics_bare_title(title: str | None) -> str:
    """`lyrics_clean_title` without any bracket that is not a version
    marker: "Deathtones (mxstrakh)" -> "Deathtones". Loose mode only - a
    bracket can name a different song ("Intro (Part 2)")."""
    text = lyrics_clean_title(title)
    spans = [(start, end) for start, end, inner in _bracket_groups(text) if not variant_markers(inner)]
    return _strip_edges(_remove_spans(text, spans)) or text


def split_title_artist(title: str | None) -> tuple[str, str] | None:
    """"AQUAKEY - ALONE" uploaded by another account: (artist, title), or
    None when the title has no "artist - song" form."""
    text = collapse(title)
    parts = _split_artist_part(text)
    if parts is None:
        match = re.match(r"^\s*([^-–—\s][^-–—]*?)[-–—]{1,2}\s+(\S.*)$", text)
        parts = (_strip_edges(match.group(1)), _strip_edges(match.group(2))) if match else None
    if parts is None or not parts[0] or not parts[1]:
        return None
    artist, song = parts
    if _looks_like_song_suffix(song) or variant_markers(artist) or len(artist) > 60:
        return None
    return artist, song


def looks_instrumental(title: str | None) -> bool:
    """An upload without words: a DJ set, a mix, a beat, or a title marked
    as an instrumental in a bracket or a dash part ("Song (Instrumental)",
    "Song - минус"). A bare word in the name ("Beat It", "Minus") is not
    enough."""
    text = collapse(title)
    if has_no_lyrics_hint(text):
        return True
    parts = [inner for start, end, inner in _bracket_groups(text)]
    dashed = _DASH_SEPARATOR_RE.split(text)
    if len(dashed) > 1:
        parts.append(dashed[-1])
    return any("instrumental" in variant_markers(part) for part in parts)


_CYRILLIC_TO_LATIN = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s",
    "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya", "і": "i", "ї": "yi", "є": "e", "ґ": "g",
})


def latin_key(text: str | None) -> str:
    """`normalize_name` with Cyrillic letters transliterated ("хестон" ->
    "heston") and without spaces, for fuzzy comparisons in loose mode."""
    return normalize_name(text).translate(_CYRILLIC_TO_LATIN).replace(" ", "")
