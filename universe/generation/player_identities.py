"""Pure builders for the opening cohort and the 60 draft classes.

The stored player key is the full 64-character HMAC-SHA256 hex digest. The
readable coordinate is the HMAC message. Attribute draws hang on that stored
key, the same way staff draws hang on a stored staff key. The decimal seed's
UTF-8 bytes are the HMAC key.

This module does not import Django, write a database, create a model row,
assign a draft destination, pack a draft board, rate a player, or simulate a
game. Prospect positions and draft pools are assigned. Generation blocks are
not franchises. Expansion drafts add no identities. Archive headcount is not
calculated. The reserved Joe LeCrone player is not generated.
"""

import hashlib
import hmac
from typing import NamedTuple

from universe.generation.player_counts import (
    FIRST_SEASON_YEAR,
    FOUNDING_CLUBS,
    LAST_SEASON_YEAR,
    POSITIONS,
    UNDRAFTED_MULTIPLIER,
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

DRAFT_POOL_DRAFTABLE = 'draftable'
DRAFT_POOL_UNDRAFTED = 'undrafted'

# Roster-weight eras. Each span uses one active quota plus the taxi quota.
_POSITION_ERAS = (
    (1970, 1977),
    (1978, 1986),
    (1987, 1994),
    (1995, 2029),
)

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
    """One prospect. Position, pool, body, hand, and growth are assigned.

    Draft round, pick, destination, franchise, origin draft year, and board
    rank stay empty. ``debut_year`` is the first professional year: the class
    year when the prospect is draftable, and null when undrafted.
    """

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
    position: str
    draft_pool: str
    height_in: int
    weight_lb: int
    handedness: str
    peak_age: int
    development_rate: int
    decline_rate: int
    debut_year: object
    draft_round: object = None
    draft_pick: object = None
    destination_slug: object = None
    origin_draft_year: object = None
    origin_franchise_slug: object = None
    board_rank: object = None

    @property
    def name(self):
        return f'{self.given_name} {self.surname}'

    @property
    def first_pro_year(self):
        return self.debut_year


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


def growth_draws(seed, universe_key):
    """Approved peak age and development rates for one stored player key.

    The decimal seed is the HMAC key. The message is the stored universe
    key plus the field name. Inclusive results are peak age 24–32 and both
    rates 25–75.
    """
    _require_seed(seed)
    _require_coordinate(universe_key)
    peak_age = 24 + (hmac_int(seed, f'{universe_key}:peak_age') % 9)
    development_rate = 25 + (
        hmac_int(seed, f'{universe_key}:development_rate') % 51
    )
    decline_rate = 25 + (hmac_int(seed, f'{universe_key}:decline_rate') % 51)
    return peak_age, development_rate, decline_rate


def opening_coordinate(release_slug, index):
    return f'p-{release_slug}-opening-{index:05d}'


def prospect_coordinate(release_slug, year, index):
    return f'p-{release_slug}-draft-{year}-{index:05d}'


def class_coordinate(release_slug, year):
    return f'p-{release_slug}-class-{year}'


def position_seat_coordinate(release_slug, year, original_index):
    """HMAC message for one seat in a class position multiset."""
    return (
        f'p-{release_slug}-draft-{year}-seat-{original_index:05d}:position'
    )


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


def body_band(position, season_year):
    """Inclusive height and weight for one position in one class year.

    Through 1977 the band is the stored shift, one inch and ten pounds under
    the later base. From 1978 the base is that shift plus one inch and ten
    pounds. Both edges move, so the width stays the same.
    """
    height_low, height_high, weight_low, weight_high = (
        BODY_BANDS_THROUGH_1977[position]
    )
    if season_year <= 1977:
        return height_low, height_high, weight_low, weight_high
    return (
        height_low + 1,
        height_high + 1,
        weight_low + 10,
        weight_high + 10,
    )


def _roster_weights(year):
    active = active_quota(year)
    taxi = taxi_quota(year)
    return {position: active[position] + taxi[position] for position in POSITIONS}


def _largest_remainder(total, weights):
    """Split ``total`` by ``weights``. Ties go to the earlier POSITIONS entry."""
    weight_total = sum(weights[position] for position in POSITIONS)
    seats = {
        position: (total * weights[position]) // weight_total
        for position in POSITIONS
    }
    ranked = sorted(
        (
            (
                (total * weights[position]) % weight_total,
                index,
                position,
            )
            for index, position in enumerate(POSITIONS)
        ),
        key=lambda item: (-item[0], item[1]),
    )
    for _extra in range(total - sum(seats.values())):
        seats[ranked[_extra][2]] += 1
    return seats


def order_seat_indexes(digests):
    """Seat order for a shuffle: full digest ascending, then original index.

    ``digests`` is a sequence of raw HMAC-SHA256 digests, one per original
    seat. An equal digest keeps the smaller original index first.
    """
    return tuple(sorted(
        range(len(digests)),
        key=lambda seat: (digests[seat], seat),
    ))


def _position_digest(seed, release_slug, year, original_index):
    coordinate = position_seat_coordinate(release_slug, year, original_index)
    _require_coordinate(coordinate)
    return hmac.new(
        str(seed).encode('utf-8'),
        coordinate.encode('utf-8'),
        hashlib.sha256,
    ).digest()


def year_position_counts():
    """Approved position counts for every 1970–2029 class.

    Era seats are largest remainder on the full-roster weights. Year seats
    split those era seats by class size. A leftover seat goes to the open
    cell with the largest remainder, then the earlier year, then the earlier
    position. A further leftover on the same cell ranks behind unused
    remainders.
    """
    classes = {row.season_year: row for row in draft_classes()}
    assigned = {}
    for first_year, last_year in _POSITION_ERAS:
        years = list(range(first_year, last_year + 1))
        era_size = sum(classes[year].class_size for year in years)
        era_seats = _largest_remainder(era_size, _roster_weights(first_year))
        floors = {}
        remainders = {}
        for year in years:
            class_size = classes[year].class_size
            for position in POSITIONS:
                numerator = class_size * era_seats[position]
                floors[(year, position)] = numerator // era_size
                remainders[(year, position)] = numerator % era_size
        year_need = {
            year: classes[year].class_size - sum(
                floors[(year, position)] for position in POSITIONS
            )
            for year in years
        }
        position_need = {
            position: era_seats[position] - sum(
                floors[(year, position)] for year in years
            )
            for position in POSITIONS
        }
        extras = {(year, position): 0 for year in years for position in POSITIONS}
        while sum(year_need.values()):
            chosen = None
            for year in years:
                if year_need[year] <= 0:
                    continue
                for index, position in enumerate(POSITIONS):
                    if position_need[position] <= 0:
                        continue
                    rank = (
                        remainders[(year, position)] - extras[(year, position)] * era_size,
                        -year,
                        -index,
                    )
                    if chosen is None or rank > chosen[0]:
                        chosen = (rank, year, position)
            if chosen is None:
                raise RuntimeError('The year position pack could not place a leftover seat.')
            _rank, year, position = chosen
            floors[(year, position)] += 1
            extras[(year, position)] += 1
            year_need[year] -= 1
            position_need[position] -= 1
        for year in years:
            assigned[year] = {
                position: floors[(year, position)] for position in POSITIONS
            }
            if sum(assigned[year].values()) != classes[year].class_size:
                raise RuntimeError('A class position pack missed its class size.')
    return assigned


def _shuffled_positions(seed, release_slug, year, counts):
    seats = []
    for position in POSITIONS:
        seats.extend([position] * counts[position])
    digests = tuple(
        _position_digest(seed, release_slug, year, original_index)
        for original_index in range(len(seats))
    )
    return tuple(seats[original_index] for original_index in order_seat_indexes(digests))


def _draft_pool(index, rounds):
    """Pool from the offset inside one generation block of rounds + 2.

    The first ``rounds`` offsets are draftable. The final two are undrafted.
    The block is a generation group, not a franchise.
    """
    if index % (rounds + UNDRAFTED_MULTIPLIER) < rounds:
        return DRAFT_POOL_DRAFTABLE
    return DRAFT_POOL_UNDRAFTED


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
    """Every 1970–2029 prospect, with position, pool, body, hand, and growth.

    The position multiset for a year is the approved largest-remainder count.
    It is shuffled by the full HMAC digest of the seat coordinate, with the
    decimal release seed as the key. Prospect index i receives shuffled seat
    i. The draft pool is the index offset inside a generation block and does
    not read the position. Round, pick, destination, franchise, origin draft
    year, and board rank stay empty.
    """
    _require_seed(seed)
    _require_release_slug(release_slug)
    age_low, age_high = PROSPECT_AGE_BAND
    counts = year_position_counts()
    rows = []
    for count in draft_classes():
        positions = _shuffled_positions(
            seed, release_slug, count.season_year, counts[count.season_year],
        )
        if len(positions) != count.class_size:
            raise RuntimeError('A shuffled class drifted from its class size.')
        for index, position in enumerate(positions):
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
            height_low, height_high, weight_low, weight_high = body_band(
                position, count.season_year,
            )
            peak_age, development_rate, decline_rate = growth_draws(
                seed, stored_key,
            )
            pool = _draft_pool(index, count.rounds)
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
                position=position,
                draft_pool=pool,
                height_in=_draw_span(
                    seed, stored_key, 'height_in', height_low, height_high,
                ),
                weight_lb=_draw_span(
                    seed, stored_key, 'weight_lb', weight_low, weight_high,
                ),
                handedness=_draw_handedness(seed, stored_key, position),
                peak_age=peak_age,
                development_rate=development_rate,
                decline_rate=decline_rate,
                debut_year=count.season_year if pool == DRAFT_POOL_DRAFTABLE else None,
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
