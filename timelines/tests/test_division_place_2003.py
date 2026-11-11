import hashlib
import hmac
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.query import QuerySet
from django.test import TestCase
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
    TimelineSchedulingRank,
    TimelineSeason,
    writing_division_rows,
)
from timelines.services.division_place_2002 import (
    DRAW,
    HEAD_TO_HEAD,
    DivisionPlaceRefused,
    Result,
    close_2002_regular_season,
    close_2003_regular_season,
    division_draw_label,
    verify_played_2002_places,
    verify_played_2003_places,
)
from timelines.services.schedule_2002 import generate_timeline_schedule
from timelines.tests.test_division_place_2002 import (
    DivisionProcedureTests,
    _by_key,
    _club,
    _game,
    _lock_directives,
    _rank,
    _tied_results,
    _timeline,
    _writes_during,
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
from universe.release_guard import holding_release_lock
from universe.rules import EraRuleProfile


PROFILE_KEYS = {'y1999', 'y2002'}


class SharedProcedureTests(DivisionProcedureTests):
    """The 2003 close uses the same rank_places steps as 2002."""

    def test_draw_label_keeps_the_2002_coordinate_and_names_2003(self):
        label = division_draw_label(
            'conference-1',
            'd1',
            ('founding-01', 'founding-02'),
            2,
        )
        self.assertEqual(
            label,
            'division-place:2002:conference-1:d1:founding-01,founding-02:2',
        )
        later = division_draw_label(
            'conference-1',
            'd1',
            ('founding-01', 'founding-02'),
            2,
            2003,
        )
        self.assertEqual(
            later,
            'division-place:2003:conference-1:d1:founding-01,founding-02:2',
        )
        self.assertNotIn('timeline', later.split(':'))

    def test_four_clubs_reduced_to_three_continue_at_the_next_step(self):
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
        # Games among a, b, and c alone make c the head-to-head leader.
        # Restarting when four clubs shrink to three would place c first.
        subgroup, _subgroup_draws = _rank(
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
        self.assertEqual(_by_key(subgroup)['c'].place, 1)
        self.assertNotEqual(found['c'].place, 1)


class DivisionPlace2003Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        league = League(
            name='Division Place 2003 League',
            slug='division-place-2003-league',
            tier=1,
            active=True,
        )
        league.save()
        release = UniverseRelease(
            slug='division-place-2003',
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
        cls.timeline = _timeline(release, 'opens in 2002', profiles, 2002)
        generate_timeline_schedule(timeline_id=cls.timeline.pk, season_year=2002)
        _lock_directives(cls.timeline)
        close_2002_regular_season(
            timeline_id=cls.timeline.pk,
            results=_tied_results(cls.timeline),
        )
        _season(cls.timeline, profiles, 2003)
        generate_timeline_schedule(timeline_id=cls.timeline.pk, season_year=2003)

    def test_results_store_256_games_and_32_places(self):
        before = _kept(self.release, self.timeline)
        games_before = _game_rows(self.timeline, 2003)
        _lock_2003(self.timeline)
        outcome = close_2003_regular_season(
            timeline_id=self.timeline.pk,
            results=_season_results(self.timeline, 2003),
        )
        self.assertEqual(outcome, 'created')
        season = TimelineSeason.objects.get(timeline=self.timeline, season_year=2003)
        self.assertTrue(season.regular_season_simulated)
        self.assertEqual(
            ScheduleGameResult.objects.filter(
                timeline=self.timeline,
                season_year=2003,
            ).count(),
            256,
        )
        self.assertEqual(
            TimelineDivisionPlace.objects.filter(
                timeline=self.timeline,
                season_year=2003,
            ).count(),
            32,
        )
        self.assertEqual(
            TimelineDivisionDraw.objects.filter(
                timeline=self.timeline,
                season_year=2003,
            ).count(),
            24,
        )
        for conference, division in (
            FranchiseSeasonMembership.objects.filter(
                franchise__release=self.release,
                season_year=2003,
            ).values_list('conference_slug', 'division_slug').distinct()
        ):
            places = list(TimelineDivisionPlace.objects.filter(
                timeline=self.timeline,
                season_year=2003,
                conference_slug=conference,
                division_slug=division,
            ))
            self.assertEqual(sorted(place.division_place for place in places), [1, 2, 3, 4])
            for place in places:
                membership = FranchiseSeasonMembership.objects.get(
                    franchise_id=place.franchise_id,
                    season_year=2003,
                )
                self.assertEqual(place.conference_slug, membership.conference_slug)
                self.assertEqual(place.division_slug, membership.division_slug)
                if place.division_place == 4:
                    self.assertEqual(place.resolved_by, 'sole_remaining')
                    self.assertIsNone(place.draw_id)
                else:
                    self.assertEqual(place.resolved_by, DRAW)
                    self.assertEqual(place.draw.season_year, 2003)
                    self.assertEqual(place.draw.chosen_slug, place.franchise.placeholder_slug)
        self.assertEqual(_game_rows(self.timeline, 2003), games_before)
        self.assertEqual(_kept(self.release, self.timeline), before)

    def test_draws_name_2003_and_match_across_timelines(self):
        other = self._played_timeline('same 2003 inputs')
        for timeline in (self.timeline, other):
            _lock_2003(timeline)
            close_2003_regular_season(
                timeline_id=timeline.pk,
                results=_season_results(timeline, 2003),
            )
        left = _draw_rows(self.timeline, 2003)
        right = _draw_rows(other, 2003)
        self.assertEqual(left, right)
        self.assertEqual(_place_rows(self.timeline, 2003), _place_rows(other, 2003))
        self.assertTrue(left)
        for row in TimelineDivisionDraw.objects.filter(
            timeline=self.timeline,
            season_year=2003,
        ):
            label = division_draw_label(
                row.conference_slug,
                row.division_slug,
                tuple(row.candidates.split(',')),
                row.draw_index,
                2003,
            )
            self.assertTrue(row.coordinate.startswith(f'{label}:'))
            self.assertTrue(row.coordinate[len(label) + 1:].isdigit())
            self.assertNotIn(f'timeline-{self.timeline.pk}', row.coordinate)
            self.assertEqual(row.digest, _hex_digest(self.release.seed, row.coordinate))
            self.assertEqual(row.outcome, f'{row.resolved_place.division_place}:{row.chosen_slug}')
        for row in TimelineDivisionDraw.objects.filter(
            timeline=self.timeline,
            season_year=2002,
        ):
            self.assertTrue(row.coordinate.startswith('division-place:2002:'))
            self.assertNotIn(':2003:', row.coordinate)

    def test_different_results_place_different_clubs(self):
        games_before = _game_rows(self.timeline, 2003)
        identity = _franchise_identity(self.release)
        _lock_2003(self.timeline)
        close_2003_regular_season(
            timeline_id=self.timeline.pk,
            results=_season_results(self.timeline, 2003),
        )
        other = self._played_timeline('ranked 2003')
        _lock_2003(other)
        close_2003_regular_season(
            timeline_id=other.pk,
            results=_ranked_results(other, 2003),
        )
        tied = _place_map(self.timeline, 2003)
        ranked = _place_map(other, 2003)
        self.assertNotEqual(tied, ranked)
        self.assertEqual(set(tied), set(ranked))
        memberships = {
            row.franchise_id: row.slot_order
            for row in FranchiseSeasonMembership.objects.filter(
                franchise__release=self.release,
                season_year=2003,
            )
        }
        self.assertEqual(ranked, memberships)
        self.assertEqual(_franchise_identity(self.release), identity)
        self.assertEqual(_game_rows(self.timeline, 2003), games_before)
        self.assertEqual(
            ScheduleGame.objects.filter(timeline=self.timeline, season_year=2003).count(),
            ScheduleGame.objects.filter(timeline=other, season_year=2003).count(),
        )

    def test_ordinary_save_refuses_the_2003_completion_flag(self):
        season = TimelineSeason.objects.get(timeline=self.timeline, season_year=2003)
        season.setup_complete = True
        season.directives_locked = True
        season.save()
        season.regular_season_simulated = True
        with self.assertRaisesMessage(
            ValidationError,
            'The 2003 regular season is marked complete only by its close.',
        ):
            season.save()
        season.refresh_from_db()
        self.assertFalse(season.regular_season_simulated)
        _lock_2003(self.timeline)
        close_2003_regular_season(
            timeline_id=self.timeline.pk,
            results=_season_results(self.timeline, 2003),
        )
        season.refresh_from_db()
        self.assertTrue(season.regular_season_simulated)

    def test_exact_rerun_writes_nothing(self):
        _lock_2003(self.timeline)
        results = _season_results(self.timeline, 2003)
        close_2003_regular_season(timeline_id=self.timeline.pk, results=results)
        outcome, writes = _writes_during(lambda: close_2003_regular_season(
            timeline_id=self.timeline.pk,
            results=results,
        ))
        self.assertEqual(outcome, 'unchanged')
        self.assertEqual(writes, [])

    def test_partial_and_altered_rows_stay_in_place(self):
        game = ScheduleGame.objects.filter(
            timeline=self.timeline,
            season_year=2003,
        ).order_by('pk').first()
        with writing_division_rows():
            row = ScheduleGameResult(
                timeline=self.timeline,
                game=game,
                season_year=2003,
                home_score=3,
                away_score=0,
                home_touchdowns=0,
                away_touchdowns=0,
                source_key='partial',
            )
            row.full_clean()
            ScheduleGameResult.objects.bulk_create([row])
        _lock_2003(self.timeline)
        with self.assertRaises(DivisionPlaceRefused):
            close_2003_regular_season(
                timeline_id=self.timeline.pk,
                results=_season_results(self.timeline, 2003),
            )
        self.assertEqual(ScheduleGameResult.objects.get(pk=row.pk).home_score, 3)
        self.assertEqual(_count(self.timeline, 2003), (1, 0, 0))
        self.assertFalse(_flag(self.timeline, 2003))

        ScheduleGameResult.objects.filter(pk=row.pk).delete()
        results = _season_results(self.timeline, 2003)
        close_2003_regular_season(timeline_id=self.timeline.pk, results=results)
        stored = ScheduleGameResult.objects.filter(
            timeline=self.timeline,
            season_year=2003,
        ).order_by('pk').first()
        ScheduleGameResult.objects.filter(pk=stored.pk).update(home_score=9)
        altered = _stored_submission(self.timeline, 2003)
        with self.assertRaises(DivisionPlaceRefused):
            close_2003_regular_season(timeline_id=self.timeline.pk, results=altered)
        self.assertEqual(ScheduleGameResult.objects.get(pk=stored.pk).home_score, 9)
        self.assertEqual(
            TimelineDivisionPlace.objects.filter(
                timeline=self.timeline,
                season_year=2003,
            ).count(),
            32,
        )
        self.assertTrue(_flag(self.timeline, 2003))

    def test_semantic_changes_are_refused(self):
        _lock_2003(self.timeline)
        results = _season_results(self.timeline, 2003)
        close_2003_regular_season(timeline_id=self.timeline.pk, results=results)

        def refuse(submitted=results):
            before = _division_rows(self.timeline, 2003)
            with self.assertRaises(DivisionPlaceRefused):
                close_2003_regular_season(
                    timeline_id=self.timeline.pk,
                    results=submitted,
                )
            self.assertEqual(_division_rows(self.timeline, 2003), before)
            self.assertTrue(_flag(self.timeline, 2003))

        place = TimelineDivisionPlace.objects.filter(
            timeline=self.timeline,
            season_year=2003,
            resolved_by='sole_remaining',
        ).order_by('pk').first()
        with transaction.atomic():
            TimelineDivisionPlace.objects.filter(pk=place.pk).update(
                resolved_by='winning_percentage',
            )
            refuse()
            transaction.set_rollback(True)

        draw = TimelineDivisionDraw.objects.filter(
            timeline=self.timeline,
            season_year=2003,
        ).order_by('pk').first()
        for field, value in (
            ('coordinate', f'{draw.coordinate}-altered'),
            ('candidates', 'altered-candidates'),
            ('digest', 'a' * 64),
            ('chosen_slug', 'not-the-club'),
            ('outcome', f'{draw.outcome}-altered'),
        ):
            with transaction.atomic():
                TimelineDivisionDraw.objects.filter(pk=draw.pk).update(**{field: value})
                refuse()
                self.assertEqual(
                    TimelineDivisionDraw.objects.values_list(field, flat=True).get(pk=draw.pk),
                    value,
                )
                transaction.set_rollback(True)

        with transaction.atomic():
            TimelineDivisionDraw.objects.filter(pk=draw.pk).delete()
            refuse()
            self.assertFalse(TimelineDivisionDraw.objects.filter(pk=draw.pk).exists())
            transaction.set_rollback(True)

        with transaction.atomic():
            TimelineDivisionDraw.objects.bulk_create([TimelineDivisionDraw(
                timeline=self.timeline,
                season_year=2003,
                conference_slug=draw.conference_slug,
                division_slug=draw.division_slug,
                draw_index=99,
                candidates=draw.candidates,
                coordinate=f'{draw.coordinate}:extra',
                digest='b' * 64,
                chosen_slug=draw.chosen_slug,
                outcome='extra',
            )])
            refuse()
            self.assertTrue(TimelineDivisionDraw.objects.filter(
                timeline=self.timeline,
                season_year=2003,
                draw_index=99,
            ).exists())
            transaction.set_rollback(True)

        stored = ScheduleGameResult.objects.filter(
            timeline=self.timeline,
            season_year=2003,
        ).order_by('pk').first()
        ScheduleGameResult.objects.filter(pk=stored.pk).update(home_score=13)
        refuse(_stored_submission(self.timeline, 2003))
        self.assertEqual(ScheduleGameResult.objects.get(pk=stored.pk).home_score, 13)

    def test_cross_season_and_cross_timeline_results_are_refused(self):
        game = ScheduleGame.objects.filter(
            timeline=self.timeline,
            season_year=2003,
        ).order_by('pk').first()
        with writing_division_rows():
            mismatched = ScheduleGameResult(
                timeline=self.timeline,
                game=game,
                season_year=2002,
                home_score=6,
                away_score=0,
                home_touchdowns=1,
                away_touchdowns=0,
                source_key='project-result',
            )
            with self.assertRaisesMessage(
                ValidationError,
                'The result season must match its 2002 fixture.',
            ):
                mismatched.full_clean()
        ScheduleGameResult.objects.bulk_create([mismatched])
        _lock_2003(self.timeline)
        with self.assertRaisesMessage(
            DivisionPlaceRefused,
            'A 2003 result must belong to a 2003 fixture on the same timeline.',
        ):
            close_2003_regular_season(
                timeline_id=self.timeline.pk,
                results=_season_results(self.timeline, 2003),
            )
        self.assertEqual(
            ScheduleGameResult.objects.get(pk=mismatched.pk).season_year,
            2002,
        )
        self.assertEqual(ScheduleGameResult.objects.get(pk=mismatched.pk).game_id, game.pk)
        self.assertEqual(_count(self.timeline, 2003), (0, 0, 0))
        self.assertFalse(_flag(self.timeline, 2003))
        ScheduleGameResult.objects.filter(pk=mismatched.pk).delete()

        played = ScheduleGameResult.objects.filter(
            timeline=self.timeline,
            season_year=2002,
        ).order_by('pk').first()
        ScheduleGameResult.objects.filter(pk=played.pk).update(season_year=2003)
        with self.assertRaisesMessage(
            DivisionPlaceRefused,
            'A 2003 result must belong to a 2003 fixture on the same timeline.',
        ):
            close_2003_regular_season(
                timeline_id=self.timeline.pk,
                results=_season_results(self.timeline, 2003),
            )
        self.assertEqual(ScheduleGameResult.objects.get(pk=played.pk).season_year, 2003)
        self.assertFalse(_flag(self.timeline, 2003))
        ScheduleGameResult.objects.filter(pk=played.pk).update(season_year=2002)

        other = _timeline(self.release, 'foreign 2003', self.profiles, 2002)
        ScheduleGame.objects.bulk_create([ScheduleGame(
            timeline=other,
            season_year=2003,
            home_id=game.home_id,
            away_id=game.away_id,
            kind=ScheduleGame.Kind.REGULAR,
            category=ScheduleGame.Category.DIVISION,
        )])
        foreign = ScheduleGame.objects.get(timeline=other, season_year=2003)
        with writing_division_rows():
            crossed = ScheduleGameResult(
                timeline=self.timeline,
                game=foreign,
                season_year=2003,
                home_score=6,
                away_score=0,
                home_touchdowns=1,
                away_touchdowns=0,
                source_key='project-result',
            )
            with self.assertRaisesMessage(
                ValidationError,
                'The result belongs to the fixture\'s timeline.',
            ):
                crossed.full_clean()
        ScheduleGameResult.objects.bulk_create([crossed])
        with self.assertRaisesMessage(
            DivisionPlaceRefused,
            'A 2003 result must belong to a 2003 fixture on the same timeline.',
        ):
            close_2003_regular_season(
                timeline_id=self.timeline.pk,
                results=_season_results(self.timeline, 2003),
            )
        self.assertTrue(ScheduleGameResult.objects.filter(pk=crossed.pk).exists())
        self.assertEqual(
            TimelineDivisionPlace.objects.filter(
                timeline=self.timeline,
                season_year=2003,
            ).count(),
            0,
        )

    def test_each_write_stage_rolls_back(self):
        _lock_2003(self.timeline)
        results = _season_results(self.timeline, 2003)
        before = _kept(self.release, self.timeline)
        targets = (
            'timelines.services.division_place_2002.ScheduleGameResult.objects.bulk_create',
            'timelines.services.division_place_2002.TimelineDivisionDraw.objects.bulk_create',
            'timelines.services.division_place_2002.TimelineDivisionPlace.objects.bulk_create',
        )
        for target in targets:
            with patch(target, side_effect=IntegrityError('forced')):
                with self.assertRaises(DivisionPlaceRefused):
                    close_2003_regular_season(
                        timeline_id=self.timeline.pk,
                        results=results,
                    )
            self._assert_2003_rolled_back(before, target)
        real_update = QuerySet.update

        def fail_flag(self, **kwargs):
            if 'regular_season_simulated' in kwargs:
                raise IntegrityError('forced')
            return real_update(self, **kwargs)

        with patch.object(QuerySet, 'update', fail_flag):
            with self.assertRaises(DivisionPlaceRefused):
                close_2003_regular_season(
                    timeline_id=self.timeline.pk,
                    results=results,
                )
        self._assert_2003_rolled_back(before, 'flag update')
        for count in (0, 2):
            def wrong_count(self, count=count, **kwargs):
                if 'regular_season_simulated' in kwargs:
                    real_update(self, **kwargs)
                    return count
                return real_update(self, **kwargs)

            with patch.object(QuerySet, 'update', wrong_count):
                with self.assertRaises(DivisionPlaceRefused):
                    close_2003_regular_season(
                        timeline_id=self.timeline.pk,
                        results=results,
                    )
            self._assert_2003_rolled_back(before, f'flag count {count}')

    def test_closing_2003_leaves_2002_and_the_verifier_readable(self):
        before = _kept(self.release, self.timeline)
        games_before = _game_rows(self.timeline, 2003)
        _lock_2003(self.timeline)
        results = _season_results(self.timeline, 2003)
        close_2003_regular_season(timeline_id=self.timeline.pk, results=results)
        self.assertEqual(_kept(self.release, self.timeline), before)
        self.assertEqual(_game_rows(self.timeline, 2003), games_before)
        outcome, writes = _writes_during(lambda: close_2002_regular_season(
            timeline_id=self.timeline.pk,
            results=_tied_results(self.timeline),
        ))
        self.assertEqual(outcome, 'unchanged')
        self.assertEqual(writes, [])
        stored = _place_map(self.timeline, 2003)
        with transaction.atomic():
            with holding_release_lock(self.release.pk):
                found, verify_writes = _writes_during(
                    lambda: verify_played_2003_places(self.timeline),
                )
                played_2002 = verify_played_2002_places(self.timeline)
        self.assertEqual(found, stored)
        self.assertEqual(verify_writes, [])
        self.assertEqual(played_2002, _place_map(self.timeline, 2002))
        self.assertEqual(len(found), 32)
        self.assertTrue(_flag(self.timeline, 2002))
        self.assertTrue(_flag(self.timeline, 2003))

    def test_the_verifier_refuses_an_open_season_without_writing(self):
        _lock_2003(self.timeline)
        with transaction.atomic():
            with holding_release_lock(self.release.pk):
                with self.assertRaisesMessage(
                    DivisionPlaceRefused,
                    'The 2003 regular season is not complete.',
                ):
                    verify_played_2003_places(self.timeline)
        self.assertEqual(_count(self.timeline, 2003), (0, 0, 0))
        self.assertFalse(_flag(self.timeline, 2003))

    def test_unlocked_directives_are_refused(self):
        with self.assertRaisesMessage(
            DivisionPlaceRefused,
            'The 2003 directives are not locked.',
        ):
            close_2003_regular_season(
                timeline_id=self.timeline.pk,
                results=_season_results(self.timeline, 2003),
            )
        self.assertEqual(_count(self.timeline, 2003), (0, 0, 0))
        self.assertFalse(_flag(self.timeline, 2003))

    def _played_timeline(self, name):
        timeline = _timeline(self.release, name, self.profiles, 2002)
        generate_timeline_schedule(timeline_id=timeline.pk, season_year=2002)
        _lock_directives(timeline)
        close_2002_regular_season(
            timeline_id=timeline.pk,
            results=_tied_results(timeline),
        )
        _season(timeline, self.profiles, 2003)
        generate_timeline_schedule(timeline_id=timeline.pk, season_year=2003)
        return timeline

    def _assert_2003_rolled_back(self, before, label):
        self.assertEqual(_count(self.timeline, 2003), (0, 0, 0), label)
        self.assertFalse(_flag(self.timeline, 2003), label)
        self.assertEqual(_kept(self.release, self.timeline), before, label)


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


def _lock_2003(timeline):
    season = TimelineSeason.objects.get(timeline=timeline, season_year=2003)
    if season.directives_locked:
        return
    season.setup_complete = True
    season.directives_locked = True
    season.save()


def _season_results(timeline, year, home_score=6, away_score=6, home_td=1, away_td=1):
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
        for game in ScheduleGame.objects.filter(timeline=timeline, season_year=year)
    ]


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


def _stored_submission(timeline, year):
    return [
        Result(
            row.game.home_id,
            row.game.away_id,
            row.home_score,
            row.away_score,
            row.home_touchdowns,
            row.away_touchdowns,
            row.source_key,
        )
        for row in ScheduleGameResult.objects.filter(
            timeline=timeline,
            season_year=year,
        ).select_related('game')
    ]


def _flag(timeline, year):
    return TimelineSeason.objects.get(
        timeline=timeline,
        season_year=year,
    ).regular_season_simulated


def _count(timeline, year):
    return (
        ScheduleGameResult.objects.filter(timeline=timeline, season_year=year).count(),
        TimelineDivisionDraw.objects.filter(timeline=timeline, season_year=year).count(),
        TimelineDivisionPlace.objects.filter(timeline=timeline, season_year=year).count(),
    )


def _place_map(timeline, year):
    return {
        row.franchise_id: row.division_place
        for row in TimelineDivisionPlace.objects.filter(
            timeline=timeline,
            season_year=year,
        )
    }


def _game_rows(timeline, year):
    return tuple(ScheduleGame.objects.filter(
        timeline=timeline,
        season_year=year,
    ).order_by('pk').values_list('pk', 'home_id', 'away_id', 'category', 'kind'))


def _draw_rows(timeline, year):
    return tuple(TimelineDivisionDraw.objects.filter(
        timeline=timeline,
        season_year=year,
    ).order_by('conference_slug', 'division_slug', 'draw_index').values_list(
        'conference_slug',
        'division_slug',
        'draw_index',
        'candidates',
        'coordinate',
        'digest',
        'chosen_slug',
        'outcome',
    ))


def _place_rows(timeline, year):
    return tuple(TimelineDivisionPlace.objects.filter(
        timeline=timeline,
        season_year=year,
    ).order_by('franchise__placeholder_slug').values_list(
        'franchise__placeholder_slug',
        'division_place',
        'conference_slug',
        'division_slug',
        'resolved_by',
    ))


def _division_rows(timeline, year):
    return (
        tuple(ScheduleGameResult.objects.filter(
            timeline=timeline,
            season_year=year,
        ).order_by('pk').values_list(
            'pk',
            'game_id',
            'home_score',
            'away_score',
            'home_touchdowns',
            'away_touchdowns',
            'source_key',
        )),
        _draw_rows(timeline, year),
        tuple(TimelineDivisionPlace.objects.filter(
            timeline=timeline,
            season_year=year,
        ).order_by('pk').values_list(
            'pk',
            'franchise_id',
            'division_place',
            'resolved_by',
            'draw_id',
        )),
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
            'pk',
            'home_franchise_id',
            'away_franchise_id',
            'kind',
        )),
        tuple(OpponentWeekAssignment.objects.filter(release=release).order_by('pk').values_list(
            'pk',
            'pairing_id',
            'week',
        )),
        tuple(HistoricalStanding.objects.filter(franchise__release=release).order_by('pk').values_list(
            'pk',
            'franchise_id',
            'wins',
            'losses',
            'ties',
            'draft_order',
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
        _division_rows(timeline, 2002),
        tuple(TimelineScheduleSeed.objects.filter(timeline=timeline).order_by('pk').values_list(
            'pk',
            'franchise_id',
            'division_place',
            'conference_slug',
            'division_slug',
        )),
        tuple(TimelinePriorFinish.objects.filter(timeline=timeline).order_by('pk').values_list(
            'pk',
            'franchise_id',
            'division_place',
        )),
        tuple(TimelineSchedulingRank.objects.filter(timeline=timeline).order_by('pk').values_list(
            'pk',
            'franchise_id',
            'scheduling_rank',
        )),
        _flag(timeline, 2002),
    )


def _hex_digest(seed, message):
    return hmac.new(
        str(seed).encode('utf-8'),
        str(message).encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
