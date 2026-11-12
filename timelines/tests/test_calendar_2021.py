"""Weeks and byes for the stored 2021–2029 regular-season fixtures."""

from collections import Counter, defaultdict
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from leagues.models import League
from timelines.models import (
    ScheduleBye,
    ScheduleGame,
    ScheduleWeekAssignment,
    TimelineSeason,
    writing_calendar_rows,
)
from timelines.services.calendar_2021 import (
    BYE_COUNTS,
    DIFFER,
    DIVISION_ROUNDS,
    FIXTURE_MISMATCH,
    INSERT_FAILED,
    NO_FIXTURES,
    OUTSIDE_SPAN,
    UNPUBLISHED,
    CalendarGame,
    CalendarRefused,
    assign_regular_season_calendar,
    place_remaining_games,
    project_calendar,
)
from timelines.services.division_place_2002 import close_regular_season
from timelines.services.schedule_2002 import (
    AFC_SIDE,
    NFC_SIDE,
    POSITIONS,
    Seat,
    generate_timeline_schedule,
    planned_games,
)
from timelines.tests.test_schedule_2005 import (
    _copy_season_games,
    _kept,
    _lock,
    _prepare_ranked,
    _ranked_results,
    _season,
    _timeline,
    _writes_during,
)
from universe.generation.prospects import later_profile_values
from universe.generation.schedule import generate_schedule_release
from universe.rules import EraRuleProfile
from universe.models import (
    FranchiseSeasonMembership,
    HistoricalStanding,
    OpponentPairing,
    OpponentWeekAssignment,
    UniverseFranchise,
    UniverseRelease,
)


SOURCED_BYES = {
    2021: (0, 0, 0, 0, 0, 4, 6, 2, 4, 4, 2, 2, 4, 4, 0, 0, 0, 0),
    2022: (0, 0, 0, 0, 0, 4, 4, 2, 6, 4, 4, 0, 2, 6, 0, 0, 0, 0),
    2023: (0, 0, 0, 0, 4, 2, 6, 0, 4, 4, 4, 0, 6, 2, 0, 0, 0, 0),
    2024: (0, 0, 0, 0, 4, 4, 2, 0, 2, 4, 4, 6, 0, 6, 0, 0, 0, 0),
    2025: (0, 0, 0, 0, 4, 2, 2, 6, 4, 4, 2, 4, 0, 4, 0, 0, 0, 0),
    2026: (0, 0, 0, 0, 2, 4, 4, 4, 2, 4, 6, 0, 4, 2, 0, 0, 0, 0),
}
PROFILE_KEYS = {'y2002', 'y2004', 'y2014', 'y2021'}


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


def _expected_division_weeks(fixtures, seats, counts):
    """Division weeks from the sourced full weeks and the slot-pair rotation.

    ``fixtures`` are ``(key, home slug, away slug, category)``. The return
    game of each pair is three rounds later, so week 18 is the return of
    slot pairs 1–4 and 2–3.
    """
    groups = {}
    for seat in seats:
        groups.setdefault((seat.conference, seat.division), []).append(seat)
    ordered = []
    for conference in (AFC_SIDE, NFC_SIDE):
        for division in POSITIONS:
            ordered.append(tuple(sorted(
                groups[(conference, division)],
                key=lambda seat: seat.slot,
            )))
    full = [week for week, count in enumerate(counts, start=1) if count == 0]
    division_weeks = [week for week in full if week != 18][:5] + [18]
    by_pair = defaultdict(list)
    for key, home, away, category in fixtures:
        if category != 'division':
            continue
        by_pair[frozenset((home, away))].append((key, home, away))
    week_of = {}
    for group in ordered:
        slugs = [seat.slug for seat in group]
        for round_index, pairs in enumerate(DIVISION_ROUNDS):
            for left, right in pairs:
                matched = sorted(
                    by_pair[frozenset((slugs[left], slugs[right]))],
                    key=lambda item: (item[1], item[2], item[0]),
                )
                week_of[matched[0][0]] = division_weeks[round_index]
                week_of[matched[1][0]] = division_weeks[round_index + 3]
    return week_of


def _calendar_games(season_year, seats, places):
    by_id = {seat.franchise_id: seat for seat in seats}
    return tuple(
        CalendarGame(
            (game.home_id, game.away_id),
            by_id[game.home_id].slug,
            by_id[game.away_id].slug,
            game.category,
        )
        for game in planned_games(season_year, seats, places)
    )


class CalendarFormulaTests(SimpleTestCase):
    def test_every_published_season_matches_its_bye_calendar(self):
        self.assertEqual(BYE_COUNTS, SOURCED_BYES)
        seats = _synthetic_seats()
        places = {seat.franchise_id: seat.slot for seat in seats}
        for year, counts in SOURCED_BYES.items():
            self.assertEqual(sum(counts), 32)
            self.assertEqual(counts[0], 0)
            self.assertEqual(counts[17], 0)
            games = _calendar_games(year, seats, places)
            weeks, byes = project_calendar(year, games, seats)
            again, byes_again = project_calendar(year, reversed(games), reversed(seats))
            self.assertEqual(weeks, again)
            self.assertEqual(byes, byes_again)
            self._assert_calendar(games, weeks, byes, counts, seats)

    def test_unpublished_seasons_have_no_calendar(self):
        seats = _synthetic_seats()
        places = {seat.franchise_id: seat.slot for seat in seats}
        games = _calendar_games(2021, seats, places)
        for year in (2027, 2028, 2029):
            with self.assertRaisesMessage(CalendarRefused, UNPUBLISHED.format(year=year)):
                project_calendar(year, games, seats)
        for year in (2020, 2030, True, '2021'):
            with self.assertRaisesMessage(CalendarRefused, OUTSIDE_SPAN):
                project_calendar(year, games, seats)

    def test_preassigned_division_week_is_not_a_candidate(self):
        # Both clubs already play a division game in week 1. Weeks 3–18 are
        # closed, so the only week both are free is week 2. Ignoring the
        # division week leaves week 1 open as well, and the search then
        # either places the game there or fails to return week 2.
        closed = set(range(3, 19))
        taken = {'a': {1} | closed, 'b': {1} | closed}
        found = place_remaining_games(
            [CalendarGame('ab', 'a', 'b', 'conference')],
            taken,
        )
        self.assertEqual(found, {'ab': 2})

    def test_failed_candidate_is_undone_before_the_next_week(self):
        # Each club has three open weeks and three games. Placing b–e in
        # week 2 is legal for that pair and then fails; the search has to
        # restore both clubs' weeks and finish on the other candidate.
        closed = set(range(4, 19))
        clubs = 'abcdefgh'
        edges = (
            ('a', 'b'), ('a', 'c'), ('a', 'd'),
            ('b', 'e'), ('b', 'f'),
            ('c', 'e'), ('c', 'g'),
            ('d', 'f'), ('d', 'h'),
            ('e', 'g'), ('f', 'h'), ('g', 'h'),
        )
        found = place_remaining_games(
            [CalendarGame(home + away, home, away, 'conference') for home, away in edges],
            {club: set(closed) for club in clubs},
        )
        self.assertEqual(found, {
            'ab': 1, 'ac': 2, 'ad': 3,
            'be': 3, 'bf': 2,
            'ce': 1, 'cg': 3,
            'df': 1, 'dh': 2,
            'eg': 2, 'fh': 3, 'gh': 1,
        })
        self.assertNotEqual(found['be'], 2)

    def _assert_calendar(self, games, weeks, byes, counts, seats):
        self.assertEqual(set(weeks), {game.key for game in games})
        self.assertEqual(len(weeks), len(games))
        played = defaultdict(set)
        for game in games:
            week = weeks[game.key]
            for slug in (game.home, game.away):
                self.assertNotIn(week, played[slug])
                self.assertNotEqual(byes[slug], week)
                played[slug].add(week)
        self.assertEqual(len(byes), 32)
        self.assertEqual(set(byes.values()), {week for week, count in enumerate(counts, start=1) if count})
        self.assertEqual(Counter(byes.values()), {
            week: count for week, count in enumerate(counts, start=1) if count
        })
        for slug, used in played.items():
            self.assertEqual(len(used), 17)
            self.assertEqual(used, set(range(1, 19)) - {byes[slug]})
        per_week = Counter(weeks.values())
        for week, count in enumerate(counts, start=1):
            self.assertEqual(per_week[week], (32 - count) // 2)
        week_18 = [game for game in games if weeks[game.key] == 18]
        self.assertEqual(len(week_18), 16)
        self.assertTrue(all(game.category == 'division' for game in week_18))
        plan = _expected_division_weeks(
            ((game.key, game.home, game.away, game.category) for game in games),
            seats,
            counts,
        )
        for key, week in plan.items():
            self.assertEqual(weeks[key], week)
        division_weeks = defaultdict(set)
        for game in games:
            if game.category != 'division':
                continue
            division_weeks[game.home].add(weeks[game.key])
            division_weeks[game.away].add(weeks[game.key])
        for game in games:
            if game.category == 'division':
                continue
            self.assertNotIn(weeks[game.key], division_weeks[game.home])
            self.assertNotIn(weeks[game.key], division_weeks[game.away])


class CalendarStorageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        league = League(
            name='Calendar 2021 League',
            slug='calendar-2021-league',
            tier=1,
            active=True,
        )
        league.save()
        release = UniverseRelease(
            slug='calendar-2021',
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
        OpponentWeekAssignment(release=release, pairing=pairing, week=1).save()
        HistoricalStanding(
            franchise=home,
            season_year=1969,
            wins=1,
            losses=0,
            ties=0,
        ).save()
        release.catalog_fingerprint = 'catalog-fixed'
        release.validation_fingerprint = 'validation-fixed'
        release.status = UniverseRelease.Status.PUBLISHED
        release.published_at = timezone.now()
        release.save()
        cls.release = release
        cls.profiles = profiles
        timeline = _prepare_ranked(release, 'opens in 2002', profiles)
        _season(timeline, profiles, 2020)
        _copy_season_games(timeline, 2004, 2020)
        _lock(timeline, 2020)
        close_regular_season(
            timeline_id=timeline.pk,
            results=_ranked_results(timeline, 2020),
            season_year=2020,
        )
        for year in range(2021, 2030):
            _season(timeline, profiles, year)
            generate_timeline_schedule(timeline_id=timeline.pk, season_year=year)
            if year < 2029:
                _lock(timeline, year)
                close_regular_season(
                    timeline_id=timeline.pk,
                    results=_ranked_results(timeline, year),
                    season_year=year,
                )
        cls.timeline = timeline

    def test_each_supported_season_assigns_weeks_and_byes(self):
        before = _kept(self.release, self.timeline)
        earlier = {
            year: self._game_rows(year)
            for year in (2004, 2020, 2021, 2027, 2029)
        }
        flags = {
            year: TimelineSeason.objects.get(
                timeline=self.timeline,
                season_year=year,
            ).regular_season_simulated
            for year in range(2021, 2030)
        }
        for year in range(2021, 2027):
            fixtures = self._game_rows(year)
            outcome = assign_regular_season_calendar(
                timeline_id=self.timeline.pk,
                season_year=year,
            )
            self.assertEqual(outcome, 'created')
            self.assertEqual(self._game_rows(year), fixtures)
            self._assert_stored(year)
            again, writes = _writes_during(lambda year=year: assign_regular_season_calendar(
                timeline_id=self.timeline.pk,
                season_year=year,
            ))
            self.assertEqual(again, 'unchanged')
            self.assertEqual(writes, [])
        for year, rows in earlier.items():
            self.assertEqual(self._game_rows(year), rows)
        for year, flag in flags.items():
            self.assertEqual(
                TimelineSeason.objects.get(
                    timeline=self.timeline,
                    season_year=year,
                ).regular_season_simulated,
                flag,
            )
        self.assertEqual(_kept(self.release, self.timeline), before)

    def test_partial_and_mismatched_rows_are_left_in_place(self):
        for year in range(2021, 2027):
            game = ScheduleGame.objects.filter(
                timeline=self.timeline,
                season_year=year,
            ).order_by('pk').first()
            ScheduleWeekAssignment.objects.bulk_create([
                ScheduleWeekAssignment(
                    timeline=self.timeline,
                    game=game,
                    season_year=year,
                    week=1,
                ),
            ])
            with self.assertRaisesMessage(CalendarRefused, DIFFER):
                assign_regular_season_calendar(
                    timeline_id=self.timeline.pk,
                    season_year=year,
                )
            self.assertEqual(self._week_count(year), 1)
            self.assertEqual(self._bye_count(year), 0)
            self.assertEqual(
                ScheduleWeekAssignment.objects.get(game=game).week,
                1,
            )
            ScheduleWeekAssignment.objects.filter(
                timeline=self.timeline,
                season_year=year,
            ).delete()
            member = FranchiseSeasonMembership.objects.filter(
                franchise__release=self.release,
                season_year=year,
            ).order_by('franchise_id').first()
            ScheduleBye.objects.bulk_create([
                ScheduleBye(
                    timeline=self.timeline,
                    franchise_id=member.franchise_id,
                    season_year=year,
                    week=6,
                ),
            ])
            with self.assertRaisesMessage(CalendarRefused, DIFFER):
                assign_regular_season_calendar(
                    timeline_id=self.timeline.pk,
                    season_year=year,
                )
            self.assertEqual(self._week_count(year), 0)
            self.assertEqual(self._bye_count(year), 1)
            self.assertEqual(
                ScheduleBye.objects.get(
                    timeline=self.timeline,
                    season_year=year,
                ).week,
                6,
            )
            ScheduleBye.objects.filter(
                timeline=self.timeline,
                season_year=year,
            ).delete()

            assign_regular_season_calendar(
                timeline_id=self.timeline.pk,
                season_year=year,
            )
            stored = ScheduleWeekAssignment.objects.filter(
                timeline=self.timeline,
                season_year=year,
            ).order_by('pk').first()
            wrong = stored.week % 18 + 1
            ScheduleWeekAssignment.objects.filter(pk=stored.pk).update(week=wrong)
            with self.assertRaisesMessage(CalendarRefused, DIFFER):
                assign_regular_season_calendar(
                    timeline_id=self.timeline.pk,
                    season_year=year,
                )
            self.assertEqual(ScheduleWeekAssignment.objects.get(pk=stored.pk).week, wrong)
            self.assertEqual(self._week_count(year), 272)
            self.assertEqual(self._bye_count(year), 32)
            ScheduleWeekAssignment.objects.filter(
                timeline=self.timeline,
                season_year=year,
            ).delete()
            ScheduleBye.objects.filter(
                timeline=self.timeline,
                season_year=year,
            ).delete()

    def test_missing_and_mismatched_fixtures_store_nothing(self):
        for year in range(2021, 2027):
            row = ScheduleGame.objects.filter(
                timeline=self.timeline,
                season_year=year,
            ).order_by('pk').first()
            original = row.category
            changed = 'conference' if original != 'conference' else 'division'
            ScheduleGame.objects.filter(pk=row.pk).update(category=changed)
            with self.assertRaisesMessage(CalendarRefused, FIXTURE_MISMATCH):
                assign_regular_season_calendar(
                    timeline_id=self.timeline.pk,
                    season_year=year,
                )
            self.assertEqual(self._week_count(year), 0)
            self.assertEqual(self._bye_count(year), 0)
            ScheduleGame.objects.filter(pk=row.pk).update(category=original)
            other = _timeline(self.release, f'missing {year}', self.profiles, 2002)
            prior = year - 1
            _season(other, self.profiles, prior)
            ScheduleGame.objects.bulk_create([
                ScheduleGame(
                    timeline=other,
                    season_year=prior,
                    home_id=game.home_id,
                    away_id=game.away_id,
                    kind=game.kind,
                    category=game.category,
                )
                for game in ScheduleGame.objects.filter(
                    timeline=self.timeline,
                    season_year=prior,
                )
            ])
            _lock(other, prior)
            close_regular_season(
                timeline_id=other.pk,
                results=_ranked_results(other, prior),
                season_year=prior,
            )
            _season(other, self.profiles, year)
            with self.assertRaisesMessage(CalendarRefused, NO_FIXTURES):
                assign_regular_season_calendar(
                    timeline_id=other.pk,
                    season_year=year,
                )
            _copy_season_games(other, prior, year)
            with self.assertRaisesMessage(CalendarRefused, FIXTURE_MISMATCH):
                assign_regular_season_calendar(
                    timeline_id=other.pk,
                    season_year=year,
                )
            self.assertEqual(
                ScheduleWeekAssignment.objects.filter(timeline=other).count(),
                0,
            )
            self.assertEqual(ScheduleBye.objects.filter(timeline=other).count(), 0)

    def test_failed_insert_rolls_back_the_season(self):
        for year in range(2021, 2027):
            for target in (
                'timelines.services.calendar_2021.ScheduleWeekAssignment.objects.bulk_create',
                'timelines.services.calendar_2021.ScheduleBye.objects.bulk_create',
            ):
                with patch(target, side_effect=IntegrityError('forced')):
                    with self.assertRaisesMessage(CalendarRefused, INSERT_FAILED):
                        assign_regular_season_calendar(
                            timeline_id=self.timeline.pk,
                            season_year=year,
                        )
                self.assertEqual(self._week_count(year), 0)
                self.assertEqual(self._bye_count(year), 0)
            self.assertEqual(
                assign_regular_season_calendar(
                    timeline_id=self.timeline.pk,
                    season_year=year,
                ),
                'created',
            )
            self._assert_stored(year)

    def test_unpublished_and_outside_seasons_store_nothing(self):
        for year in (2027, 2028, 2029):
            self.assertEqual(self._game_count(year), 272)
            outcome, writes = _writes_during(
                lambda year=year: self._refused(year, UNPUBLISHED.format(year=year)),
            )
            self.assertIsNone(outcome)
            self.assertEqual(writes, [])
            self.assertEqual(self._week_count(year), 0)
            self.assertEqual(self._bye_count(year), 0)
        for year in (2020, 2030):
            with self.assertRaisesMessage(CalendarRefused, OUTSIDE_SPAN):
                assign_regular_season_calendar(
                    timeline_id=self.timeline.pk,
                    season_year=year,
                )
            self.assertEqual(self._week_count(year), 0)

    def test_one_row_cannot_be_saved_alone(self):
        game = ScheduleGame.objects.filter(
            timeline=self.timeline,
            season_year=2021,
        ).first()
        member = FranchiseSeasonMembership.objects.filter(
            franchise__release=self.release,
            season_year=2021,
        ).first()
        week = ScheduleWeekAssignment(
            timeline=self.timeline,
            game=game,
            season_year=2021,
            week=1,
        )
        with self.assertRaisesMessage(
            ValidationError,
            'A calendar week is stored with the rest of its season.',
        ):
            week.save()
        bye = ScheduleBye(
            timeline=self.timeline,
            franchise_id=member.franchise_id,
            season_year=2021,
            week=6,
        )
        with self.assertRaisesMessage(
            ValidationError,
            'A bye is stored with the rest of its season.',
        ):
            bye.save()
        self.assertEqual(self._week_count(2021), 0)
        self.assertEqual(self._bye_count(2021), 0)
        with writing_calendar_rows():
            week.full_clean()
            bye.full_clean()

    def _refused(self, year, message):
        with self.assertRaisesMessage(CalendarRefused, message):
            assign_regular_season_calendar(
                timeline_id=self.timeline.pk,
                season_year=year,
            )

    def _assert_stored(self, year):
        counts = BYE_COUNTS[year]
        weeks = list(ScheduleWeekAssignment.objects.filter(
            timeline=self.timeline,
            season_year=year,
        ).select_related('game'))
        byes = list(ScheduleBye.objects.filter(
            timeline=self.timeline,
            season_year=year,
        ))
        self.assertEqual(len(weeks), 272)
        self.assertEqual(len(byes), 32)
        self.assertEqual({row.game_id for row in weeks}, set(
            ScheduleGame.objects.filter(
                timeline=self.timeline,
                season_year=year,
            ).values_list('pk', flat=True)
        ))
        played = defaultdict(set)
        per_week = Counter()
        for row in weeks:
            per_week[row.week] += 1
            for franchise_id in (row.game.home_id, row.game.away_id):
                self.assertNotIn(row.week, played[franchise_id])
                played[franchise_id].add(row.week)
        bye_of = {row.franchise_id: row.week for row in byes}
        self.assertEqual(set(bye_of), set(played))
        self.assertEqual(Counter(bye_of.values()), {
            week: count for week, count in enumerate(counts, start=1) if count
        })
        for franchise_id, used in played.items():
            self.assertEqual(used, set(range(1, 19)) - {bye_of[franchise_id]})
        for week, count in enumerate(counts, start=1):
            self.assertEqual(per_week[week], (32 - count) // 2)
        week_18 = [row.game for row in weeks if row.week == 18]
        self.assertEqual(len(week_18), 16)
        self.assertTrue(all(game.category == 'division' for game in week_18))
        seats = [
            Seat(
                row.franchise_id,
                row.franchise.placeholder_slug,
                row.conference_slug,
                row.division_slug,
                row.slot_order,
            )
            for row in FranchiseSeasonMembership.objects.filter(
                franchise__release=self.release,
                season_year=year,
            ).select_related('franchise')
        ]
        slug_of = {seat.franchise_id: seat.slug for seat in seats}
        stored_weeks = {row.game_id: row.week for row in weeks}
        plan = _expected_division_weeks(
            (
                (
                    row.game_id,
                    slug_of[row.game.home_id],
                    slug_of[row.game.away_id],
                    row.game.category,
                )
                for row in weeks
            ),
            seats,
            counts,
        )
        for key, week in plan.items():
            self.assertEqual(stored_weeks[key], week)

    def _game_rows(self, year):
        return tuple(ScheduleGame.objects.filter(
            timeline=self.timeline,
            season_year=year,
        ).order_by('pk').values_list('pk', 'home_id', 'away_id', 'category', 'kind'))

    def _game_count(self, year):
        return ScheduleGame.objects.filter(
            timeline=self.timeline,
            season_year=year,
        ).count()

    def _week_count(self, year):
        return ScheduleWeekAssignment.objects.filter(
            timeline=self.timeline,
            season_year=year,
        ).count()

    def _bye_count(self, year):
        return ScheduleBye.objects.filter(
            timeline=self.timeline,
            season_year=year,
        ).count()
