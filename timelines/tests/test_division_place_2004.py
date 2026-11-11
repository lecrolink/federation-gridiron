from collections import Counter
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
    close_2004_regular_season,
    division_draw_label,
    verify_played_2002_places,
    verify_played_2003_places,
    verify_played_2004_places,
)
from timelines.services.schedule_2002 import (
    TimelineScheduleRefused,
    generate_timeline_schedule,
)
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
from timelines.tests.test_division_place_2003 import (
    _division_rows,
    _draw_rows,
    _flag,
    _franchise_identity,
    _game_rows,
    _hex_digest,
    _place_map,
    _place_rows,
    _ranked_results,
    _season,
    _season_results,
    _stored_submission,
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


PROFILE_KEYS = {'y2002', 'y2004'}


class SharedProcedureTests(DivisionProcedureTests):
    """The 2004 close uses the same rank_places steps as 2002 and 2003."""

    def test_draw_label_keeps_earlier_coordinates_and_names_2004(self):
        arguments = ('conference-1', 'd1', ('founding-01', 'founding-02'), 2)
        self.assertEqual(
            division_draw_label(*arguments),
            'division-place:2002:conference-1:d1:founding-01,founding-02:2',
        )
        self.assertEqual(
            division_draw_label(*arguments, 2003),
            'division-place:2003:conference-1:d1:founding-01,founding-02:2',
        )
        later = division_draw_label(*arguments, 2004)
        self.assertEqual(
            later,
            'division-place:2004:conference-1:d1:founding-01,founding-02:2',
        )
        self.assertNotIn('timeline', later.split(':'))
        named = division_draw_label(*arguments, 2005)
        self.assertIn(':2005:', named)
        self.assertIn(':2021:', division_draw_label(*arguments, 2021))
        with self.assertRaisesMessage(DivisionPlaceRefused, 'outside 2002–2029'):
            division_draw_label(*arguments, 2030)

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


class DivisionPlace2004Tests(TestCase):
    @classmethod
    def setUpTestData(cls):
        league = League(
            name='Division Place 2004 League',
            slug='division-place-2004-league',
            tier=1,
            active=True,
        )
        league.save()
        release = UniverseRelease(
            slug='division-place-2004',
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
        cls.timeline = _through_2004_fixtures(release, 'opens in 2002', profiles)

    def test_results_store_256_games_and_32_places(self):
        before = _kept(self.release, self.timeline)
        games_before = _game_rows(self.timeline, 2004)
        _lock_2004(self.timeline)
        outcome = close_2004_regular_season(
            timeline_id=self.timeline.pk,
            results=_season_results(self.timeline, 2004),
        )
        self.assertEqual(outcome, 'created')
        season = TimelineSeason.objects.get(timeline=self.timeline, season_year=2004)
        self.assertTrue(season.regular_season_simulated)
        self.timeline.refresh_from_db()
        self.assertEqual(self.timeline.current_season_year, 2002)
        results = ScheduleGameResult.objects.filter(
            timeline=self.timeline,
            season_year=2004,
        )
        self.assertEqual(results.count(), 256)
        appearances = Counter()
        for row in results:
            appearances[row.game.home_id] += 1
            appearances[row.game.away_id] += 1
            self.assertEqual(row.game.season_year, 2004)
            self.assertEqual(row.game.timeline_id, self.timeline.pk)
        membership_ids = set(FranchiseSeasonMembership.objects.filter(
            franchise__release=self.release,
            season_year=2004,
        ).values_list('franchise_id', flat=True))
        self.assertEqual(set(appearances), membership_ids)
        for count in appearances.values():
            self.assertEqual(count, 16)
        self.assertEqual(
            TimelineDivisionPlace.objects.filter(
                timeline=self.timeline,
                season_year=2004,
            ).count(),
            32,
        )
        self.assertEqual(
            TimelineDivisionDraw.objects.filter(
                timeline=self.timeline,
                season_year=2004,
            ).count(),
            24,
        )
        for conference, division in (
            FranchiseSeasonMembership.objects.filter(
                franchise__release=self.release,
                season_year=2004,
            ).values_list('conference_slug', 'division_slug').distinct()
        ):
            places = list(TimelineDivisionPlace.objects.filter(
                timeline=self.timeline,
                season_year=2004,
                conference_slug=conference,
                division_slug=division,
            ))
            self.assertEqual(sorted(place.division_place for place in places), [1, 2, 3, 4])
            for place in places:
                membership = FranchiseSeasonMembership.objects.get(
                    franchise_id=place.franchise_id,
                    season_year=2004,
                )
                self.assertEqual(place.conference_slug, membership.conference_slug)
                self.assertEqual(place.division_slug, membership.division_slug)
                if place.division_place == 4:
                    self.assertEqual(place.resolved_by, 'sole_remaining')
                    self.assertIsNone(place.draw_id)
                else:
                    self.assertEqual(place.resolved_by, DRAW)
                    self.assertEqual(place.draw.season_year, 2004)
                    self.assertEqual(place.draw.chosen_slug, place.franchise.placeholder_slug)
        self.assertEqual(_game_rows(self.timeline, 2004), games_before)
        self.assertEqual(_kept(self.release, self.timeline), before)
        self.assertEqual(
            ScheduleGame.objects.filter(timeline=self.timeline, season_year=2005).count(),
            0,
        )
        with self.assertRaisesMessage(
            TimelineScheduleRefused,
            'The timeline has no season for this year.',
        ):
            generate_timeline_schedule(timeline_id=self.timeline.pk, season_year=2005)
        self.assertEqual(
            ScheduleGame.objects.filter(timeline=self.timeline, season_year=2005).count(),
            0,
        )

    def test_draws_name_2004_and_match_across_timelines(self):
        other = self._played_timeline('same 2004 inputs')
        for timeline in (self.timeline, other):
            _lock_2004(timeline)
            close_2004_regular_season(
                timeline_id=timeline.pk,
                results=_season_results(timeline, 2004),
            )
        left = _draw_rows(self.timeline, 2004)
        right = _draw_rows(other, 2004)
        self.assertEqual(left, right)
        self.assertEqual(_place_rows(self.timeline, 2004), _place_rows(other, 2004))
        self.assertTrue(left)
        for row in TimelineDivisionDraw.objects.filter(
            timeline=self.timeline,
            season_year=2004,
        ):
            label = division_draw_label(
                row.conference_slug,
                row.division_slug,
                tuple(row.candidates.split(',')),
                row.draw_index,
                2004,
            )
            self.assertTrue(row.coordinate.startswith(f'{label}:'))
            self.assertTrue(row.coordinate[len(label) + 1:].isdigit())
            self.assertEqual(row.coordinate.split(':')[:6], label.split(':'))
            self.assertEqual(row.digest, _hex_digest(self.release.seed, row.coordinate))
            self.assertEqual(row.outcome, f'{row.resolved_place.division_place}:{row.chosen_slug}')
        for year, prefix in ((2002, 'division-place:2002:'), (2003, 'division-place:2003:')):
            rows = TimelineDivisionDraw.objects.filter(timeline=self.timeline, season_year=year)
            self.assertTrue(rows.exists())
            for row in rows:
                self.assertTrue(row.coordinate.startswith(prefix))
                self.assertNotIn(':2004:', row.coordinate)

    def test_different_results_place_different_clubs(self):
        games_before = _game_rows(self.timeline, 2004)
        identity = _franchise_identity(self.release)
        _lock_2004(self.timeline)
        close_2004_regular_season(
            timeline_id=self.timeline.pk,
            results=_season_results(self.timeline, 2004),
        )
        other = self._played_timeline('ranked 2004')
        _lock_2004(other)
        close_2004_regular_season(
            timeline_id=other.pk,
            results=_ranked_results(other, 2004),
        )
        tied = _place_map(self.timeline, 2004)
        ranked = _place_map(other, 2004)
        self.assertNotEqual(tied, ranked)
        self.assertEqual(set(tied), set(ranked))
        memberships = {
            row.franchise_id: row.slot_order
            for row in FranchiseSeasonMembership.objects.filter(
                franchise__release=self.release,
                season_year=2004,
            )
        }
        self.assertEqual(ranked, memberships)
        self.assertEqual(
            TimelineDivisionDraw.objects.filter(timeline=other, season_year=2004).count(),
            0,
        )
        self.assertEqual(_franchise_identity(self.release), identity)
        self.assertEqual(_game_rows(self.timeline, 2004), games_before)

    def test_ordinary_save_refuses_the_2004_completion_flag(self):
        season = TimelineSeason.objects.get(timeline=self.timeline, season_year=2004)
        season.setup_complete = True
        season.directives_locked = True
        season.save()
        season.regular_season_simulated = True
        with self.assertRaisesMessage(
            ValidationError,
            'The 2004 regular season is marked complete only by its close.',
        ):
            season.save()
        season.refresh_from_db()
        self.assertFalse(season.regular_season_simulated)
        game = ScheduleGame.objects.filter(
            timeline=self.timeline,
            season_year=2004,
        ).order_by('pk').first()
        refused = ScheduleGameResult(
            timeline=self.timeline,
            game=game,
            season_year=2004,
            home_score=6,
            away_score=0,
            home_touchdowns=1,
            away_touchdowns=0,
            source_key='project-result',
        )
        with self.assertRaisesMessage(
            ValidationError,
            'A 2004 result is stored with the whole season.',
        ):
            refused.save()
        _lock_2004(self.timeline)
        close_2004_regular_season(
            timeline_id=self.timeline.pk,
            results=_season_results(self.timeline, 2004),
        )
        season.refresh_from_db()
        self.assertTrue(season.regular_season_simulated)
        TimelineSeason.objects.filter(pk=season.pk).update(regular_season_simulated=False)
        season.refresh_from_db()
        season.regular_season_simulated = True
        with self.assertRaisesMessage(
            ValidationError,
            'The 2004 regular season is marked complete only by its close.',
        ):
            season.save()
        season.refresh_from_db()
        self.assertFalse(season.regular_season_simulated)

        profile = next(item for item in self.profiles if item.profile_key == 'y2004')
        later = TimelineSeason(
            timeline=self.timeline,
            profile=profile,
            season_year=2005,
            season_index=4,
            phase=Timeline.Phase.DIRECTION,
            setup_complete=True,
            directives_locked=True,
        )
        later.save()
        later.regular_season_simulated = True
        with self.assertRaisesMessage(
            ValidationError,
            'The 2005 regular season is marked complete only by its close.',
        ):
            later.save()
        later.refresh_from_db()
        self.assertFalse(later.regular_season_simulated)
        self.assertEqual(
            ScheduleGameResult.objects.filter(timeline=self.timeline, season_year=2005).count(),
            0,
        )

    def test_exact_rerun_writes_nothing(self):
        _lock_2004(self.timeline)
        results = _season_results(self.timeline, 2004)
        close_2004_regular_season(timeline_id=self.timeline.pk, results=results)
        outcome, writes = _writes_during(lambda: close_2004_regular_season(
            timeline_id=self.timeline.pk,
            results=results,
        ))
        self.assertEqual(outcome, 'unchanged')
        self.assertEqual(writes, [])

    def test_partial_altered_and_unbalanced_rows_stay_in_place(self):
        game = ScheduleGame.objects.filter(
            timeline=self.timeline,
            season_year=2004,
        ).order_by('pk').first()
        with writing_division_rows():
            row = ScheduleGameResult(
                timeline=self.timeline,
                game=game,
                season_year=2004,
                home_score=3,
                away_score=0,
                home_touchdowns=0,
                away_touchdowns=0,
                source_key='partial',
            )
            row.full_clean()
            ScheduleGameResult.objects.bulk_create([row])
        _lock_2004(self.timeline)
        with self.assertRaises(DivisionPlaceRefused):
            close_2004_regular_season(
                timeline_id=self.timeline.pk,
                results=_season_results(self.timeline, 2004),
            )
        self.assertEqual(ScheduleGameResult.objects.get(pk=row.pk).home_score, 3)
        self.assertEqual(_count(self.timeline, 2004), (1, 0, 0))
        self.assertFalse(_flag(self.timeline, 2004))
        ScheduleGameResult.objects.filter(pk=row.pk).delete()

        removed = ScheduleGame.objects.filter(
            timeline=self.timeline,
            season_year=2004,
        ).order_by('pk').first()
        removed_pair = (removed.home_id, removed.away_id, removed.category)
        removed.delete()
        with self.assertRaisesMessage(
            DivisionPlaceRefused,
            'The 2004 fixtures are incomplete.',
        ):
            close_2004_regular_season(
                timeline_id=self.timeline.pk,
                results=_season_results(self.timeline, 2004),
            )
        self.assertEqual(_count(self.timeline, 2004), (0, 0, 0))
        ScheduleGame.objects.create(
            timeline=self.timeline,
            season_year=2004,
            home_id=removed_pair[0],
            away_id=removed_pair[1],
            kind=ScheduleGame.Kind.REGULAR,
            category=removed_pair[2],
        )

        games = list(ScheduleGame.objects.filter(
            timeline=self.timeline,
            season_year=2004,
        ).order_by('pk'))
        existing = {(item.home_id, item.away_id) for item in games}
        target = games[0]
        replacement = next(
            item.home_id for item in games
            if item.home_id != target.home_id
            and item.home_id != target.away_id
            and (item.home_id, target.away_id) not in existing
        )
        ScheduleGame.objects.filter(pk=target.pk).update(home_id=replacement)
        with self.assertRaisesMessage(
            DivisionPlaceRefused,
            'The 2004 fixtures are incomplete.',
        ):
            close_2004_regular_season(
                timeline_id=self.timeline.pk,
                results=_season_results(self.timeline, 2004),
            )
        self.assertEqual(ScheduleGame.objects.get(pk=target.pk).home_id, replacement)
        self.assertEqual(_count(self.timeline, 2004), (0, 0, 0))
        self.assertFalse(_flag(self.timeline, 2004))
        ScheduleGame.objects.filter(pk=target.pk).update(home_id=target.home_id)

        results = _season_results(self.timeline, 2004)
        close_2004_regular_season(timeline_id=self.timeline.pk, results=results)
        stored = ScheduleGameResult.objects.filter(
            timeline=self.timeline,
            season_year=2004,
        ).order_by('pk').first()
        ScheduleGameResult.objects.filter(pk=stored.pk).update(home_score=9)
        with self.assertRaises(DivisionPlaceRefused):
            close_2004_regular_season(
                timeline_id=self.timeline.pk,
                results=_stored_submission(self.timeline, 2004),
            )
        self.assertEqual(ScheduleGameResult.objects.get(pk=stored.pk).home_score, 9)
        self.assertEqual(
            TimelineDivisionPlace.objects.filter(
                timeline=self.timeline,
                season_year=2004,
            ).count(),
            32,
        )
        self.assertTrue(_flag(self.timeline, 2004))

    def test_semantic_changes_are_refused(self):
        _lock_2004(self.timeline)
        results = _season_results(self.timeline, 2004)
        close_2004_regular_season(timeline_id=self.timeline.pk, results=results)

        def refuse(submitted=results):
            before = _division_rows(self.timeline, 2004)
            with self.assertRaises(DivisionPlaceRefused):
                close_2004_regular_season(
                    timeline_id=self.timeline.pk,
                    results=submitted,
                )
            self.assertEqual(_division_rows(self.timeline, 2004), before)
            self.assertTrue(_flag(self.timeline, 2004))

        place = TimelineDivisionPlace.objects.filter(
            timeline=self.timeline,
            season_year=2004,
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
            season_year=2004,
        ).order_by('pk').first()
        with transaction.atomic():
            TimelineDivisionDraw.objects.filter(pk=draw.pk).update(digest='a' * 64)
            refuse()
            self.assertEqual(
                TimelineDivisionDraw.objects.values_list('digest', flat=True).get(pk=draw.pk),
                'a' * 64,
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
                season_year=2004,
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
                season_year=2004,
                draw_index=99,
            ).exists())
            transaction.set_rollback(True)

    def test_cross_season_and_cross_timeline_results_are_refused(self):
        game = ScheduleGame.objects.filter(
            timeline=self.timeline,
            season_year=2004,
        ).order_by('pk').first()
        with writing_division_rows():
            mismatched = ScheduleGameResult(
                timeline=self.timeline,
                game=game,
                season_year=2003,
                home_score=6,
                away_score=0,
                home_touchdowns=1,
                away_touchdowns=0,
                source_key='project-result',
            )
            with self.assertRaisesMessage(
                ValidationError,
                'The result season must match its fixture.',
            ):
                mismatched.full_clean()
        ScheduleGameResult.objects.bulk_create([mismatched])
        _lock_2004(self.timeline)
        with self.assertRaisesMessage(
            DivisionPlaceRefused,
            'A 2004 result must belong to a 2004 fixture on the same timeline.',
        ):
            close_2004_regular_season(
                timeline_id=self.timeline.pk,
                results=_season_results(self.timeline, 2004),
            )
        self.assertEqual(ScheduleGameResult.objects.get(pk=mismatched.pk).season_year, 2003)
        self.assertEqual(_count(self.timeline, 2004), (0, 0, 0))
        self.assertFalse(_flag(self.timeline, 2004))
        ScheduleGameResult.objects.filter(pk=mismatched.pk).delete()

        played = ScheduleGameResult.objects.filter(
            timeline=self.timeline,
            season_year=2003,
        ).order_by('pk').first()
        ScheduleGameResult.objects.filter(pk=played.pk).update(season_year=2004)
        with self.assertRaisesMessage(
            DivisionPlaceRefused,
            'A 2004 result must belong to a 2004 fixture on the same timeline.',
        ):
            close_2004_regular_season(
                timeline_id=self.timeline.pk,
                results=_season_results(self.timeline, 2004),
            )
        self.assertEqual(ScheduleGameResult.objects.get(pk=played.pk).season_year, 2004)
        self.assertFalse(_flag(self.timeline, 2004))
        ScheduleGameResult.objects.filter(pk=played.pk).update(season_year=2003)

        other = _timeline(self.release, 'foreign 2004', self.profiles, 2002)
        ScheduleGame.objects.bulk_create([ScheduleGame(
            timeline=other,
            season_year=2004,
            home_id=game.home_id,
            away_id=game.away_id,
            kind=ScheduleGame.Kind.REGULAR,
            category=ScheduleGame.Category.DIVISION,
        )])
        foreign = ScheduleGame.objects.get(timeline=other, season_year=2004)
        with writing_division_rows():
            crossed = ScheduleGameResult(
                timeline=self.timeline,
                game=foreign,
                season_year=2004,
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
            'A 2004 result must belong to a 2004 fixture on the same timeline.',
        ):
            close_2004_regular_season(
                timeline_id=self.timeline.pk,
                results=_season_results(self.timeline, 2004),
            )
        self.assertEqual(ScheduleGameResult.objects.get(pk=crossed.pk).game_id, foreign.pk)
        self.assertEqual(_count(self.timeline, 2004), (1, 0, 0))
        self.assertFalse(_flag(self.timeline, 2004))

    def test_each_write_stage_rolls_back(self):
        _lock_2004(self.timeline)
        results = _season_results(self.timeline, 2004)
        before = _kept(self.release, self.timeline)
        targets = (
            'timelines.services.division_place_2002.ScheduleGameResult.objects.bulk_create',
            'timelines.services.division_place_2002.TimelineDivisionDraw.objects.bulk_create',
            'timelines.services.division_place_2002.TimelineDivisionPlace.objects.bulk_create',
        )
        for target in targets:
            with patch(target, side_effect=IntegrityError('forced')):
                with self.assertRaises(DivisionPlaceRefused):
                    close_2004_regular_season(
                        timeline_id=self.timeline.pk,
                        results=results,
                    )
            self._assert_2004_rolled_back(before, target)
        real_update = QuerySet.update

        def fail_flag(self, **kwargs):
            if 'regular_season_simulated' in kwargs:
                raise IntegrityError('forced')
            return real_update(self, **kwargs)

        with patch.object(QuerySet, 'update', fail_flag):
            with self.assertRaises(DivisionPlaceRefused):
                close_2004_regular_season(
                    timeline_id=self.timeline.pk,
                    results=results,
                )
        self._assert_2004_rolled_back(before, 'flag update')
        for count in (0, 2):
            def wrong_count(self, count=count, **kwargs):
                if 'regular_season_simulated' in kwargs:
                    real_update(self, **kwargs)
                    return count
                return real_update(self, **kwargs)

            with patch.object(QuerySet, 'update', wrong_count):
                with self.assertRaises(DivisionPlaceRefused):
                    close_2004_regular_season(
                        timeline_id=self.timeline.pk,
                        results=results,
                    )
            self._assert_2004_rolled_back(before, f'flag count {count}')

    def test_closing_2004_leaves_2002_and_2003_and_the_verifier_readable(self):
        before = _kept(self.release, self.timeline)
        games_before = _game_rows(self.timeline, 2004)
        _lock_2004(self.timeline)
        results = _season_results(self.timeline, 2004)
        close_2004_regular_season(timeline_id=self.timeline.pk, results=results)
        self.assertEqual(_kept(self.release, self.timeline), before)
        self.assertEqual(_game_rows(self.timeline, 2004), games_before)
        for year, close, submitted in (
            (2002, close_2002_regular_season, _tied_results(self.timeline)),
            (2003, close_2003_regular_season, _season_results(self.timeline, 2003)),
        ):
            outcome, writes = _writes_during(lambda close=close, submitted=submitted: close(
                timeline_id=self.timeline.pk,
                results=submitted,
            ))
            self.assertEqual(outcome, 'unchanged')
            self.assertEqual(writes, [])
            self.assertTrue(_flag(self.timeline, year))
        stored = _place_map(self.timeline, 2004)
        with transaction.atomic():
            with holding_release_lock(self.release.pk):
                found, verify_writes = _writes_during(
                    lambda: verify_played_2004_places(self.timeline),
                )
                played_2002 = verify_played_2002_places(self.timeline)
                played_2003 = verify_played_2003_places(self.timeline)
        self.assertEqual(found, stored)
        self.assertEqual(verify_writes, [])
        self.assertEqual(played_2002, _place_map(self.timeline, 2002))
        self.assertEqual(played_2003, _place_map(self.timeline, 2003))
        self.assertEqual(len(found), 32)

    def test_the_verifier_refuses_an_open_season_without_writing(self):
        _lock_2004(self.timeline)
        with transaction.atomic():
            with holding_release_lock(self.release.pk):
                with self.assertRaisesMessage(
                    DivisionPlaceRefused,
                    'The 2004 regular season is not complete.',
                ):
                    verify_played_2004_places(self.timeline)
        self.assertEqual(_count(self.timeline, 2004), (0, 0, 0))
        self.assertFalse(_flag(self.timeline, 2004))

    def test_unlocked_directives_and_a_missing_season_are_refused(self):
        with self.assertRaisesMessage(
            DivisionPlaceRefused,
            'The 2004 directives are not locked.',
        ):
            close_2004_regular_season(
                timeline_id=self.timeline.pk,
                results=_season_results(self.timeline, 2004),
            )
        self.assertEqual(_count(self.timeline, 2004), (0, 0, 0))
        self.assertFalse(_flag(self.timeline, 2004))

        closed = _through_2003(self.release, 'no 2004 row', self.profiles)
        with self.assertRaisesMessage(
            DivisionPlaceRefused,
            'The timeline has no 2004 season.',
        ):
            close_2004_regular_season(
                timeline_id=closed.pk,
                results=_season_results(self.timeline, 2004),
            )
        self.assertEqual(_count(closed, 2004), (0, 0, 0))

    def _played_timeline(self, name):
        return _through_2004_fixtures(self.release, name, self.profiles)

    def _assert_2004_rolled_back(self, before, label):
        self.assertEqual(_count(self.timeline, 2004), (0, 0, 0), label)
        self.assertFalse(_flag(self.timeline, 2004), label)
        self.assertEqual(_kept(self.release, self.timeline), before, label)


def _through_2003(release, name, profiles):
    timeline = _timeline(release, name, profiles, 2002)
    generate_timeline_schedule(timeline_id=timeline.pk, season_year=2002)
    _lock_directives(timeline)
    close_2002_regular_season(
        timeline_id=timeline.pk,
        results=_tied_results(timeline),
    )
    _season(timeline, profiles, 2003)
    generate_timeline_schedule(timeline_id=timeline.pk, season_year=2003)
    _lock_year(timeline, 2003)
    close_2003_regular_season(
        timeline_id=timeline.pk,
        results=_season_results(timeline, 2003),
    )
    return timeline


def _through_2004_fixtures(release, name, profiles):
    timeline = _through_2003(release, name, profiles)
    _season(timeline, profiles, 2004)
    generate_timeline_schedule(timeline_id=timeline.pk, season_year=2004)
    return timeline


def _lock_2004(timeline):
    _lock_year(timeline, 2004)


def _lock_year(timeline, year):
    season = TimelineSeason.objects.get(timeline=timeline, season_year=year)
    if season.directives_locked:
        return
    season.setup_complete = True
    season.directives_locked = True
    season.save()


def _count(timeline, year):
    return (
        ScheduleGameResult.objects.filter(timeline=timeline, season_year=year).count(),
        TimelineDivisionDraw.objects.filter(timeline=timeline, season_year=year).count(),
        TimelineDivisionPlace.objects.filter(timeline=timeline, season_year=year).count(),
    )


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
        _division_rows(timeline, 2002),
        _division_rows(timeline, 2003),
        _flag(timeline, 2002),
        _flag(timeline, 2003),
    )
