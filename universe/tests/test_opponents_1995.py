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
    OpponentGenerationError,
    Seat,
    generate_opponent_pairings,
)
from universe.generation.opponents_1978 import generate_opponent_pairings_1978
from universe.generation.opponents_1990 import generate_opponent_pairings_1990
from universe.generation.opponents_1995 import (
    CROSS_GAP,
    FIRST_SEASON,
    LAST_SEASON,
    REQUIRED_SCHEDULE_LENGTH,
    build_season,
    cross_offsets,
    generate_opponent_pairings_1995,
)
from universe.generation.prospects import later_profile_values
from universe.generation.schedule import generate_schedule_release
from universe.generation.weeks import generate_opponent_weeks
from universe.generation.weeks_1978 import generate_opponent_weeks_1978
from universe.models import (
    FranchiseSeasonMembership,
    OpponentPairing,
    OpponentWeekAssignment,
    UniverseRelease,
)
from universe.rules import EraRuleProfile


SEED = 1970
OTHER_SEED = 1971
SEASON_GAMES = 240
SPAN_GAMES = 4 * SEASON_GAMES
EARLY_PAIRING_COUNT = 6 * 182 + 2 * 196
PAIRINGS_1978 = 12 * 224
PAIRINGS_1990 = 5 * 224
WEEKS_1978 = 17 * 224
EXPECTED_OFFSETS = {
    1995: (0, 5),
    1996: (1, 6),
    1997: (2, 7),
    1998: (3, 8),
}
# Conference-2 positions 0 and 5 in the d1, d2, d3 order.
OPENING_CROSS = {
    1995: ('founding-13', 'founding-17'),
    1998: ('founding-16', 'founding-20'),
}


def _sample_seats():
    seats = []
    franchise_id = 1
    for _conference in (1, 2):
        for division in ('d1', 'd2', 'd3'):
            for slot in range(1, 6):
                seats.append(Seat(franchise_id, _conference, division, slot))
                franchise_id += 1
    return tuple(seats)


def _opponents(pairings, franchise_id):
    found = set()
    for pairing in pairings:
        if pairing.home_franchise_id == franchise_id:
            found.add(pairing.away_franchise_id)
        elif pairing.away_franchise_id == franchise_id:
            found.add(pairing.home_franchise_id)
    return found


class NineteenNinetyFiveRuleTests(SimpleTestCase):
    def test_every_season_is_deterministic_and_balanced(self):
        seats = _sample_seats()
        first = {
            year: build_season(year, seats, 16)
            for year in range(FIRST_SEASON, LAST_SEASON + 1)
        }
        second = {
            year: build_season(year, seats, 16)
            for year in range(FIRST_SEASON, LAST_SEASON + 1)
        }
        self.assertEqual(first, second)
        cross_meetings = Counter()
        for year, pairings in first.items():
            self.assertEqual(cross_offsets(year), EXPECTED_OFFSETS[year])
            self.assertEqual(len(pairings), SEASON_GAMES)
            kinds = Counter(pairing.kind for pairing in pairings)
            self.assertEqual(kinds[OpponentPairing.Kind.DIVISION], 60)
            self.assertEqual(kinds[OpponentPairing.Kind.INTRACONFERENCE], 150)
            self.assertEqual(kinds[OpponentPairing.Kind.INTERCONFERENCE], 30)
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
                if pairing.kind == OpponentPairing.Kind.INTERCONFERENCE:
                    left = pairing.home_franchise_id
                    right = pairing.away_franchise_id
                    if left > 15:
                        left, right = right, left
                    cross_meetings[(left, right - 16)] += 1
            self.assertEqual(set(games.values()), {REQUIRED_SCHEDULE_LENGTH})
            self.assertEqual(set(homes.values()), {8})
            self.assertEqual(len(games), 30)
            self.assertEqual(len(seen), SEASON_GAMES)
            # Position 0 is d1 slot 1, franchise 1. It plays all 14 conference mates.
            self.assertEqual(_opponents(pairings, 1) & set(range(2, 16)), set(range(2, 16)))
            division_mates = {
                pairing.away_franchise_id if pairing.home_franchise_id == 1 else pairing.home_franchise_id
                for pairing in pairings
                if pairing.kind == OpponentPairing.Kind.DIVISION
                and 1 in (pairing.home_franchise_id, pairing.away_franchise_id)
            }
            self.assertEqual(division_mates, {2, 3, 4, 5})
        self.assertEqual(len(cross_meetings), 120)
        self.assertEqual(set(cross_meetings.values()), {1})
        self.assertEqual(
            {offset for _left, offset in cross_meetings if _left == 1},
            {0, 5, 1, 6, 2, 7, 3, 8},
        )
        self.assertEqual(CROSS_GAP, 5)

    def test_bad_length_year_or_shape_is_refused(self):
        seats = _sample_seats()
        with self.assertRaisesRegex(OpponentGenerationError, 'schedule_length'):
            build_season(1995, seats, 14)
        with self.assertRaisesRegex(OpponentGenerationError, '1995–1998'):
            build_season(1994, seats, 16)
        with self.assertRaisesRegex(OpponentGenerationError, '1995–1998'):
            build_season(1999, seats, 16)
        with self.assertRaisesRegex(OpponentGenerationError, 'seats do not match the rule'):
            build_season(1995, seats[:-1], 16)
        east_first = tuple(
            Seat(seat.franchise_id, seat.conference, 'east', seat.slot)
            for seat in seats
        )
        with self.assertRaisesRegex(OpponentGenerationError, 'd1, d2, and d3'):
            build_season(1995, east_first, 16)


class OpponentPairing1995Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.league = League(
            name='Nineteen Ninety Five League',
            slug='nineteen-ninety-five-league',
            tier=1,
            active=True,
        )
        cls.league.save()
        cls.release = UniverseRelease(
            slug='opponents-1995',
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
            if values['profile_key'] in {'y1974', 'y1976', 'y1978', 'y1987', 'y1995'}:
                EraRuleProfile(release=cls.release, **values).save()

    def seats(self, season_year):
        seats = []
        for row in FranchiseSeasonMembership.objects.filter(
            franchise__release=self.release,
            season_year=season_year,
        ):
            conference = 1 if row.conference_slug == 'conference-1' else 2
            seats.append(Seat(row.franchise_id, conference, row.division_slug, row.slot_order))
        return tuple(seats)

    def franchise_id(self, slug):
        return FranchiseSeasonMembership.objects.get(
            franchise__release=self.release,
            franchise__placeholder_slug=slug,
            season_year=1995,
        ).franchise_id

    def test_stored_rows_match_the_rule_for_every_season(self):
        profile = EraRuleProfile.objects.get(release=self.release, profile_key='y1995')
        before = (
            profile.schedule_length,
            profile.playoff_format,
            profile.style_key,
            profile.market,
            profile.overtime_rule,
            profile.cap_status,
            profile.first_year,
            profile.last_year,
        )
        self.assertEqual(profile.schedule_length, 16)
        self.assertEqual(generate_opponent_pairings_1995(self.league.id, SEED), 'created')
        profile.refresh_from_db()
        self.assertEqual(
            (
                profile.schedule_length,
                profile.playoff_format,
                profile.style_key,
                profile.market,
                profile.overtime_rule,
                profile.cap_status,
                profile.first_year,
                profile.last_year,
            ),
            before,
        )
        self.assertEqual(OpponentWeekAssignment.objects.filter(release=self.release).count(), 0)
        self.assertEqual(
            OpponentPairing.objects.filter(release=self.release).count(),
            SPAN_GAMES,
        )
        self.assertFalse(
            OpponentPairing.objects.filter(release=self.release, season_year__lt=1995).exists()
        )
        self.assertFalse(
            OpponentPairing.objects.filter(release=self.release, season_year__gt=1998).exists()
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
            kinds = Counter(row.kind for row in stored)
            self.assertEqual(kinds[OpponentPairing.Kind.DIVISION], 60)
            self.assertEqual(kinds[OpponentPairing.Kind.INTRACONFERENCE], 150)
            self.assertEqual(kinds[OpponentPairing.Kind.INTERCONFERENCE], 30)
            homes = Counter(row.home_franchise_id for row in stored)
            played = Counter()
            opponents = {}
            for row in stored:
                played[row.home_franchise_id] += 1
                played[row.away_franchise_id] += 1
                opponents.setdefault(row.home_franchise_id, set()).add(row.away_franchise_id)
                opponents.setdefault(row.away_franchise_id, set()).add(row.home_franchise_id)
            self.assertEqual(set(played), set(homes))
            self.assertEqual(set(played.values()), {REQUIRED_SCHEDULE_LENGTH})
            self.assertEqual(set(homes.values()), {8})
            self.assertEqual(len(played), 30)
            self.assertTrue(all(len(names) == 16 for names in opponents.values()))
        opener = self.franchise_id('founding-01')
        for season_year, slugs in OPENING_CROSS.items():
            expected = {self.franchise_id(slug) for slug in slugs}
            rows = OpponentPairing.objects.filter(
                release=self.release,
                season_year=season_year,
                kind=OpponentPairing.Kind.INTERCONFERENCE,
            )
            found = set()
            for row in rows:
                if row.home_franchise_id == opener:
                    found.add(row.away_franchise_id)
                elif row.away_franchise_id == opener:
                    found.add(row.home_franchise_id)
            self.assertEqual(found, expected)

    def test_exact_rerun_writes_nothing(self):
        self.assertEqual(generate_opponent_pairings_1995(self.league.id, SEED), 'created')
        before = list(
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
        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(generate_opponent_pairings_1995(self.league.id, SEED), 'unchanged')
        writes = [
            query['sql']
            for query in captured.captured_queries
            if query['sql'].lstrip()[:6].upper() in {'INSERT', 'UPDATE', 'DELETE'}
        ]
        self.assertEqual(writes, [])
        after = list(
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
        self.assertEqual(before, after)

    def test_partial_or_mismatched_rows_stay(self):
        generate_opponent_pairings_1995(self.league.id, SEED)
        OpponentPairing.objects.filter(release=self.release, season_year=1996).delete()
        with self.assertRaisesRegex(OpponentGenerationError, 'will not delete'):
            generate_opponent_pairings_1995(self.league.id, SEED)
        self.assertFalse(
            OpponentPairing.objects.filter(release=self.release, season_year=1996).exists()
        )
        self.assertEqual(
            OpponentPairing.objects.filter(release=self.release, season_year=1995).count(),
            SEASON_GAMES,
        )
        game = OpponentPairing.objects.filter(
            release=self.release,
            season_year=1997,
            kind=OpponentPairing.Kind.INTERCONFERENCE,
        ).order_by('id').first()
        OpponentPairing.objects.filter(pk=game.pk).update(
            home_franchise_id=game.away_franchise_id,
            away_franchise_id=game.home_franchise_id,
        )
        with self.assertRaisesRegex(OpponentGenerationError, 'will not delete'):
            generate_opponent_pairings_1995(self.league.id, SEED)
        stored = OpponentPairing.objects.get(pk=game.pk)
        self.assertEqual(stored.home_franchise_id, game.away_franchise_id)
        self.assertEqual(stored.created_at, game.created_at)

    def test_an_extra_row_fails_closed(self):
        generate_opponent_pairings_1995(self.league.id, SEED)
        stored = {
            (row.home_franchise_id, row.away_franchise_id)
            for row in OpponentPairing.objects.filter(release=self.release, season_year=1995)
        }
        extra = None
        for home in self.seats(1995):
            for away in self.seats(1995):
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
            season_year=1995,
            home_franchise_id=extra[0],
            away_franchise_id=extra[1],
            kind=OpponentPairing.Kind.INTERCONFERENCE,
        ).save()
        with self.assertRaisesRegex(OpponentGenerationError, 'will not delete'):
            generate_opponent_pairings_1995(self.league.id, SEED)
        self.assertEqual(
            OpponentPairing.objects.filter(release=self.release).count(),
            SPAN_GAMES + 1,
        )

    def test_later_season_rows_stay_on_rerun(self):
        generate_opponent_pairings_1995(self.league.id, SEED)
        home = self.franchise_id('founding-01')
        away = self.franchise_id('founding-13')
        later = OpponentPairing.objects.create(
            release=self.release,
            season_year=1999,
            home_franchise_id=home,
            away_franchise_id=away,
            kind=OpponentPairing.Kind.INTERCONFERENCE,
        )
        self.assertEqual(generate_opponent_pairings_1995(self.league.id, SEED), 'unchanged')
        self.assertTrue(OpponentPairing.objects.filter(pk=later.pk, season_year=1999).exists())
        self.assertEqual(
            OpponentPairing.objects.filter(
                release=self.release,
                season_year__gte=1995,
                season_year__lte=1998,
            ).count(),
            SPAN_GAMES,
        )

    def test_insert_failure_rolls_back_and_keeps_earlier_spans(self):
        self.assertEqual(generate_opponent_pairings(self.league.id, SEED), 'created')
        self.assertEqual(generate_opponent_pairings_1978(self.league.id, SEED), 'created')
        self.assertEqual(generate_opponent_pairings_1990(self.league.id, SEED), 'created')
        real = OpponentPairing.objects.bulk_create
        calls = {'n': 0}

        def fail_second_season(rows, **kwargs):
            calls['n'] += 1
            if calls['n'] == 2:
                raise IntegrityError('forced')
            return real(rows, **kwargs)

        with patch(
            'universe.generation.opponents_1995.OpponentPairing.objects.bulk_create',
            fail_second_season,
        ):
            with self.assertRaisesRegex(OpponentGenerationError, 'rolled back'):
                generate_opponent_pairings_1995(self.league.id, SEED)
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
            PAIRINGS_1978,
        )
        self.assertEqual(
            OpponentPairing.objects.filter(
                release=self.release,
                season_year__gte=1990,
                season_year__lte=1994,
            ).count(),
            PAIRINGS_1990,
        )
        self.assertFalse(
            OpponentPairing.objects.filter(release=self.release, season_year__gte=1995).exists()
        )

    def test_wrong_length_writes_nothing_and_leaves_the_card(self):
        profile = EraRuleProfile.objects.get(release=self.release, profile_key='y1995')
        EraRuleProfile.objects.filter(pk=profile.pk).update(schedule_length=14)
        with self.assertRaisesRegex(OpponentGenerationError, 'schedule_length'):
            generate_opponent_pairings_1995(self.league.id, SEED)
        self.assertEqual(OpponentPairing.objects.filter(release=self.release).count(), 0)
        profile.refresh_from_db()
        self.assertEqual(profile.schedule_length, 14)
        self.assertEqual(profile.profile_key, 'y1995')
        self.assertEqual(profile.first_year, 1995)
        self.assertEqual(profile.last_year, 1998)
        self.assertEqual(profile.playoff_format, 'eight_team_3_and_1')

    def test_older_pairings_and_weeks_stay_in_place(self):
        self.assertEqual(generate_opponent_pairings(self.league.id, SEED), 'created')
        self.assertEqual(generate_opponent_weeks(self.league.id, SEED), 'created')
        self.assertEqual(generate_opponent_pairings_1978(self.league.id, SEED), 'created')
        self.assertEqual(generate_opponent_pairings_1990(self.league.id, SEED), 'created')
        self.assertEqual(generate_opponent_weeks_1978(self.league.id, SEED), 'created')
        older = list(
            OpponentPairing.objects.filter(release=self.release, season_year__lte=1994)
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
        weeks = list(
            OpponentWeekAssignment.objects.filter(release=self.release)
            .order_by('id')
            .values_list('id', 'pairing_id', 'week', 'created_at')
        )
        self.assertEqual(len(weeks), EARLY_PAIRING_COUNT + WEEKS_1978)
        self.assertEqual(generate_opponent_pairings_1995(self.league.id, SEED), 'created')
        self.assertEqual(
            list(
                OpponentPairing.objects.filter(release=self.release, season_year__lte=1994)
                .order_by('id')
                .values_list(
                    'id',
                    'created_at',
                    'season_year',
                    'home_franchise_id',
                    'away_franchise_id',
                    'kind',
                )
            ),
            older,
        )
        self.assertEqual(
            list(
                OpponentWeekAssignment.objects.filter(release=self.release)
                .order_by('id')
                .values_list('id', 'pairing_id', 'week', 'created_at')
            ),
            weeks,
        )
        self.assertEqual(generate_opponent_pairings(self.league.id, SEED), 'unchanged')
        self.assertEqual(generate_opponent_pairings_1978(self.league.id, SEED), 'unchanged')
        self.assertEqual(generate_opponent_pairings_1990(self.league.id, SEED), 'unchanged')
        self.assertEqual(generate_opponent_weeks(self.league.id, SEED), 'unchanged')
        self.assertEqual(generate_opponent_weeks_1978(self.league.id, SEED), 'unchanged')
        self.assertEqual(
            OpponentPairing.objects.filter(
                release=self.release,
                season_year__gte=1995,
                season_year__lte=1998,
            ).count(),
            SPAN_GAMES,
        )
        self.assertFalse(
            OpponentWeekAssignment.objects.filter(
                release=self.release,
                pairing__season_year__gte=1995,
            ).exists()
        )

    def test_published_release_and_wrong_seed_write_nothing(self):
        UniverseRelease.objects.filter(pk=self.release.pk).update(
            status=UniverseRelease.Status.PUBLISHED,
            catalog_fingerprint='a' * 64,
            validation_fingerprint='b' * 64,
            published_at=timezone.now(),
        )
        with self.assertRaisesRegex(OpponentGenerationError, 'published'):
            generate_opponent_pairings_1995(self.league.id, SEED)
        self.assertEqual(OpponentPairing.objects.count(), 0)
        UniverseRelease.objects.filter(pk=self.release.pk).update(
            status=UniverseRelease.Status.DRAFT,
            catalog_fingerprint='preexisting',
            validation_fingerprint='',
            published_at=None,
        )
        with self.assertRaisesRegex(OpponentGenerationError, 'seed'):
            generate_opponent_pairings_1995(self.league.id, OTHER_SEED)
        self.assertEqual(OpponentPairing.objects.count(), 0)

    def test_command_reports_unchanged(self):
        created = StringIO()
        call_command(
            'generate_opponents_1995',
            league_id=self.league.id,
            seed=SEED,
            stdout=created,
        )
        self.assertEqual(created.getvalue().strip(), 'created')
        rerun = StringIO()
        call_command(
            'generate_opponents_1995',
            league_id=self.league.id,
            seed=SEED,
            stdout=rerun,
        )
        self.assertEqual(rerun.getvalue().strip(), 'unchanged')
        self.assertEqual(OpponentPairing.objects.filter(release=self.release).count(), SPAN_GAMES)
        self.assertEqual(OpponentWeekAssignment.objects.filter(release=self.release).count(), 0)
        with self.assertRaises(CommandError):
            call_command('generate_opponents_1995', league_id=self.league.id, seed=OTHER_SEED)
