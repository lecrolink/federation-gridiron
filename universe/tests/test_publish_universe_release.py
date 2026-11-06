import socket
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models.query import QuerySet
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from leagues.models import League
from timelines.models import Timeline
from universe.fingerprints import FingerprintSchemaError, release_digests
from universe.generation.opening_players import generate_opening_players
from universe.generation.player_rating_profiles import generate_player_ratings
from universe.generation.prospects import generate_prospects
from universe.generation.provisional_names import replace_provisional_value
from universe.generation.schedule import generate_schedule_release
from universe.management.commands.publish_universe_release import Command
from universe.models import (
    FranchiseHistoryEvent,
    FranchiseSeasonMembership,
    HistoricalStanding,
    ProvisionalFranchiseName,
    UniverseFranchise,
    UniverseRelease,
)
from universe.people import (
    DEFENSE_SCHEMES,
    OFFENSE_SCHEMES,
    College,
    CoachRatingProfile,
    GMRatingProfile,
    PlayerRatingProfile,
    UniverseCoach,
    UniverseGM,
    UniversePlayer,
    UniverseStaffPost,
)
from universe.publication_service import (
    REQUIRED_GATES,
    PublicationRefused,
    publish_universe_release,
)
from universe.release_guard import (
    OWNERSHIP_MESSAGE,
    assert_release_lock_held,
    holding_release_lock,
    persisted_release_id,
)
from universe.rules import (
    DraftClass,
    EraAllowedScheme,
    EraRuleProfile,
    ExpansionClub,
    ExpansionEvent,
    OpeningAssignment,
    OpeningContract,
)


SEED = 1970
CATALOG_PLACEHOLDER = 'smoke-catalog-fingerprint'
VALIDATION_PLACEHOLDER = 'smoke-validation-fingerprint'
WRITE_WORDS = ('INSERT', 'UPDATE', 'DELETE', 'REPLACE')

_ORIGINAL_CONNECT = socket.socket.connect


def _blocked_connect(self, address):
    raise AssertionError(f'Outbound connections are blocked: {address}')


def setUpModule():
    socket.socket.connect = _blocked_connect


def tearDownModule():
    socket.socket.connect = _ORIGINAL_CONNECT


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
        if sql.lstrip().split(None, 1)[0].upper() in WRITE_WORDS
    ]
    return result, writes


def _child_ids(release):
    return {
        'franchises': list(
            release.franchises.order_by('id').values_list('id', flat=True)
        ),
        'memberships': list(
            FranchiseSeasonMembership.objects.filter(
                franchise__release=release,
            ).order_by('id').values_list('id', flat=True)
        ),
        'players': list(
            UniversePlayer.objects.filter(release=release).order_by('id').values_list(
                'id', flat=True,
            )
        ),
        'assignments': list(
            OpeningAssignment.objects.filter(release=release).order_by('id').values_list(
                'id', flat=True,
            )
        ),
        'profiles': list(
            EraRuleProfile.objects.filter(release=release).order_by('id').values_list(
                'id', flat=True,
            )
        ),
        'classes': list(
            DraftClass.objects.filter(release=release).order_by('id').values_list(
                'id', flat=True,
            )
        ),
        'gms': list(
            UniverseGM.objects.filter(release=release).order_by('id').values_list(
                'id', flat=True,
            )
        ),
        'standings': list(
            HistoricalStanding.objects.filter(
                franchise__release=release,
            ).order_by('id').values_list('id', flat=True)
        ),
        'events': list(
            FranchiseHistoryEvent.objects.filter(
                franchise__release=release,
            ).order_by('id').values_list('id', flat=True)
        ),
    }


def _stamp(release):
    release.catalog_fingerprint = CATALOG_PLACEHOLDER
    release.validation_fingerprint = VALIDATION_PLACEHOLDER
    release.save(update_fields=['catalog_fingerprint', 'validation_fingerprint'])


class SqliteSettingsTests(SimpleTestCase):
    def test_settings_do_not_name_the_remote_database(self):
        database = settings.DATABASES['default']
        self.assertEqual(database['ENGINE'], 'django.db.backends.sqlite3')
        self.assertIn('memory', database['NAME'])
        self.assertNotIn('pythonanywhere', database['NAME'])
        self.assertNotIn('mysql', database['ENGINE'])

    def test_the_command_has_no_bypass_options(self):
        parser = Command().create_parser('manage.py', 'publish_universe_release')
        dests = {action.dest for action in parser._actions}
        self.assertIn('league_id', dests)
        self.assertIn('slug', dests)
        self.assertIn('seed', dests)
        self.assertIn('generator', dests)
        self.assertFalse(dests & {
            'force',
            'skip_validation',
            'repair',
            'unpublish',
            'fingerprint_only',
        })
        self.assertTrue(issubclass(Command, BaseCommand))


class PublicationServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.league = League.objects.create(
            name='Publication League',
            slug='publication-league',
            tier=1,
            active=True,
        )
        cls.release = UniverseRelease.objects.create(
            slug='fg-1970-1',
            root_league=cls.league,
            generator_version='pending',
            seed=1,
            founding_franchise_count=26,
            expansion_schedule_version=3,
            first_season_year=1970,
            season_count=60,
            last_season_year=2029,
            status=UniverseRelease.Status.DRAFT,
        )
        if generate_schedule_release(cls.league.id, SEED) != 'created':
            raise AssertionError('The schedule fixture was not created.')
        if generate_opening_players(cls.league.id, SEED) != 'created':
            raise AssertionError('The opening fixture was not created.')
        if generate_prospects(cls.league.id, SEED) != 'created':
            raise AssertionError('The prospect fixture was not created.')
        if generate_player_ratings(cls.league.id, SEED) != 'created':
            raise AssertionError('The rating fixture was not created.')
        cls.release.refresh_from_db()

    def _publish(self, **overrides):
        kwargs = {
            'slug': self.release.slug,
            'seed': SEED,
            'generator_version': 'fg-founding-2',
            'league_id': self.league.id,
        }
        kwargs.update(overrides)
        return publish_universe_release(**kwargs)

    def _assert_draft_preserved(self, catalog, validation):
        self.release.refresh_from_db()
        self.assertEqual(self.release.status, UniverseRelease.Status.DRAFT)
        self.assertIsNone(self.release.published_at)
        self.assertEqual(self.release.catalog_fingerprint, catalog)
        self.assertEqual(self.release.validation_fingerprint, validation)

    def test_a_blank_release_publishes_and_an_exact_rerun_is_unchanged(self):
        self.assertEqual(self.release.catalog_fingerprint, '')
        self.assertEqual(self.release.validation_fingerprint, '')
        self.assertEqual(self.release.status, UniverseRelease.Status.DRAFT)
        release_id = self.release.pk
        children = _child_ids(self.release)
        calls = []
        real_lock = QuerySet.select_for_update

        def spy(qs, *args, **kwargs):
            calls.append(qs.model)
            return real_lock(qs, *args, **kwargs)

        with patch.object(QuerySet, 'select_for_update', spy):
            result, writes = _writes_during(self._publish)
        self.assertEqual(calls, [UniverseRelease, UniverseRelease])
        self.assertTrue(writes)
        self.assertTrue(all(
            sql.lstrip().upper().startswith('UPDATE')
            and 'universe_universerelease' in sql
            for sql in writes
        ))
        joined = '\n'.join(writes).lower()
        for column in (
            'catalog_fingerprint',
            'validation_fingerprint',
            'status',
            'published_at',
        ):
            self.assertIn(column, joined)
        self.release.refresh_from_db()
        recomputed = release_digests(self.release)
        self.assertEqual(result.outcome, 'published')
        self.assertEqual(
            (self.release.catalog_fingerprint, self.release.validation_fingerprint),
            recomputed,
        )
        self.assertEqual(len(self.release.catalog_fingerprint), 64)
        self.assertEqual(self.release.catalog_fingerprint, self.release.catalog_fingerprint.lower())
        self.assertEqual(len(self.release.validation_fingerprint), 64)
        self.assertTrue(timezone.is_aware(self.release.published_at))
        self.assertEqual(self.release.status, UniverseRelease.Status.PUBLISHED)
        self.assertEqual(self.release.pk, release_id)
        report = result.report
        self.assertTrue(report.cap_and_fingerprint_checks_pass)
        self.assertTrue(report.publishable)
        self.assertEqual(report.required_unresolved, ())
        for name in REQUIRED_GATES:
            self.assertEqual(getattr(report, f'{name}_status'), 'pass', name)
        self.assertEqual(_child_ids(self.release), children)

        published_at = self.release.published_at
        catalog, validation = recomputed
        rerun, rerun_writes = _writes_during(self._publish)
        self.release.refresh_from_db()
        self.assertEqual(rerun.outcome, 'unchanged')
        self.assertEqual(rerun_writes, [])
        self.assertEqual(self.release.published_at, published_at)
        self.assertEqual(self.release.catalog_fingerprint, catalog)
        self.assertEqual(self.release.validation_fingerprint, validation)
        self.assertEqual(self.release.pk, release_id)
        self.assertEqual(_child_ids(self.release), children)

    def test_each_failed_gate_preserves_the_draft_fingerprints(self):
        franchise = self.release.franchises.get(placeholder_slug='founding-01')
        starter = OpeningAssignment.objects.select_related('player').get(
            release=self.release,
            franchise=franchise,
            role='starter',
            player__position='QB',
        )
        other = OpeningAssignment.objects.filter(
            release=self.release,
            franchise=franchise,
            role__in=('starter', 'backup'),
            player__position='QB',
        ).exclude(pk=starter.pk).order_by('id').first()
        self.assertIsNotNone(other)
        gms = list(UniverseGM.objects.filter(release=self.release).order_by('id')[:2])
        self.assertEqual(len(gms), 2)
        membership_id = FranchiseSeasonMembership.objects.filter(
            franchise__release=self.release,
        ).order_by('id').values_list('id', flat=True).first()
        cases = (
            (
                'cap',
                lambda: EraRuleProfile.objects.filter(
                    release=self.release,
                    profile_key='y1995',
                ).update(cap_limit=None),
            ),
            (
                'horizon',
                lambda: UniverseRelease.objects.filter(pk=self.release.pk).update(
                    founding_franchise_count=25,
                ),
            ),
            (
                'expansion',
                lambda: ExpansionEvent.objects.filter(
                    release=self.release,
                    season_year=1976,
                ).update(expansion_draft_order_rule=''),
            ),
            (
                'membership',
                lambda: FranchiseSeasonMembership.objects.filter(
                    pk=membership_id,
                ).update(owner_selectable=False),
            ),
            (
                'quota',
                lambda: EraRuleProfile.objects.filter(
                    release=self.release,
                    profile_key='y1970',
                ).update(draft_rounds=16),
            ),
            (
                'population',
                lambda: DraftClass.objects.filter(
                    release=self.release,
                    season_year=1970,
                ).update(rounds=16, draftable_count=416),
            ),
            (
                'staff_signature',
                lambda: UniverseGM.objects.filter(pk=gms[1].pk).update(
                    strength_1=gms[0].strength_1,
                    strength_2=gms[0].strength_2,
                    weakness=gms[0].weakness,
                    trait_1=gms[0].trait_1,
                    trait_2=gms[0].trait_2,
                ),
            ),
            (
                'history_events',
                lambda: FranchiseHistoryEvent.objects.create(
                    franchise=franchise,
                    season_year=1970,
                    kind=FranchiseHistoryEvent.Kind.RENAME,
                    previous_name='Founding franchise 01',
                    new_name='Renamed franchise 01',
                ),
            ),
            (
                'opening_depth',
                lambda: OpeningAssignment.objects.filter(pk=other.pk).update(
                    depth=starter.depth,
                ),
            ),
            (
                'draft_order',
                lambda: HistoricalStanding.objects.create(
                    franchise=franchise,
                    season_year=1969,
                    wins=0,
                    losses=0,
                    ties=0,
                ),
            ),
        )
        for gate, mutate in cases:
            with self.subTest(gate=gate):
                with transaction.atomic():
                    _stamp(self.release)
                    mutate()
                    with self.assertRaises(PublicationRefused) as caught:
                        self._publish()
                    self.assertEqual(caught.exception.failed_gates, (gate,))
                    self.assertIn(gate, str(caught.exception))
                    self._assert_draft_preserved(
                        CATALOG_PLACEHOLDER,
                        VALIDATION_PLACEHOLDER,
                    )
                    transaction.set_rollback(True)

    def test_fingerprint_schema_failure_preserves_the_draft(self):
        _stamp(self.release)
        with patch(
            'universe.publication_service.release_digests',
            side_effect=FingerprintSchemaError('bad record'),
        ):
            with self.assertRaises(PublicationRefused) as caught:
                self._publish()
        self.assertEqual(caught.exception.failed_gates, ('fingerprint_schema',))
        self.assertIn('fingerprint_schema', str(caught.exception))
        self._assert_draft_preserved(CATALOG_PLACEHOLDER, VALIDATION_PLACEHOLDER)

    def test_wrong_identity_refuses_without_writes(self):
        other = League.objects.create(
            name='Other League',
            slug='other-league',
            tier=1,
            active=False,
        )
        cases = (
            {'slug': 'missing-release'},
            {'league_id': other.id},
            {'seed': SEED + 1},
            {'generator_version': 'fg-founding-1'},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                def attempt(overrides=overrides):
                    try:
                        self._publish(**overrides)
                    except PublicationRefused as exc:
                        return exc
                    return None

                with patch('universe.publication_service.release_digests') as digests:
                    refused, writes = _writes_during(attempt)
                self.assertIsInstance(refused, PublicationRefused)
                digests.assert_not_called()
                self.assertEqual(writes, [])
                self._assert_draft_preserved('', '')

    def test_an_unsupported_generator_does_not_compute_v1_fingerprints(self):
        league = League.objects.create(
            name='Unsupported League',
            slug='unsupported-league',
            tier=1,
            active=False,
        )
        release = UniverseRelease.objects.create(
            slug='fg-founding-1-release',
            root_league=league,
            generator_version='fg-founding-1',
            seed=SEED,
            founding_franchise_count=24,
            expansion_schedule_version=1,
            first_season_year=1970,
            season_count=60,
            last_season_year=2029,
        )
        with patch('universe.publication_service.release_digests') as digests:
            with self.assertRaises(PublicationRefused) as caught:
                publish_universe_release(
                    slug=release.slug,
                    seed=SEED,
                    generator_version='fg-founding-1',
                    league_id=league.id,
                )
        digests.assert_not_called()
        self.assertEqual(caught.exception.failed_gates, ('fingerprint_scheme',))
        release.refresh_from_db()
        self.assertEqual(release.catalog_fingerprint, '')
        self.assertEqual(release.validation_fingerprint, '')
        self.assertEqual(release.status, UniverseRelease.Status.DRAFT)
        self.assertIsNone(release.published_at)

    def test_a_draft_timeline_reference_fails_closed(self):
        _stamp(self.release)
        now = timezone.now()
        Timeline.objects.bulk_create([
            Timeline(
                release=self.release,
                name='Malformed draft timeline',
                start_season_year=1970,
                current_season_year=1970,
                season_index=1,
                phase=Timeline.Phase.CHOOSE_FRANCHISE,
                status=Timeline.Status.ACTIVE,
                created_at=now,
                updated_at=now,
            ),
        ])
        with patch('universe.publication_service.release_digests') as digests:
            with self.assertRaises(PublicationRefused) as caught:
                self._publish()
        digests.assert_not_called()
        self.assertIn('timeline', str(caught.exception))
        self._assert_draft_preserved(CATALOG_PLACEHOLDER, VALIDATION_PLACEHOLDER)

    def test_an_invalid_status_pair_refuses_without_writes(self):
        _stamp(self.release)
        stamped = timezone.now()
        UniverseRelease.objects.filter(pk=self.release.pk).update(published_at=stamped)
        def attempt():
            try:
                self._publish()
            except PublicationRefused as exc:
                return exc
            return None

        refused, writes = _writes_during(attempt)
        self.assertIsInstance(refused, PublicationRefused)
        self.assertIn('status', str(refused))
        self.assertEqual(writes, [])
        self.release.refresh_from_db()
        self.assertEqual(self.release.status, UniverseRelease.Status.DRAFT)
        self.assertEqual(self.release.published_at, stamped)
        self.assertEqual(self.release.catalog_fingerprint, CATALOG_PLACEHOLDER)
        self.assertEqual(self.release.validation_fingerprint, VALIDATION_PLACEHOLDER)

    def test_a_published_mismatch_is_not_repaired(self):
        self._publish()
        self.release.refresh_from_db()
        published_at = self.release.published_at
        validation = self.release.validation_fingerprint
        bad = 'ab' * 32
        UniverseRelease.objects.filter(pk=self.release.pk).update(
            catalog_fingerprint=bad,
        )
        with self.assertRaises(PublicationRefused) as caught:
            self._publish()
        self.assertIn('fingerprint', caught.exception.failed_gates)
        self.release.refresh_from_db()
        self.assertEqual(self.release.status, UniverseRelease.Status.PUBLISHED)
        self.assertEqual(self.release.published_at, published_at)
        self.assertEqual(self.release.catalog_fingerprint, bad)
        self.assertEqual(self.release.validation_fingerprint, validation)

        UniverseRelease.objects.filter(pk=self.release.pk).update(
            catalog_fingerprint=release_digests(self.release)[0],
            seed=SEED + 1,
        )
        self.release.refresh_from_db()
        catalog = self.release.catalog_fingerprint
        with self.assertRaises(PublicationRefused) as caught:
            self._publish()
        self.assertIn('seed', str(caught.exception))
        self.release.refresh_from_db()
        self.assertEqual(self.release.seed, SEED + 1)
        self.assertEqual(self.release.catalog_fingerprint, catalog)
        self.assertEqual(self.release.validation_fingerprint, validation)
        self.assertEqual(self.release.published_at, published_at)
        self.assertEqual(self.release.status, UniverseRelease.Status.PUBLISHED)

    def test_a_forced_exception_after_computation_rolls_back(self):
        self.assertEqual(self.release.catalog_fingerprint, '')
        real_save = UniverseRelease.save

        def explode(instance, *args, **kwargs):
            real_save(instance, *args, **kwargs)
            raise RuntimeError('forced after candidate computation')

        with patch.object(UniverseRelease, 'save', explode):
            with self.assertRaises(RuntimeError):
                self._publish()
        self._assert_draft_preserved('', '')

    def test_the_command_prints_published_unchanged_and_refused_gates(self):
        out = StringIO()
        call_command(
            'publish_universe_release',
            league_id=self.league.id,
            slug=self.release.slug,
            seed=SEED,
            generator='fg-founding-2',
            stdout=out,
        )
        self.assertEqual(out.getvalue().strip(), 'published')
        self.release.refresh_from_db()
        published_at = self.release.published_at
        catalog = self.release.catalog_fingerprint
        again = StringIO()
        call_command(
            'publish_universe_release',
            league_id=self.league.id,
            slug=self.release.slug,
            seed=SEED,
            generator='fg-founding-2',
            stdout=again,
        )
        self.assertEqual(again.getvalue().strip(), 'unchanged')
        self.release.refresh_from_db()
        self.assertEqual(self.release.published_at, published_at)
        self.assertEqual(self.release.catalog_fingerprint, catalog)

    def test_the_command_raises_for_a_refused_draft(self):
        EraRuleProfile.objects.filter(
            release=self.release,
            profile_key='y1970',
        ).update(draft_rounds=16)
        with self.assertRaises(CommandError) as caught:
            call_command(
                'publish_universe_release',
                league_id=self.league.id,
                slug=self.release.slug,
                seed=SEED,
                generator='fg-founding-2',
                stdout=StringIO(),
            )
        self.assertIn('quota', str(caught.exception))
        self._assert_draft_preserved('', '')

    def _lock_events(self, callback):
        events = []
        real_lock = QuerySet.select_for_update

        def spy(qs, *args, **kwargs):
            events.append(('lock', qs.model.__name__))
            return real_lock(qs, *args, **kwargs)

        def wrap(execute, sql, params, many, context):
            verb = sql.lstrip().split(None, 1)[0].upper()
            if verb in WRITE_WORDS or verb == 'SELECT':
                events.append((verb, sql))
            return execute(sql, params, many, context)

        connection.execute_wrappers.append(wrap)
        try:
            with patch.object(QuerySet, 'select_for_update', spy):
                result = callback()
        finally:
            connection.execute_wrappers.remove(wrap)
        return result, events

    def _mark_published_in_database(self):
        UniverseRelease.objects.filter(pk=self.release.pk).update(
            status=UniverseRelease.Status.PUBLISHED,
            catalog_fingerprint='a' * 64,
            validation_fingerprint='b' * 64,
            published_at=timezone.now(),
        )

    def test_ordinary_child_save_locks_the_parent_release_before_writing(self):
        """SQLite omits FOR UPDATE, so this records call order only.

        A disposable MariaDB smoke test still has to show that a second
        connection waits. This test does not open one.
        """
        college = College.objects.filter(release=self.release).order_by('pk').first()
        original = college.name
        college.name = f'{original} Locked'

        def save():
            college.save()

        _, events = self._lock_events(save)
        self.assertFalse(connection.features.has_select_for_update)
        lock_at = events.index(('lock', 'UniverseRelease'))
        update_at = next(
            index for index, event in enumerate(events)
            if event[0] == 'UPDATE' and 'universe_college' in event[1]
        )
        self.assertLess(lock_at, update_at)
        self.assertTrue(all(
            event[1] == 'UniverseRelease'
            for event in events if event[0] == 'lock'
        ))
        self.assertFalse(any(
            'FOR UPDATE' in event[1].upper()
            for event in events if event[0] != 'lock'
        ))
        college.refresh_from_db()
        self.assertEqual(college.name, f'{original} Locked')

    def test_ordinary_child_delete_locks_the_parent_release_before_writing(self):
        college = College.objects.create(
            release=self.release,
            slug='lock-probe-college',
            name='Lock Probe College',
        )

        def remove():
            college.delete()

        _, events = self._lock_events(remove)
        lock_at = events.index(('lock', 'UniverseRelease'))
        delete_at = next(
            index for index, event in enumerate(events)
            if event[0] == 'DELETE' and 'universe_college' in event[1]
        )
        self.assertLess(lock_at, delete_at)
        self.assertFalse(College.objects.filter(pk=college.pk).exists())

    def test_cached_draft_release_cannot_save_after_the_row_is_published(self):
        college = College.objects.filter(release=self.release).order_by('pk').first()
        cached = college.release
        original = college.name
        self.assertEqual(cached.status, UniverseRelease.Status.DRAFT)
        self._mark_published_in_database()
        college.name = f'{original} stale'
        with self.assertRaises(ValidationError):
            college.save()
        self.assertEqual(cached.status, UniverseRelease.Status.DRAFT)
        college.refresh_from_db()
        self.assertEqual(college.name, original)
        with self.assertRaises(ValidationError):
            college.delete()
        self.assertTrue(College.objects.filter(pk=college.pk).exists())

    def test_custom_save_models_read_status_from_the_locked_release(self):
        franchise = UniverseFranchise.objects.filter(
            release=self.release,
            origin=UniverseFranchise.Origin.FOUNDING,
            first_active_season=1970,
        ).order_by('pk').first()
        membership = FranchiseSeasonMembership.objects.filter(
            franchise=franchise,
        ).order_by('pk').first()
        provisional = ProvisionalFranchiseName.objects.filter(
            release=self.release,
        ).order_by('pk').first()
        event = FranchiseHistoryEvent(
            franchise=franchise,
            season_year=1970,
            kind=FranchiseHistoryEvent.Kind.RENAME,
            previous_name='Before',
            new_name='After',
            notes='original',
        )
        event.save()
        standing = HistoricalStanding(
            franchise=franchise,
            season_year=1965,
            wins=4,
            losses=6,
            ties=0,
        )
        standing.save()
        samples = (
            (franchise, 'display_name', f'{franchise.display_name} X', franchise.release),
            (
                membership,
                'owner_selectable',
                not membership.owner_selectable,
                membership.franchise.release,
            ),
            (provisional, 'city', 'Lockport', provisional.release),
            (event, 'notes', 'changed', event.franchise.release),
            (standing, 'wins', 5, standing.franchise.release),
        )
        for _instance, _field, _value, cached in samples:
            self.assertEqual(cached.status, UniverseRelease.Status.DRAFT)
        self._mark_published_in_database()
        for instance, field, value, cached in samples:
            self.assertEqual(cached.status, UniverseRelease.Status.DRAFT)
            original = getattr(instance, field)
            calls = []
            real_lock = QuerySet.select_for_update

            def spy(qs, *args, **kwargs):
                calls.append(qs.model)
                return real_lock(qs, *args, **kwargs)

            setattr(instance, field, value)
            with patch.object(QuerySet, 'select_for_update', spy):
                with self.assertRaises(ValidationError):
                    instance.save()
                self.assertIn(UniverseRelease, calls)
                calls.clear()
                with self.assertRaises(ValidationError):
                    instance.delete()
                self.assertIn(UniverseRelease, calls)
            instance.refresh_from_db()
            self.assertEqual(getattr(instance, field), original)
            self.assertTrue(type(instance).objects.filter(pk=instance.pk).exists())

    def test_child_write_between_snapshot_and_publication_save_rolls_back(self):
        """A child save in that window takes the release lock and does not remain.

        SQLite does not block the nested ``select_for_update``. The child
        statement runs inside the publication transaction, the digests
        diverge, and both the child write and the publication roll back.
        A disposable MariaDB smoke test has to show the second connection
        waiting instead. This test does not open one.
        """
        college = College.objects.filter(release=self.release).order_by('pk').first()
        original = college.name
        order = []
        real_lock = QuerySet.select_for_update
        real_digests = release_digests

        def spy(qs, *args, **kwargs):
            order.append(('lock', qs.model.__name__))
            return real_lock(qs, *args, **kwargs)

        def digests(release):
            result = real_digests(release)
            order.append('snapshot')
            college.name = f'{original} race'
            college.save()
            order.append('child-returned')
            return result

        with patch.object(QuerySet, 'select_for_update', spy):
            with patch('universe.publication_service.release_digests', digests):
                with self.assertRaises(PublicationRefused) as caught:
                    self._publish()
        self.assertIn('fingerprint', caught.exception.failed_gates)
        self.assertEqual(order[0], ('lock', 'UniverseRelease'))
        snapshot_at = order.index('snapshot')
        child_lock_at = order.index(('lock', 'UniverseRelease'), snapshot_at)
        self.assertLess(snapshot_at, child_lock_at)
        self.assertLess(child_lock_at, order.index('child-returned'))
        self.assertTrue(all(item[1] == 'UniverseRelease' for item in order if item[0] == 'lock'))
        college.refresh_from_db()
        self.assertEqual(college.name, original)
        self._assert_draft_preserved('', '')

    def test_a_failed_child_write_rolls_back(self):
        college = College.objects.filter(release=self.release).order_by('pk').first()
        original = college.name
        college.name = f'{original} rollback'
        real_save = __import__(
            'universe.release_guard',
            fromlist=['models'],
        ).models.Model.save

        def explode(instance, *args, **kwargs):
            real_save(instance, *args, **kwargs)
            raise RuntimeError('child write failed')

        with patch('universe.release_guard.models.Model.save', explode):
            with self.assertRaises(RuntimeError):
                college.save()
        college.refresh_from_db()
        self.assertEqual(college.name, original)

    def _other_release(self):
        league = League.objects.create(
            name='Lock Probe League',
            slug='lock-probe-league',
            tier=1,
            active=False,
        )
        return UniverseRelease.objects.create(
            slug='fg-lock-other',
            root_league=league,
            generator_version=self.release.generator_version,
            seed=self.release.seed,
            founding_franchise_count=self.release.founding_franchise_count,
            expansion_schedule_version=self.release.expansion_schedule_version,
            first_season_year=self.release.first_season_year,
            season_count=self.release.season_count,
            last_season_year=self.release.last_season_year,
            status=UniverseRelease.Status.DRAFT,
        )

    def _stored_release(self, release):
        release.refresh_from_db()
        return (
            release.status,
            release.catalog_fingerprint,
            release.validation_fingerprint,
            release.published_at,
            release.slug,
        )

    def _row_values(self, instance):
        instance.refresh_from_db()
        return {
            field.attname: getattr(instance, field.attname)
            for field in instance._meta.concrete_fields
        }

    def _patched_gets(self):
        seen = []
        real_get = QuerySet.get

        def get(queryset, *args, **kwargs):
            if queryset.query.select_for_update:
                seen.append((queryset.db, kwargs.get('pk')))
            return real_get(queryset, *args, **kwargs)

        return seen, patch.object(QuerySet, 'get', get)

    def _assert_lock_context_clear(self):
        with self.assertRaises(RuntimeError):
            assert_release_lock_held(self.release.pk, using='default')
        with self.assertRaises(RuntimeError):
            assert_release_lock_held(self.release.pk, using='other')

    def _assert_refused(self, instance, persisted_pk, other_pk, before):
        seen, patched = self._patched_gets()
        with patched:
            with patch.object(instance, 'full_clean', side_effect=AssertionError('full_clean')):
                with self.assertRaises(ValidationError) as caught:
                    instance.save()
        self.assertEqual(caught.exception.messages, [OWNERSHIP_MESSAGE])
        self.assertEqual(seen, [('default', persisted_pk)])
        self.assertNotIn(other_pk, [pk for _alias, pk in seen])
        self.assertEqual(self._row_values(instance), before)
        self._assert_lock_context_clear()
        return seen

    def test_reassigning_a_direct_child_locks_the_persisted_release(self):
        other = self._other_release()
        self.assertNotEqual(other.pk, self.release.pk)
        draft_college = College.objects.create(
            release=other,
            slug='lock-draft-college',
            name='Draft College',
        )
        before_draft = self._row_values(draft_college)
        draft_college.release = self.release
        draft_college.name = 'Draft Moved'
        self._assert_refused(draft_college, other.pk, self.release.pk, before_draft)
        self.assertEqual(self._stored_release(other)[0], UniverseRelease.Status.DRAFT)
        self.assertEqual(self._stored_release(self.release)[0], UniverseRelease.Status.DRAFT)

        self._mark_published_in_database()
        published = self._stored_release(self.release)
        draft = self._stored_release(other)
        college = College.objects.filter(release=self.release).exclude(
            pk=draft_college.pk,
        ).order_by('pk').first()
        before = self._row_values(college)
        cached = college.release
        college.release_id = other.pk
        type(college)._meta.get_field('release').set_cached_value(college, cached)
        college.name = 'Published Moved'
        self._assert_refused(college, self.release.pk, other.pk, before)
        self.assertEqual(self._stored_release(self.release), published)
        self.assertEqual(self._stored_release(other), draft)
        self.assertFalse(College.objects.filter(release=other, name='Published Moved').exists())

    def _rating_values(self, model):
        values = {}
        for field in model._meta.concrete_fields:
            if field.name.startswith('initial_') or field.name.startswith('potential_'):
                values[field.name] = 40
        return values

    def _copy_to_release(self, instance, release, **changes):
        clone = type(instance).objects.get(pk=instance.pk)
        clone.pk = None
        clone._state.adding = True
        clone.release = release
        for name, value in changes.items():
            setattr(clone, name, value)
        clone.save()
        return clone

    def _prepare_indirect_children(self):
        rating = PlayerRatingProfile.objects.filter(
            player__release=self.release,
            player__cohort=UniversePlayer.Cohort.OPENING,
        ).order_by('pk').first()
        gm = UniverseGM.objects.filter(release=self.release).order_by('pk').first()
        coach = UniverseCoach.objects.filter(release=self.release).order_by('pk').first()
        gm_rating = GMRatingProfile(gm=gm, **self._rating_values(GMRatingProfile))
        gm_rating.save()
        coach_rating = CoachRatingProfile(
            coach=coach,
            **self._rating_values(CoachRatingProfile),
        )
        coach_rating.save()
        profile = EraRuleProfile.objects.get(release=self.release, profile_key='y1970')
        taken = set(
            EraAllowedScheme.objects.filter(profile=profile).values_list('side', 'scheme')
        )
        choice = next(
            ('offense', scheme) for scheme in OFFENSE_SCHEMES
            if ('offense', scheme) not in taken
        ) if any(('offense', scheme) not in taken for scheme in OFFENSE_SCHEMES) else next(
            ('defense', scheme) for scheme in DEFENSE_SCHEMES
            if ('defense', scheme) not in taken
        )
        allowed = EraAllowedScheme(profile=profile, side=choice[0], scheme=choice[1])
        allowed.save()
        club = ExpansionClub.objects.filter(event__release=self.release).order_by('pk').first()
        assignment = OpeningAssignment.objects.filter(
            release=self.release,
        ).exclude(role='free_agent').exclude(franchise=None).order_by('pk').first()
        years = profile.veteran_contract_years_min
        contract = OpeningContract(
            assignment=assignment,
            years=years,
            salary_per_year=100,
            guaranteed=100,
            signing_bonus=0,
            start_year=self.release.first_season_year,
            end_year=self.release.first_season_year + years - 1,
        )
        contract.save()
        membership = FranchiseSeasonMembership.objects.filter(
            franchise__release=self.release,
        ).order_by('pk').first()
        post = UniverseStaffPost(
            gm=gm,
            role='gm',
            start_year=gm.debut_year - 2,
            end_year=gm.debut_year - 1,
            organization_name='Lock Probe Club',
            ended_by=UniverseStaffPost.EndedBy.RESIGNED,
        )
        post.save()
        return {
            'player': rating,
            'gm': gm_rating,
            'coach': coach_rating,
            'profile': allowed,
            'event': club,
            'assignment': contract,
            'franchise': membership,
            'staff': post,
        }

    def test_a_dirty_or_replaced_parent_cannot_redirect_the_lock(self):
        other = self._other_release()
        self.assertNotEqual(other.pk, self.release.pk)
        children = self._prepare_indirect_children()
        self._mark_published_in_database()
        published = self._stored_release(self.release)
        draft = self._stored_release(other)
        parent_fields = {
            'player': 'player',
            'gm': 'gm',
            'coach': 'coach',
            'profile': 'profile',
            'event': 'event',
            'assignment': 'assignment',
            'franchise': 'franchise',
            'staff': 'gm',
        }
        for label, field_name in parent_fields.items():
            row = children[label]
            before = self._row_values(row)
            parent_id = getattr(row, f'{field_name}_id')
            parent = getattr(row, field_name)
            stored_release = type(parent).objects.values_list(
                'release_id', flat=True,
            ).get(pk=parent_id)
            parent.release = other
            self._assert_refused(row, self.release.pk, other.pk, before)
            self.assertEqual(
                type(parent).objects.values_list('release_id', flat=True).get(pk=parent_id),
                stored_release,
            )
        self.assertEqual(self._stored_release(self.release), published)
        self.assertEqual(self._stored_release(other), draft)

        rating = children['player']
        source = UniversePlayer.objects.get(pk=rating.player_id)
        college = self._copy_to_release(source.college, other)
        profile = self._copy_to_release(source.origin_profile, other)
        franchise = None
        if source.origin_franchise_id:
            franchise = self._copy_to_release(
                source.origin_franchise,
                other,
                placeholder_slug='lock-probe-origin',
                catalog_team=None,
                history_predecessor=None,
            )
        replacement = self._copy_to_release(
            source,
            other,
            universe_key='lock-probe-player',
            college=college,
            origin_profile=profile,
            origin_franchise=franchise,
        )
        before = self._row_values(rating)
        rating.player = replacement
        self._assert_refused(rating, self.release.pk, other.pk, before)
        self.assertEqual(
            UniversePlayer.objects.values_list('release_id', flat=True).get(pk=source.pk),
            self.release.pk,
        )
        self.assertEqual(replacement.release_id, other.pk)

        membership = children['franchise']
        before = self._row_values(membership)
        stored_franchise_id = membership.franchise_id
        replacement_franchise = self._copy_to_release(
            UniverseFranchise.objects.get(pk=stored_franchise_id),
            other,
            placeholder_slug='lock-probe-franchise',
            catalog_team=None,
            history_predecessor=None,
        )
        membership.franchise_id = replacement_franchise.pk
        type(membership)._meta.get_field('franchise').set_cached_value(
            membership,
            UniverseFranchise.objects.get(pk=stored_franchise_id),
        )
        self._assert_refused(membership, self.release.pk, other.pk, before)

        post = UniverseStaffPost.objects.get(pk=children['staff'].pk)
        before = self._row_values(post)
        post.franchise = replacement_franchise
        seen, patched = self._patched_gets()
        with patched:
            with self.assertRaises(ValidationError):
                post.save()
        self.assertEqual(seen, [('default', self.release.pk)])
        self.assertEqual(self._row_values(post), before)
        self.assertEqual(self._stored_release(self.release), published)
        self.assertEqual(replacement_franchise.release_id, other.pk)

    def test_delete_uses_the_persisted_owner(self):
        other = self._other_release()
        self.assertNotEqual(other.pk, self.release.pk)
        self._mark_published_in_database()
        college = College.objects.filter(release=self.release).order_by('pk').first()
        college.release = other
        seen, patched = self._patched_gets()
        with patched:
            with self.assertRaises(ValidationError):
                college.delete()
        self.assertEqual(seen, [('default', self.release.pk)])
        self.assertTrue(College.objects.filter(pk=college.pk, release_id=self.release.pk).exists())

        rating = PlayerRatingProfile.objects.filter(
            player__release=self.release,
        ).order_by('pk').first()
        player_id = rating.player_id
        rating.player.release = other
        seen, patched = self._patched_gets()
        with patched:
            with self.assertRaises(ValidationError):
                rating.delete()
        self.assertEqual(seen, [('default', self.release.pk)])
        self.assertTrue(PlayerRatingProfile.objects.filter(pk=rating.pk).exists())
        self.assertEqual(
            UniversePlayer.objects.values_list('release_id', flat=True).get(pk=player_id),
            self.release.pk,
        )

        draft_college = College.objects.create(
            release=other,
            slug='lock-delete-draft',
            name='Delete Draft',
        )
        draft_pk = draft_college.pk
        draft_college.release = self.release
        seen, patched = self._patched_gets()
        with patched:
            draft_college.delete()
        self.assertEqual(seen, [('default', other.pk)])
        self.assertFalse(College.objects.filter(pk=draft_pk).exists())
        self._assert_lock_context_clear()

    def test_a_draft_child_saves_and_deletes_and_a_new_child_locks_its_release(self):
        other = self._other_release()
        self.assertNotEqual(other.pk, self.release.pk)
        college = College.objects.create(
            release=other,
            slug='lock-live-college',
            name='Live',
        )
        college.name = 'Live Updated'
        seen, patched = self._patched_gets()
        with patched:
            college.save(using='default')
        self.assertEqual(seen, [('default', other.pk)])
        college.refresh_from_db()
        self.assertEqual(college.name, 'Live Updated')
        self.assertEqual(college.release_id, other.pk)

        rating = PlayerRatingProfile.objects.filter(
            player__release=self.release,
            player__cohort=UniversePlayer.Cohort.OPENING,
        ).order_by('pk').first()
        player = rating.player
        original_name = player.name
        player.name = f'{original_name} unsaved'
        target = 0 if rating.initial_speed else min(1, rating.potential_speed)
        replacement = self._copy_to_release(
            player,
            other,
            universe_key='lock-live-player',
            college=self._copy_to_release(player.college, other),
            origin_profile=self._copy_to_release(player.origin_profile, other),
            origin_franchise=self._copy_to_release(
                player.origin_franchise,
                other,
                placeholder_slug='lock-live-origin',
                catalog_team=None,
                history_predecessor=None,
            ) if player.origin_franchise_id else None,
        )
        type(rating)._meta.get_field('player').set_cached_value(rating, replacement)
        rating.initial_speed = target
        seen, patched = self._patched_gets()
        with patched:
            rating.save()
        self.assertEqual(seen, [('default', self.release.pk)])
        rating.refresh_from_db()
        player.refresh_from_db()
        self.assertEqual(rating.initial_speed, target)
        self.assertEqual(rating.player_id, player.pk)
        self.assertEqual(player.name, original_name)
        self.assertEqual(player.release_id, self.release.pk)
        self.assertEqual(replacement.release_id, other.pk)

        extra = College.objects.create(
            release=self.release,
            slug='lock-live-delete',
            name='Delete Me',
        )
        extra_pk = extra.pk
        seen, patched = self._patched_gets()
        with patched:
            extra.delete()
        self.assertEqual(seen, [('default', self.release.pk)])
        self.assertFalse(College.objects.filter(pk=extra_pk).exists())

        created = College(release=other, slug='lock-new-college', name='New College')
        seen, patched = self._patched_gets()
        with patched:
            created.save()
        self.assertEqual(seen, [('default', other.pk)])
        self.assertTrue(College.objects.filter(
            pk=created.pk,
            release_id=other.pk,
            name='New College',
        ).exists())
        self._assert_lock_context_clear()

    def test_force_insert_and_force_update_follow_persisted_ownership(self):
        other = self._other_release()
        self.assertNotEqual(other.pk, self.release.pk)
        college = College.objects.create(
            release=other,
            slug='lock-force-college',
            name='Force',
        )
        college.name = 'Force Updated'
        seen, patched = self._patched_gets()
        with patched:
            college.save(force_update=True)
        self.assertEqual(seen, [('default', other.pk)])
        college.refresh_from_db()
        self.assertEqual(college.name, 'Force Updated')
        self.assertEqual(college.release_id, other.pk)

        inserted = College(release=other, slug='lock-force-insert', name='Inserted')
        seen, patched = self._patched_gets()
        with patched:
            inserted.save(force_insert=True)
        self.assertEqual(seen, [('default', other.pk)])
        self.assertTrue(College.objects.filter(pk=inserted.pk, release_id=other.pk).exists())

        self._mark_published_in_database()
        published = College.objects.filter(release=self.release).order_by('pk').first()
        before = self._row_values(published)
        published.release = other
        published.name = 'Force Moved'
        seen, patched = self._patched_gets()
        with patched:
            with self.assertRaises(ValidationError) as caught:
                published.save(force_update=True)
        self.assertEqual(caught.exception.messages, [OWNERSHIP_MESSAGE])
        self.assertEqual(seen, [('default', self.release.pk)])
        seen, patched = self._patched_gets()
        with patched:
            with self.assertRaises(ValidationError) as caught:
                published.save(force_insert=True)
        self.assertEqual(caught.exception.messages, [OWNERSHIP_MESSAGE])
        self.assertEqual(seen, [('default', self.release.pk)])
        self.assertEqual(self._row_values(published), before)
        self._assert_lock_context_clear()

    def test_nested_locks_and_alias_assertions(self):
        other = self._other_release()
        self.assertNotEqual(other.pk, self.release.pk)
        with holding_release_lock(self.release.pk, using='default'):
            assert_release_lock_held(self.release.pk, using='default')
            with holding_release_lock(self.release.pk, using='default'):
                assert_release_lock_held(self.release.pk, using='default')
            assert_release_lock_held(self.release.pk, using='default')
            with self.assertRaises(RuntimeError):
                assert_release_lock_held(self.release.pk, using='other')
            with self.assertRaises(RuntimeError):
                with holding_release_lock(other.pk, using='default'):
                    pass
            assert_release_lock_held(self.release.pk, using='default')
            with self.assertRaises(RuntimeError):
                assert_release_lock_held(other.pk, using='default')
        self._assert_lock_context_clear()

        with self.assertRaises(RuntimeError):
            with holding_release_lock(self.release.pk, using='default'):
                assert_release_lock_held(self.release.pk, using='default')
                raise RuntimeError('probe')
        self._assert_lock_context_clear()

    def test_full_clean_cannot_retarget_or_skip_the_locked_owner(self):
        other = self._other_release()
        college = College.objects.filter(release=self.release).order_by('pk').first()
        before = self._row_values(college)
        published = self._stored_release(self.release)
        draft = self._stored_release(other)
        original_clean = college.full_clean

        def retarget(*args, **kwargs):
            original_clean(*args, **kwargs)
            college.release = other

        seen, patched = self._patched_gets()
        with patched:
            with patch.object(college, 'full_clean', retarget):
                with patch(
                    'universe.release_guard.models.Model.save',
                    side_effect=AssertionError('wrote'),
                ):
                    with self.assertRaises(ValidationError) as caught:
                        college.save()
        self.assertEqual(caught.exception.messages, [OWNERSHIP_MESSAGE])
        self.assertEqual(seen, [('default', self.release.pk)])
        self.assertEqual(self._row_values(college), before)
        self.assertEqual(self._stored_release(self.release), published)
        self.assertEqual(self._stored_release(other), draft)

        self._mark_published_in_database()
        college = College.objects.filter(release=self.release).order_by('pk').first()
        before = self._row_values(college)
        real = persisted_release_id
        state = {'calls': 0}

        def flip(instance, using):
            state['calls'] += 1
            value = real(instance, using)
            if state['calls'] == 2:
                return other.pk
            return value

        seen, patched = self._patched_gets()
        with patched:
            with patch('universe.release_guard.persisted_release_id', flip):
                with self.assertRaises(ValidationError) as caught:
                    college.name = 'Changed Under Lock'
                    college.save()
        self.assertEqual(caught.exception.messages, [OWNERSHIP_MESSAGE])
        self.assertEqual(seen, [('default', self.release.pk)])
        self.assertGreaterEqual(state['calls'], 2)
        self.assertEqual(self._row_values(college), before)
        self._assert_lock_context_clear()


class ReleaseLockInventoryTests(SimpleTestCase):
    def _assert_lock_calls_pass_using(self, text, name):
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith('def '):
                continue
            for call in ('assert_release_lock_held(', 'holding_release_lock('):
                if call in stripped:
                    self.assertIn('using=', stripped, f'{name}: {stripped}')

    def test_every_fingerprint_model_locks_the_parent_release(self):
        from universe.fingerprints import _coverage

        for model, _included, _excluded in _coverage():
            self.assertTrue(
                getattr(model.save, 'locks_parent_release', False),
                model.__name__,
            )
            self.assertTrue(
                getattr(model.delete, 'locks_parent_release', False),
                model.__name__,
            )

    def test_product_bulk_bypasses_are_inside_the_release_lock(self):
        universe = Path(__file__).resolve().parents[1]
        found = []
        for path in universe.rglob('*.py'):
            if {'tests', 'migrations', '__pycache__'} & set(path.parts):
                continue
            text = path.read_text()
            queryset_delete = any(
                '.delete()' in line and 'super().delete' not in line
                for line in text.splitlines()
            )
            if (
                'bulk_create(' in text
                or 'bulk_update(' in text
                or '.all().delete()' in text
                or queryset_delete
            ):
                found.append(path.relative_to(universe).as_posix())
        self.assertEqual(
            sorted(found),
            [
                'generation/founding.py',
                'generation/opening_players.py',
                'generation/player_rating_profiles.py',
                'generation/prospects.py',
                'generation/schedule.py',
            ],
        )
        for name in found:
            text = (universe / name).read_text()
            self.assertIn('holding_release_lock', text)
            self.assertIn('assert_release_lock_held', text)
        for name in (
            'publication_service.py',
            'generation/provisional_names.py',
            'release_guard.py',
        ):
            self.assertIn('holding_release_lock', (universe / name).read_text())
        for name in list(found) + [
            'publication_service.py',
            'generation/provisional_names.py',
            'release_guard.py',
            'models.py',
        ]:
            self._assert_lock_calls_pass_using((universe / name).read_text(), name)
        self.assertIn(
            'holding_release_lock',
            replace_provisional_value.__doc__ + '\n' + Path(
                replace_provisional_value.__code__.co_filename,
            ).read_text(),
        )

    def test_release_content_locks_universe_release_before_any_child(self):
        universe = Path(__file__).resolve().parents[1]
        root = universe.parent
        product_hits = []
        for path in universe.rglob('*.py'):
            if {'tests', 'migrations', '__pycache__'} & set(path.parts):
                continue
            if path.name == 'release_guard.py':
                continue
            if 'select_for_update' in path.read_text():
                product_hits.append(path.relative_to(universe).as_posix())
        self.assertEqual(product_hits, [])
        for path in (root / 'timelines').rglob('*.py'):
            if 'tests' in path.parts or 'migrations' in path.parts:
                continue
            self.assertNotIn(
                'UniverseRelease.objects.select_for_update',
                path.read_text(),
            )
