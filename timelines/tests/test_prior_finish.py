import hashlib
import hmac
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from leagues.models import League
from timelines.models import (
    Timeline,
    TimelinePriorFinish,
    TimelineSchedulingDraw,
    TimelineSchedulingRank,
    TimelineSeason,
    writing_transition_rows,
)
from timelines.services.prior_finish import (
    Finish,
    PriorClub,
    PriorFinishRefused,
    _hex_digest,
    _index_from_digest,
    _uniform_index,
    derive_scheduling_ranks,
    scheduling_order,
    store_prior_finishes,
)
from universe.generation.prospects import later_profile_values
from universe.generation.schedule import generate_schedule_release
from universe.models import (
    FranchiseSeasonMembership,
    OpponentPairing,
    OpponentWeekAssignment,
    UniverseFranchise,
    UniverseRelease,
)
from universe.rules import EraRuleProfile


WRITE_WORDS = ('INSERT', 'UPDATE', 'DELETE', 'REPLACE')
PROFILE_KEYS = {'y1999', 'y2002'}


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


def _boom(*_args, **_kwargs):
    raise AssertionError('The draw was not required.')


def _club(slug, wins, losses, ties, conference, division, place):
    return PriorClub(slug, wins, losses, ties, conference, division, place)


def _unplayed(slug):
    return PriorClub(slug, None, None, None, None, None, None)


class SchedulingOrderTests(SimpleTestCase):
    def test_percentage_orders_a_shared_division_before_any_draw(self):
        clubs = (
            _club('late', 6, 10, 0, 'conference-1', 'd1', 1),
            _club('early', 10, 6, 0, 'conference-1', 'd1', 2),
        )

        self.assertEqual(scheduling_order(clubs, _boom), ('early', 'late'))
        self.assertEqual(
            scheduling_order(tuple(reversed(clubs)), _boom),
            ('early', 'late'),
        )

    def test_a_half_win_ties_the_same_division_place_order(self):
        clubs = (
            _club('second', 8, 6, 2, 'conference-1', 'd1', 2),
            _club('first', 9, 7, 0, 'conference-1', 'd1', 1),
        )

        self.assertEqual(scheduling_order(clubs, _boom), ('first', 'second'))

    def test_ten_and_six_ranks_ahead_of_nine_six_and_a_tie(self):
        clubs = (
            _club('lower', 9, 6, 1, 'conference-1', 'd1', 1),
            _club('higher', 10, 6, 0, 'conference-1', 'd2', 4),
        )

        self.assertEqual(scheduling_order(clubs, _boom), ('higher', 'lower'))

    def test_a_winless_played_club_ranks_ahead_of_a_club_with_no_season(self):
        clubs = (
            _unplayed('expansion'),
            _club('winless', 0, 16, 0, 'conference-2', 'd3', 5),
        )

        self.assertEqual(scheduling_order(clubs, _boom), ('winless', 'expansion'))

    def test_same_division_order_survives_the_draw(self):
        clubs = (
            _club('a', 8, 8, 0, 'conference-1', 'd1', 1),
            _club('b', 8, 8, 0, 'conference-1', 'd1', 2),
            _club('c', 8, 8, 0, 'conference-1', 'd2', 1),
            _club('d', 8, 8, 0, 'conference-1', 'd2', 4),
        )

        def last(_step, _slugs, modulus):
            return modulus - 1

        order = scheduling_order(clubs, last)
        reversed_input = scheduling_order(tuple(reversed(clubs)), last)

        self.assertEqual(order, reversed_input)
        self.assertLess(order.index('a'), order.index('b'))
        self.assertLess(order.index('c'), order.index('d'))
        self.assertEqual(order[0], 'c')

    def test_cross_division_tie_follows_the_draw_rather_than_the_slug(self):
        clubs = (
            _club('a', 8, 8, 0, 'conference-2', 'd1', 1),
            _club('b', 8, 8, 0, 'conference-2', 'd2', 3),
        )

        def last(_step, slugs, modulus):
            self.assertEqual(slugs, ('a', 'b'))
            self.assertEqual(modulus, 2)
            return modulus - 1

        self.assertEqual(scheduling_order(clubs, last), ('b', 'a'))
        self.assertEqual(
            scheduling_order(tuple(reversed(clubs)), lambda _step, _slugs, _modulus: 0),
            ('a', 'b'),
        )

    def test_an_outsider_can_land_between_a_place_chain(self):
        clubs = (
            _club('first', 8, 8, 0, 'conference-1', 'd1', 1),
            _club('second', 8, 8, 0, 'conference-1', 'd1', 2),
            _club('other', 8, 8, 0, 'conference-1', 'd3', 1),
        )

        def middle(_step, _slugs, modulus):
            return 0 if modulus == 3 else modulus - 1

        order = scheduling_order(clubs, middle)

        self.assertEqual(order, ('first', 'other', 'second'))

    def test_uniform_index_rejects_a_biased_remainder_and_then_accepts(self):
        seen = []

        def draw(_seed, label):
            seen.append(label)
            if label.endswith(':0'):
                return (1 << 64) - 1
            return 1

        self.assertEqual(_uniform_index(7, 'scheduling-rank-2002:c:d:0:a,b', 3, draw), 1)
        self.assertEqual(
            seen,
            [
                'scheduling-rank-2002:c:d:0:a,b:0',
                'scheduling-rank-2002:c:d:0:a,b:1',
            ],
        )

    def test_uniform_index_stops_when_every_attempt_is_outside_the_range(self):
        def draw(_seed, _label):
            return (1 << 64) - 1

        with self.assertRaises(PriorFinishRefused):
            _uniform_index(7, 'label', 3, draw)


class PriorFinishTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        league = League(
            name='Prior Finish League',
            slug='prior-finish-league',
            tier=1,
            active=True,
        )
        league.save()
        release = UniverseRelease(
            slug='prior-finish',
            root_league=league,
            generator_version='pending',
            seed=11,
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
        for values in later_profile_values():
            if values['profile_key'] not in PROFILE_KEYS:
                continue
            EraRuleProfile(release=release, **values).save()
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
        release.catalog_fingerprint = 'catalog-fixed'
        release.validation_fingerprint = 'validation-fixed'
        release.status = UniverseRelease.Status.PUBLISHED
        release.published_at = timezone.now()
        release.save()
        cls.release = release
        cls.profiles = list(release.era_profiles.all())
        cls.timeline = _timeline(release, 'played 2001', cls.profiles)
        cls.identity = _identity(release)

    def test_finishes_require_a_completed_2001_season_and_its_schedule_length(self):
        season = TimelineSeason.objects.get(timeline=self.timeline, season_year=2001)
        self.assertTrue(season.regular_season_simulated)
        self.assertEqual(season.profile.schedule_length, 16)
        finishes = _default_finishes(self.release)

        status = store_prior_finishes(timeline_id=self.timeline.pk, finishes=finishes)

        self.assertEqual(status, 'created')
        season.refresh_from_db()
        self.assertTrue(season.regular_season_simulated)
        rows = list(TimelinePriorFinish.objects.filter(timeline=self.timeline))
        self.assertEqual(len(rows), 31)
        grouped = {}
        for row in rows:
            grouped.setdefault((row.conference_slug, row.division_slug), []).append(row)
            self.assertEqual(row.wins + row.losses + row.ties, 16)
        self.assertEqual(
            sorted(row.division_place for row in grouped[('conference-1', 'd1')]),
            list(range(1, 7)),
        )
        self.assertEqual(len(grouped[('conference-1', 'd1')]), 6)
        for key, members in grouped.items():
            if key == ('conference-1', 'd1'):
                continue
            self.assertEqual(sorted(row.division_place for row in members), list(range(1, 6)))
            self.assertEqual(len(members), 5)
        expansion = UniverseFranchise.objects.get(
            release=self.release,
            placeholder_slug='expansion-2002-01',
        )
        self.assertFalse(TimelinePriorFinish.objects.filter(franchise=expansion).exists())
        self.assertFalse(FranchiseSeasonMembership.objects.filter(
            franchise=expansion,
            season_year=2001,
        ).exists())
        self.assertEqual(_identity(self.release), self.identity)

    def test_a_missing_or_unfinished_2001_season_is_refused_before_writing(self):
        unfinished = _timeline(
            self.release,
            'season open',
            self.profiles,
            simulated=False,
        )
        with self.assertRaisesMessage(
            PriorFinishRefused,
            'The 2001 regular season is not complete.',
        ):
            store_prior_finishes(
                timeline_id=unfinished.pk,
                finishes=_default_finishes(self.release),
            )
        open_season = TimelineSeason.objects.get(timeline=unfinished, season_year=2001)
        self.assertFalse(open_season.regular_season_simulated)
        self.assertEqual(TimelinePriorFinish.objects.filter(timeline=unfinished).count(), 0)

        missing = _timeline(
            self.release,
            'no 2001 row',
            self.profiles,
            include_2001=False,
        )
        with self.assertRaisesMessage(
            PriorFinishRefused,
            'The 2001 regular season is not complete.',
        ):
            store_prior_finishes(
                timeline_id=missing.pk,
                finishes=_default_finishes(self.release),
            )
        self.assertEqual(TimelinePriorFinish.objects.filter(timeline=missing).count(), 0)

    def test_records_must_add_up_to_the_stored_schedule_length(self):
        base = _default_finishes(self.release)
        for replacement in (
            base[0]._replace(wins=0, losses=0, ties=0),
            base[0]._replace(wins=100, losses=0, ties=0),
            base[0]._replace(wins=15, losses=0, ties=0),
        ):
            finishes = [replacement, *base[1:]]
            with self.assertRaisesMessage(
                PriorFinishRefused,
                'The record uses the stored 2001 schedule length.',
            ):
                store_prior_finishes(timeline_id=self.timeline.pk, finishes=finishes)
        self.assertEqual(TimelinePriorFinish.objects.count(), 0)

    def test_equal_percentages_may_use_either_place_order(self):
        forward = _tied_finishes(self.release)
        swapped = _swap_one_division(forward, self.release)
        other = _timeline(self.release, 'either order', self.profiles)

        self.assertEqual(
            store_prior_finishes(timeline_id=self.timeline.pk, finishes=forward),
            'created',
        )
        self.assertEqual(
            store_prior_finishes(timeline_id=other.pk, finishes=swapped),
            'created',
        )

    def test_exact_finish_rerun_writes_nothing(self):
        finishes = _default_finishes(self.release)
        store_prior_finishes(timeline_id=self.timeline.pk, finishes=finishes)

        status, writes = _writes_during(lambda: store_prior_finishes(
            timeline_id=self.timeline.pk,
            finishes=list(reversed(finishes)),
        ))

        self.assertEqual(status, 'unchanged')
        self.assertEqual(writes, [])

    def test_a_partial_or_inconsistent_set_stores_nothing(self):
        finishes = _default_finishes(self.release)
        with self.assertRaises(PriorFinishRefused):
            store_prior_finishes(timeline_id=self.timeline.pk, finishes=finishes[:-1])
        inconsistent = []
        demoted = False
        for finish in finishes:
            if not demoted and finish.division_place == 1:
                inconsistent.append(finish._replace(wins=0, losses=16, ties=0))
                demoted = True
                continue
            inconsistent.append(finish)
        with self.assertRaises(PriorFinishRefused):
            store_prior_finishes(timeline_id=self.timeline.pk, finishes=inconsistent)
        duplicated = _duplicate_place(finishes, self.release)
        with self.assertRaisesMessage(
            PriorFinishRefused,
            'A 2001 division place is outside that division.',
        ):
            store_prior_finishes(timeline_id=self.timeline.pk, finishes=duplicated)
        self.assertEqual(TimelinePriorFinish.objects.count(), 0)

    def test_the_database_rejects_a_duplicate_place(self):
        members = list(
            FranchiseSeasonMembership.objects.filter(
                franchise__release=self.release,
                season_year=2001,
                conference_slug='conference-1',
                division_slug='d1',
            ).select_related('franchise')[:2]
        )
        rows = [
            TimelinePriorFinish(
                timeline=self.timeline,
                franchise=member.franchise,
                season_year=2001,
                wins=15,
                losses=1,
                ties=0,
                division_place=1,
                conference_slug='conference-1',
                division_slug='d1',
            )
            for member in members
        ]
        with writing_transition_rows():
            for row in rows:
                row.full_clean()
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    TimelinePriorFinish.objects.bulk_create(rows)
        self.assertEqual(TimelinePriorFinish.objects.count(), 0)

    def test_a_changed_set_leaves_the_stored_finishes(self):
        finishes = _default_finishes(self.release)
        store_prior_finishes(timeline_id=self.timeline.pk, finishes=finishes)
        row = TimelinePriorFinish.objects.get(
            timeline=self.timeline,
            division_place=1,
            conference_slug='conference-1',
            division_slug='d1',
        )
        TimelinePriorFinish.objects.filter(pk=row.pk).update(wins=row.wins - 1)

        with self.assertRaises(PriorFinishRefused):
            store_prior_finishes(timeline_id=self.timeline.pk, finishes=finishes)

        row.refresh_from_db()
        self.assertEqual(row.wins, 14)

    def test_a_failed_finish_insert_rolls_back(self):
        with patch.object(
            TimelinePriorFinish.objects,
            'bulk_create',
            side_effect=IntegrityError('forced'),
        ):
            with self.assertRaises(PriorFinishRefused):
                store_prior_finishes(
                    timeline_id=self.timeline.pk,
                    finishes=_default_finishes(self.release),
                )
        self.assertEqual(TimelinePriorFinish.objects.count(), 0)
        season = TimelineSeason.objects.get(timeline=self.timeline, season_year=2001)
        self.assertTrue(season.regular_season_simulated)

    def test_ordinary_save_cannot_freeze_one_finish(self):
        franchise = UniverseFranchise.objects.get(
            release=self.release,
            placeholder_slug='founding-01',
        )
        membership = FranchiseSeasonMembership.objects.get(
            franchise=franchise,
            season_year=2001,
        )
        with self.assertRaises(ValidationError):
            TimelinePriorFinish(
                timeline=self.timeline,
                franchise=franchise,
                season_year=2001,
                wins=15,
                losses=1,
                ties=0,
                division_place=1,
                conference_slug=membership.conference_slug,
                division_slug=membership.division_slug,
            ).save()
        self.assertEqual(TimelinePriorFinish.objects.count(), 0)
        store_prior_finishes(
            timeline_id=self.timeline.pk,
            finishes=_default_finishes(self.release),
        )
        stored = TimelinePriorFinish.objects.get(franchise=franchise)
        original = stored.wins
        stored.wins = original - 1
        with self.assertRaises(ValidationError):
            stored.save()
        stored.refresh_from_db()
        self.assertEqual(stored.wins, original)
        expansion = UniverseFranchise.objects.get(
            release=self.release,
            placeholder_slug='expansion-2002-01',
        )
        with self.assertRaises(PriorFinishRefused):
            store_prior_finishes(
                timeline_id=_timeline(self.release, 'expansion finish', self.profiles).pk,
                finishes=[Finish(expansion.pk, 15, 1, 0, 1)],
            )

    def test_ranks_use_percentage_and_leave_the_expansion_club_last(self):
        store_prior_finishes(
            timeline_id=self.timeline.pk,
            finishes=_default_finishes(self.release),
        )
        TimelineSeason.objects.filter(
            timeline=self.timeline,
            season_year=2002,
        ).delete()

        status = derive_scheduling_ranks(timeline_id=self.timeline.pk)

        self.assertEqual(status, 'created')
        ranks = _ranks(self.timeline, 'conference-2', 'd4')
        records = _records(self.timeline)
        grouped_ranks = {}
        for row in TimelineSchedulingRank.objects.filter(timeline=self.timeline):
            grouped_ranks.setdefault(
                (row.conference_slug, row.division_slug),
                [],
            ).append(row.scheduling_rank)
        self.assertEqual(len(grouped_ranks), 8)
        for values in grouped_ranks.values():
            self.assertEqual(sorted(values), [1, 2, 3, 4])
        self.assertEqual(ranks['expansion-2002-01'], 4)
        self.assertLess(ranks['expansion-1995-02'], ranks['founding-21'])
        self.assertEqual(records['founding-21'][:3], records['founding-23'][:3])
        self.assertGreater(records['expansion-1995-02'][0], records['founding-21'][0])
        self.assertEqual(TimelinePriorFinish.objects.filter(timeline=self.timeline).count(), 31)
        self.assertEqual(_identity(self.release), self.identity)

    def test_the_persisted_draw_is_the_same_on_two_timelines(self):
        finishes = _default_finishes(self.release)
        other = _timeline(self.release, 'same finishes', self.profiles)
        store_prior_finishes(timeline_id=self.timeline.pk, finishes=finishes)
        store_prior_finishes(timeline_id=other.pk, finishes=finishes)

        derive_scheduling_ranks(timeline_id=self.timeline.pk)
        derive_scheduling_ranks(timeline_id=other.pk)

        left = _draw_rows(self.timeline)
        right = _draw_rows(other)
        self.assertTrue(left)
        self.assertEqual(
            [_draw_audit(row) for row in left],
            [_draw_audit(row) for row in right],
        )
        self.assertEqual(
            {row.pk for row in left}.isdisjoint({row.pk for row in right}),
            True,
        )
        draw = next(
            row for row in left
            if row.conference_slug == 'conference-2' and row.division_slug == 'd4'
        )
        self.assertRegex(
            draw.coordinate,
            r'^scheduling-rank-2002:conference-2:d4:\d+:[a-z0-9,-]+:\d+$',
        )
        self.assertIn('founding-21', draw.coordinate)
        self.assertIn('founding-23', draw.coordinate)
        self.assertNotIn('expansion-2002-01', draw.coordinate)
        self.assertNotRegex(draw.coordinate, rf'(^|:){self.timeline.pk}(:|$)')
        self.assertEqual(draw.digest, _hex_digest(self.release.seed, draw.coordinate))
        self.assertEqual(
            draw.digest,
            hmac.new(
                str(self.release.seed).encode('utf-8'),
                draw.coordinate.encode('utf-8'),
                hashlib.sha256,
            ).hexdigest(),
        )
        self.assertIn('founding-21', draw.candidates)
        self.assertIn('founding-23', draw.candidates)
        chains = [item.split(':') for item in draw.candidates.split('|')]
        self.assertEqual(len(chains), 2)
        choice = _index_from_digest(draw.digest, 2)
        expected_chain = ':'.join(chains[choice][:2])
        expected_slug = chains[choice][-1]
        self.assertEqual(draw.chosen_slug, expected_slug)
        self.assertEqual(draw.chosen_chain, expected_chain)
        self.assertIn(draw.chosen_slug, draw.outcome)
        self.assertEqual(_rank_pairs(self.timeline), _rank_pairs(other))

        def sample(*_args, **_kwargs):
            raise AssertionError('An exact rerun sampled a new draw.')

        with patch('timelines.services.prior_finish._resolving_draw', side_effect=sample):
            status, writes = _writes_during(lambda: derive_scheduling_ranks(
                timeline_id=self.timeline.pk,
            ))
        self.assertEqual(status, 'unchanged')
        self.assertEqual(writes, [])
        self.assertEqual(_identity(self.release), self.identity)

    def test_the_draw_swaps_only_the_tied_clubs(self):
        store_prior_finishes(
            timeline_id=self.timeline.pk,
            finishes=_default_finishes(self.release),
        )

        def forced(choice):
            def fake(_seed, label, modulus, draw=None):
                picked = choice if choice >= 0 else modulus - 1
                message = f'{label}:0'
                digest = picked.to_bytes(8, 'big') + bytes(24)
                return message, digest.hex(), picked
            return fake

        with patch(
            'timelines.services.prior_finish._resolving_draw',
            side_effect=forced(0),
        ):
            derive_scheduling_ranks(timeline_id=self.timeline.pk)
        first = _ranks(self.timeline, 'conference-2', 'd4')
        TimelineSchedulingRank.objects.filter(timeline=self.timeline).delete()
        TimelineSchedulingDraw.objects.filter(timeline=self.timeline).delete()
        with patch(
            'timelines.services.prior_finish._resolving_draw',
            side_effect=forced(-1),
        ):
            derive_scheduling_ranks(timeline_id=self.timeline.pk)
        second = _ranks(self.timeline, 'conference-2', 'd4')

        self.assertEqual(first['expansion-1995-02'], 1)
        self.assertEqual(second['expansion-1995-02'], 1)
        self.assertEqual(first['expansion-2002-01'], 4)
        self.assertEqual(second['expansion-2002-01'], 4)
        self.assertEqual((first['founding-21'], first['founding-23']), (2, 3))
        self.assertEqual((second['founding-21'], second['founding-23']), (3, 2))

    def test_a_better_outsider_ranks_between_a_2001_division_chain(self):
        overrides = {
            'founding-10': (10, 6, 0, 1),
            'founding-12': (8, 8, 0, 2),
            'founding-25': (6, 10, 0, 3),
            'founding-09': (4, 12, 0, 4),
            'founding-11': (2, 14, 0, 5),
            'expansion-1976-01': (9, 7, 0, 1),
            'founding-05': (7, 9, 0, 2),
            'founding-06': (5, 11, 0, 3),
            'founding-07': (3, 13, 0, 4),
            'founding-08': (1, 15, 0, 5),
        }
        store_prior_finishes(
            timeline_id=self.timeline.pk,
            finishes=_default_finishes(self.release, overrides),
        )

        derive_scheduling_ranks(timeline_id=self.timeline.pk)

        ranks = _ranks(self.timeline, 'conference-1', 'd3')
        self.assertEqual(
            [slug for slug, _rank in sorted(ranks.items(), key=lambda item: item[1])],
            ['founding-10', 'expansion-1976-01', 'founding-12', 'founding-25'],
        )
        self.assertEqual(_identity(self.release), self.identity)

    def test_tied_place_order_holds_for_clubs_that_share_a_2001_division(self):
        store_prior_finishes(
            timeline_id=self.timeline.pk,
            finishes=_tied_finishes(self.release),
        )

        def last(_seed, _label, modulus, draw=None):
            message = f'{_label}:0'
            picked = modulus - 1
            digest = picked.to_bytes(8, 'big') + bytes(24)
            return message, digest.hex(), picked

        with patch('timelines.services.prior_finish._resolving_draw', side_effect=last):
            derive_scheduling_ranks(timeline_id=self.timeline.pk)

        finishes = {
            row.franchise.placeholder_slug: row
            for row in TimelinePriorFinish.objects.filter(
                timeline=self.timeline,
            ).select_related('franchise')
        }
        ranks = _ranks(self.timeline, 'conference-1', 'd4')
        by_division = {}
        for slug in ranks:
            row = finishes[slug]
            by_division.setdefault((row.conference_slug, row.division_slug), []).append(slug)
        self.assertEqual(len(by_division), 2)
        for slugs in by_division.values():
            ordered = sorted(slugs, key=lambda slug: finishes[slug].division_place)
            self.assertLess(ranks[ordered[0]], ranks[ordered[1]])

    def test_a_partial_or_changed_rank_set_is_left_in_place(self):
        store_prior_finishes(
            timeline_id=self.timeline.pk,
            finishes=_default_finishes(self.release),
        )
        derive_scheduling_ranks(timeline_id=self.timeline.pk)
        row = TimelineSchedulingRank.objects.filter(timeline=self.timeline).order_by('pk').first()
        TimelineSchedulingRank.objects.filter(pk=row.pk).delete()

        with self.assertRaises(PriorFinishRefused):
            derive_scheduling_ranks(timeline_id=self.timeline.pk)

        self.assertEqual(
            TimelineSchedulingRank.objects.filter(timeline=self.timeline).count(),
            31,
        )
        self.assertFalse(TimelineSchedulingRank.objects.filter(pk=row.pk).exists())

        fresh = _timeline(self.release, 'changed draw', self.profiles)
        store_prior_finishes(
            timeline_id=fresh.pk,
            finishes=_default_finishes(self.release),
        )
        derive_scheduling_ranks(timeline_id=fresh.pk)
        draw = TimelineSchedulingDraw.objects.filter(timeline=fresh).order_by('pk').first()
        TimelineSchedulingDraw.objects.filter(pk=draw.pk).update(chosen_slug='tampered')
        with self.assertRaises(PriorFinishRefused):
            derive_scheduling_ranks(timeline_id=fresh.pk)
        draw.refresh_from_db()
        self.assertEqual(draw.chosen_slug, 'tampered')
        self.assertEqual(
            TimelineSchedulingRank.objects.filter(timeline=fresh).count(),
            32,
        )

        missing = _timeline(self.release, 'missing draw', self.profiles)
        store_prior_finishes(
            timeline_id=missing.pk,
            finishes=_default_finishes(self.release),
        )
        derive_scheduling_ranks(timeline_id=missing.pk)
        gone = TimelineSchedulingDraw.objects.filter(timeline=missing).order_by('pk').first()
        gone_id = gone.pk
        gone.delete()
        with self.assertRaises(PriorFinishRefused):
            derive_scheduling_ranks(timeline_id=missing.pk)
        self.assertFalse(TimelineSchedulingDraw.objects.filter(pk=gone_id).exists())
        self.assertEqual(
            TimelineSchedulingRank.objects.filter(timeline=missing).count(),
            32,
        )

    def test_a_failed_rank_insert_rolls_back_the_draw(self):
        store_prior_finishes(
            timeline_id=self.timeline.pk,
            finishes=_default_finishes(self.release),
        )
        with patch.object(
            TimelineSchedulingRank.objects,
            'bulk_create',
            side_effect=IntegrityError('forced'),
        ):
            with self.assertRaises(PriorFinishRefused):
                derive_scheduling_ranks(timeline_id=self.timeline.pk)
        self.assertEqual(TimelineSchedulingRank.objects.count(), 0)
        self.assertEqual(TimelineSchedulingDraw.objects.count(), 0)
        self.assertEqual(TimelinePriorFinish.objects.filter(timeline=self.timeline).count(), 31)

    def test_an_inactive_timeline_is_refused(self):
        Timeline.objects.filter(pk=self.timeline.pk).update(
            status=Timeline.Status.ARCHIVED,
            completed_at=timezone.now(),
        )
        with self.assertRaises(PriorFinishRefused):
            store_prior_finishes(
                timeline_id=self.timeline.pk,
                finishes=_default_finishes(self.release),
            )
        self.assertEqual(TimelinePriorFinish.objects.count(), 0)


def _timeline(release, name, profiles, include_2001=True, simulated=True):
    timeline = Timeline(
        release=release,
        name=name,
        start_season_year=2001 if include_2001 else 2002,
        current_season_year=2002,
        season_index=2 if include_2001 else 1,
        phase=Timeline.Phase.DIRECTION,
        status=Timeline.Status.ACTIVE,
    )
    timeline.save()
    years = (2001, 2002) if include_2001 else (2002,)
    for year in years:
        profile = next(
            item for item in profiles
            if item.first_year <= year <= item.last_year
        )
        complete = year == 2001 and include_2001 and simulated
        TimelineSeason(
            timeline=timeline,
            profile=profile,
            season_year=year,
            season_index=year - timeline.start_season_year + 1,
            phase=Timeline.Phase.DIRECTION,
            setup_complete=complete,
            directives_locked=complete,
            regular_season_simulated=complete,
        ).save()
    return timeline


def _default_finishes(release, overrides=None):
    overrides = overrides or {}
    grouped = {}
    memberships = FranchiseSeasonMembership.objects.filter(
        franchise__release=release,
        season_year=2001,
    ).select_related('franchise')
    for row in memberships:
        grouped.setdefault((row.conference_slug, row.division_slug), []).append(row)
    finishes = []
    for members in grouped.values():
        slugs = {row.franchise.placeholder_slug for row in members}
        if slugs & set(overrides):
            if not slugs <= set(overrides):
                raise AssertionError('An override must cover the whole 2001 division.')
            for row in members:
                wins, losses, ties, place = overrides[row.franchise.placeholder_slug]
                finishes.append(Finish(
                    row.franchise_id, wins, losses, ties, place,
                ))
            continue
        ordered = sorted(members, key=lambda row: row.franchise.placeholder_slug)
        for place, row in enumerate(ordered, start=1):
            finishes.append(Finish(
                row.franchise_id, 16 - place, place, 0, place,
            ))
    return finishes


def _tied_finishes(release):
    finishes = []
    memberships = FranchiseSeasonMembership.objects.filter(
        franchise__release=release,
        season_year=2001,
    ).select_related('franchise')
    grouped = {}
    for row in memberships:
        grouped.setdefault((row.conference_slug, row.division_slug), []).append(row)
    for members in grouped.values():
        ordered = sorted(members, key=lambda row: row.franchise.placeholder_slug)
        for place, row in enumerate(ordered, start=1):
            finishes.append(Finish(row.franchise_id, 8, 8, 0, place))
    return finishes


def _swap_one_division(finishes, release):
    members = list(
        FranchiseSeasonMembership.objects.filter(
            franchise__release=release,
            season_year=2001,
            conference_slug='conference-2',
            division_slug='d1',
        )
    )
    by_franchise = {row.franchise_id: row for row in members}
    first = next(item for item in finishes if item.franchise_id in by_franchise and item.division_place == 1)
    second = next(item for item in finishes if item.franchise_id in by_franchise and item.division_place == 2)
    swapped = []
    for item in finishes:
        if item.franchise_id == first.franchise_id:
            swapped.append(item._replace(division_place=2))
        elif item.franchise_id == second.franchise_id:
            swapped.append(item._replace(division_place=1))
        else:
            swapped.append(item)
    return swapped


def _duplicate_place(finishes, release):
    members = list(
        FranchiseSeasonMembership.objects.filter(
            franchise__release=release,
            season_year=2001,
            conference_slug='conference-1',
            division_slug='d1',
        )
    )
    ids = {row.franchise_id for row in members}
    targets = [item for item in finishes if item.franchise_id in ids][:2]
    replaced = {item.franchise_id: item._replace(division_place=1) for item in targets}
    return [replaced.get(item.franchise_id, item) for item in finishes]


def _identity(release):
    release.refresh_from_db()
    memberships = tuple(
        FranchiseSeasonMembership.objects.filter(franchise__release=release)
        .order_by('pk')
        .values_list(
            'pk',
            'franchise_id',
            'season_year',
            'conference_slug',
            'division_slug',
            'slot_order',
        )
    )
    franchises = tuple(
        UniverseFranchise.objects.filter(release=release)
        .order_by('pk')
        .values_list('pk', 'placeholder_slug', 'origin')
    )
    pairings = tuple(
        OpponentPairing.objects.filter(release=release)
        .order_by('pk')
        .values_list(
            'pk',
            'season_year',
            'home_franchise_id',
            'away_franchise_id',
            'kind',
        )
    )
    weeks = tuple(
        OpponentWeekAssignment.objects.filter(release=release)
        .order_by('pk')
        .values_list('pk', 'pairing_id', 'week')
    )
    return (
        memberships,
        franchises,
        pairings,
        weeks,
        release.catalog_fingerprint,
        release.validation_fingerprint,
    )


def _records(timeline):
    return {
        row.franchise.placeholder_slug: (
            row.wins,
            row.losses,
            row.ties,
            row.division_place,
        )
        for row in TimelinePriorFinish.objects.filter(
            timeline=timeline,
        ).select_related('franchise')
    }


def _ranks(timeline, conference, division):
    return {
        row.franchise.placeholder_slug: row.scheduling_rank
        for row in TimelineSchedulingRank.objects.filter(
            timeline=timeline,
            conference_slug=conference,
            division_slug=division,
        ).select_related('franchise')
    }


def _rank_pairs(timeline):
    return set(
        TimelineSchedulingRank.objects.filter(timeline=timeline).values_list(
            'franchise_id',
            'scheduling_rank',
            'conference_slug',
            'division_slug',
        )
    )


def _draw_rows(timeline):
    return list(
        TimelineSchedulingDraw.objects.filter(timeline=timeline).order_by(
            'conference_slug',
            'division_slug',
            'draw_step',
        )
    )


def _draw_audit(row):
    return (
        row.season_year,
        row.conference_slug,
        row.division_slug,
        row.draw_step,
        row.candidates,
        row.coordinate,
        row.digest,
        row.chosen_slug,
        row.chosen_chain,
        row.outcome,
    )
