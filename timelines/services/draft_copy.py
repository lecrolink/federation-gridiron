"""Bulk copy of one complete, absent draft class and its fresh placements.

``materialize_draft_class`` uses this path only when every player in the
class has no overlay. ``place_drafted_players`` uses it only when every
requested player is still a prospect with no membership. A partial or
mismatched cohort keeps one ``save()`` per row. Ordinary ``save()`` is
unchanged.

The measured MariaDB draft issued 87,132 statements for 442 picks. Almost
all of them were per-row ``full_clean`` work: ``PlayerSeasonRating`` has 33
check constraints, and each ``Q.check()`` is a SELECT inside its own
savepoint. The same pattern repeated for overlays, selections, memberships,
and status updates.

This module validates scalars and relationships in memory, then inserts
with ``bulk_create``. ``DRAFT_BULK_BATCH`` matches the opening-state batch.
Ratings have 35 inserted columns, so SQLite further caps a batch at
``999 // columns``. Database unique and check constraints stay the final
guard. An ``IntegrityError`` is not caught here; the caller's transaction
rolls the whole draft back.
"""

from django.core.exceptions import ObjectDoesNotExist, ValidationError

from timelines.models import (
    ROSTER_AVAILABILITY,
    PlayerSeasonRating,
    RosterMembership,
    Timeline,
    TimelineDraftSelection,
    TimelinePlayer,
    _check_lifecycle,
    _note_deferred,
    _status_for_role,
)
from universe.models import FranchiseSeasonMembership
from universe.people import PLAYER_COMPONENTS, POSITIONS, UniversePlayer

DRAFT_BULK_BATCH = 400
PICK_COUNT = 442
ROUND_COUNT = 17
CLUB_COUNT = 26


def _fail(message):
    raise ValidationError(message)


def bulk_insert_rows(model, rows):
    """Insert one model in documented batches."""
    if not rows:
        return
    model.objects.bulk_create(rows, batch_size=DRAFT_BULK_BATCH)


def copy_absent_draft_class(timeline, season, players):
    """Insert prospect overlays and initial ratings for an absent class."""
    if not players:
        _fail('The draft class has no players.')
    if TimelinePlayer.objects.filter(
        timeline_id=timeline.pk,
        player_id__in=[player.pk for player in players],
    ).exists():
        _fail('A partial draft class is not copied in bulk.')
    rows = _class_rows(timeline, season, players)
    _validate_class_rows(timeline, season, rows)
    bulk_insert_rows(TimelinePlayer, [row.overlay for row in rows])
    stored = {
        overlay.player_id: overlay
        for overlay in TimelinePlayer.objects.filter(
            timeline_id=timeline.pk,
            player_id__in=[row.player.pk for row in rows],
        )
    }
    if len(stored) != len(rows) or set(stored) != {row.player.pk for row in rows}:
        _fail('Every class player maps to exactly one overlay.')
    for row in rows:
        row.rating.overlay = stored[row.player.pk]
    bulk_insert_rows(PlayerSeasonRating, [row.rating for row in rows])


def place_fresh_draft_cohort(timeline, season, placements):
    """Place a uniform fresh cohort. Return False when it is not one.

    False means the caller must keep the one-row path. This function does
    not write before that decision. A fresh cohort that fails validation
    raises ``ValidationError`` and writes nothing.
    """
    from timelines.services.player_lifecycle import (
        DRAFT_PLACEMENT_ROLES,
        _placement_fields,
        _player_pk,
        _reject_complete_phase,
        _require_active,
    )

    parsed = []
    seen = set()
    for record in placements:
        fields = _placement_fields(record)
        player_id = _player_pk(fields['player'])
        if player_id in seen or fields['role'] not in DRAFT_PLACEMENT_ROLES:
            return False
        if not hasattr(fields['franchise'], 'pk') or not fields['franchise'].pk:
            return False
        seen.add(player_id)
        parsed.append((player_id, fields))
    if not parsed:
        return False
    overlays = {
        overlay.player_id: overlay
        for overlay in TimelinePlayer.objects.filter(
            timeline_id=timeline.pk,
            player_id__in=seen,
        ).select_related('player').prefetch_related('roster_memberships')
    }
    if set(overlays) != seen:
        return False
    for player_id, _fields in parsed:
        overlay = overlays[player_id]
        if (
            overlay.status != TimelinePlayer.Status.PROSPECT
            or overlay.retired_year is not None
            or list(overlay.roster_memberships.all())
        ):
            return False
    _require_active(timeline)
    _reject_complete_phase(season)
    _insert_fresh_placements(timeline, season, parsed, overlays)
    return True


def copy_opening_selections(timeline, season, selections, franchises, overlays):
    """Insert the validated 1970 board in overall-pick order."""
    rows = []
    seen_players = set()
    seen_overall = set()
    seen_slots = set()
    ordered = sorted(selections, key=lambda record: record['overall_pick'])
    if len(ordered) != PICK_COUNT:
        _fail('The opening draft requires %s selections.' % PICK_COUNT)
    if TimelineDraftSelection.objects.filter(
        timeline_id=timeline.pk,
        season_id=season.pk,
    ).exists():
        _fail('A partial draft is not copied in bulk.')
    for record in ordered:
        overall = record['overall_pick']
        round_number = record['round']
        slot = record['slot_in_round']
        expected = (round_number - 1) * CLUB_COUNT + slot
        if (
            overall < 1
            or overall > PICK_COUNT
            or round_number < 1
            or round_number > ROUND_COUNT
            or slot < 1
            or slot > CLUB_COUNT
            or overall != expected
        ):
            _fail('The overall pick is (round - 1) * 26 + the slot in the round.')
        if overall in seen_overall or (round_number, slot) in seen_slots:
            _fail('Each opening-draft slot is selected once.')
        player_id = record['player_id']
        if player_id in seen_players:
            _fail('A player is selected once.')
        overlay = overlays.get(player_id)
        franchise = franchises.get(record['franchise_slug'])
        if overlay is None or franchise is None:
            _fail('The selection belongs to this timeline and its release.')
        seen_overall.add(overall)
        seen_slots.add((round_number, slot))
        seen_players.add(player_id)
        row = TimelineDraftSelection(
            timeline=timeline,
            season=season,
            overall_pick=overall,
            round=round_number,
            slot_in_round=slot,
            franchise=franchise,
            overlay=overlay,
        )
        row.clean_fields(exclude=('timeline', 'season', 'franchise', 'overlay'))
        row.clean()
        rows.append(row)
    if len(rows) != PICK_COUNT:
        _fail('The opening draft requires %s selections.' % PICK_COUNT)
    bulk_insert_rows(TimelineDraftSelection, rows)


class _ClassRow:
    __slots__ = ('player', 'overlay', 'rating')

    def __init__(self, player, overlay, rating):
        self.player = player
        self.overlay = overlay
        self.rating = rating


def _class_rows(timeline, season, players):
    rows = []
    for player in players:
        overlay = TimelinePlayer(
            timeline=timeline,
            player=player,
            status=TimelinePlayer.Status.PROSPECT,
        )
        rating = PlayerSeasonRating(overlay=overlay, season=season)
        profile = _profile(player)
        for component in PLAYER_COMPONENTS:
            setattr(
                rating,
                'current_%s' % component,
                getattr(profile, 'initial_%s' % component),
            )
        rows.append(_ClassRow(player, overlay, rating))
    return rows


def _profile(player):
    try:
        profile = player.rating_profile
    except ObjectDoesNotExist:
        profile = None
    if profile is None or profile.player_id != player.pk:
        _fail('The shared player has a rating profile.')
    return profile


def _validate_class_rows(timeline, season, rows):
    if timeline.pk is None or timeline.status != Timeline.Status.ACTIVE:
        _fail('A completed or archived timeline cannot change player state.')
    if (
        season.pk is None
        or season.timeline_id != timeline.pk
        or season.phase == Timeline.Phase.COMPLETE
    ):
        _fail('The season is the draft class year.')
    seen = set()
    for row in rows:
        player = row.player
        if player.pk is None or player.pk in seen:
            _fail('Every class player maps to exactly one overlay.')
        seen.add(player.pk)
        if player.release_id != timeline.release_id:
            _fail('The player belongs to this timeline\'s release.')
        if player.cohort == UniversePlayer.Cohort.ARCHIVE:
            _fail('Archive players are not placed on a timeline.')
        if row.overlay.timeline_id != timeline.pk or row.overlay.player_id != player.pk:
            _fail('The overlay belongs to this timeline.')
        if row.overlay.status != TimelinePlayer.Status.PROSPECT or (
            row.overlay.retired_year is not None
        ):
            _fail('A draft-class player starts as a prospect.')
        row.overlay.clean_fields(exclude=('timeline', 'player'))
        row.overlay.clean()
        profile = _profile(player)
        for component in PLAYER_COMPONENTS:
            current = getattr(row.rating, 'current_%s' % component)
            initial = getattr(profile, 'initial_%s' % component)
            potential = getattr(profile, 'potential_%s' % component)
            if current != initial:
                _fail('Current ratings equal the shared initial ratings.')
            if current > 100 or current > potential:
                _fail('A season rating does not exceed shared potential.')
        if row.rating.season_id != season.pk:
            _fail('The season is the draft class year.')
        row.rating.clean_fields(exclude=('overlay', 'season'))


def _insert_fresh_placements(timeline, season, parsed, overlays):
    active = set(
        FranchiseSeasonMembership.objects.filter(
            franchise__release_id=timeline.release_id,
            season_year=season.season_year,
        ).values_list('franchise_id', flat=True)
    )
    memberships = []
    for player_id, fields in parsed:
        overlay = overlays[player_id]
        player = overlay.player
        franchise = fields['franchise']
        role = fields['role']
        position = fields['position']
        availability = fields['availability']
        start_week = fields['start_week']
        if franchise.release_id != timeline.release_id:
            _fail('The franchise belongs to this timeline\'s release.')
        if franchise.pk not in active:
            _fail('The franchise is active in this season.')
        allowed = {player.position}
        if player.secondary_position_1:
            allowed.add(player.secondary_position_1)
        if player.secondary_position_2:
            allowed.add(player.secondary_position_2)
        if position not in allowed or position not in POSITIONS:
            _fail(
                'The roster position is the player\'s primary position '
                'or a listed secondary.'
            )
        if availability not in ROSTER_AVAILABILITY:
            _fail('Availability is available or unavailable.')
        if (
            isinstance(start_week, bool)
            or not isinstance(start_week, int)
            or start_week < 0
        ):
            _fail('start_week is at least 0.')
        status = _status_for_role(role)
        if status not in (
            TimelinePlayer.Status.ACTIVE,
            TimelinePlayer.Status.TAXI,
        ):
            _fail('A drafted player is a starter, backup, or taxi player.')
        membership = RosterMembership(
            overlay=overlay,
            season=season,
            franchise=franchise,
            position=position,
            role=role,
            availability=availability,
            start_week=start_week,
        )
        membership.clean_fields(exclude=('overlay', 'season', 'franchise'))
        if membership.end_week is not None or membership.reason_ended is not None:
            _fail('A drafted membership stays open.')
        memberships.append((overlay, status, membership))
    for overlay, status, membership in memberships:
        previous = overlay.status
        overlay.status = status
        errors = {}
        _check_lifecycle(overlay, [membership], errors)
        overlay.status = previous
        if errors:
            _fail(next(iter(errors.values())))
    keys = [overlay.pk for overlay, _status, _membership in memberships]
    if len(keys) != len(set(keys)):
        _fail('A player is placed once in the call.')
    if RosterMembership.objects.filter(open_overlay_key__in=keys).exists():
        _fail('A player in the league has one open roster membership.')
    bulk_insert_rows(
        RosterMembership,
        [membership for _overlay, _status, membership in memberships],
    )
    updating = []
    for overlay, status, _membership in memberships:
        overlay.status = status
        updating.append(overlay)
        _note_deferred(overlay.pk)
    TimelinePlayer.objects.bulk_update(
        updating,
        ['status'],
        batch_size=DRAFT_BULK_BATCH,
    )
