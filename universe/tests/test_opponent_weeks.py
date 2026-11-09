from collections import defaultdict
from io import StringIO
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection, transaction
from django.db.models import ProtectedError
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from leagues.models import League
from universe.generation.opening_players import y1970_values
from universe.generation.opponents import generate_opponent_pairings
from universe.generation.prospects import later_profile_values
from universe.generation.schedule import ScheduleGenerationError, generate_schedule_release
from universe.generation.weeks import (
    WEEK_COUNT,
    OpponentWeekError,
    WeekGame,
    generate_opponent_weeks,
    schedule_season,
)
from universe.models import OpponentPairing, OpponentWeekAssignment, UniverseRelease
from universe.rules import EraRuleProfile


SEED = 1970
OTHER_SEED = 1971
PAIRING_COUNT = 6 * 182 + 2 * 196


def _k4():
    clubs = ('a', 'b', 'c', 'd')
    games = []
    key = 1
    for index, home in enumerate(clubs):
        for away in clubs[index + 1:]:
            games.append(WeekGame(key, home, away))
            key += 1
    return tuple(games)


def _petersen():
    names = tuple(f'p{index}' for index in range(10))
    edges = (
        (0, 1), (1, 2), (2, 3), (3, 4), (4, 0),
        (0, 5), (1, 6), (2, 7), (3, 8), (4, 9),
        (5, 7), (7, 9), (9, 6), (6, 8), (8, 5),
    )
    return tuple(WeekGame(index, names[home], names[away]) for index, (home, away) in enumerate(edges))


class WeekSearchTests(SimpleTestCase):
    def test_the_same_games_keep_the_same_weeks_in_either_order(self):
        games = _k4()
        forward = schedule_season(games, weeks=3)
        backward = schedule_season(tuple(reversed(games)), weeks=3)

        self.assertEqual(forward, backward)
        self.assertEqual(set(forward), {game.key for game in games})
        used = set()
        for game in games:
            week = forward[game.key]
            self.assertIn(week, {1, 2, 3})
            for club in (game.home, game.away):
                self.assertNotIn((club, week), used)
                used.add((club, week))
        self.assertEqual(len(used), 12)

    def test_opposite_directions_fill_separate_weeks(self):
        games = (
            WeekGame(1, 'alpha', 'beta'),
            WeekGame(2, 'beta', 'alpha'),
        )

        assignment = schedule_season(games, weeks=2)

        self.assertEqual(set(assignment.values()), {1, 2})

    def test_an_unschedulable_graph_has_no_assignment(self):
        self.assertIsNone(schedule_season(_petersen(), weeks=3))
        self.assertIsNone(schedule_season(tuple(reversed(_petersen())), weeks=3))


class OpponentWeekTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.league = League(name='Week League', slug='week-league', tier=1, active=True)
        cls.league.save()
        cls.release = UniverseRelease(
            slug='weeks-1970',
            root_league=cls.league,
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
        cls.release.save()
        generate_schedule_release(cls.league.id, SEED)
        cls.release.refresh_from_db()
        EraRuleProfile(release=cls.release, **y1970_values()).save()
        for values in later_profile_values():
            if values['profile_key'] in {'y1974', 'y1976'}:
                EraRuleProfile(release=cls.release, **values).save()
        generate_opponent_pairings(cls.league.id, SEED)

    def pairing_rows(self):
        return list(
            OpponentPairing.objects.filter(release=self.release)
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

    def test_every_pairing_is_played_once_per_club_per_week(self):
        before = self.pairing_rows()
        fingerprint = (
            self.release.catalog_fingerprint,
            self.release.validation_fingerprint,
            self.release.expansion_schedule_version,
            self.release.seed,
        )

        self.assertEqual(generate_opponent_weeks(self.league.id, SEED), 'created')

        rows = list(
            OpponentWeekAssignment.objects.filter(release=self.release)
            .select_related('pairing')
        )
        self.assertEqual(len(rows), PAIRING_COUNT)
        self.assertEqual(len({row.pairing_id for row in rows}), PAIRING_COUNT)
        by_season = defaultdict(list)
        for row in rows:
            self.assertEqual(row.pairing.release_id, self.release.pk)
            by_season[row.pairing.season_year].append(row)
        self.assertEqual(set(by_season), set(range(1970, 1978)))
        for season_year, season_rows in by_season.items():
            clubs = set()
            weeks = defaultdict(set)
            for row in season_rows:
                self.assertGreaterEqual(row.week, 1)
                self.assertLessEqual(row.week, WEEK_COUNT)
                for club_id in (
                    row.pairing.home_franchise_id,
                    row.pairing.away_franchise_id,
                ):
                    self.assertNotIn(club_id, weeks[row.week])
                    weeks[row.week].add(club_id)
                    clubs.add(club_id)
            self.assertEqual(set(weeks), set(range(1, WEEK_COUNT + 1)))
            for present in weeks.values():
                self.assertEqual(present, clubs)
            expected = 182 if season_year < 1976 else 196
            self.assertEqual(len(season_rows), expected)
            self.assertEqual(len(clubs), 26 if season_year < 1976 else 28)
        self.assertEqual(self.pairing_rows(), before)
        self.release.refresh_from_db()
        self.assertEqual(
            (
                self.release.catalog_fingerprint,
                self.release.validation_fingerprint,
                self.release.expansion_schedule_version,
                self.release.seed,
            ),
            fingerprint,
        )

    def test_exact_rerun_writes_nothing_and_repeats_the_weeks(self):
        self.assertEqual(generate_opponent_weeks(self.league.id, SEED), 'created')
        before = list(
            OpponentWeekAssignment.objects.filter(release=self.release)
            .order_by('id')
            .values_list('id', 'created_at', 'pairing_id', 'week')
        )
        pairings = self.pairing_rows()
        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(generate_opponent_weeks(self.league.id, SEED), 'unchanged')
        writes = [
            query['sql'] for query in captured.captured_queries
            if query['sql'].lstrip()[:6].upper() in {'INSERT', 'UPDATE', 'DELETE'}
        ]
        self.assertEqual(writes, [])
        after = list(
            OpponentWeekAssignment.objects.filter(release=self.release)
            .order_by('id')
            .values_list('id', 'created_at', 'pairing_id', 'week')
        )
        self.assertEqual(before, after)
        self.assertEqual(self.pairing_rows(), pairings)

        OpponentWeekAssignment.objects.filter(release=self.release).delete()
        self.assertEqual(generate_opponent_weeks(self.league.id, SEED), 'created')
        restored = dict(
            OpponentWeekAssignment.objects.filter(release=self.release)
            .values_list('pairing_id', 'week')
        )
        self.assertEqual(restored, {pairing_id: week for _pk, _created, pairing_id, week in before})
        self.assertEqual(self.pairing_rows(), pairings)

    def test_a_mismatched_or_partial_set_fails_closed(self):
        generate_opponent_weeks(self.league.id, SEED)
        row = OpponentWeekAssignment.objects.filter(
            release=self.release,
            pairing__season_year=1970,
        ).order_by('id').first()
        replacement = 1 if row.week != 1 else 2
        OpponentWeekAssignment.objects.filter(pk=row.pk).update(week=replacement)
        with self.assertRaisesRegex(OpponentWeekError, 'will not delete'):
            generate_opponent_weeks(self.league.id, SEED)
        self.assertEqual(
            OpponentWeekAssignment.objects.get(pk=row.pk).week,
            replacement,
        )
        self.assertEqual(OpponentPairing.objects.filter(release=self.release).count(), PAIRING_COUNT)

        OpponentWeekAssignment.objects.filter(pk=row.pk).update(week=row.week)
        OpponentWeekAssignment.objects.filter(pairing__season_year=1974).delete()
        with self.assertRaisesRegex(OpponentWeekError, 'will not delete'):
            generate_opponent_weeks(self.league.id, SEED)
        self.assertFalse(
            OpponentWeekAssignment.objects.filter(pairing__season_year=1974).exists()
        )
        self.assertEqual(
            OpponentWeekAssignment.objects.filter(pairing__season_year=1970).count(),
            182,
        )

    def test_an_impossible_season_and_a_bad_insert_store_nothing(self):
        pairings = self.pairing_rows()
        with patch('universe.generation.weeks.schedule_season', return_value=None):
            with self.assertRaisesRegex(OpponentWeekError, 'cannot be assigned'):
                generate_opponent_weeks(self.league.id, SEED)
        self.assertEqual(OpponentWeekAssignment.objects.count(), 0)
        self.assertEqual(self.pairing_rows(), pairings)

        real = OpponentWeekAssignment.objects.bulk_create
        calls = {'n': 0}

        def fail_second_season(rows, **kwargs):
            calls['n'] += 1
            if calls['n'] == 2:
                raise IntegrityError('forced')
            return real(rows, **kwargs)

        with patch(
            'universe.generation.weeks.OpponentWeekAssignment.objects.bulk_create',
            fail_second_season,
        ):
            with self.assertRaisesRegex(OpponentWeekError, 'rolled back'):
                generate_opponent_weeks(self.league.id, SEED)
        self.assertEqual(calls['n'], 2)
        self.assertEqual(OpponentWeekAssignment.objects.count(), 0)
        self.assertEqual(self.pairing_rows(), pairings)
        self.release.refresh_from_db()
        self.assertEqual(self.release.catalog_fingerprint, 'preexisting')

    def test_missing_pairings_wrong_length_and_publication_write_nothing(self):
        OpponentPairing.objects.filter(release=self.release).delete()
        with self.assertRaisesRegex(OpponentWeekError, 'no stored opponent pairings'):
            generate_opponent_weeks(self.league.id, SEED)
        self.assertEqual(OpponentWeekAssignment.objects.count(), 0)

        generate_opponent_pairings(self.league.id, SEED)
        EraRuleProfile.objects.filter(release=self.release, profile_key='y1976').update(
            schedule_length=16,
        )
        with self.assertRaisesRegex(OpponentWeekError, 'schedule_length'):
            generate_opponent_weeks(self.league.id, SEED)
        self.assertEqual(OpponentWeekAssignment.objects.count(), 0)
        EraRuleProfile.objects.filter(release=self.release, profile_key='y1976').update(
            schedule_length=14,
        )

        UniverseRelease.objects.filter(pk=self.release.pk).update(
            status=UniverseRelease.Status.PUBLISHED,
            catalog_fingerprint='a' * 64,
            validation_fingerprint='b' * 64,
            published_at=timezone.now(),
        )
        with self.assertRaisesRegex(OpponentWeekError, 'published'):
            generate_opponent_weeks(self.league.id, SEED)
        self.assertEqual(OpponentWeekAssignment.objects.count(), 0)
        self.assertEqual(OpponentPairing.objects.filter(release=self.release).count(), PAIRING_COUNT)

    def test_schedule_rebuild_leaves_weeks_and_pairings_in_place(self):
        generate_opponent_weeks(self.league.id, SEED)
        with self.assertRaisesRegex(ScheduleGenerationError, 'Opponent week assignments'):
            generate_schedule_release(self.league.id, OTHER_SEED)
        self.assertEqual(OpponentWeekAssignment.objects.filter(release=self.release).count(), PAIRING_COUNT)
        self.assertEqual(OpponentPairing.objects.filter(release=self.release).count(), PAIRING_COUNT)
        self.assertEqual(self.release.franchises.count(), 32)

    def test_model_rules_reject_a_bad_week_and_protect_the_pairing(self):
        games = list(
            OpponentPairing.objects.filter(release=self.release, season_year=1970)
            .select_related('home_franchise', 'away_franchise')
            .order_by('id')
        )
        first = games[0]
        shared = next(
            game for game in games[1:]
            if first.home_franchise_id in (game.home_franchise_id, game.away_franchise_id)
            or first.away_franchise_id in (game.home_franchise_id, game.away_franchise_id)
        )
        occupied = {
            first.home_franchise_id,
            first.away_franchise_id,
            shared.home_franchise_id,
            shared.away_franchise_id,
        }
        separate = next(
            game for game in games
            if game.home_franchise_id not in occupied
            and game.away_franchise_id not in occupied
        )
        fresh = next(
            game for game in games
            if game.pk not in {first.pk, shared.pk, separate.pk}
        )
        OpponentWeekAssignment(release=self.release, pairing=first, week=3).save()
        OpponentWeekAssignment(release=self.release, pairing=separate, week=3).save()
        with self.assertRaises(ValidationError):
            OpponentWeekAssignment(release=self.release, pairing=shared, week=3).save()
        with self.assertRaises(ValidationError):
            OpponentWeekAssignment(release=self.release, pairing=fresh, week=15).save()

        other = UniverseRelease(
            slug='weeks-other',
            root_league=self.league,
            generator_version='pending',
            seed=1,
            founding_franchise_count=26,
            expansion_schedule_version=1,
            first_season_year=1970,
            season_count=60,
            last_season_year=2029,
            status=UniverseRelease.Status.DRAFT,
            catalog_fingerprint='',
            validation_fingerprint='',
        )
        other.save()
        with self.assertRaises(ValidationError):
            OpponentWeekAssignment(release=other, pairing=fresh, week=1).full_clean()

        with self.assertRaises(ProtectedError):
            with transaction.atomic():
                first.delete()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                OpponentWeekAssignment.objects.bulk_create([
                    OpponentWeekAssignment(release=self.release, pairing=fresh, week=0),
                ])
        self.assertFalse(
            OpponentWeekAssignment.objects.filter(pairing=fresh).exists()
        )

    def test_command_reports_unchanged(self):
        created = StringIO()
        call_command(
            'generate_opponent_weeks',
            league_id=self.league.id,
            seed=SEED,
            stdout=created,
        )
        self.assertEqual(created.getvalue().strip(), 'created')
        rerun = StringIO()
        call_command(
            'generate_opponent_weeks',
            league_id=self.league.id,
            seed=SEED,
            stdout=rerun,
        )
        self.assertEqual(rerun.getvalue().strip(), 'unchanged')
        self.assertEqual(
            OpponentWeekAssignment.objects.filter(release=self.release).count(),
            PAIRING_COUNT,
        )
        with self.assertRaises(CommandError):
            call_command(
                'generate_opponent_weeks',
                league_id=self.league.id,
                seed=OTHER_SEED,
            )
