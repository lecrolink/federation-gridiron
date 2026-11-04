"""Pure builders for the opening cohort and the 60 draft classes.

The stored player key is the full 64-character HMAC-SHA256 hex digest. The
readable coordinate is the HMAC message. Attribute draws hang on that stored
key, the same way staff draws hang on a stored staff key. The decimal seed's
UTF-8 bytes are the HMAC key.

This module does not import Django, write a database, create a model row,
assign a draft destination, pack a draft board, rate a player, or simulate a
game. Expansion drafts add no identities. Archive headcount is not calculated.
The reserved Joe LeCrone player is not generated.
"""

import hashlib
import hmac
from typing import NamedTuple

from universe.generation.player_counts import (
    FIRST_SEASON_YEAR,
    FOUNDING_CLUBS,
    LAST_SEASON_YEAR,
    POSITIONS,
    active_quota,
    draft_classes,
    opening_cohort_count,
    playable_identity_count,
    taxi_quota,
)
from universe.generation.player_names import (
    COLLEGES,
    GIVEN_NAMES,
    JOE_GIVEN_INDEX,
    SURNAMES,
)

OPENING_SEASON_YEAR = FIRST_SEASON_YEAR
ROSTERED_DEBUT_YEAR = 1969
FREE_AGENT_DEBUT_YEAR = 1968

SLOT_STARTER = 'starter'
SLOT_BACKUP = 'backup'
SLOT_TAXI = 'taxi'
SLOT_FREE_AGENT = 'free_agent'

# Inclusive ages in the anchor year. Opening ages are as of 1970.
OPENING_AGE_BANDS = {
    SLOT_STARTER: (24, 30),
    SLOT_BACKUP: (22, 32),
    SLOT_TAXI: (21, 24),
    SLOT_FREE_AGENT: (27, 34),
}
PROSPECT_AGE_BAND = (21, 23)

# One of each, off every founding roster, after that club's taxi slots.
FREE_AGENT_POSITIONS = ('QB', 'WR', 'C', 'DE', 'CB')

# Inclusive height and weight through 1977, one inch and ten pounds under
# the later base bands. The whole opening cohort uses these shifted bands.
BODY_BANDS_THROUGH_1977 = {
    'QB': (71, 76, 195, 225),
    'HB': (67, 71, 175, 210),
    'FB': (70, 74, 210, 240),
    'WR': (69, 75, 165, 205),
    'TE': (74, 77, 225, 255),
    'LT': (74, 78, 255, 310),
    'LG': (74, 78, 255, 310),
    'C': (74, 78, 255, 310),
    'RG': (74, 78, 255, 310),
    'RT': (74, 78, 255, 310),
    'DE': (73, 77, 235, 275),
    'DT': (73, 77, 265, 320),
    'ILB': (71, 75, 210, 240),
    'OLB': (71, 75, 210, 240),
    'CB': (68, 72, 165, 195),
    'FS': (70, 74, 180, 210),
    'SS': (70, 74, 180, 210),
    'K': (69, 74, 165, 205),
    'P': (69, 74, 165, 205),
}

LEFT_HAND_PER_THOUSAND = 150
OTHER_LEFT_HAND_PER_THOUSAND = 80
_LEFT_HAND_POSITIONS = frozenset(('QB', 'K', 'P'))

# Last birth year in the band, then the Joe promotion threshold out of 1,000.
# 1920–1935 is intentionally absent.
JOE_THRESHOLDS = (
    (1949, 16),
    (1969, 6),
    (1989, 2),
    (2008, 0),
)
PLANNED_BIRTH_YEAR_MIN = 1936
PLANNED_BIRTH_YEAR_MAX = 2008
MODEL_BIRTH_YEAR_MIN = 1920

_OPENING_DEBUT = {
    SLOT_STARTER: ROSTERED_DEBUT_YEAR,
    SLOT_BACKUP: ROSTERED_DEBUT_YEAR,
    SLOT_TAXI: ROSTERED_DEBUT_YEAR,
    SLOT_FREE_AGENT: FREE_AGENT_DEBUT_YEAR,
}


class OpeningPlayer(NamedTuple):
    universe_key: str
    coordinate: str
    index: int
    club_slug: str
    slot_kind: str
    position: str
    depth: object
    on_roster: bool
    debut_year: int
    age: int
    birth_year: int
    birth_day: int
    given_name: str
    surname: str
    college_name: str
    college_slug: str
    handedness: str
    height_in: int
    weight_lb: int

    @property
    def name(self):
        return f'{self.given_name} {self.surname}'


class ProspectIdentity(NamedTuple):
    """One prospect identity. Position and draft result stay unassigned."""

    universe_key: str
    coordinate: str
    season_year: int
    index: int
    age: int
    birth_year: int
    birth_day: int
    given_name: str
    surname: str
    college_name: str
    college_slug: str
    position: object = None
    draft_pool: object = None
    draft_round: object = None
    draft_pick: object = None
    destination_slug: object = None
    debut_year: object = None

    @property
    def name(self):
        return f'{self.given_name} {self.surname}'


class ClassHeader(NamedTuple):
    universe_key: str
    coordinate: str
    season_year: int
    franchise_count: int
    rounds: int
    draftable_count: int
    undrafted_count: int

    @property
    def class_size(self):
        return self.draftable_count + self.undrafted_count


class PlayerUniverse(NamedTuple):
    release_slug: str
    seed: int
    opening: tuple
    prospects: tuple
    class_headers: tuple

    @property
    def playable_count(self):
        return len(self.opening) + len(self.prospects)


def _require_seed(seed):
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError('The release seed must be an integer.')


def _require_release_slug(release_slug):
    if (
        not isinstance(release_slug, str)
        or not release_slug
        or release_slug.strip() != release_slug
        or any(character.isspace() for character in release_slug)
        or ':' in release_slug
    ):
        raise ValueError('A release slug is required.')


def _require_coordinate(coordinate):
    if not isinstance(coordinate, str) or not coordinate:
        raise ValueError('A player coordinate is required.')


def hmac_int(seed, coordinate):
    """First eight HMAC-SHA256 bytes, as a big-endian integer."""
    _require_seed(seed)
    _require_coordinate(coordinate)
    digest = hmac.new(
        str(seed).encode('utf-8'),
        coordinate.encode('utf-8'),
        hashlib.sha256,
    ).digest()
    return int.from_bytes(digest[:8], 'big')


def player_key(seed, coordinate):
    """Full 64-character HMAC-SHA256 hex digest of the readable coordinate."""
    _require_seed(seed)
    _require_coordinate(coordinate)
    return hmac.new(
        str(seed).encode('utf-8'),
        coordinate.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def opening_coordinate(release_slug, index):
    return f'p-{release_slug}-opening-{index:05d}'


def prospect_coordinate(release_slug, year, index):
    return f'p-{release_slug}-draft-{year}-{index:05d}'


def class_coordinate(release_slug, year):
    return f'p-{release_slug}-class-{year}'


def founding_club_slugs():
    """The 26 founding clubs, in schedule order. This does not import the schedule."""
    return tuple(
        f'founding-{number:02d}' for number in range(1, FOUNDING_CLUBS + 1)
    )


def joe_threshold(birth_year):
    """Tickets below this value promote a non-Joe draw to Joe.

    Birth years 1920 through 1935 remain outside the planned population.
    This function does not invent a threshold for them.
    """
    if isinstance(birth_year, bool) or not isinstance(birth_year, int):
        raise ValueError('Birth year must be an integer.')
    if MODEL_BIRTH_YEAR_MIN <= birth_year < PLANNED_BIRTH_YEAR_MIN:
        raise ValueError(
            'Birth years 1920 through 1935 are outside the planned '
            'population and have no approved Joe threshold.'
        )
    if birth_year < MODEL_BIRTH_YEAR_MIN or birth_year > PLANNED_BIRTH_YEAR_MAX:
        raise ValueError('Birth year is outside 1920 through 2008.')
    for last_year, threshold in JOE_THRESHOLDS:
        if birth_year <= last_year:
            return threshold
    raise RuntimeError('The Joe threshold table does not cover this birth year.')


def resolve_given_name(seed, stored_key, birth_year):
    """Uniform given-name draw, then the birth-year Joe promotion ticket.

    Index 140 stays Joe. Any other index becomes Joe when the ``:given_joe``
    ticket is strictly below the threshold for that birth year.
    """
    threshold = joe_threshold(birth_year)
    index = hmac_int(seed, f'{stored_key}:given_name') % len(GIVEN_NAMES)
    if index == JOE_GIVEN_INDEX:
        return GIVEN_NAMES[index]
    ticket = hmac_int(seed, f'{stored_key}:given_joe') % 1000
    if ticket < threshold:
        return 'Joe'
    return GIVEN_NAMES[index]


def _draw_span(seed, stored_key, field, low, high):
    width = high - low + 1
    return low + (hmac_int(seed, f'{stored_key}:{field}') % width)


def _draw_age(seed, stored_key, low, high):
    """Age from the ``:birth_year`` coordinate. The anchor year converts it."""
    return _draw_span(seed, stored_key, 'birth_year', low, high)


def _draw_birth_day(seed, stored_key):
    return 1 + (hmac_int(seed, f'{stored_key}:birth_day') % 365)


def _draw_surname(seed, stored_key):
    return SURNAMES[hmac_int(seed, f'{stored_key}:surname') % len(SURNAMES)]


def _draw_college(seed, stored_key):
    name, slug, _kind = COLLEGES[
        hmac_int(seed, f'{stored_key}:college') % len(COLLEGES)
    ]
    return name, slug


def _draw_handedness(seed, stored_key, position):
    if position in _LEFT_HAND_POSITIONS:
        rate = LEFT_HAND_PER_THOUSAND
    else:
        rate = OTHER_LEFT_HAND_PER_THOUSAND
    ticket = hmac_int(seed, f'{stored_key}:handedness') % 1000
    if ticket < rate:
        return 'left'
    return 'right'


def _planned_birth_year(anchor_year, age):
    birth_year = anchor_year - age
    if birth_year < PLANNED_BIRTH_YEAR_MIN or birth_year > PLANNED_BIRTH_YEAR_MAX:
        raise RuntimeError('Planned players are born from 1936 through 2008.')
    return birth_year


def _reject_reserved_player(given_name, surname):
    if given_name == 'Joe' and surname == 'LeCrone':
        raise RuntimeError('Joe LeCrone is reserved and is not generated.')


def _opening_slots():
    """Active positions, then taxi, then five free agents, for each club."""
    active = active_quota(OPENING_SEASON_YEAR)
    taxi = taxi_quota(OPENING_SEASON_YEAR)
    slots = []
    for club_slug in founding_club_slugs():
        for position in POSITIONS:
            for depth in range(1, active[position] + 1):
                kind = SLOT_STARTER if depth == 1 else SLOT_BACKUP
                slots.append((club_slug, kind, position, depth, True))
        for position in POSITIONS:
            for depth in range(1, taxi[position] + 1):
                slots.append((club_slug, SLOT_TAXI, position, depth, True))
        for position in FREE_AGENT_POSITIONS:
            slots.append((club_slug, SLOT_FREE_AGENT, position, None, False))
    return tuple(slots)


def build_opening_cohort(seed, release_slug):
    """The 1,378 opening players. The 1970 rookie class is not included."""
    _require_seed(seed)
    _require_release_slug(release_slug)
    rows = []
    for index, slot in enumerate(_opening_slots()):
        club_slug, kind, position, depth, on_roster = slot
        coordinate = opening_coordinate(release_slug, index)
        stored_key = player_key(seed, coordinate)
        age_low, age_high = OPENING_AGE_BANDS[kind]
        age = _draw_age(seed, stored_key, age_low, age_high)
        birth_year = _planned_birth_year(OPENING_SEASON_YEAR, age)
        given_name = resolve_given_name(seed, stored_key, birth_year)
        surname = _draw_surname(seed, stored_key)
        _reject_reserved_player(given_name, surname)
        college_name, college_slug = _draw_college(seed, stored_key)
        height_low, height_high, weight_low, weight_high = (
            BODY_BANDS_THROUGH_1977[position]
        )
        rows.append(OpeningPlayer(
            universe_key=stored_key,
            coordinate=coordinate,
            index=index,
            club_slug=club_slug,
            slot_kind=kind,
            position=position,
            depth=depth,
            on_roster=on_roster,
            debut_year=_OPENING_DEBUT[kind],
            age=age,
            birth_year=birth_year,
            birth_day=_draw_birth_day(seed, stored_key),
            given_name=given_name,
            surname=surname,
            college_name=college_name,
            college_slug=college_slug,
            handedness=_draw_handedness(seed, stored_key, position),
            height_in=_draw_span(
                seed, stored_key, 'height_in', height_low, height_high,
            ),
            weight_lb=_draw_span(
                seed, stored_key, 'weight_lb', weight_low, weight_high,
            ),
        ))
    if len(rows) != opening_cohort_count():
        raise RuntimeError('The opening cohort drifted from 1,378.')
    return tuple(rows)


def build_class_headers(seed, release_slug):
    """One header per season. Counts come from player_counts.draft_classes."""
    _require_seed(seed)
    _require_release_slug(release_slug)
    rows = []
    for count in draft_classes():
        coordinate = class_coordinate(release_slug, count.season_year)
        rows.append(ClassHeader(
            universe_key=player_key(seed, coordinate),
            coordinate=coordinate,
            season_year=count.season_year,
            franchise_count=count.franchise_count,
            rounds=count.rounds,
            draftable_count=count.draftable_count,
            undrafted_count=count.undrafted_count,
        ))
    if len(rows) != (LAST_SEASON_YEAR - FIRST_SEASON_YEAR + 1):
        raise RuntimeError('The draft calendar is 60 class headers.')
    return tuple(rows)


def build_prospects(seed, release_slug):
    """Identity rows for every 1970–2029 prospect.

    Year-by-year club bundles and draft position packing are still
    undecided, so position, draft pool, round, pick, destination, and
    debut year stay empty. Body and handedness wait on position.
    """
    _require_seed(seed)
    _require_release_slug(release_slug)
    age_low, age_high = PROSPECT_AGE_BAND
    rows = []
    for count in draft_classes():
        for index in range(count.class_size):
            coordinate = prospect_coordinate(
                release_slug, count.season_year, index,
            )
            stored_key = player_key(seed, coordinate)
            age = _draw_age(seed, stored_key, age_low, age_high)
            birth_year = _planned_birth_year(count.season_year, age)
            given_name = resolve_given_name(seed, stored_key, birth_year)
            surname = _draw_surname(seed, stored_key)
            _reject_reserved_player(given_name, surname)
            college_name, college_slug = _draw_college(seed, stored_key)
            rows.append(ProspectIdentity(
                universe_key=stored_key,
                coordinate=coordinate,
                season_year=count.season_year,
                index=index,
                age=age,
                birth_year=birth_year,
                birth_day=_draw_birth_day(seed, stored_key),
                given_name=given_name,
                surname=surname,
                college_name=college_name,
                college_slug=college_slug,
            ))
    return tuple(rows)


def build_player_universe(seed, release_slug):
    """Opening players, prospect identities, and the 60 class headers."""
    opening = build_opening_cohort(seed, release_slug)
    prospects = build_prospects(seed, release_slug)
    class_headers = build_class_headers(seed, release_slug)
    if len(opening) + len(prospects) != playable_identity_count():
        raise RuntimeError('The playable identity total drifted from 21,155.')
    keys = [row.universe_key for row in opening]
    keys.extend(row.universe_key for row in prospects)
    keys.extend(row.universe_key for row in class_headers)
    if len(keys) != len(set(keys)):
        raise RuntimeError('Player keys must be unique.')
    return PlayerUniverse(
        release_slug=release_slug,
        seed=seed,
        opening=opening,
        prospects=prospects,
        class_headers=class_headers,
    )
