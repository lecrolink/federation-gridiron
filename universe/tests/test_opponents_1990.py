from collections import Counter
from io import StringIO
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
    EAST,
    WEST,
    CENTRAL,
    OpponentGenerationError,
    Seat,
    generate_opponent_pairings,
)
from universe.generation.opponents_1978 import (
    FIRST_SEASON as EARLIER_FIRST,
    build_season as build_earlier_season,
    cross_offsets as earlier_offsets,
    generate_opponent_pairings_1978,
)
from universe.generation.opponents_1990 import (
    FIRST_SEASON,
    LAST_SEASON,
    REPEATED_SEASONS,
    REQUIRED_SCHEDULE_LENGTH,
    build_season,
    cross_offsets,
    generate_opponent_pairings_1990,
)
from universe.generation.prospects import later_profile_values
from universe.generation.schedule import generate_schedule_release
from universe.generation.weeks import generate_opponent_weeks
from universe.models import (
    FranchiseSeasonMembership,
    OpponentPairing,
    OpponentWeekAssignment,
    UniverseRelease,
)
from universe.rules import EraRuleProfile


SEED = 1970
OTHER_SEED = 1971
SEASON_GAMES = 224
SPAN_GAMES = 5 * SEASON_GAMES
EARLY_PAIRING_COUNT = 6 * 182 + 2 * 196
SIXTEEN_PAIRING_COUNT = 12 * SEASON_GAMES
EXPECTED_OFFSETS = {
    1990: (12, 0, 2),
    1991: (13, 1, 3),
    1992: (0, 2, 4),
    1993: (1, 3, 5),
    1994: (2, 4, 6),
}


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


def _identity(pairings):
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


class NineteenNinetyRuleTests(SimpleTestCase):
    def test_every_season_is_deterministic_balanced_and_repeats(self):
        seats = _sample_seats()
        first = {year: build_season(year, seats, 16) for year in range(FIRST_SEASON, LAST_SEASON + 1)}
        second = {year: build_season(year, seats, 16) for year in range(FIRST_SEASON, LAST_SEASON + 1)}
        self.assertEqual(first, second)
        for year, pairings in first.items():
            self.assertEqual(cross_offsets(year), EXPECTED_OFFSETS[year])
            self.assertEqual(len(pairings), SEASON_GAMES)
            homes = Counter()
            games = Counter()
            seen = set()
            for pairing in pairings:
                games[pairing.home_franchise_id] += 1
                games[pairing.away_franchise_id] += 1
                homes[pairing.home_franchise_id] += 1
                pair = frozenset((pairing.home_franchise_id, pairing.away_franchise_id))
                self.assertNotIn(pair, seen)
                seen.add(pair)
            self.assertEqual(set(games.values()), {REQUIRED_SCHEDULE_LENGTH})
            self.assertEqual(set(homes.values()), {8})
            self.assertEqual(len(seen), SEASON_GAMES)
        for later, earlier in REPEATED_SEASONS.items():
            self.assertEqual(cross_offsets(later), earlier_offsets(earlier))
            repeated = build_earlier_season(earlier, seats, 16)
            self.assertEqual(_identity(first[later]), _identity(repeated))
        self.assertEqual(cross_offsets(1990), (12, 0, 2))
        self.assertNotEqual(cross_offsets(1990), earlier_offsets(EARLIER_FIRST))

    def test_bad_length_or_year_is_refused(self):
        seats = _sample_seats()
        with self.assertRaisesRegex(OpponentGenerationError, 'schedule_length'):
            build_season(1990, seats, 14)
        with self.assertRaisesRegex(OpponentGenerationError, '1990–1994'):
            build_season(1989, seats, 16)
        with self.assertRaisesRegex(OpponentGenerationError, '1990–1994'):
            build_season(1995, seats, 16)


class OpponentPairing1990Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.league = League(name='Nineteen Ninety League', slug='nineteen-ninety-league', tier=1, active=True)
        cls.league.save()
        cls.release = UniverseRelease(
            slug='opponents-1990',
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

    def seats(self, season_year):
        seats = []
        for row in FranchiseSeasonMembership.objects.filter(
            franchise__release=self.release,
            season_year=season_year,
        ):
            conference = 1 if row.conference_slug == 'conference-1' else 2
            role = {'d3': EAST, 'd1': CENTRAL, 'd2': WEST}[row.division_slug]
            seats.append(Seat(row.franchise_id, conference, role, row.slot_order))
        return tuple(seats)

    def test_stored_rows_match_the_rule_for_every_season(self):
        profile = EraRuleProfile.objects.get(release=self.release, profile_key='y1987')
        before = (profile.schedule_length, profile.playoff_format, profile.style_key, profile.market)
        self.assertEqual(generate_opponent_pairings_1990(self.league.id, SEED), 'created')
        profile.refresh_from_db()
        self.assertEqual(
            (profile.schedule_length, profile.playoff_format, profile.style_key, profile.market),
            before,
        )
        self.assertEqual(OpponentWeekAssignment.objects.filter(release=self.release).count(), 0)
        self.assertEqual(
            OpponentPairing.objects.filter(release=self.release).count(),
            SPAN_GAMES,
        )
        self.assertFalse(
            OpponentPairing.objects.filter(release=self.release, season_year__lt=1990).exists()
        )
        self.assertFalse(
            OpponentPairing.objects.filter(release=self.release, season_year__gt=1994).exists()
        )
        for season_year in range(FIRST_SEASON, LAST_SEASON + 1):
            stored = list(
                OpponentPairing.objects.filter(release=self.release, season_year=season_year)
            )
            built = build_season(season_year, self.seats(season_year), 16)
            self.assertEqual(len(stored), SEASON_GAMES)
            self.assertEqual(
                {(row.home_franchise_id, row.away_franchise_id, row.kind) for row in stored},
                {(row.home_franchise_id, row.away_franchise_id, row.kind) for row in built},
            )
            homes = Counter(row.home_franchise_id for row in stored)
            played = Counter()
            for row in stored:
                played[row.home_franchise_id] += 1
                played[row.away_franchise_id] += 1
            self.assertEqual(set(played), set(homes))
            self.assertEqual(set(played.values()), {REQUIRED_SCHEDULE_LENGTH})
            self.assertEqual(set(homes.values()), {8})
            self.assertEqual(len(played), 28)
        for later, earlier in REPEATED_SEASONS.items():
            repeated = build_earlier_season(earlier, self.seats(later), 16)
            stored = OpponentPairing.objects.filter(release=self.release, season_year=later)
            self.assertEqual(
                {(row.home_franchise_id, row.away_franchise_id, row.kind) for row in stored},
                {(row.home_franchise_id, row.away_franchise_id, row.kind) for row in repeated},
            )

    def test_exact_rerun_writes_nothing(self):
        self.assertEqual(generate_opponent_pairings_1990(self.league.id, SEED), 'created')
        before = list(
            OpponentPairing.objects.filter(release=self.release)
            .order_by('id')
            .values_list('id', 'created_at', 'season_year', 'home_franchise_id', 'away_franchise_id', 'kind')
        )
        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(generate_opponent_pairings_1990(self.league.id, SEED), 'unchanged')
        writes = [
            query['sql'] for query in captured.captured_queries
            if query['sql'].lstrip()[:6].upper() in {'INSERT', 'UPDATE', 'DELETE'}
        ]
        self.assertEqual(writes, [])
        after = list(
            OpponentPairing.objects.filter(release=self.release)
            .order_by('id')
            .values_list('id', 'created_at', 'season_year', 'home_franchise_id', 'away_franchise_id', 'kind')
        )
        self.assertEqual(before, after)

    def test_partial_or_mismatched_rows_stay(self):
        generate_opponent_pairings_1990(self.league.id, SEED)
        OpponentPairing.objects.filter(release=self.release, season_year=1991).delete()
        with self.assertRaisesRegex(OpponentGenerationError, 'will not delete'):
            generate_opponent_pairings_1990(self.league.id, SEED)
        self.assertFalse(
            OpponentPairing.objects.filter(release=self.release, season_year=1991).exists()
        )
        self.assertEqual(
            OpponentPairing.objects.filter(release=self.release, season_year=1990).count(),
            SEASON_GAMES,
        )
        game = OpponentPairing.objects.filter(
            release=self.release,
            season_year=1992,
            kind=OpponentPairing.Kind.INTERCONFERENCE,
        ).order_by('id').first()
        OpponentPairing.objects.filter(pk=game.pk).update(
            home_franchise_id=game.away_franchise_id,
            away_franchise_id=game.home_franchise_id,
        )
        with self.assertRaisesRegex(OpponentGenerationError, 'will not delete'):
            generate_opponent_pairings_1990(self.league.id, SEED)
        stored = OpponentPairing.objects.get(pk=game.pk)
        self.assertEqual(stored.home_franchise_id, game.away_franchise_id)
        self.assertEqual(stored.created_at, game.created_at)

    def test_an_extra_row_fails_closed(self):
        generate_opponent_pairings_1990(self.league.id, SEED)
        stored = {
            (row.home_franchise_id, row.away_franchise_id)
            for row in OpponentPairing.objects.filter(release=self.release, season_year=1990)
        }
        extra = None
        for home in self.seats(1990):
            for away in self.seats(1990):
                if home.franchise_id == away.franchise_id:
                    continue
                key = (home.franchise_id, away.franchise_id)
                if key not in stored:
                    extra = key
                    break
            if extra:
                break
        OpponentPairing(
            release=self.release,
            season_year=1990,
            home_franchise_id=extra[0],
            away_franchise_id=extra[1],
            kind=OpponentPairing.Kind.INTERCONFERENCE,
        ).save()
        with self.assertRaisesRegex(OpponentGenerationError, 'will not delete'):
            generate_opponent_pairings_1990(self.league.id, SEED)
        self.assertEqual(
            OpponentPairing.objects.filter(release=self.release).count(),
            SPAN_GAMES + 1,
        )

    def test_insert_failure_rolls_back_and_keeps_earlier_spans(self):
        self.assertEqual(generate_opponent_pairings(self.league.id, SEED), 'created')
        self.assertEqual(generate_opponent_pairings_1978(self.league.id, SEED), 'created')
        real = OpponentPairing.objects.bulk_create
        calls = {'n': 0}

        def fail_second_season(rows, **kwargs):
            calls['n'] += 1
            if calls['n'] == 2:
                raise IntegrityError('forced')
            return real(rows, **kwargs)

        with patch(
            'universe.generation.opponents_1990.OpponentPairing.objects.bulk_create',
            fail_second_season,
        ):
            with self.assertRaisesRegex(OpponentGenerationError, 'rolled back'):
                generate_opponent_pairings_1990(self.league.id, SEED)
        self.assertEqual(calls['n'], 2)
        self.assertEqual(
            OpponentPairing.objects.filter(release=self.release, season_year__lte=1977).count(),
            EARLY_PAIRING_COUNT,
        )
        self.assertEqual(
            OpponentPairing.objects.filter(
                release=self.release,
                season_year__gte=1978,
                season_year__lte=1989,
            ).count(),
            SIXTEEN_PAIRING_COUNT,
        )
        self.assertFalse(
            OpponentPairing.objects.filter(release=self.release, season_year__gte=1990).exists()
        )

    def test_wrong_length_writes_nothing_and_leaves_the_card(self):
        profile = EraRuleProfile.objects.get(release=self.release, profile_key='y1987')
        EraRuleProfile.objects.filter(pk=profile.pk).update(schedule_length=14)
        with self.assertRaisesRegex(OpponentGenerationError, 'schedule_length'):
            generate_opponent_pairings_1990(self.league.id, SEED)
        self.assertEqual(OpponentPairing.objects.filter(release=self.release).count(), 0)
        profile.refresh_from_db()
        self.assertEqual(profile.schedule_length, 14)
        self.assertEqual(profile.profile_key, 'y1987')
        self.assertEqual(profile.first_year, 1987)
        self.assertEqual(profile.last_year, 1994)

    def test_older_pairings_and_weeks_stay_in_place(self):
        self.assertEqual(generate_opponent_pairings(self.league.id, SEED), 'created')
        self.assertEqual(generate_opponent_weeks(self.league.id, SEED), 'created')
        self.assertEqual(generate_opponent_pairings_1978(self.league.id, SEED), 'created')
        older = list(
            OpponentPairing.objects.filter(release=self.release, season_year__lte=1989)
            .order_by('id')
            .values_list('id', 'created_at', 'season_year', 'home_franchise_id', 'away_franchise_id', 'kind')
        )
        weeks = list(
            OpponentWeekAssignment.objects.filter(release=self.release)
            .order_by('id')
            .values_list('id', 'pairing_id', 'week')
        )
        self.assertEqual(generate_opponent_pairings_1990(self.league.id, SEED), 'created')
        self.assertEqual(
            list(
                OpponentPairing.objects.filter(release=self.release, season_year__lte=1989)
                .order_by('id')
                .values_list('id', 'created_at', 'season_year', 'home_franchise_id', 'away_franchise_id', 'kind')
            ),
            older,
        )
        self.assertEqual(
            list(
                OpponentWeekAssignment.objects.filter(release=self.release)
                .order_by('id')
                .values_list('id', 'pairing_id', 'week')
            ),
            weeks,
        )
        self.assertEqual(generate_opponent_pairings(self.league.id, SEED), 'unchanged')
        self.assertEqual(generate_opponent_pairings_1978(self.league.id, SEED), 'unchanged')
        self.assertEqual(generate_opponent_weeks(self.league.id, SEED), 'unchanged')
        for later, earlier in REPEATED_SEASONS.items():
            later_rows = {
                (row.home_franchise_id, row.away_franchise_id, row.kind)
                for row in OpponentPairing.objects.filter(release=self.release, season_year=later)
            }
            earlier_rows = {
                (row.home_franchise_id, row.away_franchise_id, row.kind)
                for row in OpponentPairing.objects.filter(release=self.release, season_year=earlier)
            }
            self.assertEqual(later_rows, earlier_rows)

    def test_published_release_and_wrong_seed_write_nothing(self):
        UniverseRelease.objects.filter(pk=self.release.pk).update(
            status=UniverseRelease.Status.PUBLISHED,
            catalog_fingerprint='a' * 64,
            validation_fingerprint='b' * 64,
            published_at=timezone.now(),
        )
        with self.assertRaisesRegex(OpponentGenerationError, 'published'):
            generate_opponent_pairings_1990(self.league.id, SEED)
        self.assertEqual(OpponentPairing.objects.count(), 0)
        UniverseRelease.objects.filter(pk=self.release.pk).update(
            status=UniverseRelease.Status.DRAFT,
            catalog_fingerprint='preexisting',
            validation_fingerprint='',
            published_at=None,
        )
        with self.assertRaisesRegex(OpponentGenerationError, 'seed'):
            generate_opponent_pairings_1990(self.league.id, OTHER_SEED)
        self.assertEqual(OpponentPairing.objects.count(), 0)

    def test_command_reports_unchanged(self):
        created = StringIO()
        call_command(
            'generate_opponents_1990',
            league_id=self.league.id,
            seed=SEED,
            stdout=created,
        )
        self.assertEqual(created.getvalue().strip(), 'created')
        rerun = StringIO()
        call_command(
            'generate_opponents_1990',
            league_id=self.league.id,
            seed=SEED,
            stdout=rerun,
        )
        self.assertEqual(rerun.getvalue().strip(), 'unchanged')
        self.assertEqual(OpponentPairing.objects.filter(release=self.release).count(), SPAN_GAMES)
        with self.assertRaises(CommandError):
            call_command('generate_opponents_1990', league_id=self.league.id, seed=OTHER_SEED)
