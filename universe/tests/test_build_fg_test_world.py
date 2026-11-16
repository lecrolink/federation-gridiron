"""Orchestration tests for the staged fg-1970 test-world command.

Service boundaries are mocked. These tests do not generate the 21,155-player
world, connect to MySQL, or call PythonAnywhere.
"""

from contextlib import ExitStack
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import SimpleTestCase, TestCase
from django.test.utils import CaptureQueriesContext

from leagues.models import League
from timelines.models import (
    ScheduleGame,
    ScheduleWeekAssignment,
    Timeline,
    TimelineScheduleSlotAssignment,
    TimelineSeason,
)
from timelines.services.historical_1970 import (
    generate_historical_timeline_schedule_1970,
    historical_fixture_state,
)
from universe.generation.historical_1970 import load_historical_schedule_1970
from universe.generation.opening_players import y1970_values
from universe.generation.schedule import generate_schedule_release
from universe.generation.player_counts import (
    opening_cohort_count,
    opening_free_agent_count,
    opening_rostered_count,
    playable_identity_count,
    prospect_totals,
)
from universe.generation.player_names import COLLEGES
from universe.generation.schedule import (
    GENERATOR_VERSION,
    MEMBERSHIP_COUNT,
    YEAR_COUNTS,
)
from universe.historical import HistoricalScheduleSlot
from universe.models import (
    FranchiseSeasonMembership,
    ProvisionalFranchiseName,
    UniverseFranchise,
    UniverseRelease,
)
from universe.rules import EraRuleProfile
from universe.world_build import (
    EXPECTED,
    FIXTURE_COUNTS,
    PRODUCTION_DATABASE_NAME,
    SCHEDULE_KEYS,
    SEASON_SIZES,
    TEST_DATABASE_NAME,
    TIMELINE_COUNTS,
    alignments_are_exact,
    classify_world,
    empty_snapshot,
    exact_snapshot,
    names_are_exact,
    same_counts,
    template_status,
)
from timelines.services.timeline_start import (
    BACKUP_COUNT,
    FREE_AGENT_COUNT,
    ROSTERED_COUNT,
    STARTER_COUNT,
    TAXI_COUNT,
)


ROOT = Path(__file__).resolve().parents[2]
COMMAND = ROOT / 'universe' / 'management' / 'commands' / 'build_fg_test_world.py'
WORLD = ROOT / 'universe' / 'world_build.py'
CATALOG = 'ab' * 32
VALIDATION = 'cd' * 32


def _classify(snapshot, **overrides):
    fields = {
        'status': 'draft',
        'generator_version': 'pending',
        'fingerprint_kind': 'blank',
        'published': False,
        'template': 'ABSENT',
        'timeline_count': 0,
        'timeline_exact': False,
        'fixtures': 'ABSENT',
    }
    fields.update(overrides)
    return classify_world(snapshot, **fields)


def _schedule_snapshot():
    snapshot = empty_snapshot()
    for key in SCHEDULE_KEYS:
        snapshot[key] = EXPECTED[key]
    snapshot['season_sizes'] = dict(SEASON_SIZES)
    snapshot['names_exact'] = True
    snapshot['alignments_exact'] = True
    return snapshot


def _published_fields():
    return {
        'status': 'published',
        'generator_version': GENERATOR_VERSION,
        'fingerprint_kind': 'stored',
        'published': True,
    }


class ExpectedCountTests(SimpleTestCase):
    def test_approved_counts_match_the_generators(self):
        draftable, undrafted, prospects = prospect_totals()
        self.assertEqual(EXPECTED['franchises'], 32)
        self.assertEqual(EXPECTED['memberships'], 1797)
        self.assertEqual(EXPECTED['memberships'], MEMBERSHIP_COUNT)
        self.assertEqual(EXPECTED['replaced_names'], 32)
        self.assertEqual(EXPECTED['expansion_events'], 4)
        self.assertEqual(EXPECTED['expansion_clubs'], 6)
        self.assertEqual(EXPECTED['staff_identities'], 200)
        self.assertEqual(EXPECTED['general_managers'], 100)
        self.assertEqual(EXPECTED['head_coaches'], 100)
        self.assertEqual(EXPECTED['seated_general_managers'], 26)
        self.assertEqual(EXPECTED['seated_head_coaches'], 26)
        self.assertEqual(EXPECTED['colleges'], 64)
        self.assertEqual(EXPECTED['colleges'], len(COLLEGES))
        self.assertEqual(EXPECTED['era_profiles'], 12)
        self.assertEqual(EXPECTED['draft_classes'], 60)
        self.assertEqual(EXPECTED['opening_players'], 1378)
        self.assertEqual(EXPECTED['opening_players'], opening_cohort_count())
        self.assertEqual(EXPECTED['opening_assignments'], 1378)
        self.assertEqual(EXPECTED['starters'], 494)
        self.assertEqual(EXPECTED['starters'], STARTER_COUNT)
        self.assertEqual(EXPECTED['backups'], 546)
        self.assertEqual(EXPECTED['backups'], BACKUP_COUNT)
        self.assertEqual(EXPECTED['taxi'], 208)
        self.assertEqual(EXPECTED['taxi'], TAXI_COUNT)
        self.assertEqual(EXPECTED['opening_free_agents'], 130)
        self.assertEqual(EXPECTED['opening_free_agents'], opening_free_agent_count())
        self.assertEqual(EXPECTED['prospects'], 19777)
        self.assertEqual(EXPECTED['prospects'], prospects)
        self.assertEqual(EXPECTED['draftable_prospects'], 16183)
        self.assertEqual(EXPECTED['draftable_prospects'], draftable)
        self.assertEqual(EXPECTED['undrafted_prospects'], 3594)
        self.assertEqual(EXPECTED['undrafted_prospects'], undrafted)
        self.assertEqual(EXPECTED['players'], 21155)
        self.assertEqual(EXPECTED['players'], playable_identity_count())
        self.assertEqual(EXPECTED['rating_profiles'], 21155)
        self.assertEqual(TIMELINE_COUNTS['rostered'], 1248)
        self.assertEqual(TIMELINE_COUNTS['rostered'], ROSTERED_COUNT)
        self.assertEqual(TIMELINE_COUNTS['rostered'], opening_rostered_count())
        self.assertEqual(TIMELINE_COUNTS['free_agents'], 130)
        self.assertEqual(TIMELINE_COUNTS['free_agents'], FREE_AGENT_COUNT)
        self.assertEqual(FIXTURE_COUNTS['slot_assignments'], 26)
        self.assertEqual(FIXTURE_COUNTS['games'], 182)
        self.assertEqual(FIXTURE_COUNTS['weeks'], 182)
        self.assertEqual(FIXTURE_COUNTS['byes'], 0)
        self.assertEqual(
            SEASON_SIZES,
            {year: YEAR_COUNTS[year] for year in SEASON_SIZES},
        )
        self.assertEqual(TEST_DATABASE_NAME, 'lecrolink$fg_test')
        self.assertEqual(PRODUCTION_DATABASE_NAME, 'lecrolink$fg')
        self.assertNotEqual(TEST_DATABASE_NAME, PRODUCTION_DATABASE_NAME)

    def test_new_sources_do_not_call_forbidden_commands(self):
        sources = COMMAND.read_text() + WORLD.read_text()
        for forbidden in (
            'fg-founding-1',
            'generate_founding_release',
            'manage.py flush',
            'manage.py migrate',
            'manage.py loaddata',
            'manage.py dumpdata',
            'manage.py sqlflush',
            'manage.py dbshell',
            'sqltruncate',
            '.delete(',
            'API_TOKEN',
            'PRIVATE_KEY',
        ):
            self.assertNotIn(forbidden, sources)
        self.assertIn("name == 'lecrolink$fg'", COMMAND.read_text())
        self.assertIn("name != 'lecrolink$fg_test'", COMMAND.read_text())
        self.assertIn("TEST_DATABASE_NAME = 'lecrolink$fg_test'", WORLD.read_text())
        self.assertIn("PRODUCTION_DATABASE_NAME = 'lecrolink$fg'", WORLD.read_text())


class ClassificationTests(SimpleTestCase):
    def test_empty_draft_and_later_exact_states(self):
        self.assertEqual(_classify(empty_snapshot()), 'EMPTY_DRAFT')
        self.assertEqual(
            _classify(empty_snapshot(), template='DIFFERENT'),
            'EMPTY_DRAFT',
        )
        built = _classify(
            exact_snapshot(),
            generator_version=GENERATOR_VERSION,
        )
        self.assertEqual(built, 'BUILT_DRAFT')
        published = _classify(exact_snapshot(), template='DIFFERENT', **_published_fields())
        self.assertEqual(published, 'PUBLISHED')
        started = _classify(
            exact_snapshot(),
            template='EXACT',
            timeline_count=1,
            timeline_exact=True,
            fixtures='EXACT',
            **_published_fields(),
        )
        self.assertEqual(started, 'STARTED_1970')

    def test_partial_prefixes_and_invalid_states(self):
        partial = _classify(
            _schedule_snapshot(),
            generator_version=GENERATOR_VERSION,
        )
        self.assertEqual(partial, 'PARTIAL')
        resumed = _classify(
            exact_snapshot(),
            template='ABSENT',
            timeline_count=1,
            timeline_exact=True,
            fixtures='ABSENT',
            **_published_fields(),
        )
        self.assertEqual(resumed, 'PARTIAL')
        mismatched = exact_snapshot()
        mismatched['season_sizes'] = dict(SEASON_SIZES)
        mismatched['season_sizes'][1970] = 25
        self.assertEqual(
            _classify(mismatched, generator_version=GENERATOR_VERSION),
            'INVALID',
        )
        unnamed = _schedule_snapshot()
        unnamed['names_exact'] = False
        self.assertEqual(
            _classify(unnamed, generator_version=GENERATOR_VERSION),
            'INVALID',
        )
        self.assertEqual(
            _classify(empty_snapshot(), generator_version='fg-founding-2'),
            'INVALID',
        )
        self.assertEqual(
            _classify(exact_snapshot(), generator_version='pending'),
            'INVALID',
        )
        self.assertEqual(
            _classify(
                exact_snapshot(),
                generator_version=GENERATOR_VERSION,
                fingerprint_kind='stored',
            ),
            'INVALID',
        )
        self.assertEqual(
            _classify(exact_snapshot(), timeline_count=2, **_published_fields()),
            'INVALID',
        )
        self.assertEqual(
            _classify(
                exact_snapshot(),
                timeline_count=1,
                timeline_exact=False,
                **_published_fields(),
            ),
            'INVALID',
        )
        self.assertEqual(
            _classify(
                exact_snapshot(),
                template='EXACT',
                timeline_count=1,
                timeline_exact=True,
                fixtures='DIFFERENT',
                **_published_fields(),
            ),
            'INVALID',
        )
        self.assertFalse(same_counts(exact_snapshot(), empty_snapshot()))
        changed = exact_snapshot()
        changed['players'] = 1
        self.assertFalse(same_counts(exact_snapshot(), changed))


class WorldCommandTests(TestCase):
    def setUp(self):
        self._database_name = settings_name()
        set_database_name(TEST_DATABASE_NAME)
        self.archive = League.objects.create(
            name='Archive',
            slug='archive',
            tier=1,
            active=False,
        )
        self.reader = get_user_model().objects.create(
            username='reader',
            is_active=True,
            is_superuser=False,
        )
        self.league = League.objects.create(
            name='Federation',
            slug='federation',
            tier=1,
            active=True,
        )
        self.release = UniverseRelease.objects.create(
            slug='fg-1970',
            root_league=self.league,
            generator_version='pending',
            seed=1970,
            founding_franchise_count=26,
            expansion_schedule_version=3,
            first_season_year=1970,
            season_count=60,
            last_season_year=2029,
        )
        self.user = get_user_model().objects.create(
            username='lecrolink',
            is_active=True,
            is_superuser=True,
            is_staff=True,
        )
        self.user.set_unusable_password()
        self.user.save()

    def tearDown(self):
        set_database_name(self._database_name)

    def _run(self, stage='VERIFY'):
        stdout = StringIO()
        call_command('build_fg_test_world', stage=stage, stdout=stdout)
        return stdout.getvalue()

    def _fail(self, stage='VERIFY'):
        stdout = StringIO()
        with self.assertRaises(CommandError) as caught:
            call_command('build_fg_test_world', stage=stage, stdout=stdout)
        return str(caught.exception), stdout.getvalue()

    def test_verify_is_read_only_and_reports_the_resolved_rows(self):
        with self._service_patches() as mocks:
            output = self._run('VERIFY')
        for mock in mocks.values():
            mock.assert_not_called()
        self.assertEqual(_result_lines(output), ['world_result=readonly'])
        self.assertIn('classification=EMPTY_DRAFT', output)
        self.assertIn('template=ABSENT', output)
        self.assertIn('fixtures=ABSENT', output)
        self.assertIn('migrations_pending=0', output)
        self.assertIn(f'database={TEST_DATABASE_NAME}', output)
        self.assertIn(f'league_id={self.league.pk}', output)
        self.assertIn(f'release_id={self.release.pk}', output)
        self.assertIn(f'user_id={self.user.pk}', output)
        self.assertNotEqual(self.league.pk, self.archive.pk)
        self.assertNotEqual(self.user.pk, self.reader.pk)
        self.assertNotIn(f'league_id={self.archive.pk}', output)
        self.assertNotIn(f'user_id={self.reader.pk}', output)
        self.assertIn('count.franchises=0', output)
        self.assertIn('count.players=0', output)
        self.assertIn('count.rating_profiles=0', output)
        self.assertIn('count.season.1970=0', output)
        self.assertIn('count.season.2029=0', output)
        self.assertEqual(self.release.franchises.count(), 0)
        self.assertEqual(HistoricalScheduleSlot.objects.count(), 0)

    def test_second_league_release_user_or_timeline_is_refused(self):
        League.objects.create(
            name='Federation',
            slug='other-federation',
            tier=1,
            active=False,
        )
        message, output = self._fail('VERIFY')
        self.assertIn('More than one tier-1 Federation league', message)
        self.assertEqual(_result_lines(output), ['world_result=failure'])

    def test_second_release_is_refused(self):
        UniverseRelease.objects.create(
            slug='other-release',
            root_league=self.league,
            generator_version='pending',
            seed=1970,
            founding_franchise_count=26,
            expansion_schedule_version=3,
            first_season_year=1970,
            season_count=60,
            last_season_year=2029,
        )
        message, output = self._fail('BUILD_DRAFT')
        self.assertIn('Exactly one universe release', message)
        self.assertEqual(_result_lines(output), ['world_result=failure'])

    def test_second_active_superuser_is_refused(self):
        get_user_model().objects.create(
            username='other-admin',
            is_active=True,
            is_superuser=True,
            is_staff=True,
        )
        message, output = self._fail('PUBLISH')
        self.assertIn('Exactly one active superuser', message)
        self.assertNotIn('world_result=writes', output)

    def test_template_status_uses_the_real_loader_comparison(self):
        self.assertEqual(template_status(), 'ABSENT')
        self.assertEqual(load_historical_schedule_1970(), 'created')
        self.assertEqual(template_status(), 'EXACT')
        self.assertEqual(load_historical_schedule_1970(), 'unchanged')

    def test_a_different_template_is_not_exact(self):
        HistoricalScheduleSlot.objects.bulk_create([
            HistoricalScheduleSlot(
                season_year=1970,
                conference_role='afc',
                division_role='east',
                slot_number=1,
                provenance_club='not-a-template-club',
                opening_basis='merger-placement',
                source_citation='test citation',
            ),
        ])
        self.assertEqual(template_status(), 'DIFFERENT')
        self.assertEqual(HistoricalScheduleSlot.objects.count(), 1)

    def test_build_calls_the_writers_in_order_and_does_not_publish(self):
        order = []
        snapshots = [empty_snapshot(), exact_snapshot()]

        def snapshot(_release):
            return snapshots.pop(0)

        def schedule(league_id, seed):
            order.append(('schedule', league_id, seed))
            self.release.generator_version = GENERATOR_VERSION
            self.release.save()
            return 'created'

        def opening(league_id, seed):
            order.append(('opening', league_id, seed))
            return 'created'

        def prospects(league_id, seed):
            order.append(('prospects', league_id, seed))
            return 'unchanged'

        def ratings(league_id, seed):
            order.append(('ratings', league_id, seed))
            return 'rebuilt'

        def digests(_release):
            order.append(('digests',))
            return CATALOG, VALIDATION

        def evaluate(_release, *, candidate_fingerprints):
            order.append(('evaluate', candidate_fingerprints))
            return SimpleNamespace(publishable=True, messages=())

        with self._service_patches(
            product_snapshot=snapshot,
            generate_schedule_release=schedule,
            generate_opening_players=opening,
            generate_prospects=prospects,
            generate_player_ratings=ratings,
            release_digests=digests,
            evaluate_release_publication=evaluate,
        ):
            output = self._run('BUILD_DRAFT')
        self.assertEqual(
            [name for name, *_rest in order],
            ['schedule', 'opening', 'prospects', 'ratings', 'digests', 'evaluate'],
        )
        self.assertEqual(order[0], ('schedule', self.league.pk, 1970))
        self.assertEqual(order[1], ('opening', self.league.pk, 1970))
        self.assertEqual(order[4], ('digests',))
        self.assertEqual(order[5][1], (CATALOG, VALIDATION))
        self.assertEqual(_result_lines(output), ['world_result=writes'])
        self.assertIn('writer=schedule outcome=created', output)
        self.assertIn('writer=opening outcome=created', output)
        self.assertIn('writer=prospects outcome=unchanged', output)
        self.assertIn('writer=ratings outcome=rebuilt', output)
        self.assertIn('writer=publication_ready outcome=pass', output)
        self.release.refresh_from_db()
        self.assertEqual(self.release.status, 'draft')
        self.assertEqual(self.release.catalog_fingerprint, '')
        self.assertEqual(self.release.validation_fingerprint, '')
        self.assertIsNone(self.release.published_at)

    def test_exact_build_rerun_is_unchanged(self):
        self.release.generator_version = GENERATOR_VERSION
        self.release.save()
        with self._service_patches(
            product_snapshot=lambda _release: exact_snapshot(),
            generate_schedule_release=lambda *_args: 'unchanged',
            generate_opening_players=lambda *_args: 'unchanged',
            generate_prospects=lambda *_args: 'unchanged',
            generate_player_ratings=lambda *_args: 'unchanged',
            release_digests=lambda _release: (CATALOG, VALIDATION),
            evaluate_release_publication=lambda *_args, **_kwargs: SimpleNamespace(
                publishable=True,
                messages=(),
            ),
        ) as mocks:
            output = self._run('BUILD_DRAFT')
        mocks['publish_universe_release'].assert_not_called()
        self.assertEqual(_result_lines(output), ['world_result=unchanged'])

    def test_a_later_writer_failure_stops_the_chain(self):
        def schedule(league_id, seed):
            self.release.generator_version = GENERATOR_VERSION
            self.release.save()
            return 'created'

        def opening(_league_id, _seed):
            raise RuntimeError('opening failed')

        with self._service_patches(
            product_snapshot=lambda _release: empty_snapshot(),
            generate_schedule_release=schedule,
            generate_opening_players=opening,
        ) as mocks:
            message, output = self._fail('BUILD_DRAFT')
        self.assertIn('opening failed', message)
        mocks['generate_prospects'].assert_not_called()
        mocks['generate_player_ratings'].assert_not_called()
        mocks['publish_universe_release'].assert_not_called()
        self.assertEqual(_result_lines(output), ['world_result=failure'])
        self.assertIn('writer=schedule outcome=created', output)

    def test_mismatched_or_guarded_drafts_do_not_call_writers(self):
        mismatched = empty_snapshot()
        mismatched['franchises'] = 3
        cases = (
            ('mismatched', mismatched, 'pending', ''),
            ('other-generator', empty_snapshot(), 'fg-founding-2', ''),
            ('fingerprints', exact_snapshot(), GENERATOR_VERSION, CATALOG),
        )
        for name, snapshot, generator, fingerprint in cases:
            with self.subTest(name=name):
                self.release.generator_version = generator
                self.release.catalog_fingerprint = fingerprint
                self.release.validation_fingerprint = fingerprint
                self.release.save()
                with self._service_patches(
                    product_snapshot=lambda _release, snapshot=snapshot: snapshot,
                ) as mocks:
                    message, output = self._fail('BUILD_DRAFT')
                mocks['generate_schedule_release'].assert_not_called()
                mocks['generate_opening_players'].assert_not_called()
                self.assertEqual(_result_lines(output), ['world_result=failure'])
                self.assertNotIn('world_result=writes', output)
                self.assertTrue(message)

    def test_a_partial_schedule_resumes_through_the_writers(self):
        self.release.generator_version = GENERATOR_VERSION
        self.release.save()
        snapshots = [_schedule_snapshot(), exact_snapshot()]
        with self._service_patches(
            product_snapshot=lambda _release: snapshots.pop(0),
            generate_schedule_release=lambda *_args: 'unchanged',
            generate_opening_players=lambda *_args: 'created',
            generate_prospects=lambda *_args: 'created',
            generate_player_ratings=lambda *_args: 'created',
            release_digests=lambda _release: (CATALOG, VALIDATION),
            evaluate_release_publication=lambda *_args, **_kwargs: SimpleNamespace(
                publishable=True,
                messages=(),
            ),
        ) as mocks:
            output = self._run('BUILD_DRAFT')
        mocks['generate_schedule_release'].assert_called_once_with(self.league.pk, 1970)
        mocks['generate_player_ratings'].assert_called_once_with(self.league.pk, 1970)
        self.assertEqual(_result_lines(output), ['world_result=writes'])

    def test_failed_readiness_does_not_store_fingerprints(self):
        snapshots = [empty_snapshot(), exact_snapshot()]

        def schedule(_league_id, _seed):
            self.release.generator_version = GENERATOR_VERSION
            self.release.save()
            return 'created'

        with self._service_patches(
            product_snapshot=lambda _release: snapshots.pop(0),
            generate_schedule_release=schedule,
            generate_opening_players=lambda *_args: 'created',
            generate_prospects=lambda *_args: 'created',
            generate_player_ratings=lambda *_args: 'created',
            release_digests=lambda _release: (CATALOG, VALIDATION),
            evaluate_release_publication=lambda *_args, **_kwargs: SimpleNamespace(
                publishable=False,
                messages=('The release is not publishable.',),
            ),
        ) as mocks:
            message, output = self._fail('BUILD_DRAFT')
        mocks['publish_universe_release'].assert_not_called()
        self.assertIn('Publication readiness failed.', message)
        self.assertEqual(_result_lines(output), ['world_result=failure'])
        self.release.refresh_from_db()
        self.assertEqual(self.release.catalog_fingerprint, '')
        self.assertEqual(self.release.validation_fingerprint, '')

    def test_publish_calls_the_service_and_checks_the_stored_digests(self):
        self.release.generator_version = GENERATOR_VERSION
        self.release.save()
        seen = {}

        def publish(*, slug, seed, generator_version, league_id):
            seen.update(
                slug=slug,
                seed=seed,
                generator_version=generator_version,
                league_id=league_id,
            )
            self.release.status = UniverseRelease.Status.PUBLISHED
            self.release.published_at = datetime(2026, 11, 15, tzinfo=timezone.utc)
            self.release.catalog_fingerprint = CATALOG
            self.release.validation_fingerprint = VALIDATION
            self.release.save()
            return SimpleNamespace(outcome='published', release_id=self.release.pk)

        with self._service_patches(
            product_snapshot=lambda _release: exact_snapshot(),
            publish_universe_release=publish,
            release_digests=lambda _release: (CATALOG, VALIDATION),
        ):
            output = self._run('PUBLISH')
        self.assertEqual(seen['slug'], 'fg-1970')
        self.assertEqual(seen['seed'], 1970)
        self.assertEqual(seen['generator_version'], GENERATOR_VERSION)
        self.assertEqual(seen['league_id'], self.league.pk)
        self.assertNotEqual(seen['league_id'], self.archive.pk)
        self.assertEqual(_result_lines(output), ['world_result=writes'])
        self.assertIn('writer=publish outcome=published', output)
        self.release.refresh_from_db()
        self.assertEqual(self.release.status, UniverseRelease.Status.PUBLISHED)
        self.assertIsNotNone(self.release.published_at)

    def test_exact_published_rerun_is_unchanged(self):
        self._publish_release()
        with self._service_patches(
            product_snapshot=lambda _release: exact_snapshot(),
            publish_universe_release=lambda **_kwargs: SimpleNamespace(outcome='unchanged'),
            release_digests=lambda _release: (CATALOG, VALIDATION),
        ) as mocks:
            output = self._run('PUBLISH')
        mocks['publish_universe_release'].assert_called_once_with(
            slug='fg-1970',
            seed=1970,
            generator_version=GENERATOR_VERSION,
            league_id=self.league.pk,
        )
        mocks['generate_schedule_release'].assert_not_called()
        self.assertEqual(_result_lines(output), ['world_result=unchanged'])

    def test_publish_refuses_an_empty_draft_before_the_service(self):
        with self._service_patches() as mocks:
            message, output = self._fail('PUBLISH')
        mocks['publish_universe_release'].assert_not_called()
        self.assertIn('exact built draft', message)
        self.assertEqual(_result_lines(output), ['world_result=failure'])

    def test_start_loads_an_absent_template_before_the_timeline(self):
        self._publish_release()
        order = []
        templates = ['ABSENT', 'EXACT']
        fixtures = ['ABSENT', 'EXACT']

        def load():
            order.append('load')
            return 'created'

        def start(**kwargs):
            order.append(('start', kwargs))
            timeline = self._timeline()
            return _started(timeline, 'created')

        def generate(*, timeline_id):
            order.append(('fixtures', timeline_id))
            return 'created'

        with self._service_patches(
            product_snapshot=lambda _release: exact_snapshot(),
            template_status=lambda: templates.pop(0),
            load_historical_schedule_1970=load,
            start_timeline=start,
            fixture_status=lambda _timeline: fixtures.pop(0),
            generate_historical_timeline_schedule_1970=generate,
        ) as mocks:
            output = self._run('START_1970')
        self.assertEqual(order[0], 'load')
        self.assertEqual(order[1][0], 'start')
        self.assertEqual(order[2][0], 'fixtures')
        self.assertEqual(order[1][1]['slug'], 'fg-1970')
        self.assertEqual(order[1][1]['seed'], 1970)
        self.assertEqual(order[1][1]['generator_version'], GENERATOR_VERSION)
        self.assertEqual(order[1][1]['user_id'], self.user.pk)
        self.assertEqual(order[1][1]['name'], 'Opening 1970')
        self.assertEqual(order[1][1]['franchise_slug'], 'founding-01')
        self.assertEqual(order[2][1], Timeline.objects.get().pk)
        mocks['generate_schedule_release'].assert_not_called()
        self.assertLess(output.index('writer=template outcome=created'), output.index('writer=timeline'))
        self.assertLess(output.index('writer=timeline'), output.index('writer=fixtures'))
        self.assertEqual(_result_lines(output), ['world_result=writes'])

    def test_exact_template_does_not_load_before_the_timeline(self):
        self._publish_release()

        def start(**_kwargs):
            return _started(self._timeline(), 'unchanged')

        with self._service_patches(
            product_snapshot=lambda _release: exact_snapshot(),
            template_status=lambda: 'EXACT',
            start_timeline=start,
            fixture_status=lambda _timeline: 'EXACT',
            generate_historical_timeline_schedule_1970=lambda *, timeline_id: 'unchanged',
        ) as mocks:
            output = self._run('START_1970')
        mocks['load_historical_schedule_1970'].assert_not_called()
        mocks['start_timeline'].assert_called_once()
        self.assertIn('writer=template outcome=unchanged', output)
        self.assertEqual(_result_lines(output), ['world_result=unchanged'])

    def test_different_template_stops_before_the_timeline(self):
        self._publish_release()
        HistoricalScheduleSlot.objects.bulk_create([
            HistoricalScheduleSlot(
                season_year=1970,
                conference_role='afc',
                division_role='east',
                slot_number=1,
                provenance_club='not-a-template-club',
                opening_basis='merger-placement',
                source_citation='test citation',
            ),
        ])
        with self._service_patches(
            product_snapshot=lambda _release: exact_snapshot(),
        ) as mocks:
            message, output = self._fail('START_1970')
        mocks['load_historical_schedule_1970'].assert_not_called()
        mocks['start_timeline'].assert_not_called()
        mocks['generate_historical_timeline_schedule_1970'].assert_not_called()
        self.assertIn('historical template differs', message)
        self.assertEqual(HistoricalScheduleSlot.objects.count(), 1)
        self.assertEqual(Timeline.objects.count(), 0)
        self.assertEqual(_result_lines(output), ['world_result=failure'])

    def test_a_draft_does_not_load_the_template_or_start(self):
        with self._service_patches(
            product_snapshot=lambda _release: exact_snapshot(),
        ) as mocks:
            message, output = self._fail('START_1970')
        mocks['load_historical_schedule_1970'].assert_not_called()
        mocks['start_timeline'].assert_not_called()
        self.assertIn('not an exact published world', message)
        self.assertEqual(_result_lines(output), ['world_result=failure'])

    def test_a_different_timeline_or_fixture_set_is_left_unchanged(self):
        self._publish_release()
        self._timeline(name='Some Other Campaign')
        with self._service_patches(
            product_snapshot=lambda _release: exact_snapshot(),
            template_status=lambda: 'EXACT',
        ) as mocks:
            message, output = self._fail('START_1970')
        mocks['start_timeline'].assert_not_called()
        mocks['load_historical_schedule_1970'].assert_not_called()
        mocks['generate_historical_timeline_schedule_1970'].assert_not_called()
        self.assertIn('different timeline', message)
        self.assertEqual(Timeline.objects.get().name, 'Some Other Campaign')
        self.assertEqual(_result_lines(output), ['world_result=failure'])

    def test_missing_fixtures_on_an_exact_timeline_are_completed(self):
        self._publish_release()
        timeline = self._timeline()
        fixtures = ['ABSENT', 'ABSENT', 'EXACT']
        with self._service_patches(
            product_snapshot=lambda _release: exact_snapshot(),
            template_status=lambda: 'EXACT',
            timeline_is_exact=lambda _timeline, _user: True,
            fixture_status=lambda _timeline: fixtures.pop(0),
            start_timeline=lambda **_kwargs: _started(timeline, 'unchanged'),
            generate_historical_timeline_schedule_1970=lambda *, timeline_id: 'created',
        ) as mocks:
            output = self._run('START_1970')
        mocks['load_historical_schedule_1970'].assert_not_called()
        mocks['generate_historical_timeline_schedule_1970'].assert_called_once_with(
            timeline_id=timeline.pk,
        )
        self.assertEqual(_result_lines(output), ['world_result=writes'])

    def test_different_fixtures_are_not_rewritten(self):
        self._publish_release()
        self._timeline()
        with self._service_patches(
            product_snapshot=lambda _release: exact_snapshot(),
            template_status=lambda: 'EXACT',
            timeline_is_exact=lambda _timeline, _user: True,
            fixture_status=lambda _timeline: 'DIFFERENT',
        ) as mocks:
            message, output = self._fail('START_1970')
        mocks['generate_historical_timeline_schedule_1970'].assert_not_called()
        mocks['start_timeline'].assert_not_called()
        self.assertIn('fixtures differ', message)
        self.assertEqual(_result_lines(output), ['world_result=failure'])

    def _publish_release(self):
        self.release.generator_version = GENERATOR_VERSION
        self.release.status = UniverseRelease.Status.PUBLISHED
        self.release.published_at = datetime(2026, 11, 15, tzinfo=timezone.utc)
        self.release.catalog_fingerprint = CATALOG
        self.release.validation_fingerprint = VALIDATION
        self.release.save()

    def _timeline(self, name='Opening 1970'):
        return Timeline.objects.create(
            created_by=self.user,
            release=self.release,
            name=name,
            start_season_year=1970,
            current_season_year=1970,
            season_index=1,
            phase=Timeline.Phase.DIRECTION,
            status=Timeline.Status.ACTIVE,
        )

    def _service_patches(self, **overrides):
        targets = {
            'generate_schedule_release': lambda *_args: 'created',
            'generate_opening_players': lambda *_args: 'created',
            'generate_prospects': lambda *_args: 'created',
            'generate_player_ratings': lambda *_args: 'created',
            'release_digests': lambda _release: (CATALOG, VALIDATION),
            'evaluate_release_publication': lambda *_args, **_kwargs: SimpleNamespace(
                publishable=True,
                messages=(),
            ),
            'publish_universe_release': lambda **_kwargs: SimpleNamespace(outcome='published'),
            'load_historical_schedule_1970': lambda: 'created',
            'start_timeline': lambda **_kwargs: SimpleNamespace(
                outcome='created',
                timeline_id=1,
                player_count=1378,
                rating_count=1378,
                membership_count=1378,
                rostered_count=1248,
                free_agent_count=130,
            ),
            'generate_historical_timeline_schedule_1970': lambda *, timeline_id: 'created',
        }
        targets.update(overrides)
        stack = ExitStack()
        self.addCleanup(stack.close)
        mocks = {}
        for name, value in targets.items():
            if callable(value) and not hasattr(value, 'assert_not_called'):
                mocks[name] = stack.enter_context(
                    patch(f'universe.world_build.{name}', side_effect=value)
                )
            else:
                mocks[name] = stack.enter_context(
                    patch(f'universe.world_build.{name}', value)
                )
        return stack.pop_all() if False else _open_stack(stack, mocks)


class SameCountCorruptionTests(TestCase):
    """Counts stay canonical while one stored fact is wrong.

    Catalog cases are reduced to the exact schedule draft before corruption.
    Fixture cases keep the stored 1970 timeline rows. Neither case generates
    opening players, prospects, or ratings.
    """

    @classmethod
    def setUpTestData(cls):
        league = League.objects.create(
            name='Federation',
            slug='federation',
            tier=1,
            active=True,
        )
        release = UniverseRelease.objects.create(
            slug='fg-1970',
            root_league=league,
            generator_version='pending',
            seed=1970,
            founding_franchise_count=26,
            expansion_schedule_version=3,
            first_season_year=1970,
            season_count=60,
            last_season_year=2029,
        )
        user = get_user_model().objects.create(
            username='lecrolink',
            is_active=True,
            is_superuser=True,
            is_staff=True,
        )
        user.set_unusable_password()
        user.save()
        if generate_schedule_release(league.pk, 1970) != 'created':
            raise AssertionError('The schedule release was not created.')
        release.refresh_from_db()
        EraRuleProfile(release=release, **y1970_values()).save()
        if load_historical_schedule_1970() != 'created':
            raise AssertionError('The 1970 historical template was not stored.')
        release.catalog_fingerprint = CATALOG
        release.validation_fingerprint = VALIDATION
        release.status = UniverseRelease.Status.PUBLISHED
        release.published_at = datetime(2026, 11, 15, tzinfo=timezone.utc)
        release.save()
        timeline = Timeline.objects.create(
            created_by=user,
            release=release,
            name='Opening 1970',
            start_season_year=1970,
            current_season_year=1970,
            season_index=1,
            phase=Timeline.Phase.DIRECTION,
            status=Timeline.Status.ACTIVE,
        )
        TimelineSeason.objects.create(
            timeline=timeline,
            profile=release.era_profiles.get(),
            season_year=1970,
            season_index=1,
            phase=Timeline.Phase.DIRECTION,
        )
        if generate_historical_timeline_schedule_1970(timeline_id=timeline.pk) != 'created':
            raise AssertionError('The 1970 fixtures were not created.')
        cls.league = league
        cls.release = release
        cls.user = user
        cls.timeline = timeline

    def setUp(self):
        self._database_name = settings_name()
        set_database_name(TEST_DATABASE_NAME)

    def tearDown(self):
        set_database_name(self._database_name)

    def test_uncorrupted_schedule_counts_are_exact(self):
        self.assertTrue(names_are_exact(self.release))
        self.assertTrue(alignments_are_exact(self.release))
        self.assertEqual(self._classify_schedule_layer(names_exact=True), 'PARTIAL')
        output = self._verify()
        self.assertIn('names_exact=yes', output)
        self.assertIn('alignments_exact=yes', output)
        self.assertIn('count.franchises=32', output)
        self.assertIn('count.memberships=1797', output)
        self.assertIn('count.replaced_names=32', output)
        self.assertIn('count.season.1970=26', output)

    def test_uncorrupted_fixtures_match_the_historical_plan(self):
        self.assertEqual(historical_fixture_state(self.timeline), 'EXACT')
        output = self._verify()
        self.assertIn('fixtures=EXACT', output)
        self.assertIn('names_exact=yes', output)
        self.assertIn('alignments_exact=yes', output)
        self.assertIn('count.fixture.slot_assignments=26', output)
        self.assertIn('count.fixture.games=182', output)
        self.assertIn('count.fixture.weeks=182', output)
        self.assertIn('count.fixture.byes=0', output)

    def test_one_changed_city_is_invalid_and_refused(self):
        row = self._name('founding-01')
        ProvisionalFranchiseName.objects.filter(pk=row.pk).update(city='Albany')
        output = self._verify()
        self.assertIn('classification=INVALID', output)
        self.assertIn('names_exact=no', output)
        self.assertIn('alignments_exact=yes', output)
        self.assertEqual(self._classify_schedule_layer(), 'INVALID')
        self.assertIn('count.franchises=32', output)
        self.assertIn('count.replaced_names=32', output)
        self.assertIn('count.memberships=1797', output)
        self._refuse_writers('The approved franchise names do not match.')
        self.assertEqual(self._name('founding-01').city, 'Albany')
        self.assertEqual(self.release.franchises.count(), 32)
        self.assertEqual(self.release.provisional_franchise_names.count(), 32)

    def test_one_changed_replaced_status_is_invalid_and_refused(self):
        row = self._name('founding-02')
        ProvisionalFranchiseName.objects.filter(pk=row.pk).update(
            city_status=ProvisionalFranchiseName.Status.PROVISIONAL,
        )
        output = self._verify()
        self.assertIn('classification=INVALID', output)
        self.assertIn('names_exact=no', output)
        self.assertEqual(self._classify_schedule_layer(), 'INVALID')
        self.assertFalse(names_are_exact(self.release))
        self._refuse_writers('The approved franchise names do not match.')
        self.assertEqual(
            self._name('founding-02').city_status,
            ProvisionalFranchiseName.Status.PROVISIONAL,
        )
        self.assertEqual(self.release.provisional_franchise_names.count(), 32)
        self.assertEqual(self.release.franchises.count(), 32)

    def test_one_changed_display_name_is_invalid_and_refused(self):
        franchise = self.release.franchises.get(placeholder_slug='founding-03')
        UniverseFranchise.objects.filter(pk=franchise.pk).update(display_name='Detroit Pilots')
        output = self._verify()
        self.assertIn('classification=INVALID', output)
        self.assertIn('names_exact=no', output)
        self.assertIn('alignments_exact=yes', output)
        self.assertEqual(self._classify_schedule_layer(), 'INVALID')
        self.assertIn('count.franchises=32', output)
        self.assertIn('count.replaced_names=32', output)
        self.assertIn('count.memberships=1797', output)
        self._refuse_writers('The approved franchise names do not match.')
        self.assertEqual(
            self.release.franchises.get(placeholder_slug='founding-03').display_name,
            'Detroit Pilots',
        )
        self.assertIsNone(
            self.release.franchises.get(placeholder_slug='founding-03').catalog_team_id,
        )

    def test_one_changed_membership_seat_is_invalid_and_refused(self):
        membership = FranchiseSeasonMembership.objects.get(
            franchise__release=self.release,
            franchise__placeholder_slug='founding-01',
            season_year=1970,
        )
        original = membership.slot_order
        FranchiseSeasonMembership.objects.filter(pk=membership.pk).update(
            slot_order=original + 1,
        )
        output = self._verify()
        self.assertIn('classification=INVALID', output)
        self.assertIn('names_exact=yes', output)
        self.assertIn('alignments_exact=no', output)
        self.assertEqual(self._classify_schedule_layer(), 'INVALID')
        self.assertIn('count.memberships=1797', output)
        self.assertIn('count.season.1970=26', output)
        self.assertIn('count.season.2002=32', output)
        self.assertIn('count.season.2029=32', output)
        self.assertFalse(alignments_are_exact(self.release))
        self._refuse_writers('The franchise-season alignments do not match.')
        self.assertEqual(
            FranchiseSeasonMembership.objects.get(pk=membership.pk).slot_order,
            original + 1,
        )
        self.assertEqual(
            FranchiseSeasonMembership.objects.filter(franchise__release=self.release).count(),
            1797,
        )

    def test_one_changed_slot_assignment_is_different_and_refused(self):
        assignment = TimelineScheduleSlotAssignment.objects.get(
            timeline=self.timeline,
            franchise__placeholder_slug='founding-01',
        )
        expansion = self.release.franchises.get(placeholder_slug='expansion-1976-01')
        TimelineScheduleSlotAssignment.objects.filter(pk=assignment.pk).update(
            franchise_id=expansion.pk,
        )
        self._assert_fixture_corruption()
        self.assertEqual(
            TimelineScheduleSlotAssignment.objects.get(pk=assignment.pk).franchise_id,
            expansion.pk,
        )
        self.assertEqual(
            TimelineScheduleSlotAssignment.objects.filter(timeline=self.timeline).count(),
            26,
        )

    def test_one_changed_home_franchise_is_different_and_refused(self):
        game = ScheduleGame.objects.filter(timeline=self.timeline, season_year=1970).order_by('pk').first()
        expansion = self.release.franchises.get(placeholder_slug='expansion-1976-02')
        original_home = game.home_id
        ScheduleGame.objects.filter(pk=game.pk).update(home_id=expansion.pk)
        self._assert_fixture_corruption()
        stored = ScheduleGame.objects.get(pk=game.pk)
        self.assertEqual(stored.home_id, expansion.pk)
        self.assertNotEqual(stored.home_id, original_home)
        self.assertEqual(stored.historical_game_id, game.historical_game_id)
        self.assertEqual(ScheduleGame.objects.filter(timeline=self.timeline).count(), 182)

    def test_one_changed_historical_source_is_different_and_refused(self):
        games = list(
            ScheduleGame.objects.filter(timeline=self.timeline, season_year=1970).order_by('pk')[:2]
        )
        first, second = games
        first_source = first.historical_game_id
        second_source = second.historical_game_id
        with connection.constraint_checks_disabled():
            ScheduleGame.objects.filter(pk=first.pk).update(historical_game_id=2140000000)
            ScheduleGame.objects.filter(pk=second.pk).update(historical_game_id=first_source)
            ScheduleGame.objects.filter(pk=first.pk).update(historical_game_id=second_source)
        self._assert_fixture_corruption()
        self.assertEqual(ScheduleGame.objects.get(pk=first.pk).historical_game_id, second_source)
        self.assertEqual(ScheduleGame.objects.get(pk=second.pk).historical_game_id, first_source)
        self.assertEqual(ScheduleGame.objects.get(pk=first.pk).home_id, first.home_id)
        self.assertEqual(ScheduleGame.objects.get(pk=first.pk).away_id, first.away_id)
        self.assertEqual(ScheduleGame.objects.filter(timeline=self.timeline).count(), 182)

    def test_one_changed_week_assignment_is_different_and_refused(self):
        week = ScheduleWeekAssignment.objects.filter(timeline=self.timeline).order_by('pk').first()
        changed = 2 if week.week == 1 else 1
        ScheduleWeekAssignment.objects.filter(pk=week.pk).update(week=changed)
        self._assert_fixture_corruption()
        self.assertEqual(ScheduleWeekAssignment.objects.get(pk=week.pk).week, changed)
        self.assertEqual(ScheduleWeekAssignment.objects.get(pk=week.pk).game_id, week.game_id)
        self.assertEqual(
            ScheduleWeekAssignment.objects.filter(timeline=self.timeline).count(),
            182,
        )
        self.assertEqual(
            ScheduleGame.objects.filter(timeline=self.timeline, season_year=1970).count(),
            182,
        )

    def _assert_fixture_corruption(self):
        self.assertEqual(historical_fixture_state(self.timeline), 'DIFFERENT')
        output = self._verify()
        self.assertIn('classification=INVALID', output)
        self.assertIn('fixtures=DIFFERENT', output)
        self.assertIn('count.fixture.slot_assignments=26', output)
        self.assertIn('count.fixture.games=182', output)
        self.assertIn('count.fixture.weeks=182', output)
        self.assertIn('count.fixture.byes=0', output)
        self._refuse_stage('START_1970', 'The stored 1970 fixtures differ.')
        self._refuse_stage('BUILD_DRAFT', 'BUILD_DRAFT requires a draft release.')
        self._refuse_stage('PUBLISH', 'PUBLISH refuses a release that already has a timeline.')

    def _verify(self):
        with self._service_patches() as mocks:
            with CaptureQueriesContext(connection) as captured:
                output = self._run('VERIFY')
        for mock in mocks.values():
            mock.assert_not_called()
        self.assertEqual(_mutating_sql(captured), [])
        self.assertEqual(_result_lines(output), ['world_result=readonly'])
        self.assertNotIn('world_result=writes', output)
        return output

    def _refuse_writers(self, text):
        for stage in ('BUILD_DRAFT', 'PUBLISH', 'START_1970'):
            self._refuse_stage(stage, text)

    def _refuse_stage(self, stage, text):
        with self._service_patches() as mocks:
            with CaptureQueriesContext(connection) as captured:
                message, output = self._fail(stage)
        for name in (
            'generate_schedule_release',
            'generate_opening_players',
            'generate_prospects',
            'generate_player_ratings',
            'publish_universe_release',
            'load_historical_schedule_1970',
            'start_timeline',
            'generate_historical_timeline_schedule_1970',
        ):
            mocks[name].assert_not_called()
        self.assertIn(text, message)
        self.assertEqual(_result_lines(output), ['world_result=failure'])
        self.assertNotIn('world_result=writes', output)
        self.assertEqual(_mutating_sql(captured), [])

    def _classify_schedule_layer(self, names_exact=None):
        """Classify the stored schedule with later layers held absent.

        The stored era profile exists only so the 1970 timeline can be
        built. Clearing those two snapshot fields shows whether the name
        and seat comparison, with the real counts, is PARTIAL or INVALID.
        """
        from universe.world_build import product_snapshot

        snapshot = product_snapshot(self.release)
        snapshot['era_profiles'] = 0
        snapshot['has_y1970'] = False
        if names_exact is not None:
            snapshot['names_exact'] = names_exact
        return _classify(snapshot, generator_version=GENERATOR_VERSION)

    def _name(self, slug):
        return self.release.provisional_franchise_names.get(placeholder_slug=slug)

    def _run(self, stage):
        stdout = StringIO()
        call_command('build_fg_test_world', stage=stage, stdout=stdout)
        return stdout.getvalue()

    def _fail(self, stage):
        stdout = StringIO()
        with self.assertRaises(CommandError) as caught:
            call_command('build_fg_test_world', stage=stage, stdout=stdout)
        return str(caught.exception), stdout.getvalue()

    def _service_patches(self):
        targets = {
            'generate_schedule_release': lambda *_args: 'created',
            'generate_opening_players': lambda *_args: 'created',
            'generate_prospects': lambda *_args: 'created',
            'generate_player_ratings': lambda *_args: 'created',
            'release_digests': lambda _release: (CATALOG, VALIDATION),
            'evaluate_release_publication': lambda *_args, **_kwargs: SimpleNamespace(
                publishable=True,
                messages=(),
            ),
            'publish_universe_release': lambda **_kwargs: SimpleNamespace(outcome='published'),
            'load_historical_schedule_1970': lambda: 'created',
            'start_timeline': lambda **_kwargs: SimpleNamespace(
                outcome='created',
                timeline_id=self.timeline.pk,
                player_count=1378,
                rating_count=1378,
                membership_count=1378,
                rostered_count=1248,
                free_agent_count=130,
            ),
            'generate_historical_timeline_schedule_1970': lambda *, timeline_id: 'created',
        }
        stack = ExitStack()
        self.addCleanup(stack.close)
        mocks = {}
        for name, value in targets.items():
            mocks[name] = stack.enter_context(
                patch(f'universe.world_build.{name}', side_effect=value)
            )
        return _open_stack(stack, mocks)


class DatabaseGuardTests(TestCase):
    def test_production_and_other_names_are_refused_before_any_service(self):
        original = settings_name()
        self.addCleanup(set_database_name, original)
        for name in (PRODUCTION_DATABASE_NAME, ':memory:', 'lecrolink$fg_test_copy'):
            with self.subTest(name=name):
                set_database_name(name)
                stdout = StringIO()
                with patch('universe.world_build.generate_schedule_release') as schedule:
                    with self.assertRaises(CommandError) as caught:
                        call_command('build_fg_test_world', stage='BUILD_DRAFT', stdout=stdout)
                schedule.assert_not_called()
                self.assertEqual(_result_lines(stdout.getvalue()), ['world_result=failure'])
                self.assertNotIn(name, str(caught.exception))
                if name == PRODUCTION_DATABASE_NAME:
                    self.assertIn('production database', str(caught.exception))
                else:
                    self.assertIn('lecrolink$fg_test', str(caught.exception))
                self.assertNotIn('lecrolink$fg.', str(caught.exception))


def _result_lines(output):
    return [line for line in output.splitlines() if line.startswith('world_result=')]


def _mutating_sql(captured):
    found = []
    for query in captured.captured_queries:
        sql = ' '.join(query['sql'].split()).upper()
        if sql.startswith(('INSERT', 'UPDATE', 'DELETE')):
            found.append(sql)
    return found


def _started(timeline, outcome):
    return SimpleNamespace(
        outcome=outcome,
        timeline_id=timeline.pk,
        player_count=1378,
        rating_count=1378,
        membership_count=1378,
        rostered_count=1248,
        free_agent_count=130,
    )


def settings_name():
    from django.conf import settings
    return settings.DATABASES['default']['NAME']


def set_database_name(name):
    from django.conf import settings
    settings.DATABASES['default']['NAME'] = name


class _open_stack:
    """Expose the entered mocks and close their patches with the test."""

    def __init__(self, stack, mocks):
        self.stack = stack
        self.mocks = mocks

    def __enter__(self):
        return self.mocks

    def __exit__(self, exc_type, exc, traceback):
        self.stack.close()
        return False
