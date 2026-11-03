import hashlib
import hmac
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from leagues.models import Conference, Division, League, Team
from timelines.models import Timeline
from universe.generation.founding import (
    AGE_MAX,
    AGE_MIN,
    COACH_FORMER_ROLES,
    EMPLOYED_HEAD_COACH_AGE_MIN,
    FIRST_SEASON_YEAR,
    GENERATOR_VERSION,
    GM_FORMER_ROLES,
    OFFENSE_AT_FOUNDING,
    OPENING_SEATS,
    POOL_SIZE,
    ROLE_GM,
    ROLE_HEAD_COACH,
    FoundingGenerationError,
    birth_year_for,
    build_staff,
    choose_name,
    founding_franchises,
    generate_founding_release,
    hmac_int,
    key_coordinate,
    universe_key,
)
from universe.generation.names import GIVEN_NAMES, SURNAMES
from universe.models import FranchiseSeasonMembership, UniverseFranchise, UniverseRelease
from universe.people import (
    DEFENSE_SCHEMES,
    GM_TAGS,
    GM_TRAITS,
    UniverseCoach,
    UniverseGM,
    UniverseProStint,
    UniverseStaffIdentity,
    UniverseStaffPost,
)
from universe.rules import OpeningStaffAssignment

SEED = 1970
OTHER_SEED = 1971
RELEASE_SLUG = 'confirmed-1970'


class HmacRuleTests(SimpleTestCase):
    def test_name_lists_keep_the_approved_order(self):
        self.assertEqual(len(GIVEN_NAMES), 44)
        self.assertEqual(len(SURNAMES), 66)
        self.assertEqual(GIVEN_NAMES[0], 'Arthur')
        self.assertEqual(GIVEN_NAMES[-1], 'Willis')
        self.assertEqual(SURNAMES[0], 'Adler')
        self.assertEqual(SURNAMES[-1], 'Yarbrough')
        self.assertEqual(len(set(GIVEN_NAMES)), 44)
        self.assertEqual(len(set(SURNAMES)), 66)

    def test_name_uses_the_first_eight_hmac_bytes(self):
        staff_key = universe_key(SEED, RELEASE_SLUG, ROLE_GM, 0)
        given_digest = hmac.new(
            b'1970',
            f'{staff_key}:given_name'.encode('utf-8'),
            hashlib.sha256,
        ).digest()
        surname_digest = hmac.new(
            b'1970',
            f'{staff_key}:surname'.encode('utf-8'),
            hashlib.sha256,
        ).digest()
        given = GIVEN_NAMES[int.from_bytes(given_digest[:8], 'big') % len(GIVEN_NAMES)]
        surname = SURNAMES[int.from_bytes(surname_digest[:8], 'big') % len(SURNAMES)]
        self.assertEqual(choose_name(SEED, staff_key), f'{given} {surname}')

    def test_key_is_the_prefixed_hmac_hex(self):
        coordinate = key_coordinate(RELEASE_SLUG, ROLE_GM, 0)
        digest = hmac.new(b'1970', coordinate.encode('utf-8'), hashlib.sha256).hexdigest()
        self.assertEqual(universe_key(SEED, RELEASE_SLUG, ROLE_GM, 0), 'g-' + digest[:62])
        coach = hmac.new(
            b'1970',
            key_coordinate(RELEASE_SLUG, ROLE_HEAD_COACH, 7).encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        self.assertEqual(
            universe_key(SEED, RELEASE_SLUG, ROLE_HEAD_COACH, 7),
            'c-' + coach[:62],
        )
        self.assertLessEqual(len(universe_key(SEED, RELEASE_SLUG, ROLE_GM, 99)), 64)

    def test_attributes_use_independent_coordinates(self):
        staff_key = universe_key(SEED, RELEASE_SLUG, ROLE_GM, 4)
        given_draw = hmac_int(SEED, f'{staff_key}:given_name')
        birth_draw = hmac_int(SEED, f'{staff_key}:birth_year')
        role_draw = hmac_int(SEED, f'{staff_key}:former_role')
        self.assertNotEqual(given_draw, birth_draw)
        self.assertNotEqual(given_draw, role_draw)
        self.assertEqual(
            choose_name(SEED, staff_key).split()[0],
            GIVEN_NAMES[given_draw % len(GIVEN_NAMES)],
        )
        self.assertEqual(
            birth_year_for(SEED, staff_key, ROLE_GM, 4, FIRST_SEASON_YEAR),
            FIRST_SEASON_YEAR - (AGE_MIN + birth_draw % (AGE_MAX - AGE_MIN + 1)),
        )
        record = next(
            row for row in build_staff(SEED, RELEASE_SLUG, FIRST_SEASON_YEAR)
            if row['universe_key'] == staff_key
        )
        self.assertEqual(record['name'], choose_name(SEED, staff_key))
        self.assertEqual(record['birth_year'], birth_year_for(
            SEED, staff_key, ROLE_GM, 4, FIRST_SEASON_YEAR,
        ))
        self.assertEqual(
            record['former_role'],
            GM_FORMER_ROLES[role_draw % len(GM_FORMER_ROLES)],
        )
        attempt = record['signature_attempt']
        strength_coordinate = f'{staff_key}:strength_1'
        if attempt:
            strength_coordinate = f'{strength_coordinate}:{attempt}'
        self.assertEqual(
            record['strength_1'],
            GM_TAGS[hmac_int(SEED, strength_coordinate) % len(GM_TAGS)],
        )

    def test_employed_head_coaches_use_the_older_half(self):
        staff = [
            row for row in build_staff(SEED, RELEASE_SLUG, FIRST_SEASON_YEAR)
            if row['role'] == ROLE_HEAD_COACH
        ]
        for row in staff[:OPENING_SEATS]:
            age = FIRST_SEASON_YEAR - row['birth_year']
            self.assertGreaterEqual(age, EMPLOYED_HEAD_COACH_AGE_MIN)
            self.assertLessEqual(age, AGE_MAX)
        unemployed_ages = [
            FIRST_SEASON_YEAR - row['birth_year'] for row in staff[OPENING_SEATS:]
        ]
        self.assertTrue(any(age < EMPLOYED_HEAD_COACH_AGE_MIN for age in unemployed_ages))
        self.assertTrue(all(AGE_MIN <= age <= AGE_MAX for age in unemployed_ages))

    def test_same_seed_rebuilds_the_same_pool(self):
        first = build_staff(SEED, RELEASE_SLUG, FIRST_SEASON_YEAR)
        second = build_staff(SEED, RELEASE_SLUG, FIRST_SEASON_YEAR)
        other = build_staff(OTHER_SEED, RELEASE_SLUG, FIRST_SEASON_YEAR)
        self.assertEqual(first, second)
        self.assertNotEqual(
            [row['universe_key'] for row in first],
            [row['universe_key'] for row in other],
        )
        for role in (ROLE_GM, ROLE_HEAD_COACH):
            signatures = [
                (
                    row['strength_1'],
                    row['strength_2'],
                    row['weakness'],
                    row['trait_1'],
                    row['trait_2'],
                )
                for row in first
                if row['role'] == role
            ]
            self.assertEqual(len(signatures), len(set(signatures)))


class FoundingReleaseTests(TestCase):
    def setUp(self):
        self.league = League(name='Test League', slug='test-league', tier=1, active=True)
        self.league.save()
        self.release = self.make_release()

    def make_release(self, **overrides):
        fields = {
            'slug': RELEASE_SLUG,
            'root_league': self.league,
            'generator_version': 'pending',
            'seed': 1,
            'founding_franchise_count': 24,
            'expansion_schedule_version': 1,
            'first_season_year': 1970,
            'season_count': 60,
            'last_season_year': 2029,
            'status': UniverseRelease.Status.DRAFT,
        }
        fields.update(overrides)
        release = UniverseRelease(**fields)
        release.save()
        return release

    def test_writes_the_documented_slice(self):
        self.assertEqual(generate_founding_release(self.league.id, SEED), 'created')
        self.release.refresh_from_db()
        self.assertEqual(self.release.generator_version, GENERATOR_VERSION)
        self.assertEqual(self.release.seed, SEED)
        self.assertEqual(self.release.status, UniverseRelease.Status.DRAFT)
        self.assertEqual(self.release.catalog_fingerprint, '')
        self.assertEqual(self.release.validation_fingerprint, '')
        self.assertIsNone(self.release.published_at)

        franchises = list(self.release.franchises.order_by('placeholder_slug'))
        self.assertEqual(
            [franchise.placeholder_slug for franchise in franchises],
            [f'founding-{number:02d}' for number in range(1, 25)],
        )
        for franchise in franchises:
            number = int(franchise.placeholder_slug.split('-')[1])
            self.assertEqual(franchise.display_name, f'Founding franchise {number:02d}')
            self.assertEqual(franchise.origin, UniverseFranchise.Origin.FOUNDING)
            self.assertEqual(franchise.first_active_season, 1970)
            self.assertIsNone(franchise.final_active_season)
            self.assertIsNone(franchise.catalog_team_id)
            self.assertIsNone(franchise.history_predecessor_id)

        expected_divisions = {
            'c1-d1': ['founding-01', 'founding-02', 'founding-03', 'founding-04'],
            'c1-d2': ['founding-05', 'founding-06', 'founding-07', 'founding-08'],
            'c1-d3': ['founding-09', 'founding-10', 'founding-11', 'founding-12'],
            'c2-d1': ['founding-13', 'founding-14', 'founding-15', 'founding-16'],
            'c2-d2': ['founding-17', 'founding-18', 'founding-19', 'founding-20'],
            'c2-d3': ['founding-21', 'founding-22', 'founding-23', 'founding-24'],
        }
        memberships = FranchiseSeasonMembership.objects.filter(franchise__release=self.release)
        self.assertEqual(memberships.count(), 24)
        self.assertFalse(memberships.exclude(season_year=1970).exists())
        for division, slugs in expected_divisions.items():
            conference = 'conference-1' if division.startswith('c1-') else 'conference-2'
            seated = list(
                memberships.filter(division_slug=division).order_by('slot_order').values_list(
                    'franchise__placeholder_slug',
                    'conference_slug',
                    'slot_order',
                    'owner_selectable',
                )
            )
            self.assertEqual(
                seated,
                [(slug, conference, slot, True) for slot, slug in enumerate(slugs, start=1)],
            )
        self.assertEqual(
            [(row['slug'], row['division_slug'], row['slot_order']) for row in founding_franchises(1970)],
            [
                (slug, division, slot)
                for division, slugs in expected_divisions.items()
                for slot, slug in enumerate(slugs, start=1)
            ],
        )

        gms = list(UniverseGM.objects.filter(release=self.release).order_by('universe_key'))
        coaches = list(UniverseCoach.objects.filter(release=self.release))
        self.assertEqual(len(gms), POOL_SIZE)
        self.assertEqual(len(coaches), POOL_SIZE)
        gm_identities = {gm.identity_id for gm in gms}
        coach_identities = {coach.identity_id for coach in coaches}
        self.assertEqual(len(gm_identities), POOL_SIZE)
        self.assertEqual(len(coach_identities), POOL_SIZE)
        self.assertTrue(gm_identities.isdisjoint(coach_identities))
        self.assertEqual(
            UniverseStaffIdentity.objects.filter(release=self.release).count(),
            POOL_SIZE * 2,
        )
        self.assertTrue(all(gm.universe_key.startswith('g-') for gm in gms))
        self.assertTrue(all(coach.universe_key.startswith('c-') for coach in coaches))
        for gm in gms:
            self.assertEqual(gm.universe_key, gm.identity.universe_key)
            age = 1970 - gm.birth_year
            self.assertGreaterEqual(age, AGE_MIN)
            self.assertLessEqual(age, AGE_MAX)
            self.assertNotEqual(gm.strength_1, gm.strength_2)
            self.assertNotEqual(gm.trait_1, gm.trait_2)
            self.assertNotIn(gm.weakness, (gm.strength_1, gm.strength_2))
            self.assertNotEqual(gm.positional_priority_1, gm.positional_priority_2)
            self.assertEqual(gm.name, gm.identity.name)
            self.assertEqual(gm.birth_year, gm.identity.birth_year)
            self.assertEqual(gm.debut_year, 1970)
        signatures = {
            (gm.strength_1, gm.strength_2, gm.weakness, gm.trait_1, gm.trait_2)
            for gm in gms
        }
        self.assertEqual(len(signatures), POOL_SIZE)
        self.assertTrue(signatures <= set(_tag_product(GM_TAGS, GM_TRAITS)))

        coach_signatures = set()
        for coach in coaches:
            age = 1970 - coach.birth_year
            self.assertGreaterEqual(age, AGE_MIN)
            self.assertLessEqual(age, AGE_MAX)
            self.assertEqual(coach.specialty, 'both')
            self.assertIn(coach.preferred_offense, OFFENSE_AT_FOUNDING)
            self.assertIn(coach.preferred_defense, DEFENSE_SCHEMES)
            self.assertIn(coach.former_role, COACH_FORMER_ROLES)
            self.assertNotEqual(coach.strength_1, coach.strength_2)
            self.assertNotEqual(coach.trait_1, coach.trait_2)
            self.assertNotIn(coach.weakness, (coach.strength_1, coach.strength_2))
            self.assertEqual(coach.identity.universe_key, coach.universe_key)
            self.assertEqual(coach.name, coach.identity.name)
            coach_signatures.add((
                coach.strength_1,
                coach.strength_2,
                coach.weakness,
                coach.trait_1,
                coach.trait_2,
            ))
        self.assertEqual(len(coach_signatures), POOL_SIZE)

        assignments = OpeningStaffAssignment.objects.filter(release=self.release)
        self.assertEqual(assignments.filter(role='gm').count(), OPENING_SEATS)
        self.assertEqual(assignments.filter(role='head_coach').count(), OPENING_SEATS)
        self.assertFalse(
            assignments.exclude(role__in=('gm', 'head_coach')).exists()
        )
        for index, slug in enumerate(f'founding-{number:02d}' for number in range(1, 25)):
            gm_seat = assignments.get(franchise__placeholder_slug=slug, role='gm')
            coach_seat = assignments.get(franchise__placeholder_slug=slug, role='head_coach')
            self.assertEqual(
                gm_seat.gm.universe_key,
                universe_key(SEED, RELEASE_SLUG, ROLE_GM, index),
            )
            self.assertIsNone(gm_seat.coach_id)
            self.assertEqual(
                coach_seat.coach.universe_key,
                universe_key(SEED, RELEASE_SLUG, ROLE_HEAD_COACH, index),
            )
            self.assertIsNone(coach_seat.gm_id)
            self.assertGreaterEqual(1970 - coach_seat.coach.birth_year, EMPLOYED_HEAD_COACH_AGE_MIN)
        seated_coach_ids = set(
            assignments.filter(role='head_coach').values_list('coach_id', flat=True)
        )
        unemployed = [coach for coach in coaches if coach.pk not in seated_coach_ids]
        self.assertEqual(len(unemployed), POOL_SIZE - OPENING_SEATS)
        self.assertTrue(any(1970 - coach.birth_year < EMPLOYED_HEAD_COACH_AGE_MIN for coach in unemployed))

        self.assertEqual(Conference.objects.count(), 0)
        self.assertEqual(Division.objects.count(), 0)
        self.assertEqual(Team.objects.count(), 0)
        self.assertEqual(Timeline.objects.count(), 0)
        self.assertEqual(UniverseStaffPost.objects.count(), 0)
        self.assertEqual(UniverseProStint.objects.count(), 0)
        self.assertFalse(UniverseFranchise.objects.filter(origin='expansion').exists())
        self.assertFalse(
            FranchiseSeasonMembership.objects.exclude(season_year=1970).exists()
        )

    def test_identical_rerun_keeps_the_rows(self):
        generate_founding_release(self.league.id, SEED)
        franchise_id = UniverseFranchise.objects.get(placeholder_slug='founding-01').pk
        identity_id = UniverseGM.objects.get(
            universe_key=universe_key(SEED, RELEASE_SLUG, ROLE_GM, 0),
        ).identity_id
        self.assertEqual(generate_founding_release(self.league.id, SEED), 'unchanged')
        self.assertEqual(
            UniverseFranchise.objects.get(placeholder_slug='founding-01').pk,
            franchise_id,
        )
        self.assertEqual(
            UniverseGM.objects.get(
                universe_key=universe_key(SEED, RELEASE_SLUG, ROLE_GM, 0),
            ).identity_id,
            identity_id,
        )

    def test_changed_seed_replaces_only_this_slice(self):
        generate_founding_release(self.league.id, SEED)
        original_keys = set(
            UniverseStaffIdentity.objects.values_list('universe_key', flat=True)
        )
        self.assertEqual(generate_founding_release(self.league.id, OTHER_SEED), 'rebuilt')
        self.release.refresh_from_db()
        self.assertEqual(self.release.seed, OTHER_SEED)
        self.assertEqual(self.release.generator_version, GENERATOR_VERSION)
        replaced_keys = set(
            UniverseStaffIdentity.objects.values_list('universe_key', flat=True)
        )
        self.assertEqual(len(replaced_keys), POOL_SIZE * 2)
        self.assertTrue(original_keys.isdisjoint(replaced_keys))
        self.assertEqual(UniverseFranchise.objects.count(), 24)
        self.assertEqual(FranchiseSeasonMembership.objects.count(), 24)
        self.assertEqual(OpeningStaffAssignment.objects.count(), 48)

    def test_rebuild_restores_a_tampered_name_and_keeps_a_fingerprint(self):
        self.release.catalog_fingerprint = 'preexisting'
        self.release.save()
        generate_founding_release(self.league.id, SEED)
        staff_key = universe_key(SEED, RELEASE_SLUG, ROLE_GM, 3)
        original = UniverseGM.objects.get(universe_key=staff_key).name
        UniverseGM.objects.filter(universe_key=staff_key).update(name='Tampered Name')
        UniverseStaffIdentity.objects.filter(universe_key=staff_key).update(name='Tampered Name')
        self.assertEqual(generate_founding_release(self.league.id, SEED), 'rebuilt')
        restored = UniverseGM.objects.get(universe_key=staff_key)
        self.assertEqual(restored.name, original)
        self.assertEqual(restored.identity.name, original)
        self.release.refresh_from_db()
        self.assertEqual(self.release.catalog_fingerprint, 'preexisting')
        self.assertEqual(self.release.validation_fingerprint, '')
        self.assertEqual(self.release.status, UniverseRelease.Status.DRAFT)

    def test_published_and_pinned_releases_are_refused(self):
        generate_founding_release(self.league.id, SEED)
        staff_key = universe_key(SEED, RELEASE_SLUG, ROLE_GM, 1)
        UniverseGM.objects.filter(universe_key=staff_key).update(name='Tampered Name')
        UniverseStaffIdentity.objects.filter(universe_key=staff_key).update(name='Tampered Name')
        self.pin_timeline()
        with self.assertRaises(FoundingGenerationError):
            generate_founding_release(self.league.id, SEED)
        self.assertEqual(
            UniverseGM.objects.get(universe_key=staff_key).name,
            'Tampered Name',
        )
        Timeline.objects.all().delete()

        UniverseRelease.objects.filter(pk=self.release.pk).update(
            status=UniverseRelease.Status.PUBLISHED,
            catalog_fingerprint='a' * 64,
            validation_fingerprint='b' * 64,
            published_at=timezone.now(),
        )
        with self.assertRaises(FoundingGenerationError):
            generate_founding_release(self.league.id, SEED)
        self.assertEqual(
            UniverseGM.objects.get(universe_key=staff_key).name,
            'Tampered Name',
        )
        self.assertEqual(UniverseFranchise.objects.count(), 24)

    def test_a_published_league_without_a_draft_is_refused(self):
        self.release.delete()
        published = self.make_release(
            status=UniverseRelease.Status.PUBLISHED,
            catalog_fingerprint='c' * 64,
            validation_fingerprint='d' * 64,
            published_at=timezone.now(),
            generator_version=GENERATOR_VERSION,
            seed=SEED,
        )
        with self.assertRaises(FoundingGenerationError):
            generate_founding_release(self.league.id, SEED)
        self.assertEqual(published.franchises.count(), 0)

    def test_missing_ambiguous_and_wrong_horizon_write_nothing(self):
        with self.assertRaises(FoundingGenerationError):
            generate_founding_release(self.league.id + 1000, SEED)
        self.make_release(slug='second-draft')
        with self.assertRaises(FoundingGenerationError):
            generate_founding_release(self.league.id, SEED)
        self.assertEqual(UniverseFranchise.objects.count(), 0)
        UniverseRelease.objects.filter(slug='second-draft').delete()
        self.release.founding_franchise_count = 32
        self.release.save()
        with self.assertRaises(FoundingGenerationError):
            generate_founding_release(self.league.id, SEED)
        self.assertEqual(UniverseFranchise.objects.count(), 0)

    def test_command_prints_the_outcome(self):
        output = StringIO()
        call_command(
            'generate_founding_release',
            league_id=self.league.id,
            seed=SEED,
            stdout=output,
        )
        self.assertEqual(output.getvalue().strip(), 'created')
        again = StringIO()
        call_command(
            'generate_founding_release',
            league_id=self.league.id,
            seed=SEED,
            stdout=again,
        )
        self.assertEqual(again.getvalue().strip(), 'unchanged')
        refused = StringIO()
        UniverseRelease.objects.filter(pk=self.release.pk).update(
            status=UniverseRelease.Status.PUBLISHED,
            catalog_fingerprint='e' * 64,
            validation_fingerprint='f' * 64,
            published_at=timezone.now(),
        )
        with self.assertRaises(CommandError):
            call_command(
                'generate_founding_release',
                league_id=self.league.id,
                seed=SEED,
                stdout=refused,
            )

    def pin_timeline(self):
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


def _tag_product(tags, traits):
    for strength_1 in tags:
        for strength_2 in tags:
            if strength_1 == strength_2:
                continue
            for weakness in tags:
                if weakness in (strength_1, strength_2):
                    continue
                for trait_1 in traits:
                    for trait_2 in traits:
                        if trait_1 == trait_2:
                            continue
                        yield (strength_1, strength_2, weakness, trait_1, trait_2)
