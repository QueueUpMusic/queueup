"""Helpers for reducing Spotify artist genre tags to one broad genre per song."""

import re


# Ordered from more distinctive broad genres to more general ones. A Spotify
# artist may expose many detailed tags; Genre Hopper should count only one
# broad musical family for each submitted song.
_BROAD_GENRE_PATTERNS = (
    ("children's music", (r"\bchildren(?:'s)?\b", r"\bkids?\b", r"\bnursery\b")),
    ("classical", (r"\bclassical\b", r"\bopera\b", r"\borchestra", r"\bchamber music\b")),
    ("jazz", (r"\bjazz\b", r"\bbebop\b", r"\bswing\b")),
    ("blues", (r"\bblues\b",)),
    ("reggae", (r"\breggae\b", r"\bdancehall\b", r"\bska\b")),
    ("country", (r"\bcountry\b", r"\bbluegrass\b", r"\bamericana\b")),
    ("hip hop", (r"\bhip[ -]?hop\b", r"\brap\b", r"\btrap\b")),
    ("r&b", (r"\br\s*&\s*b\b", r"\brhythm and blues\b", r"\bsoul\b", r"\bmotown\b")),
    ("electronic", (r"\belectronic\b", r"\bedm\b", r"\bhouse\b", r"\btechno\b", r"\btrance\b", r"\bdubstep\b", r"\bdrum and bass\b", r"\bambient\b")),
    ("metal", (r"\bmetal\b",)),
    ("punk", (r"\bpunk\b", r"\bhardcore\b")),
    ("rock", (r"\brock\b", r"\bgrunge\b", r"\bshoegaze\b")),
    ("folk", (r"\bfolk\b", r"\bsinger-songwriter\b")),
    ("gospel", (r"\bgospel\b", r"\bworship\b", r"\bchristian\b")),
    ("latin", (r"\blatin\b", r"\breggaeton\b", r"\bsalsa\b", r"\bbachata\b", r"\bmerengue\b")),
    ("world", (r"\bworld\b", r"\bafrobeat\b", r"\bk-pop\b", r"\bj-pop\b")),
    ("pop", (r"\bpop\b",)),
    ("soundtrack", (r"\bsoundtrack\b", r"\bscore\b", r"\bshow tunes?\b", r"\bbroadway\b")),
    ("comedy", (r"\bcomedy\b", r"\bnovelty\b")),
)


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def main_genre(genres):
    """Return one broad genre for a song from its Spotify artist genre tags.

    Spotify exposes artist-level subgenre labels rather than a true track genre.
    We inspect all available labels, collapse detailed labels into a broad family,
    and return only one result. The first matching label wins, preserving the
    source list's relevance ordering where available.
    """
    cleaned = [_clean(genre) for genre in (genres or []) if _clean(genre)]
    if not cleaned:
        return None

    for genre in cleaned:
        for broad_name, patterns in _BROAD_GENRE_PATTERNS:
            if any(re.search(pattern, genre) for pattern in patterns):
                return broad_name

    # Unknown genres still count as one genre for the song, never as multiple
    # subgenres. Keep a stable normalized label rather than discarding the song.
    return cleaned[0]
