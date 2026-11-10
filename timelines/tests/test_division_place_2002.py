import hashlib
import hmac
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from leagues.models import League
from timelines.models import (
    ScheduleGame,
    ScheduleGameResult,
    Timeline,
    TimelineDivisionDraw,
    TimelineDivisionPlace,
    TimelinePriorFinish,
    TimelineScheduleSeed,
    TimelineSchedulingDraw,
    TimelineSchedulingRank,
    TimelineSeason,
    writing_division_rows,
)
from timelines.services.division_place_2002 import (
    DRAW,
    HEAD_TO_HEAD,
    Club,
    DivisionPlaceRefused,
    Result,
    ScoredGame,
    _cmp_better,
    _fraction,
    close_2002_regular_season,
    competition_rank,
    division_draw_label,
    rank_places,
)
from timelines.services.prior_finish import (
    Finish,
    derive_scheduling_ranks,
    store_prior_finishes,
)
from timelines.services.schedule_2002 import generate_timeline_schedule
from universe.generation.prospects import later_profile_values
from universe.generation.schedule import generate_schedule_release
from universe.models import (
    FranchiseSeasonMembership,
    HistoricalStanding,
    OpponentPairing,
    OpponentWeekAssignment,
    UniverseFranchise,
    UniverseRelease,
)
from universe.rules import EraRuleProfile


WRITE_WORDS = ('INSERT', 'UPDATE', 'DELETE', 'REPLACE')
PROFILE_KEYS = {'y1999', 'y2002'}


def _writes_during(callback):
    statements = []

    def wrap(execute, sql, params, many, context):
        statements.append(sql)
        return execute(sql, params, many, context)

    connection.execute_wrappers.append(wrap)
    try:
        result = callback()
    finally:
        connection.execute_wrappers.remove(wrap)
    writes = [
        sql for sql in statements
        if sql.lstrip().split(None, 1)[0].strip('"').upper() in WRITE_WORDS
    ]
    return result, writes


def _club(key, conference='c1', division='d1'):
    return Club(key, conference, division)


def _game(home, away, home_score, away_score, home_td=0, away_td=0):
    return ScoredGame(home, away, home_score, away_score, home_td, away_td)


def _rank(clubs, games, choose=None):
    def refuse(*_args):
        raise AssertionError('This ranking reached the draw.')

    return rank_places(clubs, games, choose or refuse)


def _by_key(places):
    return {place.key: place for place in places}


class CompetitionRankTests(SimpleTestCase):
    def test_tied_values_share_the_best_rank_and_skip_the_next(self):
        items = ['a', 'b', 'c', 'd']
        scores = {'a': 10, 'b': 10, 'c': 9, 'd': 8}
        ranks = competition_rank(
            items,
            key=lambda name: scores[name],
            higher_better=True,
        )
        self.assertEqual(ranks, [1, 1, 3, 4])

    def test_points_allowed_ranks_the_lower_total_first(self):
        items = ['a', 'b', 'c']
        allowed = {'a': 20, 'b': 10, 'c': 10}
        ranks = competition_rank(
            items,
            key=lambda name: allowed[name],
            higher_better=False,
        )
        self.assertEqual(ranks, [3, 1, 1])


class FractionTests(SimpleTestCase):
    def test_equal_rates_match_and_a_half_win_is_exact(self):
        short = _fraction(1, 1, 0)
        long = _fraction(2, 2, 0)
        self.assertEqual(_cmp_better(short, long), 0)
        ahead = _fraction(2, 1, 0)
        half = _fraction(1, 1, 1)
        self.assertEqual(_cmp_better(ahead, half), -1)
        self.assertEqual(_fraction(9, 7, 0)[1:], (18, 32))
        self.assertEqual(_fraction(8, 7, 1)[1:], (17, 32))
        self.assertEqual(
            _cmp_better(_fraction(9, 7, 0), _fraction(8, 7, 1)),
            -1,
        )


class DivisionProcedureTests(SimpleTestCase):
    def test_winning_percentage_orders_places_one_through_four(self):
        clubs = [_club(name) for name in ('a', 'b', 'c', 'd')]
        games = [
            _game('a', 'b', 1, 0),
            _game('a', 'c', 1, 0),
            _game('a', 'd', 1, 0),
            _game('b', 'c', 1, 0),
            _game('b', 'd', 1, 0),
            _game('c', 'd', 1, 0),
        ]
        places, draws = _rank(clubs, games)
        found = _by_key(places)
        self.assertEqual(
            [(found[name].place, found[name].resolved_by) for name in ('a', 'b', 'c', 'd')],
            [
                (1, 'winning_percentage'),
                (2, 'winning_percentage'),
                (3, 'winning_percentage'),
                (4, 'sole_remaining'),
            ],
        )
        self.assertEqual(draws, ())

    def test_a_half_win_ranks_ahead_of_an_even_split(self):
        clubs = [
            _club('a'),
            _club('b'),
            _club('x', 'c2', 'd1'),
            _club('y', 'c2', 'd1'),
            _club('p', 'c2', 'd2'),
            _club('q', 'c2', 'd2'),
        ]
        games = [
            _game('a', 'x', 10, 0),
            _game('a', 'y', 3, 3),
            _game('b', 'p', 10, 0),
            _game('b', 'q', 0, 10),
        ]
        places, _draws = _rank(clubs, games)
        found = _by_key(places)
        self.assertEqual(found['a'].place, 1)
        self.assertEqual(found['a'].resolved_by, 'winning_percentage')
        self.assertEqual(found['b'].place, 2)
        self.assertEqual(found['b'].resolved_by, 'sole_remaining')

    def test_two_clubs_are_separated_by_head_to_head(self):
        clubs = [_club('a'), _club('b'), _club('x', 'c2', 'd1')]
        games = [
            _game('a', 'b', 24, 10),
            _game('x', 'a', 21, 0),
            _game('b', 'x', 17, 7),
        ]
        places, _draws = _rank(clubs, games)
        found = _by_key(places)
        self.assertEqual(found['a'].resolved_by, HEAD_TO_HEAD)
        self.assertEqual(found['b'].resolved_by, 'sole_remaining')

    def test_three_clubs_use_the_group_head_to_head_record(self):
        clubs = [
            _club('a'),
            _club('b'),
            _club('c'),
            _club('x', 'c2', 'd1'),
            _club('y', 'c2', 'd1'),
        ]
        games = [
            _game('a', 'b', 1, 0),
            _game('a', 'c', 1, 0),
            _game('b', 'c', 1, 0),
            _game('x', 'a', 1, 0),
            _game('y', 'a', 1, 0),
            _game('b', 'x', 1, 0),
            _game('y', 'b', 1, 0),
            _game('c', 'x', 1, 0),
            _game('c', 'y', 1, 0),
        ]
        places, _draws = _rank(clubs, games)
        found = _by_key(places)
        self.assertEqual((found['a'].place, found['a'].resolved_by), (1, HEAD_TO_HEAD))
        self.assertEqual(found['b'].place, 2)
        self.assertEqual(found['c'].place, 3)

    def test_three_clubs_reduced_to_two_restart_at_head_to_head(self):
        clubs = [
            _club('a'),
            _club('b'),
            _club('c'),
            _club('d'),
            _club('x', 'c2', 'd1'),
        ]
        games = [
            _game('a', 'b', 1, 0),
            _game('b', 'c', 1, 0),
            _game('c', 'a', 1, 0),
            _game('a', 'd', 1, 0),
            _game('b', 'd', 1, 0),
            _game('d', 'c', 1, 0),
            _game('c', 'x', 1, 0),
            _game('x', 'a', 1, 0),
            _game('x', 'b', 1, 0),
            _game('x', 'd', 1, 0),
        ]
        places, _draws = _rank(clubs, games)
        found = _by_key(places)
        self.assertEqual(
            [(found[name].place, found[name].resolved_by) for name in ('a', 'b', 'c', 'd')],
            [
                (1, HEAD_TO_HEAD),
                (2, HEAD_TO_HEAD),
                (3, 'winning_percentage'),
                (4, 'sole_remaining'),
            ],
        )

    def test_four_clubs_reduced_to_two_restart_at_head_to_head(self):
        clubs = [
            _club('a'),
            _club('b'),
            _club('c'),
            _club('d'),
            _club('x', 'c2', 'd1'),
            _club('y', 'c2', 'd2'),
            _club('z', 'c2', 'd2'),
        ]
        games = [
            _game('a', 'b', 1, 0),
            _game('c', 'a', 1, 0),
            _game('a', 'd', 1, 1),
            _game('b', 'c', 1, 0),
            _game('b', 'd', 1, 1),
            _game('c', 'd', 1, 1),
            _game('a', 'x', 1, 0),
            _game('b', 'x', 1, 0),
            _game('x', 'c', 1, 0),
            _game('x', 'd', 1, 0),
            _game('y', 'a', 1, 0),
            _game('y', 'b', 1, 0),
            _game('c', 'z', 1, 0),
            _game('d', 'z', 1, 0),
        ]
        places, _draws = _rank(clubs, games)
        found = _by_key(places)
        self.assertEqual(
            [(found[name].place, found[name].resolved_by) for name in ('a', 'b', 'c', 'd')],
            [
                (1, HEAD_TO_HEAD),
                (2, HEAD_TO_HEAD),
                (3, 'strength_of_victory'),
                (4, 'sole_remaining'),
            ],
        )

    def test_common_games_use_only_the_shared_opponents(self):
        clubs = [
            _club('a'),
            _club('b'),
            _club('x', 'c2', 'd1'),
            _club('y', 'c2', 'd1'),
            _club('z', 'c2', 'd1'),
        ]
        games = [
            _game('a', 'b', 10, 10),
            _game('a', 'x', 7, 0),
            _game('a', 'y', 0, 50),
            _game('x', 'b', 7, 0),
            _game('b', 'z', 50, 0),
        ]
        places, _draws = _rank(clubs, games)
        found = _by_key(places)
        self.assertEqual((found['a'].place, found['a'].resolved_by), (1, 'common_games'))
        self.assertEqual(found['b'].place, 2)

    def test_an_empty_common_set_does_not_separate_the_clubs(self):
        clubs = [
            _club('a'),
            _club('b'),
            _club('y', 'c1', 'd2'),
            _club('z', 'c1', 'd2'),
            _club('w', 'c2', 'd1'),
            _club('v', 'c2', 'd1'),
            _club('x', 'c2', 'd2'),
            _club('p', 'c2', 'd2'),
        ]
        games = [
            _game('a', 'b', 10, 10),
            _game('a', 'x', 10, 10),
            _game('b', 'x', 10, 10),
            _game('a', 'y', 21, 0),
            _game('a', 'w', 0, 7),
            _game('b', 'z', 0, 14),
            _game('b', 'v', 14, 0),
        ]
        places, _draws = _rank(clubs, games)
        self.assertEqual(_by_key(places)['a'].resolved_by, 'conference_percentage')

    def test_division_percentage_uses_the_whole_division(self):
        clubs = [
            _club('a'),
            _club('b'),
            _club('c'),
            _club('d'),
            _club('x', 'c2', 'd1'),
            _club('y', 'c2', 'd1'),
            _club('p', 'c2', 'd2'),
        ]
        games = [
            _game('a', 'b', 3, 3),
            _game('a', 'c', 1, 0),
            _game('a', 'd', 1, 0),
            _game('c', 'b', 1, 0),
            _game('d', 'b', 1, 0),
            _game('x', 'a', 1, 0),
            _game('y', 'a', 1, 0),
            _game('b', 'x', 1, 0),
            _game('b', 'y', 1, 0),
            _game('c', 'x', 0, 1),
            _game('c', 'y', 0, 1),
            _game('d', 'x', 0, 1),
            _game('d', 'y', 0, 1),
            _game('x', 'y', 1, 0),
            _game('c', 'p', 1, 0),
            _game('p', 'd', 1, 0),
        ]
        places, _draws = _rank(clubs, games)
        self.assertEqual(_by_key(places)['a'].resolved_by, 'division_percentage')

    def test_repeated_wins_weight_strength_of_victory(self):
        clubs = [
            _club('a'),
            _club('b'),
            _club('x', 'c2', 'd1'),
            _club('y', 'c2', 'd1'),
            _club('z', 'c2', 'd1'),
        ]
        games = [
            _game('a', 'b', 0, 0),
            _game('a', 'x', 7, 0),
            _game('x', 'a', 0, 7),
            _game('a', 'y', 7, 0),
            _game('b', 'x', 7, 0),
            _game('b', 'y', 7, 0),
            _game('y', 'b', 0, 7),
            _game('x', 'z', 7, 0),
            _game('z', 'x', 0, 7),
        ]
        places, _draws = _rank(clubs, games)
        self.assertEqual(_by_key(places)['a'].resolved_by, 'strength_of_victory')

    def test_repeated_games_weight_strength_of_schedule(self):
        clubs = [
            _club('a'),
            _club('b'),
            _club('x', 'c2', 'd1'),
            _club('y', 'c2', 'd1'),
            _club('z', 'c2', 'd1'),
        ]
        games = [
            _game('a', 'b', 0, 0),
            _game('x', 'a', 7, 0),
            _game('a', 'x', 0, 7),
            _game('y', 'a', 7, 0),
            _game('x', 'b', 7, 0),
            _game('y', 'b', 7, 0),
            _game('b', 'y', 0, 7),
            _game('x', 'z', 7, 0),
            _game('z', 'y', 7, 0),
        ]
        places, _draws = _rank(clubs, games)
        self.assertEqual(_by_key(places)['a'].resolved_by, 'strength_of_schedule')

    def test_conference_rank_prefers_the_lower_sum(self):
        clubs = [_club('a'), _club('b'), _club('x', 'c2', 'd1')]
        games = [
            _game('a', 'b', 10, 10),
            _game('a', 'x', 30, 0),
            _game('b', 'x', 14, 0),
        ]
        places, _draws = _rank(clubs, games)
        self.assertEqual(_by_key(places)['a'].resolved_by, 'conference_rank')

    def test_league_rank_can_separate_clubs_whose_conference_sums_tie(self):
        clubs = [_club('a'), _club('b'), _club('z', 'c2', 'd1')]
        games = [
            _game('a', 'b', 0, 0),
            _game('a', 'z', 30, 20),
            _game('b', 'z', 22, 10),
        ]
        places, _draws = _rank(clubs, games)
        self.assertEqual(_by_key(places)['a'].resolved_by, 'league_rank')

    def test_common_net_points_ignore_the_private_games(self):
        clubs = [
            _club('a'),
            _club('b'),
            _club('x', 'c2', 'd1'),
            _club('y', 'c2', 'd1'),
            _club('z', 'c2', 'd1'),
        ]
        games = [
            _game('a', 'b', 0, 0),
            _game('a', 'x', 20, 0),
            _game('y', 'a', 20, 0),
            _game('b', 'x', 10, 0),
            _game('z', 'b', 20, 10),
        ]
        places, _draws = _rank(clubs, games)
        self.assertEqual(_by_key(places)['a'].resolved_by, 'common_net_points')

    def test_net_points_use_every_game(self):
        clubs = [
            _club('a', 'c1', 'd1'),
            _club('b', 'c1', 'd1'),
            _club('x', 'c1', 'd2'),
            _club('y', 'c1', 'd2'),
            _club('z', 'c1', 'd2'),
        ]
        games = [
            _game('a', 'x', 10, 0),
            _game('b', 'x', 10, 0),
            _game('a', 'y', 20, 12),
            _game('b', 'z', 19, 10),
        ]
        places, _draws = _rank(clubs, games)
        self.assertEqual(_by_key(places)['b'].resolved_by, 'net_points')

    def test_net_touchdowns_are_independent_of_the_score(self):
        clubs = [_club('a'), _club('b')]
        games = [_game('a', 'b', 6, 6, 1, 0)]
        places, _draws = _rank(clubs, games)
        found = _by_key(places)
        self.assertEqual(found['a'].resolved_by, 'net_touchdowns')
        self.assertEqual(found['b'].resolved_by, 'sole_remaining')

    def test_the_draw_uses_sorted_keys_and_the_chosen_index(self):
        seen = []

        def choose(draw_index, conference, division, keys):
            seen.append((draw_index, conference, division, keys))
            return 1

        clubs = [_club('b'), _club('a')]
        places, draws = _rank(clubs, [_game('a', 'b', 0, 0)], choose)
        found = _by_key(places)
        self.assertEqual(seen, [(0, 'c1', 'd1', ('a', 'b'))])
        self.assertEqual(found['b'].resolved_by, DRAW)
        self.assertEqual(found['b'].place, 1)
        self.assertEqual(found['a'].resolved_by, 'sole_remaining')
        self.assertEqual(draws[0].chosen_key, 'b')
        self.assertEqual(draws[0].keys, ('a', 'b'))
        label = division_draw_label('conference-1', 'd1', ('founding-01', 'founding-02'), 2)
        self.assertEqual(
            label,
            'division-place:2002:conference-1:d1:founding-01,founding-02:2',
        )
        self.assertNotIn('timeline', label.split(':'))


class DivisionPlaceStorageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        league = League(
            name='Division Place League',
            slug='division-place-2002-league',
            tier=1,
            active=True,
        )
        league.save()
        release = UniverseRelease(
            slug='division-place-2002',
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
        generate_schedule_release(league.id, 1970)
        release.refresh_from_db()
        profiles = []
        for values in later_profile_values():
            if values['profile_key'] not in PROFILE_KEYS:
                continue
            profile = EraRuleProfile(release=release, **values)
            profile.save()
            profiles.append(profile)
        home = UniverseFranchise.objects.get(
            release=release,
            placeholder_slug='founding-01',
        )
        away = UniverseFranchise.objects.get(
            release=release,
            placeholder_slug='founding-02',
        )
        pairing = OpponentPairing(
            release=release,
            season_year=1970,
            home_franchise=home,
            away_franchise=away,
            kind=OpponentPairing.Kind.DIVISION,
        )
        pairing.save()
        week = OpponentWeekAssignment(release=release, pairing=pairing, week=1)
        week.save()
        standing = HistoricalStanding(
            franchise=home,
            season_year=1969,
            wins=1,
            losses=0,
            ties=0,
        )
        standing.save()
        release.catalog_fingerprint = 'catalog-fixed'
        release.validation_fingerprint = 'validation-fixed'
        release.status = UniverseRelease.Status.PUBLISHED
        release.published_at = timezone.now()
        release.save()
        cls.release = release
        cls.profiles = profiles
        cls.pairing_id = pairing.pk
        cls.week_id = week.pk
        cls.standing_id = standing.pk
        cls.timeline = _timeline(release, 'opens in 2002', profiles, 2002)
        generate_timeline_schedule(timeline_id=cls.timeline.pk, season_year=2002)

    def test_results_place_the_divisions_and_then_mark_the_season(self):
        before = _snapshot(self.release, self.timeline, self.pairing_id, self.week_id, self.standing_id)
        _lock_directives(self.timeline)
        outcome = close_2002_regular_season(
            timeline_id=self.timeline.pk,
            results=_tied_results(self.timeline),
        )
        self.assertEqual(outcome, 'created')
        season = TimelineSeason.objects.get(timeline=self.timeline, season_year=2002)
        self.assertTrue(season.regular_season_simulated)
        self.assertEqual(ScheduleGameResult.objects.filter(timeline=self.timeline).count(), 256)
        self.assertEqual(TimelineDivisionPlace.objects.filter(timeline=self.timeline).count(), 32)
        self.assertEqual(TimelineDivisionDraw.objects.filter(timeline=self.timeline).count(), 24)
        self.assertEqual(
            set(ScheduleGame.objects.filter(timeline=self.timeline).values_list('season_year', flat=True)),
            {2002},
        )
        stored = ScheduleGameResult.objects.filter(timeline=self.timeline).first()
        self.assertEqual(stored.home_score, 6)
        self.assertEqual(stored.away_score, 6)
        self.assertEqual(stored.home_touchdowns, 1)
        self.assertEqual(stored.away_touchdowns, 1)
        self.assertEqual(stored.source_key, 'project-result')
        for conference, division in (
            FranchiseSeasonMembership.objects.filter(
                franchise__release=self.release,
                season_year=2002,
            ).values_list('conference_slug', 'division_slug').distinct()
        ):
            places = list(TimelineDivisionPlace.objects.filter(
                timeline=self.timeline,
                conference_slug=conference,
                division_slug=division,
            ))
            self.assertEqual(sorted(place.division_place for place in places), [1, 2, 3, 4])
            for place in places:
                membership = FranchiseSeasonMembership.objects.get(
                    franchise_id=place.franchise_id,
                    season_year=2002,
                )
                self.assertEqual(place.conference_slug, membership.conference_slug)
                self.assertEqual(place.division_slug, membership.division_slug)
                if place.division_place == 4:
                    self.assertEqual(place.resolved_by, 'sole_remaining')
                    self.assertIsNone(place.draw_id)
                else:
                    self.assertEqual(place.resolved_by, DRAW)
                    self.assertEqual(place.draw.chosen_slug, place.franchise.placeholder_slug)
        draw = TimelineDivisionDraw.objects.filter(timeline=self.timeline).order_by(
            'conference_slug',
            'division_slug',
            'draw_index',
        ).first()
        parts = draw.coordinate.split(':')
        self.assertEqual(parts[0], 'division-place')
        self.assertEqual(parts[1], '2002')
        self.assertEqual(parts[2], draw.conference_slug)
        self.assertEqual(parts[3], draw.division_slug)
        self.assertEqual(parts[4], draw.candidates)
        self.assertEqual(parts[5], str(draw.draw_index))
        self.assertNotIn(str(self.timeline.pk), parts)
        self.assertEqual(draw.digest, _hex_digest(self.release.seed, draw.coordinate))
        self.assertEqual(
            _snapshot(self.release, self.timeline, self.pairing_id, self.week_id, self.standing_id),
            before,
        )

    def test_two_timelines_draw_the_same_order_from_the_release_seed(self):
        other = _timeline(self.release, 'second 2002', self.profiles, 2002)
        generate_timeline_schedule(timeline_id=other.pk, season_year=2002)
        for timeline in (self.timeline, other):
            _lock_directives(timeline)
            close_2002_regular_season(
                timeline_id=timeline.pk,
                results=_tied_results(timeline),
            )
        left = _draw_identity(self.timeline)
        right = _draw_identity(other)
        self.assertEqual(left, right)
        self.assertEqual(_place_identity(self.timeline), _place_identity(other))

    def test_exact_rerun_writes_nothing(self):
        _lock_directives(self.timeline)
        results = _tied_results(self.timeline)
        close_2002_regular_season(timeline_id=self.timeline.pk, results=results)
        outcome, writes = _writes_during(lambda: close_2002_regular_season(
            timeline_id=self.timeline.pk,
            results=results,
        ))
        self.assertEqual(outcome, 'unchanged')
        self.assertEqual(writes, [])

    def test_validation_failures_write_nothing(self):
        _lock_directives(self.timeline)
        results = _tied_results(self.timeline)
        short = results[:-1]
        flipped = Result(
            results[0].away_id,
            results[0].home_id,
            1,
            0,
            0,
            0,
            'project-result',
        )
        bad_score = results[0]._replace(home_score=True)
        blank_source = results[0]._replace(source_key='')
        home_touchdowns = results[0]._replace(home_score=0, home_touchdowns=1)
        away_touchdowns = results[0]._replace(away_score=5, away_touchdowns=1)
        for submitted in (
            short,
            [flipped, *results[1:]],
            [bad_score, *results[1:]],
            [blank_source, *results[1:]],
            [home_touchdowns, *results[1:]],
            [away_touchdowns, *results[1:]],
        ):
            _outcome, writes = _writes_during(lambda submitted=submitted: self.assertRaises(
                DivisionPlaceRefused,
                lambda: close_2002_regular_season(
                    timeline_id=self.timeline.pk,
                    results=submitted,
                ),
            ))
            self.assertEqual(writes, [])
        self.assertEqual(ScheduleGameResult.objects.filter(timeline=self.timeline).count(), 0)
        self.assertFalse(TimelineSeason.objects.get(
            timeline=self.timeline,
            season_year=2002,
        ).regular_season_simulated)

    def test_unlocked_directives_and_a_missing_schedule_are_refused(self):
        with self.assertRaises(DivisionPlaceRefused):
            close_2002_regular_season(
                timeline_id=self.timeline.pk,
                results=_tied_results(self.timeline),
            )
        self.assertEqual(ScheduleGameResult.objects.count(), 0)
        bare = _timeline(self.release, 'no fixtures', self.profiles, 2002)
        _lock_directives(bare)
        with self.assertRaises(DivisionPlaceRefused):
            close_2002_regular_season(timeline_id=bare.pk, results=())
        self.assertEqual(ScheduleGame.objects.filter(timeline=bare).count(), 0)
        self.assertFalse(TimelineSeason.objects.get(
            timeline=bare,
            season_year=2002,
        ).regular_season_simulated)

    def test_a_failed_completion_rolls_back_results_draws_and_places(self):
        _lock_directives(self.timeline)
        results = _tied_results(self.timeline)
        targets = (
            'timelines.services.division_place_2002.ScheduleGameResult.objects.bulk_create',
            'timelines.services.division_place_2002.TimelineDivisionDraw.objects.bulk_create',
            'timelines.services.division_place_2002.TimelineDivisionPlace.objects.bulk_create',
            'timelines.services.division_place_2002.TimelineSeason.save',
        )
        for target in targets:
            with patch(target, side_effect=IntegrityError('forced')):
                with self.assertRaises(DivisionPlaceRefused):
                    close_2002_regular_season(
                        timeline_id=self.timeline.pk,
                        results=results,
                    )
            self.assertEqual(
                ScheduleGameResult.objects.filter(timeline=self.timeline).count(),
                0,
                target,
            )
            self.assertEqual(
                TimelineDivisionDraw.objects.filter(timeline=self.timeline).count(),
                0,
                target,
            )
            self.assertEqual(
                TimelineDivisionPlace.objects.filter(timeline=self.timeline).count(),
                0,
                target,
            )
            self.assertFalse(TimelineSeason.objects.get(
                timeline=self.timeline,
                season_year=2002,
            ).regular_season_simulated, target)

    def test_partial_and_changed_rows_stay_in_place(self):
        game = ScheduleGame.objects.filter(timeline=self.timeline).order_by('pk').first()
        with writing_division_rows():
            row = ScheduleGameResult(
                timeline=self.timeline,
                game=game,
                season_year=2002,
                home_score=3,
                away_score=0,
                home_touchdowns=0,
                away_touchdowns=0,
                source_key='partial',
            )
            row.full_clean()
            ScheduleGameResult.objects.bulk_create([row])
        _lock_directives(self.timeline)
        with self.assertRaises(DivisionPlaceRefused):
            close_2002_regular_season(
                timeline_id=self.timeline.pk,
                results=_tied_results(self.timeline),
            )
        self.assertEqual(ScheduleGameResult.objects.get(pk=row.pk).home_score, 3)
        self.assertEqual(TimelineDivisionPlace.objects.filter(timeline=self.timeline).count(), 0)
        self.assertFalse(TimelineSeason.objects.get(
            timeline=self.timeline,
            season_year=2002,
        ).regular_season_simulated)
        ScheduleGameResult.objects.filter(pk=row.pk).delete()

        _lock_directives(self.timeline)
        close_2002_regular_season(
            timeline_id=self.timeline.pk,
            results=_tied_results(self.timeline),
        )
        stored = ScheduleGameResult.objects.filter(timeline=self.timeline).order_by('pk').first()
        ScheduleGameResult.objects.filter(pk=stored.pk).update(home_score=9)
        draw = TimelineDivisionDraw.objects.filter(timeline=self.timeline).order_by('pk').first()
        TimelineDivisionDraw.objects.filter(pk=draw.pk).update(outcome=f'{draw.outcome}-tampered')
        with self.assertRaises(DivisionPlaceRefused):
            close_2002_regular_season(
                timeline_id=self.timeline.pk,
                results=_tied_results(self.timeline),
            )
        self.assertEqual(ScheduleGameResult.objects.get(pk=stored.pk).home_score, 9)
        self.assertTrue(TimelineDivisionDraw.objects.filter(
            pk=draw.pk,
            outcome=f'{draw.outcome}-tampered',
        ).exists())
        self.assertEqual(TimelineDivisionPlace.objects.filter(timeline=self.timeline).count(), 32)
        self.assertTrue(TimelineSeason.objects.get(
            timeline=self.timeline,
            season_year=2002,
        ).regular_season_simulated)

    def test_ordinary_save_cannot_freeze_one_result_or_place(self):
        game = ScheduleGame.objects.filter(timeline=self.timeline).first()
        with self.assertRaises(ValidationError):
            ScheduleGameResult(
                timeline=self.timeline,
                game=game,
                season_year=2002,
                home_score=1,
                away_score=0,
                home_touchdowns=0,
                away_touchdowns=0,
                source_key='lone',
            ).save()
        franchise = FranchiseSeasonMembership.objects.filter(
            franchise__release=self.release,
            season_year=2002,
        ).first()
        with self.assertRaises(ValidationError):
            TimelineDivisionPlace(
                timeline=self.timeline,
                franchise_id=franchise.franchise_id,
                season_year=2002,
                division_place=1,
                conference_slug=franchise.conference_slug,
                division_slug=franchise.division_slug,
                resolved_by='winning_percentage',
            ).save()
        self.assertEqual(ScheduleGameResult.objects.count(), 0)
        self.assertEqual(TimelineDivisionPlace.objects.count(), 0)

    def test_touchdown_totals_cannot_exceed_six_points_each(self):
        game = ScheduleGame.objects.filter(timeline=self.timeline).first()
        with writing_division_rows():
            accepted = ScheduleGameResult(
                timeline=self.timeline,
                game=game,
                season_year=2002,
                home_score=6,
                away_score=13,
                home_touchdowns=1,
                away_touchdowns=1,
                source_key='project-result',
            )
            accepted.full_clean()
            ScheduleGameResult.objects.bulk_create([accepted])
        stored = ScheduleGameResult.objects.get(timeline=self.timeline, game=game)
        self.assertEqual(stored.home_touchdowns, 1)
        self.assertEqual(stored.home_score, 6)
        self.assertEqual(stored.away_score, 13)
        ScheduleGameResult.objects.filter(pk=stored.pk).delete()
        for home_score, away_score, home_td, away_td in (
            (0, 0, 1, 0),
            (0, 0, 0, 1),
            (5, 6, 1, 1),
            (6, 5, 1, 1),
        ):
            with writing_division_rows():
                row = ScheduleGameResult(
                    timeline=self.timeline,
                    game=game,
                    season_year=2002,
                    home_score=home_score,
                    away_score=away_score,
                    home_touchdowns=home_td,
                    away_touchdowns=away_td,
                    source_key='project-result',
                )
                with self.assertRaises(ValidationError):
                    row.full_clean()
        self.assertEqual(ScheduleGameResult.objects.filter(timeline=self.timeline).count(), 0)

    def test_the_2002_flag_requires_the_stored_close(self):
        season = TimelineSeason.objects.get(timeline=self.timeline, season_year=2002)
        _lock_directives(self.timeline)
        season.refresh_from_db()

        def refuse_flag():
            season.regular_season_simulated = True
            with self.assertRaises(ValidationError):
                season.save()
            season.refresh_from_db()
            self.assertFalse(season.regular_season_simulated)

        refuse_flag()

        game = ScheduleGame.objects.filter(timeline=self.timeline).order_by('pk').first()
        with writing_division_rows():
            partial = ScheduleGameResult(
                timeline=self.timeline,
                game=game,
                season_year=2002,
                home_score=6,
                away_score=0,
                home_touchdowns=1,
                away_touchdowns=0,
                source_key='partial',
            )
            partial.full_clean()
            ScheduleGameResult.objects.bulk_create([partial])
        refuse_flag()
        self.assertEqual(ScheduleGameResult.objects.get(pk=partial.pk).home_score, 6)

        ScheduleGameResult.objects.filter(pk=partial.pk).delete()
        with writing_division_rows():
            rows = [
                ScheduleGameResult(
                    timeline=self.timeline,
                    game=fixture,
                    season_year=2002,
                    home_score=6,
                    away_score=6,
                    home_touchdowns=1,
                    away_touchdowns=1,
                    source_key='project-result',
                )
                for fixture in ScheduleGame.objects.filter(timeline=self.timeline)
            ]
            for row in rows:
                row.full_clean()
            ScheduleGameResult.objects.bulk_create(rows)
        membership = FranchiseSeasonMembership.objects.filter(
            franchise__release=self.release,
            season_year=2002,
        ).first()
        with writing_division_rows():
            one_place = TimelineDivisionPlace(
                timeline=self.timeline,
                franchise_id=membership.franchise_id,
                season_year=2002,
                division_place=1,
                conference_slug=membership.conference_slug,
                division_slug=membership.division_slug,
                resolved_by='winning_percentage',
            )
            one_place.full_clean()
            TimelineDivisionPlace.objects.bulk_create([one_place])
        refuse_flag()
        self.assertEqual(TimelineDivisionPlace.objects.filter(timeline=self.timeline).count(), 1)
        self.assertEqual(ScheduleGameResult.objects.filter(timeline=self.timeline).count(), 256)

        TimelineDivisionPlace.objects.filter(timeline=self.timeline).delete()
        memberships = list(FranchiseSeasonMembership.objects.filter(
            franchise__release=self.release,
            season_year=2002,
        ))
        grouped = {}
        for row in memberships:
            grouped.setdefault((row.conference_slug, row.division_slug), []).append(row)
        moved_key = sorted(grouped)[0]
        invalid = []
        for key, members in grouped.items():
            division = 'dx' if key == moved_key else key[1]
            for index, row in enumerate(sorted(members, key=lambda item: item.franchise_id), start=1):
                invalid.append(TimelineDivisionPlace(
                    timeline=self.timeline,
                    franchise_id=row.franchise_id,
                    season_year=2002,
                    division_place=index,
                    conference_slug=key[0],
                    division_slug=division,
                    resolved_by='winning_percentage',
                ))
        TimelineDivisionPlace.objects.bulk_create(invalid)
        refuse_flag()
        self.assertEqual(TimelineDivisionPlace.objects.filter(timeline=self.timeline).count(), 32)

        TimelineDivisionPlace.objects.filter(timeline=self.timeline).delete()
        ScheduleGameResult.objects.filter(timeline=self.timeline).delete()
        close_2002_regular_season(
            timeline_id=self.timeline.pk,
            results=_tied_results(self.timeline),
        )
        season.refresh_from_db()
        self.assertTrue(season.regular_season_simulated)
        outcome, writes = _writes_during(lambda: close_2002_regular_season(
            timeline_id=self.timeline.pk,
            results=_tied_results(self.timeline),
        ))
        self.assertEqual(outcome, 'unchanged')
        self.assertEqual(writes, [])

    def test_an_already_simulated_incomplete_season_is_refused(self):
        _lock_directives(self.timeline)
        game = ScheduleGame.objects.filter(timeline=self.timeline).order_by('pk').first()
        with writing_division_rows():
            row = ScheduleGameResult(
                timeline=self.timeline,
                game=game,
                season_year=2002,
                home_score=6,
                away_score=0,
                home_touchdowns=1,
                away_touchdowns=0,
                source_key='partial',
            )
            row.full_clean()
            ScheduleGameResult.objects.bulk_create([row])
        TimelineSeason.objects.filter(timeline=self.timeline, season_year=2002).update(
            regular_season_simulated=True,
        )
        with self.assertRaises(DivisionPlaceRefused):
            close_2002_regular_season(
                timeline_id=self.timeline.pk,
                results=_tied_results(self.timeline),
            )
        self.assertEqual(ScheduleGameResult.objects.get(pk=row.pk).source_key, 'partial')
        self.assertEqual(TimelineDivisionPlace.objects.filter(timeline=self.timeline).count(), 0)
        self.assertTrue(TimelineSeason.objects.get(
            timeline=self.timeline,
            season_year=2002,
        ).regular_season_simulated)

    def test_a_2001_season_can_still_be_marked_simulated(self):
        played = _timeline(self.release, '2001 still closes', self.profiles, 2001)
        season = TimelineSeason.objects.get(timeline=played, season_year=2001)
        self.assertFalse(season.regular_season_simulated)
        season.setup_complete = True
        season.directives_locked = True
        season.regular_season_simulated = True
        season.save()
        season.refresh_from_db()
        self.assertTrue(season.regular_season_simulated)
        self.assertEqual(ScheduleGameResult.objects.filter(timeline=played).count(), 0)
        self.assertEqual(TimelineDivisionPlace.objects.filter(timeline=played).count(), 0)
        store_prior_finishes(timeline_id=played.pk, finishes=_default_finishes(self.release))
        self.assertEqual(TimelinePriorFinish.objects.filter(timeline=played).count(), 31)
        derive_scheduling_ranks(timeline_id=played.pk)
        self.assertEqual(TimelineSchedulingRank.objects.filter(timeline=played).count(), 32)

    def test_a_played_2001_timeline_keeps_its_transition_rows(self):
        played = _timeline(self.release, 'played 2001', self.profiles, 2001, simulated_2001=True)
        store_prior_finishes(timeline_id=played.pk, finishes=_default_finishes(self.release))
        derive_scheduling_ranks(timeline_id=played.pk)
        generate_timeline_schedule(timeline_id=played.pk, season_year=2002)
        before_finishes = list(TimelinePriorFinish.objects.filter(timeline=played).values_list(
            'franchise_id',
            'wins',
            'losses',
            'ties',
            'division_place',
        ))
        before_ranks = list(TimelineSchedulingRank.objects.filter(timeline=played).values_list(
            'franchise_id',
            'scheduling_rank',
        ))
        before_draws = TimelineSchedulingDraw.objects.filter(timeline=played).count()
        season_2001 = TimelineSeason.objects.get(timeline=played, season_year=2001)
        self.assertTrue(season_2001.regular_season_simulated)
        _lock_directives(played)
        close_2002_regular_season(timeline_id=played.pk, results=_tied_results(played))
        self.assertEqual(
            list(TimelinePriorFinish.objects.filter(timeline=played).values_list(
                'franchise_id',
                'wins',
                'losses',
                'ties',
                'division_place',
            )),
            before_finishes,
        )
        self.assertEqual(
            list(TimelineSchedulingRank.objects.filter(timeline=played).values_list(
                'franchise_id',
                'scheduling_rank',
            )),
            before_ranks,
        )
        self.assertEqual(TimelineSchedulingDraw.objects.filter(timeline=played).count(), before_draws)
        self.assertTrue(TimelineSeason.objects.get(
            timeline=played,
            season_year=2001,
        ).regular_season_simulated)
        self.assertEqual(ScheduleGame.objects.filter(timeline=played, season_year=2003).count(), 0)
        self.assertEqual(TimelineScheduleSeed.objects.filter(timeline=played).count(), 0)

    def test_an_inactive_timeline_is_refused(self):
        _lock_directives(self.timeline)
        timeline = Timeline.objects.get(pk=self.timeline.pk)
        timeline.status = Timeline.Status.ARCHIVED
        timeline.completed_at = timezone.now()
        timeline.save()
        with self.assertRaises(DivisionPlaceRefused):
            close_2002_regular_season(
                timeline_id=timeline.pk,
                results=_tied_results(timeline),
            )
        self.assertEqual(ScheduleGameResult.objects.filter(timeline=timeline).count(), 0)


def _hex_digest(seed, message):
    return hmac.new(
        str(seed).encode('utf-8'),
        str(message).encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()


def _tied_results(timeline, home_score=6, away_score=6, home_td=1, away_td=1):
    return [
        Result(
            game.home_id,
            game.away_id,
            home_score,
            away_score,
            home_td,
            away_td,
            'project-result',
        )
        for game in ScheduleGame.objects.filter(timeline=timeline, season_year=2002)
    ]


def _lock_directives(timeline):
    season = TimelineSeason.objects.get(timeline=timeline, season_year=2002)
    if season.directives_locked:
        return
    season.setup_complete = True
    season.directives_locked = True
    season.save()


def _timeline(release, name, profiles, start_year, simulated_2001=False):
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
            item for item in profiles
            if item.first_year <= year <= item.last_year
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


def _snapshot(release, timeline, pairing_id, week_id, standing_id):
    release.refresh_from_db()
    pairing = OpponentPairing.objects.get(pk=pairing_id)
    week = OpponentWeekAssignment.objects.get(pk=week_id)
    standing = HistoricalStanding.objects.get(pk=standing_id)
    games = tuple(ScheduleGame.objects.filter(timeline=timeline).order_by('pk').values_list(
        'home_id',
        'away_id',
        'category',
        'kind',
        'season_year',
    ))
    memberships = tuple(FranchiseSeasonMembership.objects.filter(
        franchise__release=release,
    ).order_by('franchise_id', 'season_year').values_list(
        'franchise_id',
        'season_year',
        'conference_slug',
        'division_slug',
        'slot_order',
        'owner_selectable',
    ))
    seeds = tuple(TimelineScheduleSeed.objects.filter(timeline=timeline).order_by('pk').values_list(
        'franchise_id',
        'division_place',
        'conference_slug',
        'division_slug',
    ))
    return (
        release.catalog_fingerprint,
        release.validation_fingerprint,
        pairing.home_franchise_id,
        pairing.away_franchise_id,
        pairing.kind,
        week.week,
        week.pairing_id,
        standing.wins,
        standing.losses,
        standing.ties,
        standing.draft_order,
        OpponentPairing.objects.filter(release=release).count(),
        OpponentWeekAssignment.objects.filter(release=release).count(),
        HistoricalStanding.objects.filter(franchise__release=release).count(),
        games,
        memberships,
        seeds,
        UniverseFranchise.objects.filter(release=release).count(),
    )


def _draw_identity(timeline):
    return tuple(TimelineDivisionDraw.objects.filter(timeline=timeline).order_by(
        'conference_slug',
        'division_slug',
        'draw_index',
    ).values_list(
        'conference_slug',
        'division_slug',
        'draw_index',
        'candidates',
        'coordinate',
        'digest',
        'chosen_slug',
        'outcome',
    ))


def _place_identity(timeline):
    return tuple(TimelineDivisionPlace.objects.filter(timeline=timeline).order_by(
        'conference_slug',
        'division_slug',
        'division_place',
    ).values_list(
        'franchise__placeholder_slug',
        'division_place',
        'resolved_by',
        'conference_slug',
        'division_slug',
    ))
