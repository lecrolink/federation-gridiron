"""Record one complete 1970 opening draft.

The caller supplies every selection. This service checks that list against
the recomputed neutral board, then moves the campaign from direction to
draft. It does not choose a player, read the owner directive to fill a
pick, or repair a partial log.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import DEFAULT_DB_ALIAS, IntegrityError

from timelines.models import (
    RosterMembership,
    Timeline,
    TimelineDraftSelection,
    TimelinePlayer,
    TimelineSeason,
)
from timelines.services.player_lifecycle import (
    OPENING_START_WEEK,
    materialize_draft_class,
    place_drafted_players,
)
from universe.generation.opening_draft_order import (
    FOUNDING_FRANCHISE_COUNT,
    FOUNDING_SLUGS,
    OPENING_DRAFT_ROUNDS,
    OPENING_SEASON_YEAR,
    opening_draft_board,
)
from universe.models import UniverseRelease
from universe.people import POSITIONS, PlayerRatingProfile, UniversePlayer
from universe.release_guard import holding_release_lock
from universe.rules import DraftClass


GENERATOR_VERSION = 'fg-founding-2'
DRAFTABLE_COUNT = OPENING_DRAFT_ROUNDS * FOUNDING_FRANCHISE_COUNT
UNDRAFTED_COUNT = 2 * FOUNDING_FRANCHISE_COUNT
CLASS_SIZE = DRAFTABLE_COUNT + UNDRAFTED_COUNT
OPENING_COUNT = 1378
PLACEMENT_ROLES = ('starter', 'backup', 'taxi')
PLACEMENT_AVAILABILITY = ('available', 'unavailable')
_MISSING = object()


class OpeningDraftRefused(Exception):
    """The draft was not recorded and this call kept no new row."""


class OpeningDraftResult:
    """``recorded`` wrote the board. ``unchanged`` wrote nothing."""

    def __init__(
        self,
        outcome,
        timeline_id,
        season_id,
        selection_count,
        drafted_count,
        undrafted_count,
    ):
        self.outcome = outcome
        self.timeline_id = timeline_id
        self.season_id = season_id
        self.selection_count = selection_count
        self.drafted_count = drafted_count
        self.undrafted_count = undrafted_count


def record_opening_draft(
    *,
    slug,
    seed,
    generator_version,
    user_id,
    name,
    selections,
):
    """Record the 1970 board, or return unchanged when it already matches."""
    parsed = _parse_selections(selections)
    _require_identity(slug, seed, generator_version, user_id, name)
    try:
        with holding_release_lock(slug=slug, using=DEFAULT_DB_ALIAS) as release:
            try:
                return _draft_locked(
                    release,
                    seed,
                    generator_version,
                    user_id,
                    name,
                    parsed,
                )
            except ValidationError as exc:
                raise OpeningDraftRefused(
                    'Opening draft refused: %s' % exc
                ) from exc
            except IntegrityError as exc:
                raise OpeningDraftRefused(
                    'Opening draft refused: the draft conflicts with a stored row.'
                ) from exc
    except UniverseRelease.DoesNotExist:
        raise OpeningDraftRefused(
            'Opening draft refused: the release is missing.'
        ) from None


def _draft_locked(release, seed, generator_version, user_id, name, selections):
    _require_release(release, seed, generator_version)
    user = _require_user(user_id)
    timeline = _lock_timeline(_one_timeline(release, user, name))
    season = _lock_only_season(timeline)
    _require_direction_shell(timeline, season, release, user)
    board = _board(release)
    _require_slot_order(selections, board)
    draft_class, draftable, undrafted = _load_class(release)
    _require_players(selections, draftable)
    franchises = _franchises(release)
    kind = _stored_kind(timeline, season, release, selections, draftable, undrafted)
    if kind == 'unchanged':
        return _result('unchanged', timeline, season)
    if kind != 'source':
        _refuse('The stored draft is neither the direction state nor this board.')
    timeline.phase = Timeline.Phase.DRAFT
    timeline.save()
    season.phase = Timeline.Phase.DRAFT
    season.save()
    materialize_draft_class(timeline, draft_class, season)
    overlays = {
        overlay.player_id: overlay
        for overlay in TimelinePlayer.objects.filter(
            timeline_id=timeline.pk,
            player_id__in=draftable,
        )
    }
    if len(overlays) != DRAFTABLE_COUNT:
        _fail('The draft class did not materialize the draftable prospects.')
    for record in selections:
        TimelineDraftSelection(
            timeline=timeline,
            season=season,
            overall_pick=record['overall_pick'],
            round=record['round'],
            slot_in_round=record['slot_in_round'],
            franchise=franchises[record['franchise_slug']],
            overlay=overlays[record['player_id']],
        ).save()
    place_drafted_players(
        timeline,
        season,
        [
            {
                'player': draftable[record['player_id']],
                'franchise': franchises[record['franchise_slug']],
                'role': record['role'],
                'position': record['position'],
                'availability': record['availability'],
                'start_week': record['start_week'],
            }
            for record in selections
        ],
    )
    if _stored_kind(
        timeline,
        season,
        release,
        selections,
        draftable,
        undrafted,
    ) != 'unchanged':
        _fail('The opening draft did not reach the requested board.')
    return _result('recorded', timeline, season)


def _parse_selections(selections):
    if not isinstance(selections, (list, tuple)):
        _refuse('The opening draft requires 442 selections.')
    if len(selections) != DRAFTABLE_COUNT:
        _refuse('The opening draft requires 442 selections.')
    parsed = []
    for record in selections:
        parsed.append(_parse_selection(record))
    _require_unique_slots(parsed)
    parsed.sort(key=lambda record: record['overall_pick'])
    return tuple(parsed)


def _parse_selection(record):
    overall_pick = _whole(_field(record, 'overall_pick'), 'overall pick')
    round_number = _whole(_field(record, 'round'), 'round')
    slot_in_round = _whole(_field(record, 'slot_in_round'), 'slot')
    franchise_slug = _field(record, 'franchise_slug')
    player_id = _whole(_field(record, 'player_id'), 'player')
    role = _field(record, 'role')
    position = _field(record, 'position')
    availability = _field(record, 'availability')
    start_week = _whole(_field(record, 'start_week'), 'start week')
    if not isinstance(franchise_slug, str) or franchise_slug == '':
        _refuse('A selection is missing its franchise.')
    if player_id < 1:
        _refuse('A selection names a missing player.')
    if role not in PLACEMENT_ROLES:
        _refuse('A drafted player is a starter, backup, or taxi player.')
    if not isinstance(position, str) or position not in POSITIONS:
        _refuse('A drafted player is missing a legal position.')
    if availability not in PLACEMENT_AVAILABILITY:
        _refuse('A drafted player is missing a legal availability.')
    if start_week < 0 or start_week > 32767:
        _refuse('A drafted player is missing a legal start week.')
    return {
        'overall_pick': overall_pick,
        'round': round_number,
        'slot_in_round': slot_in_round,
        'franchise_slug': franchise_slug,
        'player_id': player_id,
        'role': role,
        'position': position,
        'availability': availability,
        'start_week': start_week,
    }


def _require_unique_slots(selections):
    overalls = [record['overall_pick'] for record in selections]
    slots = [(record['round'], record['slot_in_round']) for record in selections]
    players = [record['player_id'] for record in selections]
    if len(set(players)) != len(players):
        _refuse('A player is selected once.')
    if len(set(overalls)) != len(overalls):
        _refuse('An overall pick is selected once.')
    if len(set(slots)) != len(slots):
        _refuse('A round and slot are selected once.')
    for record in selections:
        expected = (record['round'] - 1) * FOUNDING_FRANCHISE_COUNT + record['slot_in_round']
        if (
            record['round'] < 1
            or record['round'] > OPENING_DRAFT_ROUNDS
            or record['slot_in_round'] < 1
            or record['slot_in_round'] > FOUNDING_FRANCHISE_COUNT
            or record['overall_pick'] < 1
            or record['overall_pick'] > DRAFTABLE_COUNT
            or record['overall_pick'] != expected
        ):
            _refuse('The overall pick does not match its round and slot.')


def _require_identity(slug, seed, generator_version, user_id, name):
    if not isinstance(slug, str) or slug == '':
        _refuse('A release slug is required.')
    if isinstance(seed, bool) or not isinstance(seed, int):
        _refuse('The seed must be an integer.')
    if generator_version != GENERATOR_VERSION:
        _refuse('A generator version is required.')
    if isinstance(user_id, bool) or not isinstance(user_id, int):
        _refuse('A user id is required.')
    if not isinstance(name, str) or name == '':
        _refuse('A campaign name is required.')


def _require_release(release, seed, generator_version):
    if release.status != release.Status.PUBLISHED:
        _refuse('The release is not published.')
    if release.seed != seed or release.generator_version != generator_version:
        _refuse('The release identity does not match.')
    if release.first_season_year != OPENING_SEASON_YEAR:
        _refuse('Only the 1970 opening draft is legal for this slice.')


def _require_user(user_id):
    user = get_user_model().objects.filter(pk=user_id).first()
    if user is None:
        _refuse('The user is missing.')
    return user


def _one_timeline(release, user, name):
    found = {}
    creator_rows = Timeline.objects.filter(release_id=release.pk, created_by_id=user.pk)
    participant_rows = Timeline.objects.filter(
        release_id=release.pk,
        participants__user_id=user.pk,
    )
    for timeline in list(creator_rows) + list(participant_rows):
        found[timeline.pk] = timeline
    matches = [timeline for timeline in found.values() if timeline.name == name]
    if len(matches) > 1:
        _refuse(
            'More than one timeline matches this release, user, and campaign name.'
        )
    if len(matches) != 1:
        _refuse('The campaign is missing.')
    return matches[0]


def _lock_timeline(timeline):
    return Timeline.objects.select_for_update().get(pk=timeline.pk)


def _lock_only_season(timeline):
    seasons = list(
        TimelineSeason.objects.select_for_update().filter(
            timeline_id=timeline.pk,
        ).order_by('pk')
    )
    if len(seasons) != 1:
        _refuse('The campaign does not have its single 1970 season.')
    return seasons[0]


def _require_direction_shell(timeline, season, release, user):
    if (
        timeline.status != Timeline.Status.ACTIVE
        or timeline.start_season_year != OPENING_SEASON_YEAR
        or timeline.current_season_year != OPENING_SEASON_YEAR
        or timeline.season_index != 1
        or timeline.completed_at is not None
    ):
        _refuse('The campaign is not the active 1970 opening campaign.')
    if (
        season.season_year != OPENING_SEASON_YEAR
        or season.season_index != 1
        or season.expansion_gate != 'not_expansion'
        or season.setup_complete
        or season.directives_locked
        or season.regular_season_simulated
        or season.postseason_complete
        or season.completed_at is not None
        or season.profile.release_id != release.pk
        or season.profile.first_year > OPENING_SEASON_YEAR
        or season.profile.last_year < OPENING_SEASON_YEAR
    ):
        _refuse('The campaign is not the active 1970 opening campaign.')
    participants = list(timeline.participants.order_by('pk'))
    if len(participants) != 1 or participants[0].user_id != user.pk or (
        participants[0].status != participants[0].Status.ACTIVE
    ):
        _refuse('The campaign is not the active 1970 opening campaign.')
    if timeline.franchise_controls.count() != 1:
        _refuse('The campaign is not the active 1970 opening campaign.')
    directives = list(timeline.directives.order_by('pk'))
    if len(directives) != 1 or directives[0].locked or (
        directives[0].participant_id != participants[0].pk
        or directives[0].season_id != season.pk
    ):
        _refuse('The campaign is not the active 1970 opening campaign.')
    if timeline.phase != season.phase:
        _refuse('The timeline and season phases do not match.')
    if timeline.phase not in (Timeline.Phase.DIRECTION, Timeline.Phase.DRAFT):
        _refuse('The campaign is not in the opening draft transition.')


def _board(release):
    try:
        board = opening_draft_board(release.seed, release.slug)
    except ValueError as exc:
        _refuse('The opening draft order is not the approved neutral order.')
    if len(board) != OPENING_DRAFT_ROUNDS:
        _refuse('The opening draft order is not the approved neutral order.')
    for round_rows in board:
        picks = [row.pick for row in round_rows]
        slugs = [row.franchise_slug for row in round_rows]
        if picks != list(range(1, FOUNDING_FRANCHISE_COUNT + 1)) or (
            set(slugs) != set(FOUNDING_SLUGS)
        ):
            _refuse('The opening draft order is not the approved neutral order.')
    return board


def _require_slot_order(selections, board):
    for record in selections:
        slot = board[record['round'] - 1][record['slot_in_round'] - 1]
        if slot.franchise_slug != record['franchise_slug'] or (
            slot.pick != record['slot_in_round']
        ):
            _refuse('The franchise does not match the opening draft order.')


def _load_class(release):
    draft_class = DraftClass.objects.filter(
        release_id=release.pk,
        season_year=OPENING_SEASON_YEAR,
    ).first()
    if draft_class is None or (
        draft_class.franchise_count != FOUNDING_FRANCHISE_COUNT
        or draft_class.rounds != OPENING_DRAFT_ROUNDS
        or draft_class.draftable_count != DRAFTABLE_COUNT
        or draft_class.undrafted_count != UNDRAFTED_COUNT
    ):
        _refuse('The 1970 draft class is incomplete.')
    players = list(
        draft_class.players.select_related('rating_profile', 'draft_class')
    )
    draftable = {}
    undrafted = {}
    for player in players:
        try:
            player.rating_profile
        except ObjectDoesNotExist:
            _refuse('The 1970 draft class is incomplete.')
        if player.cohort != UniversePlayer.Cohort.DRAFT:
            _refuse('The 1970 draft class is incomplete.')
        if player.draft_pool == UniversePlayer.DraftPool.DRAFTABLE:
            draftable[player.pk] = player
        elif player.draft_pool == UniversePlayer.DraftPool.UNDRAFTED:
            undrafted[player.pk] = player
        else:
            _refuse('The 1970 draft class is incomplete.')
    if len(draftable) != DRAFTABLE_COUNT or len(undrafted) != UNDRAFTED_COUNT:
        _refuse('The 1970 draft class is incomplete.')
    if PlayerRatingProfile.objects.filter(player_id__in=draftable).count() != (
        DRAFTABLE_COUNT
    ):
        _refuse('The 1970 draft class is incomplete.')
    return draft_class, draftable, undrafted


def _require_players(selections, draftable):
    for record in selections:
        player = draftable.get(record['player_id'])
        if player is None:
            _refuse('A selection names a player who is not a 1970 draftable prospect.')
        allowed = {player.position}
        if player.secondary_position_1:
            allowed.add(player.secondary_position_1)
        if player.secondary_position_2:
            allowed.add(player.secondary_position_2)
        if record['position'] not in allowed:
            _refuse('The roster position is not one of that player\'s positions.')
    if {record['player_id'] for record in selections} != set(draftable):
        _refuse('Every 1970 draftable player is selected once.')


def _franchises(release):
    rows = {
        franchise.placeholder_slug: franchise
        for franchise in release.franchises.filter(placeholder_slug__in=FOUNDING_SLUGS)
    }
    if set(rows) != set(FOUNDING_SLUGS):
        _refuse('The founding franchises are incomplete.')
    return rows


def _stored_kind(timeline, season, release, selections, draftable, undrafted):
    timeline.refresh_from_db()
    season.refresh_from_db()
    if timeline.phase != season.phase:
        _refuse('The timeline and season phases do not match.')
    opening_ok = _opening_intact(timeline, season, release)
    if timeline.phase == Timeline.Phase.DIRECTION:
        if not opening_ok or _class_rows_present(timeline):
            _refuse('The stored draft is neither the direction state nor this board.')
        return 'source'
    if timeline.phase != Timeline.Phase.DRAFT:
        _refuse('The campaign is not in the opening draft transition.')
    if not opening_ok or not _target_matches(
        timeline,
        season,
        selections,
        draftable,
        undrafted,
    ):
        _refuse('The stored draft is neither the direction state nor this board.')
    return 'unchanged'


def _class_rows_present(timeline):
    return (
        TimelineDraftSelection.objects.filter(timeline_id=timeline.pk).exists()
        or TimelinePlayer.objects.filter(timeline_id=timeline.pk).exclude(
            player__cohort=UniversePlayer.Cohort.OPENING,
        ).exists()
    )


def _opening_intact(timeline, season, release):
    from universe.rules import OpeningAssignment

    assignments = list(
        OpeningAssignment.objects.filter(release_id=release.pk).select_related(
            'player',
            'player__rating_profile',
        )
    )
    if len(assignments) != OPENING_COUNT:
        return False
    expected = {row.player_id: row for row in assignments}
    overlays = list(
        TimelinePlayer.objects.filter(
            timeline_id=timeline.pk,
            player_id__in=expected,
        ).prefetch_related('season_ratings', 'roster_memberships')
    )
    if len(overlays) != OPENING_COUNT:
        return False
    for overlay in overlays:
        assignment = expected[overlay.player_id]
        try:
            profile = assignment.player.rating_profile
        except ObjectDoesNotExist:
            return False
        if overlay.retired_year is not None or overlay.status != _status_for(assignment.role):
            return False
        ratings = list(overlay.season_ratings.all())
        memberships = list(overlay.roster_memberships.all())
        if len(ratings) != 1 or len(memberships) != 1:
            return False
        rating = ratings[0]
        membership = memberships[0]
        if rating.season_id != season.pk or not _rating_matches(rating, profile):
            return False
        if (
            membership.season_id != season.pk
            or membership.franchise_id != assignment.franchise_id
            or membership.position != assignment.player.position
            or membership.role != assignment.role
            or membership.availability != RosterMembership.Availability.AVAILABLE
            or membership.start_week != OPENING_START_WEEK
            or membership.end_week is not None
            or membership.reason_ended is not None
        ):
            return False
    return True


def _target_matches(timeline, season, selections, draftable, undrafted):
    stored = list(
        TimelineDraftSelection.objects.filter(timeline_id=timeline.pk).select_related(
            'franchise',
            'overlay',
        ).order_by('overall_pick')
    )
    if len(stored) != DRAFTABLE_COUNT:
        return False
    for row, record in zip(stored, selections):
        if (
            row.overall_pick != record['overall_pick']
            or row.round != record['round']
            or row.slot_in_round != record['slot_in_round']
            or row.franchise.placeholder_slug != record['franchise_slug']
            or row.overlay.player_id != record['player_id']
            or row.season_id != season.pk
            or row.timeline_id != timeline.pk
        ):
            return False
    drafted_overlays = {
        overlay.player_id: overlay
        for overlay in TimelinePlayer.objects.filter(
            timeline_id=timeline.pk,
            player_id__in=draftable,
        ).prefetch_related('roster_memberships', 'season_ratings')
    }
    if set(drafted_overlays) != set(draftable):
        return False
    for record in selections:
        overlay = drafted_overlays[record['player_id']]
        memberships = list(overlay.roster_memberships.all())
        ratings = [
            rating for rating in overlay.season_ratings.all()
            if rating.season_id == season.pk
        ]
        if overlay.retired_year is not None or len(memberships) != 1 or len(ratings) != 1:
            return False
        if overlay.status != _status_for(record['role']):
            return False
        membership = memberships[0]
        selected_slug = None
        if membership.franchise_id:
            selected_slug = membership.franchise.placeholder_slug
        if (
            membership.season_id != season.pk
            or selected_slug != record['franchise_slug']
            or membership.position != record['position']
            or membership.role != record['role']
            or membership.availability != record['availability']
            or membership.start_week != record['start_week']
            or membership.end_week is not None
            or membership.reason_ended is not None
        ):
            return False
    unsigned = list(
        TimelinePlayer.objects.filter(
            timeline_id=timeline.pk,
            player_id__in=undrafted,
        ).prefetch_related('roster_memberships', 'season_ratings')
    )
    if len(unsigned) != UNDRAFTED_COUNT:
        return False
    for overlay in unsigned:
        if overlay.status != TimelinePlayer.Status.PROSPECT or overlay.retired_year is not None:
            return False
        if overlay.roster_memberships.exists():
            return False
        if not overlay.season_ratings.filter(season_id=season.pk).exists():
            return False
    if TimelineDraftSelection.objects.filter(timeline_id=timeline.pk).exclude(
        overlay__player_id__in=draftable,
    ).exists():
        return False
    extra = TimelinePlayer.objects.filter(timeline_id=timeline.pk).exclude(
        player__cohort=UniversePlayer.Cohort.OPENING,
    ).exclude(player_id__in=list(draftable) + list(undrafted))
    if extra.exists():
        return False
    return True


def _rating_matches(rating, profile):
    from universe.people import PLAYER_COMPONENTS
    return all(
        getattr(rating, 'current_%s' % component) == getattr(profile, 'initial_%s' % component)
        for component in PLAYER_COMPONENTS
    )


def _status_for(role):
    if role in ('starter', 'backup'):
        return TimelinePlayer.Status.ACTIVE
    return role


def _result(outcome, timeline, season):
    return OpeningDraftResult(
        outcome=outcome,
        timeline_id=timeline.pk,
        season_id=season.pk,
        selection_count=TimelineDraftSelection.objects.filter(timeline_id=timeline.pk).count(),
        drafted_count=DRAFTABLE_COUNT if outcome == 'unchanged' or outcome == 'recorded' else 0,
        undrafted_count=UNDRAFTED_COUNT,
    )


def _field(record, name):
    if isinstance(record, dict):
        if name not in record:
            _refuse('A selection is missing a required field.')
        return record[name]
    if not hasattr(record, name):
        _refuse('A selection is missing a required field.')
    value = getattr(record, name)
    if value is None and name in (
        'role',
        'position',
        'availability',
        'start_week',
        'franchise_slug',
        'player_id',
    ):
        _refuse('A selection is missing a required field.')
    return value


def _whole(value, label):
    if value is _MISSING or isinstance(value, bool) or not isinstance(value, int):
        _refuse('A selection has an illegal %s.' % label)
    return value


def _fail(message):
    raise ValidationError(message)


def _refuse(message):
    raise OpeningDraftRefused('Opening draft refused: %s' % message)
