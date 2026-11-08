import socket
from copy import deepcopy
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models.query import QuerySet
from django.test import SimpleTestCase, TestCase

from leagues.models import League
from timelines.models import (
    DevelopmentEvent,
    OwnerDirective,
    RosterMembership,
    Timeline,
    TimelineDraftSelection,
    TimelinePlayer,
    TimelineSeason,
    deferred_lifecycle,
)
from timelines.services.opening_draft import (
    DRAFTABLE_COUNT,
    UNDRAFTED_COUNT,
    OpeningDraftRefused,
    record_opening_draft,
)
from timelines.services.player_lifecycle import place_drafted_players
from timelines.services.timeline_start import OPENING_COUNT, start_timeline
from universe.generation.opening_draft_order import opening_draft_board
from universe.generation.opening_players import generate_opening_players
from universe.generation.player_rating_profiles import generate_player_ratings
from universe.generation.prospects import generate_prospects
from universe.generation.schedule import generate_schedule_release
from universe.models import UniverseRelease
from universe.people import POSITIONS, UniversePlayer
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


def _writes(callback):
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


def _illegal_position(player):
    allowed = {
        player.position,
        player.secondary_position_1,
        player.secondary_position_2,
    }
    for position in POSITIONS:
        if position not in allowed:
            return position
    raise AssertionError('The player lists every position.')


class OpeningDraftContractTests(SimpleTestCase):
    def test_settings_do_not_name_the_remote_database(self):
        database = settings.DATABASES['default']
        self.assertEqual(database['ENGINE'], 'django.db.backends.sqlite3')
        self.assertIn('memory', database['NAME'])
        self.assertNotIn('pythonanywhere', str(database))
        self.assertNotIn('mysql', database['ENGINE'])

    def test_created_at_is_the_insert_time(self):
        model = (ROOT / 'timelines' / 'models.py').read_text()
        service = (ROOT / 'timelines' / 'services' / 'opening_draft.py').read_text()
        self.assertIn(
            'records when this selection row is inserted, not when the surrounding transaction commits',
            model,
        )
        self.assertNotIn('board_rank', service)


class OpeningDraftTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.league = League.objects.create(
            name='Opening Draft League',
            slug='opening-draft-league',
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
            username='draft-owner',
            password='unused',
        )
        started = start_timeline(
            slug=cls.release.slug,
            seed=SEED,
            generator_version='fg-founding-2',
            user_id=cls.user.pk,
            name='Harbor Draft',
            franchise_slug='founding-01',
        )
        cls.timeline = Timeline.objects.get(pk=started.timeline_id)
        cls.season = TimelineSeason.objects.get(pk=started.season_id)
        cls.directive = OwnerDirective.objects.get(timeline=cls.timeline)
        cls.draftable = list(
            UniversePlayer.objects.filter(
                release=cls.release,
                draft_class__season_year=1970,
                draft_pool=UniversePlayer.DraftPool.DRAFTABLE,
            ).order_by('pk')
        )
        cls.undrafted = list(
            UniversePlayer.objects.filter(
                release=cls.release,
                draft_class__season_year=1970,
                draft_pool=UniversePlayer.DraftPool.UNDRAFTED,
            ).order_by('pk')
        )
        cls.opening_player = UniversePlayer.objects.filter(
            release=cls.release,
            cohort=UniversePlayer.Cohort.OPENING,
        ).order_by('pk').first()
        cls.later_player = UniversePlayer.objects.filter(
            release=cls.release,
            cohort=UniversePlayer.Cohort.DRAFT,
            draft_pool=UniversePlayer.DraftPool.DRAFTABLE,
            draft_class__season_year=1971,
        ).order_by('pk').first()
        cls.board = cls._build_board(cls.draftable, cls.release.slug)

    @classmethod
    def _build_board(cls, players, slug):
        if len(players) != DRAFTABLE_COUNT:
            raise AssertionError('The 1970 draftable pool is not 442 players.')
        roles = ('backup', 'starter', 'taxi')
        records = []
        board = opening_draft_board(SEED, slug)
        index = 0
        for round_number, round_rows in enumerate(board, start=1):
            for slot in round_rows:
                player = players[index]
                records.append({
                    'overall_pick': (round_number - 1) * 26 + slot.pick,
                    'round': round_number,
                    'slot_in_round': slot.pick,
                    'franchise_slug': slot.franchise_slug,
                    'player_id': player.pk,
                    'role': roles[index % 3],
                    'position': player.position,
                    'availability': 'unavailable' if index == 4 else 'available',
                    'start_week': 1 if index == 7 else 0,
                })
                index += 1
        return records

    def _call(self, selections=None, **overrides):
        kwargs = {
            'slug': self.release.slug,
            'seed': SEED,
            'generator_version': 'fg-founding-2',
            'user_id': self.user.pk,
            'name': 'Harbor Draft',
            'selections': self.board if selections is None else selections,
        }
        kwargs.update(overrides)
        return record_opening_draft(**kwargs)

    def _replaced(self, **changes):
        records = deepcopy(self.board)
        records[0].update(changes)
        return records

    def _snapshot(self):
        timeline = Timeline.objects.get(pk=self.timeline.pk)
        season = TimelineSeason.objects.get(pk=self.season.pk)
        directive = OwnerDirective.objects.get(pk=self.directive.pk)
        return (
            timeline.phase,
            timeline.updated_at,
            season.phase,
            directive.draft_participation,
            directive.locked,
            directive.team_direction,
            TimelinePlayer.objects.filter(timeline_id=timeline.pk).count(),
            TimelineDraftSelection.objects.filter(timeline_id=timeline.pk).count(),
            RosterMembership.objects.filter(overlay__timeline_id=timeline.pk).count(),
            self._opening_rows(),
        )

    def _opening_rows(self):
        return tuple(
            RosterMembership.objects.filter(
                overlay__timeline_id=self.timeline.pk,
                overlay__player__cohort=UniversePlayer.Cohort.OPENING,
            ).order_by('pk').values_list(
                'pk',
                'franchise_id',
                'position',
                'role',
                'availability',
                'start_week',
                'end_week',
                'reason_ended',
            )
        )

    def _shared_counts(self):
        return (
            UniversePlayer.objects.count(),
            DraftClass.objects.count(),
            OpeningAssignment.objects.count(),
            OpeningStaffAssignment.objects.count(),
            UniversePlayer.objects.filter(origin_draft_pick__isnull=False).count(),
        )

    def _assert_refused(self, selections, fragment):
        before = self._snapshot()
        shared = self._shared_counts()
        with self.assertRaises(OpeningDraftRefused) as caught:
            self._call(selections)
        self.assertIn(fragment, str(caught.exception))
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(self._shared_counts(), shared)

    def test_complete_board_and_exact_rerun(self):
        calls = []
        real_lock = QuerySet.select_for_update

        def spy(qs, *args, **kwargs):
            calls.append(qs.model)
            return real_lock(qs, *args, **kwargs)

        shared = self._shared_counts()
        opening = self._opening_rows()
        directive = (
            self.directive.pk,
            self.directive.draft_participation,
            self.directive.team_direction,
            self.directive.locked,
        )
        with patch_select(spy):
            result, writes = _writes(self._call)
        self.assertEqual(calls[0], UniverseRelease)
        self.assertLess(calls.index(Timeline), calls.index(TimelineSeason))
        self.assertEqual(result.outcome, 'recorded')
        self.assertTrue(writes)
        self.assertEqual(result.selection_count, DRAFTABLE_COUNT)
        self.assertEqual(result.drafted_count, DRAFTABLE_COUNT)
        self.assertEqual(result.undrafted_count, UNDRAFTED_COUNT)
        timeline = Timeline.objects.get(pk=self.timeline.pk)
        season = TimelineSeason.objects.get(pk=self.season.pk)
        self.assertEqual(timeline.phase, Timeline.Phase.DRAFT)
        self.assertEqual(season.phase, Timeline.Phase.DRAFT)
        self.assertEqual(timeline.current_season_year, 1970)
        self.assertEqual(season.season_index, 1)
        self.assertFalse(season.setup_complete)
        self.assertFalse(season.directives_locked)
        self.assertEqual(
            TimelinePlayer.objects.filter(timeline=timeline).count(),
            OPENING_COUNT + DRAFTABLE_COUNT + UNDRAFTED_COUNT,
        )
        self.assertEqual(
            RosterMembership.objects.filter(overlay__timeline=timeline).count(),
            OPENING_COUNT + DRAFTABLE_COUNT,
        )
        self.assertEqual(self._opening_rows(), opening)
        self.assertEqual(self._shared_counts(), shared)
        self.assertFalse(
            RosterMembership.objects.filter(
                overlay__timeline=timeline,
                overlay__player__draft_pool=UniversePlayer.DraftPool.UNDRAFTED,
            ).exists()
        )
        self.assertFalse(
            TimelineDraftSelection.objects.filter(
                timeline=timeline,
                overlay__player__draft_pool=UniversePlayer.DraftPool.UNDRAFTED,
            ).exists()
        )
        self.assertFalse(DevelopmentEvent.objects.exists())
        stored_directive = OwnerDirective.objects.get(pk=self.directive.pk)
        self.assertEqual(
            (
                stored_directive.pk,
                stored_directive.draft_participation,
                stored_directive.team_direction,
                stored_directive.locked,
            ),
            directive,
        )
        self.assertEqual(stored_directive.draft_participation, 'delegate_to_gm')
        drafted = RosterMembership.objects.filter(
            overlay__timeline=timeline,
            overlay__player__draft_pool=UniversePlayer.DraftPool.DRAFTABLE,
        )
        self.assertEqual(drafted.filter(role='backup').count(), 148)
        self.assertEqual(drafted.filter(role='starter').count(), 147)
        self.assertEqual(drafted.filter(role='taxi').count(), 147)
        self.assertEqual(drafted.filter(availability='unavailable').count(), 1)
        self.assertEqual(drafted.filter(start_week=1).count(), 1)
        selection = TimelineDraftSelection.objects.get(timeline=timeline, overall_pick=1)
        created_at = tuple(
            TimelineDraftSelection.objects.filter(timeline=timeline).order_by(
                'overall_pick',
            ).values_list('pk', 'created_at')
        )
        child_ids = (
            tuple(TimelinePlayer.objects.filter(timeline=timeline).order_by('pk').values_list('pk', flat=True)),
            tuple(RosterMembership.objects.filter(overlay__timeline=timeline).order_by('pk').values_list('pk', flat=True)),
        )
        updated_at = timeline.updated_at
        original_round = selection.round

        def forbid_materialize(*args, **kwargs):
            raise AssertionError('The exact rerun materialized the class again.')

        from timelines.services import opening_draft
        with patch_object(opening_draft, 'materialize_draft_class', forbid_materialize):
            rerun, rerun_writes = _writes(self._call)
        self.assertEqual(rerun.outcome, 'unchanged')
        self.assertEqual(rerun.timeline_id, result.timeline_id)
        self.assertEqual(rerun.season_id, result.season_id)
        self.assertEqual(rerun_writes, [])
        timeline.refresh_from_db()
        self.assertEqual(timeline.updated_at, updated_at)
        self.assertEqual(
            tuple(
                TimelineDraftSelection.objects.filter(timeline=timeline).order_by(
                    'overall_pick',
                ).values_list('pk', 'created_at')
            ),
            created_at,
        )
        self.assertEqual(
            (
                tuple(TimelinePlayer.objects.filter(timeline=timeline).order_by('pk').values_list('pk', flat=True)),
                tuple(RosterMembership.objects.filter(overlay__timeline=timeline).order_by('pk').values_list('pk', flat=True)),
            ),
            child_ids,
        )
        selection.round = original_round + 1
        with self.assertRaises(ValidationError):
            selection.save()
        selection.refresh_from_db()
        self.assertEqual(selection.round, original_round)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                TimelineDraftSelection.objects.bulk_create([
                    TimelineDraftSelection(
                        timeline_id=selection.timeline_id,
                        season_id=selection.season_id,
                        overall_pick=1,
                        round=1,
                        slot_in_round=2,
                        franchise_id=selection.franchise_id,
                        overlay_id=selection.overlay_id,
                    ),
                ])

    def test_owner_selects_is_preserved_and_not_used(self):
        OwnerDirective.objects.filter(pk=self.directive.pk).update(
            draft_participation='owner_selects',
        )
        result = self._call()
        self.assertEqual(result.outcome, 'recorded')
        directive = OwnerDirective.objects.get(pk=self.directive.pk)
        self.assertEqual(directive.draft_participation, 'owner_selects')
        self.assertEqual(
            TimelineDraftSelection.objects.filter(timeline_id=self.timeline.pk).count(),
            DRAFTABLE_COUNT,
        )

    def test_empty_partial_and_extra_boards_refuse(self):
        self._assert_refused([], '442 selections')
        self._assert_refused(self.board[:441], '442 selections')
        self._assert_refused(self.board + [dict(self.board[0])], '442 selections')

    def test_duplicate_player_and_slot_and_bad_arithmetic_refuse(self):
        duplicated_player = deepcopy(self.board)
        duplicated_player[1]['player_id'] = duplicated_player[0]['player_id']
        self._assert_refused(duplicated_player, 'selected once')
        duplicated_slot = deepcopy(self.board)
        duplicated_slot[1]['round'] = duplicated_slot[0]['round']
        duplicated_slot[1]['slot_in_round'] = duplicated_slot[0]['slot_in_round']
        self._assert_refused(duplicated_slot, 'round and slot')
        arithmetic = deepcopy(self.board)
        arithmetic[0]['slot_in_round'] = 2
        arithmetic[1]['slot_in_round'] = 1
        self._assert_refused(arithmetic, 'round and slot')

    def test_wrong_franchise_refuses(self):
        self._assert_refused(
            self._replaced(franchise_slug=self.board[1]['franchise_slug']),
            'opening draft order',
        )

    def _archive_player(self):
        # fg-founding-2 stores no archive cohort. Clone one opening identity
        # into that cohort so the refusal is about a real archive row.
        source = self.opening_player
        fields = {
            field.attname: getattr(source, field.attname)
            for field in source._meta.concrete_fields
            if not field.primary_key
        }
        fields['universe_key'] = 'p-fg-1970-1-archive-smoke'
        fields['cohort'] = UniversePlayer.Cohort.ARCHIVE
        fields['draft_pool'] = None
        fields['draft_class_id'] = None
        UniversePlayer.objects.bulk_create([UniversePlayer(**fields)])
        return UniversePlayer.objects.get(universe_key=fields['universe_key'])

    def test_undrafted_opening_archive_and_wrong_class_players_refuse(self):
        self._assert_refused(
            self._replaced(player_id=self.undrafted[0].pk),
            'not a 1970 draftable prospect',
        )
        self._assert_refused(
            self._replaced(player_id=self.opening_player.pk),
            'not a 1970 draftable prospect',
        )
        self._assert_refused(
            self._replaced(player_id=self._archive_player().pk),
            'not a 1970 draftable prospect',
        )
        self._assert_refused(
            self._replaced(player_id=self.later_player.pk),
            'not a 1970 draftable prospect',
        )

    def test_missing_and_illegal_placement_fields_refuse(self):
        missing = deepcopy(self.board)
        del missing[0]['position']
        self._assert_refused(missing, 'required field')
        self._assert_refused(self._replaced(role='reserve'), 'starter, backup, or taxi')
        self._assert_refused(self._replaced(role='free_agent'), 'starter, backup, or taxi')
        self._assert_refused(self._replaced(availability='questionable'), 'availability')
        self._assert_refused(self._replaced(start_week=-1), 'start week')
        self._assert_refused(
            self._replaced(position=_illegal_position(self.draftable[0])),
            'positions',
        )

    def test_partial_overlay_is_left_untouched(self):
        overlay = TimelinePlayer.objects.create(
            timeline=self.timeline,
            player=self.undrafted[0],
            status=TimelinePlayer.Status.PROSPECT,
        )
        self._assert_refused(self.board, 'neither the direction state nor this board')
        self.assertTrue(TimelinePlayer.objects.filter(pk=overlay.pk).exists())
        self.assertFalse(overlay.season_ratings.exists())
        self.assertFalse(TimelineDraftSelection.objects.exists())

    def test_partial_pick_is_left_untouched(self):
        overlay = TimelinePlayer.objects.create(
            timeline=self.timeline,
            player=self.draftable[0],
            status=TimelinePlayer.Status.PROSPECT,
        )
        selection = TimelineDraftSelection.objects.create(
            timeline=self.timeline,
            season=self.season,
            overall_pick=1,
            round=1,
            slot_in_round=1,
            franchise=self.release.franchises.get(placeholder_slug='founding-01'),
            overlay=overlay,
        )
        self._assert_refused(self.board, 'neither the direction state nor this board')
        self.assertTrue(TimelineDraftSelection.objects.filter(pk=selection.pk).exists())
        self.assertEqual(TimelineDraftSelection.objects.count(), 1)

    def test_partial_membership_is_left_untouched(self):
        with deferred_lifecycle():
            overlay = TimelinePlayer(
                timeline=self.timeline,
                player=self.draftable[0],
                status=TimelinePlayer.Status.ACTIVE,
            )
            overlay.save()
            membership = RosterMembership(
                overlay=overlay,
                season=self.season,
                franchise=self.release.franchises.get(placeholder_slug='founding-01'),
                position=self.draftable[0].position,
                role=RosterMembership.Role.STARTER,
                availability=RosterMembership.Availability.AVAILABLE,
                start_week=0,
            )
            membership.save()
        self._assert_refused(self.board, 'neither the direction state nor this board')
        self.assertTrue(RosterMembership.objects.filter(pk=membership.pk).exists())
        self.assertFalse(TimelineDraftSelection.objects.exists())

    def test_mismatched_phases_refuse(self):
        TimelineSeason.objects.filter(pk=self.season.pk).update(phase=Timeline.Phase.STAFF)
        self._assert_refused(self.board, 'phases do not match')
        season = TimelineSeason.objects.get(pk=self.season.pk)
        timeline = Timeline.objects.get(pk=self.timeline.pk)
        self.assertEqual(season.phase, Timeline.Phase.STAFF)
        self.assertEqual(timeline.phase, Timeline.Phase.DIRECTION)

    def test_different_permutation_refuses_without_repair(self):
        first = self._call()
        self.assertEqual(first.outcome, 'recorded')
        stored = tuple(
            TimelineDraftSelection.objects.filter(timeline_id=self.timeline.pk).order_by(
                'overall_pick',
            ).values_list('pk', 'overlay__player_id')
        )
        swapped = deepcopy(self.board)
        swapped[0]['player_id'], swapped[1]['player_id'] = (
            swapped[1]['player_id'],
            swapped[0]['player_id'],
        )
        swapped[0]['position'], swapped[1]['position'] = (
            swapped[1]['position'],
            swapped[0]['position'],
        )
        before = self._snapshot()
        with self.assertRaises(OpeningDraftRefused) as caught:
            self._call(swapped)
        self.assertIn('neither the direction state nor this board', str(caught.exception))
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(
            tuple(
                TimelineDraftSelection.objects.filter(timeline_id=self.timeline.pk).order_by(
                    'overall_pick',
                ).values_list('pk', 'overlay__player_id')
            ),
            stored,
        )

    def test_integrity_error_rolls_the_draft_back(self):
        opening = self._opening_rows()
        shared = self._shared_counts()
        seen = {}

        def inject(timeline, season, placements):
            place_drafted_players(timeline, season, placements)
            seen['during'] = (
                Timeline.objects.get(pk=timeline.pk).phase,
                TimelineSeason.objects.get(pk=season.pk).phase,
                TimelinePlayer.objects.filter(timeline_id=timeline.pk).count(),
                TimelineDraftSelection.objects.filter(timeline_id=timeline.pk).count(),
                RosterMembership.objects.filter(overlay__timeline_id=timeline.pk).count(),
            )
            first = TimelineDraftSelection.objects.get(
                timeline_id=timeline.pk,
                overall_pick=1,
            )
            TimelineDraftSelection.objects.bulk_create([
                TimelineDraftSelection(
                    timeline_id=first.timeline_id,
                    season_id=first.season_id,
                    overall_pick=first.overall_pick,
                    round=first.round,
                    slot_in_round=first.slot_in_round,
                    franchise_id=first.franchise_id,
                    overlay_id=first.overlay_id,
                ),
            ])

        from timelines.services import opening_draft
        with patch_object(opening_draft, 'place_drafted_players', inject):
            with self.assertRaises(OpeningDraftRefused) as caught:
                self._call()
        self.assertIn('conflicts with a stored row', str(caught.exception))
        self.assertEqual(
            seen['during'],
            (
                Timeline.Phase.DRAFT,
                Timeline.Phase.DRAFT,
                OPENING_COUNT + DRAFTABLE_COUNT + UNDRAFTED_COUNT,
                DRAFTABLE_COUNT,
                OPENING_COUNT + DRAFTABLE_COUNT,
            ),
        )
        timeline = Timeline.objects.get(pk=self.timeline.pk)
        season = TimelineSeason.objects.get(pk=self.season.pk)
        self.assertEqual(timeline.phase, Timeline.Phase.DIRECTION)
        self.assertEqual(season.phase, Timeline.Phase.DIRECTION)
        self.assertEqual(
            TimelinePlayer.objects.filter(timeline_id=timeline.pk).count(),
            OPENING_COUNT,
        )
        self.assertFalse(
            TimelinePlayer.objects.filter(timeline_id=timeline.pk).exclude(
                player__cohort=UniversePlayer.Cohort.OPENING,
            ).exists()
        )
        self.assertFalse(TimelineDraftSelection.objects.filter(timeline_id=timeline.pk).exists())
        self.assertEqual(
            RosterMembership.objects.filter(overlay__timeline_id=timeline.pk).count(),
            OPENING_COUNT,
        )
        self.assertEqual(self._opening_rows(), opening)
        self.assertEqual(self._shared_counts(), shared)


def patch_select(spy):
    return patch_object(QuerySet, 'select_for_update', spy)


def patch_object(target, name, replacement):
    from unittest.mock import patch
    if isinstance(target, type):
        return patch.object(target, name, replacement)
    return patch.object(target, name, replacement)
