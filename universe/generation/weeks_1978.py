"""16-week assignment for the stored 1978–1994 opponent pairings.

Each season has 16 calendar weeks and no byes. Weeks 1–13 are one circle
round played in both conferences. Weeks 14–16 are the cross-conference
offsets ``t``, ``t+2``, and ``t+4`` modulo 14, in that order, where ``t``
is the season minus 1978. The same seats make 1992 repeat the 1978 weeks,
1993 repeat 1979, and 1994 repeat 1980.

The writer reads stored pairings and does not change them. It accepts a
stored ``schedule_length`` of 16 and does not edit the ``y1978`` or
``y1987`` cards. Missing, partial, duplicated, or mismatched pairings store
nothing. An exact week rerun writes nothing. A partial or different week
set is left in place. 1970–1977 week rows are not read or replaced. This
writer does not assign kickoffs, scores, standings, or simulation.
"""

from collections import defaultdict

from django.db import DEFAULT_DB_ALIAS, IntegrityError

from timelines.models import Timeline
from universe.generation.founding import (
    FoundingGenerationError,
    _resolve_release_id,
    _validate_seed,
)
from universe.generation.opponents import (
    CONFERENCE_INDEX,
    ROLE_BY_DIVISION,
    OpponentGenerationError,
    Seat,
)
from universe.generation.opponents_1978 import (
    POSITIONS,
    _group_seats,
    formula_offsets,
)
from universe.generation.schedule import GENERATOR_VERSION
from universe.generation.weeks import OpponentWeekError, _pairing_rows, _require_same_pairings
from universe.models import (
    FranchiseSeasonMembership,
    OpponentPairing,
    OpponentWeekAssignment,
    UniverseRelease,
)
from universe.release_guard import assert_release_lock_held, database_alias, holding_release_lock
from universe.rules import EraRuleProfile


FIRST_SEASON = 1978
LAST_SEASON = 1994
WEEK_COUNT = 16
ROUND_COUNT = 13
CROSS_WEEK = 14
CLUBS = 28
SEASON_GAMES = 224
REQUIRED_SCHEDULE_LENGTH = 16
REPEATED_SEASONS = {1992: 1978, 1993: 1979, 1994: 1980}

_PAIRINGS = (
    'Stored 1978–1994 opponent pairings are missing, partial, duplicated, '
    'or different from the project rule. The week writer stored nothing.'
)
_DIFFER = (
    'Stored 1978–1994 week assignments differ from the project rule. '
    'The writer will not delete or replace them.'
)


def _circle_rounds():
    """Thirteen rounds. Seat 0 stays fixed and the other seats rotate.

    The rotating seats start as 1 through 13. Each round moves the last of
    those seats to the front. Index ``i`` is paired with index ``13 - i``.
    Round 0 is 0–13, 1–12, 2–11, 3–10, 4–9, 5–8, and 6–7.
    """
    others = list(range(1, POSITIONS))
    rounds = []
    for _round_index in range(ROUND_COUNT):
        row = [0] + others
        rounds.append(tuple(
            (row[index], row[POSITIONS - 1 - index])
            for index in range(POSITIONS // 2)
        ))
        others = [others[-1]] + others[:-1]
    return tuple(rounds)


CIRCLE_ROUNDS = _circle_rounds()


def generate_opponent_weeks_1978(league_id, seed):
    """Write 1978–1994 weeks. Return ``created`` or ``unchanged``."""
    try:
        _validate_seed(seed)
        release_id = _resolve_release_id(league_id)
    except FoundingGenerationError as exc:
        raise OpponentWeekError(str(exc)) from exc
    try:
        with holding_release_lock(release_id, using=DEFAULT_DB_ALIAS) as release:
            metadata = _release_metadata(release)
            pairings = _pairing_rows(release)
            _refuse_closed(release)
            if release.generator_version != GENERATOR_VERSION:
                raise OpponentWeekError(
                    'The draft release must already have its fg-founding-2 schedule.'
                )
            if release.seed != seed:
                raise OpponentWeekError(
                    'The week writer will not change the release seed.'
                )
            expected = _expected_weeks(release)
            state = _stored_state(release, expected)
            if state == 'match':
                _require_same_pairings(release, pairings)
                return 'unchanged'
            if state == 'differ':
                raise OpponentWeekError(_DIFFER)
            _write_weeks(release, expected)
            if _stored_state(release, expected) != 'match':
                raise OpponentWeekError(
                    'The 1978–1994 week assignments did not match the project rule.'
                )
            _require_same_pairings(release, pairings)
            release.refresh_from_db()
            if _release_metadata(release) != metadata:
                raise OpponentWeekError(
                    'The week writer changed the release metadata.'
                )
            return 'created'
    except IntegrityError as exc:
        raise OpponentWeekError(
            'An opponent week insert violated a database constraint. '
            'The writer rolled back the week inserts.'
        ) from exc
    except (RuntimeError, ValueError) as exc:
        raise OpponentWeekError(str(exc)) from exc


def project_weeks(season_year, seats, pairings):
    """Return ``{pairing: week}`` for one season's stored rows.

    The pairing objects are the same objects that were passed in. A missing,
    extra, or repeated opponent raises ``OpponentWeekError``.
    """
    if not FIRST_SEASON <= season_year <= LAST_SEASON:
        raise OpponentWeekError('This week rule covers 1978–1994 only.')
    try:
        ordered, franchise_at = _group_seats(season_year, seats)
    except OpponentGenerationError as exc:
        raise OpponentWeekError(str(exc)) from exc
    clubs = {
        conference: tuple(franchise_at[node] for node in nodes)
        for conference, nodes in ordered.items()
    }
    expected = _expected_edges(season_year, clubs)
    found = {}
    seen = set()
    for pairing in pairings:
        key = frozenset((pairing.home_franchise_id, pairing.away_franchise_id))
        if len(key) != 2 or key in seen or key not in expected:
            raise OpponentWeekError(_PAIRINGS)
        seen.add(key)
        found[pairing] = expected[key]
    if seen != set(expected) or len(found) != SEASON_GAMES:
        raise OpponentWeekError(_PAIRINGS)
    _validate_assignment(season_year, found, clubs)
    return found


def _circle_rounds_cover_every_pair():
    seen = set()
    for pairs in CIRCLE_ROUNDS:
        clubs = [club for pair in pairs for club in pair]
        if len(clubs) != len(set(clubs)) or len(pairs) != POSITIONS // 2:
            return False
        for pair in pairs:
            key = frozenset(pair)
            if key in seen:
                return False
            seen.add(key)
    return len(seen) == POSITIONS * (POSITIONS - 1) // 2


def _expected_edges(season_year, clubs):
    if not _circle_rounds_cover_every_pair():
        raise OpponentWeekError('The circle rounds do not cover every conference game.')
    expected = {}
    for round_index, pairs in enumerate(CIRCLE_ROUNDS):
        week = round_index + 1
        for conference in (1, 2):
            seated = clubs[conference]
            for left, right in pairs:
                key = frozenset((seated[left], seated[right]))
                if len(key) != 2 or key in expected:
                    raise OpponentWeekError(f'{season_year} repeats a conference game.')
                expected[key] = week
    for offset_index, offset in enumerate(formula_offsets(season_year)):
        week = CROSS_WEEK + offset_index
        for position, left in enumerate(clubs[1]):
            right = clubs[2][(position + offset) % POSITIONS]
            key = frozenset((left, right))
            if len(key) != 2 or key in expected:
                raise OpponentWeekError(f'{season_year} repeats a cross-conference game.')
            expected[key] = week
    if len(expected) != SEASON_GAMES:
        raise OpponentWeekError(f'{season_year} does not have {SEASON_GAMES} games.')
    return expected


def _validate_assignment(season_year, found, clubs):
    position_of = {
        franchise_id: (conference, position)
        for conference, seated in clubs.items()
        for position, franchise_id in enumerate(seated)
    }
    offsets = formula_offsets(season_year)
    by_week = defaultdict(list)
    played = defaultdict(set)
    for pairing, week in found.items():
        if not 1 <= week <= WEEK_COUNT:
            raise OpponentWeekError(f'{season_year} assigned a week outside 1–16.')
        inter = pairing.kind == OpponentPairing.Kind.INTERCONFERENCE
        if week >= CROSS_WEEK and not inter:
            raise OpponentWeekError(f'{season_year} puts a conference game in a cross week.')
        if week < CROSS_WEEK and inter:
            raise OpponentWeekError(f'{season_year} puts a cross game in a circle week.')
        by_week[week].append(pairing)
        for franchise_id in (pairing.home_franchise_id, pairing.away_franchise_id):
            if week in played[franchise_id]:
                raise OpponentWeekError(f'{season_year} plays a club twice in one week.')
            played[franchise_id].add(week)
        home_at = position_of[pairing.home_franchise_id]
        away_at = position_of[pairing.away_franchise_id]
        if week >= CROSS_WEEK:
            left, right = home_at, away_at
            if left[0] == 2:
                left, right = right, left
            offset = (right[1] - left[1]) % POSITIONS
            if left[0] != 1 or right[0] != 2 or offset != offsets[week - CROSS_WEEK]:
                raise OpponentWeekError(f'{season_year} cross week {week} left formula order.')
        else:
            if home_at[0] != away_at[0]:
                raise OpponentWeekError(f'{season_year} week {week} crosses conferences.')
            round_pairs = {frozenset(pair) for pair in CIRCLE_ROUNDS[week - 1]}
            if frozenset((home_at[1], away_at[1])) not in round_pairs:
                raise OpponentWeekError(f'{season_year} week {week} left the circle round.')
    if set(by_week) != set(range(1, WEEK_COUNT + 1)):
        raise OpponentWeekError(f'{season_year} does not use every week.')
    if any(len(rows) != CLUBS // 2 for rows in by_week.values()):
        raise OpponentWeekError(f'{season_year} has a week that is not full.')
    full = set(range(1, WEEK_COUNT + 1))
    if len(played) != CLUBS or any(weeks != full for weeks in played.values()):
        raise OpponentWeekError(f'{season_year} gives a club a bye or a missing week.')


def _expected_weeks(release):
    pair_weeks = {}
    expected = {}
    for season_year in range(FIRST_SEASON, LAST_SEASON + 1):
        length = _schedule_length(release, season_year)
        if length != REQUIRED_SCHEDULE_LENGTH:
            raise OpponentWeekError(
                f'{season_year} stores schedule_length {length}, not 16.'
            )
        seats = _seats_for(release, season_year)
        stored = _pairings_for(release, season_year)
        found = project_weeks(season_year, seats, stored)
        expected[season_year] = {pairing.pk: week for pairing, week in found.items()}
        pair_weeks[season_year] = {
            frozenset((pairing.home_franchise_id, pairing.away_franchise_id)): week
            for pairing, week in found.items()
        }
    for season_year, earlier in REPEATED_SEASONS.items():
        if pair_weeks[season_year] != pair_weeks[earlier]:
            raise OpponentWeekError(
                f'{season_year} does not repeat the {earlier} weeks.'
            )
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
        raise OpponentWeekError(f'{season_year} needs exactly one stored era profile.')
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
            raise OpponentWeekError(
                f'{season_year} has a membership outside the approved roles.'
            )
        seats.append(Seat(row.franchise_id, conference, role, row.slot_order))
    return tuple(seats)


def _pairings_for(release, season_year):
    return tuple(
        OpponentPairing.objects.filter(
            release_id=release.pk,
            season_year=season_year,
        )
    )


def _stored_state(release, expected):
    stored = list(
        OpponentWeekAssignment.objects.filter(
            release_id=release.pk,
            pairing__season_year__gte=FIRST_SEASON,
            pairing__season_year__lte=LAST_SEASON,
        ).values_list('pairing_id', 'week')
    )
    if not stored:
        return 'absent'
    expected_rows = {
        (pairing_id, week)
        for assignment in expected.values()
        for pairing_id, week in assignment.items()
    }
    if len(stored) != len(expected_rows) or set(stored) != expected_rows:
        return 'differ'
    return 'match'


def _write_weeks(release, expected):
    alias = database_alias(release)
    assert_release_lock_held(release.pk, using=alias)
    for season_year in range(FIRST_SEASON, LAST_SEASON + 1):
        rows = [
            OpponentWeekAssignment(
                release=release,
                pairing_id=pairing_id,
                week=week,
            )
            for pairing_id, week in sorted(expected[season_year].items())
        ]
        OpponentWeekAssignment.objects.bulk_create(rows, batch_size=500)


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
        raise OpponentWeekError('A published release cannot be changed.')
    if Timeline.objects.filter(release_id=release.pk).exists():
        raise OpponentWeekError('A timeline points at this release.')
