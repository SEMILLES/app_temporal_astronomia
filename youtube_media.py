"""Strict, network-free parsing for canonical YouTube video references."""

import re
from urllib.parse import parse_qs, urlparse

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
YOUTUBE_HOSTS = frozenset(("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"))


class InvalidYouTubeURL(ValueError):
    pass


def parse_youtube_url(value):
    raw = (value or "").strip()
    try:
        parsed = urlparse(raw)
    except ValueError as error:
        raise InvalidYouTubeURL("La URL de YouTube no es válida.") from error
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
        raise InvalidYouTubeURL("La URL debe usar HTTPS y pertenecer a YouTube.")
    host = (parsed.hostname or "").lower().rstrip(".")
    parts = [part for part in parsed.path.split("/") if part]
    video_id = None
    if host == "youtu.be" and len(parts) == 1:
        video_id = parts[0]
    elif host in YOUTUBE_HOSTS:
        if parsed.path.rstrip("/") == "/watch":
            values = parse_qs(parsed.query).get("v", [])
            video_id = values[0] if len(values) == 1 else None
        elif len(parts) == 2 and parts[0] in ("shorts", "embed", "live"):
            video_id = parts[1]
    if not video_id or not VIDEO_ID.fullmatch(video_id):
        raise InvalidYouTubeURL("La URL no contiene un identificador de video de YouTube válido.")
    return video_id


def normalize_youtube_url(value):
    return youtube_watch_url(parse_youtube_url(value))


def youtube_watch_url(video_id):
    if not VIDEO_ID.fullmatch(video_id or ""):
        raise InvalidYouTubeURL("Identificador de video de YouTube no válido.")
    return f"https://www.youtube.com/watch?v={video_id}"


def youtube_embed_url(video_id):
    if not VIDEO_ID.fullmatch(video_id or ""):
        raise InvalidYouTubeURL("Identificador de video de YouTube no válido.")
    return f"https://www.youtube.com/embed/{video_id}"
