import inspect
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from leagues.models import League
from timelines.models import Timeline
from universe.generation.founding import (
    GENERATOR_VERSION as FOUNDING_VERSION,
    ROLE_GM,
    ROLE_HEAD_COACH,
    FoundingGenerationError,
    build_staff,
    generate_founding_release,
    hmac_int,
    universe_key,
)
from universe.generation.names import GIVEN_NAMES, SURNAMES
from universe.generation.provisional_names import (
    CITIES,
    NICKNAMES,
    ProvisionalNameError,
    assignment_order,
    replace_provisional_value,
    resolve_names,
)
from universe.generation.schedule import (
    GENERATOR_VERSION,
    OPENING_SEATS,
    POOL_SIZE,
    RULE_TEXT,
    ScheduleGenerationError,
    build_opening_staff,
    generate_schedule_release,
)
from universe.models import (
    FranchiseSeasonMembership,
    HistoricalStanding,
    ProvisionalFranchiseName,
    UniverseFranchise,
    UniverseRelease,
)
from universe.people import UniverseCoach, UniverseGM, UniversePlayer, UniverseStaffIdentity
from universe.rules import EraRuleProfile, ExpansionClub, ExpansionEvent, OpeningStaffAssignment

SEED = 1970
OTHER_SEED = 1971
RELEASE_SLUG = 'confirmed-1970'

NFL_CITIES = {
    'Phoenix', 'Atlanta', 'Baltimore', 'Buffalo', 'Charlotte', 'Chicago',
    'Cincinnati', 'Cleveland', 'Dallas', 'Denver', 'Detroit', 'Green Bay',
    'Houston', 'Indianapolis', 'Jacksonville', 'Kansas City', 'Las Vegas',
    'Los Angeles', 'Miami', 'Minneapolis', 'Boston', 'New Orleans', 'New York',
    'Philadelphia', 'Pittsburgh', 'San Francisco', 'Seattle', 'Tampa',
    'Nashville', 'Washington', 'Oakland', 'San Diego', 'St. Louis',
}
NFL_NICKNAMES = {
    'Cardinals', 'Falcons', 'Ravens', 'Bills', 'Panthers', 'Bears', 'Bengals',
    'Browns', 'Cowboys', 'Broncos', 'Lions', 'Packers', 'Texans', 'Colts',
    'Jaguars', 'Chiefs', 'Raiders', 'Chargers', 'Rams', 'Dolphins', 'Vikings',
    'Patriots', 'Saints', 'Giants', 'Jets', 'Eagles', 'Steelers', 'Seahawks',
    'Buccaneers', 'Titans', 'Commanders', 'Oilers', 'Redskins',
}


class ProvisionalListTests(SimpleTestCase):
    def test_lists_keep_the_approved_order(self):
        self.assertEqual(CITIES[0], 'Aldermere')
        self.assertEqual(CITIES[-1], 'Yarrowfield')
        self.assertEqual(NICKNAMES[0], 'Albatrosses')
        self.assertEqual(NICKNAMES[-1], 'Thrushes')
        self.assertEqual(len(CITIES), 40)
        self.assertEqual(len(NICKNAMES), 40)
        self.assertEqual(len(set(CITIES)), 40)
        self.assertEqual(len(set(NICKNAMES)), 40)
        self.assertFalse(set(CITIES) & NFL_CITIES)
        self.assertFalse(set(NICKNAMES) & NFL_NICKNAMES)

    def test_assignment_order_is_expansion_then_numeric_founding(self):
        order = assignment_order()
        self.assertEqual(order[:6], (
            'expansion-1976-01',
            'expansion-1976-02',
            'expansion-1995-01',
            'expansion-1995-02',
            'expansion-1999-01',
            'expansion-2002-01',
        ))
        self.assertEqual(
            order[6:],
            tuple(f'founding-{number:02d}' for number in range(1, 27)),
        )
        self.assertEqual(len(order), 32)

    def test_founding_module_does_not_name_provisional_records(self):
        import universe.generation.founding as founding
        source = inspect.getsource(founding)
        self.assertNotIn('ProvisionalFranchiseName', source)
        self.assertNotIn('provisional_names', source)


class ScheduleReleaseTests(TestCase):
    def setUp(self):
        self.league = League(name='Schedule League', slug='schedule-league', tier=1, active=True)
        self.league.save()
        self.release = self.make_release()
        self.release.catalog_fingerprint = 'preexisting'
        self.release.expansion_schedule_version = 3
        self.release.save()
        self.assertEqual(
            generate_schedule_release(self.league.id, SEED),
            'created',
        )
        self.release.refresh_from_db()

    def make_release(self, **overrides):
        fields = {
            'slug': RELEASE_SLUG,
            'root_league': self.league,
            'generator_version': 'pending',
            'seed': 1,
            'founding_franchise_count': 26,
            'expansion_schedule_version': 3,
            'first_season_year': 1970,
            'season_count': 60,
            'last_season_year': 2029,
            'status': UniverseRelease.Status.DRAFT,
        }
        fields.update(overrides)
        release = UniverseRelease(**fields)
        release.save()
        return release

    def test_writes_the_schedule_counts(self):
        self.assertEqual(self.release.generator_version, GENERATOR_VERSION)
        self.assertEqual(self.release.seed, SEED)
        self.assertEqual(self.release.status, UniverseRelease.Status.DRAFT)
        self.assertEqual(self.release.catalog_fingerprint, 'preexisting')
        self.assertEqual(self.release.validation_fingerprint, '')
        self.assertEqual(self.release.expansion_schedule_version, 3)
        self.assertIsNone(self.release.published_at)
        self.assertEqual(self.release.franchises.count(), 32)
        self.assertEqual(
            self.release.franchises.filter(origin=UniverseFranchise.Origin.FOUNDING).count(),
            26,
        )
        self.assertEqual(
            self.release.franchises.filter(origin=UniverseFranchise.Origin.EXPANSION).count(),
            6,
        )
        self.assertEqual(
            FranchiseSeasonMembership.objects.filter(franchise__release=self.release).count(),
            1797,
        )
        self.assertEqual(self.release.expansion_events.count(), 4)
        self.assertEqual(ExpansionClub.objects.filter(event__release=self.release).count(), 6)
        self.assertEqual(UniverseStaffIdentity.objects.filter(release=self.release).count(), 200)
        self.assertEqual(UniverseGM.objects.filter(release=self.release).count(), POOL_SIZE)
        self.assertEqual(UniverseCoach.objects.filter(release=self.release).count(), POOL_SIZE)
        self.assertEqual(
            OpeningStaffAssignment.objects.filter(release=self.release, role='gm').count(),
            OPENING_SEATS,
        )
        self.assertEqual(
            OpeningStaffAssignment.objects.filter(release=self.release, role='head_coach').count(),
            OPENING_SEATS,
        )
        self.assertEqual(self.release.provisional_franchise_names.count(), 32)
        self.assertEqual(UniversePlayer.objects.count(), 0)
        self.assertEqual(HistoricalStanding.objects.count(), 0)
        self.assertEqual(EraRuleProfile.objects.count(), 0)
        self.assertFalse(
            OpeningStaffAssignment.objects.exclude(role__in=('gm', 'head_coach')).exists()
        )
        self.assertFalse(
            self.release.franchises.filter(catalog_team__isnull=False).exists()
        )
        self.assertFalse(
            self.release.franchises.filter(history_predecessor__isnull=False).exists()
        )

    def test_alignment_follows_the_corrected_seats(self):
        def seat(year, slug):
            return FranchiseSeasonMembership.objects.get(
                franchise__placeholder_slug=slug,
                season_year=year,
            )

        founding = seat(1970, 'founding-09')
        self.assertEqual(founding.conference_slug, 'conference-1')
        self.assertEqual(founding.division_slug, 'd3')
        self.assertEqual(founding.slot_order, 1)
        self.assertEqual(
            (seat(1976, 'founding-09').division_slug, seat(1976, 'founding-09').slot_order),
            ('d3', 1),
        )
        self.assertEqual(seat(1976, 'expansion-1976-01').conference_slug, 'conference-2')
        self.assertEqual(
            (seat(1976, 'expansion-1976-01').division_slug, seat(1976, 'expansion-1976-01').slot_order),
            ('d2', 5),
        )
        self.assertEqual(seat(1976, 'expansion-1976-02').conference_slug, 'conference-1')
        self.assertEqual(seat(1977, 'expansion-1976-01').conference_slug, 'conference-1')
        self.assertEqual(
            (seat(1977, 'expansion-1976-02').division_slug, seat(1977, 'expansion-1976-02').slot_order),
            ('d1', 5),
        )
        self.assertEqual(
            (seat(1994, 'expansion-1976-01').conference_slug, seat(1994, 'expansion-1976-01').division_slug),
            ('conference-1', 'd2'),
        )
        self.assertEqual(
            (seat(1998, 'founding-09').division_slug, seat(1998, 'founding-09').slot_order),
            ('d1', 5),
        )
        moved = seat(1999, 'founding-09')
        self.assertEqual((moved.conference_slug, moved.division_slug, moved.slot_order), ('conference-1', 'd3', 1))
        self.assertEqual(seat(2029, 'founding-09').division_slug, 'd4')
        self.assertEqual(seat(1970, 'founding-25').slot_order, 5)
        self.assertEqual(
            (seat(1999, 'expansion-1999-01').division_slug, seat(1999, 'expansion-1999-01').slot_order),
            ('d1', 6),
        )
        self.assertEqual(seat(2002, 'expansion-2002-01').conference_slug, 'conference-2')
        self.assertFalse(
            FranchiseSeasonMembership.objects.filter(
                franchise__placeholder_slug='expansion-2002-01',
                season_year__lt=2002,
            ).exists()
        )
        self.assertTrue(
            FranchiseSeasonMembership.objects.filter(franchise__release=self.release).filter(
                owner_selectable=True,
            ).count() == 1797
        )

    def test_expansion_rows_match_the_approved_arithmetic(self):
        event = ExpansionEvent.objects.get(release=self.release, season_year=1976)
        self.assertEqual(event.existing_club_count, 26)
        self.assertEqual(event.new_club_count, 2)
        self.assertEqual(event.active_target, 40)
        self.assertEqual(event.taxi_capacity, 8)
        self.assertEqual(event.max_losses_per_existing, 2)
        self.assertEqual(event.min_losses_per_existing, 1)
        self.assertEqual(event.clubs_at_max_loss, 6)
        self.assertEqual(event.clubs_at_min_loss, 20)
        self.assertEqual(event.veteran_picks_total, 32)
        self.assertEqual(event.rookie_picks_per_new, 17)
        self.assertEqual(event.undrafted_multiplier, 2)
        self.assertEqual(event.undrafted_pool, 56)
        self.assertEqual(event.undrafted_reserved_per_new, 7)
        self.assertEqual(event.expansion_draft_order_rule, RULE_TEXT['expansion_draft_order_rule'])
        self.assertEqual(event.rookie_draft_slot_rule, RULE_TEXT['rookie_draft_slot_rule'])
        self.assertIn('does not choose which person', event.gm_hiring_rule)
        self.assertIn('does not choose which person', event.hc_hiring_rule)
        later = ExpansionEvent.objects.get(release=self.release, season_year=2002)
        self.assertEqual(later.existing_club_count, 31)
        self.assertEqual(later.min_losses_per_existing, 0)
        self.assertEqual(later.clubs_at_min_loss, 5)
        self.assertEqual(later.undrafted_pool, 64)
        clubs = list(
            ExpansionClub.objects.filter(event__season_year=1976).order_by('selection_order')
        )
        self.assertEqual(
            [club.franchise.placeholder_slug for club in clubs],
            ['expansion-1976-01', 'expansion-1976-02'],
        )
        self.assertEqual(
            ExpansionClub.objects.get(event__season_year=1999).selection_order,
            1,
        )

    def test_names_follow_the_draw_and_project_display_name(self):
        expected = resolve_names(SEED, RELEASE_SLUG, {}, redraw=False)
        cities = []
        nicknames = []
        for name in self.release.provisional_franchise_names.all():
            row = expected[name.placeholder_slug]
            self.assertEqual(name.city, row['city'])
            self.assertEqual(name.nickname, row['nickname'])
            self.assertEqual(name.city_status, 'provisional')
            self.assertEqual(name.nickname_status, 'provisional')
            self.assertIn(name.city, CITIES)
            self.assertIn(name.nickname, NICKNAMES)
            cities.append(name.city)
            nicknames.append(name.nickname)
            franchise = self.release.franchises.get(placeholder_slug=name.placeholder_slug)
            self.assertEqual(franchise.display_name, f'{name.city} {name.nickname}')
        self.assertEqual(len(set(cities)), 32)
        self.assertEqual(len(set(nicknames)), 32)

    def test_opening_staff_match_v1_through_index_23(self):
        v1_rows = build_staff(SEED, RELEASE_SLUG, 1970)
        v2_rows = build_opening_staff(SEED, RELEASE_SLUG, 1970)
        for role in (ROLE_GM, ROLE_HEAD_COACH):
            for index in range(24):
                old = next(row for row in v1_rows if row['role'] == role and row['index'] == index)
                new = next(row for row in v2_rows if row['role'] == role and row['index'] == index)
                self.assertEqual(old['universe_key'], new['universe_key'])
                self.assertEqual(old['name'], new['name'])
                self.assertEqual(old['birth_year'], new['birth_year'])
                self.assertEqual(old['strength_1'], new['strength_1'])
        for index in (24, 25):
            new = next(row for row in v2_rows if row['role'] == ROLE_HEAD_COACH and row['index'] == index)
            age = 49 + (
                hmac_int(SEED, f"{new['universe_key']}:birth_year") % 17
            )
            self.assertEqual(new['birth_year'], 1970 - age)
            self.assertGreaterEqual(1970 - new['birth_year'], 49)
            self.assertLessEqual(1970 - new['birth_year'], 65)
        for index in range(OPENING_SEATS):
            slug = f'founding-{index + 1:02d}'
            gm_seat = OpeningStaffAssignment.objects.get(
                franchise__placeholder_slug=slug,
                role='gm',
            )
            coach_seat = OpeningStaffAssignment.objects.get(
                franchise__placeholder_slug=slug,
                role='head_coach',
            )
            self.assertEqual(
                gm_seat.gm.universe_key,
                universe_key(SEED, RELEASE_SLUG, ROLE_GM, index),
            )
            self.assertEqual(
                coach_seat.coach.universe_key,
                universe_key(SEED, RELEASE_SLUG, ROLE_HEAD_COACH, index),
            )
            self.assertGreaterEqual(1970 - coach_seat.coach.birth_year, 49)
            given, surname = gm_seat.gm.name.split()
            self.assertIn(given, GIVEN_NAMES)
            self.assertIn(surname, SURNAMES)
        seated = set(
            OpeningStaffAssignment.objects.filter(role='head_coach').values_list('coach_id', flat=True)
        )
        unemployed = UniverseCoach.objects.exclude(pk__in=seated)
        self.assertEqual(unemployed.count(), POOL_SIZE - OPENING_SEATS)
        self.assertTrue(any(1970 - coach.birth_year < 49 for coach in unemployed))
        self.assertFalse(
            OpeningStaffAssignment.objects.filter(
                franchise__origin=UniverseFranchise.Origin.EXPANSION,
            ).exists()
        )

    def test_same_seed_keeps_rows_and_a_provisional_edit(self):
        franchise = UniverseFranchise.objects.get(placeholder_slug='founding-01')
        name = ProvisionalFranchiseName.objects.get(placeholder_slug='founding-01')
        franchise_id = franchise.pk
        name_id = name.pk
        self.assertEqual(generate_schedule_release(self.league.id, SEED), 'unchanged')
        self.assertEqual(UniverseFranchise.objects.get(placeholder_slug='founding-01').pk, franchise_id)

        name.city = 'Customport'
        name.save()
        franchise.display_name = 'Tampered'
        franchise.save()
        self.assertEqual(generate_schedule_release(self.league.id, SEED), 'rebuilt')
        name.refresh_from_db()
        restored = UniverseFranchise.objects.get(placeholder_slug='founding-01')
        self.assertEqual(name.pk, name_id)
        self.assertNotEqual(restored.pk, franchise_id)
        self.assertEqual(name.city, 'Customport')
        self.assertEqual(name.city_status, 'provisional')
        self.assertEqual(restored.display_name, f'Customport {name.nickname}')

    def test_replaced_values_survive_a_new_seed(self):
        name = ProvisionalFranchiseName.objects.get(placeholder_slug='expansion-1999-01')
        replace_provisional_value(name, 'city', 'Harborview')
        ProvisionalFranchiseName.objects.filter(pk=name.pk).update(nickname='Customnick')
        self.assertEqual(generate_schedule_release(self.league.id, OTHER_SEED), 'rebuilt')
        name.refresh_from_db()
        self.assertEqual(name.city, 'Harborview')
        self.assertEqual(name.city_status, 'replaced')
        self.assertNotEqual(name.nickname, 'Customnick')
        self.assertEqual(name.nickname_status, 'provisional')
        franchise = UniverseFranchise.objects.get(placeholder_slug='expansion-1999-01')
        self.assertEqual(franchise.display_name, f'{name.city} {name.nickname}')
        self.release.refresh_from_db()
        self.assertEqual(self.release.catalog_fingerprint, 'preexisting')
        self.assertEqual(self.release.expansion_schedule_version, 3)
        self.assertEqual(self.release.validation_fingerprint, '')

    def test_replacement_rejects_an_exact_duplicate_and_keeps_fields_independent(self):
        first = ProvisionalFranchiseName.objects.get(placeholder_slug='founding-01')
        second = ProvisionalFranchiseName.objects.get(placeholder_slug='founding-02')
        with self.assertRaises(ProvisionalNameError):
            replace_provisional_value(second, 'city', first.city)
        second.refresh_from_db()
        self.assertEqual(second.city_status, 'provisional')
        replace_provisional_value(second, 'nickname', first.city.lower())
        second.refresh_from_db()
        self.assertEqual(second.nickname, first.city.lower())
        self.assertEqual(second.nickname_status, 'replaced')
        self.assertEqual(second.city_status, 'provisional')
        with self.assertRaises(ProvisionalNameError):
            replace_provisional_value(second, 'nickname', 'Another Nickname')
        franchise = UniverseFranchise.objects.get(placeholder_slug='founding-02')
        self.assertEqual(franchise.display_name, f'{second.city} {second.nickname}')

    def test_replacement_locks_the_release_before_the_sibling_check(self):
        second = ProvisionalFranchiseName.objects.get(placeholder_slug='founding-02')
        events = []
        real_select = UniverseRelease.objects.select_for_update
        real_filter = ProvisionalFranchiseName.objects.filter

        def select_for_update(*args, **kwargs):
            queryset = real_select(*args, **kwargs)
            real_get = queryset.get

            def get(*get_args, **get_kwargs):
                events.append(('lock', get_kwargs.get('pk')))
                return real_get(*get_args, **get_kwargs)

            queryset.get = get
            return queryset

        def filter(*args, **kwargs):
            if kwargs.get('release_id') == second.release_id:
                events.append('siblings')
            return real_filter(*args, **kwargs)

        with (
            patch.object(UniverseRelease.objects, 'select_for_update', side_effect=select_for_update),
            patch.object(ProvisionalFranchiseName.objects, 'filter', side_effect=filter),
        ):
            replace_provisional_value(second, 'nickname', 'Lockname')

        self.assertEqual(events[0], ('lock', second.release_id))
        self.assertEqual(events[1], 'siblings')
        second.refresh_from_db()
        self.assertEqual(second.nickname, 'Lockname')
        self.assertEqual(second.nickname_status, 'replaced')
        franchise = UniverseFranchise.objects.get(placeholder_slug='founding-02')
        self.assertEqual(franchise.display_name, f'{second.city} Lockname')

    def test_duplicate_stored_names_and_an_exhausted_list_refuse(self):
        franchise_id = UniverseFranchise.objects.get(placeholder_slug='founding-03').pk
        first, second = list(self.release.provisional_franchise_names.order_by('placeholder_slug')[:2])
        ProvisionalFranchiseName.objects.filter(pk=second.pk).update(city=first.city)
        with self.assertRaises(ScheduleGenerationError):
            generate_schedule_release(self.league.id, SEED)
        self.assertEqual(
            UniverseFranchise.objects.get(placeholder_slug='founding-03').pk,
            franchise_id,
        )
        ProvisionalFranchiseName.objects.filter(pk=second.pk).update(city='Separateport')
        with patch('universe.generation.provisional_names.CITIES', ('Onlyport',)):
            with self.assertRaises(ScheduleGenerationError) as raised:
                generate_schedule_release(self.league.id, OTHER_SEED)
        self.assertIn('No unused provisional name remains.', str(raised.exception))
        self.assertEqual(
            UniverseFranchise.objects.get(placeholder_slug='founding-03').pk,
            franchise_id,
        )

    def test_a_nonblank_rule_survives_a_rebuild(self):
        event = ExpansionEvent.objects.get(season_year=1976)
        event.expansion_draft_order_rule = 'Keep this rule.'
        event.save()
        self.assertEqual(generate_schedule_release(self.league.id, OTHER_SEED), 'rebuilt')
        event = ExpansionEvent.objects.get(season_year=1976)
        self.assertEqual(event.expansion_draft_order_rule, 'Keep this rule.')
        self.assertEqual(event.roster_building_rule, RULE_TEXT['roster_building_rule'])

    def test_published_and_pinned_releases_are_refused(self):
        self.pin_timeline()
        with self.assertRaises(ScheduleGenerationError):
            generate_schedule_release(self.league.id, OTHER_SEED)
        Timeline.objects.all().delete()
        UniverseRelease.objects.filter(pk=self.release.pk).update(
            status=UniverseRelease.Status.PUBLISHED,
            catalog_fingerprint='a' * 64,
            validation_fingerprint='b' * 64,
            published_at=timezone.now(),
        )
        with self.assertRaises(ScheduleGenerationError):
            generate_schedule_release(self.league.id, OTHER_SEED)
        self.assertEqual(self.release.franchises.count(), 32)

    def test_wrong_horizon_writes_nothing(self):
        self.release.founding_franchise_count = 24
        self.release.save()
        with self.assertRaises(ScheduleGenerationError):
            generate_schedule_release(self.league.id, SEED)
        self.assertEqual(self.release.franchises.count(), 32)

    def test_command_selects_v2(self):
        output = StringIO()
        call_command(
            'generate_founding_release',
            league_id=self.league.id,
            seed=SEED,
            generator='fg-founding-2',
            stdout=output,
        )
        self.assertEqual(output.getvalue().strip(), 'unchanged')

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


class GeneratorBoundaryTests(TestCase):
    def make_league(self, slug):
        league = League(name=slug, slug=slug, tier=1, active=True)
        league.save()
        return league

    def make_release(self, league, **overrides):
        fields = {
            'slug': f'{league.slug}-release',
            'root_league': league,
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

    def test_default_command_stays_on_fg_founding_1(self):
        league = self.make_league('default-league')
        release = self.make_release(league)
        output = StringIO()
        call_command(
            'generate_founding_release',
            league_id=league.id,
            seed=SEED,
            stdout=output,
        )
        self.assertEqual(output.getvalue().strip(), 'created')
        release.refresh_from_db()
        self.assertEqual(release.generator_version, FOUNDING_VERSION)
        self.assertEqual(release.franchises.count(), 24)
        self.assertEqual(release.provisional_franchise_names.count(), 0)

    def test_v1_leaves_a_provisional_name_untouched(self):
        league = self.make_league('v1-name-league')
        release = self.make_release(league)
        name = ProvisionalFranchiseName(
            release=release,
            placeholder_slug='founding-01',
            city='Keptport',
            city_status='provisional',
            nickname='Keptname',
            nickname_status='replaced',
        )
        name.save()
        name_id = name.pk
        self.assertEqual(generate_founding_release(league.id, SEED), 'created')
        name.refresh_from_db()
        self.assertEqual(name.pk, name_id)
        self.assertEqual(name.city, 'Keptport')
        self.assertEqual(name.nickname, 'Keptname')
        self.assertEqual(name.nickname_status, 'replaced')
        franchise = release.franchises.get(placeholder_slug='founding-01')
        self.assertEqual(franchise.display_name, 'Founding franchise 01')

    def test_v1_refuses_a_26_club_horizon(self):
        league = self.make_league('v1-horizon-league')
        release = self.make_release(league, founding_franchise_count=26)
        with self.assertRaises(FoundingGenerationError):
            generate_founding_release(league.id, SEED)
        self.assertEqual(release.franchises.count(), 0)
        self.assertEqual(release.provisional_franchise_names.count(), 0)

    def test_v2_refuses_an_fg_founding_1_slice(self):
        league = self.make_league('v1-slice-league')
        release = self.make_release(league)
        self.assertEqual(generate_founding_release(league.id, SEED), 'created')
        UniverseRelease.objects.filter(pk=release.pk).update(founding_franchise_count=26)
        with self.assertRaises(ScheduleGenerationError) as raised:
            generate_schedule_release(league.id, SEED)
        self.assertIn('fg-founding-1', str(raised.exception))
        self.assertEqual(release.franchises.count(), 24)
        self.assertEqual(release.provisional_franchise_names.count(), 0)

    def test_v2_command_refuses_a_published_release(self):
        league = self.make_league('published-league')
        release = self.make_release(league, founding_franchise_count=26)
        generate_schedule_release(league.id, SEED)
        UniverseRelease.objects.filter(pk=release.pk).update(
            status=UniverseRelease.Status.PUBLISHED,
            catalog_fingerprint='c' * 64,
            validation_fingerprint='d' * 64,
            published_at=timezone.now(),
        )
        with self.assertRaises(CommandError):
            call_command(
                'generate_founding_release',
                league_id=league.id,
                seed=OTHER_SEED,
                generator='fg-founding-2',
            )
        self.assertEqual(release.franchises.count(), 32)
