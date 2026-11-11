from collections import Counter
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from leagues.models import League
from timelines.models import (
    ScheduleGame,
    Timeline,
    TimelinePriorFinish,
    TimelineScheduleSeed,
    TimelineSchedulingDraw,
    TimelineSchedulingRank,
    TimelineSeason,
)
from timelines.services.prior_finish import (
    Finish,
    derive_scheduling_ranks,
    store_prior_finishes,
)
from timelines.services.schedule_2002 import (
    AFC_SIDE,
    FIRST_SEASON,
    LATER_SEASON,
    NFC_SIDE,
    POSITIONS,
    Fixture,
    Seat,
    TimelineScheduleRefused,
    _rank_slugs,
    generate_timeline_schedule,
    opening_places,
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
PROFILE_KEYS = {'y1999', 'y2002', 'y2014'}


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


class RotationFormulaTests(SimpleTestCase):
    def test_each_club_plays_sixteen_with_eight_at_home(self):
        seats = _synthetic_seats()
        games = planned_games(2002, seats, _slot_places(seats))

        self.assertEqual(len(games), 256)
        self.assertEqual(len(set(games)), 256)
        self.assertEqual(Counter(game.category for game in games), {
            'division': 96,
            'conference': 64,
            'interconference': 64,
            'same_place': 32,
        })
        for seat in seats:
            played = [
                game for game in games
                if seat.franchise_id in (game.home_id, game.away_id)
            ]
            home = [game for game in played if game.home_id == seat.franchise_id]
            self.assertEqual(len(played), 16)
            self.assertEqual(len(home), 8)
            self.assertEqual(
                len([game for game in home if game.category == 'same_place']),
                1,
            )

    def test_2002_orientation_and_later_flips(self):
        seats = _synthetic_seats()
        places = _slot_places(seats)
        first = planned_games(2002, seats, places)
        flipped = planned_games(2005, seats, places)
        west = planned_games(2010, seats, places)
        again = planned_games(2020, seats, places)

        def seat_id(conference, division, slot):
            return next(
                seat.franchise_id for seat in seats
                if seat.conference == conference
                and seat.division == division
                and seat.slot == slot
            )

        east = seat_id(AFC_SIDE, 'd1', 1)
        self.assertIn(Fixture(east, seat_id(AFC_SIDE, 'd4', 1), 'conference'), first)
        self.assertIn(Fixture(east, seat_id(AFC_SIDE, 'd4', 2), 'conference'), first)
        self.assertIn(Fixture(seat_id(AFC_SIDE, 'd4', 3), east, 'conference'), first)
        self.assertIn(Fixture(seat_id(AFC_SIDE, 'd4', 4), east, 'conference'), first)
        self.assertIn(Fixture(east, seat_id(NFC_SIDE, 'd2', 1), 'interconference'), first)
        self.assertIn(Fixture(east, seat_id(NFC_SIDE, 'd2', 2), 'interconference'), first)
        self.assertIn(Fixture(seat_id(NFC_SIDE, 'd2', 3), east, 'interconference'), first)
        self.assertIn(Fixture(east, seat_id(AFC_SIDE, 'd2', 1), 'same_place'), first)
        self.assertIn(Fixture(seat_id(AFC_SIDE, 'd3', 1), east, 'same_place'), first)
        self.assertIn(Fixture(seat_id(AFC_SIDE, 'd4', 1), east, 'conference'), flipped)
        self.assertIn(Fixture(east, seat_id(AFC_SIDE, 'd4', 3), 'conference'), flipped)
        self.assertIn(Fixture(east, seat_id(AFC_SIDE, 'd3', 1), 'same_place'), flipped)
        self.assertIn(Fixture(seat_id(AFC_SIDE, 'd2', 1), east, 'same_place'), flipped)
        afc_west = seat_id(AFC_SIDE, 'd4', 1)
        self.assertIn(
            Fixture(afc_west, seat_id(NFC_SIDE, 'd4', 1), 'interconference'),
            west,
        )
        self.assertIn(
            Fixture(afc_west, seat_id(NFC_SIDE, 'd4', 2), 'interconference'),
            west,
        )
        self.assertIn(
            Fixture(seat_id(NFC_SIDE, 'd4', 3), afc_west, 'interconference'),
            west,
        )
        self.assertEqual(
            [game for game in first if game.category == 'conference'],
            [game for game in again if game.category == 'conference'],
        )

    def test_opening_places_ignore_slug_order_and_input_order(self):
        slugs = ['d', 'c', 'b', 'a']

        def fake(_seed, coordinate):
            _label, conference, division, slug, index = coordinate.split(':')
            self.assertEqual(conference, AFC_SIDE)
            self.assertEqual(division, 'd1')
            self.assertNotIn('timeline', coordinate)
            base = {'a': 10, 'b': 1, 'c': 2, 'd': 1}[slug]
            return base if index == '0' else {'a': 10, 'b': 4, 'c': 2, 'd': 3}[slug]

        with patch('timelines.services.schedule_2002.hmac_int', side_effect=fake):
            forward = _rank_slugs(7, AFC_SIDE, 'd1', slugs)
            backward = _rank_slugs(7, AFC_SIDE, 'd1', list(reversed(slugs)))

        self.assertEqual(forward, backward)
        self.assertEqual(forward, ('c', 'd', 'b', 'a'))
        self.assertNotEqual(forward, tuple(sorted(slugs)))

    def test_years_outside_the_formula_are_refused(self):
        seats = _synthetic_seats()
        for year in (2001, 2021, True, '2002'):
            with self.assertRaises(TimelineScheduleRefused):
                planned_games(year, seats, _slot_places(seats))


class TimelineScheduleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        league = League(
            name='Schedule League',
            slug='schedule-2002-league',
            tier=1,
            active=True,
        )
        league.save()
        release = UniverseRelease(
            slug='schedule-2002',
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
        cls.profiles = profiles
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
        cls.pairing_id = pairing.pk
        cls.week_id = week.pk
        cls.standing_id = standing.pk
        cls.timeline = _timeline(release, 'opens in 2002', profiles, 2002)
        cls.slots = _slot_snapshot(release)

    def test_a_later_start_persists_the_opening_seed(self):
        before = _shared_snapshot(
            self.release, self.pairing_id, self.week_id, self.standing_id,
        )

        status = generate_timeline_schedule(
            timeline_id=self.timeline.pk,
            season_year=2002,
        )

        self.assertEqual(status, 'created')
        self._assert_full_2002_schedule(self.timeline)
        seeds = list(TimelineScheduleSeed.objects.filter(timeline=self.timeline))
        self.assertEqual(len(seeds), 32)
        self.assertTrue(all(seed.season_year == 2002 for seed in seeds))
        seats = _seats(self.release, 2002)
        self.assertEqual(
            {seed.franchise_id: seed.division_place for seed in seeds},
            opening_places(self.release.seed, seats),
        )
        for rows in _by_division(seeds).values():
            self.assertEqual(sorted(row.division_place for row in rows), [1, 2, 3, 4])
        expansion = UniverseFranchise.objects.get(
            release=self.release,
            placeholder_slug='expansion-2002-01',
        )
        self.assertFalse(FranchiseSeasonMembership.objects.filter(
            franchise=expansion,
            season_year=2001,
        ).exists())
        self.assertFalse(TimelinePriorFinish.objects.filter(franchise=expansion).exists())
        self.assertTrue(TimelineScheduleSeed.objects.filter(
            timeline=self.timeline,
            franchise=expansion,
        ).exists())
        self.assertEqual(TimelinePriorFinish.objects.count(), 0)
        self.assertEqual(TimelineSchedulingRank.objects.count(), 0)
        self.assertEqual(_slot_snapshot(self.release), self.slots)
        self.assertEqual(
            _shared_snapshot(self.release, self.pairing_id, self.week_id, self.standing_id),
            before,
        )
        self._assert_named_2002_sites(self.timeline)

    def test_a_timeline_that_includes_2001_uses_scheduling_ranks(self):
        for start_year in (1970, 2001):
            timeline = _played_timeline(
                self.release,
                f'starts {start_year}',
                self.profiles,
                start_year,
            )
            self.assertEqual(
                generate_timeline_schedule(timeline_id=timeline.pk, season_year=2002),
                'created',
            )
            self._assert_full_2002_schedule(timeline)
            self.assertEqual(
                TimelineScheduleSeed.objects.filter(timeline=timeline).count(),
                0,
            )
            expansion = UniverseFranchise.objects.get(
                release=self.release,
                placeholder_slug='expansion-2002-01',
            )
            self.assertFalse(TimelinePriorFinish.objects.filter(
                timeline=timeline,
                franchise=expansion,
            ).exists())
            self.assertTrue(TimelineSchedulingRank.objects.filter(
                timeline=timeline,
                franchise=expansion,
            ).exists())
            ranks = {
                row.franchise_id: row.scheduling_rank
                for row in TimelineSchedulingRank.objects.filter(timeline=timeline)
            }
            finishes = {
                row.franchise_id: row.division_place
                for row in TimelinePriorFinish.objects.filter(timeline=timeline)
            }
            self.assertEqual(len(finishes), 31)
            self.assertTrue(any(
                finishes[franchise_id] != ranks[franchise_id]
                for franchise_id in finishes
            ))
            same_place = {
                (game.home_id, game.away_id)
                for game in planned_games(2002, _seats(self.release, 2002), ranks)
                if game.category == 'same_place'
            }
            stored = set(ScheduleGame.objects.filter(
                timeline=timeline,
                season_year=2002,
                category=ScheduleGame.Category.SAME_PLACE,
            ).values_list('home_id', 'away_id'))
            self.assertEqual(stored, same_place)
            season = TimelineSeason.objects.get(timeline=timeline, season_year=2001)
            self.assertTrue(season.regular_season_simulated)
            self._assert_named_2002_sites(timeline)
        self.assertEqual(_slot_snapshot(self.release), self.slots)

    def test_an_unfinished_2001_season_blocks_the_schedule(self):
        timeline = _timeline(
            self.release,
            '2001 still open',
            self.profiles,
            2001,
            simulated_2001=False,
        )

        with self.assertRaisesMessage(TimelineScheduleRefused, 'not complete'):
            generate_timeline_schedule(timeline_id=timeline.pk, season_year=2002)

        season = TimelineSeason.objects.get(timeline=timeline, season_year=2001)
        self.assertFalse(season.regular_season_simulated)
        self.assertEqual(ScheduleGame.objects.count(), 0)
        self.assertEqual(TimelineScheduleSeed.objects.count(), 0)

    def test_missing_or_partial_transition_rows_block_every_fixture(self):
        missing = _timeline(
            self.release, 'finished without rows', self.profiles, 2001, True,
        )
        with self.assertRaisesMessage(TimelineScheduleRefused, 'prior-finish'):
            generate_timeline_schedule(timeline_id=missing.pk, season_year=2002)
        self.assertEqual(ScheduleGame.objects.count(), 0)

        partial = _played_timeline(self.release, 'partial finish', self.profiles, 2001)
        row = TimelinePriorFinish.objects.filter(timeline=partial).order_by('pk').first()
        row.delete()
        with self.assertRaisesMessage(TimelineScheduleRefused, 'prior-finish'):
            generate_timeline_schedule(timeline_id=partial.pk, season_year=2002)
        self.assertFalse(TimelinePriorFinish.objects.filter(pk=row.pk).exists())
        self.assertEqual(ScheduleGame.objects.filter(timeline=partial).count(), 0)

        short_ranks = _played_timeline(self.release, 'partial rank', self.profiles, 2001)
        rank = TimelineSchedulingRank.objects.filter(timeline=short_ranks).order_by('pk').first()
        rank.delete()
        with self.assertRaisesMessage(TimelineScheduleRefused, 'scheduling ranks'):
            generate_timeline_schedule(timeline_id=short_ranks.pk, season_year=2002)
        self.assertEqual(
            TimelineSchedulingRank.objects.filter(timeline=short_ranks).count(),
            31,
        )
        self.assertEqual(ScheduleGame.objects.filter(timeline=short_ranks).count(), 0)

        tampered = _played_timeline(self.release, 'tampered draw', self.profiles, 2001)
        draw = TimelineSchedulingDraw.objects.filter(timeline=tampered).order_by('pk').first()
        self.assertIsNotNone(draw)
        TimelineSchedulingDraw.objects.filter(pk=draw.pk).update(chosen_slug='tampered')
        with self.assertRaisesMessage(TimelineScheduleRefused, 'do not match'):
            generate_timeline_schedule(timeline_id=tampered.pk, season_year=2002)
        draw.refresh_from_db()
        self.assertEqual(draw.chosen_slug, 'tampered')
        self.assertEqual(ScheduleGame.objects.filter(timeline=tampered).count(), 0)
        self.assertEqual(TimelineScheduleSeed.objects.count(), 0)

    def test_exact_rerun_writes_nothing(self):
        generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2002)
        played = _played_timeline(self.release, 'rerun ranks', self.profiles, 2001)
        generate_timeline_schedule(timeline_id=played.pk, season_year=2002)

        for timeline in (self.timeline, played):
            before = list(ScheduleGame.objects.filter(timeline=timeline).values_list(
                'pk', 'home_id', 'away_id', 'category',
            ))
            status, writes = _writes_during(lambda timeline=timeline: generate_timeline_schedule(
                timeline_id=timeline.pk,
                season_year=2002,
            ))
            self.assertEqual(status, 'unchanged')
            self.assertEqual(writes, [])
            self.assertEqual(list(ScheduleGame.objects.filter(timeline=timeline).values_list(
                'pk', 'home_id', 'away_id', 'category',
            )), before)

    def test_partial_and_mismatched_fixtures_stay_in_place(self):
        home = UniverseFranchise.objects.get(
            release=self.release, placeholder_slug='founding-01',
        )
        away = UniverseFranchise.objects.get(
            release=self.release, placeholder_slug='founding-02',
        )
        ScheduleGame(
            timeline=self.timeline,
            season_year=2002,
            home=home,
            away=away,
            kind=ScheduleGame.Kind.REGULAR,
            category=ScheduleGame.Category.DIVISION,
        ).save()
        with self.assertRaisesMessage(TimelineScheduleRefused, 'Stored fixtures'):
            generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2002)
        self.assertEqual(ScheduleGame.objects.filter(timeline=self.timeline).count(), 1)
        self.assertEqual(TimelineScheduleSeed.objects.filter(timeline=self.timeline).count(), 0)

        generate_timeline_schedule(timeline_id=_timeline(
            self.release, 'full then changed', self.profiles, 2002,
        ).pk, season_year=2002)
        changed = Timeline.objects.get(name='full then changed')
        game = ScheduleGame.objects.filter(
            timeline=changed,
            category=ScheduleGame.Category.DIVISION,
        ).order_by('pk').first()
        ScheduleGame.objects.filter(pk=game.pk).update(
            category=ScheduleGame.Category.SAME_PLACE,
        )
        with self.assertRaisesMessage(TimelineScheduleRefused, 'Stored fixtures'):
            generate_timeline_schedule(timeline_id=changed.pk, season_year=2002)
        game.refresh_from_db()
        self.assertEqual(game.category, ScheduleGame.Category.SAME_PLACE)
        self.assertEqual(ScheduleGame.objects.filter(timeline=changed).count(), 256)

    def test_a_wrong_opening_seed_blocks_the_season(self):
        franchise = UniverseFranchise.objects.get(
            release=self.release,
            placeholder_slug='founding-01',
        )
        membership = FranchiseSeasonMembership.objects.get(
            franchise=franchise,
            season_year=2002,
        )
        TimelineScheduleSeed(
            timeline=self.timeline,
            franchise=franchise,
            season_year=2002,
            division_place=1,
            conference_slug=membership.conference_slug,
            division_slug=membership.division_slug,
        ).save()
        with self.assertRaisesMessage(TimelineScheduleRefused, 'opening seed'):
            generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2002)
        self.assertEqual(ScheduleGame.objects.count(), 0)
        self.assertEqual(TimelineScheduleSeed.objects.count(), 1)

        played = _played_timeline(self.release, 'no seed allowed', self.profiles, 2001)
        with self.assertRaises(ValidationError):
            TimelineScheduleSeed(
                timeline=played,
                franchise=franchise,
                season_year=2002,
                division_place=1,
                conference_slug=membership.conference_slug,
                division_slug=membership.division_slug,
            ).save()

    def test_insert_failure_rolls_back_the_seed_and_the_games(self):
        with patch(
            'timelines.services.schedule_2002.ScheduleGame.objects.bulk_create',
            side_effect=IntegrityError('fail'),
        ):
            with self.assertRaisesMessage(TimelineScheduleRefused, 'insert failed'):
                generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2002)
        self.assertEqual(ScheduleGame.objects.count(), 0)
        self.assertEqual(TimelineScheduleSeed.objects.count(), 0)

        with patch(
            'timelines.services.schedule_2002.TimelineScheduleSeed.objects.bulk_create',
            side_effect=IntegrityError('fail'),
        ):
            with self.assertRaisesMessage(TimelineScheduleRefused, 'insert failed'):
                generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2002)
        self.assertEqual(ScheduleGame.objects.count(), 0)
        self.assertEqual(TimelineScheduleSeed.objects.count(), 0)

    def test_two_opening_timelines_store_the_same_seed(self):
        other = _timeline(self.release, 'other opening', self.profiles, 2002)
        generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2002)
        generate_timeline_schedule(timeline_id=other.pk, season_year=2002)
        first = _seed_map(self.timeline)
        second = _seed_map(other)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 32)
        self.assertTrue(
            set(TimelineScheduleSeed.objects.filter(timeline=self.timeline).values_list('pk', flat=True))
            .isdisjoint(TimelineScheduleSeed.objects.filter(timeline=other).values_list('pk', flat=True))
        )

    def test_later_seasons_are_refused_without_writing(self):
        timeline = _timeline(self.release, 'later seasons', self.profiles, 2002)
        _season(timeline, self.profiles, 2003)
        _season(timeline, self.profiles, 2020)
        with self.assertRaisesMessage(
            TimelineScheduleRefused,
            'The 2002 regular season is not complete.',
        ):
            generate_timeline_schedule(timeline_id=timeline.pk, season_year=2003)
        with self.assertRaisesMessage(TimelineScheduleRefused, LATER_SEASON):
            generate_timeline_schedule(timeline_id=timeline.pk, season_year=2020)
        with self.assertRaisesMessage(TimelineScheduleRefused, 'outside 2002'):
            generate_timeline_schedule(timeline_id=timeline.pk, season_year=2021)
        self.assertEqual(ScheduleGame.objects.count(), 0)
        self.assertEqual(TimelineScheduleSeed.objects.count(), 0)

    def test_a_simulated_or_inactive_timeline_is_refused(self):
        season = TimelineSeason.objects.get(timeline=self.timeline, season_year=2002)
        season.setup_complete = True
        season.directives_locked = True
        season.save()
        TimelineSeason.objects.filter(pk=season.pk).update(regular_season_simulated=True)
        with self.assertRaisesMessage(TimelineScheduleRefused, 'simulated'):
            generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2002)
        self.assertEqual(ScheduleGame.objects.count(), 0)
        Timeline.objects.filter(pk=self.timeline.pk).update(
            status=Timeline.Status.ARCHIVED,
            completed_at=timezone.now(),
        )
        with self.assertRaisesMessage(TimelineScheduleRefused, 'not active'):
            generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2002)
        with self.assertRaisesMessage(TimelineScheduleRefused, 'missing'):
            generate_timeline_schedule(timeline_id=0, season_year=2002)

    def _assert_full_2002_schedule(self, timeline):
        games = list(ScheduleGame.objects.filter(timeline=timeline, season_year=2002))
        self.assertEqual(len(games), 256)
        self.assertEqual(Counter(game.category for game in games), {
            'division': 96,
            'conference': 64,
            'interconference': 64,
            'same_place': 32,
        })
        clubs = set()
        for game in games:
            clubs.add(game.home_id)
            clubs.add(game.away_id)
        self.assertEqual(len(clubs), 32)
        for franchise_id in clubs:
            played = [
                game for game in games
                if franchise_id in (game.home_id, game.away_id)
            ]
            home = [game for game in played if game.home_id == franchise_id]
            self.assertEqual(len(played), 16)
            self.assertEqual(len(home), 8)

    def _assert_named_2002_sites(self, timeline):
        founding = UniverseFranchise.objects.get(
            release=self.release, placeholder_slug='founding-01',
        )
        ninth = UniverseFranchise.objects.get(
            release=self.release, placeholder_slug='founding-09',
        )
        eleventh = UniverseFranchise.objects.get(
            release=self.release, placeholder_slug='founding-11',
        )
        seventeenth = UniverseFranchise.objects.get(
            release=self.release, placeholder_slug='founding-17',
        )
        nineteenth = UniverseFranchise.objects.get(
            release=self.release, placeholder_slug='founding-19',
        )
        self.assertTrue(ScheduleGame.objects.filter(
            timeline=timeline, season_year=2002, home=founding, away=ninth,
            category=ScheduleGame.Category.CONFERENCE,
        ).exists())
        self.assertTrue(ScheduleGame.objects.filter(
            timeline=timeline, season_year=2002, home=founding, away=eleventh,
            category=ScheduleGame.Category.CONFERENCE,
        ).exists())
        self.assertTrue(ScheduleGame.objects.filter(
            timeline=timeline, season_year=2002, home=founding, away=seventeenth,
            category=ScheduleGame.Category.INTERCONFERENCE,
        ).exists())
        self.assertTrue(ScheduleGame.objects.filter(
            timeline=timeline, season_year=2002, home=nineteenth, away=founding,
            category=ScheduleGame.Category.INTERCONFERENCE,
        ).exists())


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
        _season(timeline, profiles, year, simulated=year == 2001 and simulated_2001)
    return timeline


def _season(timeline, profiles, year, simulated=False):
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
        setup_complete=simulated,
        directives_locked=simulated,
        regular_season_simulated=simulated,
    ).save()


def _played_timeline(release, name, profiles, start_year):
    timeline = _timeline(release, name, profiles, start_year, simulated_2001=True)
    store_prior_finishes(
        timeline_id=timeline.pk,
        finishes=_default_finishes(release),
    )
    derive_scheduling_ranks(timeline_id=timeline.pk)
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


def _seats(release, season_year):
    return [
        Seat(
            row.franchise_id,
            row.franchise.placeholder_slug,
            row.conference_slug,
            row.division_slug,
            row.slot_order,
        )
        for row in FranchiseSeasonMembership.objects.filter(
            franchise__release=release,
            season_year=season_year,
        ).select_related('franchise')
    ]


def _slot_snapshot(release):
    return tuple(sorted(
        FranchiseSeasonMembership.objects.filter(franchise__release=release).values_list(
            'franchise_id',
            'season_year',
            'conference_slug',
            'division_slug',
            'slot_order',
        )
    ))


def _shared_snapshot(release, pairing_id, week_id, standing_id):
    release.refresh_from_db()
    pairing = OpponentPairing.objects.get(pk=pairing_id)
    week = OpponentWeekAssignment.objects.get(pk=week_id)
    standing = HistoricalStanding.objects.get(pk=standing_id)
    return (
        release.catalog_fingerprint,
        release.validation_fingerprint,
        pairing.season_year,
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
    )


def _by_division(seeds):
    grouped = {}
    for seed in seeds:
        grouped.setdefault((seed.conference_slug, seed.division_slug), []).append(seed)
    return grouped


def _seed_map(timeline):
    return {
        row.franchise_id: row.division_place
        for row in TimelineScheduleSeed.objects.filter(timeline=timeline)
    }
