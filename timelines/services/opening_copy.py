"""Bulk copy of one full opening cohort.

``initialize_opening_state`` uses this path only when all 1,378 opening
overlays are absent. A partial cohort still uses one ``save()`` per row.
Ordinary ``save()`` is unchanged and still runs ``full_clean()``.

Each ``save()`` validated every check constraint with ``Q.check()``, which
issues one SQL statement per constraint, and validated each foreign key with
its own ``EXISTS`` query. That was about 146 statements per opening player.
This module validates scalars and relationships in memory, then inserts the
three large tables with ``bulk_create``.

``OPENING_BULK_BATCH`` is the requested batch size. Ratings have 35 inserted
columns, so 400 rows are 14,000 variables. Django's SQLite backend further
caps a batch at ``max_query_params // column_count`` (999 // columns), which
puts ratings in batches of 28 on SQLite. MariaDB accepts 400. Inserted
primary keys are not taken from ``bulk_create``; overlays are reloaded by
``(timeline_id, player_id)`` before ratings and memberships are built.
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError

from timelines.models import (
    ROSTER_AVAILABILITY,
    PlayerSeasonRating,
    RosterMembership,
    Timeline,
    TimelineFranchiseControl,
    TimelinePlayer,
    _note_deferred,
    _status_for_role,
    deferred_lifecycle,
)
from universe.models import FranchiseSeasonMembership, UniverseFranchise
from universe.people import PLAYER_COMPONENTS, POSITIONS, UniversePlayer

OPENING_BULK_BATCH = 400
OPENING_COHORT = 1378
ROSTERED_COUNT = 1248
FREE_AGENT_COUNT = 130
ROLE_COUNTS = {
    'starter': 494,
    'backup': 546,
    'taxi': 208,
    'free_agent': 130,
}

# Foreign keys omitted from per-row ``clean_fields``. Each one is checked in
# memory by ``RELATION_CHECKS`` before the corresponding insert.
EXCLUDED_FOREIGN_KEYS = {
    'TimelinePlayer': ('timeline', 'player'),
    'PlayerSeasonRating': ('overlay', 'season'),
    'RosterMembership': ('overlay', 'season', 'franchise'),
}
FIELD_CHECKERS = {
    ('TimelinePlayer', 'timeline'): 'timeline',
    ('TimelinePlayer', 'player'): 'player',
    ('PlayerSeasonRating', 'overlay'): 'overlay',
    ('PlayerSeasonRating', 'season'): 'season',
    ('RosterMembership', 'overlay'): 'overlay',
    ('RosterMembership', 'season'): 'season',
    ('RosterMembership', 'franchise'): 'franchise',
}
PRE_INSERT_CHECKS = (
    'timeline',
    'season',
    'player',
    'release',
    'rating_profile',
    'franchise',
    'role',
    'status',
    'position',
    'availability',
)


def _fail(message):
    raise ValidationError(message)


def expected_open_roster_key(overlay_id, end_week):
    """Value the persisted ``open_overlay_key`` expression will store."""
    if end_week is not None:
        return None
    return overlay_id


def expected_open_control_keys(participant_id, franchise_id, final_controlled_season):
    """Values the open participant and franchise expressions will store."""
    if final_controlled_season is not None:
        return None, None
    return participant_id, franchise_id


def verify_open_control_keys(timeline_id, participant_id, franchise_id):
    """Reject an open control whose generated keys are missing or already stored."""
    participant_key, franchise_key = expected_open_control_keys(
        participant_id,
        franchise_id,
        None,
    )
    if participant_key != participant_id or franchise_key != franchise_id:
        _fail(
            'The open control key is the participant, and the open franchise '
            'key is the franchise.'
        )
    if TimelineFranchiseControl.objects.filter(
        open_participant_key=participant_key,
    ).exists():
        _fail('The open participant key is already stored.')
    if TimelineFranchiseControl.objects.filter(
        timeline_id=timeline_id,
        open_franchise_key=franchise_key,
    ).exists():
        _fail('The open franchise key is already stored.')


def bulk_insert_rows(model, rows):
    """Insert one model in documented batches.

    A database constraint failure becomes ``ValidationError`` so the caller's
    transaction rolls the whole cohort back.
    """
    if not rows:
        return
    try:
        model.objects.bulk_create(rows, batch_size=OPENING_BULK_BATCH)
    except IntegrityError as exc:
        raise ValidationError(
            'Opening state conflicts with a stored row.'
        ) from exc


def copy_fresh_opening(timeline, season, assignments, availability_by_player):
    """Insert the full absent cohort. Return False when the cohort is not full.

    The caller is inside ``deferred_lifecycle`` so the exit check sees the
    inserted player ids. This function does not open its own transaction.
    """
    if len(assignments) != OPENING_COHORT:
        return False
    with deferred_lifecycle():
        rows = _build_rows(timeline, season, assignments, availability_by_player)
        context = _context(timeline, season, availability_by_player, rows)
        _validate_before_insert(context)
        bulk_insert_rows(TimelinePlayer, [row.player for row in rows])
        stored = _reload_players(timeline, [row.assignment.player_id for row in rows])
        context['stored'] = stored
        RELATION_CHECKS['overlay'](context)
        bulk_insert_rows(
            context['rating_model'],
            [row.rating for row in rows],
        )
        bulk_insert_rows(RosterMembership, [row.membership for row in rows])
        for overlay in stored.values():
            _note_deferred(overlay.pk)
    return True


def validate_opening_batch(timeline, season, assignments, availability_by_player):
    """Run the in-memory opening checks and return without writing."""
    with deferred_lifecycle():
        rows = _build_rows(timeline, season, assignments, availability_by_player)
        context = _context(timeline, season, availability_by_player, rows)
        _validate_before_insert(context)
    return rows


class _OpeningRow:
    __slots__ = ('assignment', 'player', 'rating', 'membership')

    def __init__(self, assignment, player, rating, membership):
        self.assignment = assignment
        self.player = player
        self.rating = rating
        self.membership = membership


def _context(timeline, season, availability_by_player, rows):
    return {
        'timeline': timeline,
        'season': season,
        'availability': availability_by_player,
        'rows': rows,
        'active_franchise_ids': _active_franchise_ids(timeline, season),
        'stored': None,
        'rating_model': rows[0].rating.__class__ if rows else None,
    }


def _active_franchise_ids(timeline, season):
    return set(
        FranchiseSeasonMembership.objects.filter(
            franchise__release_id=timeline.release_id,
            season_year=season.season_year,
        ).values_list('franchise_id', flat=True)
    )


def _build_rows(timeline, season, assignments, availability_by_player):
    rows = []
    for assignment in assignments:
        player_obj = assignment.player
        profile = getattr(player_obj, 'rating_profile', None)
        status = _status_for_role(assignment.role)
        overlay = TimelinePlayer(
            timeline=timeline,
            player=player_obj,
            status=status,
        )
        rating = PlayerSeasonRating(season=season)
        if profile is not None:
            for component in PLAYER_COMPONENTS:
                setattr(
                    rating,
                    'current_%s' % component,
                    getattr(profile, 'initial_%s' % component),
                )
        membership = RosterMembership(
            season=season,
            franchise=assignment.franchise,
            position=player_obj.position,
            role=assignment.role,
            availability=availability_by_player.get(assignment.player_id),
            start_week=0,
        )
        rows.append(_OpeningRow(assignment, overlay, rating, membership))
    return rows


def _validate_before_insert(context):
    for name in PRE_INSERT_CHECKS:
        RELATION_CHECKS[name](context)
    for row in context['rows']:
        _validate_scalars(row)


def _validate_scalars(row):
    row.player.clean_fields(exclude=EXCLUDED_FOREIGN_KEYS['TimelinePlayer'])
    row.player.clean()
    row.rating.clean_fields(exclude=EXCLUDED_FOREIGN_KEYS['PlayerSeasonRating'])
    row.membership.clean_fields(exclude=EXCLUDED_FOREIGN_KEYS['RosterMembership'])


def _reload_players(timeline, player_ids):
    return {
        overlay.player_id: overlay
        for overlay in TimelinePlayer.objects.filter(
            timeline_id=timeline.pk,
            player_id__in=player_ids,
        )
    }


def _check_timeline(context):
    timeline = context['timeline']
    if timeline.pk is None or timeline.status != Timeline.Status.ACTIVE:
        _fail('The timeline is an active saved campaign.')
    for row in context['rows']:
        if row.player.timeline_id != timeline.pk:
            _fail('The overlay belongs to this timeline.')


def _check_season(context):
    season = context['season']
    timeline = context['timeline']
    if (
        season.pk is None
        or season.timeline_id != timeline.pk
        or season.season_year != timeline.start_season_year
    ):
        _fail('The season belongs to the player\'s timeline.')
    if season.phase == Timeline.Phase.COMPLETE:
        _fail('The season is not complete.')
    for row in context['rows']:
        if row.rating.season_id != season.pk or row.membership.season_id != season.pk:
            _fail('The season belongs to the player\'s timeline.')


def _check_player(context):
    seen = set()
    keys = []
    for row in context['rows']:
        player = row.assignment.player
        if player.pk is None or row.player.player_id != player.pk:
            _fail('The overlay belongs to the shared opening player.')
        if player.cohort == UniversePlayer.Cohort.ARCHIVE:
            _fail('Archive players are not placed on a timeline.')
        if player.cohort != UniversePlayer.Cohort.OPENING:
            _fail('Only the opening cohort is copied onto the opening roster.')
        if player.pk in seen:
            _fail('Opening players are unique.')
        seen.add(player.pk)
        if not player.universe_key:
            _fail('The shared player has a universe key.')
        keys.append(player.universe_key)
    if len(keys) != len(set(keys)):
        _fail('Universe keys are unique.')
    if len(seen) != OPENING_COHORT:
        _fail('Every opening assignment maps to exactly one overlay.')


def _check_release(context):
    release_id = context['timeline'].release_id
    if not release_id:
        _fail('The timeline belongs to a release.')
    for row in context['rows']:
        player = row.assignment.player
        if player.release_id != release_id or row.assignment.release_id != release_id:
            _fail('The player belongs to this timeline\'s release.')
        franchise = row.assignment.franchise
        if franchise is not None and franchise.release_id != release_id:
            _fail('The franchise belongs to this timeline\'s release.')


def _check_rating_profile(context):
    for row in context['rows']:
        profile = getattr(row.assignment.player, 'rating_profile', None)
        if profile is None or profile.player_id != row.assignment.player_id:
            _fail('The shared player has a rating profile.')
        for component in PLAYER_COMPONENTS:
            current = getattr(row.rating, 'current_%s' % component)
            initial = getattr(profile, 'initial_%s' % component)
            potential = getattr(profile, 'potential_%s' % component)
            if current != initial:
                _fail('Current ratings equal the shared initial ratings.')
            if current > 100 or current > potential:
                _fail('A season rating does not exceed shared potential.')


def _check_franchise(context):
    active = context['active_franchise_ids']
    rostered = 0
    free_agents = 0
    for row in context['rows']:
        franchise = row.assignment.franchise
        if row.assignment.role == 'free_agent':
            free_agents += 1
            if franchise is not None or row.membership.franchise_id is not None:
                _fail('A free agent has no franchise.')
            continue
        rostered += 1
        if franchise is None or row.membership.franchise_id != franchise.pk:
            _fail('The roster franchise is the opening assignment franchise.')
        if franchise.release_id != context['timeline'].release_id:
            _fail('The franchise belongs to this timeline\'s release.')
        if franchise.origin != UniverseFranchise.Origin.FOUNDING:
            _fail('An opening roster uses a founding franchise.')
        if franchise.pk not in active:
            _fail('The franchise is active in this season.')
    if rostered != ROSTERED_COUNT or free_agents != FREE_AGENT_COUNT:
        _fail('Opening memberships are 1,248 rostered players and 130 free agents.')


def _check_role(context):
    counts = {}
    for row in context['rows']:
        role = row.assignment.role
        if role not in ROLE_COUNTS or row.membership.role != role:
            _fail('An opening role is starter, backup, taxi, or free agent.')
        counts[role] = counts.get(role, 0) + 1
    if counts != ROLE_COUNTS:
        _fail(
            'Opening role counts are 494 starters, 546 backups, 208 taxi '
            'players, and 130 free agents.'
        )


def _check_status(context):
    for row in context['rows']:
        expected = _status_for_role(row.assignment.role)
        if row.player.status != expected or row.player.retired_year is not None:
            _fail('Lifecycle status matches the opening role.')


def _check_position(context):
    for row in context['rows']:
        player = row.assignment.player
        position = row.membership.position
        if position != player.position:
            _fail('The opening roster position is the player\'s primary position.')
        if position not in POSITIONS:
            _fail('The roster position is a known position.')


def _check_availability(context):
    supplied = context['availability']
    for row in context['rows']:
        availability = supplied.get(row.assignment.player_id)
        if availability not in ROSTER_AVAILABILITY:
            _fail('Availability is available or unavailable.')
        membership = row.membership
        if membership.availability != availability:
            _fail('The membership stores the supplied availability.')
        if (
            membership.start_week != 0
            or membership.end_week is not None
            or membership.reason_ended is not None
        ):
            _fail('An opening membership starts at week 0 and stays open.')


def _check_overlay(context):
    stored = context['stored'] or {}
    expected = [row.assignment.player_id for row in context['rows']]
    if len(stored) != len(expected) or set(stored) != set(expected):
        _fail('Every opening assignment maps to exactly one overlay.')
    timeline_id = context['timeline'].pk
    season_id = context['season'].pk
    for row in context['rows']:
        overlay = stored[row.assignment.player_id]
        if (
            overlay.pk is None
            or overlay.player_id != row.assignment.player_id
            or overlay.timeline_id != timeline_id
            or overlay.status != row.player.status
        ):
            _fail('The rating and membership belong to the saved overlay.')
        row.rating.overlay = overlay
        row.membership.overlay = overlay
        if row.rating.season_id != season_id or row.membership.season_id != season_id:
            _fail('The season belongs to the player\'s timeline.')
        key = expected_open_roster_key(overlay.pk, row.membership.end_week)
        if key != overlay.pk:
            _fail('An opening roster key is the overlay id.')
    keys = [row.membership.overlay_id for row in context['rows']]
    if len(keys) != len(set(keys)):
        _fail('Opening roster keys are unique.')
    existing = set(
        RosterMembership.objects.filter(open_overlay_key__in=keys).values_list(
            'open_overlay_key',
            flat=True,
        )
    )
    if existing:
        _fail('Opening roster keys are unique.')


RELATION_CHECKS = {
    'timeline': _check_timeline,
    'season': _check_season,
    'player': _check_player,
    'release': _check_release,
    'rating_profile': _check_rating_profile,
    'franchise': _check_franchise,
    'overlay': _check_overlay,
    'role': _check_role,
    'status': _check_status,
    'position': _check_position,
    'availability': _check_availability,
}
