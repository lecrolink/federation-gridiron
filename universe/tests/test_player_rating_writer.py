import socket
from collections import Counter
from io import StringIO
from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, connection
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from leagues.models import League
from timelines.models import DevelopmentEvent, Timeline
from universe.generation.opening_players import generate_opening_players
from universe.generation.player_rating_profiles import (
    PLAYER_BATCH_SIZE,
    PlayerRatingGenerationError,
    generate_player_ratings,
)
from universe.generation.player_ratings import build_playable_ratings
from universe.generation.prospects import generate_prospects
from universe.generation.schedule import generate_schedule_release
from universe.models import UniverseRelease
from universe.people import (
    PLAYER_COMPONENTS,
    College,
    PlayerRatingProfile,
    UniversePlayer,
)
from universe.rules import DraftClass, EraRuleProfile, OpeningAssignment, OpeningContract

SEED = 1970
OTHER_SEED = 1971
RELEASE_SLUG = 'fg-1970-1'

_ORIGINAL_CONNECT = socket.socket.connect


def _blocked_connect(self, address):
    raise AssertionError(f'Outbound connections are blocked: {address}')


def setUpModule():
    socket.socket.connect = _blocked_connect


def tearDownModule():
    socket.socket.connect = _ORIGINAL_CONNECT


def _legal_values(initial=10, potential=20):
    values = {}
    for component in PLAYER_COMPONENTS:
        values[f'initial_{component}'] = initial
        values[f'potential_{component}'] = potential
    return values


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


class MissingScheduleTests(TestCase):
    def test_a_release_without_the_schedule_is_refused(self):
        league = League.objects.create(
            name='Bare League',
            slug='bare-league',
            tier=1,
            active=True,
        )
        UniverseRelease.objects.create(
            slug='bare-release',
            root_league=league,
            generator_version='pending',
            seed=SEED,
            founding_franchise_count=26,
            expansion_schedule_version=3,
            first_season_year=1970,
            season_count=60,
            last_season_year=2029,
            status=UniverseRelease.Status.DRAFT,
        )
        with self.assertRaisesRegex(PlayerRatingGenerationError, 'fg-founding-2'):
            generate_player_ratings(league.id, SEED)
        self.assertEqual(PlayerRatingProfile.objects.count(), 0)
        self.assertEqual(UniversePlayer.objects.count(), 0)

    def test_a_league_without_a_draft_release_is_refused(self):
        league = League.objects.create(
            name='Empty League',
            slug='empty-league',
            tier=1,
            active=True,
        )
        with self.assertRaisesRegex(PlayerRatingGenerationError, 'no draft release'):
            generate_player_ratings(league.id, SEED)
        self.assertEqual(PlayerRatingProfile.objects.count(), 0)


class PlayerRatingWriterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.league = League.objects.create(
            name='Rating League',
            slug='rating-league',
            tier=1,
            active=True,
        )
        cls.release = UniverseRelease.objects.create(
            slug=RELEASE_SLUG,
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
        cls.release.catalog_fingerprint = 'preexisting'
        cls.release.validation_fingerprint = 'kept-validation'
        cls.release.expansion_schedule_version = 3
        cls.release.save()
        if generate_schedule_release(cls.league.id, SEED) != 'created':
            raise AssertionError('The schedule fixture was not created.')
        if generate_opening_players(cls.league.id, SEED) != 'created':
            raise AssertionError('The opening fixture was not created.')
        if generate_prospects(cls.league.id, SEED) != 'created':
            raise AssertionError('The prospect fixture was not created.')
        cls.release.refresh_from_db()

    def test_batch_size_stays_at_the_shared_writer_size(self):
        self.assertEqual(PLAYER_BATCH_SIZE, 500)

    def test_writes_every_playable_profile_and_a_rerun_preserves_ids(self):
        player_ids = set(UniversePlayer.objects.values_list('pk', flat=True))
        self.assertEqual(len(player_ids), 21155)
        self.assertEqual(generate_player_ratings(self.league.id, SEED), 'created')

        profiles = list(
            PlayerRatingProfile.objects.select_related('player').order_by('player__universe_key')
        )
        self.assertEqual(len(profiles), 21155)
        ratings = build_playable_ratings(SEED, RELEASE_SLUG)
        self.assertEqual(len(ratings), 21155)
        by_key = {profile.player.universe_key: profile for profile in profiles}
        self.assertEqual(set(by_key), {rating.universe_key for rating in ratings})
        prospect_pools = Counter()
        opening_profiles = 0
        for rating in ratings:
            stored = by_key[rating.universe_key]
            self.assertEqual(stored.player.universe_key, rating.universe_key)
            self.assertEqual(stored.player.release_id, self.release.pk)
            self.assertEqual(len(rating.components), 33)
            if stored.player.cohort == 'opening':
                opening_profiles += 1
                self.assertIsNone(stored.player.draft_pool)
            else:
                self.assertEqual(stored.player.cohort, 'draft')
                prospect_pools[stored.player.draft_pool] += 1
            for component in rating.components:
                initial = getattr(stored, f'initial_{component.component}')
                potential = getattr(stored, f'potential_{component.component}')
                self.assertEqual(initial, component.initial)
                self.assertEqual(potential, component.potential)
                self.assertIsInstance(initial, int)
                self.assertIsInstance(potential, int)
                self.assertGreaterEqual(initial, 1)
                self.assertLessEqual(potential, 96)
                self.assertLessEqual(initial, potential)
                self.assertNotIn(initial, (0, 97, 98, 99, 100))
                self.assertNotIn(potential, (0, 97, 98, 99, 100))
        self.assertEqual(opening_profiles, 1378)
        roles = Counter(
            OpeningAssignment.objects.filter(release=self.release).values_list(
                'role', flat=True,
            )
        )
        self.assertEqual(roles['starter'], 494)
        self.assertEqual(roles['backup'], 546)
        self.assertEqual(roles['taxi'], 208)
        self.assertEqual(roles['free_agent'], 130)
        self.assertEqual(prospect_pools['draftable'], 16183)
        self.assertEqual(prospect_pools['undrafted'], 3594)
        self.assertEqual(
            PlayerRatingProfile.objects.filter(player__cohort='opening').count(),
            1378,
        )
        self.assertEqual(
            PlayerRatingProfile.objects.filter(player__cohort='draft').count(),
            19777,
        )
        self.assertFalse(UniversePlayer.objects.filter(name='Joe LeCrone').exists())
        self.assertEqual(Timeline.objects.count(), 0)
        self.assertEqual(DevelopmentEvent.objects.count(), 0)
        self.assertEqual(OpeningContract.objects.count(), 0)
        self.assertEqual(DraftClass.objects.count(), 60)
        self.assertEqual(EraRuleProfile.objects.count(), 12)
        self.assertEqual(College.objects.count(), 64)
        self.assertEqual(OpeningAssignment.objects.count(), 1378)
        self.assertEqual(UniversePlayer.objects.count(), 21155)
        self.release.refresh_from_db()
        self.assertEqual(self.release.catalog_fingerprint, 'preexisting')
        self.assertEqual(self.release.validation_fingerprint, 'kept-validation')
        self.assertEqual(self.release.seed, SEED)
        self.assertEqual(self.release.status, UniverseRelease.Status.DRAFT)
        self.assertIsNone(self.release.published_at)

        profile_ids = {profile.pk for profile in profiles}
        self.assertEqual(generate_player_ratings(self.league.id, SEED), 'unchanged')
        self.assertEqual(
            set(PlayerRatingProfile.objects.values_list('pk', flat=True)),
            profile_ids,
        )
        self.assertEqual(
            set(UniversePlayer.objects.values_list('pk', flat=True)),
            player_ids,
        )

    def test_command_batches_inserts_and_prints_unchanged(self):
        created = StringIO()
        executed = []
        seen = {}
        real_clean = PlayerRatingProfile.full_clean

        def spy_clean(self, *args, **kwargs):
            if 'flags' not in seen:
                seen['flags'] = (
                    kwargs.get('validate_unique', True),
                    kwargs.get('validate_constraints', True),
                )
                seen['exclude'] = kwargs.get('exclude')
                queries = []

                def wrap(execute, sql, params, many, context):
                    queries.append(sql)
                    return execute(sql, params, many, context)

                connection.execute_wrappers.append(wrap)
                try:
                    return real_clean(self, *args, **kwargs)
                finally:
                    connection.execute_wrappers.remove(wrap)
                    seen['queries'] = queries
            return real_clean(self, *args, **kwargs)

        def count_queries(execute, sql, params, many, context):
            executed.append(sql)
            return execute(sql, params, many, context)

        connection.execute_wrappers.append(count_queries)
        try:
            with patch(
                'universe.generation.player_rating_profiles.PlayerRatingProfile.full_clean',
                spy_clean,
            ):
                call_command(
                    'generate_player_ratings',
                    league_id=self.league.id,
                    seed=SEED,
                    stdout=created,
                )
        finally:
            connection.execute_wrappers.remove(count_queries)
        repeats = Counter(executed)
        top = [(count, sql[:140]) for sql, count in repeats.most_common(3)]
        relation_or_check = [
            sql for sql in executed
            if 'AS "a"' in sql or 'AS "_check"' in sql
        ]
        inserts = [
            sql for sql in executed
            if sql.startswith('INSERT INTO "universe_playerratingprofile"')
        ]
        non_insert = Counter(
            sql for sql in executed if not sql.startswith('INSERT')
        )
        # SQLite splits a 500-row bulk_create into chunks of about 14 rows
        # because each profile has 67 columns. One statement per player
        # would be 21,155 inserts, and one constraint query per player
        # would repeat a SELECT that many times.
        self.assertLess(len(executed), 5000, f'rating write ran {len(executed)} queries; top {top}')
        self.assertLess(max(non_insert.values(), default=0), 200, f'top {top}')
        self.assertLess(max(Counter(relation_or_check).values(), default=0), 50)
        self.assertGreater(len(inserts), 1)
        self.assertLess(len(inserts), 21155)
        self.assertEqual(seen['flags'], (False, False))
        self.assertEqual(seen['exclude'], {'player'})
        self.assertEqual(seen['queries'], [])
        self.assertEqual(created.getvalue().strip(), 'created')
        self.assertEqual(PlayerRatingProfile.objects.count(), 21155)
        again = StringIO()
        call_command(
            'generate_player_ratings',
            league_id=self.league.id,
            seed=SEED,
            stdout=again,
        )
        self.assertEqual(again.getvalue().strip(), 'unchanged')

    def test_wrong_seed_published_and_timeline_refuse(self):
        with self.assertRaisesRegex(PlayerRatingGenerationError, 'will not change the release seed'):
            generate_player_ratings(self.league.id, OTHER_SEED)
        self.assertEqual(PlayerRatingProfile.objects.count(), 0)
        self.assertEqual(self.release.seed, SEED)

        UniverseRelease.objects.filter(pk=self.release.pk).update(
            status=UniverseRelease.Status.PUBLISHED,
            catalog_fingerprint='c' * 64,
            validation_fingerprint='d' * 64,
            published_at=timezone.now(),
        )
        with self.assertRaisesRegex(PlayerRatingGenerationError, 'published'):
            generate_player_ratings(self.league.id, SEED)
        self.assertEqual(PlayerRatingProfile.objects.count(), 0)
        UniverseRelease.objects.filter(pk=self.release.pk).update(
            status=UniverseRelease.Status.DRAFT,
            catalog_fingerprint='preexisting',
            validation_fingerprint='kept-validation',
            published_at=timezone.now(),
        )
        with self.assertRaisesRegex(PlayerRatingGenerationError, 'published release cannot be changed'):
            generate_player_ratings(self.league.id, SEED)
        self.assertEqual(PlayerRatingProfile.objects.count(), 0)
        UniverseRelease.objects.filter(pk=self.release.pk).update(published_at=None)

        Timeline.objects.bulk_create([
            Timeline(
                release=self.release,
                name='Pinned campaign',
                start_season_year=1970,
                current_season_year=1970,
                season_index=1,
                phase=Timeline.Phase.CHOOSE_FRANCHISE,
                status=Timeline.Status.ACTIVE,
            )
        ])
        with self.assertRaisesRegex(PlayerRatingGenerationError, 'timeline'):
            generate_player_ratings(self.league.id, SEED)
        self.assertEqual(PlayerRatingProfile.objects.count(), 0)
        self.assertEqual(Timeline.objects.filter(release=self.release).count(), 1)
        self.assertEqual(UniversePlayer.objects.count(), 21155)

    def test_missing_owned_rows_are_kept(self):
        college = College.objects.filter(release=self.release).order_by('slug').first()
        College.objects.filter(pk=college.pk).update(name='Wrong College')
        with self.assertRaisesRegex(PlayerRatingGenerationError, 'will not delete or replace'):
            generate_player_ratings(self.league.id, SEED)
        college.refresh_from_db()
        self.assertEqual(college.name, 'Wrong College')
        self.assertEqual(PlayerRatingProfile.objects.count(), 0)
        self.assertEqual(UniversePlayer.objects.count(), 21155)

        from universe.generation.player_names import COLLEGES
        approved = {slug: name for name, slug, _kind in COLLEGES}
        College.objects.filter(pk=college.pk).update(name=approved[college.slug])
        prospect = UniversePlayer.objects.filter(cohort='draft').order_by('pk').first()
        UniversePlayer.objects.filter(pk=prospect.pk).update(name='Edited Prospect')
        with self.assertRaisesRegex(PlayerRatingGenerationError, 'will not delete or replace'):
            generate_player_ratings(self.league.id, SEED)
        prospect.refresh_from_db()
        self.assertEqual(prospect.name, 'Edited Prospect')
        self.assertEqual(PlayerRatingProfile.objects.count(), 0)
        self.assertEqual(DraftClass.objects.count(), 60)
        self.assertEqual(EraRuleProfile.objects.count(), 12)

    def test_partial_extra_and_mismatched_profiles_are_kept(self):
        player = UniversePlayer.objects.filter(cohort='opening').order_by('pk').first()
        partial = PlayerRatingProfile(player=player, **_legal_values())
        partial.save()
        partial_id = partial.pk
        with self.assertRaisesRegex(PlayerRatingGenerationError, 'will not delete or replace'):
            generate_player_ratings(self.league.id, SEED)
        partial.refresh_from_db()
        self.assertEqual(partial.pk, partial_id)
        self.assertEqual(partial.initial_speed, 10)
        self.assertEqual(PlayerRatingProfile.objects.count(), 1)
        partial.delete()

        self.assertEqual(generate_player_ratings(self.league.id, SEED), 'created')
        profile_ids = set(PlayerRatingProfile.objects.values_list('pk', flat=True))
        player_ids = set(UniversePlayer.objects.values_list('pk', flat=True))
        mismatched = PlayerRatingProfile.objects.select_related('player').order_by('pk').first()
        PlayerRatingProfile.objects.filter(pk=mismatched.pk).update(initial_speed=1)
        with self.assertRaisesRegex(PlayerRatingGenerationError, 'will not delete or replace'):
            generate_player_ratings(self.league.id, SEED)
        mismatched.refresh_from_db()
        self.assertEqual(mismatched.initial_speed, 1)
        self.assertEqual(
            set(PlayerRatingProfile.objects.values_list('pk', flat=True)),
            profile_ids,
        )

        source = UniversePlayer.objects.filter(cohort='opening').order_by('pk').first()
        extra = UniversePlayer.objects.get(pk=source.pk)
        extra.pk = None
        extra.universe_key = 'a' * 64
        extra.name = 'Extra Opening'
        UniversePlayer.objects.bulk_create([extra])
        extra_profile = PlayerRatingProfile(player=extra, **_legal_values(12, 24))
        extra_profile.save()
        extra_profile_id = extra_profile.pk
        with self.assertRaisesRegex(PlayerRatingGenerationError, 'will not delete or replace'):
            generate_player_ratings(self.league.id, SEED)
        extra_profile.refresh_from_db()
        self.assertEqual(extra_profile.pk, extra_profile_id)
        self.assertEqual(extra_profile.initial_speed, 12)
        self.assertEqual(UniversePlayer.objects.filter(universe_key='a' * 64).count(), 1)
        self.assertEqual(PlayerRatingProfile.objects.count(), 21156)
        self.assertTrue(profile_ids <= set(PlayerRatingProfile.objects.values_list('pk', flat=True)))
        self.assertTrue(player_ids <= set(UniversePlayer.objects.values_list('pk', flat=True)))

    def test_an_illegal_scalar_is_refused_before_insert(self):
        from universe.generation.player_ratings import build_playable_ratings as real_build

        def bad_ratings(seed, slug):
            rows = list(real_build(seed, slug))
            first = rows[0]
            components = list(first.components)
            components[0] = components[0]._replace(initial=0)
            rows[0] = first._replace(components=tuple(components))
            return tuple(rows)

        inserted = {'called': False}

        def reject_insert(*args, **kwargs):
            inserted['called'] = True
            raise AssertionError('Rating bulk insertion ran after an illegal scalar.')

        with patch(
            'universe.generation.player_rating_profiles.build_playable_ratings',
            bad_ratings,
        ), patch(
            'universe.generation.player_rating_profiles.PlayerRatingProfile.objects.bulk_create',
            reject_insert,
        ):
            with self.assertRaisesRegex(PlayerRatingGenerationError, 'outside 1–96'):
                generate_player_ratings(self.league.id, SEED)
        self.assertFalse(inserted['called'])
        self.assertEqual(PlayerRatingProfile.objects.count(), 0)
        self.assertEqual(UniversePlayer.objects.count(), 21155)

    def test_an_insert_constraint_failure_rolls_back(self):
        def reject_insert(*args, **kwargs):
            raise IntegrityError('FOREIGN KEY constraint failed')

        with patch(
            'universe.generation.player_rating_profiles.PlayerRatingProfile.objects.bulk_create',
            reject_insert,
        ):
            with self.assertRaisesRegex(PlayerRatingGenerationError, 'rolled back'):
                generate_player_ratings(self.league.id, SEED)
        self.assertEqual(PlayerRatingProfile.objects.count(), 0)
        self.assertEqual(UniversePlayer.objects.count(), 21155)
        self.assertEqual(DraftClass.objects.count(), 60)
        self.assertEqual(EraRuleProfile.objects.count(), 12)

    def test_command_refuses_a_published_release(self):
        UniverseRelease.objects.filter(pk=self.release.pk).update(
            status=UniverseRelease.Status.PUBLISHED,
            catalog_fingerprint='e' * 64,
            validation_fingerprint='f' * 64,
            published_at=timezone.now(),
        )
        with self.assertRaises(CommandError):
            call_command(
                'generate_player_ratings',
                league_id=self.league.id,
                seed=SEED,
                stdout=StringIO(),
            )
        self.assertEqual(PlayerRatingProfile.objects.count(), 0)
