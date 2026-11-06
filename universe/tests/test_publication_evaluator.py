import socket

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import SimpleTestCase, TestCase

from universe.generation.opening_players import y1970_values
from universe.generation.prospects import later_profile_values
from universe.models import UniverseRelease
from timelines.models import Timeline
from universe.publication import (
    SMOKE_FINGERPRINT_PLACEHOLDERS,
    evaluate_release_publication,
)
from universe.rules import EraRuleProfile
from leagues.models import League


CATALOG_PLACEHOLDER = 'smoke-catalog-fingerprint'
VALIDATION_PLACEHOLDER = 'smoke-validation-fingerprint'
CAP_ON_KEYS = (
    'y1995',
    'y1999',
    'y2002',
    'y2004',
    'y2012',
    'y2014',
    'y2021',
)
CAP_OFF_KEYS = (
    'y1970',
    'y1974',
    'y1976',
    'y1978',
    'y1987',
)
# Supplied by this test. It is not a product cap amount.
TEST_CAP_LIMIT = 1

_ORIGINAL_CONNECT = socket.socket.connect


def _blocked_connect(self, address):
    raise AssertionError(f'Outbound connections are blocked: {address}')


def setUpModule():
    socket.socket.connect = _blocked_connect


def tearDownModule():
    socket.socket.connect = _ORIGINAL_CONNECT


def _cards():
    return (y1970_values(),) + tuple(later_profile_values())


def _release(league, slug, catalog, validation):
    return UniverseRelease.objects.create(
        slug=slug,
        root_league=league,
        generator_version='fg-founding-2',
        seed=1970,
        founding_franchise_count=26,
        expansion_schedule_version=3,
        catalog_fingerprint=catalog,
        validation_fingerprint=validation,
        first_season_year=1970,
        season_count=60,
        last_season_year=2029,
        status=UniverseRelease.Status.DRAFT,
    )


def _store_cards(release, cap_limits=None):
    for values in _cards():
        row = dict(values)
        key = row['profile_key']
        if cap_limits and key in cap_limits:
            row['cap_limit'] = cap_limits[key]
        EraRuleProfile(release=release, **row).save()


def _snapshot(release):
    release.refresh_from_db()
    profiles = tuple(
        release.era_profiles.order_by('profile_key').values_list(
            'pk',
            'profile_key',
            'cap_status',
            'cap_limit',
            'minimum_salary',
            'rookie_slot_scale',
        )
    )
    return {
        'status': release.status,
        'published_at': release.published_at,
        'catalog_fingerprint': release.catalog_fingerprint,
        'validation_fingerprint': release.validation_fingerprint,
        'profiles': profiles,
    }


class SqliteSettingsTests(SimpleTestCase):
    def test_settings_do_not_name_the_remote_database(self):
        database = settings.DATABASES['default']
        self.assertEqual(database['ENGINE'], 'django.db.backends.sqlite3')
        self.assertIn('memory', database['NAME'])
        self.assertNotIn('mysql', database['ENGINE'])
        self.assertNotIn('pythonanywhere', str(settings.DATABASES))
        self.assertNotIn('lecrolink', str(settings.DATABASES))
        self.assertFalse(database.get('HOST'))
        self.assertEqual(connection.vendor, 'sqlite')
        self.assertFalse(connection.settings_dict.get('HOST'))


class PublicationEvaluatorTests(TestCase):
    def test_smoke_shaped_release_reports_seven_cap_blockers(self):
        league = League.objects.create(
            name='Smoke League',
            slug='smoke-league',
            tier=1,
            active=True,
        )
        release = _release(
            league,
            'fg-1970-1',
            CATALOG_PLACEHOLDER,
            VALIDATION_PLACEHOLDER,
        )
        _store_cards(release)
        self.assertEqual(
            set(SMOKE_FINGERPRINT_PLACEHOLDERS),
            {CATALOG_PLACEHOLDER, VALIDATION_PLACEHOLDER},
        )
        before = _snapshot(release)
        statements = []

        def wrap(execute, sql, params, many, context):
            statements.append(sql)
            return execute(sql, params, many, context)

        connection.execute_wrappers.append(wrap)
        try:
            report = evaluate_release_publication(release)
        finally:
            connection.execute_wrappers.remove(wrap)

        self.assertEqual(report.cap_blockers, CAP_ON_KEYS)
        self.assertEqual(report.fingerprint_verification, 'unresolved')
        self.assertEqual(
            report.unverified_placeholder_fields,
            ('catalog_fingerprint', 'validation_fingerprint'),
        )
        self.assertFalse(report.meets_publication_requirements)
        text = '\n'.join(report.messages)
        self.assertIn('unverified placeholder', text)
        self.assertIn('No fingerprint algorithm has been approved', text)
        self.assertNotIn('minimum_salary', text)
        self.assertNotIn('rookie_slot_scale', text)
        for key in CAP_OFF_KEYS:
            self.assertNotIn(key, report.cap_blockers)
        self.assertFalse(any(
            sql.lstrip().split(None, 1)[0].upper() in ('INSERT', 'UPDATE', 'DELETE', 'REPLACE')
            for sql in statements
        ))
        self.assertEqual(_snapshot(release), before)
        self.assertEqual(release.status, UniverseRelease.Status.DRAFT)
        self.assertIsNone(release.published_at)
        self.assertEqual(
            EraRuleProfile.objects.filter(
                release=release,
                cap_status='off',
                cap_limit__isnull=True,
                minimum_salary__isnull=True,
                rookie_slot_scale__isnull=True,
            ).count(),
            len(CAP_OFF_KEYS),
        )

    def test_test_supplied_caps_are_not_blockers_and_do_not_publish(self):
        league = League.objects.create(
            name='Capped League',
            slug='capped-league',
            tier=1,
            active=True,
        )
        release = _release(
            league,
            'fg-capped-1',
            'test-supplied-catalog',
            'test-supplied-validation',
        )
        _store_cards(release, {key: TEST_CAP_LIMIT for key in CAP_ON_KEYS})
        before = _snapshot(release)
        report = evaluate_release_publication(release)
        self.assertEqual(report.cap_blockers, ())
        self.assertEqual(report.fingerprint_verification, 'unresolved')
        self.assertEqual(report.unverified_placeholder_fields, ())
        self.assertFalse(report.meets_publication_requirements)
        self.assertEqual(_snapshot(release), before)
        self.assertEqual(release.status, UniverseRelease.Status.DRAFT)
        self.assertIsNone(release.published_at)
        self.assertFalse(
            EraRuleProfile.objects.filter(
                release=release,
                profile_key__in=CAP_ON_KEYS,
                cap_limit__isnull=True,
            ).exists()
        )

    def test_a_draft_release_still_rejects_a_new_timeline(self):
        league = League.objects.create(
            name='Timeline League',
            slug='timeline-league',
            tier=1,
            active=True,
        )
        release = _release(
            league,
            'fg-timeline-1',
            CATALOG_PLACEHOLDER,
            VALIDATION_PLACEHOLDER,
        )
        timeline = Timeline(
            release=release,
            name='Draft campaign',
            start_season_year=1970,
            current_season_year=1970,
            season_index=1,
            phase=Timeline.Phase.CHOOSE_FRANCHISE,
            status=Timeline.Status.ACTIVE,
        )
        with self.assertRaisesRegex(
            ValidationError,
            'A timeline uses a published release.',
        ):
            timeline.save()
        self.assertEqual(Timeline.objects.count(), 0)
        release.refresh_from_db()
        self.assertEqual(release.status, UniverseRelease.Status.DRAFT)
        self.assertIsNone(release.published_at)
