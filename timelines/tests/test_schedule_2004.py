from collections import Counter
from unittest.mock import patch

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
    TimelineScheduleSeed,
    TimelineSeason,
)
from timelines.services.division_place_2002 import (
    Result,
    close_2002_regular_season,
    close_2003_regular_season,
)
from timelines.services.schedule_2002 import (
    AFC_SIDE,
    NFC_SIDE,
    POSITIONS,
    Seat,
    TimelineScheduleRefused,
    generate_timeline_schedule,
    planned_games,
)
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
PROFILE_KEYS = {'y2002', 'y2004'}
SAME_PLACE_HOME = {'d1': 'd3', 'd2': 'd4', 'd3': 'd2', 'd4': 'd1'}


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


def _slot_places(seats):
    return {seat.franchise_id: seat.slot for seat in seats}


def _directions(games, category):
    return {
        (game.home_id, game.away_id)
        for game in games
        if game.category == category
    }


class Schedule2004FormulaTests(SimpleTestCase):
    def test_2004_rotation_sites_balance_and_second_meeting(self):
        self.assertEqual((2004 - 2002) % 3, 2)
        self.assertEqual((2004 - 2002) % 4, 2)
        seats = _synthetic_seats()
        places = _slot_places(seats)
        games = planned_games(2004, seats, places)
        by_id = {seat.franchise_id: seat for seat in seats}

        self.assertEqual(len(games), 256)
        self.assertEqual(len(set(games)), 256)
        self.assertEqual(Counter(game.category for game in games), {
            'division': 96,
            'conference': 64,
            'interconference': 64,
            'same_place': 32,
        })
        directed = {(game.home_id, game.away_id) for game in games}
        conference_pairs = set()
        inter_pairs = set()
        same_pairs = set()
        for game in games:
            home = by_id[game.home_id]
            away = by_id[game.away_id]
            if game.category == 'conference':
                self.assertEqual(home.conference, away.conference)
                conference_pairs.add(frozenset((home.division, away.division)))
            elif game.category == 'interconference':
                self.assertNotEqual(home.conference, away.conference)
                inter_pairs.add((home.conference, home.division, away.division))
            elif game.category == 'same_place':
                self.assertEqual(home.conference, away.conference)
                self.assertEqual(places[home.franchise_id], places[away.franchise_id])
                same_pairs.add((home.conference, home.division, away.division))
        self.assertEqual(
            conference_pairs,
            {frozenset(('d1', 'd2')), frozenset(('d3', 'd4'))},
        )
        self.assertEqual(inter_pairs, {
            (AFC_SIDE, 'd1', 'd4'),
            (NFC_SIDE, 'd4', 'd1'),
            (AFC_SIDE, 'd2', 'd1'),
            (NFC_SIDE, 'd1', 'd2'),
            (AFC_SIDE, 'd3', 'd2'),
            (NFC_SIDE, 'd2', 'd3'),
            (AFC_SIDE, 'd4', 'd3'),
            (NFC_SIDE, 'd3', 'd4'),
        })
        self.assertEqual(same_pairs, {
            (conference, home_division, away_division)
            for conference in (AFC_SIDE, NFC_SIDE)
            for home_division, away_division in SAME_PLACE_HOME.items()
        })
        for seat in seats:
            played = [
                game for game in games
                if seat.franchise_id in (game.home_id, game.away_id)
            ]
            homes = [game for game in played if game.home_id == seat.franchise_id]
            self.assertEqual(len(played), 16)
            self.assertEqual(len(homes), 8)
            self.assertEqual(
                len([game for game in homes if game.category == 'same_place']),
                1,
            )
            for game in played:
                if game.category not in ('conference', 'interconference'):
                    continue
                other_id = game.away_id if game.home_id == seat.franchise_id else game.home_id
                self.assertEqual(
                    (seat.franchise_id, other_id) in directed,
                    _first_meeting_home(seat, by_id[other_id]),
                )

        returned = planned_games(2007, seats, places)
        self.assertEqual(
            {(away, home) for home, away in _directions(games, 'conference')},
            _directions(returned, 'conference'),
        )
        self.assertEqual(
            {(away, home) for home, away in _directions(games, 'same_place')},
            _directions(returned, 'same_place'),
        )
        inter_return = planned_games(2008, seats, places)
        self.assertEqual(
            {(away, home) for home, away in _directions(games, 'interconference')},
            _directions(inter_return, 'interconference'),
        )


class Schedule2004StorageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        league = League(
            name='Schedule 2004 League',
            slug='schedule-2004-league',
            tier=1,
            active=True,
        )
        league.save()
        release = UniverseRelease(
            slug='schedule-2004',
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
        cls.timeline = _prepare_ranked(release, 'opens in 2002', profiles)

    def test_verified_2003_places_schedule_2004(self):
        before = _kept(self.release, self.timeline)
        places = _place_map(self.timeline, 2003)
        memberships = {
            row.franchise_id: row.slot_order
            for row in FranchiseSeasonMembership.objects.filter(
                franchise__release=self.release,
                season_year=2003,
            )
        }
        self.assertEqual(places, memberships)
        outcome = generate_timeline_schedule(
            timeline_id=self.timeline.pk,
            season_year=2004,
        )
        self.assertEqual(outcome, 'created')
        games = list(ScheduleGame.objects.filter(
            timeline=self.timeline,
            season_year=2004,
        ))
        self.assertEqual(len(games), 256)
        self.assertEqual(Counter(game.category for game in games), {
            ScheduleGame.Category.DIVISION: 96,
            ScheduleGame.Category.CONFERENCE: 64,
            ScheduleGame.Category.INTERCONFERENCE: 64,
            ScheduleGame.Category.SAME_PLACE: 32,
        })
        appearances = Counter()
        homes = Counter()
        for game in games:
            appearances[game.home_id] += 1
            appearances[game.away_id] += 1
            homes[game.home_id] += 1
        self.assertEqual(set(appearances), set(memberships))
        for franchise_id, count in appearances.items():
            self.assertEqual(count, 16)
            self.assertEqual(homes[franchise_id], 8)
        self._assert_same_place_uses_2003_places()
        season = TimelineSeason.objects.get(timeline=self.timeline, season_year=2004)
        self.assertFalse(season.regular_season_simulated)
        self.timeline.refresh_from_db()
        self.assertEqual(self.timeline.current_season_year, 2002)
        self.assertEqual(
            ScheduleGameResult.objects.filter(timeline=self.timeline, season_year=2004).count(),
            0,
        )
        self.assertEqual(_kept(self.release, self.timeline), before)
        again, writes = _writes_during(lambda: generate_timeline_schedule(
            timeline_id=self.timeline.pk,
            season_year=2004,
        ))
        self.assertEqual(again, 'unchanged')
        self.assertEqual(writes, [])
        with self.assertRaisesMessage(
            TimelineScheduleRefused,
            'The timeline has no season for this year.',
        ):
            generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2005)
        self.assertEqual(
            ScheduleGame.objects.filter(timeline=self.timeline, season_year=2005).count(),
            0,
        )

    def test_different_2003_places_change_only_same_place_games(self):
        generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2004)
        other = _prepare_tied(self.release, 'tied 2003', self.profiles)
        identity = _franchise_identity(self.release)
        generate_timeline_schedule(timeline_id=other.pk, season_year=2004)
        self.assertNotEqual(
            _place_map(self.timeline, 2003),
            _place_map(other, 2003),
        )
        self.assertEqual(_franchise_identity(self.release), identity)
        for category in (
            ScheduleGame.Category.DIVISION,
            ScheduleGame.Category.CONFERENCE,
            ScheduleGame.Category.INTERCONFERENCE,
        ):
            self.assertEqual(
                _category_pairs(self.timeline, category),
                _category_pairs(other, category),
            )
        self.assertNotEqual(
            _category_pairs(self.timeline, ScheduleGame.Category.SAME_PLACE),
            _category_pairs(other, ScheduleGame.Category.SAME_PLACE),
        )

    def test_incomplete_2003_and_a_missing_2004_season_write_nothing(self):
        open_season = _timeline(self.release, '2003 still open', self.profiles, 2002)
        _open_through_2003_fixtures(open_season, self.profiles)
        _season(open_season, self.profiles, 2004)
        with self.assertRaisesMessage(
            TimelineScheduleRefused,
            'The 2003 regular season is not complete.',
        ):
            generate_timeline_schedule(timeline_id=open_season.pk, season_year=2004)
        self.assertEqual(
            ScheduleGame.objects.filter(timeline=open_season, season_year=2004).count(),
            0,
        )

        closed = _prepare_ranked(self.release, 'no 2004 row', self.profiles, make_2004=False)
        with self.assertRaisesMessage(
            TimelineScheduleRefused,
            'The timeline has no season for this year.',
        ):
            generate_timeline_schedule(timeline_id=closed.pk, season_year=2004)
        self.assertEqual(
            ScheduleGame.objects.filter(timeline=closed, season_year=2004).count(),
            0,
        )

    def test_partial_and_mismatched_2004_rows_stay_in_place(self):
        home = UniverseFranchise.objects.get(
            release=self.release,
            placeholder_slug='founding-01',
        )
        away = UniverseFranchise.objects.get(
            release=self.release,
            placeholder_slug='founding-02',
        )
        ScheduleGame.objects.bulk_create([ScheduleGame(
            timeline=self.timeline,
            season_year=2004,
            home=home,
            away=away,
            kind=ScheduleGame.Kind.REGULAR,
            category=ScheduleGame.Category.DIVISION,
        )])
        with self.assertRaisesMessage(TimelineScheduleRefused, 'do not match'):
            generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2004)
        self.assertEqual(
            ScheduleGame.objects.filter(timeline=self.timeline, season_year=2004).count(),
            1,
        )
        ScheduleGame.objects.filter(timeline=self.timeline, season_year=2004).delete()

        generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2004)
        game = ScheduleGame.objects.filter(
            timeline=self.timeline,
            season_year=2004,
            category=ScheduleGame.Category.SAME_PLACE,
        ).order_by('pk').first()
        ScheduleGame.objects.filter(pk=game.pk).delete()
        with self.assertRaisesMessage(TimelineScheduleRefused, 'do not match'):
            generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2004)
        self.assertEqual(
            ScheduleGame.objects.filter(timeline=self.timeline, season_year=2004).count(),
            255,
        )
        other = ScheduleGame.objects.filter(
            timeline=self.timeline,
            season_year=2004,
            category=ScheduleGame.Category.CONFERENCE,
        ).order_by('pk').first()
        ScheduleGame.objects.filter(pk=other.pk).update(
            category=ScheduleGame.Category.INTERCONFERENCE,
        )
        with self.assertRaisesMessage(TimelineScheduleRefused, 'do not match'):
            generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2004)
        self.assertEqual(
            ScheduleGame.objects.get(pk=other.pk).category,
            ScheduleGame.Category.INTERCONFERENCE,
        )

    def test_insert_failure_rolls_back_2004(self):
        before = _kept(self.release, self.timeline)
        with patch(
            'timelines.services.schedule_2002.ScheduleGame.objects.bulk_create',
            side_effect=IntegrityError('fail'),
        ):
            with self.assertRaisesMessage(TimelineScheduleRefused, 'insert failed'):
                generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2004)
        self.assertEqual(
            ScheduleGame.objects.filter(timeline=self.timeline, season_year=2004).count(),
            0,
        )
        self.assertFalse(TimelineSeason.objects.get(
            timeline=self.timeline,
            season_year=2004,
        ).regular_season_simulated)
        self.assertEqual(_kept(self.release, self.timeline), before)

    def _assert_same_place_uses_2003_places(self):
        places = {
            row.franchise_id: row
            for row in TimelineDivisionPlace.objects.filter(
                timeline=self.timeline,
                season_year=2003,
            )
        }
        memberships = {
            row.franchise_id: row
            for row in FranchiseSeasonMembership.objects.filter(
                franchise__release=self.release,
                season_year=2004,
            )
        }
        by_seat = {}
        for franchise_id, place in places.items():
            seat = memberships[franchise_id]
            by_seat[(seat.conference_slug, seat.division_slug, place.division_place)] = franchise_id
        found = {}
        for game in ScheduleGame.objects.filter(
            timeline=self.timeline,
            season_year=2004,
            category=ScheduleGame.Category.SAME_PLACE,
        ):
            found.setdefault(game.home_id, set()).add(('home', game.away_id))
            found.setdefault(game.away_id, set()).add(('away', game.home_id))
        for row in places.values():
            seat = memberships[row.franchise_id]
            hosted = by_seat[(
                seat.conference_slug,
                SAME_PLACE_HOME[seat.division_slug],
                row.division_place,
            )]
            visitor_division = next(
                division for division, host in SAME_PLACE_HOME.items()
                if host == seat.division_slug
            )
            visited = by_seat[(
                seat.conference_slug,
                visitor_division,
                row.division_place,
            )]
            self.assertEqual(
                found[row.franchise_id],
                {('home', hosted), ('away', visited)},
            )


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


def _prepare_ranked(release, name, profiles, make_2004=True):
    timeline = _timeline(release, name, profiles, 2002)
    generate_timeline_schedule(timeline_id=timeline.pk, season_year=2002)
    _lock(timeline, 2002)
    close_2002_regular_season(
        timeline_id=timeline.pk,
        results=_ranked_results(timeline, 2002),
    )
    _season(timeline, profiles, 2003)
    generate_timeline_schedule(timeline_id=timeline.pk, season_year=2003)
    _lock(timeline, 2003)
    close_2003_regular_season(
        timeline_id=timeline.pk,
        results=_ranked_results(timeline, 2003),
    )
    if make_2004:
        _season(timeline, profiles, 2004)
    return timeline


def _prepare_tied(release, name, profiles):
    timeline = _timeline(release, name, profiles, 2002)
    generate_timeline_schedule(timeline_id=timeline.pk, season_year=2002)
    _lock(timeline, 2002)
    close_2002_regular_season(
        timeline_id=timeline.pk,
        results=_tied_results(timeline, 2002),
    )
    _season(timeline, profiles, 2003)
    generate_timeline_schedule(timeline_id=timeline.pk, season_year=2003)
    _lock(timeline, 2003)
    close_2003_regular_season(
        timeline_id=timeline.pk,
        results=_tied_results(timeline, 2003),
    )
    _season(timeline, profiles, 2004)
    return timeline


def _open_through_2003_fixtures(timeline, profiles):
    generate_timeline_schedule(timeline_id=timeline.pk, season_year=2002)
    _lock(timeline, 2002)
    close_2002_regular_season(
        timeline_id=timeline.pk,
        results=_ranked_results(timeline, 2002),
    )
    _season(timeline, profiles, 2003)
    generate_timeline_schedule(timeline_id=timeline.pk, season_year=2003)
    _lock(timeline, 2003)


def _ranked_results(timeline, year):
    memberships = {
        row.franchise_id: row
        for row in FranchiseSeasonMembership.objects.filter(
            franchise__release_id=timeline.release_id,
            season_year=year,
        )
    }
    results = []
    for game in ScheduleGame.objects.filter(timeline=timeline, season_year=year):
        home = memberships[game.home_id]
        away = memberships[game.away_id]
        if (
            home.conference_slug == away.conference_slug
            and home.division_slug == away.division_slug
        ):
            if home.slot_order < away.slot_order:
                results.append(Result(
                    game.home_id, game.away_id, 13, 0, 1, 0, 'project-result',
                ))
            else:
                results.append(Result(
                    game.home_id, game.away_id, 0, 13, 0, 1, 'project-result',
                ))
        else:
            results.append(Result(
                game.home_id, game.away_id, 6, 6, 1, 1, 'project-result',
            ))
    return results


def _tied_results(timeline, year):
    return [
        Result(game.home_id, game.away_id, 6, 6, 1, 1, 'project-result')
        for game in ScheduleGame.objects.filter(timeline=timeline, season_year=year)
    ]


def _lock(timeline, year):
    season = TimelineSeason.objects.get(timeline=timeline, season_year=year)
    if season.directives_locked:
        return
    season.setup_complete = True
    season.directives_locked = True
    season.save()


def _timeline(release, name, profiles, start_year):
    timeline = Timeline(
        release=release,
        name=name,
        start_season_year=start_year,
        current_season_year=start_year,
        season_index=1,
        phase=Timeline.Phase.DIRECTION,
        status=Timeline.Status.ACTIVE,
    )
    timeline.save()
    _season(timeline, profiles, start_year)
    return timeline


def _season(timeline, profiles, year):
    profile = next(
        item for item in profiles
        if item.first_year <= year <= item.last_year
    )
    TimelineSeason(
        timeline=timeline,
        profile=profile,
        season_year=year,
        season_index=year - timeline.start_season_year + 1,
        phase=Timeline.Phase.DIRECTION,
    ).save()


def _place_map(timeline, year):
    return {
        row.franchise_id: row.division_place
        for row in TimelineDivisionPlace.objects.filter(
            timeline=timeline,
            season_year=year,
        )
    }


def _category_pairs(timeline, category):
    return frozenset(
        ScheduleGame.objects.filter(
            timeline=timeline,
            season_year=2004,
            category=category,
        ).values_list('home_id', 'away_id')
    )


def _franchise_identity(release):
    return tuple(UniverseFranchise.objects.filter(release=release).order_by('pk').values_list(
        'pk',
        'placeholder_slug',
        'origin',
        'first_active_season',
    ))


def _kept(release, timeline):
    release.refresh_from_db()
    return (
        release.catalog_fingerprint,
        release.validation_fingerprint,
        tuple(OpponentPairing.objects.filter(release=release).order_by('pk').values_list(
            'pk', 'home_franchise_id', 'away_franchise_id', 'kind',
        )),
        tuple(OpponentWeekAssignment.objects.filter(release=release).order_by('pk').values_list(
            'pk', 'pairing_id', 'week',
        )),
        tuple(HistoricalStanding.objects.filter(
            franchise__release=release,
        ).order_by('pk').values_list(
            'pk', 'franchise_id', 'wins', 'losses', 'ties', 'draft_order',
        )),
        tuple(FranchiseSeasonMembership.objects.filter(
            franchise__release=release,
        ).order_by('franchise_id', 'season_year').values_list(
            'franchise_id',
            'season_year',
            'conference_slug',
            'division_slug',
            'slot_order',
        )),
        _franchise_identity(release),
        _game_rows(timeline, 2002),
        _game_rows(timeline, 2003),
        _result_rows(timeline, 2002),
        _result_rows(timeline, 2003),
        _place_rows(timeline, 2002),
        _place_rows(timeline, 2003),
        _draw_rows(timeline, 2002),
        _draw_rows(timeline, 2003),
        tuple(TimelineScheduleSeed.objects.filter(timeline=timeline).order_by('pk').values_list(
            'pk', 'franchise_id', 'division_place', 'conference_slug', 'division_slug',
        )),
        TimelineSeason.objects.get(timeline=timeline, season_year=2002).regular_season_simulated,
        TimelineSeason.objects.get(timeline=timeline, season_year=2003).regular_season_simulated,
    )


def _game_rows(timeline, year):
    return tuple(ScheduleGame.objects.filter(
        timeline=timeline,
        season_year=year,
    ).order_by('pk').values_list('pk', 'home_id', 'away_id', 'category', 'kind'))


def _result_rows(timeline, year):
    return tuple(ScheduleGameResult.objects.filter(
        timeline=timeline,
        season_year=year,
    ).order_by('pk').values_list(
        'pk', 'game_id', 'home_score', 'away_score', 'source_key',
    ))


def _place_rows(timeline, year):
    return tuple(TimelineDivisionPlace.objects.filter(
        timeline=timeline,
        season_year=year,
    ).order_by('pk').values_list(
        'pk', 'franchise_id', 'division_place', 'resolved_by', 'draw_id',
    ))


def _draw_rows(timeline, year):
    return tuple(TimelineDivisionDraw.objects.filter(
        timeline=timeline,
        season_year=year,
    ).order_by('pk').values_list(
        'pk', 'coordinate', 'digest', 'chosen_slug', 'outcome', 'candidates',
    ))
