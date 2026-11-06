import socket
from collections import Counter, defaultdict
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
    generate_opening_players,
)
from universe.generation.player_counts import (
    EXPANSION_YEARS,
    UNDRAFTED_MULTIPLIER,
    active_quota,
    draft_class,
    draft_classes,
    draft_rounds,
    prospect_totals,
    taxi_quota,
)
from universe.generation.player_identities import build_prospects, year_position_counts
from universe.generation.prospects import (
    ProspectGenerationError,
    _insert_players,
    _validate_generated_players,
    generate_prospects,
    later_profile_values,
)
from universe.generation.schedule import (
    GENERATOR_VERSION,
    OPENING_SEATS,
    POOL_SIZE,
    generate_schedule_release,
)
from universe.models import (
    FranchiseSeasonMembership,
    HistoricalStanding,
    UniverseFranchise,
    UniverseRelease,
)
from universe.people import (
    College,
    PlayerRatingProfile,
    UniversePlayer,
    UniverseProSeason,
    UniverseStaffIdentity,
)
from universe.rules import (
    DraftClass,
    EraCollegeWeight,
    EraRuleProfile,
    ExpansionClub,
    ExpansionEvent,
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


def _prepare_release(league_slug, *, opening=True):
    league = League(
        name=league_slug,
        slug=league_slug,
        tier=1,
        active=True,
    )
    league.save()
    release = UniverseRelease(
        slug=RELEASE_SLUG if league_slug == 'prospect-league' else f'{league_slug}-release',
        root_league=league,
        generator_version='pending',
        seed=1,
        founding_franchise_count=26,
        expansion_schedule_version=3,
        first_season_year=1970,
        season_count=60,
        last_season_year=2029,
        status=UniverseRelease.Status.DRAFT,
    )
    release.save()
    release.catalog_fingerprint = 'preexisting'
    release.validation_fingerprint = 'kept-validation'
    release.expansion_schedule_version = 3
    release.save()
    if generate_schedule_release(league.id, SEED) != 'created':
        raise AssertionError('The schedule fixture was not created.')
    release.refresh_from_db()
    if opening and generate_opening_players(league.id, SEED) != 'created':
        raise AssertionError('The opening fixture was not created.')
    release.refresh_from_db()
    return league, release


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


class ProspectWriterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.league, cls.release = _prepare_release('prospect-league')
        cls.opening_player_ids = set(
            UniversePlayer.objects.filter(cohort='opening').values_list('pk', flat=True)
        )
        cls.assignment_ids = set(OpeningAssignment.objects.values_list('pk', flat=True))
        cls.college_ids = set(
            cls.release.colleges.values_list('pk', flat=True)
        )
        cls.y1970_id = EraRuleProfile.objects.get(profile_key='y1970').pk
        cls.kept_counts = {
            'franchises': UniverseFranchise.objects.count(),
            'memberships': FranchiseSeasonMembership.objects.count(),
            'expansion_events': ExpansionEvent.objects.count(),
            'expansion_clubs': ExpansionClub.objects.count(),
            'staff': UniverseStaffIdentity.objects.count(),
            'staff_assignments': OpeningStaffAssignment.objects.count(),
            'standings': HistoricalStanding.objects.count(),
        }
        cls.metadata = (
            cls.release.generator_version,
            cls.release.seed,
            cls.release.status,
            cls.release.catalog_fingerprint,
            cls.release.validation_fingerprint,
            cls.release.expansion_schedule_version,
            cls.release.published_at,
        )
        if generate_prospects(cls.league.id, SEED) != 'created':
            raise AssertionError('The prospect fixture was not created.')
        cls.release.refresh_from_db()

    def test_writes_the_approved_cards_classes_and_prospects(self):
        self.release.refresh_from_db()
        self.assertEqual(self.release.generator_version, GENERATOR_VERSION)
        self.assertEqual(self.release.seed, SEED)
        self.assertEqual(self.release.status, UniverseRelease.Status.DRAFT)
        self.assertEqual(self.release.catalog_fingerprint, 'preexisting')
        self.assertEqual(self.release.validation_fingerprint, 'kept-validation')
        self.assertEqual(self.release.expansion_schedule_version, 3)
        self.assertIsNone(self.release.published_at)
        self.assertEqual(
            (
                self.release.generator_version,
                self.release.seed,
                self.release.status,
                self.release.catalog_fingerprint,
                self.release.validation_fingerprint,
                self.release.expansion_schedule_version,
                self.release.published_at,
            ),
            self.metadata,
        )
        self.assertEqual(UniverseFranchise.objects.count(), self.kept_counts['franchises'])
        self.assertEqual(FranchiseSeasonMembership.objects.count(), self.kept_counts['memberships'])
        self.assertEqual(ExpansionEvent.objects.count(), self.kept_counts['expansion_events'])
        self.assertEqual(ExpansionClub.objects.count(), self.kept_counts['expansion_clubs'])
        self.assertEqual(UniverseStaffIdentity.objects.count(), self.kept_counts['staff'])
        self.assertEqual(UniverseStaffIdentity.objects.count(), POOL_SIZE * 2)
        self.assertEqual(
            OpeningStaffAssignment.objects.count(),
            self.kept_counts['staff_assignments'],
        )
        self.assertEqual(OpeningStaffAssignment.objects.count(), OPENING_SEATS * 2)
        self.assertEqual(HistoricalStanding.objects.count(), self.kept_counts['standings'])
        self.assertEqual(set(self.release.colleges.values_list('pk', flat=True)), self.college_ids)
        self.assertEqual(EraRuleProfile.objects.get(profile_key='y1970').pk, self.y1970_id)

        profiles = list(EraRuleProfile.objects.filter(release=self.release).order_by('first_year'))
        self.assertEqual(len(profiles), 12)
        self.assertEqual(profiles[0].profile_key, 'y1970')
        self.assertEqual((profiles[0].first_year, profiles[0].last_year), (1970, 1973))
        previous_last = 1969
        for profile in profiles:
            self.assertEqual(profile.first_year, previous_last + 1)
            self.assertGreaterEqual(profile.last_year, profile.first_year)
            if profile.cap_status == 'on':
                self.assertEqual(profile.cap_limit, 10000)
            else:
                self.assertIsNone(profile.cap_limit)
            self.assertIsNone(profile.minimum_salary)
            self.assertIsNone(profile.rookie_slot_scale)
            previous_last = profile.last_year
        self.assertEqual(previous_last, 2029)

        expected_cards = later_profile_values()
        self.assertEqual(
            [profile.profile_key for profile in profiles[1:]],
            [card['profile_key'] for card in expected_cards],
        )
        headlines = {
            'y1974': ('controlled', 'run-first', 40, 8, 17, 14, 'eight_team_3_and_1', 'sudden_death', 'unofficial', 'off'),
            'y1976': ('controlled', 'run-first', 40, 8, 17, 14, 'eight_team_2_and_2', 'sudden_death', 'unofficial', 'off'),
            'y1978': ('controlled', 'passing-rules', 45, 8, 12, 16, 'eight_team_2_and_2', 'sudden_death', 'unofficial', 'off'),
            'y1987': ('transitional', 'timing-specialists', 47, 8, 8, 16, 'eight_team_2_and_2', 'sudden_death', 'official', 'off'),
            'y1995': ('modern', 'cap-and-size', 53, 8, 7, 16, 'eight_team_3_and_1', 'sudden_death', 'official', 'on'),
            'y1999': ('modern', 'cap-and-size', 53, 8, 7, 16, 'twelve_team_fill_to_six', 'sudden_death', 'official', 'on'),
            'y2002': ('modern', 'cap-and-size', 53, 8, 7, 16, 'twelve_team_4_and_2', 'sudden_death', 'official', 'on'),
            'y2004': ('modern', 'spread-influence', 53, 8, 7, 16, 'twelve_team_4_and_2', 'sudden_death', 'official', 'on'),
            'y2012': ('modern', 'spread-influence', 53, 8, 7, 16, 'twelve_team_4_and_2', 'both_teams_possess', 'official', 'on'),
            'y2014': ('modern', 'modern-passing', 53, 8, 7, 16, 'twelve_team_4_and_2', 'both_teams_possess', 'official', 'on'),
            'y2021': ('modern', 'modern-passing', 53, 8, 7, 17, 'twelve_team_4_and_2', 'both_teams_possess', 'official', 'on'),
        }
        stored_by_key = {profile.profile_key: profile for profile in profiles}
        for card in expected_cards:
            profile = stored_by_key[card['profile_key']]
            for field, value in card.items():
                self.assertEqual(getattr(profile, field), value, card['profile_key'])
            self.assertEqual(
                (
                    profile.market,
                    profile.style_key,
                    profile.active_limit,
                    profile.taxi_limit,
                    profile.draft_rounds,
                    profile.schedule_length,
                    profile.playoff_format,
                    profile.overtime_rule,
                    profile.sack_leaderboard,
                    profile.cap_status,
                ),
                headlines[profile.profile_key],
            )
            self.assertEqual(profile.draft_rounds, draft_rounds(profile.first_year))
            for position, count in active_quota(profile.first_year).items():
                self.assertEqual(getattr(profile, f'quota_active_{position.lower()}'), count)
            for position, count in taxi_quota(profile.first_year).items():
                self.assertEqual(getattr(profile, f'quota_taxi_{position.lower()}'), count)
        fill = EraRuleProfile.objects.filter(playoff_format='twelve_team_fill_to_six')
        self.assertEqual(fill.count(), 1)
        y1999 = fill.get()
        self.assertEqual(y1999.profile_key, 'y1999')
        self.assertEqual((y1999.first_year, y1999.last_year), (1999, 2001))
        cap_on = EraRuleProfile.objects.filter(cap_status='on')
        self.assertEqual(cap_on.count(), 7)
        self.assertEqual(
            set(cap_on.values_list('profile_key', flat=True)),
            {'y1995', 'y1999', 'y2002', 'y2004', 'y2012', 'y2014', 'y2021'},
        )
        self.assertFalse(cap_on.exclude(cap_limit=10000).exists())
        self.assertEqual(EraRuleProfile.objects.filter(cap_status='off').count(), 5)
        controlled = stored_by_key['y1974']
        self.assertEqual(
            (
                controlled.ir_max,
                controlled.ir_min_weeks,
                controlled.ir_returns_allowed,
                controlled.rookie_contract_years_min,
                controlled.rookie_contract_years_max,
                controlled.veteran_contract_years_min,
                controlled.veteran_contract_years_max,
                controlled.accrued_seasons_for_ufa,
                controlled.future_pick_years,
                controlled.waiver_rule,
                controlled.free_agency_type,
                controlled.fa_compensation,
                controlled.franchise_tag,
            ),
            (2, 8, 0, 1, 3, 1, 5, None, 1, 'all_releases', 'reserve', 'none', 'off'),
        )
        self.assertEqual(stored_by_key['y1976'].accrued_seasons_for_ufa, None)
        self.assertEqual(stored_by_key['y1978'].accrued_seasons_for_ufa, None)
        transitional = stored_by_key['y1987']
        self.assertEqual(transitional.accrued_seasons_for_ufa, 4)
        self.assertEqual(transitional.free_agency_type, 'restricted')
        self.assertEqual(transitional.fa_compensation, 'tender_pick')
        self.assertEqual(transitional.future_pick_years, 2)
        modern = stored_by_key['y1995']
        self.assertEqual(modern.accrued_seasons_for_ufa, 4)
        self.assertEqual(modern.free_agency_type, 'unrestricted_and_restricted')
        self.assertEqual(modern.fa_compensation, 'restricted_only')
        self.assertEqual(modern.franchise_tag, 'top_five_position_average')
        self.assertEqual(modern.future_pick_years, 3)
        self.assertEqual(modern.ir_max, 4)
        self.assertEqual(modern.rookie_contract_years_min, 4)
        self.assertEqual(modern.rookie_contract_years_max, 4)

        classes = list(DraftClass.objects.filter(release=self.release).order_by('season_year'))
        counts = draft_classes()
        self.assertEqual(len(classes), 60)
        self.assertEqual([row.season_year for row in classes], [row.season_year for row in counts])
        for stored, count in zip(classes, counts):
            self.assertEqual(stored.franchise_count, count.franchise_count)
            self.assertEqual(stored.rounds, count.rounds)
            self.assertEqual(stored.draftable_count, count.draftable_count)
            self.assertEqual(stored.undrafted_count, count.undrafted_count)
            self.assertEqual(stored.undrafted_multiplier, UNDRAFTED_MULTIPLIER)
            self.assertEqual(stored.profile.draft_rounds, stored.rounds)
            self.assertLessEqual(stored.profile.first_year, stored.season_year)
            self.assertGreaterEqual(stored.profile.last_year, stored.season_year)
        self.assertEqual(
            DraftClass.objects.get(season_year=1999).profile.profile_key,
            'y1999',
        )
        self.assertEqual(
            DraftClass.objects.get(season_year=1970).profile.profile_key,
            'y1970',
        )
        self.assertEqual(
            DraftClass.objects.get(season_year=2021).profile.profile_key,
            'y2021',
        )

        draftable, undrafted, prospects = prospect_totals()
        self.assertEqual((draftable, undrafted, prospects), (16183, 3594, 19777))
        prospect_rows = UniversePlayer.objects.filter(release=self.release, cohort='draft')
        self.assertEqual(prospect_rows.count(), 19777)
        self.assertEqual(prospect_rows.filter(draft_pool='draftable').count(), 16183)
        self.assertEqual(prospect_rows.filter(draft_pool='undrafted').count(), 3594)
        self.assertEqual(
            UniversePlayer.objects.filter(release=self.release, cohort='opening').count(),
            1378,
        )
        self.assertEqual(set(
            UniversePlayer.objects.filter(cohort='opening').values_list('pk', flat=True)
        ), self.opening_player_ids)
        self.assertEqual(set(OpeningAssignment.objects.values_list('pk', flat=True)), self.assignment_ids)
        self.assertEqual(OpeningAssignment.objects.count(), 1378)
        self.assertFalse(
            OpeningAssignment.objects.filter(player__cohort='draft').exists()
        )
        self.assertFalse(prospect_rows.exclude(origin_draft_round=None).exists())
        self.assertFalse(prospect_rows.exclude(origin_draft_pick=None).exists())
        self.assertFalse(prospect_rows.exclude(origin_franchise=None).exists())
        self.assertFalse(prospect_rows.exclude(board_rank=None).exists())
        self.assertFalse(prospect_rows.exclude(secondary_position_1=None).exists())
        self.assertFalse(prospect_rows.exclude(secondary_position_2=None).exists())
        self.assertFalse(prospect_rows.exclude(trait_1=None).exists())
        self.assertFalse(prospect_rows.exclude(trait_2=None).exists())
        self.assertFalse(prospect_rows.exclude(trait_3=None).exists())
        self.assertFalse(prospect_rows.filter(name='Joe LeCrone').exists())
        self.assertEqual(PlayerRatingProfile.objects.count(), 0)
        self.assertEqual(OpeningContract.objects.count(), 0)
        self.assertEqual(EraCollegeWeight.objects.count(), 0)
        self.assertEqual(UniverseProSeason.objects.count(), 0)
        self.assertEqual(Timeline.objects.count(), 0)

        position_counts = year_position_counts()
        actual_positions = defaultdict(lambda: defaultdict(int))
        pool_counts = defaultdict(lambda: {'draftable': 0, 'undrafted': 0})
        for year, position, pool in prospect_rows.values_list(
            'draft_class__season_year',
            'position',
            'draft_pool',
        ):
            actual_positions[year][position] += 1
            pool_counts[year][pool] += 1
        for count in counts:
            expected_positions = position_counts[count.season_year]
            actual = {
                position: actual_positions[count.season_year][position]
                for position in expected_positions
            }
            self.assertEqual(actual, expected_positions)
            self.assertEqual(
                sum(actual_positions[count.season_year].values()),
                sum(expected_positions.values()),
            )
            self.assertEqual(pool_counts[count.season_year]['draftable'], count.draftable_count)
            self.assertEqual(pool_counts[count.season_year]['undrafted'], count.undrafted_count)
            self.assertEqual(
                prospect_rows.filter(draft_class__season_year=count.season_year).count(),
                count.class_size,
            )
        for year in EXPANSION_YEARS:
            self.assertEqual(
                prospect_rows.filter(draft_class__season_year=year).count(),
                draft_class(year).class_size,
            )

        stored_by_player_key = {
            player.universe_key: player
            for player in prospect_rows.select_related('college', 'origin_profile', 'draft_class')
        }
        built = build_prospects(SEED, RELEASE_SLUG)
        self.assertEqual(len(built), 19777)
        by_year = defaultdict(list)
        for row in built:
            by_year[row.season_year].append(row)
            stored = stored_by_player_key[row.universe_key]
            width = draft_rounds(row.season_year) + 2
            expected_pool = 'draftable' if row.index % width < draft_rounds(row.season_year) else 'undrafted'
            self.assertEqual(stored.draft_pool, expected_pool)
            self.assertEqual(stored.draft_pool, row.draft_pool)
            self.assertEqual(stored.position, row.position)
            self.assertEqual(stored.name, row.name)
            self.assertEqual(stored.birth_year, row.birth_year)
            self.assertEqual(stored.birth_day, row.birth_day)
            self.assertEqual(stored.college.slug, row.college_slug)
            self.assertEqual(stored.handedness, row.handedness)
            self.assertEqual(stored.height_in, row.height_in)
            self.assertEqual(stored.weight_lb, row.weight_lb)
            self.assertEqual(stored.peak_age, row.peak_age)
            self.assertEqual(stored.development_rate, row.development_rate)
            self.assertEqual(stored.decline_rate, row.decline_rate)
            self.assertEqual(stored.draft_class.season_year, row.season_year)
            self.assertLessEqual(stored.origin_profile.first_year, row.season_year)
            self.assertGreaterEqual(stored.origin_profile.last_year, row.season_year)
            self.assertEqual(stored.origin_profile.draft_rounds, stored.draft_class.rounds)
            if expected_pool == 'draftable':
                self.assertEqual(stored.first_pro_year, row.season_year)
                self.assertEqual(stored.origin_draft_year, row.season_year)
            else:
                self.assertIsNone(stored.first_pro_year)
                self.assertIsNone(stored.origin_draft_year)
        for year, group in by_year.items():
            group.sort(key=lambda row: row.index)
            width = draft_rounds(year) + 2
            self.assertEqual(len(group) % width, 0)
            for start in range(0, len(group), width):
                block = group[start:start + width]
                self.assertEqual(sum(row.draft_pool == 'undrafted' for row in block), 2)
                self.assertTrue(all(row.draft_pool == 'draftable' for row in block[:-2]))
                self.assertTrue(all(row.draft_pool == 'undrafted' for row in block[-2:]))

    def test_identical_rerun_preserves_row_ids(self):
        profile_ids = set(EraRuleProfile.objects.values_list('pk', flat=True))
        class_ids = set(DraftClass.objects.values_list('pk', flat=True))
        player_ids = set(UniversePlayer.objects.values_list('pk', flat=True))
        self.assertEqual(generate_prospects(self.league.id, SEED), 'unchanged')
        again = StringIO()
        call_command(
            'generate_prospects',
            league_id=self.league.id,
            seed=SEED,
            stdout=again,
        )
        self.assertEqual(again.getvalue().strip(), 'unchanged')
        self.assertEqual(set(EraRuleProfile.objects.values_list('pk', flat=True)), profile_ids)
        self.assertEqual(set(DraftClass.objects.values_list('pk', flat=True)), class_ids)
        self.assertEqual(set(UniversePlayer.objects.values_list('pk', flat=True)), player_ids)
        self.assertEqual(set(OpeningAssignment.objects.values_list('pk', flat=True)), self.assignment_ids)
        self.assertEqual(UniversePlayer.objects.filter(cohort='draft').count(), 19777)
        self.release.refresh_from_db()
        self.assertEqual(self.release.catalog_fingerprint, 'preexisting')
        self.assertEqual(self.release.validation_fingerprint, 'kept-validation')
        self.assertEqual(self.release.expansion_schedule_version, 3)
        self.assertEqual(self.release.status, UniverseRelease.Status.DRAFT)

    def test_opening_writer_is_unchanged_after_prospects(self):
        opening_ids = set(
            UniversePlayer.objects.filter(cohort='opening').values_list('pk', flat=True)
        )
        prospect_ids = set(
            UniversePlayer.objects.filter(cohort='draft').values_list('pk', flat=True)
        )
        assignment_ids = set(OpeningAssignment.objects.values_list('pk', flat=True))
        self.assertEqual(generate_opening_players(self.league.id, SEED), 'unchanged')
        self.assertEqual(set(
            UniversePlayer.objects.filter(cohort='opening').values_list('pk', flat=True)
        ), opening_ids)
        self.assertEqual(set(
            UniversePlayer.objects.filter(cohort='draft').values_list('pk', flat=True)
        ), prospect_ids)
        self.assertEqual(set(OpeningAssignment.objects.values_list('pk', flat=True)), assignment_ids)
        self.assertEqual(len(opening_ids), 1378)
        self.assertEqual(len(prospect_ids), 19777)

        player = UniversePlayer.objects.filter(cohort='opening').order_by('pk').first()
        UniversePlayer.objects.filter(pk=player.pk).update(name='Edited Opening')
        with self.assertRaisesRegex(OpeningGenerationError, 'will not delete or replace'):
            generate_opening_players(self.league.id, SEED)
        player.refresh_from_db()
        self.assertEqual(player.name, 'Edited Opening')
        self.assertEqual(set(
            UniversePlayer.objects.filter(cohort='draft').values_list('pk', flat=True)
        ), prospect_ids)

        extra = UniversePlayer.objects.filter(cohort='opening').order_by('pk').last()
        kept_name = extra.name
        kept_pk = extra.pk
        extra.pk = None
        extra.universe_key = 'd' * 64
        extra.name = 'Extra Opening'
        UniversePlayer.objects.bulk_create([extra])
        with self.assertRaisesRegex(OpeningGenerationError, 'will not delete or replace'):
            generate_opening_players(self.league.id, SEED)
        self.assertEqual(UniversePlayer.objects.get(pk=kept_pk).name, kept_name)
        self.assertEqual(UniversePlayer.objects.get(universe_key='d' * 64).name, 'Extra Opening')
        self.assertEqual(set(
            UniversePlayer.objects.filter(cohort='draft').values_list('pk', flat=True)
        ), prospect_ids)
        self.assertEqual(UniversePlayer.objects.filter(cohort='opening').count(), 1379)

    def test_a_different_seed_refuses_without_deleting(self):
        player_ids = set(UniversePlayer.objects.values_list('pk', flat=True))
        with self.assertRaisesRegex(ProspectGenerationError, 'will not change the release seed'):
            generate_prospects(self.league.id, OTHER_SEED)
        self.assertEqual(set(UniversePlayer.objects.values_list('pk', flat=True)), player_ids)
        self.release.refresh_from_db()
        self.assertEqual(self.release.seed, SEED)
        self.assertEqual(DraftClass.objects.count(), 60)

    def test_mismatched_rows_are_kept(self):
        player = UniversePlayer.objects.filter(cohort='draft').order_by('pk').first()
        UniversePlayer.objects.filter(pk=player.pk).update(name='Edited Name')
        with self.assertRaisesRegex(ProspectGenerationError, 'will not delete or replace'):
            generate_prospects(self.league.id, SEED)
        player.refresh_from_db()
        self.assertEqual(player.name, 'Edited Name')
        self.assertEqual(UniversePlayer.objects.filter(cohort='draft').count(), 19777)
        self.assertEqual(set(OpeningAssignment.objects.values_list('pk', flat=True)), self.assignment_ids)

        profile = EraRuleProfile.objects.get(profile_key='y1999')
        EraRuleProfile.objects.filter(pk=profile.pk).update(playoff_format='twelve_team_4_and_2')
        with self.assertRaisesRegex(ProspectGenerationError, 'will not delete or replace'):
            generate_prospects(self.league.id, SEED)
        profile.refresh_from_db()
        self.assertEqual(profile.playoff_format, 'twelve_team_4_and_2')
        self.assertEqual(EraRuleProfile.objects.get(profile_key='y1970').pk, self.y1970_id)

    def test_a_different_cap_fails_closed(self):
        profile = EraRuleProfile.objects.get(profile_key='y1995')
        self.assertEqual(profile.cap_limit, 10000)
        EraRuleProfile.objects.filter(pk=profile.pk).update(cap_limit=None)
        with self.assertRaisesRegex(ProspectGenerationError, 'will not delete or replace'):
            generate_prospects(self.league.id, SEED)
        profile.refresh_from_db()
        self.assertIsNone(profile.cap_limit)
        self.assertIsNone(profile.minimum_salary)
        self.assertIsNone(profile.rookie_slot_scale)
        self.assertEqual(
            EraRuleProfile.objects.get(profile_key='y1999').cap_limit,
            10000,
        )
        self.assertIsNone(EraRuleProfile.objects.get(profile_key='y1970').cap_limit)

    def test_a_changed_draft_class_is_refused_on_rerun(self):
        profile_ids = set(EraRuleProfile.objects.values_list('pk', flat=True))
        class_ids = set(DraftClass.objects.values_list('pk', flat=True))
        player_ids = set(UniversePlayer.objects.values_list('pk', flat=True))
        row = DraftClass.objects.get(release=self.release, season_year=1970)
        other = EraRuleProfile.objects.get(release=self.release, profile_key='y1974')
        DraftClass.objects.filter(pk=row.pk).update(profile_id=other.pk)
        with self.assertRaisesRegex(ProspectGenerationError, 'will not delete or replace'):
            generate_prospects(self.league.id, SEED)
        row.refresh_from_db()
        self.assertEqual(row.profile_id, other.pk)
        self.assertEqual(set(EraRuleProfile.objects.values_list('pk', flat=True)), profile_ids)
        self.assertEqual(set(DraftClass.objects.values_list('pk', flat=True)), class_ids)
        self.assertEqual(set(UniversePlayer.objects.values_list('pk', flat=True)), player_ids)
        self.assertEqual(set(OpeningAssignment.objects.values_list('pk', flat=True)), self.assignment_ids)

    def test_published_and_pinned_releases_are_refused(self):
        player_ids = set(UniversePlayer.objects.values_list('pk', flat=True))
        UniverseRelease.objects.filter(pk=self.release.pk).update(
            status=UniverseRelease.Status.PUBLISHED,
            catalog_fingerprint='c' * 64,
            validation_fingerprint='d' * 64,
            published_at=timezone.now(),
        )
        with self.assertRaisesRegex(ProspectGenerationError, 'published'):
            generate_prospects(self.league.id, SEED)
        self.assertEqual(set(UniversePlayer.objects.values_list('pk', flat=True)), player_ids)

        UniverseRelease.objects.filter(pk=self.release.pk).update(
            status=UniverseRelease.Status.DRAFT,
            catalog_fingerprint='preexisting',
            validation_fingerprint='kept-validation',
            published_at=timezone.now(),
        )
        with self.assertRaisesRegex(ProspectGenerationError, 'published release cannot be changed'):
            generate_prospects(self.league.id, SEED)
        self.assertEqual(UniversePlayer.objects.count(), 1378 + 19777)
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
        with self.assertRaisesRegex(ProspectGenerationError, 'timeline'):
            generate_prospects(self.league.id, SEED)
        self.assertEqual(UniversePlayer.objects.filter(cohort='draft').count(), 19777)
        self.assertEqual(Timeline.objects.filter(release=self.release).count(), 1)

        UniverseRelease.objects.filter(pk=self.release.pk).update(
            status=UniverseRelease.Status.PUBLISHED,
            catalog_fingerprint='e' * 64,
            validation_fingerprint='f' * 64,
            published_at=timezone.now(),
        )
        with self.assertRaises(CommandError):
            call_command(
                'generate_prospects',
                league_id=self.league.id,
                seed=SEED,
                stdout=StringIO(),
            )
        self.assertEqual(UniversePlayer.objects.filter(cohort='draft').count(), 19777)


class ProspectCommandTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.league, cls.release = _prepare_release('prospect-command')

    def test_command_prints_created_and_then_unchanged(self):
        created = StringIO()
        executed = []

        def count_queries(execute, sql, params, many, context):
            executed.append(sql)
            return execute(sql, params, many, context)

        connection.execute_wrappers.append(count_queries)
        try:
            call_command(
                'generate_prospects',
                league_id=self.league.id,
                seed=SEED,
                stdout=created,
            )
        finally:
            connection.execute_wrappers.remove(count_queries)
        fk_lookups = []
        player_inserts = []
        for sql in executed:
            if sql.startswith('SELECT %s AS "a" FROM "universe_'):
                fk_lookups.append(sql)
            elif sql.startswith('INSERT INTO "universe_universeplayer"'):
                player_inserts.append(sql)
        repeats = Counter(executed)
        top = [(count, sql[:160]) for sql, count in repeats.most_common(3)]
        fk_repeats = Counter(fk_lookups)
        # One pass per prospect would repeat a statement about 19,777 times.
        # Profile and class validation may still look up their own foreign keys.
        self.assertLess(
            len(executed),
            4000,
            f'full write ran {len(executed)} queries; top {top}',
        )
        self.assertLess(
            max(repeats.values(), default=0),
            2000,
            f'a statement repeated once per prospect; top {top}',
        )
        self.assertLess(
            len(fk_lookups),
            500,
            f'foreign-key existence queries were {len(fk_lookups)}',
        )
        self.assertLess(max(fk_repeats.values(), default=0), 200)
        self.assertGreater(len(player_inserts), 0)
        self.assertLess(len(player_inserts), 19777)
        self.assertEqual(created.getvalue().strip(), 'created')
        self.assertEqual(UniversePlayer.objects.filter(cohort='draft').count(), 19777)
        self.assertEqual(DraftClass.objects.count(), 60)
        self.assertEqual(EraRuleProfile.objects.count(), 12)
        opening_ids = set(
            UniversePlayer.objects.filter(cohort='opening').values_list('pk', flat=True)
        )
        again = StringIO()
        call_command(
            'generate_prospects',
            league_id=self.league.id,
            seed=SEED,
            stdout=again,
        )
        self.assertEqual(again.getvalue().strip(), 'unchanged')
        self.assertEqual(set(
            UniversePlayer.objects.filter(cohort='opening').values_list('pk', flat=True)
        ), opening_ids)
        self.release.refresh_from_db()
        self.assertEqual(self.release.catalog_fingerprint, 'preexisting')
        self.assertEqual(self.release.validation_fingerprint, 'kept-validation')
        self.assertEqual(self.release.status, UniverseRelease.Status.DRAFT)
        self.assertEqual(self.release.seed, SEED)

    def test_a_different_seed_writes_nothing_before_prospects_exist(self):
        with self.assertRaisesRegex(ProspectGenerationError, 'will not change the release seed'):
            generate_prospects(self.league.id, OTHER_SEED)
        self.assertEqual(UniversePlayer.objects.filter(cohort='draft').count(), 0)
        self.assertEqual(DraftClass.objects.count(), 0)
        self.assertEqual(EraRuleProfile.objects.count(), 1)
        self.assertEqual(UniversePlayer.objects.filter(cohort='opening').count(), 1378)

    def test_a_mismatched_college_is_refused_before_insertion(self):
        college = self.release.colleges.order_by('slug').first()
        College = college.__class__
        College.objects.filter(pk=college.pk).update(name='Wrong College')
        with self.assertRaisesRegex(ProspectGenerationError, 'will not delete or replace'):
            generate_prospects(self.league.id, SEED)
        college.refresh_from_db()
        self.assertEqual(college.name, 'Wrong College')
        self.assertEqual(EraRuleProfile.objects.count(), 1)
        self.assertEqual(DraftClass.objects.count(), 0)
        self.assertEqual(UniversePlayer.objects.filter(cohort='draft').count(), 0)
        self.assertEqual(UniversePlayer.objects.filter(cohort='opening').count(), 1378)

    def test_a_changed_opening_player_is_refused_before_insertion(self):
        player = UniversePlayer.objects.filter(cohort='opening').order_by('pk').first()
        UniversePlayer.objects.filter(pk=player.pk).update(name='Edited Opening')
        with self.assertRaisesRegex(ProspectGenerationError, 'will not delete or replace'):
            generate_prospects(self.league.id, SEED)
        player.refresh_from_db()
        self.assertEqual(player.name, 'Edited Opening')
        self.assertEqual(EraRuleProfile.objects.count(), 1)
        self.assertEqual(DraftClass.objects.count(), 0)
        self.assertEqual(UniversePlayer.objects.filter(cohort='draft').count(), 0)
        self.assertEqual(OpeningAssignment.objects.filter(release=self.release).count(), 1378)

    def test_field_validation_fails_before_player_insertion(self):
        seen = {'players': False, 'profiles': None, 'flags': None, 'queries': None}
        real_clean = UniversePlayer.full_clean

        def spy_clean(self, *args, **kwargs):
            seen['profiles'] = EraRuleProfile.objects.count()
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

        def bad_prospects(seed, slug):
            rows = list(build_prospects(seed, slug))
            rows[0] = rows[0]._replace(position='NOPE')
            return tuple(rows)

        def reject_insert(*args, **kwargs):
            seen['players'] = True
            raise AssertionError('Player bulk insertion ran after invalid field data.')

        with patch(
            'universe.generation.prospects.build_prospects',
            bad_prospects,
        ), patch(
            'universe.generation.prospects.UniversePlayer.full_clean',
            spy_clean,
        ), patch(
            'universe.generation.prospects.UniversePlayer.objects.bulk_create',
            reject_insert,
        ):
            with self.assertRaisesRegex(ProspectGenerationError, 'failed validation'):
                generate_prospects(self.league.id, SEED)
        self.assertEqual(seen['flags'], (False, False))
        self.assertEqual(set(seen['exclude']), {
            'release',
            'college',
            'origin_franchise',
            'draft_class',
            'origin_profile',
        })
        self.assertEqual(seen['queries'], [])
        self.assertEqual(seen['profiles'], 12)
        self.assertFalse(seen['players'])
        self.assertEqual(EraRuleProfile.objects.count(), 1)
        self.assertEqual(DraftClass.objects.count(), 0)
        self.assertEqual(UniversePlayer.objects.filter(cohort='draft').count(), 0)

    def test_extra_clean_rejects_a_draftable_player_before_insert(self):
        profile = EraRuleProfile.objects.get(release=self.release, profile_key='y1970')
        college = self.release.colleges.order_by('slug').first()
        draft_class = DraftClass(
            release=self.release,
            profile=profile,
            season_year=1970,
            franchise_count=26,
            rounds=17,
            draftable_count=17 * 26,
            undrafted_multiplier=2,
            undrafted_count=52,
        )
        draft_class.save()
        player = UniversePlayer(
            release=self.release,
            college=college,
            origin_franchise=None,
            draft_class=draft_class,
            origin_profile=profile,
            universe_key='b' * 64,
            name='Bad Prospect',
            birth_year=1950,
            birth_day=1,
            position='QB',
            secondary_position_1=None,
            secondary_position_2=None,
            handedness='right',
            height_in=72,
            weight_lb=200,
            cohort=UniversePlayer.Cohort.DRAFT,
            draft_pool=UniversePlayer.DraftPool.DRAFTABLE,
            first_pro_year=None,
            origin_draft_year=None,
            origin_draft_round=None,
            origin_draft_pick=None,
            peak_age=28,
            development_rate=40,
            decline_rate=40,
            board_rank=None,
            trait_1=None,
            trait_2=None,
            trait_3=None,
        )
        before = UniversePlayer.objects.count()
        calls = {'extra': 0}
        real_extra = UniversePlayer.extra_clean

        def spy_extra(self, errors):
            calls['extra'] += 1
            return real_extra(self, errors)

        with patch.object(UniversePlayer, 'extra_clean', spy_extra):
            with self.assertRaisesRegex(ProspectGenerationError, 'class year') as caught:
                _validate_generated_players([player])
        self.assertEqual(calls['extra'], 1)
        self.assertIn('failed validation', str(caught.exception))
        self.assertEqual(UniversePlayer.objects.count(), before)
        self.assertFalse(UniversePlayer.objects.filter(universe_key='b' * 64).exists())

    def test_untrusted_relations_are_rejected_before_insertion(self):
        profile = EraRuleProfile.objects.get(release=self.release, profile_key='y1970')
        college = self.release.colleges.order_by('slug').first()
        draft_class = DraftClass(
            release=self.release,
            profile=profile,
            season_year=1970,
            franchise_count=26,
            rounds=17,
            draftable_count=17 * 26,
            undrafted_multiplier=2,
            undrafted_count=52,
        )
        draft_class.save()
        college_ids = set(self.release.colleges.values_list('pk', flat=True))
        classes_by_year = {1970: draft_class}
        franchise = UniverseFranchise.objects.filter(release=self.release).order_by('pk').first()

        def build():
            return UniversePlayer(
                release=self.release,
                college=college,
                origin_franchise=None,
                draft_class=draft_class,
                origin_profile=profile,
                universe_key='c' * 64,
                name='Relation Prospect',
                birth_year=1950,
                birth_day=1,
                position='QB',
                secondary_position_1=None,
                secondary_position_2=None,
                handedness='right',
                height_in=72,
                weight_lb=200,
                cohort=UniversePlayer.Cohort.DRAFT,
                draft_pool=UniversePlayer.DraftPool.DRAFTABLE,
                first_pro_year=1970,
                origin_draft_year=1970,
                origin_draft_round=None,
                origin_draft_pick=None,
                peak_age=28,
                development_rate=40,
                decline_rate=40,
                board_rank=None,
                trait_1=None,
                trait_2=None,
                trait_3=None,
            )

        foreign_college = College(
            release=self.release,
            slug='not-approved',
            name='Not Approved',
        )
        foreign_college.pk = max(college_ids) + 1
        foreign_class = DraftClass(
            release=self.release,
            profile=profile,
            season_year=1970,
            franchise_count=26,
            rounds=17,
            draftable_count=17 * 26,
            undrafted_multiplier=2,
            undrafted_count=52,
        )
        foreign_class.pk = draft_class.pk + 1
        foreign_profile = EraRuleProfile(
            release=self.release,
            first_year=1970,
            last_year=1973,
        )
        foreign_profile.pk = profile.pk + 1
        foreign_release = UniverseRelease(slug='other-release')
        foreign_release.pk = self.release.pk + 1
        cases = (
            ('college', lambda row: setattr(row, 'college', foreign_college), 'stored colleges'),
            ('class', lambda row: setattr(row, 'draft_class', foreign_class), 'stored class'),
            ('profile', lambda row: setattr(row, 'origin_profile', foreign_profile), 'origin profile'),
            ('release', lambda row: setattr(row, 'release', foreign_release), 'locked release'),
            ('origin franchise', lambda row: setattr(row, 'origin_franchise', franchise), 'origin franchise'),
        )
        for name, mutate, pattern in cases:
            with self.subTest(name=name):
                row = build()
                mutate(row)
                inserted = {'called': False}

                def reject_insert(*args, **kwargs):
                    inserted['called'] = True
                    raise AssertionError('Player bulk insertion ran after an untrusted relation.')

                with patch(
                    'universe.generation.prospects.UniversePlayer.objects.bulk_create',
                    reject_insert,
                ):
                    with self.assertRaisesRegex(ProspectGenerationError, pattern):
                        _insert_players([row], self.release, college_ids, classes_by_year)
                self.assertFalse(inserted['called'])
                self.assertFalse(UniversePlayer.objects.filter(universe_key='c' * 64).exists())

    def test_a_player_foreign_key_failure_rolls_back_the_writer(self):
        seen = {}

        def reject_insert(*args, **kwargs):
            seen['profiles'] = EraRuleProfile.objects.count()
            seen['classes'] = DraftClass.objects.count()
            raise IntegrityError('FOREIGN KEY constraint failed')

        with patch(
            'universe.generation.prospects.UniversePlayer.objects.bulk_create',
            reject_insert,
        ):
            with self.assertRaisesRegex(ProspectGenerationError, 'rolled back'):
                generate_prospects(self.league.id, SEED)
        self.assertEqual(seen['profiles'], 12)
        self.assertEqual(seen['classes'], 60)
        self.assertEqual(EraRuleProfile.objects.count(), 1)
        self.assertEqual(DraftClass.objects.count(), 0)
        self.assertEqual(UniversePlayer.objects.filter(cohort='draft').count(), 0)
        self.assertEqual(UniversePlayer.objects.filter(cohort='opening').count(), 1378)


class ProspectPrerequisiteTests(TestCase):
    def test_a_release_without_opening_players_writes_nothing(self):
        league, release = _prepare_release('prospect-bare', opening=False)
        with self.assertRaisesRegex(ProspectGenerationError, 'opening'):
            generate_prospects(league.id, SEED)
        self.assertEqual(UniversePlayer.objects.count(), 0)
        self.assertEqual(DraftClass.objects.count(), 0)
        self.assertEqual(EraRuleProfile.objects.filter(release=release).count(), 0)
        self.assertEqual(release.franchises.count(), 32)

    def test_a_release_without_the_schedule_writes_nothing(self):
        league = League(name='No Schedule', slug='no-schedule', tier=1, active=False)
        league.save()
        UniverseRelease(
            slug='no-schedule-release',
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
        with self.assertRaisesRegex(ProspectGenerationError, 'fg-founding-2'):
            generate_prospects(league.id, SEED)
        self.assertEqual(UniversePlayer.objects.count(), 0)
        self.assertEqual(DraftClass.objects.count(), 0)
        self.assertEqual(EraRuleProfile.objects.count(), 0)
