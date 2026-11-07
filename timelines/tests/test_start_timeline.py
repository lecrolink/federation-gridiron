import socket
import subprocess
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.models.query import QuerySet
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from leagues.models import League
from timelines.management.commands.start_timeline import Command
from timelines.models import (
    DevelopmentEvent,
    ExpansionPin,
    GameKickoffState,
    OwnerDirective,
    PlayerSeasonRating,
    RosterMembership,
    SimulationAuditRecord,
    Timeline,
    TimelineExpansionChoice,
    TimelineFranchiseControl,
    TimelineParticipant,
    TimelinePlayer,
    TimelineSeason,
)
from timelines.services.timeline_start import (
    BACKUP_COUNT,
    FREE_AGENT_COUNT,
    OPENING_COUNT,
    ROSTERED_COUNT,
    STARTER_COUNT,
    TAXI_COUNT,
    TimelineStartRefused,
    start_timeline,
)
from universe.generation.opening_players import generate_opening_players
from universe.generation.player_rating_profiles import generate_player_ratings
from universe.generation.prospects import generate_prospects
from universe.generation.schedule import generate_schedule_release
from universe.models import ProvisionalFranchiseName, UniverseFranchise, UniverseRelease
from universe.people import PLAYER_COMPONENTS, PlayerRatingProfile
from universe.publication_service import publish_universe_release
from universe.rules import DraftClass, OpeningAssignment, OpeningStaffAssignment


SEED = 1970
ROOT = Path(__file__).resolve().parents[2]
WRITE_WORDS = ('INSERT', 'UPDATE', 'DELETE', 'REPLACE')
_ORIGINAL_CONNECT = socket.socket.connect


def _blocked_connect(self, address):
    raise AssertionError('Outbound connections are blocked: %s' % (address,))


def setUpModule():
    socket.socket.connect = _blocked_connect


def tearDownModule():
    socket.socket.connect = _ORIGINAL_CONNECT


def _writes_during(callback):
    statements = []

    def wrap(execute, sql, params, many, context):
        statements.append(sql)
        return execute(sql, params, many, context)

    connection.execute_wrappers.append(wrap)
    try:
        result = callback()
    finally:
        connection.execute_wrappers.remove(wrap)
    writes = [
        sql for sql in statements
        if sql.lstrip().split(None, 1)[0].strip('"').upper() in WRITE_WORDS
    ]
    return result, writes


class StartTimelineSettingsTests(SimpleTestCase):
    def test_settings_do_not_name_the_remote_database(self):
        database = settings.DATABASES['default']
        self.assertEqual(database['ENGINE'], 'django.db.backends.sqlite3')
        self.assertIn('memory', database['NAME'])
        self.assertNotIn('pythonanywhere', str(database))
        self.assertNotIn('mysql', database['ENGINE'])

    def test_the_command_has_no_bypass_options(self):
        parser = Command().create_parser('manage.py', 'start_timeline')
        dests = {action.dest for action in parser._actions}
        self.assertEqual(dests & {
            'slug', 'seed', 'generator', 'user_id', 'name', 'franchise_slug',
        }, {
            'slug', 'seed', 'generator', 'user_id', 'name', 'franchise_slug',
        })
        self.assertFalse(dests & {
            'force', 'skip_validation', 'repair', 'publish', 'limit',
        })
        self.assertTrue(issubclass(Command, BaseCommand))

    def test_cold_import_does_not_cycle_with_publication(self):
        script = (
            'import socket\n'
            'def blocked(self, address):\n'
            '    raise AssertionError("Outbound connections are blocked: %s" % (address,))\n'
            'socket.socket.connect = blocked\n'
            'import os\n'
            'os.environ["FG_DB_PASSWORD"] = "unused-isolated-test"\n'
            'os.environ["DJANGO_SETTINGS_MODULE"] = "federationgridiron.sqlite_test_settings"\n'
            'import django\n'
            'django.setup()\n'
            'import sys\n'
            'import timelines.services.timeline_start\n'
            'assert "universe.publication_service" not in sys.modules\n'
            'import universe.models\n'
            'import timelines.models\n'
            'print("cold-import-ok")\n'
        )
        completed = subprocess.run(
            [sys.executable, '-c', script],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('cold-import-ok', completed.stdout)


class StartTimelineTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.league = League.objects.create(
            name='Timeline Start League',
            slug='timeline-start-league',
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
        publish_universe_release(
            slug=cls.release.slug,
            seed=SEED,
            generator_version='fg-founding-2',
            league_id=cls.league.id,
        )
        cls.release.refresh_from_db()
        cls.user = get_user_model().objects.create_user(
            username='owner',
            password='unused',
        )
        cls.other = get_user_model().objects.create_user(
            username='other',
            password='unused',
        )
        cls.franchise = cls.release.franchises.get(placeholder_slug='founding-01')
        cls.display_name = cls.franchise.display_name
        cls.provisional = ProvisionalFranchiseName.objects.get(
            release=cls.release,
            placeholder_slug='founding-01',
        )
        cls.city = cls.provisional.city
        cls.nickname = cls.provisional.nickname
        cls.city_status = cls.provisional.city_status
        cls.nickname_status = cls.provisional.nickname_status
        cls.catalog = cls.release.catalog_fingerprint
        cls.validation = cls.release.validation_fingerprint

    def _start(self, **overrides):
        kwargs = {
            'slug': self.release.slug,
            'seed': SEED,
            'generator_version': 'fg-founding-2',
            'user_id': self.user.pk,
            'name': 'Harbor',
            'franchise_slug': 'founding-01',
        }
        kwargs.update(overrides)
        return start_timeline(**kwargs)

    def _shell(self, name, user=None):
        return Timeline.objects.create(
            created_by=user or self.user,
            release=self.release,
            name=name,
            start_season_year=1970,
            current_season_year=1970,
            season_index=1,
            phase=Timeline.Phase.DIRECTION,
            status=Timeline.Status.ACTIVE,
        )

    def test_creation_rerun_distinct_names_and_shared_seats(self):
        calls = []
        real_lock = QuerySet.select_for_update

        def spy(qs, *args, **kwargs):
            calls.append(qs.model)
            return real_lock(qs, *args, **kwargs)

        staff_ids = list(
            OpeningStaffAssignment.objects.filter(release=self.release).order_by(
                'pk',
            ).values_list('pk', 'gm_id', 'coach_id')
        )
        draft_classes = DraftClass.objects.filter(release=self.release).count()
        self._shell('Harbor', user=self.other)
        with patch.object(QuerySet, 'select_for_update', spy):
            created, writes = _writes_during(lambda: self._start())
        self.assertEqual(calls[0], UniverseRelease)
        self.assertLess(calls.index(Timeline), calls.index(TimelineSeason))
        self.assertEqual(created.outcome, 'created')
        self.assertEqual(created.player_count, OPENING_COUNT)
        self.assertEqual(created.rating_count, OPENING_COUNT)
        self.assertEqual(created.membership_count, OPENING_COUNT)
        self.assertEqual(created.rostered_count, ROSTERED_COUNT)
        self.assertEqual(created.free_agent_count, FREE_AGENT_COUNT)
        timeline = Timeline.objects.get(pk=created.timeline_id)
        self.assertEqual(timeline.phase, Timeline.Phase.DIRECTION)
        self.assertEqual(timeline.status, Timeline.Status.ACTIVE)
        self.assertEqual(timeline.current_season_year, 1970)
        self.assertEqual(timeline.season_index, 1)
        self.assertEqual(timeline.created_by_id, self.user.pk)
        self.assertIsNone(timeline.completed_at)
        season = TimelineSeason.objects.get(pk=created.season_id)
        self.assertEqual(season.profile.profile_key, 'y1970')
        self.assertEqual(season.phase, Timeline.Phase.DIRECTION)
        self.assertEqual(season.expansion_gate, 'not_expansion')
        self.assertFalse(season.setup_complete)
        self.assertFalse(season.directives_locked)
        self.assertFalse(season.regular_season_simulated)
        self.assertFalse(season.postseason_complete)
        directive = OwnerDirective.objects.get(pk=created.directive_id)
        self.assertFalse(directive.locked)
        self.assertEqual(directive.draft_participation, 'delegate_to_gm')
        self.assertEqual(directive.team_direction, 'balanced')
        self.assertEqual(
            TimelineFranchiseControl.objects.get(pk=created.control_id).franchise_id,
            self.franchise.pk,
        )
        self.assertEqual(timeline.franchise_controls.count(), 1)
        self.assertEqual(self.release.franchises.count(), 32)
        statuses = {
            row['status']: row['n']
            for row in TimelinePlayer.objects.filter(timeline=timeline).values(
                'status',
            ).order_by().annotate(n=models_count())
        }
        # annotate helper below keeps this assertion readable
        self.assertEqual(statuses['active'], STARTER_COUNT + BACKUP_COUNT)
        self.assertEqual(statuses['taxi'], TAXI_COUNT)
        self.assertEqual(statuses['free_agent'], FREE_AGENT_COUNT)
        self.assertNotIn('prospect', statuses)
        roles = {
            row['role']: row['n']
            for row in RosterMembership.objects.filter(
                overlay__timeline=timeline,
            ).values('role').order_by().annotate(n=models_count())
        }
        self.assertEqual(roles['starter'], STARTER_COUNT)
        self.assertEqual(roles['backup'], BACKUP_COUNT)
        self.assertEqual(roles['taxi'], TAXI_COUNT)
        self.assertEqual(roles['free_agent'], FREE_AGENT_COUNT)
        self.assertEqual(
            RosterMembership.objects.filter(
                overlay__timeline=timeline,
                franchise__isnull=True,
                role='free_agent',
                availability='available',
                start_week=0,
                end_week__isnull=True,
            ).count(),
            FREE_AGENT_COUNT,
        )
        self.assertEqual(
            RosterMembership.objects.filter(
                overlay__timeline=timeline,
                franchise__isnull=False,
            ).count(),
            ROSTERED_COUNT,
        )
        self._assert_ratings_match_initial(timeline)
        self.assertEqual(
            TimelinePlayer.objects.filter(timeline=timeline, status='prospect').count(),
            0,
        )
        self.assertEqual(
            DraftClass.objects.get(release=self.release, season_year=1970).players.count(),
            494,
        )
        self.assertFalse(timeline.expansion_choices.exists())
        self.assertFalse(ExpansionPin.objects.filter(choice__timeline=timeline).exists())
        self.assertFalse(timeline.kickoff_states.exists())
        self.assertFalse(
            SimulationAuditRecord.objects.filter(kickoff__timeline=timeline).exists()
        )
        self.assertFalse(DevelopmentEvent.objects.filter(overlay__timeline=timeline).exists())
        self.assertFalse(TimelineExpansionChoice.objects.filter(timeline=timeline).exists())
        self.assertFalse(GameKickoffState.objects.filter(timeline=timeline).exists())
        joined = '\n'.join(writes).lower()
        self.assertIn('timelines_timeline', joined)
        self.assertLess(
            joined.index('timelines_timeline'),
            joined.index('timelines_timelineseason'),
        )
        self.assertLess(
            joined.index('timelines_timelineplayer'),
            joined.rindex('timelines_ownerdirective'),
        )
        self.assertEqual(
            list(OpeningStaffAssignment.objects.filter(release=self.release).order_by(
                'pk',
            ).values_list('pk', 'gm_id', 'coach_id')),
            staff_ids,
        )
        self.release.refresh_from_db()
        self.franchise.refresh_from_db()
        self.provisional.refresh_from_db()
        self.assertEqual(self.release.catalog_fingerprint, self.catalog)
        self.assertEqual(self.release.validation_fingerprint, self.validation)
        self.assertEqual(self.franchise.display_name, self.display_name)
        self.assertEqual(self.provisional.city, self.city)
        self.assertEqual(self.provisional.nickname, self.nickname)
        self.assertEqual(self.provisional.city_status, self.city_status)
        self.assertEqual(self.provisional.nickname_status, self.nickname_status)
        self.assertEqual(
            DraftClass.objects.filter(release=self.release).count(),
            draft_classes,
        )
        self.assertEqual(Timeline.objects.filter(name='Harbor').count(), 2)

        updated_at = Timeline.objects.get(pk=timeline.pk).updated_at
        rerun, rerun_writes = _writes_during(lambda: self._start())
        self.assertEqual(rerun.outcome, 'unchanged')
        self.assertEqual(rerun.timeline_id, created.timeline_id)
        self.assertEqual(rerun.participant_id, created.participant_id)
        self.assertEqual(rerun.control_id, created.control_id)
        self.assertEqual(rerun.season_id, created.season_id)
        self.assertEqual(rerun.directive_id, created.directive_id)
        self.assertEqual(rerun_writes, [])
        self.assertEqual(Timeline.objects.get(pk=timeline.pk).updated_at, updated_at)
        self.assertEqual(Timeline.objects.filter(created_by=self.user).count(), 1)
        with self.assertRaises(TimelineStartRefused):
            self._start(franchise_slug='founding-05')
        self.assertEqual(
            Timeline.objects.filter(name='Harbor', created_by=self.user).count(),
            1,
        )

        second = self._start(name='Inland', franchise_slug='founding-02')
        self.assertEqual(second.outcome, 'created')
        self.assertNotEqual(second.timeline_id, created.timeline_id)
        same_club = self._start(name='Return', franchise_slug='founding-01')
        self.assertEqual(same_club.outcome, 'created')
        self.assertEqual(
            TimelineFranchiseControl.objects.filter(
                timeline__created_by=self.user,
                franchise_id=self.franchise.pk,
                final_controlled_season__isnull=True,
            ).count(),
            2,
        )
        lower = self._start(name='harbor', franchise_slug='founding-03')
        self.assertEqual(lower.outcome, 'created')
        self.assertNotEqual(lower.timeline_id, created.timeline_id)
        composed = 'Caf\u00e9'
        decomposed = 'Cafe\u0301'
        self.assertNotEqual(composed, decomposed)
        first_mark = self._start(name=composed, franchise_slug='founding-04')
        second_mark = self._start(name=decomposed, franchise_slug='founding-04')
        self.assertNotEqual(first_mark.timeline_id, second_mark.timeline_id)
        self.assertEqual(
            Timeline.objects.get(pk=first_mark.timeline_id).name,
            composed,
        )
        self.assertEqual(
            Timeline.objects.get(pk=second_mark.timeline_id).name,
            decomposed,
        )

    def test_ambiguous_and_partial_identities_fail_closed(self):
        first = self._shell('Twin')
        second = self._shell('Twin')
        with self.assertRaises(TimelineStartRefused) as caught:
            self._start(name='Twin')
        self.assertEqual(caught.exception.timeline_ids, (first.pk, second.pk))
        self.assertIn(str(first.pk), str(caught.exception))
        self.assertIn(str(second.pk), str(caught.exception))
        self.assertEqual(Timeline.objects.filter(name='Twin').count(), 2)
        self.assertFalse(TimelineParticipant.objects.exists())

        shell = self._shell('Shell')
        with self.assertRaises(TimelineStartRefused) as missing:
            self._start(name='Shell')
        self.assertIn('no participant', str(missing.exception))
        self.assertEqual(Timeline.objects.filter(name='Shell').count(), 1)
        self.assertFalse(shell.participants.exists())
        self.assertFalse(TimelineSeason.objects.filter(timeline=shell).exists())

        borrowed = self._shell('Borrowed', user=self.other)
        TimelineParticipant.objects.create(
            timeline=borrowed,
            user=self.user,
            status=TimelineParticipant.Status.ACTIVE,
            joined_season=1970,
        )
        with self.assertRaises(TimelineStartRefused):
            self._start(name='Borrowed')
        self.assertEqual(Timeline.objects.filter(name='Borrowed').count(), 1)
        self.assertFalse(TimelineSeason.objects.filter(timeline=borrowed).exists())

        participant = TimelineParticipant.objects.create(
            timeline=shell,
            user=self.user,
            status=TimelineParticipant.Status.ACTIVE,
            joined_season=1970,
        )
        TimelineFranchiseControl.objects.create(
            timeline=shell,
            participant=participant,
            franchise=self.franchise,
            first_controlled_season=1970,
        )
        with self.assertRaises(TimelineStartRefused):
            self._start(name='Shell')
        self.assertFalse(TimelineSeason.objects.filter(timeline=shell).exists())
        self.assertEqual(shell.participants.count(), 1)

    def test_altered_or_extra_state_is_not_repaired(self):
        created = self._start()
        membership = RosterMembership.objects.filter(
            overlay__timeline_id=created.timeline_id,
            role='starter',
        ).order_by('pk').first()
        RosterMembership.objects.filter(pk=membership.pk).update(availability='unavailable')
        with self.assertRaises(TimelineStartRefused):
            self._start()
        membership.refresh_from_db()
        self.assertEqual(membership.availability, 'unavailable')
        self.assertEqual(
            TimelinePlayer.objects.filter(timeline_id=created.timeline_id).count(),
            OPENING_COUNT,
        )
        RosterMembership.objects.filter(pk=membership.pk).update(availability='available')
        overlay = TimelinePlayer.objects.filter(timeline_id=created.timeline_id).order_by(
            'pk',
        ).first()
        season = TimelineSeason.objects.get(pk=created.season_id)
        event = DevelopmentEvent.objects.create(
            overlay=overlay,
            season=season,
            component='speed',
            delta=1,
            reason='coaching',
            event_index=1,
        )
        with self.assertRaises(TimelineStartRefused):
            self._start()
        self.assertTrue(DevelopmentEvent.objects.filter(pk=event.pk).exists())
        self.assertEqual(DevelopmentEvent.objects.count(), 1)

    def test_invalid_requests_do_not_create_rows(self):
        before = Timeline.objects.count()
        draft = UniverseRelease.objects.create(
            slug='fg-draft-start',
            root_league=self.league,
            generator_version='fg-founding-2',
            seed=SEED,
            founding_franchise_count=26,
            expansion_schedule_version=3,
            first_season_year=1970,
            season_count=60,
            last_season_year=2029,
            status=UniverseRelease.Status.DRAFT,
        )
        later = UniverseRelease.objects.create(
            slug='fg-1976-start',
            root_league=self.league,
            generator_version='fg-founding-2',
            seed=SEED,
            founding_franchise_count=26,
            expansion_schedule_version=3,
            first_season_year=1976,
            season_count=60,
            last_season_year=2035,
            catalog_fingerprint='a' * 64,
            validation_fingerprint='b' * 64,
            status=UniverseRelease.Status.PUBLISHED,
            published_at=timezone.now(),
        )
        requests = (
            {'slug': 'missing-release'},
            {'slug': draft.slug},
            {'slug': later.slug},
            {'seed': 1971},
            {'generator_version': 'fg-founding-1'},
            {'user_id': self.user.pk + 1000},
            {'name': ''},
            {'franchise_slug': 'expansion-1976-01'},
            {'franchise_slug': 'founding-99'},
        )
        for overrides in requests:
            with self.assertRaises(TimelineStartRefused):
                self._start(**overrides)
        self.assertEqual(Timeline.objects.count(), before)
        self.assertFalse(Timeline.objects.filter(release=draft).exists())
        self.assertFalse(Timeline.objects.filter(release=later).exists())

    def test_incomplete_opening_data_writes_nothing(self):
        removed = OpeningAssignment.objects.filter(
            release=self.release,
            role='free_agent',
        ).order_by('pk').first()
        OpeningAssignment.objects.filter(pk=removed.pk).delete()
        before = Timeline.objects.count()
        with self.assertRaises(TimelineStartRefused) as caught:
            self._start(name='Incomplete')
        self.assertIn('shared opening data', str(caught.exception))
        self.assertEqual(Timeline.objects.count(), before)

    def test_command_prints_created_and_refuses_cleanly(self):
        stdout = StringIO()
        call_command(
            'start_timeline',
            slug=self.release.slug,
            seed=SEED,
            generator='fg-founding-2',
            user_id=self.user.pk,
            name='Command Harbor',
            franchise_slug='founding-01',
            stdout=stdout,
        )
        text = stdout.getvalue().splitlines()
        self.assertEqual(text[0], 'created')
        self.assertTrue(text[1].startswith('timeline_id='))
        self.assertIn('players=1378', text)
        self.assertIn('rostered=1248', text)
        self.assertIn('free_agents=130', text)
        with self.assertRaises(CommandError) as caught:
            call_command(
                'start_timeline',
                slug='missing-release',
                seed=SEED,
                generator='fg-founding-2',
                user_id=self.user.pk,
                name='Command Harbor',
                franchise_slug='founding-01',
            )
        self.assertIn('release is missing', str(caught.exception))

    def _assert_ratings_match_initial(self, timeline):
        overlays = TimelinePlayer.objects.filter(timeline=timeline).select_related('player')
        profiles = {
            profile.player_id: profile
            for profile in PlayerRatingProfile.objects.filter(
                player__timeline_players__timeline=timeline,
            )
        }
        ratings = {
            rating.overlay_id: rating
            for rating in PlayerSeasonRating.objects.filter(overlay__timeline=timeline)
        }
        self.assertEqual(len(ratings), OPENING_COUNT)
        for overlay in overlays:
            rating = ratings[overlay.pk]
            profile = profiles[overlay.player_id]
            for component in PLAYER_COMPONENTS:
                self.assertEqual(
                    getattr(rating, 'current_%s' % component),
                    getattr(profile, 'initial_%s' % component),
                )


def models_count():
    from django.db.models import Count
    return Count('pk')
