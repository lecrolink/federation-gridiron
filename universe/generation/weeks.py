"""14-week assignment for the stored 1970–1977 opponent pairings.

Each season has 14 regular-season weeks and no byes. Each club plays one
of its stored games in every week. The assignment points at the existing
``OpponentPairing`` and does not change that row. It is a project schedule,
not an NFL week chart, and it has no kickoff.

The search is exact. It forces a game with one legal week and a club-week
with one legal game, then branches on the tightest club-week. Ties break
by club slug, then week number, and the games for that slot are tried in
home-slug, away-slug order. The first complete assignment is kept. If the
search finishes without one, the season cannot be scheduled and nothing is
written. An exact rerun writes nothing. A partial or different set is left
in place.
"""

from collections import defaultdict
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
    OpponentWeekAssignment,
    UniverseRelease,
)
from universe.release_guard import assert_release_lock_held, database_alias, holding_release_lock
from universe.rules import EraRuleProfile


FIRST_SEASON = 1970
LAST_SEASON = 1977
WEEK_COUNT = 14

_DIFFER = (
    'Stored week assignments differ from the project schedule. '
    'The writer will not delete or replace them.'
)


class OpponentWeekError(Exception):
    """The 1970–1977 weeks cannot be written."""


class WeekGame(NamedTuple):
    key: object
    home: str
    away: str


def schedule_season(games, weeks=WEEK_COUNT):
    """Return ``{key: week}`` or ``None`` when no schedule exists.

    ``None`` means every branch failed. It is not a search limit. Weeks are
    numbered from 1. The same games produce the same result in either input
    order, because clubs and games are ordered by slug before the search.
    """
    if not games:
        return None
    if weeks < 1:
        return None
    ordered = tuple(sorted(games, key=lambda game: (game.home, game.away)))
    endpoints = {(game.home, game.away) for game in ordered}
    if len(endpoints) != len(ordered) or any(game.home == game.away for game in ordered):
        raise ValueError('Each pairing needs two distinct clubs.')
    clubs = sorted({club for game in ordered for club in (game.home, game.away)})
    index = {club: position for position, club in enumerate(clubs)}
    edges = [(index[game.home], index[game.away]) for game in ordered]
    found = _search(edges, len(clubs), weeks)
    if found is None:
        return None
    assignment = {game.key: week for game, week in zip(ordered, found)}
    if not _complete(ordered, assignment, weeks):
        raise RuntimeError('The week search constructed an invalid schedule.')
    return assignment


def generate_opponent_weeks(league_id, seed):
    """Write one week for every stored 1970–1977 pairing.

    Return ``created`` or ``unchanged``. Pairing rows stay as they are.
    """
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
                    'The week assignments did not match the project schedule.'
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


def _search(edges, club_count, weeks):
    """Return a 1-based week for every edge, or ``None`` if none exists."""
    full = (1 << weeks) - 1
    game_count = len(edges)
    incident = [[] for _ in range(club_count)]
    for game, (home, away) in enumerate(edges):
        incident[home].append(game)
        incident[away].append(game)

    def assign(week_of, domain, free, game, week):
        home, away = edges[game]
        bit = 1 << (week - 1)
        if (
            week_of[game] != 0
            or (domain[game] & bit) == 0
            or (free[home] & bit) == 0
            or (free[away] & bit) == 0
        ):
            return False
        week_of[game] = week
        domain[game] = bit
        free[home] &= ~bit
        free[away] &= ~bit
        for club in (home, away):
            for other in incident[club]:
                if other != game and week_of[other] == 0 and domain[other] & bit:
                    domain[other] &= ~bit
        return True

    def search(week_of, domain, free):
        week_of = week_of[:]
        domain = domain[:]
        free = free[:]
        changed = True
        while changed:
            changed = False
            for game, (home, away) in enumerate(edges):
                if week_of[game] != 0:
                    continue
                mask = domain[game] & free[home] & free[away]
                domain[game] = mask
                if mask == 0:
                    return None
                if mask.bit_count() == 1:
                    if not assign(week_of, domain, free, game, mask.bit_length()):
                        return None
                    changed = True
            if changed:
                continue
            for club in range(club_count):
                mask = free[club]
                week = 1
                forced = None
                while mask:
                    if mask & 1:
                        choices = [
                            game for game in incident[club]
                            if week_of[game] == 0 and domain[game] & (1 << (week - 1))
                        ]
                        if not choices:
                            return None
                        if len(choices) == 1:
                            forced = (choices[0], week)
                            break
                    mask >>= 1
                    week += 1
                if forced is not None:
                    if not assign(week_of, domain, free, *forced):
                        return None
                    changed = True
                    break
        if all(week_of):
            return week_of
        best = None
        for club in range(club_count):
            mask = free[club]
            week = 1
            while mask:
                if mask & 1:
                    choices = [
                        game for game in incident[club]
                        if week_of[game] == 0 and domain[game] & (1 << (week - 1))
                    ]
                    if not choices:
                        return None
                    ranked = (len(choices), club, week)
                    if best is None or ranked < best[0]:
                        best = (ranked, choices, week)
                mask >>= 1
                week += 1
        if best is None:
            return None
        _ranked, choices, week = best
        for game in choices:
            copied_weeks = week_of[:]
            copied_domain = domain[:]
            copied_free = free[:]
            if not assign(copied_weeks, copied_domain, copied_free, game, week):
                continue
            found = search(copied_weeks, copied_domain, copied_free)
            if found is not None:
                return found
        return None

    # Unassigned games use 0. A stored week is 1 through ``weeks``.
    return search([0] * game_count, [full] * game_count, [full] * club_count)


def _complete(games, assignment, weeks):
    if set(assignment) != {game.key for game in games}:
        return False
    used = set()
    played = defaultdict(set)
    for game in games:
        week = assignment[game.key]
        if not 1 <= week <= weeks:
            return False
        for club in (game.home, game.away):
            if (club, week) in used:
                return False
            used.add((club, week))
            played[club].add(week)
    expected = set(range(1, weeks + 1))
    return all(found == expected for found in played.values())


def _expected_weeks(release):
    stored = _pairings_by_season(release)
    expected = {}
    for season_year in range(FIRST_SEASON, LAST_SEASON + 1):
        pairings = stored.get(season_year, ())
        _require_fourteen(release, season_year, pairings)
        games = tuple(
            WeekGame(
                pairing.pk,
                pairing.home_franchise.placeholder_slug,
                pairing.away_franchise.placeholder_slug,
            )
            for pairing in sorted(
                pairings,
                key=lambda pairing: (
                    pairing.home_franchise.placeholder_slug,
                    pairing.away_franchise.placeholder_slug,
                ),
            )
        )
        assignment = schedule_season(games, WEEK_COUNT)
        if assignment is None:
            raise OpponentWeekError(
                f'{season_year} cannot be assigned to 14 weeks. '
                'The writer stored nothing.'
            )
        expected[season_year] = assignment
    return expected


def _require_fourteen(release, season_year, pairings):
    length = _schedule_length(release, season_year)
    if length != WEEK_COUNT:
        raise OpponentWeekError(
            f'{season_year} stores schedule_length {length}, not 14.'
        )
    if not pairings:
        raise OpponentWeekError(f'{season_year} has no stored opponent pairings.')
    members = set(
        FranchiseSeasonMembership.objects.filter(
            franchise__release_id=release.pk,
            season_year=season_year,
        ).values_list('franchise_id', flat=True)
    )
    counts = defaultdict(int)
    seen = set()
    for pairing in pairings:
        if (
            pairing.home_franchise.release_id != release.pk
            or pairing.away_franchise.release_id != release.pk
        ):
            raise OpponentWeekError(f'{season_year} has a club from another release.')
        counts[pairing.home_franchise_id] += 1
        counts[pairing.away_franchise_id] += 1
        seen.add(pairing.home_franchise_id)
        seen.add(pairing.away_franchise_id)
    if seen != members or set(counts.values()) != {WEEK_COUNT}:
        raise OpponentWeekError(
            f'{season_year} does not give every active club exactly 14 games.'
        )
    if len(seen) % 2 != 0:
        raise OpponentWeekError(
            f'{season_year} has an odd number of clubs, so a week cannot include every club.'
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
        raise OpponentWeekError(f'{season_year} needs exactly one stored era profile.')
    return profiles[0].schedule_length


def _pairings_by_season(release):
    rows = (
        OpponentPairing.objects.filter(
            release_id=release.pk,
            season_year__gte=FIRST_SEASON,
            season_year__lte=LAST_SEASON,
        )
        .select_related('home_franchise', 'away_franchise')
    )
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.season_year].append(row)
    return grouped


def _stored_state(release, expected):
    stored = list(
        OpponentWeekAssignment.objects.filter(
            release_id=release.pk,
            pairing__season_year__gte=FIRST_SEASON,
            pairing__season_year__lte=LAST_SEASON,
        ).values_list('pairing_id', 'week')
    )
    if not stored:
        outside = OpponentWeekAssignment.objects.filter(release_id=release.pk).exists()
        if outside:
            return 'differ'
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


def _pairing_rows(release):
    return tuple(
        OpponentPairing.objects.filter(release_id=release.pk)
        .order_by('id')
        .values_list(
            'id',
            'created_at',
            'season_year',
            'home_franchise_id',
            'away_franchise_id',
            'kind',
        )
    )


def _require_same_pairings(release, pairings):
    if _pairing_rows(release) != pairings:
        raise OpponentWeekError('The week writer changed an opponent pairing.')


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
