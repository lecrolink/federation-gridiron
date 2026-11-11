from django.test import SimpleTestCase

from timelines.services.division_place_2002 import (
    HEAD_TO_HEAD,
    RULE_WINDOWS,
    SUPPORTED_SEASONS,
    DivisionPlaceRefused,
    close_regular_season,
    division_draw_label,
    verify_played_places,
)
from timelines.tests.test_division_place_2002 import _by_key, _club, _game, _rank


class CloseSpanTests(SimpleTestCase):
    def test_supported_span_follows_the_sourced_windows(self):
        closing = []
        refused = []
        for window in RULE_WINDOWS:
            self.assertIn(window['procedure'], ('two-club-restart', 'unestablished'))
            self.assertTrue(window['source'])
            if window['closes']:
                self.assertEqual(window['procedure'], 'two-club-restart')
                closing.extend(window['seasons'])
            else:
                self.assertEqual(window['procedure'], 'unestablished')
                refused.extend(window['seasons'])
        self.assertEqual(tuple(closing), SUPPORTED_SEASONS)
        self.assertEqual(SUPPORTED_SEASONS, (2002, 2003, 2004))
        self.assertEqual(tuple(refused), tuple(range(2005, 2021)))
        sources = {window['seasons'][0]: window['source'] for window in RULE_WINDOWS}
        self.assertIn('2002 text', sources[2002])
        self.assertIn('December 13, 2003', sources[2003])
        self.assertIn('2004 close keeps', sources[2004])
        self.assertIn('three-club restart', sources[2005])
        self.assertIn('publication year', sources[2005])

    def test_supported_procedure_does_not_restart_three_clubs(self):
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
        places, draws = _rank(clubs, games)
        found = _by_key(places)
        self.assertEqual(len(draws), 0)
        self.assertEqual(
            [(found[name].place, found[name].resolved_by) for name in 'abcd'],
            [
                (2, HEAD_TO_HEAD),
                (1, HEAD_TO_HEAD),
                (4, 'sole_remaining'),
                (3, HEAD_TO_HEAD),
            ],
        )
        restarted, _draws = _rank(
            [_club(name) for name in 'abc'],
            [
                _game('a', 'b', 1, 1),
                _game('a', 'b', 0, 1),
                _game('a', 'c', 1, 0),
                _game('a', 'c', 0, 1),
                _game('b', 'c', 0, 1),
                _game('b', 'c', 0, 1),
            ],
        )
        self.assertEqual(_by_key(restarted)['c'].place, 1)
        self.assertNotEqual(found['c'].place, 1)

    def test_unsupported_seasons_are_refused_before_a_lock(self):
        arguments = ('conference-1', 'd1', ('founding-01', 'founding-02'), 1)
        for year in (2002, 2003, 2004):
            label = division_draw_label(*arguments, year)
            self.assertIn(f':{year}:', label)
            self.assertNotIn('timeline', label)
        for year in (2005, 2012, 2020):
            with self.assertRaisesMessage(DivisionPlaceRefused, 'outside 2002–2004'):
                division_draw_label(*arguments, year)
            with self.assertRaisesMessage(DivisionPlaceRefused, 'outside 2002–2004'):
                close_regular_season(
                    timeline_id=1,
                    results=(),
                    season_year=year,
                )
            with self.assertRaisesMessage(DivisionPlaceRefused, 'outside 2002–2004'):
                verify_played_places(object(), year)
