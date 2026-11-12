"""Opening 1970 historical-template assignments and timeline fixtures."""

from collections import Counter
from unittest.mock import patch

from django.db import IntegrityError, connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from leagues.models import League
from timelines.models import (
    ScheduleBye,
    ScheduleGame,
    ScheduleGameResult,
    ScheduleWeekAssignment,
    Timeline,
    TimelineDivisionPlace,
    TimelinePriorFinish,
    TimelineScheduleSeed,
    TimelineScheduleSlotAssignment,
    TimelineSchedulingDraw,
    TimelineSchedulingRank,
    TimelineSeason,
    writing_calendar_rows,
)
from timelines.services.historical_1970 import (
    DIFFER,
    INSERT_FAILED,
    HistoricalTimelineScheduleRefused,
    generate_historical_timeline_schedule_1970,
)
from timelines.services.prior_finish import (
    Finish,
    derive_scheduling_ranks,
    store_prior_finishes,
)
from timelines.services.schedule_2002 import generate_timeline_schedule
from universe.generation.historical_1970 import load_historical_schedule_1970
from universe.generation.historical_1970_data import GAMES, SLOTS
from universe.generation.opening_players import y1970_values
from universe.generation.opponents import generate_opponent_pairings
from universe.generation.prospects import later_profile_values
from universe.generation.schedule import generate_schedule_release
from universe.generation.weeks import generate_opponent_weeks
from universe.historical import (
    HistoricalScheduleBye,
    HistoricalScheduleGame,
    HistoricalScheduleSlot,
)
from universe.models import (
    FranchiseSeasonMembership,
    OpponentPairing,
    OpponentWeekAssignment,
    UniverseFranchise,
    UniverseRelease,
)
from universe.rules import EraRuleProfile


# slug, conference index, historical division role, slot number.
# conference-1 is 1, conference-2 is 2. Stored d1/d2/d3 are central/west/east.
SEATS = (
    ('founding-01', 1, 'central', 1),
    ('founding-02', 1, 'central', 2),
    ('founding-03', 1, 'central', 3),
    ('founding-04', 1, 'central', 4),
    ('founding-05', 1, 'west', 1),
    ('founding-06', 1, 'west', 2),
    ('founding-07', 1, 'west', 3),
    ('founding-08', 1, 'west', 4),
    ('founding-09', 1, 'east', 1),
    ('founding-10', 1, 'east', 2),
    ('founding-11', 1, 'east', 3),
    ('founding-12', 1, 'east', 4),
    ('founding-25', 1, 'east', 5),
    ('founding-13', 2, 'central', 1),
    ('founding-14', 2, 'central', 2),
    ('founding-15', 2, 'central', 3),
    ('founding-16', 2, 'central', 4),
    ('founding-17', 2, 'west', 1),
    ('founding-18', 2, 'west', 2),
    ('founding-19', 2, 'west', 3),
    ('founding-20', 2, 'west', 4),
    ('founding-21', 2, 'east', 1),
    ('founding-22', 2, 'east', 2),
    ('founding-23', 2, 'east', 3),
    ('founding-24', 2, 'east', 4),
    ('founding-26', 2, 'east', 5),
)
ROLE_BY_STORED = {
    ('conference-1', 'd3'): ('afc', 'east'),
    ('conference-1', 'd1'): ('afc', 'central'),
    ('conference-1', 'd2'): ('afc', 'west'),
    ('conference-2', 'd3'): ('nfc', 'east'),
    ('conference-2', 'd1'): ('nfc', 'central'),
    ('conference-2', 'd2'): ('nfc', 'west'),
}


def _writes(captured):
    found = []
    for query in captured.captured_queries:
        sql = query['sql'].lstrip()
        head = sql[:6].upper()
        if head.startswith(('INSERT', 'UPDATE', 'DELETE')):
            found.append(sql)
    return found


def _dump(queryset):
    names = [field.attname for field in queryset.model._meta.concrete_fields]
    return list(queryset.order_by('pk').values_list(*names))


def _category(home_seat, away_seat):
    if home_seat[0] == away_seat[0] and home_seat[1] == away_seat[1]:
        return 'division'
    if home_seat[0] == away_seat[0]:
        return 'conference'
    return 'interconference'


def _oracle():
    """Substituted 1970 fixtures from the checked-in template and seat map."""
    seats = {slug: (conference, division, number) for slug, conference, division, number in SEATS}
    slug_by_role = {
        ('afc' if conference == 1 else 'nfc', division, number): slug
        for slug, conference, division, number in SEATS
    }
    slug_by_club = {}
    for conference, division, number, club in SLOTS:
        slug_by_club[club] = slug_by_role[(conference, division, number)]
    fixtures = []
    for week, home, away in GAMES:
        home_slug = slug_by_club[home]
        away_slug = slug_by_club[away]
        fixtures.append((
            week,
            home_slug,
            away_slug,
            _category(seats[home_slug], seats[away_slug]),
        ))
    return fixtures


class Historical1970FixtureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        league = League(name='Historical Fixture League', slug='hist-fix-league', tier=1, active=True)
        league.save()
        release = UniverseRelease(
            slug='historical-fixtures-1970',
            root_league=league,
            generator_version='pending',
            seed=1,
            founding_franchise_count=26,
            expansion_schedule_version=1,
            first_season_year=1970,
            season_count=60,
            last_season_year=2029,
            status=UniverseRelease.Status.DRAFT,
            catalog_fingerprint='preexisting',
            validation_fingerprint='',
        )
        release.save()
        cls.release = release
        if generate_schedule_release(league.id, 1970) != 'created':
            raise AssertionError('The schedule release was not created.')
        release.refresh_from_db()
        profiles = [EraRuleProfile(release=release, **y1970_values())]
        profiles[0].save()
        wanted = {'y1974', 'y1976', 'y1999', 'y2002', 'y2021'}
        for values in later_profile_values():
            if values['profile_key'] in wanted:
                profile = EraRuleProfile(release=release, **values)
                profile.save()
                profiles.append(profile)
        cls.profiles = profiles
        if generate_opponent_pairings(league.id, 1970) != 'created':
            raise AssertionError('The 1970 opponent pairings were not created.')
        if generate_opponent_weeks(league.id, 1970) != 'created':
            raise AssertionError('The 1970 opponent weeks were not created.')
        if load_historical_schedule_1970() not in ('created', 'unchanged'):
            raise AssertionError('The 1970 historical template was not stored.')
        release.catalog_fingerprint = 'catalog-fixed'
        release.validation_fingerprint = 'validation-fixed'
        release.status = UniverseRelease.Status.PUBLISHED
        release.published_at = timezone.now()
        release.save()
        cls.timeline_a = _timeline_1970(release, '1970 alpha', profiles)
        cls.timeline_b = _timeline_1970(release, '1970 beta', profiles)
        cls.played = _timeline(release, 'played 2001', profiles, 2001, simulated_2001=True)
        store_prior_finishes(timeline_id=cls.played.pk, finishes=_default_finishes(release))
        derive_scheduling_ranks(timeline_id=cls.played.pk)
        cls.scheduled = _timeline(release, 'opens in 2002', profiles, 2002, simulated_2001=False)
        if generate_timeline_schedule(
            timeline_id=cls.scheduled.pk,
            season_year=2002,
        ) != 'created':
            raise AssertionError('The 2002 timeline schedule was not created.')
        _store_calendar_sample(cls.scheduled, release, profiles)

    def test_opening_slot_mapping_covers_all_six_divisions(self):
        self._create()
        memberships = FranchiseSeasonMembership.objects.filter(
            franchise__release=self.release,
            season_year=1970,
        ).select_related('franchise')
        grouped = {}
        for row in memberships:
            grouped.setdefault((row.conference_slug, row.division_slug), []).append(row)
        self.assertEqual(set(grouped), set(ROLE_BY_STORED))
        assignments = {
            row.franchise_id: row
            for row in TimelineScheduleSlotAssignment.objects.filter(
                timeline=self.timeline_a,
                season_year=1970,
            ).select_related('historical_slot', 'franchise')
        }
        self.assertEqual(len(assignments), 26)
        for key, rows in grouped.items():
            conference, division = ROLE_BY_STORED[key]
            ordered = sorted(rows, key=lambda row: row.slot_order)
            expected_count = 5 if division == 'east' else 4
            self.assertEqual(len(ordered), expected_count)
            numbers = []
            for row in ordered:
                slot = assignments[row.franchise_id].historical_slot
                self.assertEqual(slot.conference_role, conference)
                self.assertEqual(slot.division_role, division)
                self.assertEqual(slot.slot_number, row.slot_order)
                self.assertEqual(
                    assignments[row.franchise_id].assignment_basis,
                    'opening-membership-seat',
                )
                numbers.append(slot.slot_number)
            self.assertEqual(numbers, list(range(1, expected_count + 1)))
        self.assertEqual(
            {row.historical_slot_id for row in assignments.values()},
            set(HistoricalScheduleSlot.objects.filter(season_year=1970).values_list('pk', flat=True)),
        )

    def test_assignment_fixture_and_week_totals(self):
        timeline = self.timeline_a
        self.assertEqual(timeline.phase, Timeline.Phase.DIRECTION)
        season = TimelineSeason.objects.get(timeline=timeline, season_year=1970)
        self.assertFalse(season.regular_season_simulated)
        self.assertEqual(self._create(), 'created')
        timeline.refresh_from_db()
        season.refresh_from_db()
        self.assertEqual(timeline.phase, Timeline.Phase.DIRECTION)
        self.assertFalse(season.regular_season_simulated)
        self.assertEqual(season.phase, Timeline.Phase.DIRECTION)

        assignments = TimelineScheduleSlotAssignment.objects.filter(timeline=timeline)
        games = ScheduleGame.objects.filter(timeline=timeline, season_year=1970)
        weeks = ScheduleWeekAssignment.objects.filter(timeline=timeline, season_year=1970)
        self.assertEqual(assignments.count(), 26)
        self.assertEqual(games.count(), 182)
        self.assertEqual(weeks.count(), 182)
        self.assertEqual(
            ScheduleBye.objects.filter(timeline=timeline, season_year=1970).count(),
            0,
        )
        self.assertEqual(
            set(games.values_list('historical_game_id', flat=True)),
            set(HistoricalScheduleGame.objects.filter(season_year=1970).values_list('pk', flat=True)),
        )
        played = Counter()
        homes = Counter()
        aways = Counter()
        week_clubs = Counter()
        directed = set()
        for game in games.select_related('week_assignment'):
            self.assertNotEqual(game.home_id, game.away_id)
            pair = (game.home_id, game.away_id)
            self.assertNotIn(pair, directed)
            directed.add(pair)
            played[game.home_id] += 1
            played[game.away_id] += 1
            homes[game.home_id] += 1
            aways[game.away_id] += 1
            week_clubs[(game.week_assignment.week, game.home_id)] += 1
            week_clubs[(game.week_assignment.week, game.away_id)] += 1
            self.assertEqual(game.week_assignment.week, game.historical_game.week_played)
        self.assertEqual(set(played.values()), {14})
        self.assertEqual(set(homes.values()), {7})
        self.assertEqual(set(aways.values()), {7})
        self.assertEqual(len(played), 26)
        self.assertTrue(all(count == 1 for count in week_clubs.values()))
        week_counts = Counter(weeks.values_list('week', flat=True))
        self.assertEqual(week_counts, {week: 13 for week in range(1, 15)})
        self.assertEqual(
            Counter(games.values_list('category', flat=True)),
            Counter({'division': 88, 'conference': 54, 'interconference': 40}),
        )
        stored = {
            (
                game.week_assignment.week,
                game.home.placeholder_slug,
                game.away.placeholder_slug,
                game.category,
            )
            for game in games.select_related('week_assignment', 'home', 'away')
        }
        self.assertEqual(stored, set(_oracle()))

    def test_founding_01_slot_and_schedule(self):
        self._create()
        franchise = UniverseFranchise.objects.get(
            release=self.release,
            placeholder_slug='founding-01',
        )
        membership = FranchiseSeasonMembership.objects.get(
            franchise=franchise,
            season_year=1970,
        )
        self.assertEqual(
            (membership.conference_slug, membership.division_slug, membership.slot_order),
            ('conference-1', 'd1', 1),
        )
        self.assertNotIn('Cincinnati', franchise.display_name)
        self.assertNotIn('Bengals', franchise.display_name)
        self.assertNotIn('Bengals', franchise.placeholder_slug)
        assignment = TimelineScheduleSlotAssignment.objects.select_related(
            'historical_slot',
        ).get(timeline=self.timeline_a, franchise=franchise)
        slot = assignment.historical_slot
        self.assertEqual(
            (slot.conference_role, slot.division_role, slot.slot_number),
            ('afc', 'central', 1),
        )
        self.assertEqual(slot.provenance_club, 'Cincinnati Bengals')
        games = ScheduleGame.objects.filter(
            timeline=self.timeline_a,
            season_year=1970,
            home=franchise,
        ) | ScheduleGame.objects.filter(
            timeline=self.timeline_a,
            season_year=1970,
            away=franchise,
        )
        found = {
            (
                game.week_assignment.week,
                game.home.placeholder_slug,
                game.away.placeholder_slug,
                game.category,
            )
            for game in games.select_related('week_assignment', 'home', 'away')
        }
        expected = {
            row for row in _oracle()
            if row[1] == 'founding-01' or row[2] == 'founding-01'
        }
        self.assertEqual(found, expected)
        self.assertEqual(len(found), 14)
        self.assertEqual(sum(1 for row in found if row[1] == 'founding-01'), 7)
        self.assertIn((1, 'founding-01', 'founding-07', 'conference'), found)
        self.assertIn((2, 'founding-14', 'founding-01', 'interconference'), found)
        self.assertIn((3, 'founding-01', 'founding-03', 'division'), found)
        self.assertIn((6, 'founding-26', 'founding-01', 'interconference'), found)
        self.assertIn((14, 'founding-01', 'founding-10', 'conference'), found)

    def test_exact_rerun_writes_nothing(self):
        self.assertEqual(self._create(), 'created')
        before = _batch(self.timeline_a)
        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(self._create(), 'unchanged')
        self.assertEqual(_writes(captured), [])
        self.assertEqual(_batch(self.timeline_a), before)

    def test_partial_assignment_set_is_refused_unchanged(self):
        self._create()
        TimelineScheduleSlotAssignment.objects.filter(
            timeline=self.timeline_a,
        ).order_by('id').first().delete()
        before = _batch(self.timeline_a)
        self.assertEqual(len(before[0]), 25)
        self._refuse_unchanged(before)

    def test_partial_fixture_set_is_refused_unchanged(self):
        self._create()
        game = ScheduleGame.objects.filter(
            timeline=self.timeline_a,
            season_year=1970,
        ).order_by('id').first()
        game.week_assignment.delete()
        game.delete()
        before = _batch(self.timeline_a)
        self.assertEqual(len(before[1]), 181)
        self.assertEqual(len(before[2]), 181)
        self._refuse_unchanged(before)

    def test_partial_week_set_is_refused_unchanged(self):
        self._create()
        ScheduleWeekAssignment.objects.filter(
            timeline=self.timeline_a,
            season_year=1970,
        ).order_by('id').first().delete()
        before = _batch(self.timeline_a)
        self.assertEqual(len(before[2]), 181)
        self.assertEqual(len(before[1]), 182)
        self._refuse_unchanged(before)

    def test_mismatched_slot_assignment_is_refused_unchanged(self):
        self._create()
        first, second = list(
            TimelineScheduleSlotAssignment.objects.filter(
                timeline=self.timeline_a,
            ).order_by('id')[:2]
        )
        spare = UniverseFranchise.objects.filter(release=self.release).exclude(
            pk__in=TimelineScheduleSlotAssignment.objects.filter(
                timeline=self.timeline_a,
            ).values('franchise_id'),
        ).order_by('placeholder_slug').first()
        first_franchise = first.franchise_id
        second_franchise = second.franchise_id
        TimelineScheduleSlotAssignment.objects.filter(pk=first.pk).update(
            franchise_id=spare.pk,
        )
        TimelineScheduleSlotAssignment.objects.filter(pk=second.pk).update(
            franchise_id=first_franchise,
        )
        TimelineScheduleSlotAssignment.objects.filter(pk=first.pk).update(
            franchise_id=second_franchise,
        )
        before = _batch(self.timeline_a)
        self.assertEqual(len(before[0]), 26)
        self._refuse_unchanged(before)

    def test_fixture_with_wrong_home_or_away_is_refused_unchanged(self):
        self._create()
        games = list(ScheduleGame.objects.filter(
            timeline=self.timeline_a,
            season_year=1970,
        ))
        pairs = {(game.home_id, game.away_id) for game in games}
        game = next(row for row in games if (row.away_id, row.home_id) not in pairs)
        ScheduleGame.objects.filter(pk=game.pk).update(
            home_id=game.away_id,
            away_id=game.home_id,
        )
        before = _batch(self.timeline_a)
        self._refuse_unchanged(before)

    def test_fixture_with_wrong_historical_provenance_is_refused_unchanged(self):
        self._create()
        first, second = list(ScheduleGame.objects.filter(
            timeline=self.timeline_a,
            season_year=1970,
        ).order_by('id')[:2])
        first_source = first.historical_game_id
        second_source = second.historical_game_id
        ScheduleGame.objects.filter(pk=first.pk).update(
            season_year=2002,
            historical_game_id=None,
        )
        ScheduleGame.objects.filter(pk=second.pk).update(
            historical_game_id=first_source,
        )
        ScheduleGame.objects.filter(pk=first.pk).update(
            season_year=1970,
            historical_game_id=second_source,
        )
        before = _batch(self.timeline_a)
        self._refuse_unchanged(before)

    def test_week_mismatch_is_refused_unchanged(self):
        self._create()
        week = ScheduleWeekAssignment.objects.filter(
            timeline=self.timeline_a,
            season_year=1970,
            week=1,
        ).order_by('id').first()
        ScheduleWeekAssignment.objects.filter(pk=week.pk).update(week=2)
        before = _batch(self.timeline_a)
        self._refuse_unchanged(before)

    def test_assignment_insert_failure_rolls_back_the_batch(self):
        self._raise_on_insert(
            'timelines.services.historical_1970.TimelineScheduleSlotAssignment.objects.bulk_create',
        )

    def test_fixture_insert_failure_rolls_back_the_batch(self):
        self._raise_on_insert(
            'timelines.services.historical_1970.ScheduleGame.objects.bulk_create',
        )

    def test_week_insert_failure_rolls_back_the_batch(self):
        self._raise_on_insert(
            'timelines.services.historical_1970.ScheduleWeekAssignment.objects.bulk_create',
        )

    def test_two_timelines_receive_isolated_rows_and_the_same_mapping(self):
        self.assertEqual(self._create(), 'created')
        self.assertEqual(
            generate_historical_timeline_schedule_1970(timeline_id=self.timeline_b.pk),
            'created',
        )
        map_a = dict(
            TimelineScheduleSlotAssignment.objects.filter(
                timeline=self.timeline_a,
            ).values_list('franchise_id', 'historical_slot_id')
        )
        map_b = dict(
            TimelineScheduleSlotAssignment.objects.filter(
                timeline=self.timeline_b,
            ).values_list('franchise_id', 'historical_slot_id')
        )
        self.assertEqual(map_a, map_b)
        self.assertEqual(len(map_a), 26)
        for model in (
            TimelineScheduleSlotAssignment,
            ScheduleGame,
            ScheduleWeekAssignment,
        ):
            ids_a = set(model.objects.filter(timeline=self.timeline_a).values_list('pk', flat=True))
            ids_b = set(model.objects.filter(timeline=self.timeline_b).values_list('pk', flat=True))
            self.assertEqual(len(ids_a), len(ids_b))
            self.assertTrue(ids_a.isdisjoint(ids_b))
        TimelineScheduleSlotAssignment.objects.filter(
            timeline=self.timeline_a,
        ).order_by('id').first().delete()
        before_b = _batch(self.timeline_b)
        with self.assertRaisesRegex(HistoricalTimelineScheduleRefused, 'will not delete'):
            self._create()
        self.assertEqual(_batch(self.timeline_b), before_b)
        self.assertEqual(
            TimelineScheduleSlotAssignment.objects.filter(timeline=self.timeline_a).count(),
            25,
        )

    def test_existing_project_and_timeline_rows_stay_unchanged(self):
        protected = (
            OpponentPairing,
            OpponentWeekAssignment,
            ScheduleBye,
            ScheduleGameResult,
            TimelineDivisionPlace,
            TimelinePriorFinish,
            TimelineSchedulingRank,
            TimelineScheduleSeed,
            TimelineSchedulingDraw,
            FranchiseSeasonMembership,
            UniverseFranchise,
            HistoricalScheduleSlot,
            HistoricalScheduleGame,
            HistoricalScheduleBye,
            UniverseRelease,
        )
        before = {model._meta.label: _dump(model.objects.all()) for model in protected}
        before_games = _dump(ScheduleGame.objects.exclude(season_year=1970))
        before_weeks = _dump(ScheduleWeekAssignment.objects.exclude(season_year=1970))
        before_flags = list(TimelineSeason.objects.order_by('id').values_list(
            'id',
            'phase',
            'regular_season_simulated',
            'postseason_complete',
            'season_year',
        ))
        self.assertGreater(OpponentPairing.objects.filter(season_year=1970).count(), 0)
        self.assertGreater(
            OpponentWeekAssignment.objects.filter(pairing__season_year=1970).count(),
            0,
        )
        self.assertGreater(ScheduleGame.objects.filter(season_year=2002).count(), 0)
        self.assertGreater(
            ScheduleWeekAssignment.objects.filter(season_year__gte=2021, season_year__lte=2026).count(),
            0,
        )
        self.assertGreater(ScheduleBye.objects.count(), 0)
        self.assertGreater(TimelineSchedulingRank.objects.count(), 0)
        self.assertEqual(ScheduleGameResult.objects.count(), 0)
        self.assertEqual(TimelineDivisionPlace.objects.count(), 0)
        release = UniverseRelease.objects.get(pk=self.release.pk)
        self.assertEqual(release.catalog_fingerprint, 'catalog-fixed')
        self.assertEqual(release.validation_fingerprint, 'validation-fixed')
        protected_tables = {model._meta.db_table for model in protected}
        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(self._create(), 'created')
        for sql in _writes(captured):
            self.assertFalse(
                any(table in sql for table in protected_tables),
                sql,
            )
        after = {model._meta.label: _dump(model.objects.all()) for model in protected}
        self.assertEqual(after, before)
        self.assertEqual(_dump(ScheduleGame.objects.exclude(season_year=1970)), before_games)
        self.assertEqual(
            _dump(ScheduleWeekAssignment.objects.exclude(season_year=1970)),
            before_weeks,
        )
        self.assertEqual(
            list(TimelineSeason.objects.order_by('id').values_list(
                'id',
                'phase',
                'regular_season_simulated',
                'postseason_complete',
                'season_year',
            )),
            before_flags,
        )
        self.assertEqual(
            UniverseRelease.objects.get(pk=self.release.pk).catalog_fingerprint,
            'catalog-fixed',
        )
        self.assertEqual(
            UniverseRelease.objects.get(pk=self.release.pk).validation_fingerprint,
            'validation-fixed',
        )

    def test_formula_timeline_without_a_1970_season_is_refused(self):
        before = _dump(ScheduleGame.objects.filter(timeline=self.scheduled))
        with self.assertRaisesRegex(
            HistoricalTimelineScheduleRefused,
            'The timeline has no season for this year.',
        ):
            generate_historical_timeline_schedule_1970(timeline_id=self.scheduled.pk)
        self.assertEqual(_dump(ScheduleGame.objects.filter(timeline=self.scheduled)), before)
        self.assertEqual(
            TimelineScheduleSlotAssignment.objects.filter(timeline=self.scheduled).count(),
            0,
        )

    def _create(self):
        return generate_historical_timeline_schedule_1970(timeline_id=self.timeline_a.pk)

    def _refuse_unchanged(self, before):
        with self.assertRaisesRegex(HistoricalTimelineScheduleRefused, 'will not delete'):
            self._create()
        self.assertEqual(_batch(self.timeline_a), before)
        self.assertIn('will not delete', DIFFER)

    def _raise_on_insert(self, target):
        with patch(target, side_effect=IntegrityError('forced')):
            with self.assertRaisesRegex(HistoricalTimelineScheduleRefused, 'rolled back'):
                self._create()
        self.assertEqual(TimelineScheduleSlotAssignment.objects.count(), 0)
        self.assertEqual(ScheduleGame.objects.filter(season_year=1970).count(), 0)
        self.assertEqual(ScheduleWeekAssignment.objects.filter(season_year=1970).count(), 0)
        self.assertIn('rolled back', INSERT_FAILED)


def _batch(timeline):
    return (
        _dump(TimelineScheduleSlotAssignment.objects.filter(timeline=timeline)),
        _dump(ScheduleGame.objects.filter(timeline=timeline, season_year=1970)),
        _dump(ScheduleWeekAssignment.objects.filter(timeline=timeline, season_year=1970)),
    )


def _timeline_1970(release, name, profiles):
    timeline = Timeline(
        release=release,
        name=name,
        start_season_year=1970,
        current_season_year=1970,
        season_index=1,
        phase=Timeline.Phase.DIRECTION,
        status=Timeline.Status.ACTIVE,
    )
    timeline.save()
    profile = next(item for item in profiles if item.first_year <= 1970 <= item.last_year)
    TimelineSeason(
        timeline=timeline,
        profile=profile,
        season_year=1970,
        season_index=1,
        phase=Timeline.Phase.DIRECTION,
    ).save()
    return timeline


def _timeline(release, name, profiles, start_year, simulated_2001):
    timeline = Timeline(
        release=release,
        name=name,
        start_season_year=start_year,
        current_season_year=2002,
        season_index=2002 - start_year + 1,
        phase=Timeline.Phase.DIRECTION,
        status=Timeline.Status.ACTIVE,
    )
    timeline.save()
    years = [2002]
    if start_year <= 2001:
        years.insert(0, 2001)
    for year in years:
        profile = next(item for item in profiles if item.first_year <= year <= item.last_year)
        TimelineSeason(
            timeline=timeline,
            profile=profile,
            season_year=year,
            season_index=year - start_year + 1,
            phase=Timeline.Phase.DIRECTION,
            setup_complete=year == 2001 and simulated_2001,
            directives_locked=year == 2001 and simulated_2001,
            regular_season_simulated=year == 2001 and simulated_2001,
        ).save()
    return timeline


def _store_calendar_sample(timeline, release, profiles):
    profile = next(item for item in profiles if item.first_year <= 2021 <= item.last_year)
    TimelineSeason(
        timeline=timeline,
        profile=profile,
        season_year=2021,
        season_index=2021 - timeline.start_season_year + 1,
        phase=Timeline.Phase.DIRECTION,
    ).save()
    home = UniverseFranchise.objects.get(release=release, placeholder_slug='founding-01')
    away = UniverseFranchise.objects.get(release=release, placeholder_slug='founding-13')
    bye = UniverseFranchise.objects.get(release=release, placeholder_slug='founding-02')
    game = ScheduleGame(
        timeline=timeline,
        season_year=2021,
        home=home,
        away=away,
        kind=ScheduleGame.Kind.REGULAR,
        category=ScheduleGame.Category.INTERCONFERENCE,
    )
    game.save()
    with writing_calendar_rows():
        ScheduleWeekAssignment(
            timeline=timeline,
            game=game,
            season_year=2021,
            week=1,
        ).save()
        ScheduleBye(
            timeline=timeline,
            franchise=bye,
            season_year=2021,
            week=2,
        ).save()


def _default_finishes(release):
    grouped = {}
    memberships = FranchiseSeasonMembership.objects.filter(
        franchise__release=release,
        season_year=2001,
    ).select_related('franchise')
    for row in memberships:
        grouped.setdefault((row.conference_slug, row.division_slug), []).append(row)
    finishes = []
    for members in grouped.values():
        ordered = sorted(members, key=lambda row: row.franchise.placeholder_slug)
        for place, row in enumerate(ordered, start=1):
            finishes.append(Finish(row.franchise_id, 16 - place, place, 0, place))
    return finishes
