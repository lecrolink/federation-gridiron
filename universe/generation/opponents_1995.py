"""1995–1998 opponent pairings for one fg-founding-2 draft.

This is a project schedule for the stored 1995–1998 seats. Each conference
has 15 clubs in ``d1``, ``d2``, and ``d3``. Each club plays every other club
in its conference once, plus two clubs in the other conference. It is not
an NFL opponent chart and it does not extend the 1978–1994 formula.

The ``y1995`` calendar games field stays unresolved. This writer does not
edit that card. It reads the stored ``schedule_length`` and accepts only
16. Accepting 16 does not approve the era card.

An empty 1995–1998 span is written in one transaction. An exact rerun
returns ``unchanged`` and writes nothing. A partial, extra, or mismatched
set in that span fails closed and is left as it was. 1970–1994 pairings
and week assignments are not read or replaced. This writer does not assign
weeks, byes, kickoffs, scores, standings, or simulation, and it does not
schedule seasons after 1998.
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
    CONFERENCE_INDEX,
    KIND_DIVISION,
    KIND_INTRA,
    OpponentGenerationError,
    Pairing,
    Seat,
    repair_homes,
)
from universe.generation.schedule import GENERATOR_VERSION
from universe.models import FranchiseSeasonMembership, OpponentPairing, UniverseRelease
from universe.release_guard import assert_release_lock_held, database_alias, holding_release_lock
from universe.rules import EraRuleProfile


FIRST_SEASON = 1995
LAST_SEASON = 1998
REQUIRED_SCHEDULE_LENGTH = 16
SEASON_GAMES = 240
CLUBS = 30
POSITIONS = 15
DIVISION_SIZE = 5
CROSS_GAMES = 2
CROSS_GAP = 5
DIVISION_ORDER = ('d1', 'd2', 'd3')
DIVISION_RANK = {name: index for index, name in enumerate(DIVISION_ORDER)}
KIND_INTER = OpponentPairing.Kind.INTERCONFERENCE
DIVISION_GAMES = 60
INTRA_GAMES = 150
INTER_GAMES = 30
# Four seasons cover eight of the fifteen cross offsets, each once.
SPAN_CROSS_PAIRS = 120

_DIFFER = (
    'Stored 1995–1998 opponent pairings differ from the project rule. '
    'The writer will not delete or replace them.'
)


def cross_offsets(season_year):
    """Return ``t`` and ``t+5`` modulo 15 for one season in 1995–1998.

    ``t`` is ``season_year - 1995``.
    """
    if not FIRST_SEASON <= season_year <= LAST_SEASON:
        raise OpponentGenerationError('The cross-conference rotation covers 1995–1998.')
    step = season_year - FIRST_SEASON
    return tuple((step + delta) % POSITIONS for delta in (0, CROSS_GAP))


def generate_opponent_pairings_1995(league_id, seed):
    """Write 1995–1998 pairings. Return ``created`` or ``unchanged``."""
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
                    'The 1995–1998 opponent pairings did not match the project rule.'
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
    """Return one 1995–1998 season from in-memory seats.

    ``schedule_length`` is the stored era value. This rule accepts only 16.
    Accepting 16 does not approve the ``y1995`` calendar field.

    ``Seat.role`` is the division slug ``d1``, ``d2``, or ``d3``. This span
    orders those divisions directly. It does not use the East/Central/West
    size roles.
    """
    if schedule_length != REQUIRED_SCHEDULE_LENGTH:
        raise OpponentGenerationError(
            f'{season_year} stores schedule_length {schedule_length}, not 16.'
        )
    if not FIRST_SEASON <= season_year <= LAST_SEASON:
        raise OpponentGenerationError('This opponent rule covers 1995–1998 only.')
    ordered, franchise_at = _group_seats(season_year, seats)
    raw = _raw_games(season_year, ordered)
    homes = repair_homes(
        [(left, right) for _kind, left, right in raw],
        {
            node: REQUIRED_SCHEDULE_LENGTH // 2
            for _kind, left, right in raw
            for node in (left, right)
        },
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
    in seat order. Cross-conference games then follow position order, and
    each position's two opponents follow ``t``, then ``t+5``.
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
        buckets = {division: {} for division in DIVISION_ORDER}
        for seat in members:
            if seat.role not in buckets:
                raise OpponentGenerationError(
                    f'{season_year} has a division outside d1, d2, and d3.'
                )
            if seat.slot in buckets[seat.role]:
                raise OpponentGenerationError(
                    f'{season_year} repeats a slot in conference {conference}.'
                )
            buckets[seat.role][seat.slot] = seat
        sequence = []
        for division in DIVISION_ORDER:
            slots = set(buckets[division])
            if slots != set(range(1, DIVISION_SIZE + 1)):
                raise OpponentGenerationError(
                    f'{season_year} conference {conference} {division} '
                    'seats do not match the rule.'
                )
            for slot in range(1, DIVISION_SIZE + 1):
                seat = buckets[division][slot]
                node = (conference, DIVISION_RANK[division], slot)
                sequence.append(node)
                franchise_at[node] = seat.franchise_id
        ordered[conference] = tuple(sequence)
    if len(franchise_at) != CLUBS:
        raise OpponentGenerationError(f'{season_year} does not have 30 clubs.')
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
    conference_opponents = defaultdict(set)
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
        else:
            if pairing.home_node[0] != pairing.away_node[0]:
                raise OpponentGenerationError(
                    f'{season_year} conference game crosses conferences.'
                )
            conference_opponents[pairing.home_node].add(pairing.away_node)
            conference_opponents[pairing.away_node].add(pairing.home_node)
    if len(meetings) != SEASON_GAMES or any(len(rows) != 1 for rows in meetings.values()):
        raise OpponentGenerationError(f'{season_year} repeats an opponent.')
    franchises = set(games)
    if (
        len(franchises) != CLUBS
        or any(games[franchise_id] != REQUIRED_SCHEDULE_LENGTH for franchise_id in franchises)
    ):
        raise OpponentGenerationError(f'{season_year} does not give every club 16 games.')
    if any(homes[franchise_id] != REQUIRED_SCHEDULE_LENGTH // 2 for franchise_id in franchises):
        raise OpponentGenerationError(f'{season_year} does not finish every club 8–8.')
    offsets = cross_offsets(season_year)
    if len(set(offsets)) != CROSS_GAMES:
        raise OpponentGenerationError(f'{season_year} repeats a cross-conference offset.')
    expected_cross = {
        (index, (index + offset) % POSITIONS)
        for index in range(POSITIONS)
        for offset in offsets
    }
    if len(cross_pairs) != len(set(cross_pairs)) or set(cross_pairs) != expected_cross:
        raise OpponentGenerationError(f'{season_year} cross-conference opponents drifted.')
    per_club = {
        KIND_DIVISION: DIVISION_SIZE - 1,
        KIND_INTRA: (POSITIONS - 1) - (DIVISION_SIZE - 1),
        KIND_INTER: CROSS_GAMES,
    }
    for nodes in ordered.values():
        node_set = set(nodes)
        for node in nodes:
            if conference_opponents[node] != node_set - {node}:
                raise OpponentGenerationError(
                    f'{season_year} club at {node} misses a conference opponent.'
                )
            kinds = club_kinds[franchise_at[node]]
            actual = {kind: kinds[kind] for kind in per_club}
            if actual != per_club:
                raise OpponentGenerationError(
                    f'{season_year} club at {node} has the wrong game counts.'
                )


def _validate_rotation(expected):
    meetings = Counter()
    for season_year, pairings in expected.items():
        if cross_offsets(season_year) != tuple(
            (season_year - FIRST_SEASON + delta) % POSITIONS for delta in (0, CROSS_GAP)
        ):
            raise OpponentGenerationError(f'{season_year} left the 1995 rotation.')
        for pairing in pairings:
            if pairing.kind != KIND_INTER:
                continue
            endpoints = (pairing.home_node, pairing.away_node)
            left = next(node for node in endpoints if node[0] == 1)
            right = next(node for node in endpoints if node[0] == 2)
            meetings[(_position(left), _position(right))] += 1
    if len(meetings) != SPAN_CROSS_PAIRS or any(count != 1 for count in meetings.values()):
        raise OpponentGenerationError(
            'The 1995–1998 cross-conference rotation is not 120 pairs once.'
        )


def _position(node):
    """``d1`` slots, then ``d2`` slots, then ``d3`` slots, each starting at 1."""
    _conference, rank, slot = node
    return rank * DIVISION_SIZE + (slot - 1)


def _expected_span(release):
    seats_by_year = {}
    expected = {}
    for season_year in range(FIRST_SEASON, LAST_SEASON + 1):
        length = _schedule_length(release, season_year)
        seats = _seats_for(release, season_year)
        seats_by_year[season_year] = seats
        expected[season_year] = build_season(season_year, seats, length)
    _require_stable_seats(seats_by_year)
    _validate_rotation(expected)
    return expected


def _require_stable_seats(seats_by_year):
    signatures = []
    for season_year in range(FIRST_SEASON, LAST_SEASON + 1):
        signatures.append(frozenset(
            (seat.franchise_id, seat.conference, seat.role, seat.slot)
            for seat in seats_by_year[season_year]
        ))
    if any(signature != signatures[0] for signature in signatures):
        raise OpponentGenerationError('1995–1998 memberships are not the same stored seats.')


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
        conference = CONFERENCE_INDEX.get(row.conference_slug)
        if conference is None or row.division_slug not in DIVISION_RANK:
            raise OpponentGenerationError(
                f'{season_year} has a membership outside the 1995 divisions.'
            )
        if row.franchise.release_id != release.pk:
            raise OpponentGenerationError('A membership belongs to another release.')
        seats.append(Seat(row.franchise_id, conference, row.division_slug, row.slot_order))
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
