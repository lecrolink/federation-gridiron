import socket

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import SimpleTestCase, TestCase

from universe.fingerprints import release_digests
from universe.generation.opening_players import y1970_values
from universe.generation.prospects import later_profile_values
from universe.models import (
    FranchiseHistoryEvent,
    HistoricalStanding,
    UniverseFranchise,
    UniverseRelease,
)
from universe.people import College, UniverseGM, UniversePlayer, UniverseStaffIdentity
from timelines.models import Timeline
from universe.publication import (
    INFORMATIONAL_REQUIREMENTS,
    SMOKE_FINGERPRINT_PLACEHOLDERS,
    _opening_depth_from_rows,
    evaluate_release_publication,
)
from universe.rules import EraRuleProfile, OpeningAssignment
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
    def test_generated_caps_are_not_blockers_and_smoke_fingerprints_mismatch(self):
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

        self.assertEqual(report.cap_blockers, ())
        self.assertEqual(report.fingerprint_scheme, (
            'catalog-sha256-v1',
            'validation-sha256-v1',
        ))
        self.assertEqual(len(report.catalog_digest), 64)
        self.assertEqual(len(report.validation_digest), 64)
        self.assertFalse(report.catalog_digest_match)
        self.assertFalse(report.validation_digest_match)
        self.assertEqual(report.fingerprint_verification, 'mismatch')
        self.assertEqual(
            report.unverified_placeholder_fields,
            ('catalog_fingerprint', 'validation_fingerprint'),
        )
        self.assertFalse(report.cap_and_fingerprint_checks_pass)
        self.assertFalse(report.publishable)
        self.assertEqual(report.required_unresolved, ())
        self.assertEqual(report.informational_requirements, INFORMATIONAL_REQUIREMENTS)
        self.assertEqual(report.draft_order_status, 'fail')
        self.assertEqual(report.opening_depth_status, 'pass')
        self.assertEqual(report.history_events_status, 'pass')
        text = '\n'.join(report.messages)
        self.assertIn('unverified placeholder', text)
        self.assertIn('does not match the stored catalog fingerprint', text)
        self.assertNotIn(report.catalog_digest, (
            release.catalog_fingerprint,
            release.validation_fingerprint,
        ))
        self.assertIn(
            'Not a publication requirement: minimum_salary and rookie_slot_scale:',
            text,
        )
        self.assertNotIn('minimum_salary blocks', text)
        self.assertNotIn('rookie_slot_scale blocks', text)
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
        self.assertEqual(
            EraRuleProfile.objects.filter(
                release=release,
                profile_key__in=CAP_ON_KEYS,
                cap_limit=10000,
            ).count(),
            len(CAP_ON_KEYS),
        )

    def test_null_cap_on_profiles_remain_blockers(self):
        league = League.objects.create(
            name='Null Cap League',
            slug='null-cap-league',
            tier=1,
            active=True,
        )
        release = _release(
            league,
            'fg-null-cap',
            CATALOG_PLACEHOLDER,
            VALIDATION_PLACEHOLDER,
        )
        _store_cards(release)
        EraRuleProfile.objects.filter(
            release=release,
            profile_key__in=CAP_ON_KEYS,
        ).update(cap_limit=None)
        report = evaluate_release_publication(release)
        self.assertEqual(report.cap_blockers, CAP_ON_KEYS)
        self.assertFalse(report.cap_and_fingerprint_checks_pass)
        self.assertFalse(report.publishable)
        release.refresh_from_db()
        self.assertEqual(release.catalog_fingerprint, CATALOG_PLACEHOLDER)
        self.assertEqual(release.status, UniverseRelease.Status.DRAFT)

    def test_another_generator_version_has_no_scheme(self):
        league = League.objects.create(
            name='Old Generator League',
            slug='old-generator-league',
            tier=1,
            active=True,
        )
        release = _release(
            league,
            'fg-founding-1-release',
            CATALOG_PLACEHOLDER,
            VALIDATION_PLACEHOLDER,
        )
        release.generator_version = 'fg-founding-1'
        release.save()
        before = release.catalog_fingerprint
        report = evaluate_release_publication(release)
        self.assertEqual(report.fingerprint_verification, 'unsupported')
        self.assertEqual(report.fingerprint_scheme, ())
        self.assertIsNone(report.catalog_digest)
        self.assertIsNone(report.validation_digest)
        self.assertFalse(report.cap_and_fingerprint_checks_pass)
        self.assertFalse(report.publishable)
        self.assertEqual(report.horizon_status, 'unsupported')
        self.assertEqual(report.expansion_status, 'unsupported')
        self.assertEqual(report.membership_status, 'unsupported')
        self.assertEqual(report.quota_status, 'unsupported')
        self.assertEqual(report.population_status, 'unsupported')
        self.assertEqual(report.history_events_status, 'unsupported')
        self.assertEqual(report.draft_order_status, 'unsupported')
        self.assertEqual(report.opening_depth_status, 'unsupported')
        self.assertEqual(report.required_unresolved, ())
        release.refresh_from_db()
        self.assertEqual(release.catalog_fingerprint, before)
        self.assertEqual(release.generator_version, 'fg-founding-1')

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
        self.assertEqual(report.fingerprint_verification, 'mismatch')
        self.assertEqual(report.unverified_placeholder_fields, ())
        self.assertIsNotNone(report.catalog_digest)
        self.assertFalse(report.catalog_digest_match)
        self.assertFalse(report.cap_and_fingerprint_checks_pass)
        self.assertFalse(report.publishable)
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

    def test_valid_fingerprints_do_not_publish_an_incomplete_release(self):
        league = League.objects.create(
            name='Incomplete League',
            slug='incomplete-league',
            tier=1,
            active=True,
        )
        release = _release(league, 'fg-incomplete', '', '')
        _store_cards(release)
        catalog, validation = release_digests(release)
        UniverseRelease.objects.filter(pk=release.pk).update(
            catalog_fingerprint=catalog,
            validation_fingerprint=validation,
        )
        release.refresh_from_db()
        before = _snapshot(release)
        report = evaluate_release_publication(release)
        self.assertEqual(report.cap_blockers, ())
        self.assertEqual(report.fingerprint_verification, 'match')
        self.assertTrue(report.catalog_digest_match)
        self.assertTrue(report.validation_digest_match)
        self.assertTrue(report.cap_and_fingerprint_checks_pass)
        self.assertEqual(report.quota_status, 'pass')
        self.assertEqual(report.staff_signature_status, 'pass')
        self.assertEqual(report.horizon_status, 'fail')
        self.assertEqual(report.expansion_status, 'fail')
        self.assertEqual(report.membership_status, 'fail')
        self.assertEqual(report.population_status, 'fail')
        self.assertEqual(report.history_events_status, 'pass')
        self.assertEqual(report.draft_order_status, 'fail')
        self.assertEqual(report.opening_depth_status, 'pass')
        self.assertEqual(report.required_unresolved, ())
        self.assertFalse(any('passes.' in item for item in report.informational_requirements))
        self.assertFalse(report.publishable)
        self.assertIn('The release is not publishable.', report.messages)
        self.assertEqual(_snapshot(release), before)
        self.assertEqual(release.status, UniverseRelease.Status.DRAFT)
        self.assertIsNone(release.published_at)
        self.assertEqual(Timeline.objects.count(), 0)

    def test_a_quota_outside_the_documented_limits_fails(self):
        league = League.objects.create(
            name='Quota League',
            slug='quota-league',
            tier=1,
            active=True,
        )
        release = _release(league, 'fg-quota', '', '')
        _store_cards(release)
        EraRuleProfile.objects.filter(
            release=release,
            profile_key='y1970',
        ).update(quota_active_qb=3, active_limit=41)
        report = evaluate_release_publication(release)
        self.assertEqual(report.quota_status, 'fail')
        self.assertFalse(report.publishable)
        self.assertTrue(any('y1970' in failure for failure in report.quota_failures))
        self.assertEqual(
            EraRuleProfile.objects.get(release=release, profile_key='y1970').active_limit,
            41,
        )

    def test_a_repeated_gm_signature_fails(self):
        league = League.objects.create(
            name='Signature League',
            slug='signature-league',
            tier=1,
            active=True,
        )
        release = _release(league, 'fg-signature', '', '')
        signature = {
            'strength_1': 'quarterback_eye',
            'strength_2': 'lineman_eye',
            'weakness': 'finds_speed',
            'trait_1': 'patient',
            'trait_2': 'secretive',
        }
        for index in (1, 2):
            identity = UniverseStaffIdentity.objects.create(
                release=release,
                universe_key=f'g-fg-signature-1970-00{index}',
                name=f'GM {index}',
                birth_year=1940,
            )
            UniverseGM.objects.create(
                release=release,
                identity=identity,
                universe_key=identity.universe_key,
                name=identity.name,
                birth_year=1940,
                debut_year=1970,
                former_role='scout',
                positional_priority_1='quarterback',
                positional_priority_2='skill',
                **signature,
            )
        report = evaluate_release_publication(release)
        self.assertEqual(report.staff_signature_status, 'fail')
        self.assertTrue(report.staff_signature_failures)
        self.assertFalse(report.publishable)
        self.assertEqual(release.gms.count(), 2)

    def test_one_history_event_fails_the_zero_event_gate(self):
        league = League.objects.create(
            name='History League',
            slug='history-league',
            tier=1,
            active=True,
        )
        release = _release(league, 'fg-history', '', '')
        franchise = UniverseFranchise.objects.create(
            release=release,
            placeholder_slug='founding-01',
            display_name='Founding franchise 01',
            origin=UniverseFranchise.Origin.FOUNDING,
            first_active_season=1970,
        )
        before = _snapshot(release)
        FranchiseHistoryEvent.objects.create(
            franchise=franchise,
            season_year=1970,
            kind=FranchiseHistoryEvent.Kind.RENAME,
            previous_name='Founding franchise 01',
            new_name='Renamed franchise 01',
        )
        statements = []

        def wrap(execute, sql, params, many, context):
            statements.append(sql)
            return execute(sql, params, many, context)

        connection.execute_wrappers.append(wrap)
        try:
            report = evaluate_release_publication(release)
        finally:
            connection.execute_wrappers.remove(wrap)
        self.assertEqual(report.history_events_status, 'fail')
        self.assertEqual(
            report.history_events_failures,
            ('Franchise history events are 1, not zero.',),
        )
        self.assertEqual(report.draft_order_status, 'fail')
        self.assertEqual(report.opening_depth_status, 'pass')
        self.assertFalse(report.publishable)
        self.assertFalse(any(
            sql.lstrip().split(None, 1)[0].upper() in ('INSERT', 'UPDATE', 'DELETE', 'REPLACE')
            for sql in statements
        ))
        self.assertEqual(_snapshot(release), before)
        self.assertEqual(release.status, UniverseRelease.Status.DRAFT)
        self.assertIsNone(release.published_at)
        self.assertEqual(FranchiseHistoryEvent.objects.filter(franchise=franchise).count(), 1)

    def test_active_depth_one_and_taxi_depth_one_are_legal(self):
        release, profile, college, franchise = _depth_world('depth-legal')
        _assignment(release, profile, college, franchise, 'depth-legal-starter', 'starter', 1)
        _assignment(release, profile, college, franchise, 'depth-legal-taxi', 'taxi', 1)
        before = _assignment_rows(release)
        report = _read_only_report(self, release)
        self.assertEqual(report.opening_depth_status, 'pass')
        self.assertEqual(report.opening_depth_failures, ())
        self.assertEqual(_assignment_rows(release), before)

    def test_a_duplicate_within_the_active_bucket_fails(self):
        release, profile, college, franchise = _depth_world('depth-active')
        _assignment(release, profile, college, franchise, 'depth-active-starter', 'starter', 2)
        _assignment(release, profile, college, franchise, 'depth-active-backup', 'backup', 2)
        report = _read_only_report(self, release)
        self.assertEqual(report.opening_depth_status, 'fail')
        self.assertIn(
            'Active depth 2 for QB is duplicated.',
            report.opening_depth_failures,
        )

    def test_a_duplicate_within_the_taxi_bucket_fails(self):
        release, profile, college, franchise = _depth_world('depth-taxi')
        _assignment(release, profile, college, franchise, 'depth-taxi-a', 'taxi', 1)
        _assignment(release, profile, college, franchise, 'depth-taxi-b', 'taxi', 1)
        report = _read_only_report(self, release)
        self.assertEqual(report.opening_depth_status, 'fail')
        self.assertIn(
            'Taxi depth 1 for QB is duplicated.',
            report.opening_depth_failures,
        )

    def test_invalid_rostered_and_free_agent_rows_fail(self):
        legal = (
            {'role': 'starter', 'depth': 1, 'franchise_id': 1, 'player__position': 'QB'},
            {'role': 'taxi', 'depth': 1, 'franchise_id': 1, 'player__position': 'QB'},
            {'role': 'free_agent', 'depth': None, 'franchise_id': None, 'player__position': 'QB'},
        )
        self.assertEqual(_opening_depth_from_rows(legal), ('pass', ()))
        cases = (
            {'role': 'starter', 'depth': 1, 'franchise_id': None, 'player__position': 'QB'},
            {'role': 'starter', 'depth': None, 'franchise_id': 1, 'player__position': 'QB'},
            {'role': 'backup', 'depth': 0, 'franchise_id': 1, 'player__position': 'QB'},
            {'role': 'taxi', 'depth': 1, 'franchise_id': 1, 'player__position': 'XX'},
            {'role': 'free_agent', 'depth': 1, 'franchise_id': None, 'player__position': 'QB'},
            {'role': 'free_agent', 'depth': None, 'franchise_id': 1, 'player__position': 'QB'},
        )
        for row in cases:
            status, failures = _opening_depth_from_rows((row,))
            self.assertEqual(status, 'fail', row)
            self.assertTrue(failures, row)

    def test_zero_standings_and_the_founding_slugs_pass_draft_order(self):
        release = _founding_release('fg-draft-pass')
        before = _snapshot(release)
        report = _read_only_report(self, release)
        self.assertEqual(report.draft_order_status, 'pass')
        self.assertEqual(report.draft_order_failures, ())
        self.assertEqual(HistoricalStanding.objects.filter(franchise__release=release).count(), 0)
        self.assertEqual(_snapshot(release), before)
        self.assertEqual(Timeline.objects.count(), 0)

    def test_a_historical_standing_fails_the_draft_order_gate(self):
        release = _founding_release('fg-draft-standing')
        franchise = release.franchises.get(placeholder_slug='founding-01')
        HistoricalStanding.objects.create(
            franchise=franchise,
            season_year=1969,
            wins=0,
            losses=0,
            ties=0,
        )
        before = _snapshot(release)
        report = _read_only_report(self, release)
        self.assertEqual(report.draft_order_status, 'fail')
        self.assertEqual(
            report.draft_order_failures,
            ('Historical standings are 1, not zero.',),
        )
        self.assertFalse(report.publishable)
        self.assertEqual(HistoricalStanding.objects.filter(franchise__release=release).count(), 1)
        self.assertEqual(_snapshot(release), before)
        self.assertEqual(release.status, UniverseRelease.Status.DRAFT)
        self.assertIsNone(release.published_at)

    def test_candidate_fingerprints_are_compared_without_writes(self):
        league = League.objects.create(
            name='Candidate League',
            slug='candidate-league',
            tier=1,
            active=True,
        )
        release = _release(league, 'fg-candidate', '', '')
        stored = (release.catalog_fingerprint, release.validation_fingerprint)
        catalog, validation = release_digests(release)
        statements = []

        def wrap(execute, sql, params, many, context):
            statements.append(sql)
            return execute(sql, params, many, context)

        connection.execute_wrappers.append(wrap)
        try:
            stored_report = evaluate_release_publication(release)
            candidate_report = evaluate_release_publication(
                release,
                candidate_fingerprints=(catalog, validation),
            )
        finally:
            connection.execute_wrappers.remove(wrap)
        self.assertFalse(any(
            sql.lstrip().split(None, 1)[0].upper() in (
                'INSERT', 'UPDATE', 'DELETE', 'REPLACE',
            )
            for sql in statements
        ))
        self.assertFalse(stored_report.catalog_digest_match)
        self.assertFalse(stored_report.validation_digest_match)
        self.assertEqual(stored_report.fingerprint_verification, 'mismatch')
        self.assertIn(
            'does not match the stored catalog fingerprint',
            '\n'.join(stored_report.messages),
        )
        self.assertTrue(candidate_report.catalog_digest_match)
        self.assertTrue(candidate_report.validation_digest_match)
        self.assertEqual(candidate_report.fingerprint_verification, 'match')
        self.assertEqual(candidate_report.catalog_digest, catalog)
        self.assertEqual(candidate_report.validation_digest, validation)
        self.assertIn(
            'matches the candidate catalog fingerprint',
            '\n'.join(candidate_report.messages),
        )
        self.assertEqual(
            (release.catalog_fingerprint, release.validation_fingerprint),
            stored,
        )
        release.refresh_from_db()
        self.assertEqual(
            (release.catalog_fingerprint, release.validation_fingerprint),
            stored,
        )
        self.assertEqual(release.status, UniverseRelease.Status.DRAFT)
        self.assertIsNone(release.published_at)


def _depth_world(slug):
    league = League.objects.create(name=slug, slug=slug, tier=1, active=True)
    release = _release(league, slug, '', '')
    profile = EraRuleProfile(release=release, **y1970_values())
    profile.save()
    college = College.objects.create(release=release, slug='test-college', name='Test College')
    franchise = UniverseFranchise.objects.create(
        release=release,
        placeholder_slug='founding-01',
        display_name='Founding franchise 01',
        origin=UniverseFranchise.Origin.FOUNDING,
        first_active_season=1970,
    )
    return release, profile, college, franchise


def _assignment(release, profile, college, franchise, key, role, depth):
    player = UniversePlayer.objects.create(
        release=release,
        college=college,
        origin_profile=profile,
        universe_key=key,
        name=key,
        birth_year=1945,
        birth_day=10,
        position='QB',
        handedness=UniversePlayer.Handedness.RIGHT,
        height_in=72,
        weight_lb=210,
        cohort=UniversePlayer.Cohort.OPENING,
        first_pro_year=1969,
        peak_age=28,
        development_rate=40,
        decline_rate=30,
    )
    return OpeningAssignment.objects.create(
        release=release,
        player=player,
        franchise=franchise,
        role=role,
        depth=depth,
    )


def _assignment_rows(release):
    return list(release.opening_assignments.order_by('id').values_list(
        'id', 'player_id', 'franchise_id', 'role', 'depth',
    ))


def _founding_release(slug):
    league = League.objects.create(name=slug, slug=slug, tier=1, active=True)
    release = _release(league, slug, '', '')
    for number in range(1, 27):
        UniverseFranchise.objects.create(
            release=release,
            placeholder_slug=f'founding-{number:02d}',
            display_name=f'Founding franchise {number:02d}',
            origin=UniverseFranchise.Origin.FOUNDING,
            first_active_season=1970,
        )
    return release


def _read_only_report(test, release):
    statements = []

    def wrap(execute, sql, params, many, context):
        statements.append(sql)
        return execute(sql, params, many, context)

    connection.execute_wrappers.append(wrap)
    try:
        report = evaluate_release_publication(release)
    finally:
        connection.execute_wrappers.remove(wrap)
    test.assertFalse(any(
        sql.lstrip().split(None, 1)[0].upper() in ('INSERT', 'UPDATE', 'DELETE', 'REPLACE')
        for sql in statements
    ))
    return report
