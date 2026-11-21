from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from leagues.models import League
from play.campaign import campaign_for_user
from timelines.models import (
    RosterMembership,
    ScheduleGame,
    ScheduleWeekAssignment,
    Timeline,
    TimelineFranchiseControl,
    TimelineParticipant,
    TimelinePlayer,
    TimelineSeason,
    deferred_lifecycle,
    writing_calendar_rows,
)
from universe.generation.prospects import later_profile_values
from universe.models import FranchiseSeasonMembership, UniverseFranchise, UniverseRelease
from universe.people import College, UniversePlayer
from universe.rules import EraRuleProfile


class PlayPagesTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user('owner', password='campaign-pass')
        cls.other = User.objects.create_user('other', password='other-pass')
        league = League.objects.create(name='Federation', slug='play-federation', tier=1)
        release = UniverseRelease(
            slug='play-release',
            root_league=league,
            generator_version='play-demo',
            seed=1,
            founding_franchise_count=3,
            expansion_schedule_version=1,
            first_season_year=1970,
            season_count=60,
            last_season_year=2029,
        )
        release.save()
        cls.club = UniverseFranchise.objects.create(
            release=release,
            placeholder_slug='harbor',
            display_name='Harbor Hawks',
            origin=UniverseFranchise.Origin.FOUNDING,
            first_active_season=1970,
        )
        cls.opponent = UniverseFranchise.objects.create(
            release=release,
            placeholder_slug='river',
            display_name='River City',
            origin=UniverseFranchise.Origin.FOUNDING,
            first_active_season=1970,
        )
        cls.bystander = UniverseFranchise.objects.create(
            release=release,
            placeholder_slug='quiet',
            display_name='Quiet Club',
            origin=UniverseFranchise.Origin.FOUNDING,
            first_active_season=1970,
        )
        for franchise, slot in ((cls.club, 1), (cls.opponent, 2), (cls.bystander, 3)):
            FranchiseSeasonMembership.objects.create(
                franchise=franchise,
                season_year=2021,
                conference_slug='east',
                division_slug='north',
                slot_order=slot,
            )
        profile = EraRuleProfile(
            release=release,
            **next(row for row in later_profile_values() if row['profile_key'] == 'y2021'),
        )
        profile.save()
        college = College.objects.create(
            release=release,
            slug='harbor-college',
            name='Harbor College',
        )
        player = UniversePlayer.objects.create(
            release=release,
            college=college,
            origin_profile=profile,
            universe_key='c' * 64,
            name='Ada Rook',
            birth_year=1948,
            birth_day=40,
            position='QB',
            handedness=UniversePlayer.Handedness.RIGHT,
            height_in=74,
            weight_lb=215,
            cohort=UniversePlayer.Cohort.OPENING,
            first_pro_year=1965,
            peak_age=27,
            development_rate=50,
            decline_rate=40,
        )
        release.catalog_fingerprint = 'a' * 64
        release.validation_fingerprint = 'b' * 64
        release.published_at = timezone.now()
        release.status = UniverseRelease.Status.PUBLISHED
        release.save()

        cls.timeline = Timeline.objects.create(
            created_by=cls.owner,
            release=release,
            name='Opening campaign',
            start_season_year=2021,
            current_season_year=2021,
            season_index=1,
            phase=Timeline.Phase.DIRECTION,
            status=Timeline.Status.ACTIVE,
        )
        cls.participant = TimelineParticipant.objects.create(
            timeline=cls.timeline,
            user=cls.owner,
            status=TimelineParticipant.Status.ACTIVE,
            joined_season=2021,
        )
        TimelineFranchiseControl.objects.create(
            timeline=cls.timeline,
            participant=cls.participant,
            franchise=cls.club,
            first_controlled_season=2021,
        )
        season = TimelineSeason.objects.create(
            timeline=cls.timeline,
            profile=profile,
            season_year=2021,
            season_index=1,
            phase=Timeline.Phase.DIRECTION,
        )
        with deferred_lifecycle():
            overlay = TimelinePlayer.objects.create(
                timeline=cls.timeline,
                player=player,
                status=TimelinePlayer.Status.ACTIVE,
            )
            RosterMembership.objects.create(
                overlay=overlay,
                season=season,
                franchise=cls.club,
                position='QB',
                role=RosterMembership.Role.STARTER,
                availability=RosterMembership.Availability.AVAILABLE,
                start_week=0,
            )
        own_game = ScheduleGame.objects.create(
            timeline=cls.timeline,
            season_year=2021,
            home=cls.club,
            away=cls.opponent,
            kind=ScheduleGame.Kind.REGULAR,
            category=ScheduleGame.Category.DIVISION,
        )
        other_game = ScheduleGame.objects.create(
            timeline=cls.timeline,
            season_year=2021,
            home=cls.opponent,
            away=cls.bystander,
            kind=ScheduleGame.Kind.REGULAR,
            category=ScheduleGame.Category.DIVISION,
        )
        with writing_calendar_rows():
            ScheduleWeekAssignment(
                timeline=cls.timeline,
                game=own_game,
                season_year=2021,
                week=3,
            ).save()
            ScheduleWeekAssignment(
                timeline=cls.timeline,
                game=other_game,
                season_year=2021,
                week=4,
            ).save()

        stranger_timeline = Timeline.objects.create(
            created_by=cls.owner,
            release=release,
            name='Someone else plays',
            start_season_year=2021,
            current_season_year=2021,
            season_index=1,
            phase=Timeline.Phase.DIRECTION,
            status=Timeline.Status.ACTIVE,
        )
        TimelineParticipant.objects.create(
            timeline=stranger_timeline,
            user=cls.other,
            status=TimelineParticipant.Status.ACTIVE,
            joined_season=2021,
        )

    def test_home_asks_an_anonymous_visitor_to_sign_in(self):
        response = self.client.get(reverse('home'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Sign in')
        self.assertNotContains(response, 'Harbor Hawks')
        self.assertNotContains(response, 'Ada Rook')
        self.assertNotContains(response, 'Register')
        self.assertContains(response, 'width=device-width')

    def test_sign_in_and_sign_out_use_the_existing_accounts(self):
        page = self.client.get(reverse('login'))
        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, 'Register')
        refused = self.client.post(reverse('login'), {
            'username': 'owner',
            'password': 'wrong-pass',
        })
        self.assertEqual(refused.status_code, 200)
        self.assertContains(refused, 'Please enter a correct')
        signed_in = self.client.post(reverse('login'), {
            'username': 'owner',
            'password': 'campaign-pass',
        })
        self.assertRedirects(signed_in, reverse('home'))
        home = self.client.get(reverse('home'))
        self.assertContains(home, 'Harbor Hawks')
        self.assertContains(home, 'Opening campaign')
        self.assertContains(home, '2021')
        self.assertContains(home, 'Direction')
        self.assertContains(home, 'Ada Rook')
        self.assertContains(home, 'Week 3')
        self.assertContains(home, 'River City')
        self.assertNotContains(home, 'Quiet Club')
        self.assertNotContains(home, 'Someone else plays')
        self.assertEqual(self.client.get(reverse('logout')).status_code, 405)
        signed_out = self.client.post(reverse('logout'))
        self.assertRedirects(signed_out, reverse('home'))
        after = self.client.get(reverse('home'))
        self.assertNotContains(after, 'Harbor Hawks')

    def test_a_signed_in_account_without_a_campaign_sees_an_empty_state(self):
        stranger = User.objects.create_user('stranger', password='stranger-pass')
        self.client.force_login(stranger)
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'No active campaign')
        self.assertNotContains(response, 'Harbor Hawks')
        self.assertNotContains(response, 'Someone else plays')

    def test_another_participant_does_not_see_this_club(self):
        self.client.force_login(self.other)
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'Someone else plays')
        self.assertContains(response, 'No club is under your control')
        self.assertNotContains(response, 'Harbor Hawks')
        self.assertNotContains(response, 'Ada Rook')
        self.assertNotContains(response, 'River City')

    def test_a_closed_control_does_not_keep_showing_the_roster(self):
        control = TimelineFranchiseControl.objects.get(participant=self.participant)
        control.final_controlled_season = 2021
        control.reason_ended = 'released'
        control.save()
        self.client.force_login(self.owner)
        response = self.client.get(reverse('home'))
        self.assertContains(response, 'No club is under your control')
        self.assertNotContains(response, 'Ada Rook')
        self.assertNotContains(response, 'River City')

    def test_more_than_one_active_campaign_stays_unnamed_as_a_single_club(self):
        second = Timeline.objects.create(
            created_by=self.owner,
            release=self.timeline.release,
            name='Second campaign',
            start_season_year=2021,
            current_season_year=2021,
            season_index=1,
            phase=Timeline.Phase.DIRECTION,
            status=Timeline.Status.ACTIVE,
        )
        participant = TimelineParticipant.objects.create(
            timeline=second,
            user=self.owner,
            status=TimelineParticipant.Status.ACTIVE,
            joined_season=2021,
        )
        TimelineFranchiseControl.objects.create(
            timeline=second,
            participant=participant,
            franchise=self.opponent,
            first_controlled_season=2021,
        )
        page = campaign_for_user(self.owner)
        self.assertEqual(page['state'], 'several')
        self.assertEqual(page['campaigns'], ['Opening campaign', 'Second campaign'])
        self.assertNotIn('franchise_name', page)

    def test_admin_remains_routed(self):
        response = self.client.get('/admin/login/')
        self.assertEqual(response.status_code, 200)
