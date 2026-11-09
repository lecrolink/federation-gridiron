from collections import Counter, defaultdict
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
    FIRST_SEASON,
    KIND_MATRIX,
    KIND_SWING_MEETING,
    LAST_SEASON,
    MATRIX_EARLY,
    MATRIX_SWING,
    ROLE_BY_DIVISION,
    SWING_INDEX,
    OpponentGenerationError,
    generate_opponent_pairings,
    position_for,
    repair_homes,
)
from universe.generation.prospects import later_profile_values
from universe.generation.schedule import (
    ScheduleGenerationError,
    generate_schedule_release,
)
from universe.models import FranchiseSeasonMembership, OpponentPairing, UniverseRelease
from universe.rules import EraRuleProfile


SEED = 1970
OTHER_SEED = 1971
PAIRING_COUNT = 6 * 182 + 2 * 196


class HomeRepairTests(SimpleTestCase):
    def test_search_starts_at_the_lowest_over_club_and_tries_neighbors_in_order(self):
        edges = [((1, 0), (1, 1)), ((1, 0), (1, 2))]
        quotas = {(1, 0): 0, (1, 1): 1, (1, 2): 1}

        homes = repair_homes(edges, quotas, pinned=set())

        self.assertEqual(homes, ((1, 1), (1, 2)))

    def test_a_pinned_edge_keeps_its_home(self):
        edges = [((1, 0), (1, 1)), ((1, 1), (1, 2))]
        quotas = {(1, 0): 1, (1, 1): 0, (1, 2): 1}

        homes = repair_homes(edges, quotas, pinned={0})

        self.assertEqual(homes[0], (1, 0))
        self.assertEqual(homes[1], (1, 2))

    def test_repair_fails_when_the_only_path_is_pinned(self):
        edges = [((1, 0), (1, 1))]
        quotas = {(1, 0): 0, (1, 1): 1}

        with self.assertRaises(OpponentGenerationError):
            repair_homes(edges, quotas, pinned={0})


class OpponentPairingTests(TestCase):
    def setUp(self):
        self.league = League(name='Opponent League', slug='opponent-league', tier=1, active=True)
        self.league.save()
        self.release = UniverseRelease(
            slug='opponents-1970',
            root_league=self.league,
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
        self.release.save()
        self.assertEqual(generate_schedule_release(self.league.id, SEED), 'created')
        self.release.refresh_from_db()
        EraRuleProfile(release=self.release, **y1970_values()).save()
        for values in later_profile_values():
            if values['profile_key'] in {'y1974', 'y1976'}:
                EraRuleProfile(release=self.release, **values).save()

    def memberships(self, season_year):
        return {
            row.franchise_id: row
            for row in FranchiseSeasonMembership.objects.filter(
                franchise__release=self.release,
                season_year=season_year,
            ).select_related('franchise')
        }

    def node(self, membership):
        role = ROLE_BY_DIVISION[membership.division_slug]
        conference = 1 if membership.conference_slug == 'conference-1' else 2
        if role != EAST and membership.slot_order == 5:
            return conference, SWING_INDEX
        return conference, position_for(role, membership.slot_order)

    def test_every_season_matches_the_approved_counts(self):
        self.assertEqual(generate_opponent_pairings(self.league.id, SEED), 'created')
        rows = list(OpponentPairing.objects.filter(release=self.release))
        self.assertEqual(len(rows), PAIRING_COUNT)
        by_season = defaultdict(list)
        for row in rows:
            by_season[row.season_year].append(row)

        matrix = Counter()
        for season_year in range(FIRST_SEASON, LAST_SEASON + 1):
            games = by_season[season_year]
            swing_year = season_year >= 1976
            self.assertEqual(len(games), 196 if swing_year else 182)
            kinds = Counter(game.kind for game in games)
            self.assertEqual(kinds[OpponentPairing.Kind.DIVISION], 88)
            self.assertEqual(kinds[OpponentPairing.Kind.INTRACONFERENCE], 54)
            self.assertEqual(
                kinds[KIND_MATRIX],
                MATRIX_SWING if swing_year else MATRIX_EARLY,
            )
            self.assertEqual(kinds[OpponentPairing.Kind.SWING_CONFERENCE], 26 if swing_year else 0)
            self.assertEqual(kinds[KIND_SWING_MEETING], 1 if swing_year else 0)
            if swing_year:
                self.assertEqual(kinds[KIND_MATRIX] + kinds[KIND_SWING_MEETING], 28)
            seats = self.memberships(season_year)
            played = Counter()
            homes = Counter()
            meetings = defaultdict(list)
            for game in games:
                self.assertIn(game.home_franchise_id, seats)
                self.assertIn(game.away_franchise_id, seats)
                self.assertNotEqual(game.home_franchise_id, game.away_franchise_id)
                for franchise_id in (game.home_franchise_id, game.away_franchise_id):
                    played[franchise_id] += 1
                homes[game.home_franchise_id] += 1
                meetings[frozenset((game.home_franchise_id, game.away_franchise_id))].append(game)
                if game.kind == KIND_MATRIX:
                    home = self.node(seats[game.home_franchise_id])
                    away = self.node(seats[game.away_franchise_id])
                    endpoints = {home, away}
                    left = next(node for node in endpoints if node[0] == 1)
                    right = next(node for node in endpoints if node[0] == 2)
                    self.assertNotEqual(left[1], SWING_INDEX)
                    self.assertNotEqual(right[1], SWING_INDEX)
                    matrix[(left[1], right[1])] += 1
            self.assertTrue(played)
            self.assertTrue(all(count == 14 for count in played.values()))
            self.assertTrue(all(count == 7 for count in homes.values()))
            self.assertEqual(sum(homes.values()), len(games))
            for pair, pair_games in meetings.items():
                if pair_games[0].kind == OpponentPairing.Kind.DIVISION:
                    self.assertEqual(len(pair_games), 2)
                    self.assertEqual(
                        {game.home_franchise_id for game in pair_games},
                        set(pair),
                    )
                else:
                    self.assertEqual(len(pair_games), 1)
            self._assert_directed_rules(season_year, games, seats)

        self.assertEqual(len(matrix), 169)
        self.assertEqual(Counter(matrix.values()), Counter({1: 44, 2: 125}))
        self.assertEqual(sum(matrix.values()), 294)
        self.assertEqual(
            OpponentPairing.objects.filter(
                release=self.release,
                kind=KIND_SWING_MEETING,
            ).count(),
            2,
        )

    def _assert_directed_rules(self, season_year, games, seats):
        by_node = {self.node(row): row for row in seats.values()}
        matrix_pairs = set()
        for game in games:
            if game.kind != KIND_MATRIX:
                continue
            home = self.node(seats[game.home_franchise_id])
            away = self.node(seats[game.away_franchise_id])
            left = home if home[0] == 1 else away
            right = away if away[0] == 2 else home
            matrix_pairs.add((left[1], right[1]))
        if season_year == 1972:
            for index in range(5):
                self.assertIn((index, index), matrix_pairs)
        if season_year == 1976:
            for index in range(13):
                self.assertNotIn((index, (index + 10) % 13), matrix_pairs)
                self.assertIn((index, (index + 11) % 13), matrix_pairs)
                self.assertIn((index, (index + 12) % 13), matrix_pairs)
        if season_year == 1977:
            for index in range(13):
                self.assertNotIn((index, (index + 11) % 13), matrix_pairs)
                self.assertIn((index, (index + 10) % 13), matrix_pairs)
                self.assertIn((index, (index + 12) % 13), matrix_pairs)
        if season_year >= 1976:
            meeting = next(game for game in games if game.kind == KIND_SWING_MEETING)
            host = seats[meeting.home_franchise_id]
            visitor = seats[meeting.away_franchise_id]
            self.assertEqual(host.conference_slug, 'conference-1')
            self.assertEqual(visitor.conference_slug, 'conference-2')
            self.assertEqual(self.node(host)[1], SWING_INDEX)
            self.assertEqual(self.node(visitor)[1], SWING_INDEX)
            swing_opponents = {
                game.away_franchise_id if game.home_franchise_id == host.franchise_id
                else game.home_franchise_id
                for game in games
                if host.franchise_id in (game.home_franchise_id, game.away_franchise_id)
            }
            conference_mates = {
                row.franchise_id for row in seats.values()
                if row.conference_slug == 'conference-1'
                and row.franchise_id != host.franchise_id
            }
            self.assertEqual(swing_opponents, conference_mates | {visitor.franchise_id})
            self.assertEqual(by_node[(1, SWING_INDEX)].franchise_id, host.franchise_id)

    def test_exact_rerun_writes_nothing_and_keeps_identifiers(self):
        self.assertEqual(generate_opponent_pairings(self.league.id, SEED), 'created')
        before = list(
            OpponentPairing.objects.filter(release=self.release)
            .order_by('id')
            .values_list('id', 'created_at', 'season_year', 'home_franchise_id', 'away_franchise_id', 'kind')
        )
        fingerprint = (
            self.release.catalog_fingerprint,
            self.release.validation_fingerprint,
            self.release.expansion_schedule_version,
        )
        with CaptureQueriesContext(connection) as captured:
            self.assertEqual(generate_opponent_pairings(self.league.id, SEED), 'unchanged')
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
        self.release.refresh_from_db()
        self.assertEqual(
            (
                self.release.catalog_fingerprint,
                self.release.validation_fingerprint,
                self.release.expansion_schedule_version,
            ),
            fingerprint,
        )
        self.assertEqual(generate_schedule_release(self.league.id, SEED), 'unchanged')
        self.assertEqual(OpponentPairing.objects.filter(release=self.release).count(), PAIRING_COUNT)

    def test_a_mismatched_or_partial_set_fails_closed(self):
        generate_opponent_pairings(self.league.id, SEED)
        game = OpponentPairing.objects.filter(
            release=self.release,
            season_year=1970,
            kind=KIND_MATRIX,
        ).order_by('id').first()
        OpponentPairing.objects.filter(pk=game.pk).update(
            home_franchise_id=game.away_franchise_id,
            away_franchise_id=game.home_franchise_id,
        )
        with self.assertRaisesRegex(OpponentGenerationError, 'will not delete'):
            generate_opponent_pairings(self.league.id, SEED)
        self.assertEqual(OpponentPairing.objects.filter(release=self.release).count(), PAIRING_COUNT)
        stored = OpponentPairing.objects.get(pk=game.pk)
        self.assertEqual(stored.home_franchise_id, game.away_franchise_id)
        self.assertEqual(stored.created_at, game.created_at)

        OpponentPairing.objects.filter(pk=game.pk).update(
            home_franchise_id=game.home_franchise_id,
            away_franchise_id=game.away_franchise_id,
        )
        OpponentPairing.objects.filter(release=self.release, season_year=1974).delete()
        with self.assertRaisesRegex(OpponentGenerationError, 'will not delete'):
            generate_opponent_pairings(self.league.id, SEED)
        self.assertFalse(
            OpponentPairing.objects.filter(release=self.release, season_year=1974).exists()
        )
        self.assertEqual(
            OpponentPairing.objects.filter(release=self.release, season_year=1970).count(),
            182,
        )

    def test_an_extra_row_fails_closed(self):
        generate_opponent_pairings(self.league.id, SEED)
        stored = {
            (row.season_year, row.home_franchise_id, row.away_franchise_id)
            for row in OpponentPairing.objects.filter(release=self.release, season_year=1971)
        }
        seats = list(self.memberships(1971).values())
        extra = None
        for home in seats:
            for away in seats:
                if home.franchise_id == away.franchise_id:
                    continue
                key = (1971, home.franchise_id, away.franchise_id)
                if key not in stored:
                    extra = key
                    break
            if extra:
                break
        OpponentPairing(
            release=self.release,
            season_year=1971,
            home_franchise_id=extra[1],
            away_franchise_id=extra[2],
            kind=KIND_MATRIX,
        ).save()
        with self.assertRaisesRegex(OpponentGenerationError, 'will not delete'):
            generate_opponent_pairings(self.league.id, SEED)
        self.assertEqual(OpponentPairing.objects.filter(release=self.release).count(), PAIRING_COUNT + 1)

    def test_an_insert_failure_rolls_back_the_span(self):
        real = OpponentPairing.objects.bulk_create
        calls = {'n': 0}

        def fail_second_season(rows, **kwargs):
            calls['n'] += 1
            if calls['n'] == 2:
                raise IntegrityError('forced')
            return real(rows, **kwargs)

        with patch(
            'universe.generation.opponents.OpponentPairing.objects.bulk_create',
            fail_second_season,
        ):
            with self.assertRaisesRegex(OpponentGenerationError, 'rolled back'):
                generate_opponent_pairings(self.league.id, SEED)
        self.assertEqual(calls['n'], 2)
        self.assertEqual(OpponentPairing.objects.filter(release=self.release).count(), 0)
        self.release.refresh_from_db()
        self.assertEqual(self.release.catalog_fingerprint, 'preexisting')
        self.assertEqual(self.release.validation_fingerprint, '')

    def test_published_release_and_wrong_length_write_nothing(self):
        UniverseRelease.objects.filter(pk=self.release.pk).update(
            status=UniverseRelease.Status.PUBLISHED,
            catalog_fingerprint='a' * 64,
            validation_fingerprint='b' * 64,
            published_at=timezone.now(),
        )
        with self.assertRaisesRegex(OpponentGenerationError, 'published'):
            generate_opponent_pairings(self.league.id, SEED)
        self.assertEqual(OpponentPairing.objects.count(), 0)

        UniverseRelease.objects.filter(pk=self.release.pk).update(
            status=UniverseRelease.Status.DRAFT,
            catalog_fingerprint='preexisting',
            validation_fingerprint='',
            published_at=None,
        )
        EraRuleProfile.objects.filter(release=self.release, profile_key='y1976').update(
            schedule_length=16,
        )
        with self.assertRaisesRegex(OpponentGenerationError, 'schedule_length'):
            generate_opponent_pairings(self.league.id, SEED)
        self.assertEqual(OpponentPairing.objects.count(), 0)

        EraRuleProfile.objects.filter(release=self.release, profile_key='y1976').update(
            schedule_length=14,
        )
        EraRuleProfile.objects.filter(release=self.release, profile_key='y1974').delete()
        with self.assertRaisesRegex(OpponentGenerationError, 'era profile'):
            generate_opponent_pairings(self.league.id, SEED)
        self.assertEqual(OpponentPairing.objects.count(), 0)

    def test_schedule_rebuild_leaves_pairings_in_place(self):
        generate_opponent_pairings(self.league.id, SEED)
        with self.assertRaisesRegex(ScheduleGenerationError, 'Opponent pairings'):
            generate_schedule_release(self.league.id, OTHER_SEED)
        self.assertEqual(OpponentPairing.objects.filter(release=self.release).count(), PAIRING_COUNT)
        self.assertEqual(self.release.franchises.count(), 32)

    def test_command_reports_unchanged(self):
        created = StringIO()
        call_command('generate_opponents', league_id=self.league.id, seed=SEED, stdout=created)
        self.assertEqual(created.getvalue().strip(), 'created')
        rerun = StringIO()
        call_command('generate_opponents', league_id=self.league.id, seed=SEED, stdout=rerun)
        self.assertEqual(rerun.getvalue().strip(), 'unchanged')
        self.assertEqual(OpponentPairing.objects.filter(release=self.release).count(), PAIRING_COUNT)
        with self.assertRaises(CommandError):
            call_command('generate_opponents', league_id=self.league.id, seed=OTHER_SEED)
