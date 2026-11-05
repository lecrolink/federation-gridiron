import hashlib
import hmac
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
from timelines.models import Timeline
from universe.generation.opening_players import (
    OpeningGenerationError,
    _require_trusted_opening_assignment,
    _require_trusted_opening_player,
    _validate_opening_players,
    generate_opening_players,
    growth_draws,
    y1970_values,
)
from universe.generation.player_counts import ACTIVE_QUOTA_1970, TAXI_QUOTA
from universe.generation.player_identities import (
    FREE_AGENT_DEBUT_YEAR,
    ROSTERED_DEBUT_YEAR,
    build_opening_cohort,
    hmac_int,
    opening_coordinate,
    player_key,
)
from universe.generation.player_names import COLLEGES
from universe.generation.schedule import (
    GENERATOR_VERSION,
    OPENING_SEATS,
    POOL_SIZE,
    generate_schedule_release,
)
from universe.models import HistoricalStanding, UniverseFranchise, UniverseRelease
from universe.people import College, PlayerRatingProfile, UniversePlayer, UniverseStaffIdentity
from universe.rules import (
    DraftClass,
    EraCollegeWeight,
    EraRuleProfile,
    OpeningAssignment,
    OpeningContract,
    OpeningStaffAssignment,
)

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


class OpeningWriterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.league = League(
            name='Opening League',
            slug='opening-league',
            tier=1,
            active=True,
        )
        cls.league.save()
        cls.release = UniverseRelease(
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
        cls.release.save()
        cls.release.catalog_fingerprint = 'preexisting'
        cls.release.validation_fingerprint = 'kept-validation'
        cls.release.expansion_schedule_version = 3
        cls.release.save()
        cls.assert_outcome(generate_schedule_release(cls.league.id, SEED), 'created')
        cls.release.refresh_from_db()

    @staticmethod
    def assert_outcome(actual, expected):
        if actual != expected:
            raise AssertionError(actual)

    def test_writes_the_opening_cohort_and_reuses_a_matching_college(self):
        name, slug, _kind = COLLEGES[0]
        kept = College(release=self.release, slug=slug, name=name)
        kept.save()
        self.assertEqual(generate_opening_players(self.league.id, SEED), 'created')
        self.release.refresh_from_db()

        self.assertEqual(self.release.generator_version, GENERATOR_VERSION)
        self.assertEqual(self.release.seed, SEED)
        self.assertEqual(self.release.status, UniverseRelease.Status.DRAFT)
        self.assertEqual(self.release.catalog_fingerprint, 'preexisting')
        self.assertEqual(self.release.validation_fingerprint, 'kept-validation')
        self.assertEqual(self.release.expansion_schedule_version, 3)
        self.assertIsNone(self.release.published_at)
        self.assertEqual(self.release.franchises.count(), 32)
        self.assertEqual(UniverseStaffIdentity.objects.filter(release=self.release).count(), POOL_SIZE * 2)
        self.assertEqual(OpeningStaffAssignment.objects.filter(release=self.release).count(), OPENING_SEATS * 2)

        self.assertEqual(College.objects.filter(release=self.release).count(), 64)
        self.assertEqual(College.objects.get(release=self.release, slug=slug).pk, kept.pk)
        for college_name, college_slug, _kind in COLLEGES:
            stored = College.objects.get(release=self.release, slug=college_slug)
            self.assertEqual(stored.name, college_name)

        profile = EraRuleProfile.objects.get(release=self.release)
        self.assertEqual(profile.profile_key, 'y1970')
        self.assertEqual((profile.first_year, profile.last_year), (1970, 1973))
        self.assertEqual(profile.market, 'controlled')
        self.assertEqual(profile.style_key, 'run-first')
        self.assertEqual(profile.active_limit, 40)
        self.assertEqual(profile.taxi_limit, 8)
        self.assertEqual(profile.draft_rounds, 17)
        self.assertEqual(profile.schedule_length, 14)
        self.assertEqual(profile.playoff_format, 'eight_team_3_and_1')
        self.assertEqual(profile.overtime_rule, 'tie')
        self.assertEqual(profile.sack_leaderboard, 'unofficial')
        self.assertEqual(profile.cap_status, 'off')
        self.assertIsNone(profile.cap_limit)
        self.assertIsNone(profile.accrued_seasons_for_ufa)
        self.assertIsNone(profile.minimum_salary)
        self.assertIsNone(profile.rookie_slot_scale)
        self.assertEqual(profile.ir_max, 2)
        self.assertEqual(profile.ir_min_weeks, 8)
        self.assertEqual(profile.ir_returns_allowed, 0)
        self.assertEqual(profile.rookie_contract_years_min, 1)
        self.assertEqual(profile.rookie_contract_years_max, 3)
        self.assertEqual(profile.veteran_contract_years_min, 1)
        self.assertEqual(profile.veteran_contract_years_max, 5)
        self.assertEqual(profile.trade_deadline_week, 8)
        self.assertEqual(profile.future_pick_years, 1)
        self.assertEqual(profile.waiver_rule, 'all_releases')
        self.assertEqual(profile.free_agency_type, 'reserve')
        self.assertEqual(profile.fa_compensation, 'none')
        self.assertEqual(profile.franchise_tag, 'off')
        for position, count in ACTIVE_QUOTA_1970.items():
            self.assertEqual(getattr(profile, f'quota_active_{position.lower()}'), count)
        for position, count in TAXI_QUOTA.items():
            self.assertEqual(getattr(profile, f'quota_taxi_{position.lower()}'), count)
        self.assertEqual(profile.quota_active_qb, 2)
        self.assertEqual(profile.quota_taxi_fb, 0)

        built = build_opening_cohort(SEED, RELEASE_SLUG)
        self.assertEqual(len(built), 1378)
        self.assertEqual(UniversePlayer.objects.filter(release=self.release).count(), 1378)
        self.assertEqual(
            UniversePlayer.objects.filter(release=self.release, cohort='opening').count(),
            1378,
        )
        self.assertEqual(OpeningAssignment.objects.filter(release=self.release).count(), 1378)
        self.assertEqual(sum(row.slot_kind == 'starter' for row in built), 494)
        self.assertEqual(sum(row.slot_kind == 'backup' for row in built), 546)
        self.assertEqual(sum(row.slot_kind == 'taxi' for row in built), 208)
        self.assertEqual(sum(row.slot_kind == 'free_agent' for row in built), 130)
        self.assertEqual(
            OpeningAssignment.objects.filter(release=self.release, role='starter').count(),
            494,
        )
        self.assertEqual(
            OpeningAssignment.objects.filter(release=self.release, role='backup').count(),
            546,
        )
        self.assertEqual(
            OpeningAssignment.objects.filter(release=self.release, role='taxi').count(),
            208,
        )
        self.assertEqual(
            OpeningAssignment.objects.filter(release=self.release, role='free_agent').count(),
            130,
        )
        self.assertEqual(
            OpeningAssignment.objects.filter(
                release=self.release,
                franchise__isnull=False,
            ).count(),
            1248,
        )

        stored_by_key = {
            player.universe_key: player
            for player in UniversePlayer.objects.filter(release=self.release).select_related(
                'college',
                'origin_profile',
            )
        }
        for row in built:
            self.assertFalse(hasattr(row, 'peak_age'))
            player = stored_by_key[row.universe_key]
            coordinate = opening_coordinate(RELEASE_SLUG, row.index)
            digest = hmac.new(
                str(SEED).encode('utf-8'),
                coordinate.encode('utf-8'),
                hashlib.sha256,
            ).hexdigest()
            self.assertEqual(player.universe_key, digest)
            self.assertEqual(player.universe_key, player_key(SEED, coordinate))
            self.assertEqual(player.name, row.name)
            self.assertEqual(player.birth_year, row.birth_year)
            self.assertEqual(player.birth_day, row.birth_day)
            self.assertEqual(player.position, row.position)
            self.assertEqual(player.handedness, row.handedness)
            self.assertEqual(player.height_in, row.height_in)
            self.assertEqual(player.weight_lb, row.weight_lb)
            self.assertEqual(player.college.slug, row.college_slug)
            self.assertEqual(player.first_pro_year, row.debut_year)
            self.assertEqual(player.origin_profile_id, profile.pk)
            peak_age = 24 + (hmac_int(SEED, f'{player.universe_key}:peak_age') % 9)
            development_rate = 25 + (
                hmac_int(SEED, f'{player.universe_key}:development_rate') % 51
            )
            decline_rate = 25 + (
                hmac_int(SEED, f'{player.universe_key}:decline_rate') % 51
            )
            self.assertEqual(growth_draws(SEED, player.universe_key), (peak_age, development_rate, decline_rate))
            self.assertEqual(player.peak_age, peak_age)
            self.assertEqual(player.development_rate, development_rate)
            self.assertEqual(player.decline_rate, decline_rate)
            self.assertGreaterEqual(player.peak_age, 24)
            self.assertLessEqual(player.peak_age, 32)
            self.assertGreaterEqual(player.development_rate, 25)
            self.assertLessEqual(player.development_rate, 75)
            self.assertGreaterEqual(player.decline_rate, 25)
            self.assertLessEqual(player.decline_rate, 75)
            self.assertIsNone(player.origin_franchise_id)
            self.assertIsNone(player.draft_class_id)
            self.assertIsNone(player.draft_pool)
            self.assertIsNone(player.origin_draft_year)
            self.assertIsNone(player.origin_draft_round)
            self.assertIsNone(player.origin_draft_pick)
            self.assertIsNone(player.board_rank)
            self.assertIsNone(player.secondary_position_1)
            self.assertIsNone(player.secondary_position_2)
            self.assertIsNone(player.trait_1)
            self.assertIsNone(player.trait_2)
            self.assertIsNone(player.trait_3)
            assignment = OpeningAssignment.objects.get(release=self.release, player=player)
            self.assertEqual(assignment.role, row.slot_kind)
            if row.on_roster:
                self.assertEqual(assignment.franchise.placeholder_slug, row.club_slug)
                self.assertEqual(assignment.depth, row.depth)
                self.assertEqual(player.first_pro_year, ROSTERED_DEBUT_YEAR)
                self.assertGreaterEqual(assignment.depth, 1)
            else:
                self.assertIsNone(assignment.franchise_id)
                self.assertIsNone(assignment.depth)
                self.assertEqual(player.first_pro_year, FREE_AGENT_DEBUT_YEAR)
            self.assertNotEqual(player.name, 'Joe LeCrone')

        first = built[0]
        self.assertEqual(first.index, 0)
        self.assertEqual(first.club_slug, 'founding-01')
        self.assertEqual(first.slot_kind, 'starter')
        self.assertEqual(first.position, 'QB')
        self.assertEqual(first.depth, 1)
        founding = OpeningAssignment.objects.filter(
            release=self.release,
            franchise__placeholder_slug='founding-01',
        )
        self.assertEqual(founding.filter(role='starter').count(), 19)
        self.assertEqual(founding.filter(role='backup').count(), 21)
        self.assertEqual(founding.filter(role='taxi').count(), 8)

        self.assertEqual(PlayerRatingProfile.objects.count(), 0)
        self.assertEqual(OpeningContract.objects.count(), 0)
        self.assertEqual(EraCollegeWeight.objects.count(), 0)
        self.assertEqual(DraftClass.objects.count(), 0)
        self.assertEqual(HistoricalStanding.objects.count(), 0)
        self.assertEqual(UniversePlayer.objects.filter(cohort='draft').count(), 0)
        self.assertEqual(UniversePlayer.objects.filter(cohort='archive').count(), 0)

    def test_identical_rerun_is_a_no_op(self):
        self.assertEqual(generate_opening_players(self.league.id, SEED), 'created')
        player_ids = set(UniversePlayer.objects.values_list('pk', flat=True))
        college_ids = set(College.objects.values_list('pk', flat=True))
        profile_id = EraRuleProfile.objects.get().pk
        assignment_ids = set(OpeningAssignment.objects.values_list('pk', flat=True))
        self.assertEqual(generate_opening_players(self.league.id, SEED), 'unchanged')
        self.assertEqual(set(UniversePlayer.objects.values_list('pk', flat=True)), player_ids)
        self.assertEqual(set(College.objects.values_list('pk', flat=True)), college_ids)
        self.assertEqual(EraRuleProfile.objects.get().pk, profile_id)
        self.assertEqual(set(OpeningAssignment.objects.values_list('pk', flat=True)), assignment_ids)
        self.assertEqual(UniversePlayer.objects.count(), 1378)

    def test_command_prints_created_and_then_unchanged(self):
        created = StringIO()
        executed = []

        def count_queries(execute, sql, params, many, context):
            executed.append(sql)
            return execute(sql, params, many, context)

        connection.execute_wrappers.append(count_queries)
        try:
            call_command(
                'generate_opening_players',
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
            if 'AS "a"' in sql or 'AS "_check"' in sql or 'AS `a`' in sql or 'AS `_check`' in sql
        ]
        relation_repeats = Counter(relation_or_check)
        # One constraint or foreign-key lookup per opening player would repeat
        # about 1,378 times. Colleges and the y1970 profile may still validate.
        self.assertLess(
            len(executed),
            4000,
            f'opening write ran {len(executed)} queries; top {top}',
        )
        self.assertLess(
            max(repeats.values(), default=0),
            400,
            f'a statement repeated once per opening player; top {top}',
        )
        self.assertLess(max(relation_repeats.values(), default=0), 400)
        self.assertEqual(created.getvalue().strip(), 'created')
        again = StringIO()
        call_command(
            'generate_opening_players',
            league_id=self.league.id,
            seed=SEED,
            stdout=again,
        )
        self.assertEqual(again.getvalue().strip(), 'unchanged')

    def test_a_different_seed_refuses_without_deleting(self):
        with self.assertRaisesRegex(OpeningGenerationError, 'will not change the release seed'):
            generate_opening_players(self.league.id, OTHER_SEED)
        self.assertEqual(UniversePlayer.objects.count(), 0)
        self.assertEqual(self.release.seed, SEED)

        self.assertEqual(generate_opening_players(self.league.id, SEED), 'created')
        player = UniversePlayer.objects.order_by('pk').first()
        original_name = player.name
        with self.assertRaisesRegex(OpeningGenerationError, 'will not change the release seed'):
            generate_opening_players(self.league.id, OTHER_SEED)
        player.refresh_from_db()
        self.release.refresh_from_db()
        self.assertEqual(player.name, original_name)
        self.assertEqual(UniversePlayer.objects.count(), 1378)
        self.assertEqual(self.release.seed, SEED)

    def test_differing_rows_are_kept(self):
        name, slug, _kind = COLLEGES[0]
        College.objects.create(release=self.release, slug=slug, name='Wrong College')
        with self.assertRaisesRegex(OpeningGenerationError, 'will not delete or replace'):
            generate_opening_players(self.league.id, SEED)
        self.assertEqual(College.objects.get(slug=slug).name, 'Wrong College')
        self.assertEqual(UniversePlayer.objects.count(), 0)
        College.objects.all().delete()

        self.assertEqual(generate_opening_players(self.league.id, SEED), 'created')
        player = UniversePlayer.objects.order_by('pk').first()
        other = UniversePlayer.objects.order_by('pk').last()
        UniversePlayer.objects.filter(pk=player.pk).update(name='Edited Name')
        with self.assertRaisesRegex(OpeningGenerationError, 'will not delete or replace'):
            generate_opening_players(self.league.id, SEED)
        player.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(player.name, 'Edited Name')
        self.assertEqual(other.name, build_opening_cohort(SEED, RELEASE_SLUG)[-1].name)
        self.assertEqual(UniversePlayer.objects.count(), 1378)
        self.assertEqual(OpeningAssignment.objects.count(), 1378)

        extra = UniversePlayer.objects.filter(cohort='opening').order_by('pk').first()
        extra.pk = None
        extra.universe_key = 'a' * 64
        extra.name = 'Extra Opening'
        UniversePlayer.objects.bulk_create([extra])
        with self.assertRaisesRegex(OpeningGenerationError, 'will not delete or replace'):
            generate_opening_players(self.league.id, SEED)
        self.assertEqual(UniversePlayer.objects.filter(cohort='opening').count(), 1379)
        self.assertEqual(UniversePlayer.objects.get(universe_key='a' * 64).name, 'Extra Opening')
        self.assertEqual(OpeningAssignment.objects.count(), 1378)

    def test_field_validation_fails_before_player_insertion(self):
        seen = {'players': False, 'queries': None}
        real_clean = UniversePlayer.full_clean

        def spy_clean(self, *args, **kwargs):
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

        def bad_cohort(seed, slug):
            rows = list(build_opening_cohort(seed, slug))
            rows[0] = rows[0]._replace(position='NOPE')
            return tuple(rows)

        def reject_insert(*args, **kwargs):
            seen['players'] = True
            raise AssertionError('Player bulk insertion ran after invalid field data.')

        with patch(
            'universe.generation.opening_players.build_opening_cohort',
            bad_cohort,
        ), patch(
            'universe.generation.opening_players.UniversePlayer.full_clean',
            spy_clean,
        ), patch(
            'universe.generation.opening_players.UniversePlayer.objects.bulk_create',
            reject_insert,
        ):
            with self.assertRaisesRegex(OpeningGenerationError, 'failed validation'):
                generate_opening_players(self.league.id, SEED)
        self.assertEqual(seen['flags'], (False, False))
        self.assertEqual(set(seen['exclude']), {
            'release',
            'college',
            'origin_franchise',
            'draft_class',
            'origin_profile',
        })
        self.assertEqual(seen['queries'], [])
        self.assertFalse(seen['players'])
        self.assertEqual(UniversePlayer.objects.count(), 0)
        self.assertEqual(College.objects.filter(release=self.release).count(), 0)

    def test_extra_clean_rejects_an_opening_player_before_insert(self):
        profile = EraRuleProfile(release=self.release, **y1970_values())
        profile.save()
        _name, slug, _kind = COLLEGES[0]
        college = College.objects.create(release=self.release, slug=slug, name=_name)
        player = UniversePlayer(
            release=self.release,
            college=college,
            origin_franchise=None,
            draft_class=None,
            origin_profile=profile,
            universe_key='b' * 64,
            name='Bad Opening',
            birth_year=1950,
            birth_day=1,
            position='QB',
            handedness='right',
            height_in=72,
            weight_lb=200,
            cohort=UniversePlayer.Cohort.OPENING,
            draft_pool=None,
            first_pro_year=None,
            origin_draft_year=None,
            peak_age=28,
            development_rate=40,
            decline_rate=40,
        )
        calls = {'extra': 0}
        real_extra = UniversePlayer.extra_clean

        def spy_extra(self, errors):
            calls['extra'] += 1
            return real_extra(self, errors)

        before = UniversePlayer.objects.count()
        with patch.object(UniversePlayer, 'extra_clean', spy_extra):
            with self.assertRaisesRegex(OpeningGenerationError, 'archive') as caught:
                _validate_opening_players([player])
        self.assertEqual(calls['extra'], 1)
        self.assertIn('failed validation', str(caught.exception))
        self.assertEqual(UniversePlayer.objects.count(), before)

    def test_untrusted_relations_are_rejected_before_insertion(self):
        built = build_opening_cohort(SEED, RELEASE_SLUG)[0]
        profile = EraRuleProfile(release=self.release, **y1970_values())
        profile.save()
        college = College.objects.create(
            release=self.release,
            slug=built.college_slug,
            name=built.college_name,
        )
        colleges = {college.slug: college}
        peak_age, development_rate, decline_rate = growth_draws(SEED, built.universe_key)
        player = UniversePlayer(
            release=self.release,
            college=college,
            origin_franchise=None,
            draft_class=None,
            origin_profile=profile,
            universe_key=built.universe_key,
            name=built.name,
            birth_year=built.birth_year,
            birth_day=built.birth_day,
            position=built.position,
            handedness=built.handedness,
            height_in=built.height_in,
            weight_lb=built.weight_lb,
            cohort=UniversePlayer.Cohort.OPENING,
            draft_pool=None,
            first_pro_year=built.debut_year,
            peak_age=peak_age,
            development_rate=development_rate,
            decline_rate=decline_rate,
        )
        foreign_college = College(release=self.release, slug='not-approved', name='Not Approved')
        foreign_college.pk = college.pk + 1
        foreign_profile = EraRuleProfile(release=self.release, first_year=1970, last_year=1973)
        foreign_profile.pk = profile.pk + 1
        foreign_release = UniverseRelease(slug='other-release')
        foreign_release.pk = self.release.pk + 1
        franchise = self.release.franchises.filter(origin=UniverseFranchise.Origin.FOUNDING).order_by('pk').first()
        cases = (
            ('college', lambda row: setattr(row, 'college', foreign_college), 'stored colleges'),
            ('profile', lambda row: setattr(row, 'origin_profile', foreign_profile), 'y1970 profile'),
            ('release', lambda row: setattr(row, 'release', foreign_release), 'locked release'),
            ('franchise', lambda row: setattr(row, 'origin_franchise', franchise), 'origin franchise'),
        )
        for name, mutate, pattern in cases:
            with self.subTest(name=name):
                row = UniversePlayer(
                    release=player.release,
                    college=player.college,
                    origin_franchise=None,
                    draft_class=None,
                    origin_profile=player.origin_profile,
                    universe_key=player.universe_key,
                    name=player.name,
                    birth_year=player.birth_year,
                    birth_day=player.birth_day,
                    position=player.position,
                    handedness=player.handedness,
                    height_in=player.height_in,
                    weight_lb=player.weight_lb,
                    cohort=player.cohort,
                    first_pro_year=player.first_pro_year,
                    peak_age=player.peak_age,
                    development_rate=player.development_rate,
                    decline_rate=player.decline_rate,
                )
                mutate(row)
                with self.assertRaisesRegex(OpeningGenerationError, pattern):
                    _require_trusted_opening_player(
                        row, built, self.release, colleges, profile,
                    )
        player.pk = 10 ** 9
        other_franchise = self.release.franchises.filter(
            origin=UniverseFranchise.Origin.FOUNDING,
        ).exclude(placeholder_slug=built.club_slug).order_by('pk').first()
        assignment = OpeningAssignment(
            release=self.release,
            player=player,
            franchise=other_franchise,
            role=built.slot_kind,
            depth=built.depth,
        )
        franchises = {
            row.placeholder_slug: row
            for row in self.release.franchises.filter(origin=UniverseFranchise.Origin.FOUNDING)
        }
        with self.assertRaisesRegex(OpeningGenerationError, 'founding club'):
            _require_trusted_opening_assignment(
                assignment, built, self.release, player, franchises,
            )
        inserted = {'called': False}

        def reject_insert(*args, **kwargs):
            inserted['called'] = True
            raise AssertionError('Assignment bulk insertion ran after an untrusted franchise.')

        def sabotage(assignment, built_row, release, owner, stored_franchises):
            assignment.franchise = other_franchise
            return _require_trusted_opening_assignment(
                assignment, built_row, release, owner, stored_franchises,
            )

        with patch(
            'universe.generation.opening_players._require_trusted_opening_assignment',
            sabotage,
        ), patch(
            'universe.generation.opening_players.OpeningAssignment.objects.bulk_create',
            reject_insert,
        ):
            with self.assertRaisesRegex(OpeningGenerationError, 'founding club'):
                generate_opening_players(self.league.id, SEED)
        self.assertFalse(inserted['called'])
        self.assertEqual(UniversePlayer.objects.filter(release=self.release).count(), 0)
        self.assertEqual(OpeningAssignment.objects.filter(release=self.release).count(), 0)
        self.assertEqual(College.objects.filter(release=self.release).count(), 1)

    def test_a_player_constraint_failure_rolls_back_the_opening_write(self):
        seen = {}

        def reject_insert(*args, **kwargs):
            seen['colleges'] = College.objects.filter(release=self.release).count()
            seen['profiles'] = EraRuleProfile.objects.filter(release=self.release).count()
            raise IntegrityError('FOREIGN KEY constraint failed')

        with patch(
            'universe.generation.opening_players.UniversePlayer.objects.bulk_create',
            reject_insert,
        ):
            with self.assertRaisesRegex(OpeningGenerationError, 'rolled back'):
                generate_opening_players(self.league.id, SEED)
        self.assertEqual(seen['colleges'], 64)
        self.assertEqual(seen['profiles'], 1)
        self.assertEqual(College.objects.filter(release=self.release).count(), 0)
        self.assertEqual(EraRuleProfile.objects.filter(release=self.release).count(), 0)
        self.assertEqual(UniversePlayer.objects.filter(release=self.release).count(), 0)
        self.assertEqual(OpeningAssignment.objects.filter(release=self.release).count(), 0)

    def test_published_and_pinned_releases_are_refused(self):
        with self.assertRaisesRegex(OpeningGenerationError, 'published'):
            UniverseRelease.objects.filter(pk=self.release.pk).update(
                status=UniverseRelease.Status.PUBLISHED,
                catalog_fingerprint='c' * 64,
                validation_fingerprint='d' * 64,
                published_at=timezone.now(),
            )
            generate_opening_players(self.league.id, SEED)
        self.assertEqual(UniversePlayer.objects.count(), 0)
        UniverseRelease.objects.filter(pk=self.release.pk).update(
            status=UniverseRelease.Status.DRAFT,
            catalog_fingerprint='preexisting',
            validation_fingerprint='kept-validation',
            published_at=timezone.now(),
        )
        with self.assertRaisesRegex(OpeningGenerationError, 'published release cannot be changed'):
            generate_opening_players(self.league.id, SEED)
        self.assertEqual(UniversePlayer.objects.count(), 0)
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
        with self.assertRaisesRegex(OpeningGenerationError, 'timeline'):
            generate_opening_players(self.league.id, SEED)
        self.assertEqual(UniversePlayer.objects.count(), 0)
        self.assertEqual(Timeline.objects.filter(release=self.release).count(), 1)
        Timeline.objects.filter(release=self.release).delete()

        refused = StringIO()
        UniverseRelease.objects.filter(pk=self.release.pk).update(
            status=UniverseRelease.Status.PUBLISHED,
            catalog_fingerprint='e' * 64,
            validation_fingerprint='f' * 64,
            published_at=timezone.now(),
        )
        with self.assertRaises(CommandError):
            call_command(
                'generate_opening_players',
                league_id=self.league.id,
                seed=SEED,
                stdout=refused,
            )
        self.assertEqual(UniversePlayer.objects.count(), 0)

    def test_a_release_without_the_schedule_writes_nothing(self):
        league = League(name='Bare League', slug='bare-league', tier=1, active=False)
        league.save()
        UniverseRelease(
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
        ).save()
        with self.assertRaisesRegex(OpeningGenerationError, 'fg-founding-2'):
            generate_opening_players(league.id, SEED)
        self.assertEqual(UniversePlayer.objects.count(), 0)
        self.assertEqual(College.objects.count(), 0)
        self.assertEqual(EraRuleProfile.objects.count(), 0)
