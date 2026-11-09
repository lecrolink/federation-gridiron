"""1990–1994 opponent pairings for one fg-founding-2 draft.

This continues the 1978–1989 project formula on the same stored seats.
``t`` is ``season - 1978``. Each club plays every other club in its
conference once, plus conference-2 positions ``t``, ``t+2``, and ``t+4``
modulo 14. The 14-year cycle repeats 1978 in 1992, 1979 in 1993, and
1980 in 1994. Those repeated opponent sets are part of the formula.

The ``y1987`` calendar games field stays unresolved. This writer does not
edit that card. It reads the stored ``schedule_length`` and accepts only
16. It does not assign weeks, byes, kickoffs, scores, standings, or
simulation, and it does not replace 1970–1989 rows.
"""

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
    Pairing,
    Seat,
    repair_homes,
)
from universe.generation.opponents_1978 import (
    REQUIRED_SCHEDULE_LENGTH,
    _group_seats,
    _raw_games,
    _validate_season,
    build_season as build_earlier_season,
    cross_offsets as earlier_offsets,
    formula_offsets,
)
from universe.generation.schedule import GENERATOR_VERSION
from universe.models import FranchiseSeasonMembership, OpponentPairing, UniverseRelease
from universe.release_guard import assert_release_lock_held, database_alias, holding_release_lock
from universe.rules import EraRuleProfile


FIRST_SEASON = 1990
LAST_SEASON = 1994
# The modulus is 14, so these three seasons reuse an earlier offset triple.
REPEATED_SEASONS = {1992: 1978, 1993: 1979, 1994: 1980}

_DIFFER = (
    'Stored 1990–1994 opponent pairings differ from the project rule. '
    'The writer will not delete or replace them.'
)


def cross_offsets(season_year):
    """Return ``t``, ``t+2``, and ``t+4`` modulo 14 for one season in 1990–1994."""
    if not FIRST_SEASON <= season_year <= LAST_SEASON:
        raise OpponentGenerationError('The cross-conference rotation covers 1990–1994.')
    return formula_offsets(season_year)


def generate_opponent_pairings_1990(league_id, seed):
    """Write 1990–1994 pairings. Return ``created`` or ``unchanged``."""
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
                    'The 1990–1994 opponent pairings did not match the project rule.'
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
    """Return one 1990–1994 season from in-memory seats.

    ``schedule_length`` is the stored era value. This rule accepts only 16.
    Accepting 16 does not approve the ``y1987`` calendar field.
    """
    if schedule_length != REQUIRED_SCHEDULE_LENGTH:
        raise OpponentGenerationError(
            f'{season_year} stores schedule_length {schedule_length}, not 16.'
        )
    if not FIRST_SEASON <= season_year <= LAST_SEASON:
        raise OpponentGenerationError('This opponent rule covers 1990–1994 only.')
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
    if cross_offsets(season_year) != formula_offsets(season_year):
        raise OpponentGenerationError(f'{season_year} left the 1978 rotation.')
    return pairings


def _expected_span(release):
    seats_by_year = {}
    expected = {}
    for season_year in range(FIRST_SEASON, LAST_SEASON + 1):
        length = _schedule_length(release, season_year)
        seats = _seats_for(release, season_year)
        seats_by_year[season_year] = seats
        expected[season_year] = build_season(season_year, seats, length)
    _require_stable_seats(seats_by_year)
    _require_repeated_sets(seats_by_year, expected)
    return expected


def _require_stable_seats(seats_by_year):
    signatures = []
    for season_year in range(FIRST_SEASON, LAST_SEASON + 1):
        signatures.append(frozenset(
            (seat.franchise_id, seat.conference, seat.role, seat.slot)
            for seat in seats_by_year[season_year]
        ))
    if any(signature != signatures[0] for signature in signatures):
        raise OpponentGenerationError('1990–1994 memberships are not the same stored seats.')


def _require_repeated_sets(seats_by_year, expected):
    for season_year, earlier_year in REPEATED_SEASONS.items():
        if cross_offsets(season_year) != earlier_offsets(earlier_year):
            raise OpponentGenerationError(
                f'{season_year} does not reuse the {earlier_year} offsets.'
            )
        repeated = build_earlier_season(
            earlier_year,
            seats_by_year[season_year],
            REQUIRED_SCHEDULE_LENGTH,
        )
        if _game_identity(expected[season_year]) != _game_identity(repeated):
            raise OpponentGenerationError(
                f'{season_year} does not repeat the {earlier_year} opponent set.'
            )


def _game_identity(pairings):
    return tuple(
        (
            pairing.home_franchise_id,
            pairing.away_franchise_id,
            pairing.kind,
            pairing.home_node,
            pairing.away_node,
        )
        for pairing in pairings
    )


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
