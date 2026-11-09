"""1978–1989 opponent pairings for one fg-founding-2 draft.

This is a project schedule for the stored 1977–1994 seats. Each club plays
every other club in its conference once, plus three clubs in the other
conference. It is not an NFL opponent chart and it does not extend the
1970–1977 matrix. Division games are single games.

The ``y1978`` and ``y1987`` calendar games fields stay unresolved. This
writer does not edit those cards. It reads the stored ``schedule_length``
and accepts only 16, which is the length this rule uses.

An empty 1978–1989 span is written in one transaction. An exact rerun
returns ``unchanged`` and writes nothing. A partial, extra, or mismatched
set in that span fails closed and is left as it was. 1970–1977 pairings
and their week assignments are not read or replaced. This writer does not
assign weeks, byes, kickoffs, scores, standings, or simulation.
"""

from collections import Counter, defaultdict

from django.db import DEFAULT_DB_ALIAS, IntegrityError

from timelines.models import Timeline
from universe.generation.founding import (
    FoundingGenerationError,
    _resolve_release_id,
    _validate_seed,
)
from universe.generation.opponents import (
    CENTRAL,
    CONFERENCE_INDEX,
    EAST,
    KIND_DIVISION,
    KIND_INTRA,
    ROLE_BY_DIVISION,
    WEST,
    OpponentGenerationError,
    Pairing,
    Seat,
    repair_homes,
)
from universe.generation.schedule import GENERATOR_VERSION
from universe.models import FranchiseSeasonMembership, OpponentPairing, UniverseRelease
from universe.release_guard import assert_release_lock_held, database_alias, holding_release_lock
from universe.rules import EraRuleProfile


FIRST_SEASON = 1978
LAST_SEASON = 1989
REQUIRED_SCHEDULE_LENGTH = 16
SEASON_GAMES = 224
CLUBS = 28
POSITIONS = 14
CROSS_GAMES = 3
ROLE_ORDER = (EAST, CENTRAL, WEST)
ROLE_RANK = {EAST: 0, CENTRAL: 1, WEST: 2}
# East, Central, West. Conference-1 is 5/4/5. Conference-2 is 5/5/4.
DIVISION_SIZES = {
    1: {EAST: 5, CENTRAL: 4, WEST: 5},
    2: {EAST: 5, CENTRAL: 5, WEST: 4},
}
KIND_INTER = OpponentPairing.Kind.INTERCONFERENCE
DIVISION_GAMES = 52
INTRA_GAMES = 130
INTER_GAMES = 42

_DIFFER = (
    'Stored 1978–1989 opponent pairings differ from the project rule. '
    'The writer will not delete or replace them.'
)


def cross_offsets(season_year):
    """Return ``t``, ``t+2``, and ``t+4`` modulo 14.

    ``t`` is zero in 1978 and advances by one each season.
    """
    if not FIRST_SEASON <= season_year <= LAST_SEASON:
        raise OpponentGenerationError('The cross-conference rotation covers 1978–1989.')
    step = season_year - FIRST_SEASON
    return tuple((step + delta) % POSITIONS for delta in (0, 2, 4))


def generate_opponent_pairings_1978(league_id, seed):
    """Write 1978–1989 pairings. Return ``created`` or ``unchanged``."""
    try:
        _validate_seed(seed)
        release_id = _resolve_release_id(league_id)
    except FoundingGenerationError as exc:
        raise OpponentGenerationError(str(exc)) from exc
    try:
        with holding_release_lock(release_id, using=DEFAULT_DB_ALIAS) as release:
            metadata = _release_metadata(release)
            _refuse_closed(release)
            if release.generator_version != GENERATOR_VERSION:
                raise OpponentGenerationError(
                    'The draft release must already have its fg-founding-2 schedule.'
                )
            if release.seed != seed:
                raise OpponentGenerationError(
                    'The opponent writer will not change the release seed.'
                )
            expected = _expected_span(release)
            state = _stored_state(release, expected)
            if state == 'match':
                return 'unchanged'
            if state == 'differ':
                raise OpponentGenerationError(_DIFFER)
            _write_span(release, expected)
            if _stored_state(release, expected) != 'match':
                raise OpponentGenerationError(
                    'The 1978–1989 opponent pairings did not match the project rule.'
                )
            release.refresh_from_db()
            if _release_metadata(release) != metadata:
                raise OpponentGenerationError(
                    'The opponent writer changed the release metadata.'
                )
            return 'created'
    except IntegrityError as exc:
        raise OpponentGenerationError(
            'An opponent pairing insert violated a database constraint. '
            'The writer rolled back the season inserts.'
        ) from exc
    except (RuntimeError, ValueError) as exc:
        raise OpponentGenerationError(str(exc)) from exc


def build_season(season_year, seats, schedule_length):
    """Return one season's pairings from in-memory seats.

    ``schedule_length`` is the stored era value. This rule accepts only 16.
    Accepting 16 does not approve the ``y1978`` or ``y1987`` calendar field.
    """
    if schedule_length != REQUIRED_SCHEDULE_LENGTH:
        raise OpponentGenerationError(
            f'{season_year} stores schedule_length {schedule_length}, not 16.'
        )
    if not FIRST_SEASON <= season_year <= LAST_SEASON:
        raise OpponentGenerationError('This opponent rule covers 1978–1989 only.')
    ordered, franchise_at = _group_seats(season_year, seats)
    raw = _raw_games(season_year, ordered)
    homes = repair_homes(
        [(left, right) for _kind, left, right in raw],
        {node: REQUIRED_SCHEDULE_LENGTH // 2 for _kind, left, right in raw for node in (left, right)},
        pinned=set(),
    )
    pairings = []
    for (kind, left, right), home in zip(raw, homes):
        if home == left:
            away = right
        elif home == right:
            away = left
        else:
            raise OpponentGenerationError(f'{season_year} home repair returned an unknown club.')
        pairings.append(Pairing(
            season_year,
            franchise_at[home],
            franchise_at[away],
            kind,
            home,
            away,
        ))
    pairings = tuple(pairings)
    _validate_season(season_year, pairings, ordered, franchise_at)
    return pairings


def _raw_games(season_year, ordered):
    """Undirected games. Every game is single and none is pinned.

    Conference games come first, conference 1 then conference 2, each pair
    in position order. Cross-conference games then follow position order,
    and each position's three opponents follow ``t``, ``t+2``, ``t+4``.
    """
    games = []
    for conference in (1, 2):
        nodes = ordered[conference]
        for left_at, left in enumerate(nodes):
            for right in nodes[left_at + 1:]:
                kind = KIND_DIVISION if left[1] == right[1] else KIND_INTRA
                games.append((kind, left, right))
    offsets = cross_offsets(season_year)
    conference_1 = ordered[1]
    conference_2 = ordered[2]
    for index, left in enumerate(conference_1):
        for offset in offsets:
            games.append((KIND_INTER, left, conference_2[(index + offset) % POSITIONS]))
    return games


def _group_seats(season_year, seats):
    by_conference = {1: [], 2: []}
    seen = set()
    for seat in seats:
        if seat.conference not in by_conference:
            raise OpponentGenerationError(f'{season_year} has an unknown conference.')
        if seat.franchise_id in seen:
            raise OpponentGenerationError(f'{season_year} repeats a franchise.')
        seen.add(seat.franchise_id)
        by_conference[seat.conference].append(seat)
    ordered = {}
    franchise_at = {}
    for conference, members in by_conference.items():
        buckets = {role: {} for role in ROLE_ORDER}
        for seat in members:
            if seat.role not in buckets:
                raise OpponentGenerationError(f'{season_year} has an unknown division role.')
            if seat.slot in buckets[seat.role]:
                raise OpponentGenerationError(
                    f'{season_year} repeats a slot in conference {conference}.'
                )
            buckets[seat.role][seat.slot] = seat
        expected = DIVISION_SIZES[conference]
        sequence = []
        for role in ROLE_ORDER:
            slots = set(buckets[role])
            if slots != set(range(1, expected[role] + 1)):
                raise OpponentGenerationError(
                    f'{season_year} conference {conference} {role} seats do not match the rule.'
                )
            for slot in range(1, expected[role] + 1):
                seat = buckets[role][slot]
                node = (conference, ROLE_RANK[role], slot)
                sequence.append(node)
                franchise_at[node] = seat.franchise_id
        ordered[conference] = tuple(sequence)
    if len(franchise_at) != CLUBS:
        raise OpponentGenerationError(f'{season_year} does not have 28 clubs.')
    return ordered, franchise_at


def _validate_season(season_year, pairings, ordered, franchise_at):
    if len(pairings) != SEASON_GAMES:
        raise OpponentGenerationError(
            f'{season_year} has {len(pairings)} games, not {SEASON_GAMES}.'
        )
    counts = Counter(pairing.kind for pairing in pairings)
    if (
        counts[KIND_DIVISION] != DIVISION_GAMES
        or counts[KIND_INTRA] != INTRA_GAMES
        or counts[KIND_INTER] != INTER_GAMES
    ):
        raise OpponentGenerationError(f'{season_year} game categories do not match the rule.')
    index_of = {
        node: index
        for nodes in ordered.values()
        for index, node in enumerate(nodes)
    }
    directed = set()
    meetings = defaultdict(list)
    games = Counter()
    homes = Counter()
    club_kinds = defaultdict(Counter)
    cross_pairs = []
    for pairing in pairings:
        key = (pairing.home_franchise_id, pairing.away_franchise_id)
        if pairing.home_franchise_id == pairing.away_franchise_id or key in directed:
            raise OpponentGenerationError(f'{season_year} repeats a directed pairing.')
        directed.add(key)
        meetings[frozenset(key)].append(pairing)
        for franchise_id, is_home in (
            (pairing.home_franchise_id, True),
            (pairing.away_franchise_id, False),
        ):
            games[franchise_id] += 1
            club_kinds[franchise_id][pairing.kind] += 1
            if is_home:
                homes[franchise_id] += 1
        if pairing.kind == KIND_INTER:
            endpoints = (pairing.home_node, pairing.away_node)
            if endpoints[0][0] == endpoints[1][0]:
                raise OpponentGenerationError(
                    f'{season_year} interconference game stays inside one conference.'
                )
            left = next(node for node in endpoints if node[0] == 1)
            right = next(node for node in endpoints if node[0] == 2)
            cross_pairs.append((index_of[left], index_of[right]))
        elif pairing.home_node[0] != pairing.away_node[0]:
            raise OpponentGenerationError(
                f'{season_year} conference game crosses conferences.'
            )
    if len(meetings) != SEASON_GAMES or any(len(rows) != 1 for rows in meetings.values()):
        raise OpponentGenerationError(f'{season_year} repeats an opponent.')
    franchises = set(games)
    if len(franchises) != CLUBS or any(games[franchise_id] != REQUIRED_SCHEDULE_LENGTH for franchise_id in franchises):
        raise OpponentGenerationError(f'{season_year} does not give every club 16 games.')
    if any(homes[franchise_id] != REQUIRED_SCHEDULE_LENGTH // 2 for franchise_id in franchises):
        raise OpponentGenerationError(f'{season_year} does not finish every club 8–8.')
    offsets = set(cross_offsets(season_year))
    expected_cross = {
        (index, (index + offset) % POSITIONS)
        for index in range(POSITIONS)
        for offset in offsets
    }
    if len(cross_pairs) != len(set(cross_pairs)) or set(cross_pairs) != expected_cross:
        raise OpponentGenerationError(f'{season_year} cross-conference opponents drifted.')
    for nodes in ordered.values():
        for node in nodes:
            size = DIVISION_SIZES[node[0]][ROLE_ORDER[node[1]]]
            kinds = club_kinds[franchise_at[node]]
            expected = {
                KIND_DIVISION: size - 1,
                KIND_INTRA: (POSITIONS - 1) - (size - 1),
                KIND_INTER: CROSS_GAMES,
            }
            actual = {kind: kinds[kind] for kind in expected}
            if actual != expected:
                raise OpponentGenerationError(
                    f'{season_year} club at {node} has the wrong game counts.'
                )


def _validate_rotation(expected):
    meetings = Counter()
    for pairings in expected.values():
        for pairing in pairings:
            if pairing.kind != KIND_INTER:
                continue
            endpoints = (pairing.home_node, pairing.away_node)
            left = next(node for node in endpoints if node[0] == 1)
            right = next(node for node in endpoints if node[0] == 2)
            meetings[(_position(left), _position(right))] += 1
    if set(meetings) != {(left, right) for left in range(POSITIONS) for right in range(POSITIONS)}:
        raise OpponentGenerationError('A cross-conference pair is missing across 1978–1989.')
    histogram = Counter(meetings.values())
    if histogram != Counter({2: 84, 3: 112}):
        raise OpponentGenerationError(
            'The cross-conference rotation is not 84 pairs twice and 112 pairs three times.'
        )


def _position(node):
    """East slots, then Central slots, then West slots, each starting at 1."""
    _conference, rank, slot = node
    before = 0
    for role in ROLE_ORDER:
        role_rank = ROLE_RANK[role]
        if role_rank == rank:
            return before + slot - 1
        # Both conferences number East first. The position of a later role
        # depends on that conference's earlier division sizes.
        before += DIVISION_SIZES[node[0]][role]
    raise OpponentGenerationError('A seat has no position.')


def _expected_span(release):
    expected = {}
    for season_year in range(FIRST_SEASON, LAST_SEASON + 1):
        length = _schedule_length(release, season_year)
        seats = _seats_for(release, season_year)
        expected[season_year] = build_season(season_year, seats, length)
    _validate_rotation(expected)
    return expected


def _schedule_length(release, season_year):
    profiles = list(
        EraRuleProfile.objects.filter(
            release_id=release.pk,
            first_year__lte=season_year,
            last_year__gte=season_year,
        )
    )
    if len(profiles) != 1:
        raise OpponentGenerationError(
            f'{season_year} needs exactly one stored era profile.'
        )
    return profiles[0].schedule_length


def _seats_for(release, season_year):
    rows = list(
        FranchiseSeasonMembership.objects.filter(
            franchise__release_id=release.pk,
            season_year=season_year,
        ).select_related('franchise')
    )
    seats = []
    for row in rows:
        role = ROLE_BY_DIVISION.get(row.division_slug)
        conference = CONFERENCE_INDEX.get(row.conference_slug)
        if role is None or conference is None:
            raise OpponentGenerationError(
                f'{season_year} has a membership outside the approved roles.'
            )
        if row.franchise.release_id != release.pk:
            raise OpponentGenerationError('A membership belongs to another release.')
        seats.append(Seat(row.franchise_id, conference, role, row.slot_order))
    return tuple(seats)


def _stored_state(release, expected):
    stored = list(
        OpponentPairing.objects.filter(
            release_id=release.pk,
            season_year__gte=FIRST_SEASON,
            season_year__lte=LAST_SEASON,
        ).values_list(
            'season_year',
            'home_franchise_id',
            'away_franchise_id',
            'kind',
        )
    )
    if not stored:
        return 'absent'
    expected_rows = {
        (pairing.season_year, pairing.home_franchise_id, pairing.away_franchise_id, pairing.kind)
        for pairings in expected.values()
        for pairing in pairings
    }
    if len(stored) != len(expected_rows) or set(stored) != expected_rows:
        return 'differ'
    seasons = {row[0] for row in stored}
    if seasons != set(expected):
        return 'differ'
    return 'match'


def _write_span(release, expected):
    alias = database_alias(release)
    assert_release_lock_held(release.pk, using=alias)
    for season_year in range(FIRST_SEASON, LAST_SEASON + 1):
        rows = [
            OpponentPairing(
                release=release,
                season_year=season_year,
                home_franchise_id=pairing.home_franchise_id,
                away_franchise_id=pairing.away_franchise_id,
                kind=pairing.kind,
            )
            for pairing in expected[season_year]
        ]
        OpponentPairing.objects.bulk_create(rows, batch_size=500)


def _release_metadata(release):
    return (
        release.generator_version,
        release.seed,
        release.status,
        release.catalog_fingerprint,
        release.validation_fingerprint,
        release.expansion_schedule_version,
        release.published_at,
        release.founding_franchise_count,
        release.first_season_year,
        release.season_count,
        release.last_season_year,
        release.slug,
    )


def _refuse_closed(release):
    if release.status != UniverseRelease.Status.DRAFT or release.published_at is not None:
        raise OpponentGenerationError('A published release cannot be changed.')
    if Timeline.objects.filter(release_id=release.pk).exists():
        raise OpponentGenerationError('A timeline points at this release.')
