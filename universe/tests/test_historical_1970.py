from collections import Counter, defaultdict
from io import StringIO
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection
from django.test import SimpleTestCase, TestCase
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
    TimelineSchedulingRank,
    TimelineSeason,
)
from timelines.services.prior_finish import (
    Finish,
    derive_scheduling_ranks,
    store_prior_finishes,
)
from timelines.services.schedule_2002 import generate_timeline_schedule
from universe.generation.historical_1970 import (
    HistoricalScheduleError,
    load_historical_schedule_1970,
)
from universe.generation.historical_1970_data import (
    GAMES,
    SEASON_YEAR,
    SLOTS,
    SOURCE_CITATION,
)
from universe.generation.opening_players import y1970_values
from universe.generation.opponents import (
    CENTRAL,
    EAST,
    WEST,
    Seat,
    build_season,
    generate_opponent_pairings,
)
from universe.generation.prospects import later_profile_values
from universe.generation.schedule import generate_schedule_release
from universe.generation.weeks import WeekGame, generate_opponent_weeks, schedule_season
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


PROJECT_SEATS = (
    ('founding-01', 1, CENTRAL, 1),
    ('founding-02', 1, CENTRAL, 2),
    ('founding-03', 1, CENTRAL, 3),
    ('founding-04', 1, CENTRAL, 4),
    ('founding-05', 1, WEST, 1),
    ('founding-06', 1, WEST, 2),
    ('founding-07', 1, WEST, 3),
    ('founding-08', 1, WEST, 4),
    ('founding-09', 1, EAST, 1),
    ('founding-10', 1, EAST, 2),
    ('founding-11', 1, EAST, 3),
    ('founding-12', 1, EAST, 4),
    ('founding-25', 1, EAST, 5),
    ('founding-13', 2, CENTRAL, 1),
    ('founding-14', 2, CENTRAL, 2),
    ('founding-15', 2, CENTRAL, 3),
    ('founding-16', 2, CENTRAL, 4),
    ('founding-17', 2, WEST, 1),
    ('founding-18', 2, WEST, 2),
    ('founding-19', 2, WEST, 3),
    ('founding-20', 2, WEST, 4),
    ('founding-21', 2, EAST, 1),
    ('founding-22', 2, EAST, 2),
    ('founding-23', 2, EAST, 3),
    ('founding-24', 2, EAST, 4),
    ('founding-26', 2, EAST, 5),
)


def _home_masks(games):
    masks = defaultdict(int)
    seen = set()
    for home, away, week in games:
        masks[home] |= 1 << (week - 1)
        masks.setdefault(away, 0)
        seen.add(home)
        seen.add(away)
    return seen, set(masks.values())


class HistoricalTemplateComparisonTests(SimpleTestCase):
    def test_home_week_masks_share_no_pattern_with_the_project_schedule(self):
        """No permutation preserves opponents, sites, and weeks.

        A directed week-labeled schedule sends each club to one home-week
        mask. The historical masks and the project masks are two sets of 26
        patterns, and those sets are disjoint. A one-to-one relabeling would
        need at least one shared mask. This is the audited evidence. It is
        not a search of every permutation.

        The opening alignment used for the direct comparison is conference-1
        with the AFC, conference-2 with the NFC, d1 with Central, d2 with
        West, d3 with East, and membership slot order with the template slot
        number. That alignment does not assign a franchise to a slot.
        """
        provenance = {
            (1 if conference == 'afc' else 2, division, number): club
            for conference, division, number, club in SLOTS
        }
        self.assertEqual(len(provenance), 26)
        historical = {(home, away, week) for week, home, away in GAMES}
        _clubs, historical_masks = _home_masks(
            (home, away, week) for home, away, week in historical
        )
        seats = tuple(
            Seat(slug, conference, role, slot)
            for slug, conference, role, slot in PROJECT_SEATS
        )
        pairings = build_season(1970, seats, 14)
        week_games = tuple(
            WeekGame(
                (pairing.home_franchise_id, pairing.away_franchise_id),
                pairing.home_franchise_id,
                pairing.away_franchise_id,
            )
            for pairing in pairings
        )
        weeks = schedule_season(week_games)
        self.assertIsNotNone(weeks)
        aligned = set()
        project_games = []
        for pairing in pairings:
            home_seat = next(
                row for row in PROJECT_SEATS if row[0] == pairing.home_franchise_id
            )
            away_seat = next(
                row for row in PROJECT_SEATS if row[0] == pairing.away_franchise_id
            )
            week = weeks[(pairing.home_franchise_id, pairing.away_franchise_id)]
            project_games.append((home_seat[0], away_seat[0], week))
            aligned.add((
                provenance[(home_seat[1], home_seat[2], home_seat[3])],
                provenance[(away_seat[1], away_seat[2], away_seat[3])],
                week,
            ))
        _project_clubs, project_masks = _home_masks(project_games)
        self.assertEqual(len(historical_masks), 26)
        self.assertEqual(len(project_masks), 26)
        self.assertTrue(historical_masks.isdisjoint(project_masks))
        self.assertNotEqual(aligned, historical)
        self.assertNotIn(('Baltimore Colts', 'Dallas Cowboys', 1), historical)
        self.assertFalse(any(
            {home, away} == {'Baltimore Colts', 'Dallas Cowboys'}
            for home, away, _week in historical
        ))


class HistoricalTemplateTests(TestCase):
    def test_import_stores_the_1970_regular_season(self):
        self.assertEqual(load_historical_schedule_1970(), 'created')
        slots = list(HistoricalScheduleSlot.objects.order_by('id'))
        games = list(
            HistoricalScheduleGame.objects.select_related('home_slot', 'away_slot').order_by('id')
        )
        self.assertEqual(len(slots), 26)
        self.assertEqual(len(games), 182)
        self.assertEqual(HistoricalScheduleBye.objects.count(), 0)
        self.assertEqual(
            [(row.conference_role, row.division_role, row.slot_number, row.provenance_club) for row in slots],
            list(SLOTS),
        )
        self.assertTrue(all(row.season_year == 1970 for row in slots))
        self.assertTrue(all(row.opening_basis == HistoricalScheduleSlot.OpeningBasis.MERGER_PLACEMENT for row in slots))
        self.assertTrue(all(row.prior_place is None for row in slots))
        self.assertTrue(all(row.prior_conference_role is None for row in slots))
        self.assertTrue(all(row.prior_division_role is None for row in slots))
        self.assertTrue(all(row.source_citation == SOURCE_CITATION for row in slots))
        self.assertIn('pro-football-reference.com/years/1970/games.htm', SOURCE_CITATION)
        self.assertEqual(
            [(row.week_played, row.home_slot.provenance_club, row.away_slot.provenance_club) for row in games],
            list(GAMES),
        )
        self.assertTrue(all(row.season_year == SEASON_YEAR for row in games))
        self.assertTrue(all(row.status == HistoricalScheduleGame.Status.PLAYED for row in games))
        self.assertTrue(all(row.originally_scheduled_week is None for row in games))
        self.assertTrue(all(row.source_citation == SOURCE_CITATION for row in games))
        self.assertTrue(all(row.home_slot.season_year == 1970 and row.away_slot.season_year == 1970 for row in games))

        played = Counter()
        homes = Counter()
        aways = Counter()
        weeks = Counter()
        week_slots = defaultdict(set)
        directed = set()
        meetings = defaultdict(int)
        for row in games:
            self.assertNotEqual(row.home_slot_id, row.away_slot_id)
            self.assertGreaterEqual(row.week_played, 1)
            self.assertLessEqual(row.week_played, 14)
            directed.add((row.home_slot_id, row.away_slot_id))
            meetings[frozenset((row.home_slot_id, row.away_slot_id))] += 1
            weeks[row.week_played] += 1
            for slot_id, is_home in ((row.home_slot_id, True), (row.away_slot_id, False)):
                self.assertNotIn(slot_id, week_slots[row.week_played])
                week_slots[row.week_played].add(slot_id)
                played[slot_id] += 1
                if is_home:
                    homes[slot_id] += 1
                else:
                    aways[slot_id] += 1
        self.assertEqual(set(weeks), set(range(1, 15)))
        self.assertTrue(all(count == 13 for count in weeks.values()))
        self.assertEqual(len(directed), 182)
        self.assertTrue(all(played[row.id] == 14 for row in slots))
        self.assertTrue(all(homes[row.id] == 7 and aways[row.id] == 7 for row in slots))
        self.assertEqual(len(meetings), 138)
        self.assertEqual(Counter(meetings.values()), Counter({1: 94, 2: 44}))
        self.assertEqual(
            self._game('Cleveland Browns', 'New York Jets'),
            1,
        )
        self.assertEqual(self._game('Philadelphia Eagles', 'Dallas Cowboys'), 1)
        self.assertEqual(self._game('San Diego Chargers', 'Oakland Raiders'), 2)
        self.assertEqual(self._game('Kansas City Chiefs', 'Oakland Raiders'), 7)
        self.assertEqual(self._game('Denver Broncos', 'San Diego Chargers'), 13)
        self.assertFalse(HistoricalScheduleGame.objects.filter(
            home_slot__provenance_club='Baltimore Colts',
            away_slot__provenance_club='Dallas Cowboys',
        ).exists())
        self.assertFalse(HistoricalScheduleGame.objects.filter(
            home_slot__provenance_club='Dallas Cowboys',
            away_slot__provenance_club='Baltimore Colts',
        ).exists())
        self.assertEqual(UniverseFranchise.objects.count(), 0)

    def test_exact_rerun_writes_nothing(self):
        self.assertEqual(load_historical_schedule_1970(), 'created')
        before = self._identity()
        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(load_historical_schedule_1970(), 'unchanged')
        writes = [
            query['sql'] for query in captured.captured_queries
            if query['sql'].lstrip()[:6].upper() in {'INSERT', 'UPDATE', 'DELETE'}
        ]
        self.assertEqual(writes, [])
        self.assertEqual(self._identity(), before)

    def test_a_partial_set_is_left_unchanged(self):
        load_historical_schedule_1970()
        game = HistoricalScheduleGame.objects.order_by('id').first()
        table = connection.ops.quote_name(HistoricalScheduleGame._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(f'DELETE FROM {table} WHERE id = %s', [game.id])
        before = self._identity()
        with self.assertRaisesRegex(HistoricalScheduleError, 'will not delete'):
            load_historical_schedule_1970()
        self.assertEqual(self._identity(), before)
        self.assertEqual(HistoricalScheduleGame.objects.count(), 181)
        self.assertFalse(HistoricalScheduleGame.objects.filter(pk=game.id).exists())

    def test_a_mismatched_set_is_left_unchanged(self):
        load_historical_schedule_1970()
        game = HistoricalScheduleGame.objects.get(
            home_slot__provenance_club='Cleveland Browns',
            away_slot__provenance_club='New York Jets',
        )
        table = connection.ops.quote_name(HistoricalScheduleGame._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(
                f'UPDATE {table} SET week_played = %s WHERE id = %s',
                [9, game.id],
            )
        game.refresh_from_db()
        self.assertEqual(game.week_played, 9)
        before = self._identity()
        with self.assertRaisesRegex(HistoricalScheduleError, 'will not delete'):
            load_historical_schedule_1970()
        self.assertEqual(self._identity(), before)
        game.refresh_from_db()
        self.assertEqual(game.week_played, 9)

    def test_an_extra_bye_is_left_unchanged(self):
        load_historical_schedule_1970()
        slot = HistoricalScheduleSlot.objects.get(provenance_club='Baltimore Colts')
        bye = HistoricalScheduleBye(
            season_year=1970,
            slot=slot,
            week=1,
            source_citation=SOURCE_CITATION,
        )
        bye.save()
        before = self._identity()
        with self.assertRaisesRegex(HistoricalScheduleError, 'will not delete'):
            load_historical_schedule_1970()
        self.assertEqual(self._identity(), before)
        self.assertTrue(HistoricalScheduleBye.objects.filter(pk=bye.pk).exists())

    def test_a_failed_insert_rolls_back_every_template_row(self):
        def fail_games(rows, **kwargs):
            raise IntegrityError('forced')

        with patch(
            'universe.generation.historical_1970.HistoricalScheduleGame.objects.bulk_create',
            fail_games,
        ):
            with self.assertRaisesRegex(HistoricalScheduleError, 'rolled back'):
                load_historical_schedule_1970()
        self.assertEqual(HistoricalScheduleSlot.objects.count(), 0)
        self.assertEqual(HistoricalScheduleGame.objects.count(), 0)
        self.assertEqual(HistoricalScheduleBye.objects.count(), 0)

    def test_invalid_source_inserts_nothing(self):
        with patch('universe.generation.historical_1970.GAMES', GAMES[:-1]):
            with self.assertRaisesRegex(HistoricalScheduleError, '182'):
                load_historical_schedule_1970()
        self.assertEqual(HistoricalScheduleSlot.objects.count(), 0)
        self.assertEqual(HistoricalScheduleGame.objects.count(), 0)

    def test_stored_rows_cannot_be_changed_through_the_model(self):
        load_historical_schedule_1970()
        slot = HistoricalScheduleSlot.objects.order_by('id').first()
        slot.provenance_club = 'Not a franchise'
        with self.assertRaisesMessage(ValidationError, 'written once'):
            slot.save()
        with self.assertRaisesMessage(ValidationError, 'cannot be deleted'):
            slot.delete()
        with self.assertRaisesMessage(ValidationError, 'written once'):
            HistoricalScheduleSlot.objects.update(provenance_club='Not a franchise')
        with self.assertRaisesMessage(ValidationError, 'cannot be deleted'):
            HistoricalScheduleGame.objects.all().delete()
        slot.refresh_from_db()
        self.assertNotEqual(slot.provenance_club, 'Not a franchise')

    def test_command_loads_once_and_then_changes_nothing(self):
        stdout = StringIO()
        call_command('load_historical_schedule_1970', stdout=stdout)
        self.assertIn('created', stdout.getvalue())
        self.assertEqual(HistoricalScheduleGame.objects.count(), 182)
        again = StringIO()
        call_command('load_historical_schedule_1970', stdout=again)
        self.assertIn('unchanged', again.getvalue())
        with patch(
            'universe.management.commands.load_historical_schedule_1970.load_historical_schedule_1970',
            side_effect=HistoricalScheduleError('refused'),
        ):
            with self.assertRaises(CommandError):
                call_command('load_historical_schedule_1970')

    def _game(self, home, away):
        return HistoricalScheduleGame.objects.get(
            home_slot__provenance_club=home,
            away_slot__provenance_club=away,
        ).week_played

    def _identity(self):
        return (
            list(HistoricalScheduleSlot.objects.order_by('id').values_list(
                'id',
                'created_at',
                'season_year',
                'conference_role',
                'division_role',
                'slot_number',
                'provenance_club',
                'opening_basis',
                'prior_place',
                'prior_conference_role',
                'prior_division_role',
                'source_citation',
            )),
            list(HistoricalScheduleGame.objects.order_by('id').values_list(
                'id',
                'created_at',
                'season_year',
                'home_slot_id',
                'away_slot_id',
                'week_played',
                'originally_scheduled_week',
                'status',
                'source_citation',
            )),
            list(HistoricalScheduleBye.objects.order_by('id').values_list(
                'id',
                'created_at',
                'season_year',
                'slot_id',
                'week',
                'source_citation',
            )),
        )


class HistoricalTemplatePreservationTests(TestCase):
    def test_import_leaves_project_and_timeline_schedules_unchanged(self):
        league = League(name='Template League', slug='template-league', tier=1, active=True)
        league.save()
        release = UniverseRelease(
            slug='historical-1970',
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
        self.assertEqual(generate_schedule_release(league.id, 1970), 'created')
        release.refresh_from_db()
        profiles = [EraRuleProfile(release=release, **y1970_values())]
        profiles[0].save()
        for values in later_profile_values():
            if values['profile_key'] in {'y1974', 'y1976', 'y1999', 'y2002'}:
                profile = EraRuleProfile(release=release, **values)
                profile.save()
                profiles.append(profile)
        self.assertEqual(generate_opponent_pairings(league.id, 1970), 'created')
        self.assertEqual(generate_opponent_weeks(league.id, 1970), 'created')
        release.catalog_fingerprint = 'catalog-fixed'
        release.validation_fingerprint = 'validation-fixed'
        release.status = UniverseRelease.Status.PUBLISHED
        release.published_at = timezone.now()
        release.save()
        played = _timeline(release, 'played 2001', profiles, 2001, simulated_2001=True)
        store_prior_finishes(timeline_id=played.pk, finishes=_default_finishes(release))
        derive_scheduling_ranks(timeline_id=played.pk)
        scheduled = _timeline(release, 'opens in 2002', profiles, 2002, simulated_2001=False)
        self.assertEqual(
            generate_timeline_schedule(timeline_id=scheduled.pk, season_year=2002),
            'created',
        )
        before = _project_snapshot(release)
        self.assertGreater(before['pairings'][0], 0)
        self.assertGreater(before['weeks'][0], 0)
        self.assertGreater(before['games'][0], 0)
        self.assertGreater(before['finishes'][0], 0)
        self.assertGreater(before['ranks'][0], 0)
        self.assertEqual(before['results'][0], 0)
        self.assertEqual(before['places'][0], 0)
        self.assertEqual(before['calendar_weeks'][0], 0)
        self.assertEqual(before['byes'][0], 0)
        forbidden = {
            OpponentPairing._meta.db_table,
            OpponentWeekAssignment._meta.db_table,
            ScheduleGame._meta.db_table,
            ScheduleWeekAssignment._meta.db_table,
            ScheduleBye._meta.db_table,
            ScheduleGameResult._meta.db_table,
            TimelineDivisionPlace._meta.db_table,
            TimelinePriorFinish._meta.db_table,
            TimelineSchedulingRank._meta.db_table,
            FranchiseSeasonMembership._meta.db_table,
            UniverseFranchise._meta.db_table,
            UniverseRelease._meta.db_table,
        }
        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(load_historical_schedule_1970(), 'created')
        writes = [
            query['sql'] for query in captured.captured_queries
            if query['sql'].lstrip()[:6].upper() in {'INSERT', 'UPDATE', 'DELETE'}
        ]
        self.assertTrue(writes)
        for sql in writes:
            for table in forbidden:
                self.assertNotIn(table, sql)
        self.assertEqual(_project_snapshot(release), before)
        self.assertEqual(HistoricalScheduleGame.objects.count(), 182)
        self.assertFalse(UniverseFranchise.objects.filter(
            display_name__in=[club for _conference, _division, _number, club in SLOTS],
        ).exists())
        self.assertFalse(UniverseFranchise.objects.filter(
            placeholder_slug__in=[club for _conference, _division, _number, club in SLOTS],
        ).exists())


def _project_snapshot(release):
    release.refresh_from_db()
    return {
        'fingerprints': (
            release.catalog_fingerprint,
            release.validation_fingerprint,
            release.status,
            release.seed,
            release.generator_version,
            release.expansion_schedule_version,
            release.published_at,
        ),
        'franchises': (
            UniverseFranchise.objects.filter(release=release).count(),
            list(UniverseFranchise.objects.filter(release=release).order_by('id').values_list(
                'id',
                'placeholder_slug',
                'display_name',
                'origin',
                'first_active_season',
                'final_active_season',
            )),
        ),
        'memberships': (
            FranchiseSeasonMembership.objects.filter(franchise__release=release).count(),
            list(FranchiseSeasonMembership.objects.filter(franchise__release=release).order_by('id').values_list(
                'id',
                'franchise_id',
                'season_year',
                'conference_slug',
                'division_slug',
                'slot_order',
            )),
        ),
        'pairings': (
            OpponentPairing.objects.filter(release=release).count(),
            list(OpponentPairing.objects.filter(release=release).order_by('id').values_list(
                'id',
                'created_at',
                'season_year',
                'home_franchise_id',
                'away_franchise_id',
                'kind',
            )),
        ),
        'weeks': (
            OpponentWeekAssignment.objects.filter(release=release).count(),
            list(OpponentWeekAssignment.objects.filter(release=release).order_by('id').values_list(
                'id',
                'created_at',
                'release_id',
                'pairing_id',
                'week',
            )),
        ),
        'games': (
            ScheduleGame.objects.count(),
            list(ScheduleGame.objects.order_by('id').values_list(
                'id',
                'created_at',
                'timeline_id',
                'season_year',
                'home_id',
                'away_id',
                'kind',
                'category',
            )),
        ),
        'calendar_weeks': (
            ScheduleWeekAssignment.objects.count(),
            list(ScheduleWeekAssignment.objects.order_by('id').values_list(
                'id', 'created_at', 'timeline_id', 'game_id', 'season_year', 'week',
            )),
        ),
        'byes': (
            ScheduleBye.objects.count(),
            list(ScheduleBye.objects.order_by('id').values_list(
                'id', 'created_at', 'timeline_id', 'franchise_id', 'season_year', 'week',
            )),
        ),
        'results': (
            ScheduleGameResult.objects.count(),
            list(ScheduleGameResult.objects.order_by('id').values_list(
                'id',
                'created_at',
                'timeline_id',
                'game_id',
                'season_year',
                'home_score',
                'away_score',
            )),
        ),
        'places': (
            TimelineDivisionPlace.objects.count(),
            list(TimelineDivisionPlace.objects.order_by('id').values_list(
                'id',
                'created_at',
                'timeline_id',
                'franchise_id',
                'season_year',
                'division_place',
                'conference_slug',
                'division_slug',
                'resolved_by',
            )),
        ),
        'finishes': (
            TimelinePriorFinish.objects.count(),
            list(TimelinePriorFinish.objects.order_by('id').values_list(
                'id',
                'created_at',
                'timeline_id',
                'franchise_id',
                'season_year',
                'wins',
                'losses',
                'ties',
                'division_place',
                'conference_slug',
                'division_slug',
            )),
        ),
        'ranks': (
            TimelineSchedulingRank.objects.count(),
            list(TimelineSchedulingRank.objects.order_by('id').values_list(
                'id',
                'created_at',
                'timeline_id',
                'franchise_id',
                'season_year',
                'scheduling_rank',
                'conference_slug',
                'division_slug',
            )),
        ),
    }


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
        profile = next(
            item for item in profiles if item.first_year <= year <= item.last_year
        )
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
