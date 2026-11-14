"""Rich-text sanitisation.

A note body is *HTML* produced by the WYSIWYG editor. HTML supplied by a client
is never trusted: :func:`sanitize_html` runs it through a strict allow-list
before it is ever stored or returned, which makes "the content contains no
script/iframe/event-handler/unsafe-URL markup" a **domain invariant** rather
than a presentation-layer nicety.

Sanitisation is deliberately applied in the domain (not the HTTP layer) so that
*every* write path — REST today, an importer tomorrow — gets the same guarantee,
and so that content read back from storage is re-cleaned before it is served
(defence in depth).

The allow-list is exactly the vocabulary the editor toolbar can produce:
paragraphs, headings, emphasis, lists, quotes, horizontal rules and links.
Anything else is stripped while its text is kept.
"""

from __future__ import annotations

import nh3

# Tags the toolbar can produce. Keep this in sync with the frontend editor.
ALLOWED_TAGS: set[str] = {
    "p",
    "br",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "s",
    "h1",
    "h2",
    "h3",
    "h4",
    "ul",
    "ol",
    "li",
    "blockquote",
    "hr",
    "a",
    "code",
    "pre",
}

# Only links carry attributes, and only the ones we are willing to honour.
ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "a": {"href", "title"},
}

# No ``javascript:``/``data:`` URLs, ever.
ALLOWED_URL_SCHEMES: set[str] = {"http", "https", "mailto"}


def sanitize_html(value: str) -> str:
    """Return ``value`` with every disallowed tag/attribute/URL removed.

    ``nh3`` (Ammonia/Rust) is used because it parses the input as a real HTML
    document; a regex-based approach is not safe against malformed markup.
    """
    return nh3.clean(
        value,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer",
    )


__all__ = [
    "ALLOWED_ATTRIBUTES",
    "ALLOWED_TAGS",
    "ALLOWED_URL_SCHEMES",
    "sanitize_html",
]
