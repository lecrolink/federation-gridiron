from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models.query import QuerySet
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from leagues.models import League
from timelines.models import Timeline, TimelineDivisionPlace, TimelineSeason
from timelines.services.division_place import (
    DivisionPlaceRefused,
    record_division_places,
)
from universe.generation.opening_players import y1970_values
from universe.models import (
    FranchiseSeasonMembership,
    HistoricalStanding,
    OpponentPairing,
    OpponentWeekAssignment,
    UniverseFranchise,
    UniverseRelease,
)
from universe.rules import EraRuleProfile


class DivisionPlaceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.league = League.objects.create(
            name='Division Place League',
            slug='division-place-league',
            tier=1,
            active=True,
        )
        cls.release = UniverseRelease.objects.create(
            slug='fg-division-place',
            root_league=cls.league,
            generator_version='fg-founding-2',
            seed=1970,
            founding_franchise_count=5,
            expansion_schedule_version=1,
            first_season_year=1970,
            season_count=60,
            last_season_year=2029,
            status=UniverseRelease.Status.DRAFT,
        )
        cls.other_release = UniverseRelease.objects.create(
            slug='fg-division-place-other',
            root_league=cls.league,
            generator_version='fg-founding-2',
            seed=1971,
            founding_franchise_count=1,
            expansion_schedule_version=1,
            first_season_year=1970,
            season_count=60,
            last_season_year=2029,
            status=UniverseRelease.Status.DRAFT,
        )
        specs = (
            ('founding-01', 'conference-1', 'd1', 4),
            ('founding-02', 'conference-1', 'd1', 2),
            ('founding-03', 'conference-1', 'd1', 3),
            ('founding-04', 'conference-1', 'd1', 1),
            ('founding-05', 'conference-1', 'd2', 1),
            ('founding-07', 'conference-1', 'd3', 1),
        )
        cls.franchises = {}
        for slug, conference, division, slot in specs:
            franchise = UniverseFranchise.objects.create(
                release=cls.release,
                placeholder_slug=slug,
                display_name=slug,
                origin=UniverseFranchise.Origin.FOUNDING,
                first_active_season=1970,
            )
            FranchiseSeasonMembership.objects.create(
                franchise=franchise,
                season_year=1970,
                conference_slug=conference,
                division_slug=division,
                slot_order=slot,
                owner_selectable=True,
            )
            cls.franchises[slug] = franchise
        later = {
            'founding-01': ('conference-1', 'd1', 2),
            'founding-02': ('conference-1', 'd2', 1),
            'founding-03': ('conference-1', 'd1', 3),
            'founding-04': ('conference-1', 'd1', 1),
            'founding-05': ('conference-1', 'd1', 4),
            'founding-07': ('conference-1', 'd3', 1),
        }
        for slug, (conference, division, slot) in later.items():
            FranchiseSeasonMembership.objects.create(
                franchise=cls.franchises[slug],
                season_year=1971,
                conference_slug=conference,
                division_slug=division,
                slot_order=slot,
                owner_selectable=True,
            )
        cls.outsider = UniverseFranchise.objects.create(
            release=cls.other_release,
            placeholder_slug='founding-01',
            display_name='founding-01',
            origin=UniverseFranchise.Origin.FOUNDING,
            first_active_season=1970,
        )
        cls.unseated = UniverseFranchise.objects.create(
            release=cls.release,
            placeholder_slug='founding-06',
            display_name='founding-06',
            origin=UniverseFranchise.Origin.FOUNDING,
            first_active_season=1970,
        )
        EraRuleProfile(release=cls.release, **y1970_values()).save()
        cls.profile = cls.release.era_profiles.get(profile_key='y1970')
        pairing = OpponentPairing.objects.create(
            release=cls.release,
            season_year=1970,
            home_franchise=cls.franchises['founding-04'],
            away_franchise=cls.franchises['founding-02'],
            kind=OpponentPairing.Kind.DIVISION,
        )
        OpponentWeekAssignment.objects.create(
            release=cls.release,
            pairing=pairing,
            week=1,
        )
        cls.release.status = UniverseRelease.Status.PUBLISHED
        cls.release.catalog_fingerprint = 'a' * 64
        cls.release.validation_fingerprint = 'b' * 64
        cls.release.published_at = timezone.now()
        cls.release.save()
        cls.harbor = cls._timeline('Harbor')
        cls.inlet = cls._timeline('Inlet')
        cls.harbor_season = cls._season(cls.harbor)
        cls.inlet_season = cls._season(cls.inlet)
        cls.base_records = {
            'founding-02': (10, 6, 0),
            'founding-04': (8, 8, 0),
            'founding-01': (8, 8, 0),
            'founding-03': (4, 12, 0),
            'founding-05': (12, 2, 0),
            'founding-07': (0, 0, 0),
        }
        cls.pairing_rows = _table(OpponentPairing)
        cls.week_rows = _table(OpponentWeekAssignment)
        cls.franchise_rows = _table(UniverseFranchise)
        cls.membership_rows = _table(FranchiseSeasonMembership)
        cls.historical_rows = _table(HistoricalStanding)
        cls.release_rows = _table(UniverseRelease)

    @classmethod
    def _timeline(cls, name):
        return Timeline.objects.create(
            release=cls.release,
            name=name,
            start_season_year=1970,
            current_season_year=1970,
            season_index=1,
            phase=Timeline.Phase.DIRECTION,
            status=Timeline.Status.ACTIVE,
        )

    @classmethod
    def _season(cls, timeline):
        return TimelineSeason.objects.create(
            timeline=timeline,
            profile=cls.profile,
            season_year=1970,
            season_index=1,
            phase=Timeline.Phase.DIRECTION,
        )

    def _records(self, mapping):
        return [
            {
                'franchise_id': self.franchises[slug].pk,
                'wins': wins,
                'losses': losses,
                'ties': ties,
            }
            for slug, (wins, losses, ties) in mapping.items()
        ]

    def _close(self, timeline, mapping=None):
        return record_division_places(
            timeline_id=timeline.pk,
            season_year=1970,
            records=self._records(mapping or self.base_records),
        )

    def _places(self, timeline):
        return {
            row.franchise.placeholder_slug: row.division_place
            for row in TimelineDivisionPlace.objects.filter(timeline=timeline)
        }

    def _assert_shared_rows(self):
        self.assertEqual(_table(OpponentPairing), self.pairing_rows)
        self.assertEqual(_table(OpponentWeekAssignment), self.week_rows)
        self.assertEqual(_table(UniverseFranchise), self.franchise_rows)
        self.assertEqual(_table(FranchiseSeasonMembership), self.membership_rows)
        self.assertEqual(_table(HistoricalStanding), self.historical_rows)
        self.assertEqual(_table(UniverseRelease), self.release_rows)

    def test_two_timelines_rank_the_same_franchises_differently(self):
        calls = []
        real_lock = QuerySet.select_for_update

        def spy(qs, *args, **kwargs):
            calls.append(qs.model)
            return real_lock(qs, *args, **kwargs)

        reversed_records = dict(self.base_records)
        reversed_records['founding-02'] = (4, 12, 0)
        reversed_records['founding-03'] = (10, 6, 0)
        with patch.object(QuerySet, 'select_for_update', spy):
            harbor = self._close(self.harbor)
        inlet = self._close(self.inlet, reversed_records)
        self.assertEqual(harbor.outcome, 'recorded')
        self.assertEqual(inlet.outcome, 'recorded')
        self.assertEqual(calls[0], UniverseRelease)
        self.assertLess(calls.index(Timeline), calls.index(TimelineSeason))
        self.assertLess(calls.index(TimelineSeason), calls.index(TimelineDivisionPlace))
        self.assertEqual(
            self._places(self.harbor),
            {
                'founding-02': 1,
                'founding-04': None,
                'founding-01': None,
                'founding-03': 4,
                'founding-05': 1,
                'founding-07': None,
            },
        )
        self.assertEqual(
            self._places(self.inlet),
            {
                'founding-02': 4,
                'founding-04': None,
                'founding-01': None,
                'founding-03': 1,
                'founding-05': 1,
                'founding-07': None,
            },
        )
        harbor_ids = set(
            TimelineDivisionPlace.objects.filter(timeline=self.harbor).values_list(
                'franchise_id',
                flat=True,
            )
        )
        inlet_ids = set(
            TimelineDivisionPlace.objects.filter(timeline=self.inlet).values_list(
                'franchise_id',
                flat=True,
            )
        )
        self.assertEqual(harbor_ids, inlet_ids)
        self.assertEqual(harbor_ids, {row.pk for row in self.franchises.values()})
        for season in (self.harbor_season, self.inlet_season):
            season.refresh_from_db()
            self.assertEqual(season.phase, Timeline.Phase.DIRECTION)
            self.assertFalse(season.setup_complete)
            self.assertFalse(season.directives_locked)
            self.assertFalse(season.regular_season_simulated)
            self.assertFalse(season.postseason_complete)
        self._assert_shared_rows()

    def test_shared_percentage_leaves_the_tie_and_keeps_other_divisions(self):
        self._close(self.harbor)
        places = self._places(self.harbor)
        self.assertIsNone(places['founding-01'])
        self.assertIsNone(places['founding-04'])
        self.assertEqual(places['founding-02'], 1)
        self.assertEqual(places['founding-03'], 4)
        self.assertEqual(places['founding-05'], 1)
        self.assertIsNone(places['founding-07'])
        self._assert_shared_rows()

    def test_equal_percentages_with_different_records_stay_unresolved(self):
        mapping = {
            'founding-01': (1, 0, 0),
            'founding-04': (2, 0, 0),
            'founding-02': (8, 8, 0),
            'founding-03': (7, 7, 2),
            'founding-05': (3, 1, 0),
            'founding-07': (0, 0, 0),
        }
        self._close(self.harbor, mapping)
        places = self._places(self.harbor)
        self.assertIsNone(places['founding-01'])
        self.assertIsNone(places['founding-04'])
        self.assertIsNone(places['founding-02'])
        self.assertIsNone(places['founding-03'])
        self.assertEqual(places['founding-05'], 1)
        self.assertIsNone(places['founding-07'])

    def test_place_belongs_to_the_season_that_produced_the_record(self):
        mapping = {
            'founding-04': (9, 5, 0),
            'founding-01': (8, 6, 0),
            'founding-03': (7, 7, 0),
            'founding-02': (1, 13, 0),
            'founding-05': (14, 0, 0),
            'founding-07': (0, 0, 0),
        }
        recorded = self._close(self.harbor, mapping)
        self.assertEqual(recorded.season_id, self.harbor_season.pk)
        self.assertEqual(self._places(self.harbor)['founding-02'], 4)
        self.assertEqual(self._places(self.harbor)['founding-05'], 1)
        later = FranchiseSeasonMembership.objects.get(
            franchise=self.franchises['founding-02'],
            season_year=1971,
        )
        self.assertEqual(later.division_slug, 'd2')
        self.assertEqual(later.slot_order, 1)
        self.assertFalse(
            TimelineDivisionPlace.objects.filter(season__season_year=1971).exists()
        )
        self.assertEqual(
            TimelineDivisionPlace.objects.get(
                timeline=self.harbor,
                franchise=self.franchises['founding-02'],
            ).season_id,
            self.harbor_season.pk,
        )
        self._assert_shared_rows()

    def test_missing_extra_duplicate_invalid_and_cross_release_records_are_rejected(self):
        cases = {
            'missing': self._records(self.base_records)[:-1],
            'extra': self._records(self.base_records) + [
                {
                    'franchise_id': self.unseated.pk,
                    'wins': 1,
                    'losses': 0,
                    'ties': 0,
                },
            ],
            'duplicate': self._records(self.base_records) + [
                self._records(self.base_records)[0],
            ],
            'negative': [
                {**record, 'wins': -1} if index == 0 else record
                for index, record in enumerate(self._records(self.base_records))
            ],
            'boolean': [
                {**record, 'ties': True} if index == 0 else record
                for index, record in enumerate(self._records(self.base_records))
            ],
            'cross-release': [
                {**record, 'franchise_id': self.outsider.pk} if index == 0 else record
                for index, record in enumerate(self._records(self.base_records))
            ],
        }
        expected = {
            'missing': 'A membership this season has no record.',
            'extra': 'A record names a franchise that has no membership this season.',
            'duplicate': 'A franchise is repeated.',
            'negative': 'Wins, losses, and ties are nonnegative whole numbers.',
            'boolean': 'Wins, losses, and ties are nonnegative whole numbers.',
            'cross-release': 'A record names a franchise from another release.',
        }
        for name, records in cases.items():
            with self.subTest(name=name):
                with self.assertRaises(DivisionPlaceRefused) as caught:
                    record_division_places(
                        timeline_id=self.harbor.pk,
                        season_year=1970,
                        records=records,
                    )
                self.assertEqual(str(caught.exception), expected[name])
                self.assertFalse(
                    TimelineDivisionPlace.objects.filter(timeline=self.harbor).exists()
                )
        self._assert_shared_rows()

    def test_exact_rerun_writes_nothing_and_other_reruns_fail_closed(self):
        created = self._close(self.harbor)
        self.assertEqual(created.outcome, 'recorded')
        stored = _table(TimelineDivisionPlace)
        with CaptureQueriesContext(connection) as captured:
            again = self._close(self.harbor)
        self.assertEqual(again.outcome, 'unchanged')
        self.assertEqual(_write_sql(captured.captured_queries), [])
        self.assertEqual(_table(TimelineDivisionPlace), stored)
        changed = dict(self.base_records)
        changed['founding-02'] = (11, 5, 0)
        with self.assertRaises(DivisionPlaceRefused) as caught:
            self._close(self.harbor, changed)
        self.assertEqual(
            str(caught.exception),
            'The stored division places do not match this record.',
        )
        self.assertEqual(_table(TimelineDivisionPlace), stored)
        TimelineDivisionPlace.objects.filter(
            timeline=self.harbor,
            franchise=self.franchises['founding-02'],
        ).update(division_place=9)
        drifted = _table(TimelineDivisionPlace)
        with self.assertRaises(DivisionPlaceRefused):
            self._close(self.harbor)
        self.assertEqual(_table(TimelineDivisionPlace), drifted)
        self._assert_shared_rows()

    def test_partial_stored_state_fails_closed(self):
        TimelineDivisionPlace.objects.create(
            timeline=self.harbor,
            season=self.harbor_season,
            franchise=self.franchises['founding-05'],
            wins=12,
            losses=2,
            ties=0,
            division_place=1,
        )
        stored = _table(TimelineDivisionPlace)
        with self.assertRaises(DivisionPlaceRefused) as caught:
            self._close(self.harbor)
        self.assertEqual(
            str(caught.exception),
            'The stored division places are a partial set.',
        )
        self.assertEqual(_table(TimelineDivisionPlace), stored)
        self.assertEqual(TimelineDivisionPlace.objects.count(), 1)

    def test_a_recorded_place_cannot_be_saved_again(self):
        self._close(self.harbor)
        row = TimelineDivisionPlace.objects.get(
            timeline=self.harbor,
            franchise=self.franchises['founding-02'],
        )
        row.wins = 0
        with self.assertRaises(ValidationError):
            row.save()
        row.refresh_from_db()
        self.assertEqual(row.wins, 10)
        self.assertEqual(row.division_place, 1)

    def test_an_inactive_timeline_is_refused(self):
        self.inlet.completed_at = timezone.now()
        self.inlet.status = Timeline.Status.ARCHIVED
        self.inlet.save()
        with self.assertRaises(DivisionPlaceRefused) as caught:
            self._close(self.inlet)
        self.assertEqual(
            str(caught.exception),
            'An active timeline can record division places.',
        )
        self.assertFalse(
            TimelineDivisionPlace.objects.filter(timeline=self.inlet).exists()
        )
        self._assert_shared_rows()


def _table(model):
    names = [field.attname for field in model._meta.concrete_fields]
    return [
        tuple(row[name] for name in names)
        for row in model.objects.order_by('pk').values(*names)
    ]


def _write_sql(queries):
    writes = []
    for query in queries:
        sql = query['sql'].lstrip().upper()
        if sql.startswith(('INSERT', 'UPDATE', 'DELETE', 'REPLACE')):
            writes.append(sql)
    return writes
