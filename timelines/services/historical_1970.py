"""Opening 1970 historical-template assignment and timeline fixtures.

1970 has no previous Federation season. Each active franchise keeps the
historical slot selected by its stored membership seat:

- conference-1 is the historical AFC template conference
- conference-2 is the historical NFC template conference
- stored division d3 is historical East
- stored division d1 is historical Central
- stored division d2 is historical West
- ascending ``slot_order`` is the ascending historical slot number

That mapping is an opening placement rule. It does not make a Federation
franchise an NFL franchise. The provenance club stays on the historical
slot.

The earliest stored phase at which these rows can exist is ``direction``.
Timeline start creates the 1970 ``TimelineSeason`` in that phase. This
writer uses the same eligibility as the formula schedule writer: a
published release, an active timeline, that season row, and a season that
is not already marked simulated. It does not read or advance the phase,
and it does not set a completion or simulation flag.

An empty 1970 assignment, fixture, and week set is validated in memory
and then written in one transaction. An exact rerun returns ``unchanged``
and writes nothing. A partial or different set is left as it was.
"""

from collections import Counter, defaultdict
from typing import NamedTuple

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from timelines.models import (
    OPENING_ASSIGNMENT_SEASON,
    ScheduleBye,
    ScheduleGame,
    ScheduleWeekAssignment,
    Timeline,
    TimelineScheduleSlotAssignment,
    TimelineSeason,
    historical_fixture_category,
    opening_slot_role,
    writing_calendar_rows,
)
from universe.generation.historical_1970_data import (
    GAMES,
    SEASON_YEAR,
    SLOTS,
    SOURCE_CITATION,
)
from universe.historical import (
    HistoricalScheduleBye,
    HistoricalScheduleGame,
    HistoricalScheduleSlot,
)
from universe.models import FranchiseSeasonMembership
from universe.release_guard import assert_release_lock_held, holding_release_lock


SEASON = OPENING_ASSIGNMENT_SEASON
CLUBS = 26
FIXTURES = 182
WEEKS = 14
GAMES_PER_WEEK = 13
GAMES_PER_CLUB = 14
HOME_GAMES = 7
BASIS = TimelineScheduleSlotAssignment.Basis.OPENING_MEMBERSHIP_SEAT
DIVISION_SHAPE = {
    ('conference-1', 'd3'): frozenset(range(1, 6)),
    ('conference-1', 'd1'): frozenset(range(1, 5)),
    ('conference-1', 'd2'): frozenset(range(1, 5)),
    ('conference-2', 'd3'): frozenset(range(1, 6)),
    ('conference-2', 'd1'): frozenset(range(1, 5)),
    ('conference-2', 'd2'): frozenset(range(1, 5)),
}

DIFFER = (
    'Stored 1970 historical fixtures differ from the opening assignment. '
    'The writer will not delete or replace them.'
)
INSERT_FAILED = (
    'The 1970 historical fixture insert failed. '
    'The writer rolled back every new row.'
)
PLAN_FAILED = 'The opening 1970 plan failed validation.'
TEMPLATE_ABSENT = 'The 1970 historical template is not stored.'
TEMPLATE_DIFFER = (
    'The stored 1970 historical template is not the checked-in source.'
)
MEMBERSHIP_SHAPE = (
    'The 1970 memberships are not the opening 26-club alignment.'
)


class HistoricalTimelineScheduleRefused(Exception):
    """The 1970 historical timeline schedule cannot be written."""


class AssignmentPlan(NamedTuple):
    franchise_id: int
    slot_id: int
    basis: str


class GamePlan(NamedTuple):
    home_id: int
    away_id: int
    category: str
    historical_game_id: int
    week: int
    home_slot_key: tuple
    away_slot_key: tuple


class SchedulePlan(NamedTuple):
    assignments: tuple
    games: tuple


def generate_historical_timeline_schedule_1970(*, timeline_id):
    """Write one timeline's 1970 historical fixtures.

    Return ``created`` or ``unchanged``.
    """
    if isinstance(timeline_id, bool) or not isinstance(timeline_id, int):
        raise HistoricalTimelineScheduleRefused('The timeline is missing.')
    probe = Timeline.objects.filter(pk=timeline_id).only('pk', 'release_id').first()
    if probe is None:
        raise HistoricalTimelineScheduleRefused('The timeline is missing.')
    with transaction.atomic():
        with holding_release_lock(probe.release_id) as release:
            return _generate_locked(release, timeline_id)


def _generate_locked(release, timeline_id):
    if release.status != release.Status.PUBLISHED:
        raise HistoricalTimelineScheduleRefused('The release is not published.')
    try:
        timeline = Timeline.objects.select_for_update().get(pk=timeline_id)
    except Timeline.DoesNotExist as exc:
        raise HistoricalTimelineScheduleRefused('The timeline is missing.') from exc
    if timeline.release_id != release.pk:
        raise HistoricalTimelineScheduleRefused('The timeline is missing.')
    if timeline.status != Timeline.Status.ACTIVE:
        raise HistoricalTimelineScheduleRefused('The timeline is not active.')
    try:
        season = TimelineSeason.objects.select_for_update().get(
            timeline_id=timeline.pk,
            season_year=SEASON,
        )
    except TimelineSeason.DoesNotExist as exc:
        raise HistoricalTimelineScheduleRefused(
            'The timeline has no season for this year.'
        ) from exc
    if season.regular_season_simulated:
        raise HistoricalTimelineScheduleRefused(
            'The season is already marked simulated.'
        )

    template = _require_template()
    memberships = _require_memberships(release.pk)
    plan = _build_plan(template, memberships)
    _prove(plan, template, memberships)

    assignment_rows = list(
        TimelineScheduleSlotAssignment.objects.select_for_update().filter(
            timeline_id=timeline.pk,
            season_year=SEASON,
        )
    )
    game_rows = list(
        ScheduleGame.objects.select_for_update().filter(
            timeline_id=timeline.pk,
            season_year=SEASON,
        )
    )
    week_rows = list(
        ScheduleWeekAssignment.objects.select_for_update().filter(
            timeline_id=timeline.pk,
            season_year=SEASON,
        ).select_related('game')
    )
    if ScheduleBye.objects.filter(
        timeline_id=timeline.pk,
        season_year=SEASON,
    ).exists():
        raise HistoricalTimelineScheduleRefused(DIFFER)

    found_assignments = {
        (row.franchise_id, row.historical_slot_id, row.assignment_basis)
        for row in assignment_rows
    }
    found_games = {
        (
            row.home_id,
            row.away_id,
            row.category,
            row.kind,
            row.historical_game_id,
        )
        for row in game_rows
    }
    found_weeks = {
        (row.game.historical_game_id, row.week)
        for row in week_rows
    }
    expected_assignments = {
        (row.franchise_id, row.slot_id, row.basis)
        for row in plan.assignments
    }
    expected_games = {
        (
            row.home_id,
            row.away_id,
            row.category,
            ScheduleGame.Kind.REGULAR,
            row.historical_game_id,
        )
        for row in plan.games
    }
    expected_weeks = {
        (row.historical_game_id, row.week)
        for row in plan.games
    }
    exact = (
        found_assignments == expected_assignments
        and len(assignment_rows) == len(found_assignments) == CLUBS
        and found_games == expected_games
        and len(game_rows) == len(found_games) == FIXTURES
        and found_weeks == expected_weeks
        and len(week_rows) == len(found_weeks) == FIXTURES
    )
    if exact:
        return 'unchanged'
    if assignment_rows or game_rows or week_rows:
        raise HistoricalTimelineScheduleRefused(DIFFER)

    assert_release_lock_held(release.pk)
    _insert(timeline, plan)
    return 'created'


def _require_template():
    slot_rows = list(HistoricalScheduleSlot.objects.filter(season_year=SEASON_YEAR))
    game_rows = list(
        HistoricalScheduleGame.objects.filter(season_year=SEASON_YEAR).select_related(
            'home_slot',
            'away_slot',
        )
    )
    bye_count = HistoricalScheduleBye.objects.filter(season_year=SEASON_YEAR).count()
    if not slot_rows and not game_rows and bye_count == 0:
        raise HistoricalTimelineScheduleRefused(TEMPLATE_ABSENT)
    expected_slots = {_slot_key(*slot) for slot in SLOTS}
    stored_slots = {_row_slot_key(row) for row in slot_rows}
    expected_games = {
        _game_key(week, home, away)
        for week, home, away in GAMES
    }
    stored_games = {_row_game_key(row) for row in game_rows}
    if (
        bye_count
        or len(slot_rows) != len(expected_slots)
        or stored_slots != expected_slots
        or len(game_rows) != len(expected_games)
        or stored_games != expected_games
    ):
        raise HistoricalTimelineScheduleRefused(TEMPLATE_DIFFER)
    by_role = {}
    for row in slot_rows:
        by_role[(row.conference_role, row.division_role, row.slot_number)] = row
    by_clubs = {}
    for row in game_rows:
        by_clubs[(
            row.home_slot.provenance_club,
            row.away_slot.provenance_club,
            row.week_played,
        )] = row
    ordered_games = []
    for week, home, away in GAMES:
        ordered_games.append(by_clubs[(home, away, week)])
    return by_role, tuple(ordered_games)


def _slot_key(conference, division, number, club):
    return (
        SEASON,
        conference,
        division,
        number,
        club,
        HistoricalScheduleSlot.OpeningBasis.MERGER_PLACEMENT,
        None,
        None,
        None,
        SOURCE_CITATION,
    )


def _row_slot_key(row):
    return (
        row.season_year,
        row.conference_role,
        row.division_role,
        row.slot_number,
        row.provenance_club,
        row.opening_basis,
        row.prior_place,
        row.prior_conference_role,
        row.prior_division_role,
        row.source_citation,
    )


def _game_key(week, home, away):
    home_slot = next(slot for slot in SLOTS if slot[3] == home)
    away_slot = next(slot for slot in SLOTS if slot[3] == away)
    return (
        SEASON,
        _slot_key(*home_slot),
        _slot_key(*away_slot),
        week,
        None,
        HistoricalScheduleGame.Status.PLAYED,
        SOURCE_CITATION,
    )


def _row_game_key(row):
    return (
        row.season_year,
        _row_slot_key(row.home_slot),
        _row_slot_key(row.away_slot),
        row.week_played,
        row.originally_scheduled_week,
        row.status,
        row.source_citation,
    )


def _require_memberships(release_id):
    rows = list(
        FranchiseSeasonMembership.objects.filter(
            franchise__release_id=release_id,
            season_year=SEASON,
        ).select_related('franchise')
    )
    if len(rows) != CLUBS:
        raise HistoricalTimelineScheduleRefused(MEMBERSHIP_SHAPE)
    found = defaultdict(set)
    franchises = set()
    for row in rows:
        franchise = row.franchise
        if franchise.pk in franchises or not _active_in_1970(franchise):
            raise HistoricalTimelineScheduleRefused(MEMBERSHIP_SHAPE)
        franchises.add(franchise.pk)
        role = opening_slot_role(
            row.conference_slug,
            row.division_slug,
            row.slot_order,
        )
        if role is None:
            raise HistoricalTimelineScheduleRefused(MEMBERSHIP_SHAPE)
        found[(row.conference_slug, row.division_slug)].add(row.slot_order)
    if {key: frozenset(values) for key, values in found.items()} != DIVISION_SHAPE:
        raise HistoricalTimelineScheduleRefused(MEMBERSHIP_SHAPE)
    return tuple(rows)


def _active_in_1970(franchise):
    if franchise.first_active_season > SEASON:
        return False
    final = franchise.final_active_season
    return final is None or final >= SEASON


def _build_plan(template, memberships):
    slots_by_role, games = template
    franchise_by_slot = {}
    assignments = []
    for membership in memberships:
        role = opening_slot_role(
            membership.conference_slug,
            membership.division_slug,
            membership.slot_order,
        )
        slot = slots_by_role.get(role)
        if slot is None:
            raise HistoricalTimelineScheduleRefused(MEMBERSHIP_SHAPE)
        franchise_by_slot[slot.pk] = membership
        assignments.append(AssignmentPlan(
            membership.franchise_id,
            slot.pk,
            BASIS,
        ))
    if len(franchise_by_slot) != CLUBS:
        raise HistoricalTimelineScheduleRefused(MEMBERSHIP_SHAPE)
    planned_games = []
    for game in games:
        home = franchise_by_slot[game.home_slot_id]
        away = franchise_by_slot[game.away_slot_id]
        planned_games.append(GamePlan(
            home.franchise_id,
            away.franchise_id,
            historical_fixture_category(
                home.conference_slug,
                home.division_slug,
                away.conference_slug,
                away.division_slug,
            ),
            game.pk,
            game.week_played,
            (
                game.home_slot.conference_role,
                game.home_slot.division_role,
                game.home_slot.slot_number,
            ),
            (
                game.away_slot.conference_role,
                game.away_slot.division_role,
                game.away_slot.slot_number,
            ),
        ))
    role_by_slot = {slot.pk: role for role, slot in slots_by_role.items()}
    assignments.sort(key=lambda row: (role_by_slot[row.slot_id], row.franchise_id))
    planned_games.sort(key=lambda row: (
        row.week,
        row.home_slot_key,
        row.away_slot_key,
    ))
    return SchedulePlan(tuple(assignments), tuple(planned_games))


def _prove(plan, template, memberships):
    slots_by_role, games = template
    try:
        _prove_plan(plan, slots_by_role, games, memberships)
    except HistoricalTimelineScheduleRefused:
        raise
    except (KeyError, TypeError, ValueError) as exc:
        raise HistoricalTimelineScheduleRefused(PLAN_FAILED) from exc


def _prove_plan(plan, slots_by_role, games, memberships):
    assignments = plan.assignments
    fixtures = plan.games
    if len(assignments) != CLUBS or len({row.franchise_id for row in assignments}) != CLUBS:
        raise HistoricalTimelineScheduleRefused(PLAN_FAILED)
    if len({row.slot_id for row in assignments}) != CLUBS:
        raise HistoricalTimelineScheduleRefused(PLAN_FAILED)
    if {row.basis for row in assignments} != {BASIS}:
        raise HistoricalTimelineScheduleRefused(PLAN_FAILED)
    membership_by_franchise = {row.franchise_id: row for row in memberships}
    if set(membership_by_franchise) != {row.franchise_id for row in assignments}:
        raise HistoricalTimelineScheduleRefused(PLAN_FAILED)
    slot_by_id = {slot.pk: slot for slot in slots_by_role.values()}
    if set(slot_by_id) != {row.slot_id for row in assignments}:
        raise HistoricalTimelineScheduleRefused(PLAN_FAILED)
    franchise_by_slot = {}
    for row in assignments:
        membership = membership_by_franchise[row.franchise_id]
        slot = slot_by_id[row.slot_id]
        role = opening_slot_role(
            membership.conference_slug,
            membership.division_slug,
            membership.slot_order,
        )
        if role != (slot.conference_role, slot.division_role, slot.slot_number):
            raise HistoricalTimelineScheduleRefused(PLAN_FAILED)
        if slot.season_year != SEASON:
            raise HistoricalTimelineScheduleRefused(PLAN_FAILED)
        franchise_by_slot[slot.pk] = membership

    if len(fixtures) != FIXTURES or len({row.historical_game_id for row in fixtures}) != FIXTURES:
        raise HistoricalTimelineScheduleRefused(PLAN_FAILED)
    if {row.historical_game_id for row in fixtures} != {game.pk for game in games}:
        raise HistoricalTimelineScheduleRefused(PLAN_FAILED)
    directed = set()
    played = Counter()
    homes = Counter()
    aways = Counter()
    week_counts = Counter()
    week_clubs = defaultdict(set)
    game_by_id = {game.pk: game for game in games}
    for fixture in fixtures:
        if fixture.home_id == fixture.away_id:
            raise HistoricalTimelineScheduleRefused(PLAN_FAILED)
        pair = (fixture.home_id, fixture.away_id)
        if pair in directed:
            raise HistoricalTimelineScheduleRefused(PLAN_FAILED)
        directed.add(pair)
        source = game_by_id[fixture.historical_game_id]
        if source.season_year != SEASON or source.week_played != fixture.week:
            raise HistoricalTimelineScheduleRefused(PLAN_FAILED)
        home = franchise_by_slot[source.home_slot_id]
        away = franchise_by_slot[source.away_slot_id]
        if fixture.home_id != home.franchise_id or fixture.away_id != away.franchise_id:
            raise HistoricalTimelineScheduleRefused(PLAN_FAILED)
        category = historical_fixture_category(
            home.conference_slug,
            home.division_slug,
            away.conference_slug,
            away.division_slug,
        )
        if fixture.category != category:
            raise HistoricalTimelineScheduleRefused(PLAN_FAILED)
        for franchise_id in (fixture.home_id, fixture.away_id):
            if franchise_id in week_clubs[fixture.week]:
                raise HistoricalTimelineScheduleRefused(PLAN_FAILED)
            week_clubs[fixture.week].add(franchise_id)
            played[franchise_id] += 1
        homes[fixture.home_id] += 1
        aways[fixture.away_id] += 1
        week_counts[fixture.week] += 1
    club_ids = set(membership_by_franchise)
    if set(played) != club_ids or any(played[club] != GAMES_PER_CLUB for club in club_ids):
        raise HistoricalTimelineScheduleRefused(PLAN_FAILED)
    if any(homes[club] != HOME_GAMES or aways[club] != HOME_GAMES for club in club_ids):
        raise HistoricalTimelineScheduleRefused(PLAN_FAILED)
    if set(week_counts) != set(range(1, WEEKS + 1)):
        raise HistoricalTimelineScheduleRefused(PLAN_FAILED)
    if any(week_counts[week] != GAMES_PER_WEEK for week in week_counts):
        raise HistoricalTimelineScheduleRefused(PLAN_FAILED)
    if len(directed) != FIXTURES:
        raise HistoricalTimelineScheduleRefused(PLAN_FAILED)


def _insert(timeline, plan):
    assignment_rows = [
        TimelineScheduleSlotAssignment(
            timeline=timeline,
            season_year=SEASON,
            franchise_id=row.franchise_id,
            historical_slot_id=row.slot_id,
            assignment_basis=row.basis,
        )
        for row in plan.assignments
    ]
    game_rows = [
        ScheduleGame(
            timeline=timeline,
            season_year=SEASON,
            home_id=row.home_id,
            away_id=row.away_id,
            kind=ScheduleGame.Kind.REGULAR,
            category=row.category,
            historical_game_id=row.historical_game_id,
        )
        for row in plan.games
    ]
    try:
        with transaction.atomic():
            _clean(assignment_rows, INSERT_FAILED)
            TimelineScheduleSlotAssignment.objects.bulk_create(assignment_rows)
            _clean(game_rows, INSERT_FAILED)
            ScheduleGame.objects.bulk_create(game_rows)
            stored_games = {
                row.historical_game_id: row
                for row in ScheduleGame.objects.filter(
                    timeline_id=timeline.pk,
                    season_year=SEASON,
                )
            }
            week_rows = [
                ScheduleWeekAssignment(
                    timeline=timeline,
                    game=stored_games[row.historical_game_id],
                    season_year=SEASON,
                    week=row.week,
                )
                for row in sorted(plan.games, key=lambda item: (
                    item.week,
                    item.home_id,
                    item.away_id,
                ))
            ]
            with writing_calendar_rows():
                _clean(week_rows, INSERT_FAILED)
                ScheduleWeekAssignment.objects.bulk_create(week_rows)
            _confirm_inserted(timeline, plan)
    except IntegrityError as exc:
        raise HistoricalTimelineScheduleRefused(INSERT_FAILED) from exc


def _clean(rows, message):
    try:
        for row in rows:
            row.full_clean()
    except ValidationError as exc:
        raise HistoricalTimelineScheduleRefused(message) from exc


def _confirm_inserted(timeline, plan):
    assignments = TimelineScheduleSlotAssignment.objects.filter(
        timeline_id=timeline.pk,
        season_year=SEASON,
    )
    games = ScheduleGame.objects.filter(
        timeline_id=timeline.pk,
        season_year=SEASON,
    )
    weeks = ScheduleWeekAssignment.objects.filter(
        timeline_id=timeline.pk,
        season_year=SEASON,
    ).select_related('game')
    found_assignments = {
        (row.franchise_id, row.historical_slot_id, row.assignment_basis)
        for row in assignments
    }
    found_games = {
        (
            row.home_id,
            row.away_id,
            row.category,
            row.kind,
            row.historical_game_id,
        )
        for row in games
    }
    found_weeks = {
        (row.game.historical_game_id, row.week)
        for row in weeks
    }
    expected_assignments = {
        (row.franchise_id, row.slot_id, row.basis) for row in plan.assignments
    }
    expected_games = {
        (
            row.home_id,
            row.away_id,
            row.category,
            ScheduleGame.Kind.REGULAR,
            row.historical_game_id,
        )
        for row in plan.games
    }
    expected_weeks = {(row.historical_game_id, row.week) for row in plan.games}
    if (
        assignments.count() != CLUBS
        or len(found_assignments) != CLUBS
        or found_assignments != expected_assignments
        or games.count() != FIXTURES
        or len(found_games) != FIXTURES
        or found_games != expected_games
        or weeks.count() != FIXTURES
        or len(found_weeks) != FIXTURES
        or found_weeks != expected_weeks
        or ScheduleBye.objects.filter(
            timeline_id=timeline.pk,
            season_year=SEASON,
        ).exists()
    ):
        raise HistoricalTimelineScheduleRefused(INSERT_FAILED)
