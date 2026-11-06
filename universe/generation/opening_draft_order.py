"""Deterministic 1970 opening draft order for fg-founding-2.

The HMAC-SHA256 key is the UTF-8 decimal release seed. Each founding
franchise has one message:

    opening-draft-order:{release_slug}:{franchise_slug}

The 26 clubs sort by the full 32-byte digest, then by franchise slug.
That sequence is picks 1 through 26 in every round of the 17-round 1970
draft. Every timeline on the release uses it. Later seasons use that
timeline's own standings.

This module does not import Django, write a row, invent a 1969 result,
or read HistoricalStanding.
"""

import hashlib
import hmac
from typing import NamedTuple

from universe.generation.player_counts import draft_rounds


OPENING_SEASON_YEAR = 1970
OPENING_DRAFT_ROUNDS = draft_rounds(OPENING_SEASON_YEAR)
FOUNDING_FRANCHISE_COUNT = 26
FOUNDING_SLUGS = tuple(
    f'founding-{number:02d}' for number in range(1, FOUNDING_FRANCHISE_COUNT + 1)
)

if OPENING_DRAFT_ROUNDS != 17 or len(FOUNDING_SLUGS) != FOUNDING_FRANCHISE_COUNT:
    raise RuntimeError('The 1970 opening draft is 17 rounds across 26 clubs.')


class OpeningDraftPick(NamedTuple):
    pick: int
    franchise_slug: str
    digest_hex: str


def opening_draft_message(release_slug, franchise_slug):
    return f'opening-draft-order:{release_slug}:{franchise_slug}'


def opening_draft_digest(seed, release_slug, franchise_slug):
    """Full HMAC-SHA256 digest for one founding franchise."""
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError('The release seed must be an integer.')
    if not isinstance(release_slug, str) or not release_slug:
        raise ValueError('A release slug is required.')
    if franchise_slug not in FOUNDING_SLUGS:
        raise ValueError('The opening draft order uses the 26 founding slugs.')
    return hmac.new(
        str(seed).encode('utf-8'),
        opening_draft_message(release_slug, franchise_slug).encode('utf-8'),
        hashlib.sha256,
    ).digest()


def opening_draft_order(seed, release_slug):
    """Picks 1 through 26 for the release's 1970 opening draft."""
    ranked = sorted(
        (
            (opening_draft_digest(seed, release_slug, slug), slug)
            for slug in FOUNDING_SLUGS
        ),
        key=lambda item: (item[0], item[1]),
    )
    return tuple(
        OpeningDraftPick(
            pick=index,
            franchise_slug=slug,
            digest_hex=digest.hex(),
        )
        for index, (digest, slug) in enumerate(ranked, start=1)
    )


def opening_draft_board(seed, release_slug):
    """The same 26-club order in every round of the 1970 draft."""
    order = opening_draft_order(seed, release_slug)
    return tuple(order for _round in range(OPENING_DRAFT_ROUNDS))
