from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from leagues.models import League
from play.campaign import campaign_for_user
from timelines.models import (
    OwnerDirective,
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
        cls.directive = OwnerDirective.objects.create(
            timeline=cls.timeline,
            participant=cls.participant,
            season=season,
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

    def _direction_post(self, **overrides):
        payload = {
            'team_direction': 'balanced',
            'priority_group_1': '',
            'priority_group_2': '',
            'priority_group_3': '',
            'trade_appetite': 'medium',
            'veteran_preference': 'automatic',
            'draft_participation': 'delegate_to_gm',
            'offensive_emphasis': 'balanced',
            'defensive_emphasis': 'balanced',
            'fourth_down': 'balanced',
            'playing_time': 'automatic',
        }
        payload.update(overrides)
        return payload

    def test_owner_can_view_and_save_direction(self):
        self.client.force_login(self.owner)
        home = self.client.get(reverse('home'))
        self.assertContains(home, 'Set direction')
        page = self.client.get(reverse('direction'))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, 'Harbor Hawks')
        self.assertContains(page, 'Save direction')
        self.assertContains(page, 'value="balanced" selected')
        self.assertContains(page, 'csrfmiddlewaretoken')
        before_roster = RosterMembership.objects.count()
        before_games = list(ScheduleGame.objects.values_list('id', 'home_id', 'away_id'))
        saved = self.client.post(reverse('direction'), self._direction_post(
            team_direction='compete',
            priority_group_1='quarterback',
            trade_appetite='high',
            timeline='999',
            locked='true',
        ), follow=True)
        self.assertRedirects(saved, reverse('direction'))
        self.assertContains(saved, 'Direction saved.')
        self.assertContains(saved, 'value="compete" selected')
        directive = OwnerDirective.objects.get(pk=self.directive.pk)
        self.assertEqual(directive.team_direction, 'compete')
        self.assertEqual(directive.priority_group_1, 'quarterback')
        self.assertIsNone(directive.priority_group_2)
        self.assertEqual(directive.trade_appetite, 'high')
        self.assertFalse(directive.locked)
        self.assertEqual(directive.participant_id, self.participant.pk)
        self.timeline.refresh_from_db()
        season = TimelineSeason.objects.get(timeline=self.timeline)
        self.assertEqual(self.timeline.phase, Timeline.Phase.DIRECTION)
        self.assertEqual(season.phase, Timeline.Phase.DIRECTION)
        self.assertFalse(season.directives_locked)
        self.assertEqual(RosterMembership.objects.count(), before_roster)
        self.assertEqual(
            list(ScheduleGame.objects.values_list('id', 'home_id', 'away_id')),
            before_games,
        )
        self.assertEqual(self.club.display_name, 'Harbor Hawks')

    def test_invalid_direction_values_are_refused(self):
        self.client.force_login(self.owner)
        refused = self.client.post(reverse('direction'), self._direction_post(
            team_direction='win-now',
        ))
        self.assertEqual(refused.status_code, 200)
        self.assertContains(refused, 'Select a valid choice')
        ordered = self.client.post(reverse('direction'), self._direction_post(
            priority_group_2='skill',
        ))
        self.assertContains(ordered, 'Choose a first priority before a second one.')
        repeated = self.client.post(reverse('direction'), self._direction_post(
            priority_group_1='quarterback',
            priority_group_2='quarterback',
        ))
        self.assertContains(repeated, 'Choose each priority group once.')
        directive = OwnerDirective.objects.get(pk=self.directive.pk)
        self.assertEqual(directive.team_direction, 'balanced')
        self.assertIsNone(directive.priority_group_1)
        self.assertIsNone(directive.priority_group_2)

    def test_another_account_cannot_change_this_direction(self):
        self.client.force_login(self.other)
        without_club = self.client.post(reverse('direction'), self._direction_post(
            team_direction='rebuild',
        ))
        self.assertEqual(without_club.status_code, 403)
        self.assertNotContains(without_club, 'Harbor Hawks', status_code=403)
        self.directive.refresh_from_db()
        self.assertEqual(self.directive.team_direction, 'balanced')
        season = TimelineSeason.objects.create(
            timeline=Timeline.objects.get(name='Someone else plays'),
            profile=self.timeline.seasons.get().profile,
            season_year=2021,
            season_index=1,
            phase=Timeline.Phase.DIRECTION,
        )
        other_timeline = season.timeline
        other_timeline.phase = Timeline.Phase.DIRECTION
        other_timeline.save()
        participant = other_timeline.participants.get(user=self.other)
        TimelineFranchiseControl.objects.create(
            timeline=other_timeline,
            participant=participant,
            franchise=self.opponent,
            first_controlled_season=2021,
        )
        other_directive = OwnerDirective.objects.create(
            timeline=other_timeline,
            participant=participant,
            season=season,
        )
        self.client.force_login(self.owner)
        self.client.post(reverse('direction'), self._direction_post(
            team_direction='rebuild',
        ))
        other_directive.refresh_from_db()
        self.assertEqual(other_directive.team_direction, 'balanced')
        self.client.force_login(self.other)
        foreign = self.client.get(reverse('direction'))
        self.assertContains(foreign, 'River City')
        self.assertNotContains(foreign, 'Harbor Hawks')
        self.client.post(reverse('direction'), self._direction_post(
            team_direction='compete',
        ))
        self.directive.refresh_from_db()
        other_directive.refresh_from_db()
        self.assertEqual(self.directive.team_direction, 'rebuild')
        self.assertEqual(other_directive.team_direction, 'compete')

    def test_anonymous_and_closed_direction_cannot_be_saved(self):
        anonymous = self.client.post(reverse('direction'), self._direction_post(
            team_direction='rebuild',
        ))
        self.assertEqual(anonymous.status_code, 403)
        self.assertContains(anonymous, 'Sign in', status_code=403)
        self.directive.refresh_from_db()
        self.assertEqual(self.directive.team_direction, 'balanced')

        self.client.force_login(self.owner)
        self.directive.locked = True
        self.directive.save()
        locked = self.client.post(reverse('direction'), self._direction_post(
            team_direction='rebuild',
        ))
        self.assertEqual(locked.status_code, 403)
        self.assertNotContains(locked, 'Save direction', status_code=403)
        self.directive.refresh_from_db()
        self.assertEqual(self.directive.team_direction, 'balanced')
        self.assertTrue(self.directive.locked)

    def test_direction_stays_put_outside_the_direction_phase(self):
        self.client.force_login(self.owner)
        self.timeline.phase = Timeline.Phase.DRAFT
        self.timeline.save()
        season = self.timeline.seasons.get()
        season.phase = Timeline.Phase.DRAFT
        season.save()
        paused = self.client.post(reverse('direction'), self._direction_post(
            team_direction='rebuild',
        ))
        self.assertEqual(paused.status_code, 403)
        self.directive.refresh_from_db()
        self.assertEqual(self.directive.team_direction, 'balanced')
        self.timeline.refresh_from_db()
        season.refresh_from_db()
        self.assertEqual(self.timeline.phase, Timeline.Phase.DRAFT)
        self.assertEqual(season.phase, Timeline.Phase.DRAFT)

    def test_direction_post_requires_the_csrf_token(self):
        from django.test import Client

        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        refused = client.post(reverse('direction'), self._direction_post(
            team_direction='rebuild',
        ))
        self.assertEqual(refused.status_code, 403)
        self.directive.refresh_from_db()
        self.assertEqual(self.directive.team_direction, 'balanced')
