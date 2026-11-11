from django.test import SimpleTestCase

from timelines.services.division_place_2002 import (
    HEAD_TO_HEAD,
    RULE_WINDOWS,
    SUPPORTED_SEASONS,
    DivisionPlaceRefused,
    close_regular_season,
    competition_rank,
    division_draw_label,
    verify_played_places,
)
from timelines.tests.test_division_place_2002 import _by_key, _club, _game, _rank


CONTINUATION = [
    (2, HEAD_TO_HEAD),
    (1, HEAD_TO_HEAD),
    (4, 'sole_remaining'),
    (3, HEAD_TO_HEAD),
]
RESTART = [
    (3, HEAD_TO_HEAD),
    (2, HEAD_TO_HEAD),
    (1, HEAD_TO_HEAD),
    (4, 'sole_remaining'),
]


def _four_to_three():
    clubs = [_club(name) for name in 'abcd']
    clubs.extend([_club('p', 'c2', 'd1'), _club('q', 'c2', 'd2')])
    games = [
        _game('a', 'b', 1, 1),
        _game('a', 'b', 0, 1),
        _game('a', 'c', 1, 0),
        _game('a', 'c', 0, 1),
        _game('a', 'd', 1, 0),
        _game('a', 'd', 1, 0),
        _game('b', 'c', 0, 1),
        _game('b', 'c', 0, 1),
        _game('b', 'd', 1, 0),
        _game('b', 'd', 1, 0),
        _game('c', 'd', 1, 1),
        _game('c', 'd', 0, 1),
        _game('a', 'p', 0, 1),
        _game('a', 'q', 0, 1),
        _game('b', 'p', 0, 1),
        _game('b', 'q', 0, 1),
        _game('c', 'p', 0, 1),
        _game('c', 'q', 0, 1),
        _game('d', 'p', 1, 0),
        _game('d', 'q', 1, 0),
    ]
    return clubs, games


class CloseSpanTests(SimpleTestCase):
    def test_supported_span_follows_the_sourced_windows(self):
        closing = []
        for window in RULE_WINDOWS:
            self.assertIn(window['procedure'], ('two-club-restart', 'three-club-restart'))
            self.assertTrue(window['closes'])
            self.assertTrue(window['source'])
            closing.extend(window['seasons'])
        self.assertEqual(tuple(closing), SUPPORTED_SEASONS)
        self.assertEqual(SUPPORTED_SEASONS, tuple(range(2002, 2030)))
        sources = {window['seasons'][0]: window['source'] for window in RULE_WINDOWS}
        self.assertIn('2002 text', sources[2002])
        self.assertIn('December 13, 2003', sources[2003])
        self.assertIn('2004 close keeps', sources[2004])
        self.assertIn('p. 37', sources[2005])
        self.assertIn('p. 33', sources[2005])
        self.assertIn('approved project rule', sources[2005])
        self.assertIn('pp. 11–12', sources[2011])
        self.assertIn('p. 26', sources[2011])
        self.assertIn('approved project rule', sources[2011])
        self.assertIn('p. 26', sources[2021])
        self.assertIn('p. 32', sources[2021])
        self.assertIn('p. 36', sources[2021])
        self.assertIn('approved project rule', sources[2021])
        self.assertNotIn(2021, next(
            window['seasons'] for window in RULE_WINDOWS if 2011 in window['seasons']
        ))
        self.assertEqual(
            next(window['procedure'] for window in RULE_WINDOWS if 2010 in window['seasons']),
            'two-club-restart',
        )
        self.assertEqual(
            next(window['procedure'] for window in RULE_WINDOWS if 2011 in window['seasons']),
            'three-club-restart',
        )
        self.assertEqual(
            next(window['procedure'] for window in RULE_WINDOWS if 2021 in window['seasons']),
            'three-club-restart',
        )
        self.assertEqual(
            next(window['procedure'] for window in RULE_WINDOWS if 2029 in window['seasons']),
            'three-club-restart',
        )

    def test_competition_rank_gives_tied_clubs_the_same_rank(self):
        ranks = competition_rank(
            ['a', 'b', 'c'],
            key=lambda name: {'a': 10, 'b': 10, 'c': 9}[name],
            higher_better=True,
        )
        self.assertEqual(ranks, [1, 1, 3])

    def test_four_clubs_reduced_to_three_follow_the_season_window(self):
        clubs, games = _four_to_three()
        for year, expected in (
            (2005, CONTINUATION),
            (2010, CONTINUATION),
            (2011, RESTART),
            (2020, RESTART),
            (2021, RESTART),
            (2029, RESTART),
        ):
            places, draws = _rank(clubs, games, season_year=year)
            found = _by_key(places)
            self.assertEqual(len(draws), 0)
            self.assertEqual(
                [(found[name].place, found[name].resolved_by) for name in 'abcd'],
                expected,
            )
            self.assertEqual(sorted(found[name].place for name in 'abcd'), [1, 2, 3, 4])

    def test_draw_labels_name_the_season_through_2029(self):
        arguments = ('conference-1', 'd1', ('founding-01', 'founding-02'), 1)
        for year in (2002, 2005, 2011, 2020, 2021, 2029):
            label = division_draw_label(*arguments, year)
            self.assertIn(f':{year}:', label)
            self.assertNotIn('timeline', label)
        with self.assertRaisesMessage(DivisionPlaceRefused, 'outside 2002–2029'):
            division_draw_label(*arguments, 2030)
        with self.assertRaisesMessage(DivisionPlaceRefused, 'outside 2002–2029'):
            close_regular_season(timeline_id=1, results=(), season_year=2030)
        with self.assertRaisesMessage(DivisionPlaceRefused, 'outside 2002–2029'):
            verify_played_places(object(), 2030)
