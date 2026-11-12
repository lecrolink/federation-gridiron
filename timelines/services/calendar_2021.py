"""Calendar weeks and byes for stored 2021–2029 regular-season fixtures.

The NFL regular season from 2021 is 18 weeks and 272 games. Each club plays
17 games and has one bye. NFL Communications announcements for 2024, 2025,
and 2026, and the 2026 count of 17 consecutive seasons, put 16 division
games in week 18 for every season this writer assigns. Kickoff dates and
times are not stored.

Bye counts come from the NFL.com schedule's "Teams on Bye" list, read for
weeks 1 through 18 of each season. Each list names 32 clubs once. The 2021
NFL.com article "How bye weeks break down in NFL's 17-game 2021 season"
matches that season. The 2026 NFL.com article "2026 NFL schedule release:
Every team's bye week" matches 2026. The counts are:

- 2021, weeks 6–14: 4, 6, 2, 4, 4, 2, 2, 4, 4
- 2022: week 6 has 4, 7 has 4, 8 has 2, 9 has 6, 10 has 4, 11 has 4, 13 has 2, 14 has 6
- 2023: week 5 has 4, 6 has 2, 7 has 6, 9 has 4, 10 has 4, 11 has 4, 13 has 6, 14 has 2
- 2024: week 5 has 4, 6 has 4, 7 has 2, 9 has 2, 10 has 4, 11 has 4, 12 has 6, 14 has 6
- 2025: week 5 has 4, 6 has 2, 7 has 2, 8 has 6, 9 has 4, 10 has 4, 11 has 2, 12 has 4, 14 has 4
- 2026: week 5 has 2, 6 has 4, 7 has 4, 8 has 4, 9 has 2, 10 has 4, 11 has 6, 13 has 4, 14 has 2

Weeks that are not listed have no byes. 2024, 2025, and 2026 announcements
also say byes begin in week 5 and end in week 14. 2021 begins in week 6.
2022 has no bye in week 5 or week 12.

Those counts are the sourced rule. Which real club takes which bye is not.
Stored seats are unlabeled: ``conference-1`` and ``conference-2`` are the
rotation rows, ``d1`` through ``d4`` are East, North, South, and West as
rotation positions, and ``slot_order`` is not a club. The project assigns
bye slots by walking those seats. It does not copy a real club's bye.

2027, 2028, and 2029 have published opponent rotations and no published
bye calendar. NFL.com's week pages for those seasons list no teams on bye.
This writer refuses them before the release lock and stores nothing.

The writer reads the stored ``ScheduleGame`` rows. It does not change
opponents, home sites, results, division places, or completion flags.
``OpponentWeekAssignment`` is the shared 1970–1994 layer and is not read
or replaced. An exact rerun writes nothing. A partial or different
calendar is left in place. A failed insert rolls the season's weeks and
byes back together.
"""

from collections import defaultdict
from typing import NamedTuple

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from timelines.models import (
    ScheduleBye,
    ScheduleGame,
    ScheduleWeekAssignment,
    Timeline,
    TimelineSeason,
    writing_calendar_rows,
)
from timelines.services.division_place_2002 import (
    DivisionPlaceRefused,
    verify_played_places,
)
from timelines.services.schedule_2002 import (
    AFC_SIDE,
    NFC_SIDE,
    POSITIONS,
    Seat,
    TimelineScheduleRefused,
    planned_games,
)
from universe.models import FranchiseSeasonMembership
from universe.release_guard import assert_release_lock_held, holding_release_lock


FIRST_SEASON = 2021
LAST_SEASON = 2029
CALENDAR_LAST = 2026
WEEK_COUNT = 18
GAME_COUNT = 272
APPEARANCES = 17

# Week 1 is index 0. A zero is a week in which every club plays.
BYE_COUNTS = {
    2021: (0, 0, 0, 0, 0, 4, 6, 2, 4, 4, 2, 2, 4, 4, 0, 0, 0, 0),
    2022: (0, 0, 0, 0, 0, 4, 4, 2, 6, 4, 4, 0, 2, 6, 0, 0, 0, 0),
    2023: (0, 0, 0, 0, 4, 2, 6, 0, 4, 4, 4, 0, 6, 2, 0, 0, 0, 0),
    2024: (0, 0, 0, 0, 4, 4, 2, 0, 2, 4, 4, 6, 0, 6, 0, 0, 0, 0),
    2025: (0, 0, 0, 0, 4, 2, 2, 6, 4, 4, 2, 4, 0, 4, 0, 0, 0, 0),
    2026: (0, 0, 0, 0, 2, 4, 4, 4, 2, 4, 6, 0, 4, 2, 0, 0, 0, 0),
}

DIVISION_ROUNDS = (
    ((0, 1), (2, 3)),
    ((0, 2), (1, 3)),
    ((0, 3), (1, 2)),
)

OUTSIDE_SPAN = 'The season is outside 2021–2029.'
UNPUBLISHED = 'The {year} regular-season bye calendar is not published.'
NO_FIXTURES = 'The season has no stored fixtures.'
FIXTURE_MISMATCH = 'Stored fixtures do not match this season.'
DIFFER = (
    'Stored calendar rows differ from the project schedule. '
    'The writer will not delete or replace them.'
)
INSERT_FAILED = (
    'The calendar insert failed. The writer rolled back the calendar inserts.'
)


class CalendarRefused(Exception):
    """The regular-season calendar cannot be written."""


class CalendarGame(NamedTuple):
    key: object
    home: str
    away: str
    category: str


def assign_regular_season_calendar(*, timeline_id, season_year):
    """Write weeks and byes for one stored season.

    Return ``created`` or ``unchanged``. Fixture rows stay as they are.
    """
    _require_year(season_year)
    if not timeline_id:
        raise CalendarRefused('The timeline is missing.')
    probe = Timeline.objects.filter(pk=timeline_id).only('pk', 'release_id').first()
    if probe is None:
        raise CalendarRefused('The timeline is missing.')
    with transaction.atomic():
        with holding_release_lock(probe.release_id) as release:
            return _assign_locked(release, timeline_id, season_year)


def project_calendar(season_year, games, seats):
    """Return ``({key: week}, {slug: bye week})`` for one fixture set.

    ``games`` and ``seats`` are ordered inside this function. The same
    fixtures produce the same weeks in either input order. A season with
    no published bye calendar raises ``CalendarRefused``.
    """
    _require_year(season_year)
    counts = BYE_COUNTS[season_year]
    games = tuple(games)
    seats = tuple(seats)
    ordered_games = tuple(sorted(games, key=lambda game: (game.home, game.away, game.key)))
    clubs = _clubs(seats)
    byes = _bye_weeks(clubs, counts)
    division_weeks = _division_weeks(counts)
    week_of, remaining = _place_division_games(ordered_games, clubs, division_weeks)
    division_plan = dict(week_of)
    taken = _weeks_already_used(ordered_games, division_plan, byes)
    found = place_remaining_games(remaining, taken)
    if found is None:
        raise CalendarRefused(
            f'{season_year} cannot be assigned to its bye calendar. '
            'The writer stored nothing.'
        )
    week_of.update(found)
    if not _calendar_is_valid(
        ordered_games, clubs, week_of, byes, counts, division_plan,
    ):
        raise CalendarRefused(
            f'{season_year} calendar assignment is incomplete. '
            'The writer stored nothing.'
        )
    return week_of, byes


def _require_year(season_year):
    if isinstance(season_year, bool) or not isinstance(season_year, int):
        raise CalendarRefused(OUTSIDE_SPAN)
    if season_year < FIRST_SEASON or season_year > LAST_SEASON:
        raise CalendarRefused(OUTSIDE_SPAN)
    if season_year not in BYE_COUNTS:
        raise CalendarRefused(UNPUBLISHED.format(year=season_year))


def _assign_locked(release, timeline_id, season_year):
    if release.status != release.Status.PUBLISHED:
        raise CalendarRefused('The release is not published.')
    try:
        timeline = Timeline.objects.select_for_update().get(pk=timeline_id)
    except Timeline.DoesNotExist as exc:
        raise CalendarRefused('The timeline is missing.') from exc
    if timeline.release_id != release.pk:
        raise CalendarRefused('The timeline is missing.')
    if timeline.status != Timeline.Status.ACTIVE:
        raise CalendarRefused('The timeline is not active.')
    try:
        TimelineSeason.objects.select_for_update().get(
            timeline_id=timeline.pk,
            season_year=season_year,
        )
    except TimelineSeason.DoesNotExist as exc:
        raise CalendarRefused('The timeline has no season for this year.') from exc
    prior_year = season_year - 1
    try:
        places = verify_played_places(timeline, prior_year)
    except DivisionPlaceRefused as exc:
        raise CalendarRefused(str(exc)) from exc
    seats = _load_seats(release.pk, season_year)
    try:
        expected = planned_games(season_year, seats, places)
    except TimelineScheduleRefused as exc:
        raise CalendarRefused(str(exc)) from exc
    stored = list(
        ScheduleGame.objects.select_for_update().filter(
            timeline_id=timeline.pk,
            season_year=season_year,
        ).select_related('home', 'away').order_by('pk')
    )
    expected_keys = {
        (game.home_id, game.away_id, game.category, ScheduleGame.Kind.REGULAR)
        for game in expected
    }
    found_keys = {
        (row.home_id, row.away_id, row.category, row.kind)
        for row in stored
    }
    if not found_keys:
        raise CalendarRefused(NO_FIXTURES)
    if found_keys != expected_keys or len(stored) != len(found_keys):
        raise CalendarRefused(FIXTURE_MISMATCH)
    games = tuple(
        CalendarGame(
            row.pk,
            row.home.placeholder_slug,
            row.away.placeholder_slug,
            row.category,
        )
        for row in stored
    )
    week_of, byes = project_calendar(season_year, games, seats)
    slug_to_id = {seat.slug: seat.franchise_id for seat in seats}
    expected_weeks = week_of
    expected_byes = {slug_to_id[slug]: week for slug, week in byes.items()}
    stored_weeks = list(
        ScheduleWeekAssignment.objects.select_for_update().filter(
            timeline_id=timeline.pk,
            season_year=season_year,
        ).order_by('pk')
    )
    stored_byes = list(
        ScheduleBye.objects.select_for_update().filter(
            timeline_id=timeline.pk,
            season_year=season_year,
        ).order_by('pk')
    )
    state = _stored_state(stored_weeks, stored_byes, expected_weeks, expected_byes)
    if state == 'match':
        return 'unchanged'
    if state == 'differ':
        raise CalendarRefused(DIFFER)
    assert_release_lock_held(release.pk)
    _insert(timeline, season_year, stored, expected_weeks, expected_byes)
    return 'created'


def _stored_state(stored_weeks, stored_byes, expected_weeks, expected_byes):
    if not stored_weeks and not stored_byes:
        return 'empty'
    found_weeks = {row.game_id: row.week for row in stored_weeks}
    found_byes = {row.franchise_id: row.week for row in stored_byes}
    if (
        len(stored_weeks) == len(found_weeks) == len(expected_weeks)
        and len(stored_byes) == len(found_byes) == len(expected_byes)
        and found_weeks == expected_weeks
        and found_byes == expected_byes
    ):
        return 'match'
    return 'differ'


def _insert(timeline, season_year, stored, expected_weeks, expected_byes):
    by_id = {row.pk: row for row in stored}
    week_rows = [
        ScheduleWeekAssignment(
            timeline=timeline,
            game=by_id[game_id],
            season_year=season_year,
            week=week,
        )
        for game_id, week in sorted(expected_weeks.items())
    ]
    bye_rows = [
        ScheduleBye(
            timeline=timeline,
            franchise_id=franchise_id,
            season_year=season_year,
            week=week,
        )
        for franchise_id, week in sorted(expected_byes.items())
    ]
    try:
        with transaction.atomic():
            with writing_calendar_rows():
                _clean(week_rows, 'A calendar week failed validation.')
                ScheduleWeekAssignment.objects.bulk_create(week_rows)
                _clean(bye_rows, 'A bye failed validation.')
                ScheduleBye.objects.bulk_create(bye_rows)
    except IntegrityError as exc:
        raise CalendarRefused(INSERT_FAILED) from exc
    except ValidationError as exc:
        raise CalendarRefused(INSERT_FAILED) from exc


def _clean(rows, message):
    try:
        for row in rows:
            row.full_clean()
    except ValidationError as exc:
        raise CalendarRefused(message) from exc


def _load_seats(release_id, season_year):
    memberships = FranchiseSeasonMembership.objects.filter(
        franchise__release_id=release_id,
        season_year=season_year,
    ).select_related('franchise')
    return [
        Seat(
            franchise_id=row.franchise_id,
            slug=row.franchise.placeholder_slug,
            conference=row.conference_slug,
            division=row.division_slug,
            slot=row.slot_order,
        )
        for row in memberships
    ]


def _clubs(seats):
    groups = {}
    slugs = set()
    for seat in seats:
        if seat.slug in slugs:
            raise CalendarRefused('The season does not have eight divisions of four.')
        slugs.add(seat.slug)
        if (
            seat.conference not in (AFC_SIDE, NFC_SIDE)
            or seat.division not in POSITIONS
            or seat.slot not in (1, 2, 3, 4)
        ):
            raise CalendarRefused('The season does not have eight divisions of four.')
        groups.setdefault((seat.conference, seat.division), []).append(seat)
    if len(seats) != 32 or len(groups) != 8:
        raise CalendarRefused('The season does not have eight divisions of four.')
    ordered = []
    for conference in (AFC_SIDE, NFC_SIDE):
        for division in POSITIONS:
            group = groups.get((conference, division))
            if group is None or sorted(seat.slot for seat in group) != [1, 2, 3, 4]:
                raise CalendarRefused('The season does not have eight divisions of four.')
            ordered.append(tuple(sorted(group, key=lambda seat: seat.slot)))
    return tuple(ordered)


def _bye_weeks(clubs, counts):
    """Deal the sourced bye counts across unlabeled seats.

    Divisions are visited in seat order, rotating the start by the number
    of byes already dealt. Inside a division the next unused slot is taken.
    Each club is dealt once, and each bye week receives its sourced count.
    """
    assigned = {}
    cursor = [0] * len(clubs)
    start = 0
    for week, count in enumerate(counts, start=1):
        if count == 0:
            continue
        picked = 0
        for step in range(len(clubs)):
            if picked == count:
                break
            division = (start + step) % len(clubs)
            members = clubs[division]
            for offset in range(len(members)):
                seat = members[(cursor[division] + offset) % len(members)]
                if seat.slug not in assigned:
                    assigned[seat.slug] = week
                    cursor[division] = (members.index(seat) + 1) % len(members)
                    picked += 1
                    break
        if picked != count:
            raise CalendarRefused('The bye calendar could not seat every club.')
        start = (start + count) % len(clubs)
    if len(assigned) != 32:
        raise CalendarRefused('The bye calendar could not seat every club.')
    return assigned


def _division_weeks(counts):
    """Five earliest weeks with no byes, then week 18.

    Week 18 is the sourced division week. The other five division rounds
    use the earliest remaining weeks in which every club plays. Which rival
    falls in week 18 is the project rotation, not a real club's opponent.
    """
    full = [week for week, count in enumerate(counts, start=1) if count == 0]
    early = [week for week in full if week != WEEK_COUNT]
    if WEEK_COUNT not in full or len(early) < 5:
        raise CalendarRefused('The bye calendar has no week-18 division round.')
    return early[:5] + [WEEK_COUNT]


def _place_division_games(games, clubs, division_weeks):
    by_pair = defaultdict(list)
    for game in games:
        if game.category != ScheduleGame.Category.DIVISION:
            continue
        by_pair[frozenset((game.home, game.away))].append(game)
    week_of = {}
    for group in clubs:
        slugs = [seat.slug for seat in group]
        for round_index, pairs in enumerate(DIVISION_ROUNDS):
            for left, right in pairs:
                matched = sorted(
                    by_pair.get(frozenset((slugs[left], slugs[right])), ()),
                    key=lambda game: (game.home, game.away, game.key),
                )
                if len(matched) != 2:
                    raise CalendarRefused('Stored division fixtures are not a double round.')
                first = division_weeks[round_index]
                second = division_weeks[round_index + 3]
                week_of[matched[0].key] = first
                week_of[matched[1].key] = second
    division_games = [
        game for game in games if game.category == ScheduleGame.Category.DIVISION
    ]
    if len(week_of) != len(division_games):
        raise CalendarRefused('Stored division fixtures are not a double round.')
    remaining = tuple(game for game in games if game.key not in week_of)
    return week_of, remaining


def _weeks_already_used(games, division_plan, byes):
    """Weeks each club has already spent on a division game or its bye."""
    used = defaultdict(set)
    for game in games:
        week = division_plan.get(game.key)
        if week is None:
            continue
        used[game.home].add(week)
        used[game.away].add(week)
    for slug, week in byes.items():
        used[slug].add(week)
    return used


def place_remaining_games(games, taken, week_count=WEEK_COUNT):
    """Place games in weeks neither club has already used.

    ``taken`` is the occupied-week set for each club, built from division
    assignments and byes before this search starts. A candidate week is
    legal only when both clubs are free. Placing a game adds that week to
    both clubs, and a failed branch removes it before the next candidate.
    The same games and occupied weeks produce the same assignment. ``None``
    means every branch failed.
    """
    ordered = tuple(sorted(games, key=lambda game: (game.home, game.away, game.key)))
    if not ordered:
        return {}
    slugs = sorted({club for game in ordered for club in (game.home, game.away)})
    index = {slug: position for position, slug in enumerate(slugs)}
    edges = [(index[game.home], index[game.away]) for game in ordered]
    keys = [game.key for game in ordered]
    full = (1 << week_count) - 1
    free = []
    for slug in slugs:
        mask = full
        for week in taken.get(slug, ()):
            if not 1 <= week <= week_count:
                return None
            mask &= ~(1 << (week - 1))
        free.append(mask)
    domain = [free[home] & free[away] for home, away in edges]
    if any(mask == 0 for mask in domain):
        return None
    week_of = [0] * len(edges)
    incident = [[] for _ in slugs]
    for game, (home, away) in enumerate(edges):
        incident[home].append(game)
        incident[away].append(game)

    def assign(game, week):
        home, away = edges[game]
        bit = 1 << (week - 1)
        if (
            week_of[game] != 0
            or (domain[game] & bit) == 0
            or (free[home] & bit) == 0
            or (free[away] & bit) == 0
        ):
            return None
        saved_week = week_of[game]
        saved_domain = domain[game]
        saved_home = free[home]
        saved_away = free[away]
        week_of[game] = week
        domain[game] = bit
        free[home] = saved_home & ~bit
        free[away] = saved_away & ~bit
        touched = []
        seen = set()
        for club in (home, away):
            for other in incident[club]:
                if other == game or other in seen or week_of[other] != 0:
                    continue
                if domain[other] & bit:
                    seen.add(other)
                    touched.append((other, domain[other]))
                    domain[other] &= ~bit

        def undo():
            week_of[game] = saved_week
            domain[game] = saved_domain
            free[home] = saved_home
            free[away] = saved_away
            for other, previous in touched:
                domain[other] = previous

        return undo

    def search():
        undos = []

        def place(game, week):
            undo = assign(game, week)
            if undo is None:
                return False
            undos.append(undo)
            return True

        def rollback():
            while undos:
                undos.pop()()

        def narrow(game, mask):
            if domain[game] == mask:
                return
            previous = domain[game]
            domain[game] = mask
            undos.append(lambda game=game, previous=previous: domain.__setitem__(game, previous))

        changed = True
        while changed:
            changed = False
            for game, (home, away) in enumerate(edges):
                if week_of[game] != 0:
                    continue
                mask = domain[game] & free[home] & free[away]
                narrow(game, mask)
                if mask == 0:
                    rollback()
                    return None
                if mask.bit_count() == 1:
                    if not place(game, mask.bit_length()):
                        rollback()
                        return None
                    changed = True
            if changed:
                continue
            for club in range(len(slugs)):
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
                            rollback()
                            return None
                        if len(choices) == 1:
                            forced = (choices[0], week)
                            break
                    mask >>= 1
                    week += 1
                if forced is not None:
                    if not place(*forced):
                        rollback()
                        return None
                    changed = True
                    break
        if all(week_of):
            return week_of[:]
        best = None
        for club in range(len(slugs)):
            mask = free[club]
            week = 1
            while mask:
                if mask & 1:
                    choices = [
                        game for game in incident[club]
                        if week_of[game] == 0 and domain[game] & (1 << (week - 1))
                    ]
                    if not choices:
                        rollback()
                        return None
                    ranked = (len(choices), club, week)
                    if best is None or ranked < best[0]:
                        best = (ranked, choices, week)
                mask >>= 1
                week += 1
        if best is None:
            rollback()
            return None
        _ranked, choices, week = best
        for game in choices:
            if not place(game, week):
                continue
            found = search()
            undos.pop()()
            if found is not None:
                return found
        rollback()
        return None

    found = search()
    if found is None:
        return None
    return {key: week for key, week in zip(keys, found)}


def _calendar_is_valid(games, clubs, week_of, byes, counts, division_plan):
    if set(week_of) != {game.key for game in games}:
        return False
    if set(byes) != {seat.slug for group in clubs for seat in group}:
        return False
    played = defaultdict(set)
    used = set()
    per_week = defaultdict(int)
    for game in games:
        week = week_of[game.key]
        if not 1 <= week <= WEEK_COUNT:
            return False
        per_week[week] += 1
        for slug in (game.home, game.away):
            if (slug, week) in used:
                return False
            if byes[slug] == week:
                return False
            used.add((slug, week))
            played[slug].add(week)
    for slug, bye in byes.items():
        if played[slug] != set(range(1, WEEK_COUNT + 1)) - {bye}:
            return False
        if counts[bye - 1] == 0:
            return False
    bye_counts = defaultdict(int)
    for bye in byes.values():
        bye_counts[bye] += 1
    for week, count in enumerate(counts, start=1):
        if bye_counts[week] != count:
            return False
        playing = 32 - count
        if playing % 2 or per_week[week] != playing // 2:
            return False
    week_18 = [game for game in games if week_of[game.key] == WEEK_COUNT]
    if len(week_18) != 16 or any(game.category != ScheduleGame.Category.DIVISION for game in week_18):
        return False
    if any(week_of[key] != week for key, week in division_plan.items()):
        return False
    planned = {game.key for game in games if game.category == ScheduleGame.Category.DIVISION}
    if set(division_plan) != planned:
        return False
    for game in games:
        if game.category == ScheduleGame.Category.DIVISION:
            continue
        for slug in (game.home, game.away):
            division_weeks = {
                week_of[other.key]
                for other in games
                if other.category == ScheduleGame.Category.DIVISION
                and slug in (other.home, other.away)
            }
            if week_of[game.key] in division_weeks:
                return False
    return True
