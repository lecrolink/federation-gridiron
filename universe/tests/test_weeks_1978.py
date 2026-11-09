from collections import Counter, defaultdict
from io import StringIO
from itertools import combinations
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from leagues.models import League
from universe.generation.opening_players import y1970_values
from universe.generation.opponents import (
    CENTRAL,
    EAST,
    WEST,
    Seat,
    generate_opponent_pairings,
)
from universe.generation.opponents_1978 import build_season as build_early_season
from universe.generation.opponents_1978 import generate_opponent_pairings_1978
from universe.generation.opponents_1990 import build_season as build_late_season
from universe.generation.opponents_1990 import generate_opponent_pairings_1990
from universe.generation.prospects import later_profile_values
from universe.generation.schedule import generate_schedule_release
from universe.generation.weeks import OpponentWeekError, generate_opponent_weeks
from universe.generation.weeks_1978 import (
    CIRCLE_ROUNDS,
    FIRST_SEASON,
    LAST_SEASON,
    REPEATED_SEASONS,
    WEEK_COUNT,
    generate_opponent_weeks_1978,
    project_weeks,
)
from universe.models import OpponentPairing, OpponentWeekAssignment, UniverseRelease
from universe.rules import EraRuleProfile


SEED = 1970
OTHER_SEED = 1971
SEASON_GAMES = 224
SPAN_GAMES = 17 * SEASON_GAMES
EARLY_WEEKS = 6 * 182 + 2 * 196


def _sample_seats():
    seats = []
    franchise_id = 1
    sizes = {1: (5, 4, 5), 2: (5, 5, 4)}
    for conference, role_sizes in sizes.items():
        for role, size in zip((EAST, CENTRAL, WEST), role_sizes):
            for slot in range(1, size + 1):
                seats.append(Seat(franchise_id, conference, role, slot))
                franchise_id += 1
    return tuple(seats)


def _built(year, seats):
    if year <= 1989:
        return build_early_season(year, seats, 16)
    return build_late_season(year, seats, 16)


def _pair_weeks(assignment):
    return {
        frozenset((pairing.home_franchise_id, pairing.away_franchise_id)): week
        for pairing, week in assignment.items()
    }


class CircleWeekTests(SimpleTestCase):
    def test_round_zero_and_the_rotation_cover_every_pair(self):
        self.assertEqual(
            CIRCLE_ROUNDS[0],
            ((0, 13), (1, 12), (2, 11), (3, 10), (4, 9), (5, 8), (6, 7)),
        )
        self.assertEqual(
            CIRCLE_ROUNDS[1],
            ((0, 12), (13, 11), (1, 10), (2, 9), (3, 8), (4, 7), (5, 6)),
        )
        seen = set()
        for pairs in CIRCLE_ROUNDS:
            clubs = [club for pair in pairs for club in pair]
            self.assertEqual(len(clubs), len(set(clubs)))
            self.assertEqual(len(clubs), 14)
            for pair in pairs:
                seen.add(frozenset(pair))
        self.assertEqual(seen, {frozenset(pair) for pair in combinations(range(14), 2)})

    def test_every_season_is_full_deterministic_and_repeats(self):
        seats = _sample_seats()
        assignments = {}
        for year in range(FIRST_SEASON, LAST_SEASON + 1):
            pairings = _built(year, seats)
            first = project_weeks(year, seats, pairings)
            second = project_weeks(year, seats, pairings)
            self.assertEqual(
                _pair_weeks(first),
                _pair_weeks(second),
            )
            played = defaultdict(set)
            by_week = Counter()
            for pairing, week in first.items():
                self.assertIn(week, range(1, WEEK_COUNT + 1))
                by_week[week] += 1
                for franchise_id in (pairing.home_franchise_id, pairing.away_franchise_id):
                    self.assertNotIn(week, played[franchise_id])
                    played[franchise_id].add(week)
                inter = pairing.kind == 'interconference'
                if week >= 14:
                    self.assertTrue(inter)
                else:
                    self.assertFalse(inter)
            self.assertEqual(set(by_week), set(range(1, WEEK_COUNT + 1)))
            self.assertEqual(set(by_week.values()), {14})
            self.assertEqual(len(played), 28)
            self.assertTrue(all(found == set(range(1, 17)) for found in played.values()))
            assignments[year] = _pair_weeks(first)
        for later, earlier in REPEATED_SEASONS.items():
            self.assertEqual(assignments[later], assignments[earlier])
        self.assertNotEqual(assignments[1990], assignments[1978])
        week_of = assignments[1990]
        # Position 0 in each conference is the first sample franchise of that conference.
        # Offset 12 is week 14, before the numerically smaller offsets.
        conf2_start = 15
        self.assertEqual(week_of[frozenset((1, conf2_start + 12))], 14)
        self.assertEqual(week_of[frozenset((1, conf2_start))], 15)
        self.assertEqual(week_of[frozenset((1, conf2_start + 2))], 16)

    def test_other_years_are_refused(self):
        seats = _sample_seats()
        with self.assertRaisesRegex(OpponentWeekError, '1978–1994'):
            project_weeks(1977, seats, ())


class OpponentWeek1978Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.league = League(name='Sixteen Week League', slug='sixteen-week-league', tier=1, active=True)
        cls.league.save()
        cls.release = UniverseRelease(
            slug='weeks-1978',
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
        if generate_schedule_release(cls.league.id, SEED) != 'created':
            raise RuntimeError('The schedule release was not created.')
        cls.release.refresh_from_db()
        EraRuleProfile(release=cls.release, **y1970_values()).save()
        for values in later_profile_values():
            if values['profile_key'] in {'y1974', 'y1976', 'y1978', 'y1987'}:
                EraRuleProfile(release=cls.release, **values).save()
        if generate_opponent_pairings(cls.league.id, SEED) != 'created':
            raise RuntimeError('1970 pairings were not created.')
        if generate_opponent_weeks(cls.league.id, SEED) != 'created':
            raise RuntimeError('1970 weeks were not created.')
        if generate_opponent_pairings_1978(cls.league.id, SEED) != 'created':
            raise RuntimeError('1978 pairings were not created.')
        if generate_opponent_pairings_1990(cls.league.id, SEED) != 'created':
            raise RuntimeError('1990 pairings were not created.')

    def test_every_club_plays_every_week_and_cards_stay(self):
        profiles = {
            profile.profile_key: (
                profile.schedule_length,
                profile.playoff_format,
                profile.style_key,
                profile.market,
                profile.first_year,
                profile.last_year,
            )
            for profile in EraRuleProfile.objects.filter(release=self.release)
        }
        pairings = list(
            OpponentPairing.objects.filter(release=self.release)
            .order_by('id')
            .values_list(
                'id', 'created_at', 'season_year', 'home_franchise_id', 'away_franchise_id', 'kind',
            )
        )
        early = list(
            OpponentWeekAssignment.objects.filter(
                release=self.release,
                pairing__season_year__lte=1977,
            )
            .order_by('id')
            .values_list('id', 'created_at', 'pairing_id', 'week')
        )
        self.assertEqual(generate_opponent_weeks_1978(self.league.id, SEED), 'created')
        self.assertEqual(
            list(
                OpponentPairing.objects.filter(release=self.release)
                .order_by('id')
                .values_list(
                    'id', 'created_at', 'season_year', 'home_franchise_id', 'away_franchise_id', 'kind',
                )
            ),
            pairings,
        )
        self.assertEqual(
            list(
                OpponentWeekAssignment.objects.filter(
                    release=self.release,
                    pairing__season_year__lte=1977,
                )
                .order_by('id')
                .values_list('id', 'created_at', 'pairing_id', 'week')
            ),
            early,
        )
        self.assertEqual(len(early), EARLY_WEEKS)
        later = OpponentWeekAssignment.objects.filter(
            release=self.release,
            pairing__season_year__gte=1978,
        )
        self.assertEqual(later.count(), SPAN_GAMES)
        for profile in EraRuleProfile.objects.filter(release=self.release):
            self.assertEqual(
                (
                    profile.schedule_length,
                    profile.playoff_format,
                    profile.style_key,
                    profile.market,
                    profile.first_year,
                    profile.last_year,
                ),
                profiles[profile.profile_key],
            )
        for season_year in range(FIRST_SEASON, LAST_SEASON + 1):
            rows = list(
                later.filter(pairing__season_year=season_year).select_related('pairing')
            )
            self.assertEqual(len(rows), SEASON_GAMES)
            played = defaultdict(set)
            by_week = Counter()
            for row in rows:
                self.assertGreaterEqual(row.week, 1)
                self.assertLessEqual(row.week, 16)
                by_week[row.week] += 1
                for franchise_id in (
                    row.pairing.home_franchise_id,
                    row.pairing.away_franchise_id,
                ):
                    self.assertNotIn(row.week, played[franchise_id])
                    played[franchise_id].add(row.week)
                inter = row.pairing.kind == OpponentPairing.Kind.INTERCONFERENCE
                if row.week >= 14:
                    self.assertTrue(inter)
                else:
                    self.assertFalse(inter)
            self.assertEqual(set(by_week), set(range(1, 17)))
            self.assertEqual(set(by_week.values()), {14})
            self.assertEqual(len(played), 28)
            self.assertTrue(all(found == set(range(1, 17)) for found in played.values()))
        for later_year, earlier_year in REPEATED_SEASONS.items():
            def pairs(year):
                return {
                    frozenset((
                        row.pairing.home_franchise_id,
                        row.pairing.away_franchise_id,
                    )): row.week
                    for row in later.filter(pairing__season_year=year).select_related('pairing')
                }
            self.assertEqual(pairs(later_year), pairs(earlier_year))
        self.assertEqual(generate_opponent_weeks(self.league.id, SEED), 'unchanged')

    def test_exact_rerun_writes_nothing(self):
        self.assertEqual(generate_opponent_weeks_1978(self.league.id, SEED), 'created')
        before = list(
            OpponentWeekAssignment.objects.filter(release=self.release)
            .order_by('id')
            .values_list('id', 'created_at', 'pairing_id', 'week')
        )
        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(generate_opponent_weeks_1978(self.league.id, SEED), 'unchanged')
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

    def test_partial_or_mismatched_weeks_stay(self):
        generate_opponent_weeks_1978(self.league.id, SEED)
        OpponentWeekAssignment.objects.filter(
            release=self.release,
            pairing__season_year=1991,
            week=16,
        ).delete()
        with self.assertRaisesRegex(OpponentWeekError, 'will not delete'):
            generate_opponent_weeks_1978(self.league.id, SEED)
        self.assertFalse(
            OpponentWeekAssignment.objects.filter(
                release=self.release,
                pairing__season_year=1991,
                week=16,
            ).exists()
        )
        row = OpponentWeekAssignment.objects.filter(
            release=self.release,
            pairing__season_year=1978,
            week=1,
        ).order_by('id').first()
        OpponentWeekAssignment.objects.filter(pk=row.pk).update(week=2)
        with self.assertRaisesRegex(OpponentWeekError, 'will not delete'):
            generate_opponent_weeks_1978(self.league.id, SEED)
        self.assertEqual(OpponentWeekAssignment.objects.get(pk=row.pk).week, 2)
        self.assertEqual(OpponentWeekAssignment.objects.get(pk=row.pk).created_at, row.created_at)

    def test_bad_pairings_store_nothing(self):
        early = OpponentWeekAssignment.objects.filter(
            release=self.release,
            pairing__season_year__lte=1977,
        ).count()
        OpponentPairing.objects.filter(release=self.release, season_year=1994).delete()
        with self.assertRaisesRegex(OpponentWeekError, 'stored nothing'):
            generate_opponent_weeks_1978(self.league.id, SEED)
        self.assertFalse(
            OpponentWeekAssignment.objects.filter(
                release=self.release,
                pairing__season_year__gte=1978,
            ).exists()
        )
        self.assertEqual(
            OpponentWeekAssignment.objects.filter(
                release=self.release,
                pairing__season_year__lte=1977,
            ).count(),
            early,
        )

    def test_a_duplicate_or_changed_opponent_stores_nothing(self):
        stored = list(
            OpponentPairing.objects.filter(release=self.release, season_year=1990).order_by('id')
        )
        game = stored[0]
        OpponentPairing(
            release=self.release,
            season_year=1990,
            home_franchise_id=game.away_franchise_id,
            away_franchise_id=game.home_franchise_id,
            kind=game.kind,
        ).save()
        with self.assertRaisesRegex(OpponentWeekError, 'stored nothing'):
            generate_opponent_weeks_1978(self.league.id, SEED)
        self.assertFalse(
            OpponentWeekAssignment.objects.filter(
                release=self.release,
                pairing__season_year__gte=1978,
            ).exists()
        )
        OpponentPairing.objects.filter(
            release=self.release,
            season_year=1990,
            home_franchise_id=game.away_franchise_id,
            away_franchise_id=game.home_franchise_id,
        ).delete()
        directed = {
            (row.home_franchise_id, row.away_franchise_id)
            for row in OpponentPairing.objects.filter(release=self.release, season_year=1990)
        }
        replacement = next(
            franchise_id
            for row in stored
            for franchise_id in (row.home_franchise_id, row.away_franchise_id)
            if franchise_id != game.home_franchise_id
            and (game.home_franchise_id, franchise_id) not in directed
        )
        OpponentPairing.objects.filter(pk=game.pk).update(away_franchise_id=replacement)
        with self.assertRaisesRegex(OpponentWeekError, 'stored nothing'):
            generate_opponent_weeks_1978(self.league.id, SEED)
        self.assertEqual(
            OpponentPairing.objects.get(pk=game.pk).away_franchise_id,
            replacement,
        )

    def test_wrong_length_writes_nothing_and_leaves_the_card(self):
        profile = EraRuleProfile.objects.get(release=self.release, profile_key='y1987')
        EraRuleProfile.objects.filter(pk=profile.pk).update(schedule_length=14)
        with self.assertRaisesRegex(OpponentWeekError, 'schedule_length'):
            generate_opponent_weeks_1978(self.league.id, SEED)
        self.assertFalse(
            OpponentWeekAssignment.objects.filter(
                release=self.release,
                pairing__season_year__gte=1978,
            ).exists()
        )
        profile.refresh_from_db()
        self.assertEqual(profile.schedule_length, 14)
        self.assertEqual(profile.profile_key, 'y1987')
        self.assertEqual(profile.first_year, 1987)
        self.assertEqual(profile.last_year, 1994)

    def test_later_weeks_do_not_block_the_1970_writer(self):
        OpponentWeekAssignment.objects.filter(
            release=self.release,
            pairing__season_year__lte=1977,
        ).delete()
        self.assertEqual(generate_opponent_weeks_1978(self.league.id, SEED), 'created')
        self.assertEqual(generate_opponent_weeks(self.league.id, SEED), 'created')
        self.assertEqual(
            OpponentWeekAssignment.objects.filter(
                release=self.release,
                pairing__season_year__lte=1977,
            ).count(),
            EARLY_WEEKS,
        )
        self.assertEqual(generate_opponent_weeks_1978(self.league.id, SEED), 'unchanged')

    def test_insert_failure_rolls_back_and_keeps_early_weeks(self):
        early = list(
            OpponentWeekAssignment.objects.filter(
                release=self.release,
                pairing__season_year__lte=1977,
            )
            .order_by('id')
            .values_list('id', 'pairing_id', 'week')
        )
        real = OpponentWeekAssignment.objects.bulk_create
        calls = {'n': 0}

        def fail_second_season(rows, **kwargs):
            calls['n'] += 1
            if calls['n'] == 2:
                raise IntegrityError('forced')
            return real(rows, **kwargs)

        with patch(
            'universe.generation.weeks_1978.OpponentWeekAssignment.objects.bulk_create',
            fail_second_season,
        ):
            with self.assertRaisesRegex(OpponentWeekError, 'rolled back'):
                generate_opponent_weeks_1978(self.league.id, SEED)
        self.assertEqual(calls['n'], 2)
        self.assertFalse(
            OpponentWeekAssignment.objects.filter(
                release=self.release,
                pairing__season_year__gte=1978,
            ).exists()
        )
        self.assertEqual(
            list(
                OpponentWeekAssignment.objects.filter(
                    release=self.release,
                    pairing__season_year__lte=1977,
                )
                .order_by('id')
                .values_list('id', 'pairing_id', 'week')
            ),
            early,
        )

    def test_published_release_and_wrong_seed_write_nothing(self):
        UniverseRelease.objects.filter(pk=self.release.pk).update(
            status=UniverseRelease.Status.PUBLISHED,
            catalog_fingerprint='a' * 64,
            validation_fingerprint='b' * 64,
            published_at=timezone.now(),
        )
        before = OpponentWeekAssignment.objects.filter(release=self.release).count()
        with self.assertRaisesRegex(OpponentWeekError, 'published'):
            generate_opponent_weeks_1978(self.league.id, SEED)
        self.assertEqual(OpponentWeekAssignment.objects.filter(release=self.release).count(), before)
        UniverseRelease.objects.filter(pk=self.release.pk).update(
            status=UniverseRelease.Status.DRAFT,
            catalog_fingerprint='preexisting',
            validation_fingerprint='',
            published_at=None,
        )
        with self.assertRaisesRegex(OpponentWeekError, 'seed'):
            generate_opponent_weeks_1978(self.league.id, OTHER_SEED)
        self.assertEqual(OpponentWeekAssignment.objects.filter(release=self.release).count(), before)

    def test_command_reports_unchanged(self):
        created = StringIO()
        call_command(
            'generate_opponent_weeks_1978',
            league_id=self.league.id,
            seed=SEED,
            stdout=created,
        )
        self.assertEqual(created.getvalue().strip(), 'created')
        rerun = StringIO()
        call_command(
            'generate_opponent_weeks_1978',
            league_id=self.league.id,
            seed=SEED,
            stdout=rerun,
        )
        self.assertEqual(rerun.getvalue().strip(), 'unchanged')
        self.assertEqual(
            OpponentWeekAssignment.objects.filter(
                release=self.release,
                pairing__season_year__gte=1978,
            ).count(),
            SPAN_GAMES,
        )
        with self.assertRaises(CommandError):
            call_command(
                'generate_opponent_weeks_1978',
                league_id=self.league.id,
                seed=OTHER_SEED,
            )
