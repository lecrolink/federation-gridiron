"""1970–1977 opponent pairings for one fg-founding-2 draft.

This is a project approximation. It reads the corrected franchise-season
memberships, the East/Central/West roles on ``d3``/``d1``/``d2``, slot
order, the season, and the stored ``schedule_length``. It does not
transcribe the NFL's named-club opponent charts. Week assignment is a
separate writer and does not change these rows. This writer does not
assign byes, kickoffs, scores, or later-era schedules.

The 13-by-13 matrix counts games between non-swing clubs only. In 1976 and
1977 that matrix has 27 games. The meeting of the two expansion swing clubs
is outside the matrix, so those seasons have 28 interconference games.

An empty 1970–1977 span is written in one transaction. An exact rerun
returns ``unchanged`` and writes nothing. A partial, extra, or mismatched
stored set fails closed and is left as it was. A persistence error rolls
the transaction back.
"""

from collections import Counter, defaultdict
from typing import NamedTuple

from django.db import DEFAULT_DB_ALIAS, IntegrityError

from timelines.models import Timeline
from universe.generation.founding import (
    FoundingGenerationError,
    _resolve_release_id,
    _validate_seed,
)
from universe.generation.schedule import GENERATOR_VERSION
from universe.models import (
    FranchiseSeasonMembership,
    OpponentPairing,
    UniverseRelease,
)
from universe.release_guard import assert_release_lock_held, database_alias, holding_release_lock
from universe.rules import EraRuleProfile


FIRST_SEASON = 1970
LAST_SEASON = 1977
REQUIRED_SCHEDULE_LENGTH = 14
EARLY_SEASON_GAMES = 182
SWING_SEASON_GAMES = 196
MATRIX_EARLY = 40
MATRIX_SWING = 27
SWING_MEETINGS = 1
NON_SWING_POSITIONS = 13
SWING_INDEX = 13

EAST = 'east'
CENTRAL = 'central'
WEST = 'west'
ROLE_BY_DIVISION = {
    'd3': EAST,
    'd1': CENTRAL,
    'd2': WEST,
}
CONFERENCE_INDEX = {
    'conference-1': 1,
    'conference-2': 2,
}

# Places after the extra-game seat, East slot numbers 1–5.
EAST_TEMPLATE = {
    0: (1,),
    1: (1, 2),
    2: (1, 2),
    3: (2, 3),
    4: (3, 4),
    5: (3, 5),
    6: (4, 5),
    7: (4, 5),
}

KIND_DIVISION = OpponentPairing.Kind.DIVISION
KIND_INTRA = OpponentPairing.Kind.INTRACONFERENCE
KIND_MATRIX = OpponentPairing.Kind.MATRIX_INTERCONFERENCE
KIND_SWING_CONFERENCE = OpponentPairing.Kind.SWING_CONFERENCE
KIND_SWING_MEETING = OpponentPairing.Kind.SWING_MEETING

_DIFFER = (
    'Stored opponent pairings differ from the project rule. '
    'The writer will not delete or replace them.'
)


class OpponentGenerationError(Exception):
    """The 1970–1977 opponent pairings cannot be written."""


class Seat(NamedTuple):
    franchise_id: int
    conference: int
    role: str
    slot: int


class Pairing(NamedTuple):
    season_year: int
    home_franchise_id: int
    away_franchise_id: int
    kind: str
    home_node: tuple
    away_node: tuple


def position_for(role, slot):
    """Map a non-swing seat to positions 0–12.

    East slots 1–5 are 0–4, Central slots 1–4 are 5–8, and West slots 1–4
    are 9–12. Slot 5 is a swing club and has no position in that list.
    """
    if role == EAST and 1 <= slot <= 5:
        return slot - 1
    if role == CENTRAL and 1 <= slot <= 4:
        return 4 + slot
    if role == WEST and 1 <= slot <= 4:
        return 8 + slot
    raise OpponentGenerationError('That seat is outside the 13 non-swing positions.')


def extra_position(season_year):
    """Central 1, West 1, Central 2, West 2, Central 3, West 3, Central 4, West 4."""
    offset = season_year - FIRST_SEASON
    if not 0 <= offset <= 7:
        raise OpponentGenerationError('The extra-game rotation covers 1970–1977.')
    slot = offset // 2 + 1
    role = CENTRAL if offset % 2 == 0 else WEST
    return position_for(role, slot)


def generate_opponent_pairings(league_id, seed):
    """Write 1970–1977 pairings. Return ``created`` or ``unchanged``."""
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
                    'The opponent pairings did not match the project rule.'
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

    ``schedule_length`` is the stored era value. This rule accepts only 14.
    """
    if schedule_length != REQUIRED_SCHEDULE_LENGTH:
        raise OpponentGenerationError(
            f'{season_year} stores schedule_length {schedule_length}, not 14.'
        )
    if not FIRST_SEASON <= season_year <= LAST_SEASON:
        raise OpponentGenerationError('Opponent generation covers 1970–1977 only.')
    grouped = _group_seats(season_year, seats)
    raw = _raw_games(season_year, grouped)
    oriented = _orient_games(raw)
    pairings = tuple(
        Pairing(
            season_year,
            grouped['franchise_at'][home],
            grouped['franchise_at'][away],
            kind,
            home,
            away,
        )
        for kind, home, away in oriented
    )
    _validate_season(season_year, pairings, grouped)
    return pairings


def repair_homes(edges, quotas, pinned):
    """Orient single games so each node meets its home quota.

    The initial home is the endpoint with the lower ``(conference, index)``
    key. A swing club uses index 13, after the 13 non-swing positions.
    While any node is over its quota, the search starts at the lowest such
    key and walks home-to-away edges. Neighbors are tried in key order, and
    equal keys use the edge's index. The first path that reaches an
    under-quota node is reversed. Edges in ``pinned`` keep the home they
    were given and are not reversed.
    """
    homes = []
    for index, (left, right) in enumerate(edges):
        if index in pinned:
            homes.append(0)
        else:
            homes.append(0 if _node_key(left) <= _node_key(right) else 1)
    targets = {node: quotas[node] for edge in edges for node in edge}
    for _step in range(20000):
        counts = Counter()
        for edge, home_index in zip(edges, homes):
            counts[edge[home_index]] += 1
        over = sorted(
            (node for node, quota in targets.items() if counts[node] > quota),
            key=_node_key,
        )
        under = sorted(
            (node for node, quota in targets.items() if counts[node] < quota),
            key=_node_key,
        )
        if not over and not under:
            return tuple(edge[home_index] for edge, home_index in zip(edges, homes))
        if not over or not under:
            raise OpponentGenerationError('Home repair could not balance every club.')
        start = over[0]
        goals = set(under)
        adjacency = defaultdict(list)
        for index, (edge, home_index) in enumerate(zip(edges, homes)):
            if index in pinned:
                continue
            adjacency[edge[home_index]].append((edge[1 - home_index], index))
        for node in adjacency:
            adjacency[node].sort(key=lambda item: (_node_key(item[0]), item[1]))
        previous = {start: None}
        queue = [start]
        cursor = 0
        found = None
        while cursor < len(queue):
            current = queue[cursor]
            cursor += 1
            if current in goals and current != start:
                found = current
                break
            for neighbor, index in adjacency[current]:
                if neighbor not in previous:
                    previous[neighbor] = (current, index)
                    queue.append(neighbor)
        if found is None:
            raise OpponentGenerationError('Home repair could not balance every club.')
        node = found
        while node != start:
            prior, index = previous[node]
            homes[index] = 1 - homes[index]
            node = prior
    raise OpponentGenerationError('Home repair could not balance every club.')


def _node_key(node):
    return node


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


def _expected_span(release):
    expected = {}
    for season_year in range(FIRST_SEASON, LAST_SEASON + 1):
        length = _schedule_length(release, season_year)
        seats = _seats_for(release, season_year)
        expected[season_year] = build_season(season_year, seats, length)
    _validate_matrix(expected)
    return expected


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
    swing_year = season_year >= 1976
    grouped = {
        'by_node': {},
        'franchise_at': {},
        'swing': {},
        'extra': extra_position(season_year),
    }
    for conference, members in by_conference.items():
        buckets = {EAST: {}, CENTRAL: {}, WEST: {}}
        for seat in members:
            if seat.role not in buckets:
                raise OpponentGenerationError(f'{season_year} has an unknown division role.')
            if seat.slot in buckets[seat.role]:
                raise OpponentGenerationError(
                    f'{season_year} repeats a slot in conference {conference}.'
                )
            buckets[seat.role][seat.slot] = seat
        _require_core(season_year, conference, buckets, swing_year)
        swing = _swing_seat(season_year, conference, buckets, swing_year)
        if swing is not None:
            node = (conference, SWING_INDEX)
            grouped['by_node'][node] = swing
            grouped['franchise_at'][node] = swing.franchise_id
            grouped['swing'][conference] = node
        for role, slots in buckets.items():
            for slot, seat in slots.items():
                if swing is not None and seat.franchise_id == swing.franchise_id:
                    continue
                node = (conference, position_for(role, slot))
                grouped['by_node'][node] = seat
                grouped['franchise_at'][node] = seat.franchise_id
    return grouped


def _require_core(season_year, conference, buckets, swing_year):
    expected = {
        EAST: set(range(1, 6)),
        CENTRAL: set(range(1, 5)),
        WEST: set(range(1, 5)),
    }
    for role, slots in expected.items():
        present = set(buckets[role])
        if swing_year and role != EAST:
            present.discard(5)
        if present != slots:
            raise OpponentGenerationError(
                f'{season_year} conference {conference} {role} seats do not match the rule.'
            )


def _swing_seat(season_year, conference, buckets, swing_year):
    swings = []
    for role in (CENTRAL, WEST):
        seat = buckets[role].get(5)
        if seat is not None:
            swings.append(seat)
    if not swing_year:
        if swings:
            raise OpponentGenerationError(
                f'{season_year} has a swing seat before 1976.'
            )
        return None
    if len(swings) != 1:
        raise OpponentGenerationError(
            f'{season_year} conference {conference} needs one Central or West swing club.'
        )
    return swings[0]


def _raw_games(season_year, grouped):
    """Undirected single games, plus division pairs marked for home-and-home."""
    games = []
    offset = season_year - FIRST_SEASON
    for conference in (1, 2):
        games.extend(_division_games(conference, grouped))
        games.extend(_intraconference_games(season_year, conference, offset, grouped))
        if season_year >= 1976:
            swing = grouped['swing'][conference]
            for index in range(NON_SWING_POSITIONS):
                games.append((KIND_SWING_CONFERENCE, (conference, index), swing, False))
    games.extend(_interconference_games(season_year, grouped))
    if season_year >= 1976:
        games.append((
            KIND_SWING_MEETING,
            grouped['swing'][1],
            grouped['swing'][2],
            False,
        ))
    return games


def _division_games(conference, grouped):
    games = []
    bands = ((0, 5), (5, 9), (9, 13))
    for start, stop in bands:
        indexes = range(start, stop)
        nodes = [(conference, index) for index in indexes]
        for left_at, left in enumerate(nodes):
            for right in nodes[left_at + 1:]:
                games.append((KIND_DIVISION, left, right, True))
    return games


def _intraconference_games(season_year, conference, offset, grouped):
    games = []
    for central_slot in range(4):
        skip = (central_slot + offset) % 4
        for west_slot in range(4):
            if west_slot == skip:
                continue
            games.append((
                KIND_INTRA,
                (conference, position_for(CENTRAL, central_slot + 1)),
                (conference, position_for(WEST, west_slot + 1)),
                False,
            ))
    extra_local = offset
    for local in range(8):
        places = (local - extra_local) % 8
        slot = local // 2 + 1
        role = CENTRAL if local % 2 == 0 else WEST
        club = (conference, position_for(role, slot))
        for east_slot in EAST_TEMPLATE[places]:
            games.append((
                KIND_INTRA,
                (conference, position_for(EAST, east_slot)),
                club,
                False,
            ))
    return games


def _ordinary_opponents(season_year, index):
    if season_year in (1970, 1971):
        return tuple((index + diff) % 13 for diff in (1, 2, 3))
    if season_year == 1972:
        return (
            (index + 4) % 13,
            _special_1972(index),
            (index + 6) % 13,
        )
    if season_year == 1973:
        return tuple((index + diff) % 13 for diff in (4, 5, 6))
    if season_year in (1974, 1975):
        return tuple((index + diff) % 13 for diff in (7, 8, 9))
    drop = 10 if season_year == 1976 else 11
    provisional = tuple((index + diff) % 13 for diff in (10, 11, 12))
    return tuple(opponent for opponent in provisional if opponent != (index + drop) % 13)


def _special_1972(index):
    """East keeps its position. Positions 5–7 use +5. Positions 8–12 use +10."""
    if index <= 4:
        return index
    if index <= 7:
        return (index + 5) % 13
    return (index + 10) % 13


def _interconference_games(season_year, grouped):
    games = []
    seen = set()
    for index in range(NON_SWING_POSITIONS):
        for opponent in _ordinary_opponents(season_year, index):
            pair = (index, opponent)
            if pair in seen or index == opponent and season_year != 1972:
                raise OpponentGenerationError(
                    f'{season_year} repeats an interconference opponent.'
                )
            seen.add(pair)
            games.append((
                KIND_MATRIX,
                (1, index),
                (2, opponent),
                False,
            ))
    extra = grouped['extra']
    if (extra, extra) in seen:
        raise OpponentGenerationError(
            f'{season_year} already paired the extra-game clubs.'
        )
    if season_year >= 1976:
        drop = 10 if season_year == 1976 else 11
        if any((index + drop) % 13 == opponent for index, opponent in seen):
            raise OpponentGenerationError(
                f'{season_year} kept a game the deletion matching removes.'
            )
        removed = {(index, (index + drop) % 13) for index in range(NON_SWING_POSITIONS)}
        if removed & seen:
            raise OpponentGenerationError(
                f'{season_year} kept a game the deletion matching removes.'
            )
        if (extra, extra) in removed:
            raise OpponentGenerationError(
                f'{season_year} would remove the extra-game pairing.'
            )
    games.append((KIND_MATRIX, (1, extra), (2, extra), False))
    return games


def _orient_games(raw):
    division = []
    single = []
    pinned = set()
    for kind, left, right, both_ways in raw:
        if both_ways:
            division.append((kind, left, right))
            division.append((kind, right, left))
            continue
        if kind == KIND_SWING_MEETING:
            pinned.add(len(single))
            if left[0] != 1:
                raise OpponentGenerationError('Conference 1 hosts the swing-club meeting.')
        single.append((kind, left, right))
    quotas = {}
    for _kind, left, right in single:
        for node in (left, right):
            quotas.setdefault(node, _single_home_quota(node[1]))
    homes = repair_homes(
        [(left, right) for _kind, left, right in single],
        quotas,
        pinned,
    )
    oriented = list(division)
    for (kind, left, right), home in zip(single, homes):
        away = right if home == left else left
        oriented.append((kind, home, away))
    if KIND_SWING_MEETING in {kind for kind, _left, _right, _both in raw}:
        meetings = [game for game in oriented if game[0] == KIND_SWING_MEETING]
        if len(meetings) != 1 or meetings[0][1][0] != 1 or meetings[0][1][1] != SWING_INDEX:
            raise OpponentGenerationError('Conference 1 no longer hosts the swing-club meeting.')
    return oriented


def _single_home_quota(index):
    if index == SWING_INDEX:
        return 7
    if index < 5:
        return 3
    return 4


def _validate_season(season_year, pairings, grouped):
    swing_year = season_year >= 1976
    expected_total = SWING_SEASON_GAMES if swing_year else EARLY_SEASON_GAMES
    if len(pairings) != expected_total:
        raise OpponentGenerationError(
            f'{season_year} has {len(pairings)} games, not {expected_total}.'
        )
    counts = Counter(pairing.kind for pairing in pairings)
    expected_counts = {
        KIND_DIVISION: 88,
        KIND_INTRA: 54,
        KIND_MATRIX: MATRIX_SWING if swing_year else MATRIX_EARLY,
        KIND_SWING_CONFERENCE: 26 if swing_year else 0,
        KIND_SWING_MEETING: SWING_MEETINGS if swing_year else 0,
    }
    if any(counts[kind] != count for kind, count in expected_counts.items()):
        raise OpponentGenerationError(f'{season_year} game categories do not match the rule.')
    directed = set()
    meetings = defaultdict(list)
    games = Counter()
    homes = Counter()
    club_kinds = defaultdict(Counter)
    for pairing in pairings:
        key = (pairing.home_franchise_id, pairing.away_franchise_id)
        if pairing.home_franchise_id == pairing.away_franchise_id or key in directed:
            raise OpponentGenerationError(f'{season_year} repeats a directed pairing.')
        directed.add(key)
        pair = frozenset(key)
        meetings[pair].append(pairing)
        for franchise_id, is_home in (
            (pairing.home_franchise_id, True),
            (pairing.away_franchise_id, False),
        ):
            games[franchise_id] += 1
            club_kinds[franchise_id][pairing.kind] += 1
            if is_home:
                homes[franchise_id] += 1
    franchises = set(grouped['franchise_at'].values())
    if set(games) != franchises or any(games[franchise_id] != REQUIRED_SCHEDULE_LENGTH for franchise_id in franchises):
        raise OpponentGenerationError(f'{season_year} does not give every club 14 games.')
    if any(homes[franchise_id] != 7 for franchise_id in franchises):
        raise OpponentGenerationError(f'{season_year} does not finish every club 7–7.')
    for pair, rows in meetings.items():
        kinds = {row.kind for row in rows}
        if kinds == {KIND_DIVISION}:
            if len(rows) != 2:
                raise OpponentGenerationError(f'{season_year} division pair is not home-and-home.')
            homes_in_pair = {row.home_franchise_id for row in rows}
            if homes_in_pair != pair:
                raise OpponentGenerationError(f'{season_year} division pair is not home-and-home.')
        elif len(rows) != 1:
            raise OpponentGenerationError(
                f'{season_year} repeats a non-division opponent.'
            )
    _validate_role_counts(season_year, grouped, club_kinds)
    matrix_games = [pairing for pairing in pairings if pairing.kind == KIND_MATRIX]
    if swing_year:
        inter_total = len(matrix_games) + counts[KIND_SWING_MEETING]
        if len(matrix_games) != MATRIX_SWING or inter_total != 28:
            raise OpponentGenerationError(
                f'{season_year} must keep 27 matrix games and 28 interconference games.'
            )
    elif len(matrix_games) != MATRIX_EARLY:
        raise OpponentGenerationError(f'{season_year} must have 40 interconference games.')


def _validate_role_counts(season_year, grouped, club_kinds):
    extra = grouped['extra']
    swing_ids = {
        grouped['franchise_at'][node] for node in grouped['swing'].values()
    }
    for node, seat in grouped['by_node'].items():
        conference, index = node
        kinds = club_kinds[seat.franchise_id]
        if index == SWING_INDEX:
            if kinds[KIND_SWING_CONFERENCE] != 13 or kinds[KIND_SWING_MEETING] != 1:
                raise OpponentGenerationError(f'{season_year} swing club has the wrong opponents.')
            if sum(kinds.values()) != REQUIRED_SCHEDULE_LENGTH:
                raise OpponentGenerationError(f'{season_year} swing club has the wrong opponents.')
            continue
        other_division = 3
        east_games = 1 if index == extra else 2
        if index < 5:
            division_games = 8
            intra_games = 3
            east_games = 0
        else:
            division_games = 6
            intra_games = other_division + east_games
        matrix_games = 4 if index == extra else 3
        if season_year >= 1976:
            matrix_games -= 1
        expected = {
            KIND_DIVISION: division_games,
            KIND_INTRA: intra_games,
            KIND_MATRIX: matrix_games,
            KIND_SWING_CONFERENCE: 1 if season_year >= 1976 else 0,
            KIND_SWING_MEETING: 0,
        }
        actual = {kind: kinds[kind] for kind in expected}
        if actual != expected or sum(kinds.values()) != REQUIRED_SCHEDULE_LENGTH:
            raise OpponentGenerationError(
                f'{season_year} club at position {index} has the wrong game counts.'
            )
        if seat.franchise_id in swing_ids:
            raise OpponentGenerationError('A swing club was also scheduled as a non-swing seat.')
    if season_year >= 1976 and set(grouped['swing']) != {1, 2}:
        raise OpponentGenerationError(f'{season_year} is missing a swing club.')


def _validate_matrix(expected):
    """Count conference-1 position to conference-2 position, ignoring site."""
    meetings = Counter()
    for season_year, pairings in expected.items():
        directed = []
        for pairing in pairings:
            if pairing.kind != KIND_MATRIX:
                continue
            endpoints = (pairing.home_node, pairing.away_node)
            conference_1 = [node for node in endpoints if node[0] == 1]
            conference_2 = [node for node in endpoints if node[0] == 2]
            if len(conference_1) != 1 or len(conference_2) != 1:
                raise OpponentGenerationError(
                    f'{season_year} matrix game is not between the conferences.'
                )
            left, right = conference_1[0][1], conference_2[0][1]
            if left == SWING_INDEX or right == SWING_INDEX:
                raise OpponentGenerationError(
                    f'{season_year} puts a swing club inside the non-swing matrix.'
                )
            directed.append((left, right))
        if len(directed) != len(set(directed)) or set(directed) != set(_directed_matrix(season_year)):
            raise OpponentGenerationError(f'{season_year} matrix pairings drifted.')
        meetings.update(directed)
    if set(meetings) != {(left, right) for left in range(13) for right in range(13)}:
        raise OpponentGenerationError('A non-swing interconference pair is missing.')
    histogram = Counter(meetings.values())
    if histogram != Counter({1: 44, 2: 125}):
        raise OpponentGenerationError(
            'The non-swing matrix is not 125 pairs twice and 44 pairs once.'
        )
    if sum(count * times for count, times in histogram.items()) != 294:
        raise OpponentGenerationError('The non-swing matrix does not total 294 games.')


def _directed_matrix(season_year):
    pairs = []
    for index in range(NON_SWING_POSITIONS):
        for opponent in _ordinary_opponents(season_year, index):
            pairs.append((index, opponent))
    extra = extra_position(season_year)
    pairs.append((extra, extra))
    return tuple(pairs)


def _stored_state(release, expected):
    stored = list(
        OpponentPairing.objects.filter(release_id=release.pk).values_list(
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
