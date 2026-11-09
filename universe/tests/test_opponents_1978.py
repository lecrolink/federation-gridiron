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
    CENTRAL,
    EAST,
    WEST,
    OpponentGenerationError,
    Seat,
    generate_opponent_pairings,
)
from universe.generation.opponents_1978 import (
    CROSS_GAMES,
    FIRST_SEASON,
    KIND_INTER,
    LAST_SEASON,
    POSITIONS,
    REQUIRED_SCHEDULE_LENGTH,
    SEASON_GAMES,
    build_season,
    cross_offsets,
    generate_opponent_pairings_1978,
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
EARLY_PAIRING_COUNT = 6 * 182 + 2 * 196
SPAN_GAMES = 12 * SEASON_GAMES
ROLE_RANK = {EAST: 0, CENTRAL: 1, WEST: 2}


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


class SixteenGameRuleTests(SimpleTestCase):
    def test_every_season_is_deterministic_and_balanced(self):
        seats = _sample_seats()
        first = {year: build_season(year, seats, 16) for year in range(FIRST_SEASON, LAST_SEASON + 1)}
        second = {year: build_season(year, seats, 16) for year in range(FIRST_SEASON, LAST_SEASON + 1)}
        self.assertEqual(first, second)
        meetings = Counter()
        for year, pairings in first.items():
            self.assertEqual(len(pairings), SEASON_GAMES)
            self.assertEqual(cross_offsets(year), tuple((year - FIRST_SEASON + delta) % POSITIONS for delta in (0, 2, 4)))
            homes = Counter()
            games = Counter()
            kinds = Counter()
            seen = set()
            cross = set()
            for pairing in pairings:
                games[pairing.home_franchise_id] += 1
                games[pairing.away_franchise_id] += 1
                homes[pairing.home_franchise_id] += 1
                kinds[pairing.kind] += 1
                pair = frozenset((pairing.home_franchise_id, pairing.away_franchise_id))
                self.assertNotIn(pair, seen)
                seen.add(pair)
                if pairing.kind == KIND_INTER:
                    left = pairing.home_node if pairing.home_node[0] == 1 else pairing.away_node
                    right = pairing.away_node if pairing.away_node[0] == 2 else pairing.home_node
                    cross.add((_position(left), _position(right)))
            self.assertEqual(set(games.values()), {REQUIRED_SCHEDULE_LENGTH})
            self.assertEqual(set(homes.values()), {REQUIRED_SCHEDULE_LENGTH // 2})
            self.assertEqual(kinds[OpponentPairing.Kind.DIVISION], 52)
            self.assertEqual(kinds[OpponentPairing.Kind.INTRACONFERENCE], 130)
            self.assertEqual(kinds[KIND_INTER], 42)
            self.assertEqual(cross, {
                (index, (index + offset) % POSITIONS)
                for index in range(POSITIONS)
                for offset in cross_offsets(year)
            })
            meetings.update(cross)
            four_team = _franchise_at(pairings, (1, ROLE_RANK[CENTRAL], 1))
            five_team = _franchise_at(pairings, (1, ROLE_RANK[EAST], 1))
            self.assertEqual(_counts_for(pairings, four_team), (3, 10, CROSS_GAMES))
            self.assertEqual(_counts_for(pairings, five_team), (4, 9, CROSS_GAMES))
        self.assertEqual(Counter(meetings.values()), Counter({2: 84, 3: 112}))
        self.assertEqual(len(meetings), POSITIONS * POSITIONS)

    def test_rotation_advances_one_seat_each_season(self):
        seats = _sample_seats()
        previous = None
        for year in range(FIRST_SEASON, LAST_SEASON + 1):
            opponents = _cross_opponents(build_season(year, seats, 16), 0)
            self.assertEqual(
                opponents,
                {(0 + offset) % POSITIONS for offset in cross_offsets(year)},
            )
            if previous is not None:
                self.assertEqual(opponents, {(index + 1) % POSITIONS for index in previous})
            previous = opponents

    def test_bad_length_year_or_seats_are_refused(self):
        seats = _sample_seats()
        with self.assertRaisesRegex(OpponentGenerationError, 'schedule_length'):
            build_season(1978, seats, 14)
        with self.assertRaisesRegex(OpponentGenerationError, '1978–1989'):
            build_season(1977, seats, 16)
        with self.assertRaisesRegex(OpponentGenerationError, '1978–1989'):
            build_season(1990, seats, 16)
        short = tuple(seat for seat in seats if not (seat.conference == 1 and seat.role == WEST and seat.slot == 5))
        with self.assertRaisesRegex(OpponentGenerationError, 'do not match the rule'):
            build_season(1978, short, 16)


class OpponentPairing1978Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.league = League(name='Sixteen Game League', slug='sixteen-game-league', tier=1, active=True)
        cls.league.save()
        cls.release = UniverseRelease(
            slug='opponents-1978',
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
            role = { 'd3': EAST, 'd1': CENTRAL, 'd2': WEST }[row.division_slug]
            seats.append(Seat(row.franchise_id, conference, role, row.slot_order))
        return tuple(seats)

    def test_stored_rows_match_the_rule_for_every_season(self):
        self.assertEqual(generate_opponent_pairings_1978(self.league.id, SEED), 'created')
        self.assertEqual(
            OpponentPairing.objects.filter(release=self.release).count(),
            SPAN_GAMES,
        )
        self.assertEqual(OpponentWeekAssignment.objects.filter(release=self.release).count(), 0)
        self.assertFalse(
            OpponentPairing.objects.filter(
                release=self.release,
                season_year__lt=FIRST_SEASON,
            ).exists()
        )
        self.assertFalse(
            OpponentPairing.objects.filter(
                release=self.release,
                season_year__gt=LAST_SEASON,
            ).exists()
        )
        matrix = Counter()
        for season_year in range(FIRST_SEASON, LAST_SEASON + 1):
            stored = list(
                OpponentPairing.objects.filter(release=self.release, season_year=season_year)
            )
            built = build_season(season_year, self.seats(season_year), 16)
            self.assertEqual(
                {
                    (row.season_year, row.home_franchise_id, row.away_franchise_id, row.kind)
                    for row in stored
                },
                {
                    (row.season_year, row.home_franchise_id, row.away_franchise_id, row.kind)
                    for row in built
                },
            )
            self.assertEqual(len(stored), SEASON_GAMES)
            homes = Counter(row.home_franchise_id for row in stored)
            played = Counter()
            for row in stored:
                played[row.home_franchise_id] += 1
                played[row.away_franchise_id] += 1
            self.assertEqual(set(played.values()), {REQUIRED_SCHEDULE_LENGTH})
            self.assertEqual(set(homes.values()), {8})
            self.assertEqual(len(played), 28)
            for row in stored:
                if row.kind != KIND_INTER:
                    continue
                home = next(seat for seat in self.seats(season_year) if seat.franchise_id == row.home_franchise_id)
                away = next(seat for seat in self.seats(season_year) if seat.franchise_id == row.away_franchise_id)
                left = home if home.conference == 1 else away
                right = away if away.conference == 2 else home
                matrix[(_position((left.conference, ROLE_RANK[left.role], left.slot)), _position((right.conference, ROLE_RANK[right.role], right.slot)))] += 1
        self.assertEqual(Counter(matrix.values()), Counter({2: 84, 3: 112}))
        self.release.refresh_from_db()
        self.assertEqual(self.release.catalog_fingerprint, 'preexisting')
        self.assertEqual(self.release.validation_fingerprint, '')

    def test_exact_rerun_writes_nothing(self):
        self.assertEqual(generate_opponent_pairings_1978(self.league.id, SEED), 'created')
        before = list(
            OpponentPairing.objects.filter(release=self.release)
            .order_by('id')
            .values_list('id', 'created_at', 'season_year', 'home_franchise_id', 'away_franchise_id', 'kind')
        )
        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(generate_opponent_pairings_1978(self.league.id, SEED), 'unchanged')
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
        generate_opponent_pairings_1978(self.league.id, SEED)
        OpponentPairing.objects.filter(release=self.release, season_year=1984).delete()
        with self.assertRaisesRegex(OpponentGenerationError, 'will not delete'):
            generate_opponent_pairings_1978(self.league.id, SEED)
        self.assertFalse(
            OpponentPairing.objects.filter(release=self.release, season_year=1984).exists()
        )
        self.assertEqual(
            OpponentPairing.objects.filter(release=self.release, season_year=1983).count(),
            SEASON_GAMES,
        )

        game = OpponentPairing.objects.filter(
            release=self.release,
            season_year=1978,
            kind=KIND_INTER,
        ).order_by('id').first()
        OpponentPairing.objects.filter(pk=game.pk).update(
            home_franchise_id=game.away_franchise_id,
            away_franchise_id=game.home_franchise_id,
        )
        with self.assertRaisesRegex(OpponentGenerationError, 'will not delete'):
            generate_opponent_pairings_1978(self.league.id, SEED)
        stored = OpponentPairing.objects.get(pk=game.pk)
        self.assertEqual(stored.home_franchise_id, game.away_franchise_id)
        self.assertEqual(stored.away_franchise_id, game.home_franchise_id)
        self.assertEqual(stored.created_at, game.created_at)

    def test_an_extra_row_fails_closed(self):
        generate_opponent_pairings_1978(self.league.id, SEED)
        stored = {
            (row.home_franchise_id, row.away_franchise_id)
            for row in OpponentPairing.objects.filter(release=self.release, season_year=1980)
        }
        seats = self.seats(1980)
        extra = None
        for home in seats:
            for away in seats:
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
            season_year=1980,
            home_franchise_id=extra[0],
            away_franchise_id=extra[1],
            kind=KIND_INTER,
        ).save()
        with self.assertRaisesRegex(OpponentGenerationError, 'will not delete'):
            generate_opponent_pairings_1978(self.league.id, SEED)
        self.assertEqual(
            OpponentPairing.objects.filter(release=self.release).count(),
            SPAN_GAMES + 1,
        )

    def test_changed_seats_do_not_replace_stored_rows(self):
        generate_opponent_pairings_1978(self.league.id, SEED)
        before = list(
            OpponentPairing.objects.filter(release=self.release)
            .order_by('id')
            .values_list('id', 'season_year', 'home_franchise_id', 'away_franchise_id', 'kind')
        )
        east = list(
            FranchiseSeasonMembership.objects.filter(
                franchise__release=self.release,
                season_year=1978,
                conference_slug='conference-1',
                division_slug='d3',
            ).order_by('slot_order')[:2]
        )
        FranchiseSeasonMembership.objects.filter(pk=east[0].pk).update(slot_order=east[1].slot_order)
        FranchiseSeasonMembership.objects.filter(pk=east[1].pk).update(slot_order=east[0].slot_order)
        with self.assertRaisesRegex(OpponentGenerationError, 'will not delete'):
            generate_opponent_pairings_1978(self.league.id, SEED)
        after = list(
            OpponentPairing.objects.filter(release=self.release)
            .order_by('id')
            .values_list('id', 'season_year', 'home_franchise_id', 'away_franchise_id', 'kind')
        )
        self.assertEqual(before, after)

    def test_insert_failure_rolls_back_and_keeps_the_earlier_span(self):
        self.assertEqual(generate_opponent_pairings(self.league.id, SEED), 'created')
        real = OpponentPairing.objects.bulk_create
        calls = {'n': 0}

        def fail_second_season(rows, **kwargs):
            calls['n'] += 1
            if calls['n'] == 2:
                raise IntegrityError('forced')
            return real(rows, **kwargs)

        with patch(
            'universe.generation.opponents_1978.OpponentPairing.objects.bulk_create',
            fail_second_season,
        ):
            with self.assertRaisesRegex(OpponentGenerationError, 'rolled back'):
                generate_opponent_pairings_1978(self.league.id, SEED)
        self.assertEqual(calls['n'], 2)
        self.assertEqual(
            OpponentPairing.objects.filter(release=self.release, season_year__lte=1977).count(),
            EARLY_PAIRING_COUNT,
        )
        self.assertFalse(
            OpponentPairing.objects.filter(release=self.release, season_year__gte=1978).exists()
        )

    def test_wrong_length_or_missing_profile_writes_nothing(self):
        EraRuleProfile.objects.filter(release=self.release, profile_key='y1978').update(
            schedule_length=14,
        )
        with self.assertRaisesRegex(OpponentGenerationError, 'schedule_length'):
            generate_opponent_pairings_1978(self.league.id, SEED)
        self.assertEqual(OpponentPairing.objects.filter(release=self.release, season_year__gte=1978).count(), 0)

        EraRuleProfile.objects.filter(release=self.release, profile_key='y1978').update(
            schedule_length=16,
        )
        EraRuleProfile.objects.filter(release=self.release, profile_key='y1987').delete()
        with self.assertRaisesRegex(OpponentGenerationError, 'era profile'):
            generate_opponent_pairings_1978(self.league.id, SEED)
        self.assertEqual(OpponentPairing.objects.filter(release=self.release).count(), 0)

    def test_published_release_and_wrong_seed_write_nothing(self):
        UniverseRelease.objects.filter(pk=self.release.pk).update(
            status=UniverseRelease.Status.PUBLISHED,
            catalog_fingerprint='a' * 64,
            validation_fingerprint='b' * 64,
            published_at=timezone.now(),
        )
        with self.assertRaisesRegex(OpponentGenerationError, 'published'):
            generate_opponent_pairings_1978(self.league.id, SEED)
        self.assertEqual(OpponentPairing.objects.count(), 0)

        UniverseRelease.objects.filter(pk=self.release.pk).update(
            status=UniverseRelease.Status.DRAFT,
            catalog_fingerprint='preexisting',
            validation_fingerprint='',
            published_at=None,
        )
        with self.assertRaisesRegex(OpponentGenerationError, 'seed'):
            generate_opponent_pairings_1978(self.league.id, OTHER_SEED)
        self.assertEqual(OpponentPairing.objects.count(), 0)

    def test_earlier_pairings_and_weeks_stay_in_place(self):
        self.assertEqual(generate_opponent_pairings(self.league.id, SEED), 'created')
        self.assertEqual(generate_opponent_weeks(self.league.id, SEED), 'created')
        early = list(
            OpponentPairing.objects.filter(release=self.release, season_year__lte=1977)
            .order_by('id')
            .values_list('id', 'created_at', 'season_year', 'home_franchise_id', 'away_franchise_id', 'kind')
        )
        weeks = list(
            OpponentWeekAssignment.objects.filter(release=self.release)
            .order_by('id')
            .values_list('id', 'pairing_id', 'week')
        )
        self.assertEqual(generate_opponent_pairings_1978(self.league.id, SEED), 'created')
        self.assertEqual(
            list(
                OpponentPairing.objects.filter(release=self.release, season_year__lte=1977)
                .order_by('id')
                .values_list('id', 'created_at', 'season_year', 'home_franchise_id', 'away_franchise_id', 'kind')
            ),
            early,
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
        self.assertEqual(generate_opponent_weeks(self.league.id, SEED), 'unchanged')
        self.assertEqual(
            OpponentPairing.objects.filter(release=self.release, season_year__gte=1978).count(),
            SPAN_GAMES,
        )

    def test_command_reports_unchanged(self):
        created = StringIO()
        call_command(
            'generate_opponents_1978',
            league_id=self.league.id,
            seed=SEED,
            stdout=created,
        )
        self.assertEqual(created.getvalue().strip(), 'created')
        rerun = StringIO()
        call_command(
            'generate_opponents_1978',
            league_id=self.league.id,
            seed=SEED,
            stdout=rerun,
        )
        self.assertEqual(rerun.getvalue().strip(), 'unchanged')
        self.assertEqual(OpponentPairing.objects.filter(release=self.release).count(), SPAN_GAMES)
        with self.assertRaises(CommandError):
            call_command('generate_opponents_1978', league_id=self.league.id, seed=OTHER_SEED)


def _position(node):
    conference, rank, slot = node
    before = 0
    sizes = {1: (5, 4, 5), 2: (5, 5, 4)}[conference]
    for role_rank, size in enumerate(sizes):
        if role_rank == rank:
            return before + slot - 1
        before += size
    raise AssertionError(node)


def _cross_opponents(pairings, position):
    found = set()
    for pairing in pairings:
        if pairing.kind != KIND_INTER:
            continue
        left = pairing.home_node if pairing.home_node[0] == 1 else pairing.away_node
        right = pairing.away_node if pairing.away_node[0] == 2 else pairing.home_node
        if _position(left) == position:
            found.add(_position(right))
    return found


def _franchise_at(pairings, node):
    for pairing in pairings:
        if pairing.home_node == node:
            return pairing.home_franchise_id
        if pairing.away_node == node:
            return pairing.away_franchise_id
    raise AssertionError(node)


def _counts_for(pairings, franchise_id):
    kinds = Counter(
        pairing.kind
        for pairing in pairings
        if franchise_id in (pairing.home_franchise_id, pairing.away_franchise_id)
    )
    return (
        kinds[OpponentPairing.Kind.DIVISION],
        kinds[OpponentPairing.Kind.INTRACONFERENCE],
        kinds[KIND_INTER],
    )
