from collections import Counter

from django.test import SimpleTestCase

from timelines.services.schedule_2002 import (
    AFC_SIDE,
    NFC_SIDE,
    POSITIONS,
    Seat,
    planned_games,
)


CONFERENCE_PAIRS = (
    {frozenset(('d1', 'd4')), frozenset(('d2', 'd3'))},
    {frozenset(('d1', 'd3')), frozenset(('d2', 'd4'))},
    {frozenset(('d1', 'd2')), frozenset(('d3', 'd4'))},
)
INTER_ROWS = (
    ('d2', 'd3', 'd1', 'd4'),
    ('d1', 'd4', 'd3', 'd2'),
    ('d4', 'd1', 'd2', 'd3'),
    ('d3', 'd2', 'd4', 'd1'),
)
# year, k3, k4, conference flipped, interconference flipped, same-place homes
SPAN = (
    (2002, 0, 0, False, False, {'d1': 'd2', 'd2': 'd4', 'd3': 'd1', 'd4': 'd3'}),
    (2003, 1, 1, False, False, {'d1': 'd2', 'd2': 'd3', 'd3': 'd4', 'd4': 'd1'}),
    (2004, 2, 2, False, False, {'d1': 'd3', 'd2': 'd4', 'd3': 'd2', 'd4': 'd1'}),
    (2005, 0, 3, True, False, {'d1': 'd3', 'd2': 'd1', 'd3': 'd4', 'd4': 'd2'}),
    (2006, 1, 0, True, True, {'d1': 'd4', 'd2': 'd1', 'd3': 'd2', 'd4': 'd3'}),
    (2008, 0, 2, False, True, {'d1': 'd2', 'd2': 'd4', 'd3': 'd1', 'd4': 'd3'}),
    (2012, 1, 2, True, False, {'d1': 'd4', 'd2': 'd1', 'd3': 'd2', 'd4': 'd3'}),
    (2016, 2, 2, False, True, {'d1': 'd3', 'd2': 'd4', 'd3': 'd2', 'd4': 'd1'}),
    (2020, 0, 2, False, False, {'d1': 'd2', 'd2': 'd4', 'd3': 'd1', 'd4': 'd3'}),
)


class ScheduleSpanFormulaTests(SimpleTestCase):
    def test_rotation_and_sites_through_2020(self):
        seats = _synthetic_seats()
        places = {seat.franchise_id: seat.slot for seat in seats}
        by_id = {seat.franchise_id: seat for seat in seats}
        by_year = {}
        for year, k3, k4, conference_flipped, inter_flipped, homes in SPAN:
            self.assertEqual((year - 2002) % 3, k3)
            self.assertEqual((year - 2002) % 4, k4)
            games = planned_games(year, seats, places)
            by_year[year] = games
            directed = {(game.home_id, game.away_id) for game in games}
            self.assertEqual(len(games), 256)
            self.assertEqual(len(directed), 256)
            self.assertEqual(Counter(game.category for game in games), {
                'division': 96,
                'conference': 64,
                'interconference': 64,
                'same_place': 32,
            })
            conference_pairs = set()
            inter_pairs = set()
            same_pairs = set()
            for game in games:
                home = by_id[game.home_id]
                away = by_id[game.away_id]
                if game.category == 'conference':
                    conference_pairs.add(frozenset((home.division, away.division)))
                elif game.category == 'interconference':
                    inter_pairs.add((home.conference, home.division, away.division))
                elif game.category == 'same_place':
                    self.assertEqual(places[home.franchise_id], places[away.franchise_id])
                    same_pairs.add((home.conference, home.division, away.division))
            self.assertEqual(conference_pairs, CONFERENCE_PAIRS[k3])
            self.assertEqual(inter_pairs, {
                (AFC_SIDE, POSITIONS[index], away_division)
                for index, away_division in enumerate(INTER_ROWS[k4])
            } | {
                (NFC_SIDE, away_division, POSITIONS[index])
                for index, away_division in enumerate(INTER_ROWS[k4])
            })
            self.assertEqual(same_pairs, {
                (conference, home_division, away_division)
                for conference in (AFC_SIDE, NFC_SIDE)
                for home_division, away_division in homes.items()
            })
            for seat in seats:
                played = [
                    game for game in games
                    if seat.franchise_id in (game.home_id, game.away_id)
                ]
                homes_for_seat = [
                    game for game in played if game.home_id == seat.franchise_id
                ]
                self.assertEqual(len(played), 16)
                self.assertEqual(len(homes_for_seat), 8)
                self.assertEqual(
                    len([
                        game for game in homes_for_seat
                        if game.category == 'same_place'
                    ]),
                    1,
                )
                for game in played:
                    if game.category not in ('conference', 'interconference'):
                        continue
                    other_id = (
                        game.away_id if game.home_id == seat.franchise_id
                        else game.home_id
                    )
                    self.assertEqual(
                        (seat.franchise_id, other_id) in directed,
                        _expected_home(
                            seat,
                            by_id[other_id],
                            conference_flipped,
                            inter_flipped,
                        ),
                    )

        self.assertEqual(
            _reverse(_directions(by_year[2002], 'conference')),
            _directions(by_year[2005], 'conference'),
        )
        self.assertEqual(
            _reverse(_directions(by_year[2002], 'same_place')),
            _directions(by_year[2005], 'same_place'),
        )
        self.assertEqual(
            _directions(by_year[2008], 'conference'),
            _directions(by_year[2002], 'conference'),
        )
        self.assertEqual(
            _directions(by_year[2020], 'conference'),
            _directions(by_year[2002], 'conference'),
        )
        self.assertEqual(
            _directions(by_year[2020], 'interconference'),
            _directions(by_year[2004], 'interconference'),
        )
        self.assertEqual(
            _reverse(_directions(by_year[2004], 'interconference')),
            _directions(by_year[2008], 'interconference'),
        )


def _directions(games, category):
    return {
        (game.home_id, game.away_id)
        for game in games
        if game.category == category
    }


def _reverse(pairs):
    return {(away, home) for home, away in pairs}


def _expected_home(seat, opponent, conference_flipped, inter_flipped):
    first = _first_meeting_home(seat, opponent)
    flipped = (
        conference_flipped if seat.conference == opponent.conference
        else inter_flipped
    )
    if flipped:
        return not first
    return first


def _first_meeting_home(seat, opponent):
    if seat.conference == opponent.conference:
        first = min((seat.division, opponent.division), key=POSITIONS.index)
        seat_is_first = seat.division == first
    else:
        seat_is_first = seat.conference == AFC_SIDE
    same_pair = (seat.slot <= 2) == (opponent.slot <= 2)
    if seat_is_first:
        return same_pair
    return not same_pair


def _synthetic_seats():
    seats = []
    franchise_id = 1
    for conference in (AFC_SIDE, NFC_SIDE):
        for division in POSITIONS:
            for slot in range(1, 5):
                seats.append(Seat(
                    franchise_id,
                    f'{conference}-{division}-{slot}',
                    conference,
                    division,
                    slot,
                ))
                franchise_id += 1
    return seats
