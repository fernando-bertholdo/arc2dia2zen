"""URL helpers: scheme checks and normalization used for de-duplication and comparison."""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit, urlunsplit

WEB_SCHEMES = ("http", "https")
_DEFAULT_PORTS = {"http": "80", "https": "443"}


def scheme_of(url: Optional[str]) -> str:
    if not url:
        return ""
    head, sep, _ = url.partition(":")
    if not sep:
        return ""
    return head.strip().lower()


def is_web(url: Optional[str]) -> bool:
    """True for http(s) URLs, the only kind Dia keeps when it imports from Arc."""
    return scheme_of(url) in WEB_SCHEMES


def normalize(url: Optional[str]) -> str:
    """Normalize a URL for duplicate detection.

    Lowercases scheme and host, drops default ports, turns an empty path into "/" and
    drops an empty trailing "#". Query strings and non-empty fragments are kept, since
    many web apps use them to address different pages. Non-web URLs are returned as is.
    """
    if not url:
        return ""
    url = url.strip()
    if not is_web(url):
        return url
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    scheme = parts.scheme.lower()
    netloc = parts.netloc
    userinfo, at, hostport = netloc.rpartition("@")
    host, colon, port = hostport.partition(":")
    if hostport.startswith("["):  # IPv6 literal
        host, colon, port = hostport, "", ""
        end = hostport.find("]")
        if end != -1 and hostport[end + 1:end + 2] == ":":
            host, colon, port = hostport[: end + 1], ":", hostport[end + 2:]
    host = host.lower()
    if colon and port == _DEFAULT_PORTS.get(scheme):
        colon, port = "", ""
    netloc = (userinfo + at if at else "") + host + colon + port
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, parts.fragment))


def loose_key(url: Optional[str]) -> str:
    """A looser comparison key: ignores the fragment and a trailing slash in the path."""
    norm = normalize(url)
    if not is_web(norm):
        return norm
    try:
        parts = urlsplit(norm)
    except ValueError:
        return norm
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))
