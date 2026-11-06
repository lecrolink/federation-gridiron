import hashlib
import json
import socket

from django.conf import settings
from django.db import connection
from django.test import SimpleTestCase, TestCase

from leagues.models import League, Team
from timelines.models import Timeline
from universe.fingerprints import (
    CATALOG_RECORD_KEYS,
    CATALOG_SCHEME,
    VALIDATION_RECORD_KEYS,
    VALIDATION_SCHEME,
    FingerprintSchemaError,
    canonical_bytes,
    check_v1_model_coverage,
    release_digests,
    release_documents,
    validate_record,
)
from universe.generation.opening_players import generate_opening_players, y1970_values
from universe.generation.player_rating_profiles import generate_player_ratings
from universe.generation.prospects import APPROVED_CAP_LIMIT, generate_prospects, later_profile_values
from universe.generation.schedule import generate_schedule_release
from universe.models import (
    FranchiseHistoryEvent,
    FranchiseSeasonMembership,
    HistoricalStanding,
    ProvisionalFranchiseName,
    UniverseFranchise,
    UniverseRelease,
)
from universe.people import (
    College,
    CoachRatingProfile,
    GMRatingProfile,
    PlayerRatingProfile,
    UniverseAward,
    UniverseCoach,
    UniverseCollegeSeason,
    UniverseGM,
    UniversePlayer,
    UniverseProSeason,
    UniverseProStint,
    UniverseStaffIdentity,
    UniverseStaffPost,
)
from universe.publication import evaluate_release_publication
from universe.rules import (
    DraftClass,
    EraAllowedScheme,
    EraCollegeWeight,
    EraRuleProfile,
    ExpansionClub,
    ExpansionEvent,
    OpeningAssignment,
    OpeningContract,
    OpeningStaffAssignment,
)


SEED = 1970
EMPTY_CATALOG = (
    '{"identity":{"expansion_schedule_version":3,"first_season_year":1970,'
    '"founding_franchise_count":26,"generator_version":"fg-founding-2",'
    '"last_season_year":2029,"root_league_slug":"vector-league","season_count":60,'
    '"seed":1970,"slug":"fg-1970-1"},"records":{"expansion_clubs":[],'
    '"expansion_events":[],"franchises":[],"memberships":[]},'
    '"scheme":"catalog-sha256-v1"}\n'
)

_ORIGINAL_CONNECT = socket.socket.connect


def _blocked_connect(self, address):
    raise AssertionError(f'Outbound connections are blocked: {address}')


def setUpModule():
    socket.socket.connect = _blocked_connect


def tearDownModule():
    socket.socket.connect = _ORIGINAL_CONNECT


def _identity(slug='fg-1970-1', league='vector-league'):
    return {
        'slug': slug,
        'seed': SEED,
        'generator_version': 'fg-founding-2',
        'founding_franchise_count': 26,
        'expansion_schedule_version': 3,
        'first_season_year': 1970,
        'season_count': 60,
        'last_season_year': 2029,
        'root_league_slug': league,
    }


def _empty_records(keys):
    return {key: [] for key in keys}


def _release(league, slug):
    return UniverseRelease.objects.create(
        slug=slug,
        root_league=league,
        generator_version='fg-founding-2',
        seed=SEED,
        founding_franchise_count=26,
        expansion_schedule_version=3,
        first_season_year=1970,
        season_count=60,
        last_season_year=2029,
        status=UniverseRelease.Status.DRAFT,
    )


def _ratings(profile, components):
    for component in components:
        setattr(profile, f'initial_{component}', 10)
        setattr(profile, f'potential_{component}', 20)


def _stats(row):
    for name in (
        'games', 'starts', 'offensive_snaps', 'defensive_snaps',
        'special_teams_snaps', 'penalties', 'penalty_yards', 'fumbles',
        'fumbles_lost', 'pass_attempts', 'completions', 'pass_yards',
        'pass_touchdowns', 'interceptions_thrown', 'sacks_taken', 'sack_yards',
        'rush_attempts', 'rush_yards', 'rush_touchdowns', 'targets',
        'receptions', 'receiving_yards', 'receiving_touchdowns', 'drops',
        'run_block_snaps', 'pass_block_snaps', 'tackles', 'tackles_for_loss',
        'sacks', 'quarterback_hits', 'forced_fumbles', 'fumble_recoveries',
        'passes_defended', 'defensive_touchdowns', 'coverage_snaps',
        'targets_in_coverage', 'completions_allowed', 'interceptions',
        'yards_allowed', 'sacks_allowed', 'fg_made', 'fg_attempts', 'fg_long',
        'xp_made', 'xp_attempts', 'kickoff_touchbacks', 'punts', 'punt_yards',
        'punts_inside_20', 'punt_touchbacks', 'punt_long',
    ):
        setattr(row, name, 0)


class SqliteSettingsTests(SimpleTestCase):
    def test_settings_do_not_name_the_remote_database(self):
        database = settings.DATABASES['default']
        self.assertEqual(database['ENGINE'], 'django.db.backends.sqlite3')
        self.assertIn('memory', database['NAME'])
        self.assertNotIn('mysql', database['ENGINE'])
        self.assertNotIn('pythonanywhere', str(settings.DATABASES))
        self.assertFalse(database.get('HOST'))
        self.assertEqual(connection.vendor, 'sqlite')


class CanonicalTests(SimpleTestCase):
    def test_record_group_inventory_matches_the_models(self):
        check_v1_model_coverage()
        self.assertEqual(len(VALIDATION_RECORD_KEYS), 25)
        self.assertIn('staff_identities', VALIDATION_RECORD_KEYS)
        self.assertEqual(
            tuple(sorted(VALIDATION_RECORD_KEYS)),
            VALIDATION_RECORD_KEYS,
        )
        for card in later_profile_values():
            if card['cap_status'] == 'on':
                self.assertEqual(card['cap_limit'], APPROVED_CAP_LIMIT)
            else:
                self.assertIsNone(card['cap_limit'])
            self.assertIsNone(card['minimum_salary'])
            self.assertIsNone(card['rookie_slot_scale'])
        self.assertIsNone(y1970_values()['cap_limit'])

    def test_empty_catalog_matches_the_frozen_vector(self):
        payload = canonical_bytes(
            CATALOG_SCHEME,
            _identity(),
            _empty_records(CATALOG_RECORD_KEYS),
        )
        self.assertEqual(payload.decode(), EMPTY_CATALOG)
        self.assertEqual(
            hashlib.sha256(payload).hexdigest(),
            hashlib.sha256(EMPTY_CATALOG.encode()).hexdigest(),
        )

    def test_empty_validation_uses_the_25_groups(self):
        records = _empty_records(VALIDATION_RECORD_KEYS)
        payload = canonical_bytes(VALIDATION_SCHEME, _identity(), records)
        document = json.loads(payload.decode())
        self.assertEqual(list(document['records']), list(VALIDATION_RECORD_KEYS))
        self.assertTrue(all(value == [] for value in document['records'].values()))
        self.assertTrue(payload.endswith(b'\n'))
        self.assertNotIn(b' ', payload)

    def test_unicode_is_utf8_and_object_keys_are_sorted(self):
        history = {
            'placeholder_slug': 'founding-01',
            'season_year': 1970,
            'kind': 'rename',
            'previous_name': 'Éagles',
            'new_name': 'Hawks',
            'city': '',
            'notes': '',
        }
        payload = canonical_bytes(
            VALIDATION_SCHEME,
            _identity(),
            {**_empty_records(VALIDATION_RECORD_KEYS), 'history_events': [history]},
        )
        self.assertIn('Éagles'.encode(), payload)
        self.assertNotIn(b'\\u00c9', payload)
        self.assertTrue(payload.endswith(b'\n'))

    def test_null_empty_string_boolean_and_float_rules(self):
        standing = {
            'placeholder_slug': 'founding-01',
            'season_year': 1969,
            'wins': 1,
            'losses': 0,
            'ties': 0,
            'draft_order': None,
        }
        numbered = dict(standing, draft_order=0)
        self.assertNotEqual(
            canonical_bytes(
                VALIDATION_SCHEME,
                _identity(),
                {**_empty_records(VALIDATION_RECORD_KEYS), 'historical_standings': [standing]},
            ),
            canonical_bytes(
                VALIDATION_SCHEME,
                _identity(),
                {**_empty_records(VALIDATION_RECORD_KEYS), 'historical_standings': [numbered]},
            ),
        )
        history = {
            'placeholder_slug': 'founding-01',
            'season_year': 1970,
            'kind': 'rename',
            'previous_name': 'Old',
            'new_name': 'New',
            'city': '',
            'notes': '',
        }
        with self.assertRaises(FingerprintSchemaError):
            validate_record('history_events', dict(history, city=None))
        membership = {
            'placeholder_slug': 'founding-01',
            'season_year': 1970,
            'conference_slug': 'east',
            'division_slug': 'north',
            'slot_order': 1,
            'owner_selectable': True,
        }
        with self.assertRaises(FingerprintSchemaError):
            validate_record('memberships', dict(membership, owner_selectable=1))
        with self.assertRaises(FingerprintSchemaError):
            validate_record('college_weights', {
                'profile_key': 'y1970',
                'college_slug': 'state',
                'weight': 1.5,
            })
        with self.assertRaises(FingerprintSchemaError):
            validate_record('staff_identities', {
                'universe_key': 'g-1',
                'name': 'A',
                'birth_year': True,
            })

    def test_order_and_duplicates_do_not_use_insertion_order(self):
        first = {
            'placeholder_slug': 'a',
            'origin': 'founding',
            'first_active_season': 1970,
            'final_active_season': None,
            'predecessor_slug': None,
            'catalog_team_slug': None,
        }
        second = dict(first, placeholder_slug='b')
        forward = canonical_bytes(
            CATALOG_SCHEME,
            _identity(),
            {**_empty_records(CATALOG_RECORD_KEYS), 'franchises': [second, first]},
        )
        backward = canonical_bytes(
            CATALOG_SCHEME,
            _identity(),
            {**_empty_records(CATALOG_RECORD_KEYS), 'franchises': [first, second]},
        )
        self.assertEqual(forward, backward)
        duplicated = canonical_bytes(
            CATALOG_SCHEME,
            _identity(),
            {**_empty_records(CATALOG_RECORD_KEYS), 'franchises': [first, first]},
        )
        self.assertEqual(duplicated.decode().count('"placeholder_slug":"a"'), 2)
        with self.assertRaises(FingerprintSchemaError):
            canonical_bytes(
                VALIDATION_SCHEME,
                _identity(),
                _empty_records(tuple(key for key in VALIDATION_RECORD_KEYS if key != 'staff_identities')),
            )


class ReleaseFingerprintTests(TestCase):
    def test_stored_rows_ignore_ids_timeline_rows_and_external_names(self):
        first = _build_world('world-league', 'fg-world')
        first_franchise_pk = first['franchise'].pk
        first_player_pk = first['player'].pk
        left = release_digests(first['release'])
        _discard(first)
        second = _build_world('world-league', 'fg-world')
        self.assertNotEqual(second['franchise'].pk, first_franchise_pk)
        self.assertNotEqual(second['player'].pk, first_player_pk)
        self.assertEqual(release_digests(second['release']), left)
        Timeline.objects.bulk_create([
            Timeline(
                release=second['release'],
                name='Campaign',
                start_season_year=1970,
                current_season_year=1970,
                season_index=1,
                phase=Timeline.Phase.CHOOSE_FRANCHISE,
                status=Timeline.Status.ACTIVE,
            ),
        ])
        League.objects.filter(pk=second['league'].pk).update(name='Renamed League')
        Team.objects.filter(pk=second['team'].pk).update(name='Renamed Team', city='Elsewhere')
        self.assertEqual(release_digests(second['release']), left)
        Team.objects.filter(pk=second['team'].pk).update(slug='other-team')
        catalog, validation = release_digests(second['release'])
        self.assertNotEqual(catalog, left[0])
        self.assertEqual(validation, left[1])

    def test_each_included_group_changes_only_its_digest(self):
        world = _build_world('mutate-league', 'fg-mutate')
        release = world['release']
        baseline = release_digests(release)
        mutations = (
            ('catalog', lambda: UniverseFranchise.objects.filter(
                pk=world['franchise'].pk,
            ).update(origin='expansion')),
            ('catalog', lambda: FranchiseSeasonMembership.objects.filter(
                pk=world['membership'].pk,
            ).update(conference_slug='west')),
            ('catalog', lambda: ExpansionEvent.objects.filter(
                pk=world['event'].pk,
            ).update(taxi_capacity=7)),
            ('catalog', lambda: ExpansionClub.objects.filter(
                pk=world['club'].pk,
            ).update(selection_order=2)),
            ('validation', lambda: UniverseFranchise.objects.filter(
                pk=world['franchise'].pk,
            ).update(display_name='Other Name')),
            ('validation', lambda: ProvisionalFranchiseName.objects.filter(
                pk=world['provisional'].pk,
            ).update(city='Other City')),
            ('validation', lambda: FranchiseHistoryEvent.objects.filter(
                pk=world['history'].pk,
            ).update(notes='edited')),
            ('validation', lambda: HistoricalStanding.objects.filter(
                pk=world['standing'].pk,
            ).update(wins=3)),
            ('validation', lambda: College.objects.filter(
                pk=world['college'].pk,
            ).update(name='Other College')),
            ('validation', lambda: EraCollegeWeight.objects.filter(
                pk=world['weight'].pk,
            ).update(weight=2)),
            ('validation', lambda: EraAllowedScheme.objects.filter(
                pk=world['scheme'].pk,
            ).update(scheme='west_coast')),
            ('validation', lambda: EraRuleProfile.objects.filter(
                pk=world['profile'].pk,
            ).update(schedule_length=13)),
            ('validation', lambda: DraftClass.objects.filter(
                pk=world['draft_class'].pk,
            ).update(franchise_count=2, draftable_count=34, undrafted_count=4)),
            ('validation', lambda: UniversePlayer.objects.filter(
                pk=world['player'].pk,
            ).update(name='Other Player')),
            ('validation', lambda: PlayerRatingProfile.objects.filter(
                pk=world['rating'].pk,
            ).update(initial_speed=11)),
            ('validation', lambda: OpeningAssignment.objects.filter(
                pk=world['assignment'].pk,
            ).update(depth=2)),
            ('validation', lambda: OpeningContract.objects.filter(
                pk=world['contract'].pk,
            ).update(salary_per_year=250)),
            ('validation', lambda: UniverseStaffIdentity.objects.filter(
                pk=world['gm_identity'].pk,
            ).update(name='Other GM')),
            ('validation', lambda: UniverseGM.objects.filter(
                pk=world['gm'].pk,
            ).update(former_role='scout')),
            ('validation', lambda: GMRatingProfile.objects.filter(
                pk=world['gm_rating'].pk,
            ).update(initial_reputation=11)),
            ('validation', lambda: UniverseCoach.objects.filter(
                pk=world['coach'].pk,
            ).update(specialty='offense')),
            ('validation', lambda: CoachRatingProfile.objects.filter(
                pk=world['coach_rating'].pk,
            ).update(initial_reputation=12)),
            ('validation', lambda: UniverseStaffPost.objects.filter(
                pk=world['post'].pk,
            ).update(wins=4)),
            ('validation', lambda: UniverseCollegeSeason.objects.filter(
                pk=world['college_season'].pk,
            ).update(class_standing='junior')),
            ('validation', lambda: UniverseProStint.objects.filter(
                pk=world['stint'].pk,
            ).update(role='backup')),
            ('validation', lambda: UniverseProSeason.objects.filter(
                pk=world['pro_season'].pk,
            ).update(games=1)),
            ('validation', lambda: UniverseAward.objects.filter(
                pk=world['award'].pk,
            ).update(kind='opoy')),
            ('validation', lambda: ExpansionEvent.objects.filter(
                pk=world['event'].pk,
            ).update(expansion_draft_order_rule='Edited rule.')),
        )
        self.assertEqual(
            {side for side, _mutation in mutations},
            {'catalog', 'validation'},
        )
        self.assertEqual(len(mutations), 4 + 24)
        before_groups = _validation_groups(release)
        OpeningStaffAssignment.objects.filter(pk=world['gm_seat'].pk).update(
            gm_id=world['spare_gm'].pk,
        )
        catalog, validation = release_digests(release)
        after_groups = _validation_groups(release)
        self.assertEqual(catalog, baseline[0])
        self.assertNotEqual(validation, baseline[1])
        self.assertNotEqual(after_groups['opening_staff'], before_groups['opening_staff'])
        for key, rows in before_groups.items():
            if key == 'opening_staff':
                continue
            self.assertEqual(after_groups[key], rows)
        self.assertEqual(world['gm'].universe_key, f'g-{release.slug}-1970-001')
        world['gm'].refresh_from_db()
        self.assertEqual(world['gm'].universe_key, f'g-{release.slug}-1970-001')
        self.assertEqual(world['gm'].former_role, UniverseGM.FormerRole.PLAYER)
        self.assertEqual(world['post'].wins, 0)
        world['post'].refresh_from_db()
        self.assertEqual(world['post'].wins, 0)
        _restore(world)
        for side, mutate in mutations:
            mutate()
            catalog, validation = release_digests(release)
            if side == 'catalog':
                self.assertNotEqual(catalog, baseline[0])
                self.assertEqual(validation, baseline[1])
            else:
                self.assertEqual(catalog, baseline[0])
                self.assertNotEqual(validation, baseline[1])
            _restore(world)


class GeneratedReleaseTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.league = League.objects.create(
            name='Generated League',
            slug='generated-league',
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

    def test_generated_release_hashes_match_test_supplied_digests(self):
        self.assertEqual(UniversePlayer.objects.filter(release=self.release).count(), 21155)
        self.assertEqual(
            EraRuleProfile.objects.filter(
                release=self.release,
                cap_status='on',
                cap_limit=APPROVED_CAP_LIMIT,
            ).count(),
            7,
        )
        self.assertFalse(
            EraRuleProfile.objects.filter(
                release=self.release,
                cap_status='off',
                cap_limit__isnull=False,
            ).exists()
        )
        statements = []

        def wrap(execute, sql, params, many, context):
            statements.append(sql)
            return execute(sql, params, many, context)

        connection.execute_wrappers.append(wrap)
        try:
            catalog, validation = release_digests(self.release)
        finally:
            connection.execute_wrappers.remove(wrap)
        self.assertLess(len(statements), 40)
        self.assertFalse(any(
            sql.lstrip().split(None, 1)[0].upper() in ('INSERT', 'UPDATE', 'DELETE', 'REPLACE')
            for sql in statements
        ))
        self.assertEqual(len(catalog), 64)
        self.assertEqual(catalog, catalog.lower())
        self.release.catalog_fingerprint = catalog
        self.release.validation_fingerprint = validation
        self.release.save()
        report = evaluate_release_publication(self.release)
        self.assertEqual(report.cap_blockers, ())
        self.assertEqual(report.fingerprint_verification, 'match')
        self.assertTrue(report.catalog_digest_match)
        self.assertTrue(report.validation_digest_match)
        self.assertTrue(report.cap_and_fingerprint_checks_pass)
        self.assertEqual(report.horizon_status, 'pass')
        self.assertEqual(report.expansion_status, 'pass')
        self.assertEqual(report.membership_status, 'pass')
        self.assertEqual(report.quota_status, 'pass')
        self.assertEqual(report.population_status, 'pass')
        self.assertEqual(report.staff_signature_status, 'pass')
        self.assertTrue(report.publishable)
        self.assertEqual(report.catalog_digest, catalog)
        self.assertEqual(report.validation_digest, validation)
        self.release.refresh_from_db()
        self.assertEqual(self.release.status, UniverseRelease.Status.DRAFT)
        self.assertIsNone(self.release.published_at)
        self.assertEqual(self.release.catalog_fingerprint, catalog)
        self.release.catalog_fingerprint = 'smoke-catalog-fingerprint'
        self.release.save(update_fields=['catalog_fingerprint'])
        mismatched = evaluate_release_publication(self.release)
        self.assertEqual(mismatched.fingerprint_verification, 'mismatch')
        self.assertFalse(mismatched.publishable)
        self.assertEqual(
            mismatched.unverified_placeholder_fields,
            ('catalog_fingerprint',),
        )
        self.release.refresh_from_db()
        self.assertEqual(self.release.catalog_fingerprint, 'smoke-catalog-fingerprint')
        self.assertEqual(self.release.status, UniverseRelease.Status.DRAFT)


def _discard(world):
    """Remove one fixture so a second copy can reuse its natural keys."""
    release = world['release']
    UniverseAward.objects.filter(release=release).delete()
    UniverseStaffPost.objects.filter(pk=world['post'].pk).delete()
    OpeningContract.objects.filter(pk=world['contract'].pk).delete()
    OpeningAssignment.objects.filter(release=release).delete()
    OpeningStaffAssignment.objects.filter(release=release).delete()
    ExpansionClub.objects.filter(pk=world['club'].pk).delete()
    UniverseProSeason.objects.filter(player__release=release).delete()
    UniverseProStint.objects.filter(player__release=release).delete()
    UniverseCollegeSeason.objects.filter(player__release=release).delete()
    PlayerRatingProfile.objects.filter(player__release=release).delete()
    CoachRatingProfile.objects.filter(coach__release=release).delete()
    GMRatingProfile.objects.filter(gm__release=release).delete()
    UniversePlayer.objects.filter(release=release).delete()
    DraftClass.objects.filter(release=release).delete()
    EraCollegeWeight.objects.filter(profile__release=release).delete()
    EraAllowedScheme.objects.filter(profile__release=release).delete()
    UniverseGM.objects.filter(release=release).delete()
    UniverseCoach.objects.filter(release=release).delete()
    UniverseStaffIdentity.objects.filter(release=release).delete()
    HistoricalStanding.objects.filter(franchise__release=release).delete()
    FranchiseHistoryEvent.objects.filter(franchise__release=release).delete()
    FranchiseSeasonMembership.objects.filter(franchise__release=release).delete()
    ExpansionEvent.objects.filter(release=release).delete()
    ProvisionalFranchiseName.objects.filter(release=release).delete()
    UniverseFranchise.objects.filter(release=release).delete()
    College.objects.filter(release=release).delete()
    EraRuleProfile.objects.filter(release=release).delete()
    release.delete()
    world['league'].delete()


def _build_world(league_slug, release_slug):
    from universe.people import COACH_ABILITIES, GM_ABILITIES, PLAYER_COMPONENTS

    league = League.objects.create(name='World', slug=league_slug, tier=1, active=True)
    team = Team.objects.create(league=league, name='Catalog Team', slug='catalog-team')
    release = _release(league, release_slug)
    franchise = UniverseFranchise.objects.create(
        release=release,
        placeholder_slug='founding-01',
        display_name='Founding One',
        origin=UniverseFranchise.Origin.FOUNDING,
        first_active_season=1970,
        catalog_team=team,
    )
    expansion = UniverseFranchise.objects.create(
        release=release,
        placeholder_slug='expansion-01',
        display_name='Expansion One',
        origin=UniverseFranchise.Origin.EXPANSION,
        first_active_season=1976,
    )
    membership = FranchiseSeasonMembership.objects.create(
        franchise=franchise,
        season_year=1970,
        conference_slug='east',
        division_slug='north',
        slot_order=1,
    )
    provisional = ProvisionalFranchiseName.objects.create(
        release=release,
        placeholder_slug='founding-01',
        city='Harbor',
        city_status=ProvisionalFranchiseName.Status.PROVISIONAL,
        nickname='Hawks',
        nickname_status=ProvisionalFranchiseName.Status.PROVISIONAL,
    )
    history = FranchiseHistoryEvent.objects.create(
        franchise=franchise,
        season_year=1970,
        kind=FranchiseHistoryEvent.Kind.RENAME,
        previous_name='Old Hawks',
        new_name='Hawks',
        city='',
        notes='',
    )
    standing = HistoricalStanding.objects.create(
        franchise=franchise,
        season_year=1969,
        wins=10,
        losses=4,
        ties=0,
        draft_order=1,
    )
    profile = EraRuleProfile(release=release, **y1970_values())
    profile.save()
    college = College.objects.create(release=release, slug='state', name='State')
    weight = EraCollegeWeight.objects.create(profile=profile, college=college, weight=1)
    scheme = EraAllowedScheme.objects.create(
        profile=profile,
        side='offense',
        scheme='power_run',
    )
    draft_class = DraftClass.objects.create(
        release=release,
        profile=profile,
        season_year=1970,
        franchise_count=1,
        rounds=17,
        draftable_count=17,
        undrafted_multiplier=2,
        undrafted_count=2,
    )
    event = ExpansionEvent.objects.create(
        release=release,
        season_year=1976,
        existing_club_count=1,
        new_club_count=1,
        active_target=3,
        taxi_capacity=8,
        max_losses_per_existing=1,
        min_losses_per_existing=0,
        clubs_at_max_loss=1,
        clubs_at_min_loss=0,
        veteran_picks_per_new=1,
        veteran_picks_total=1,
        rookie_picks_per_new=1,
        undrafted_multiplier=1,
        undrafted_pool=2,
        undrafted_reserved_per_new=1,
        expansion_draft_order_rule='Snake.',
        roster_building_rule='Veterans.',
        free_agent_rule='Optional.',
        rookie_draft_slot_rule='End of round.',
        gm_hiring_rule='Pool.',
        hc_hiring_rule='Pool.',
    )
    club = ExpansionClub.objects.create(event=event, franchise=expansion, selection_order=1)
    player = UniversePlayer.objects.create(
        release=release,
        college=college,
        origin_profile=profile,
        universe_key=f'p-{release_slug}-opening-00001',
        name='Ada Player',
        birth_year=1945,
        birth_day=10,
        position='QB',
        handedness=UniversePlayer.Handedness.RIGHT,
        height_in=72,
        weight_lb=210,
        cohort=UniversePlayer.Cohort.OPENING,
        first_pro_year=1965,
        peak_age=28,
        development_rate=40,
        decline_rate=30,
    )
    rating = PlayerRatingProfile(player=player)
    _ratings(rating, PLAYER_COMPONENTS)
    rating.save()
    college_season = UniverseCollegeSeason(
        player=player,
        season_year=1964,
        class_standing=UniverseCollegeSeason.ClassStanding.SENIOR,
    )
    _stats(college_season)
    college_season.save()
    stint = UniverseProStint.objects.create(
        player=player,
        franchise=franchise,
        start_year=1965,
        end_year=1968,
        role=UniverseProStint.Role.STARTER,
    )
    pro_season = UniverseProSeason(
        player=player,
        franchise=franchise,
        season_year=1968,
    )
    _stats(pro_season)
    pro_season.save()
    assignment = OpeningAssignment.objects.create(
        release=release,
        player=player,
        franchise=franchise,
        role='starter',
        depth=1,
    )
    contract = OpeningContract.objects.create(
        assignment=assignment,
        years=2,
        salary_per_year=100,
        guaranteed=0,
        signing_bonus=0,
        start_year=1969,
        end_year=1970,
    )
    gm_identity = UniverseStaffIdentity.objects.create(
        release=release,
        universe_key=f'g-{release_slug}-1970-001',
        name='Gina Manager',
        birth_year=1940,
    )
    coach_identity = UniverseStaffIdentity.objects.create(
        release=release,
        universe_key=f'c-{release_slug}-1970-001',
        name='Cory Coach',
        birth_year=1940,
    )
    gm = UniverseGM.objects.create(
        release=release,
        identity=gm_identity,
        universe_key=gm_identity.universe_key,
        name=gm_identity.name,
        birth_year=1940,
        debut_year=1970,
        former_role=UniverseGM.FormerRole.PLAYER,
        positional_priority_1='quarterback',
        strength_1='quarterback_eye',
        strength_2='lineman_eye',
        weakness='loyalty',
        trait_1='patient',
        trait_2='impatient',
    )
    gm_rating = GMRatingProfile(gm=gm, initial_reputation=10)
    _ratings(gm_rating, GM_ABILITIES)
    gm_rating.save()
    spare_identity = UniverseStaffIdentity.objects.create(
        release=release,
        universe_key=f'g-{release_slug}-1970-002',
        name='Gale Spare',
        birth_year=1941,
    )
    spare_gm = UniverseGM.objects.create(
        release=release,
        identity=spare_identity,
        universe_key=spare_identity.universe_key,
        name=spare_identity.name,
        birth_year=1941,
        debut_year=1970,
        former_role=UniverseGM.FormerRole.SCOUT,
        positional_priority_1='skill',
        strength_1='finds_speed',
        strength_2='finds_size',
        weakness='character',
        trait_1='secretive',
        trait_2='public',
    )
    coach = UniverseCoach.objects.create(
        release=release,
        identity=coach_identity,
        universe_key=coach_identity.universe_key,
        name=coach_identity.name,
        birth_year=1940,
        debut_year=1970,
        specialty=UniverseCoach.Specialty.BOTH,
        former_role=UniverseCoach.FormerRole.PLAYER,
        preferred_offense='power_run',
        preferred_defense='balanced',
        strength_1='run_game',
        strength_2='passing_game',
        weakness='defense',
        trait_1='players_coach',
        trait_2='even_keel',
    )
    coach_rating = CoachRatingProfile(coach=coach, initial_reputation=10)
    _ratings(coach_rating, COACH_ABILITIES)
    coach_rating.save()
    gm_seat = OpeningStaffAssignment.objects.create(
        release=release,
        franchise=franchise,
        gm=gm,
        role='gm',
    )
    OpeningStaffAssignment.objects.create(
        release=release,
        franchise=franchise,
        coach=coach,
        role='head_coach',
    )
    post = UniverseStaffPost.objects.create(
        gm=gm,
        franchise=franchise,
        role=UniverseStaffPost.Role.GM,
        start_year=1968,
        end_year=1969,
        ended_by=UniverseStaffPost.EndedBy.RESIGNED,
    )
    award = UniverseAward.objects.create(
        release=release,
        franchise=franchise,
        player=player,
        season_year=1969,
        kind=UniverseAward.Kind.MVP,
        slot='',
    )
    return {
        'league': league,
        'team': team,
        'release': release,
        'franchise': franchise,
        'membership': membership,
        'provisional': provisional,
        'history': history,
        'standing': standing,
        'college': college,
        'weight': weight,
        'scheme': scheme,
        'profile': profile,
        'draft_class': draft_class,
        'event': event,
        'club': club,
        'player': player,
        'rating': rating,
        'assignment': assignment,
        'contract': contract,
        'gm_seat': gm_seat,
        'gm_identity': gm_identity,
        'gm': gm,
        'spare_gm': spare_gm,
        'gm_rating': gm_rating,
        'coach': coach,
        'coach_rating': coach_rating,
        'post': post,
        'college_season': college_season,
        'stint': stint,
        'pro_season': pro_season,
        'award': award,
    }


def _validation_groups(release):
    _catalog, validation = release_documents(release)
    return json.loads(validation.decode())['records']


def _restore(world):
    world['franchise'].refresh_from_db()
    UniverseFranchise.objects.filter(pk=world['franchise'].pk).update(
        origin=UniverseFranchise.Origin.FOUNDING,
        display_name='Founding One',
    )
    FranchiseSeasonMembership.objects.filter(pk=world['membership'].pk).update(
        conference_slug='east',
    )
    ExpansionEvent.objects.filter(pk=world['event'].pk).update(
        taxi_capacity=8,
        expansion_draft_order_rule='Snake.',
    )
    ExpansionClub.objects.filter(pk=world['club'].pk).update(selection_order=1)
    ProvisionalFranchiseName.objects.filter(pk=world['provisional'].pk).update(city='Harbor')
    FranchiseHistoryEvent.objects.filter(pk=world['history'].pk).update(notes='')
    HistoricalStanding.objects.filter(pk=world['standing'].pk).update(wins=10)
    College.objects.filter(pk=world['college'].pk).update(name='State')
    EraCollegeWeight.objects.filter(pk=world['weight'].pk).update(weight=1)
    EraAllowedScheme.objects.filter(pk=world['scheme'].pk).update(scheme='power_run')
    EraRuleProfile.objects.filter(pk=world['profile'].pk).update(schedule_length=14)
    DraftClass.objects.filter(pk=world['draft_class'].pk).update(
        franchise_count=1,
        draftable_count=17,
        undrafted_count=2,
    )
    UniversePlayer.objects.filter(pk=world['player'].pk).update(name='Ada Player')
    PlayerRatingProfile.objects.filter(pk=world['rating'].pk).update(initial_speed=10)
    OpeningAssignment.objects.filter(pk=world['assignment'].pk).update(depth=1)
    OpeningContract.objects.filter(pk=world['contract'].pk).update(salary_per_year=100)
    OpeningStaffAssignment.objects.filter(pk=world['gm_seat'].pk).update(
        gm_id=world['gm'].pk,
    )
    UniverseStaffIdentity.objects.filter(pk=world['gm_identity'].pk).update(name='Gina Manager')
    UniverseGM.objects.filter(pk=world['gm'].pk).update(
        former_role=UniverseGM.FormerRole.PLAYER,
    )
    GMRatingProfile.objects.filter(pk=world['gm_rating'].pk).update(initial_reputation=10)
    UniverseCoach.objects.filter(pk=world['coach'].pk).update(
        specialty=UniverseCoach.Specialty.BOTH,
    )
    CoachRatingProfile.objects.filter(pk=world['coach_rating'].pk).update(
        initial_reputation=10,
    )
    UniverseStaffPost.objects.filter(pk=world['post'].pk).update(wins=0)
    UniverseCollegeSeason.objects.filter(pk=world['college_season'].pk).update(
        class_standing=UniverseCollegeSeason.ClassStanding.SENIOR,
    )
    UniverseProStint.objects.filter(pk=world['stint'].pk).update(
        role=UniverseProStint.Role.STARTER,
    )
    UniverseProSeason.objects.filter(pk=world['pro_season'].pk).update(games=0)
    UniverseAward.objects.filter(pk=world['award'].pk).update(kind=UniverseAward.Kind.MVP)
