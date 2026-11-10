from collections import Counter, defaultdict
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
)
from timelines.services.schedule_2002 import (
    AFC_SIDE,
    LATER_SEASON,
    NFC_SIDE,
    NO_OPENING_2002_PLACES,
    POSITIONS,
    Seat,
    TimelineScheduleRefused,
    generate_timeline_schedule,
    planned_games,
)
from timelines.tests.test_division_place_2002 import _tied_results
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
PROFILE_KEYS = {'y2002'}
SAME_PLACE_HOME = {'d1': 'd2', 'd2': 'd3', 'd3': 'd4', 'd4': 'd1'}


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


class Schedule2003FormulaTests(SimpleTestCase):
    def test_2003_rotations_sites_and_totals(self):
        self.assertEqual((2003 - 2002) % 3, 1)
        self.assertEqual((2003 - 2002) % 4, 1)
        seats = _synthetic_seats()
        places = _slot_places(seats)
        games = planned_games(2003, seats, places)
        by_id = {seat.franchise_id: seat for seat in seats}

        self.assertEqual(len(games), 256)
        self.assertEqual(len(set(games)), 256)
        self.assertEqual(Counter(game.category for game in games), {
            'division': 96,
            'conference': 64,
            'interconference': 64,
            'same_place': 32,
        })
        directed = Counter((game.home_id, game.away_id) for game in games)
        self.assertTrue(all(count == 1 for count in directed.values()))
        unordered = Counter(
            frozenset((game.home_id, game.away_id)) for game in games
        )
        for pair, count in unordered.items():
            home_id, away_id = tuple(pair)
            if count == 2:
                self.assertEqual(by_id[home_id].division, by_id[away_id].division)
                self.assertEqual(by_id[home_id].conference, by_id[away_id].conference)
            else:
                self.assertEqual(count, 1)

        conference_pairs = set()
        inter_pairs = set()
        same_pairs = set()
        for game in games:
            home = by_id[game.home_id]
            away = by_id[game.away_id]
            pair = (home.division, away.division)
            if game.category == 'conference':
                self.assertEqual(home.conference, away.conference)
                conference_pairs.add(frozenset(pair))
            elif game.category == 'interconference':
                self.assertNotEqual(home.conference, away.conference)
                inter_pairs.add((home.conference, home.division, away.division))
            elif game.category == 'same_place':
                self.assertEqual(home.conference, away.conference)
                self.assertEqual(places[home.franchise_id], places[away.franchise_id])
                same_pairs.add((home.conference, home.division, away.division))
            else:
                self.assertEqual(home.division, away.division)
                self.assertEqual(home.conference, away.conference)

        self.assertEqual(conference_pairs, {frozenset(('d1', 'd3')), frozenset(('d2', 'd4'))})
        self.assertEqual(inter_pairs, {
            (AFC_SIDE, 'd1', 'd1'),
            (NFC_SIDE, 'd1', 'd1'),
            (AFC_SIDE, 'd2', 'd4'),
            (NFC_SIDE, 'd4', 'd2'),
            (AFC_SIDE, 'd3', 'd3'),
            (NFC_SIDE, 'd3', 'd3'),
            (AFC_SIDE, 'd4', 'd2'),
            (NFC_SIDE, 'd2', 'd4'),
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
            home = [game for game in played if game.home_id == seat.franchise_id]
            rivals = {
                by_id[other].franchise_id
                for other in (
                    game.away_id if game.home_id == seat.franchise_id else game.home_id
                    for game in played
                    if game.category == 'division'
                )
            }
            division_mates = {
                other.franchise_id
                for other in seats
                if other.conference == seat.conference
                and other.division == seat.division
                and other.franchise_id != seat.franchise_id
            }
            self.assertEqual(len(played), 16)
            self.assertEqual(len(home), 8)
            self.assertEqual(rivals, division_mates)
            for rival in division_mates:
                self.assertIn((seat.franchise_id, rival), directed)
                self.assertIn((rival, seat.franchise_id), directed)
            conference_opponents = {
                by_id[game.away_id if game.home_id == seat.franchise_id else game.home_id]
                for game in played
                if game.category == 'conference'
            }
            inter_opponents = {
                by_id[game.away_id if game.home_id == seat.franchise_id else game.home_id]
                for game in played
                if game.category == 'interconference'
            }
            place_opponents = {
                by_id[game.away_id if game.home_id == seat.franchise_id else game.home_id]
                for game in played
                if game.category == 'same_place'
            }
            self.assertEqual(len(conference_opponents), 4)
            self.assertEqual(len(inter_opponents), 4)
            self.assertEqual(len(place_opponents), 2)
            self.assertEqual(len([game for game in home if game.category == 'same_place']), 1)
            self.assertEqual(
                {club.division for club in conference_opponents},
                {_rotating_same(seat.division)},
            )
            self.assertEqual(
                {club.division for club in inter_opponents},
                _rotating_other(seat.conference, seat.division),
            )
            self.assertEqual(
                {club.division for club in place_opponents},
                _same_place_divisions(seat.division),
            )
            for opponents in (conference_opponents, inter_opponents):
                for club in opponents:
                    direction = (seat.franchise_id, club.franchise_id)
                    self.assertEqual(direction in directed, _first_meeting_home(seat, club))


def _first_meeting_home(seat, opponent):
    """The pairing's first division hosts a club in its own slot pair."""
    if seat.conference == opponent.conference:
        first = min((seat.division, opponent.division), key=POSITIONS.index)
        seat_is_first = seat.division == first
    else:
        seat_is_first = seat.conference == AFC_SIDE
    same_pair = (seat.slot <= 2) == (opponent.slot <= 2)
    if seat_is_first:
        return same_pair
    return not same_pair


def _rotating_same(division):
    return {'d1': 'd3', 'd3': 'd1', 'd2': 'd4', 'd4': 'd2'}[division]


def _rotating_other(conference, division):
    afc = {'d1': 'd1', 'd2': 'd4', 'd3': 'd3', 'd4': 'd2'}
    if conference == AFC_SIDE:
        return {afc[division]}
    reverse = {right: left for left, right in afc.items()}
    return {reverse[division]}


def _same_place_divisions(division):
    partner = _rotating_same(division)
    return {item for item in POSITIONS if item not in (division, partner)}


class Schedule2003StorageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        league = League(
            name='Schedule 2003 League',
            slug='schedule-2003-league',
            tier=1,
            active=True,
        )
        league.save()
        release = UniverseRelease(
            slug='schedule-2003',
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

    def test_played_places_schedule_2003_and_reruns_write_nothing(self):
        outcome, writes = _writes_during(lambda: generate_timeline_schedule(
            timeline_id=self.timeline.pk,
            season_year=2002,
        ))
        self.assertEqual(outcome, 'unchanged')
        self.assertEqual(writes, [])

        _close_ranked(self.timeline)
        _season(self.timeline, self.profiles, 2003)
        before = _kept_rows(self.release, self.timeline, self.pairing_id, self.week_id, self.standing_id)
        created = generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2003)
        self.assertEqual(created, 'created')
        self._assert_stored_2003(self.timeline)
        self._assert_place_opponents(self.timeline)
        season = TimelineSeason.objects.get(timeline=self.timeline, season_year=2003)
        self.assertFalse(season.regular_season_simulated)
        self.timeline.refresh_from_db()
        self.assertEqual(self.timeline.current_season_year, 2002)
        self.assertEqual(
            ScheduleGameResult.objects.filter(timeline=self.timeline, season_year=2003).count(),
            0,
        )

        again, again_writes = _writes_during(lambda: generate_timeline_schedule(
            timeline_id=self.timeline.pk,
            season_year=2003,
        ))
        self.assertEqual(again, 'unchanged')
        self.assertEqual(again_writes, [])
        with self.assertRaisesMessage(TimelineScheduleRefused, 'simulated'):
            generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2002)
        with self.assertRaisesMessage(TimelineScheduleRefused, LATER_SEASON):
            generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2004)
        self.assertEqual(
            _kept_rows(self.release, self.timeline, self.pairing_id, self.week_id, self.standing_id),
            before,
        )
        self.assertEqual(
            ScheduleGame.objects.filter(timeline=self.timeline, season_year=2003).count(),
            256,
        )

    def test_two_timelines_keep_identity_and_change_same_place_opponents(self):
        ranked = self.timeline
        tied = _timeline(self.release, 'tied 2002', self.profiles, 2002)
        generate_timeline_schedule(timeline_id=tied.pk, season_year=2002)
        _close_ranked(ranked)
        _lock_directives(tied)
        close_2002_regular_season(timeline_id=tied.pk, results=_tied_results(tied))
        identity = _franchise_identity(self.release)
        memberships = _membership_rows(self.release)
        for timeline in (ranked, tied):
            _season(timeline, self.profiles, 2003)
            generate_timeline_schedule(timeline_id=timeline.pk, season_year=2003)

        ranked_places = _place_map(ranked)
        tied_places = _place_map(tied)
        self.assertNotEqual(ranked_places, tied_places)
        ranked_place_games = _category_games(ranked, 'same_place')
        tied_place_games = _category_games(tied, 'same_place')
        self.assertNotEqual(ranked_place_games, tied_place_games)
        self.assertEqual(_category_games(ranked, 'division'), _category_games(tied, 'division'))
        self.assertEqual(_category_games(ranked, 'conference'), _category_games(tied, 'conference'))
        self.assertEqual(
            _category_games(ranked, 'interconference'),
            _category_games(tied, 'interconference'),
        )
        self.assertEqual(_franchise_identity(self.release), identity)
        self.assertEqual(_membership_rows(self.release), memberships)
        for timeline in (ranked, tied):
            self._assert_place_opponents(timeline)

    def test_incomplete_2002_is_refused(self):
        _season(self.timeline, self.profiles, 2003)
        with self.assertRaisesMessage(TimelineScheduleRefused, 'not complete'):
            generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2003)
        self.assertEqual(ScheduleGame.objects.filter(season_year=2003).count(), 0)
        self.assertFalse(TimelineSeason.objects.get(
            timeline=self.timeline,
            season_year=2002,
        ).regular_season_simulated)

    def test_the_flag_alone_does_not_schedule_2003(self):
        _season(self.timeline, self.profiles, 2003)
        season = TimelineSeason.objects.get(timeline=self.timeline, season_year=2002)
        season.setup_complete = True
        season.directives_locked = True
        season.save()
        TimelineSeason.objects.filter(pk=season.pk).update(regular_season_simulated=True)
        with self.assertRaisesMessage(TimelineScheduleRefused, 'not complete'):
            generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2003)
        self.assertEqual(ScheduleGame.objects.filter(season_year=2003).count(), 0)

    def test_tampered_results_places_and_draws_are_refused(self):
        _lock_directives(self.timeline)
        close_2002_regular_season(
            timeline_id=self.timeline.pk,
            results=_tied_results(self.timeline),
        )
        _season(self.timeline, self.profiles, 2003)
        result = ScheduleGameResult.objects.filter(timeline=self.timeline).order_by('pk').first()
        ScheduleGameResult.objects.filter(pk=result.pk).update(home_score=result.home_score + 7)
        with self.assertRaises(TimelineScheduleRefused):
            generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2003)
        self.assertEqual(ScheduleGame.objects.filter(season_year=2003).count(), 0)
        self.assertEqual(
            ScheduleGameResult.objects.get(pk=result.pk).home_score,
            result.home_score + 7,
        )

        ScheduleGameResult.objects.filter(pk=result.pk).update(home_score=result.home_score)
        place = TimelineDivisionPlace.objects.filter(
            timeline=self.timeline,
            resolved_by='sole_remaining',
        ).order_by('pk').first()
        TimelineDivisionPlace.objects.filter(pk=place.pk).update(resolved_by='head_to_head')
        with self.assertRaisesMessage(TimelineScheduleRefused, 'do not match'):
            generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2003)
        self.assertEqual(ScheduleGame.objects.filter(season_year=2003).count(), 0)
        self.assertEqual(
            TimelineDivisionPlace.objects.get(pk=place.pk).resolved_by,
            'head_to_head',
        )

        TimelineDivisionPlace.objects.filter(pk=place.pk).update(resolved_by='sole_remaining')
        draw = TimelineDivisionDraw.objects.filter(timeline=self.timeline).order_by('pk').first()
        self.assertIsNotNone(draw)
        TimelineDivisionDraw.objects.filter(pk=draw.pk).update(digest='0' * 64)
        with self.assertRaisesMessage(TimelineScheduleRefused, 'draw'):
            generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2003)
        self.assertEqual(ScheduleGame.objects.filter(season_year=2003).count(), 0)
        self.assertEqual(TimelineDivisionDraw.objects.get(pk=draw.pk).digest, '0' * 64)

    def test_a_2003_start_is_refused_without_a_seed(self):
        timeline = Timeline(
            release=self.release,
            name='starts in 2003',
            start_season_year=2003,
            current_season_year=2003,
            season_index=1,
            phase=Timeline.Phase.DIRECTION,
            status=Timeline.Status.ACTIVE,
        )
        timeline.save()
        _season(timeline, self.profiles, 2003)
        with self.assertRaisesMessage(TimelineScheduleRefused, NO_OPENING_2002_PLACES):
            generate_timeline_schedule(timeline_id=timeline.pk, season_year=2003)
        self.assertEqual(TimelineScheduleSeed.objects.filter(timeline=timeline).count(), 0)
        self.assertEqual(ScheduleGame.objects.filter(timeline=timeline).count(), 0)
        self.assertEqual(
            list(TimelineSeason.objects.filter(timeline=timeline).values_list('season_year', flat=True)),
            [2003],
        )

    def test_a_missing_2003_season_is_not_created(self):
        _close_ranked(self.timeline)
        years = set(TimelineSeason.objects.filter(timeline=self.timeline).values_list(
            'season_year',
            flat=True,
        ))
        with self.assertRaisesMessage(TimelineScheduleRefused, 'no season for this year'):
            generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2003)
        self.assertEqual(
            set(TimelineSeason.objects.filter(timeline=self.timeline).values_list('season_year', flat=True)),
            years,
        )
        self.assertNotIn(2003, years)
        self.assertEqual(ScheduleGame.objects.filter(season_year=2003).count(), 0)

    def test_partial_and_mismatched_2003_rows_stay(self):
        _close_ranked(self.timeline)
        _season(self.timeline, self.profiles, 2003)
        generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2003)
        game = ScheduleGame.objects.filter(
            timeline=self.timeline,
            season_year=2003,
            category=ScheduleGame.Category.SAME_PLACE,
        ).order_by('pk').first()
        ScheduleGame.objects.filter(pk=game.pk).delete()
        with self.assertRaisesMessage(TimelineScheduleRefused, 'do not match'):
            generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2003)
        self.assertEqual(
            ScheduleGame.objects.filter(timeline=self.timeline, season_year=2003).count(),
            255,
        )
        self.assertFalse(ScheduleGame.objects.filter(pk=game.pk).exists())

        other = ScheduleGame.objects.filter(
            timeline=self.timeline,
            season_year=2003,
            category=ScheduleGame.Category.CONFERENCE,
        ).order_by('pk').first()
        ScheduleGame.objects.filter(pk=other.pk).update(
            category=ScheduleGame.Category.INTERCONFERENCE,
        )
        with self.assertRaisesMessage(TimelineScheduleRefused, 'do not match'):
            generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2003)
        self.assertEqual(
            ScheduleGame.objects.get(pk=other.pk).category,
            ScheduleGame.Category.INTERCONFERENCE,
        )
        self.assertEqual(
            ScheduleGame.objects.filter(timeline=self.timeline, season_year=2003).count(),
            255,
        )

    def test_insert_failure_rolls_back_2003(self):
        _close_ranked(self.timeline)
        _season(self.timeline, self.profiles, 2003)
        before = _kept_rows(self.release, self.timeline, self.pairing_id, self.week_id, self.standing_id)
        with patch(
            'timelines.services.schedule_2002.ScheduleGame.objects.bulk_create',
            side_effect=IntegrityError('fail'),
        ):
            with self.assertRaisesMessage(TimelineScheduleRefused, 'insert failed'):
                generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2003)
        self.assertEqual(ScheduleGame.objects.filter(season_year=2003).count(), 0)
        self.assertEqual(
            _kept_rows(self.release, self.timeline, self.pairing_id, self.week_id, self.standing_id),
            before,
        )

    def _assert_stored_2003(self, timeline):
        games = list(ScheduleGame.objects.filter(timeline=timeline, season_year=2003))
        self.assertEqual(len(games), 256)
        self.assertEqual(Counter(game.category for game in games), {
            ScheduleGame.Category.DIVISION: 96,
            ScheduleGame.Category.CONFERENCE: 64,
            ScheduleGame.Category.INTERCONFERENCE: 64,
            ScheduleGame.Category.SAME_PLACE: 32,
        })
        memberships = {
            row.franchise_id: row
            for row in FranchiseSeasonMembership.objects.filter(
                franchise__release=self.release,
                season_year=2003,
            )
        }
        directed = {(game.home_id, game.away_id) for game in games}
        appearances = Counter()
        homes = Counter()
        for game in games:
            appearances[game.home_id] += 1
            appearances[game.away_id] += 1
            homes[game.home_id] += 1
            home = memberships[game.home_id]
            away = memberships[game.away_id]
            if game.category == ScheduleGame.Category.DIVISION:
                self.assertEqual(home.division_slug, away.division_slug)
                self.assertIn((away.franchise_id, home.franchise_id), directed)
            elif game.category == ScheduleGame.Category.CONFERENCE:
                self.assertEqual(away.division_slug, _rotating_same(home.division_slug))
            elif game.category == ScheduleGame.Category.INTERCONFERENCE:
                self.assertEqual(
                    away.division_slug,
                    next(iter(_rotating_other(home.conference_slug, home.division_slug))),
                )
            else:
                self.assertEqual(away.division_slug, SAME_PLACE_HOME[home.division_slug])
        self.assertEqual(set(appearances), set(memberships))
        for franchise_id, count in appearances.items():
            self.assertEqual(count, 16)
            self.assertEqual(homes[franchise_id], 8)
        for game in games:
            if game.category not in (
                ScheduleGame.Category.CONFERENCE,
                ScheduleGame.Category.INTERCONFERENCE,
            ):
                continue
            home = memberships[game.home_id]
            away = memberships[game.away_id]
            self.assertTrue(_first_meeting_home(
                Seat(home.franchise_id, '', home.conference_slug, home.division_slug, home.slot_order),
                Seat(away.franchise_id, '', away.conference_slug, away.division_slug, away.slot_order),
            ))

    def _assert_place_opponents(self, timeline):
        places = {
            row.franchise_id: row
            for row in TimelineDivisionPlace.objects.filter(timeline=timeline, season_year=2002)
        }
        memberships = {
            row.franchise_id: row
            for row in FranchiseSeasonMembership.objects.filter(
                franchise__release=self.release,
                season_year=2003,
            )
        }
        by_seat = {}
        for franchise_id, place in places.items():
            seat = memberships[franchise_id]
            by_seat[(seat.conference_slug, seat.division_slug, place.division_place)] = franchise_id
        games = ScheduleGame.objects.filter(
            timeline=timeline,
            season_year=2003,
            category=ScheduleGame.Category.SAME_PLACE,
        )
        found = defaultdict(set)
        for game in games:
            found[game.home_id].add(('home', game.away_id))
            found[game.away_id].add(('away', game.home_id))
        representatives = [
            row for row in places.values()
            if row.conference_slug == AFC_SIDE and row.division_slug == 'd1'
        ]
        self.assertEqual(sorted(row.division_place for row in representatives), [1, 2, 3, 4])
        for row in representatives:
            seat = memberships[row.franchise_id]
            hosted = by_seat[(seat.conference_slug, SAME_PLACE_HOME[seat.division_slug], row.division_place)]
            visitor_division = next(
                division for division, host in SAME_PLACE_HOME.items()
                if host == seat.division_slug
            )
            visited = by_seat[(seat.conference_slug, visitor_division, row.division_place)]
            self.assertEqual(found[row.franchise_id], {('home', hosted), ('away', visited)})
            self.assertNotEqual(hosted, visited)


def _close_ranked(timeline):
    _lock_directives(timeline)
    close_2002_regular_season(timeline_id=timeline.pk, results=_ranked_results(timeline))


def _ranked_results(timeline):
    memberships = {
        row.franchise_id: row
        for row in FranchiseSeasonMembership.objects.filter(
            franchise__release_id=timeline.release_id,
            season_year=2002,
        )
    }
    results = []
    for game in ScheduleGame.objects.filter(timeline=timeline, season_year=2002):
        home = memberships[game.home_id]
        away = memberships[game.away_id]
        if home.conference_slug == away.conference_slug and home.division_slug == away.division_slug:
            if home.slot_order < away.slot_order:
                results.append(Result(game.home_id, game.away_id, 13, 0, 1, 0, 'project-result'))
            else:
                results.append(Result(game.home_id, game.away_id, 0, 13, 0, 1, 'project-result'))
        else:
            results.append(Result(game.home_id, game.away_id, 6, 6, 1, 1, 'project-result'))
    return results


def _lock_directives(timeline):
    season = TimelineSeason.objects.get(timeline=timeline, season_year=2002)
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


def _place_map(timeline):
    return {
        row.franchise_id: row.division_place
        for row in TimelineDivisionPlace.objects.filter(timeline=timeline, season_year=2002)
    }


def _category_games(timeline, category):
    return frozenset(
        ScheduleGame.objects.filter(
            timeline=timeline,
            season_year=2003,
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


def _membership_rows(release):
    return tuple(FranchiseSeasonMembership.objects.filter(
        franchise__release=release,
    ).order_by('franchise_id', 'season_year').values_list(
        'franchise_id',
        'season_year',
        'conference_slug',
        'division_slug',
        'slot_order',
    ))


def _kept_rows(release, timeline, pairing_id, week_id, standing_id):
    release.refresh_from_db()
    pairing = OpponentPairing.objects.get(pk=pairing_id)
    week = OpponentWeekAssignment.objects.get(pk=week_id)
    standing = HistoricalStanding.objects.get(pk=standing_id)
    season = TimelineSeason.objects.get(timeline=timeline, season_year=2002)
    return (
        release.catalog_fingerprint,
        release.validation_fingerprint,
        pairing.home_franchise_id,
        pairing.away_franchise_id,
        pairing.kind,
        week.week,
        standing.wins,
        standing.draft_order,
        OpponentPairing.objects.filter(release=release).count(),
        OpponentWeekAssignment.objects.filter(release=release).count(),
        HistoricalStanding.objects.filter(franchise__release=release).count(),
        _membership_rows(release),
        _franchise_identity(release),
        tuple(ScheduleGame.objects.filter(timeline=timeline, season_year=2002).order_by('pk').values_list(
            'pk',
            'home_id',
            'away_id',
            'category',
            'kind',
        )),
        tuple(ScheduleGameResult.objects.filter(timeline=timeline).order_by('pk').values_list(
            'pk',
            'game_id',
            'home_score',
            'away_score',
            'home_touchdowns',
            'away_touchdowns',
            'source_key',
            'season_year',
        )),
        tuple(TimelineDivisionPlace.objects.filter(timeline=timeline).order_by('pk').values_list(
            'pk',
            'franchise_id',
            'division_place',
            'conference_slug',
            'division_slug',
            'resolved_by',
            'draw_id',
        )),
        tuple(TimelineDivisionDraw.objects.filter(timeline=timeline).order_by('pk').values_list(
            'pk',
            'coordinate',
            'digest',
            'chosen_slug',
            'outcome',
            'candidates',
        )),
        tuple(TimelineScheduleSeed.objects.filter(timeline=timeline).order_by('pk').values_list(
            'pk',
            'franchise_id',
            'division_place',
            'conference_slug',
            'division_slug',
        )),
        season.regular_season_simulated,
    )
