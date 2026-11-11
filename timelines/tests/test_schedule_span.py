from collections import Counter

from django.test import SimpleTestCase

from timelines.services.schedule_2002 import (
    AFC_SIDE,
    INTER_ROW,
    INTRA_PAIRS,
    NFC_SIDE,
    POSITIONS,
    Seat,
    TimelineScheduleRefused,
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


# NFC division for AFC East, North, South, West.
# 2021: NFL.com, "NFL season to feature 17 regular-season games per team".
# 2022: the league rotation reprinted with that season's opponents.
PUBLISHED_INTER = {
    2021: (2, 1, 3, 0),
    2022: (1, 2, 0, 3),
}
# 2021 AFC hosts NFC East, West, South, North. 2022 NFC hosts the next row.
PUBLISHED_SEVENTEENTH = {
    2021: (0, 3, 2, 1),
    2022: (3, 0, 1, 2),
}
# NFC West 17th-game AFC division, and whether that NFC club is at home.
# 2021–2024 is the Rams cycle. 2025–2029 is the Cardinals list in the 2024
# Official NFL Record & Fact Book, future opponents.
NFC_WEST_SEVENTEENTH = {
    2021: ('d2', False),
    2022: ('d1', True),
    2023: ('d3', False),
    2024: ('d4', True),
    2025: ('d2', False),
    2026: ('d1', True),
    2027: ('d3', False),
    2028: ('d4', True),
    2029: ('d2', False),
}
# Cardinals rotating divisions in that same 2024 book, 2025–2029.
NFC_WEST_ROTATION = {
    2025: ('d3', 'd3'),
    2026: ('d1', 'd4'),
    2027: ('d2', 'd2'),
    2028: ('d3', 'd1'),
    2029: ('d1', 'd3'),
}


class SeventeenGameFormulaTests(SimpleTestCase):
    def test_every_season_from_2021_through_2029(self):
        seats = _synthetic_seats()
        places = {seat.franchise_id: seat.slot for seat in seats}
        by_id = {seat.franchise_id: seat for seat in seats}
        self.assertEqual(len(planned_games(2020, seats, places)), 256)
        for year in range(2021, 2030):
            games = planned_games(year, seats, places)
            self.assertEqual(len(games), 272)
            self.assertEqual(len({(game.home_id, game.away_id) for game in games}), 272)
            self.assertEqual(Counter(game.category for game in games), {
                'division': 96,
                'conference': 64,
                'interconference': 64,
                'same_place': 48,
            })
            self._assert_rotation(year, games, by_id, places)
            self._assert_counts(year, games, seats)
        with self.assertRaisesMessage(TimelineScheduleRefused, 'outside 2002–2029'):
            planned_games(2030, seats, places)

    def _assert_counts(self, year, games, seats):
        afc_hosts = (year - 2021) % 2 == 0
        for seat in seats:
            played = [
                game for game in games
                if seat.franchise_id in (game.home_id, game.away_id)
            ]
            homes = [game for game in played if game.home_id == seat.franchise_id]
            self.assertEqual(len(played), 17)
            expected_homes = 9 if (seat.conference == AFC_SIDE) == afc_hosts else 8
            self.assertEqual(len(homes), expected_homes)
            intra_homes = [
                game for game in homes
                if game.category == 'same_place'
                and _conference(game.away_id, seats) == seat.conference
            ]
            self.assertEqual(len(intra_homes), 1)

    def _assert_rotation(self, year, games, by_id, places):
        k3 = (year - 2002) % 3
        k4 = (year - 2002) % 4
        conference_pairs = {
            frozenset((
                by_id[game.home_id].division,
                by_id[game.away_id].division,
            ))
            for game in games
            if game.category == 'conference'
        }
        self.assertEqual(conference_pairs, {
            frozenset((POSITIONS[left], POSITIONS[right]))
            for left, right in INTRA_PAIRS[k3]
        })
        inter_row = INTER_ROW[k4]
        if year in PUBLISHED_INTER:
            self.assertEqual(inter_row, PUBLISHED_INTER[year])
        seventeenth_row = INTER_ROW[(year - 2004) % 4]
        if year in PUBLISHED_SEVENTEENTH:
            self.assertEqual(seventeenth_row, PUBLISHED_SEVENTEENTH[year])
        self.assertNotEqual(inter_row, seventeenth_row)
        for game in games:
            if game.category != 'interconference':
                continue
            home = by_id[game.home_id]
            away = by_id[game.away_id]
            afc = home if home.conference == AFC_SIDE else away
            nfc = away if afc is home else home
            self.assertEqual(
                nfc.division,
                POSITIONS[inter_row[POSITIONS.index(afc.division)]],
            )
        directed = {(game.home_id, game.away_id) for game in games}
        conference_flipped = ((year - (2002 + k3)) // 3) % 2 == 1
        inter_flipped = ((year - (2002 + k4)) // 4) % 2 == 1
        for seat in by_id.values():
            for game in games:
                if game.category not in ('conference', 'interconference'):
                    continue
                if seat.franchise_id not in (game.home_id, game.away_id):
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
        cross = []
        for game in games:
            if game.category != 'same_place':
                continue
            home = by_id[game.home_id]
            away = by_id[game.away_id]
            if home.conference == away.conference:
                continue
            cross.append(game)
            self.assertEqual(places[home.franchise_id], places[away.franchise_id])
            afc = home if home.conference == AFC_SIDE else away
            nfc = away if afc is home else home
            self.assertEqual(
                nfc.division,
                POSITIONS[seventeenth_row[POSITIONS.index(afc.division)]],
            )
            if (year - 2021) % 2 == 0:
                self.assertEqual(home.conference, AFC_SIDE)
            else:
                self.assertEqual(home.conference, NFC_SIDE)
        self.assertEqual(len(cross), 16)
        west = next(
            seat for seat in by_id.values()
            if seat.conference == NFC_SIDE and seat.division == 'd4' and seat.slot == 1
        )
        opponent_division, nfc_home = NFC_WEST_SEVENTEENTH[year]
        matched = [
            game for game in cross
            if west.franchise_id in (game.home_id, game.away_id)
        ]
        self.assertEqual(len(matched), 1)
        game = matched[0]
        other = by_id[game.away_id if game.home_id == west.franchise_id else game.home_id]
        self.assertEqual(other.division, opponent_division)
        self.assertEqual(other.conference, AFC_SIDE)
        self.assertEqual(game.home_id == west.franchise_id, nfc_home)
        if year in NFC_WEST_ROTATION:
            intra, inter = NFC_WEST_ROTATION[year]
            self.assertEqual(_partner(games, by_id, west, 'conference'), intra)
            self.assertEqual(_partner(games, by_id, west, 'interconference'), inter)


def _conference(franchise_id, seats):
    return next(seat.conference for seat in seats if seat.franchise_id == franchise_id)


def _partner(games, by_id, seat, category):
    divisions = {
        by_id[game.away_id if game.home_id == seat.franchise_id else game.home_id].division
        for game in games
        if game.category == category and seat.franchise_id in (game.home_id, game.away_id)
    }
    if len(divisions) != 1:
        raise AssertionError(divisions)
    return next(iter(divisions))


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
