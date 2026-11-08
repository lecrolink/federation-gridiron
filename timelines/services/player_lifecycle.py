"""Campaign player lifecycle services.

The approved workflow is docs/PLAYER_LIFECYCLE_SERVICES_PROPOSAL.md.
Shared universe rows are read and never saved. Campaign rows are written
with ``save()`` or, for one guarded rating, ``delete()``. The fresh
1,378-player opening copy is the exception: it validates in memory and
uses ``bulk_create``.
"""

from django.core.exceptions import ValidationError
from django.db import transaction

from timelines.models import (
    DEVELOPMENT_REASONS,
    ROSTER_AVAILABILITY,
    DevelopmentEvent,
    PlayerSeasonRating,
    RosterMembership,
    Timeline,
    TimelinePlayer,
    TimelineSeason,
    _status_for_role,
    deferred_lifecycle,
)
from universe.models import UniverseRelease
from universe.people import PLAYER_COMPONENTS, PlayerRatingProfile
from universe.rules import OpeningAssignment

DRAFT_PLACEMENT_ROLES = ('starter', 'backup', 'taxi')
CLUB_ROLES = ('starter', 'backup', 'taxi', 'reserve')
OPENING_START_WEEK = 0


def _fail(message):
    raise ValidationError(message)


def _require_active(timeline):
    if timeline.status != Timeline.Status.ACTIVE:
        _fail('A completed or archived timeline cannot change player state.')


def _require_published(timeline):
    release = timeline.release
    if release.status != UniverseRelease.Status.PUBLISHED:
        _fail('The release is published.')


def _lock_timeline(timeline):
    return Timeline.objects.select_for_update().get(pk=timeline.pk)


def _player_pk(value):
    if hasattr(value, 'pk'):
        return value.pk
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    _fail('A player id is required.')


def _field(record, name):
    if isinstance(record, dict):
        if name not in record:
            _fail(f'Missing {name}.')
        return record[name]
    if not hasattr(record, name):
        _fail(f'Missing {name}.')
    return getattr(record, name)


def _whole_number(value, label):
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f'{label} is a whole number.')
    return value


def _week(value, label):
    number = _whole_number(value, label)
    if number < 0:
        _fail(f'{label} is at least 0.')
    return number


def _lock_season(season):
    return TimelineSeason.objects.select_for_update().get(pk=season.pk)


def _availability(value):
    if value not in ROSTER_AVAILABILITY:
        _fail('Availability is available or unavailable.')
    return value


def _profile(player_id):
    profile = PlayerRatingProfile.objects.filter(player_id=player_id).first()
    if profile is None:
        _fail('The shared player has a rating profile.')
    return profile


def _component_names():
    return PLAYER_COMPONENTS


def _clamp(value, potential):
    if value < 0:
        return 0
    if value > potential:
        return potential
    return value


def _set_current(rating, source, prefix):
    for component in _component_names():
        setattr(rating, f'current_{component}', getattr(source, f'{prefix}{component}'))


def _same_vector(left, right):
    return all(
        getattr(left, f'current_{component}') == getattr(right, f'current_{component}')
        for component in _component_names()
    )


def _matches_initial(rating, profile):
    return all(
        getattr(rating, f'current_{component}') == getattr(profile, f'initial_{component}')
        for component in _component_names()
    )


def _save_rating_from(overlay, season, source, prefix):
    rating = PlayerSeasonRating(overlay=overlay, season=season)
    _set_current(rating, source, prefix)
    rating.save()
    return rating


def _overlay_for(timeline, player):
    overlay = TimelinePlayer.objects.filter(
        timeline=timeline,
        player_id=_player_pk(player),
    ).first()
    if overlay is None:
        _fail('The player is on this timeline.')
    return overlay


def _open_memberships(overlay):
    return list(overlay.roster_memberships.filter(end_week__isnull=True))


def _open_in_season(overlay, season):
    return overlay.roster_memberships.filter(
        season=season,
        end_week__isnull=True,
    ).first()


def _only_open(overlay):
    rows = _open_memberships(overlay)
    if len(rows) != 1:
        return None
    return rows[0]


def _row_matches(row, *, season, franchise_id, position, role, availability, start_week):
    return (
        row.season_id == season.id
        and row.franchise_id == franchise_id
        and row.position == position
        and row.role == role
        and row.availability == availability
        and row.start_week == start_week
        and row.end_week is None
        and row.reason_ended is None
    )


def _close_membership(row, end_week, reason):
    row.end_week = end_week
    row.reason_ended = reason
    row.save()


def _open_membership(overlay, season, *, franchise, position, role, availability, start_week):
    row = RosterMembership(
        overlay=overlay,
        season=season,
        franchise=franchise,
        position=position,
        role=role,
        availability=availability,
        start_week=start_week,
    )
    row.save()
    return row


def _save_status(overlay, status, retired_year=None):
    overlay.status = status
    overlay.retired_year = retired_year
    overlay.save()


def _season_on_timeline(season, timeline):
    if season.timeline_id != timeline.id:
        _fail('The season belongs to this timeline.')


def _reject_complete_phase(season):
    if season.phase == Timeline.Phase.COMPLETE:
        _fail('The season is not complete.')


def replay_season(overlay, season):
    """Replay one season's rating from its prior and ordered events.

    The returned map is the value of each component after clamping every
    delta. This function does not write.
    """
    rating = PlayerSeasonRating.objects.filter(overlay=overlay, season=season).first()
    if rating is None:
        _fail('The season rating exists.')
    profile = _profile(overlay.player_id)
    has_earlier = PlayerSeasonRating.objects.filter(
        overlay=overlay,
        season__season_year__lt=season.season_year,
    ).exists()
    if has_earlier:
        previous = PlayerSeasonRating.objects.filter(
            overlay=overlay,
            season__season_year=season.season_year - 1,
        ).select_related('season').first()
        if previous is None:
            _fail('The previous season rating exists.')
        if previous.season.phase != Timeline.Phase.COMPLETE:
            _fail('The previous season rating is frozen.')
        prior = {
            component: getattr(previous, f'current_{component}')
            for component in _component_names()
        }
    else:
        prior = {
            component: getattr(profile, f'initial_{component}')
            for component in _component_names()
        }
    grouped = {component: [] for component in _component_names()}
    events = DevelopmentEvent.objects.filter(overlay=overlay, season=season).order_by(
        'event_index',
    )
    for event in events:
        grouped[event.component].append(event)
    replayed = {}
    for component in _component_names():
        value = prior[component]
        potential = getattr(profile, f'potential_{component}')
        for event in grouped[component]:
            value = _clamp(value + event.delta, potential)
        replayed[component] = value
    return replayed


def _replay_matches(overlay, season, rating):
    replayed = replay_season(overlay, season)
    return all(
        getattr(rating, f'current_{component}') == value
        for component, value in replayed.items()
    )


def verify_season_replay(season):
    """Read every stored rating in the season and compare it with replay."""
    problems = {}
    ratings = PlayerSeasonRating.objects.filter(season=season).select_related('overlay')
    for rating in ratings:
        try:
            matches = _replay_matches(rating.overlay, season, rating)
        except ValidationError as exc:
            problems[str(rating.overlay_id)] = exc.messages
            continue
        if not matches:
            problems[str(rating.overlay_id)] = [
                'The stored rating does not match a replay that clamps after every delta.',
            ]
    if problems:
        raise ValidationError(problems)


def initialize_opening_state(timeline, season, availability_by_player):
    """Copy the release's opening assignments into the first season."""
    with deferred_lifecycle():
        timeline = _lock_timeline(timeline)
        season = _lock_season(season)
        _season_on_timeline(season, timeline)
        if season.season_year != timeline.start_season_year:
            _fail('Opening state is copied into the timeline\'s first season.')
        assignments = list(
            OpeningAssignment.objects.filter(release_id=timeline.release_id)
            .select_related('player', 'player__rating_profile', 'franchise')
        )
        supplied = {}
        for key, value in availability_by_player.items():
            supplied[_player_pk(key)] = _availability(value)
        expected_ids = {assignment.player_id for assignment in assignments}
        if set(supplied) != expected_ids:
            _fail('Availability is supplied for every opening player and no one else.')
        overlays = {
            overlay.player_id: overlay
            for overlay in TimelinePlayer.objects.filter(
                timeline=timeline,
                player_id__in=expected_ids,
            )
        }
        pending = []
        for assignment in assignments:
            profile = getattr(assignment.player, 'rating_profile', None)
            if profile is None:
                _fail('The shared player has a rating profile.')
            availability = supplied[assignment.player_id]
            expected_status = _status_for_role(assignment.role)
            overlay = overlays.get(assignment.player_id)
            if overlay is None:
                pending.append((assignment, profile, availability, expected_status, None))
                continue
            ratings = list(overlay.season_ratings.all())
            memberships = list(overlay.roster_memberships.all())
            rating = next((row for row in ratings if row.season_id == season.id), None)
            rating_ok = rating is not None and _matches_initial(rating, profile)
            if (
                overlay.retired_year is None
                and overlay.status == expected_status
                and rating_ok
                and len(ratings) == 1
                and len(memberships) == 1
                and _opening_target_matches(memberships[0], season, assignment, availability)
            ):
                continue
            if (
                overlay.retired_year is None
                and not memberships
                and (not ratings or (len(ratings) == 1 and rating_ok))
            ):
                pending.append((assignment, profile, availability, expected_status, overlay))
                continue
            _fail('Opening state is neither the requested result nor the required source.')
        if not pending:
            return
        _require_active(timeline)
        _require_published(timeline)
        _reject_complete_phase(season)
        if (
            not overlays
            and len(pending) == len(assignments)
            and all(item[4] is None for item in pending)
        ):
            from timelines.services.opening_copy import copy_fresh_opening
            if copy_fresh_opening(timeline, season, assignments, supplied):
                return
        for assignment, profile, availability, expected_status, overlay in pending:
            if overlay is None:
                overlay = TimelinePlayer(
                    timeline=timeline,
                    player_id=assignment.player_id,
                    status=expected_status,
                )
                overlay.save()
            elif overlay.status != expected_status:
                _save_status(overlay, expected_status)
            if not overlay.season_ratings.filter(season=season).exists():
                _save_rating_from(overlay, season, profile, 'initial_')
            _open_membership(
                overlay,
                season,
                franchise=assignment.franchise,
                position=assignment.player.position,
                role=assignment.role,
                availability=availability,
                start_week=OPENING_START_WEEK,
            )


def _opening_target_matches(row, season, assignment, availability):
    return _row_matches(
        row,
        season=season,
        franchise_id=assignment.franchise_id,
        position=assignment.player.position,
        role=assignment.role,
        availability=availability,
        start_week=OPENING_START_WEEK,
    )


def materialize_draft_class(timeline, draft_class, season):
    """Insert prospect overlays for one shared draft class."""
    with transaction.atomic():
        timeline = _lock_timeline(timeline)
        season = _lock_season(season)
        if draft_class.release_id != timeline.release_id:
            _fail('The draft class belongs to this timeline\'s release.')
        _season_on_timeline(season, timeline)
        if season.season_year != draft_class.season_year:
            _fail('The season is the draft class year.')
        release = timeline.release
        if season.season_year < timeline.start_season_year or (
            season.season_year > release.last_season_year
        ):
            _fail('The class year is inside this timeline and its release.')
        players = list(draft_class.players.all())
        to_create = []
        for player in players:
            overlay = TimelinePlayer.objects.filter(timeline=timeline, player=player).first()
            if overlay is None:
                to_create.append((player, _profile(player.pk)))
                continue
            has_rating = overlay.season_ratings.filter(season=season).exists()
            has_membership = overlay.roster_memberships.exists()
            if not has_rating:
                _fail('A draft-class overlay is missing its season rating.')
            if overlay.status == TimelinePlayer.Status.PROSPECT and has_membership:
                _fail('A prospect has no roster membership.')
        if not to_create:
            return
        _require_active(timeline)
        _require_published(timeline)
        _reject_complete_phase(season)
        for player, profile in to_create:
            overlay = TimelinePlayer(
                timeline=timeline,
                player=player,
                status=TimelinePlayer.Status.PROSPECT,
            )
            overlay.save()
            _save_rating_from(overlay, season, profile, 'initial_')


def _placement_fields(record):
    return {
        'player': _field(record, 'player'),
        'franchise': _field(record, 'franchise'),
        'role': _field(record, 'role'),
        'position': _field(record, 'position'),
        'availability': _availability(_field(record, 'availability')),
        'start_week': _week(_field(record, 'start_week'), 'start_week'),
    }


def _placement_matches(overlay, season, fields):
    if overlay.status != _status_for_role(fields['role']) or overlay.retired_year is not None:
        return False
    open_row = _only_open(overlay)
    if open_row is None:
        return False
    return _row_matches(
        open_row,
        season=season,
        franchise_id=fields['franchise'].pk,
        position=fields['position'],
        role=fields['role'],
        availability=fields['availability'],
        start_week=fields['start_week'],
    )


def place_drafted_players(timeline, season, placements):
    """Record roster results for players a draft service has already selected."""
    if not placements:
        return
    with deferred_lifecycle():
        timeline = _lock_timeline(timeline)
        season = _lock_season(season)
        _season_on_timeline(season, timeline)
        seen = set()
        planned = []
        for record in placements:
            fields = _placement_fields(record)
            player_id = _player_pk(fields['player'])
            if player_id in seen:
                _fail('A player is placed once in the call.')
            seen.add(player_id)
            if fields['role'] not in DRAFT_PLACEMENT_ROLES:
                _fail('A drafted player is a starter, backup, or taxi player.')
            if not hasattr(fields['franchise'], 'pk'):
                _fail('A drafted player joins a franchise.')
            overlay = _overlay_for(timeline, fields['player'])
            if _placement_matches(overlay, season, fields):
                continue
            if (
                overlay.status != TimelinePlayer.Status.PROSPECT
                or overlay.retired_year is not None
                or overlay.roster_memberships.exists()
            ):
                _fail('Draft placement is neither the requested result nor a prospect.')
            planned.append((overlay, fields))
        if not planned:
            return
        _require_active(timeline)
        _reject_complete_phase(season)
        for overlay, fields in planned:
            _open_membership(
                overlay,
                season,
                franchise=fields['franchise'],
                position=fields['position'],
                role=fields['role'],
                availability=fields['availability'],
                start_week=fields['start_week'],
            )
            _save_status(overlay, _status_for_role(fields['role']))


def _development_fields(record):
    component = _field(record, 'component')
    if component not in PLAYER_COMPONENTS:
        _fail('The component is one of the 33 rating names.')
    delta = _whole_number(_field(record, 'delta'), 'delta')
    if delta < -20 or delta > 20:
        _fail('A development delta is from -20 through 20.')
    reason = _field(record, 'reason')
    if reason not in DEVELOPMENT_REASONS:
        _fail('A development reason is age, snaps, injury, or coaching.')
    event_index = _whole_number(_field(record, 'event_index'), 'event_index')
    if event_index < 1:
        _fail('A development event index starts at 1.')
    return component, delta, reason, event_index


def record_development(overlay, season, changes):
    """Append ordered development events and move the live season rating."""
    with transaction.atomic():
        timeline = _lock_timeline(overlay.timeline)
        season = _lock_season(season)
        _season_on_timeline(season, timeline)
        overlay = TimelinePlayer.objects.select_for_update().get(pk=overlay.pk)
        rating = (
            PlayerSeasonRating.objects.select_for_update()
            .filter(overlay=overlay, season=season)
            .first()
        )
        if rating is None:
            _fail('The season rating exists.')
        if overlay.retired_year is not None and season.season_year > overlay.retired_year:
            _fail('A retired player has no development after the retirement season.')
        if not _replay_matches(overlay, season, rating):
            _fail('The stored rating does not match the events already recorded.')
        parsed = [_development_fields(record) for record in changes]
        by_component = {component: [] for component in _component_names()}
        for component, delta, reason, event_index in parsed:
            by_component[component].append((delta, reason, event_index))
        profile = _profile(overlay.player_id)
        existing = {}
        for event in DevelopmentEvent.objects.filter(overlay=overlay, season=season):
            existing.setdefault(event.component, {})[event.event_index] = event
        planned = []
        for component in _component_names():
            rows = by_component[component]
            last_index = None
            for _delta, _reason, event_index in rows:
                if last_index is not None and event_index <= last_index:
                    _fail('Development indexes for a component are strictly increasing.')
                last_index = event_index
            stored = existing.get(component, {})
            stored_max = max(stored) if stored else 0
            for delta, reason, event_index in rows:
                current = stored.get(event_index)
                if current is not None:
                    if current.delta != delta or current.reason != reason:
                        _fail('A stored development event does not match the request.')
                    continue
                if event_index <= stored_max:
                    _fail('A development event does not rewrite an earlier index.')
                planned.append((component, delta, reason, event_index))
        if not planned:
            return
        _require_active(timeline)
        _reject_complete_phase(season)
        for component, delta, reason, event_index in planned:
            DevelopmentEvent(
                overlay=overlay,
                season=season,
                component=component,
                delta=delta,
                reason=reason,
                event_index=event_index,
            ).save()
            potential = getattr(profile, f'potential_{component}')
            updated = _clamp(getattr(rating, f'current_{component}') + delta, potential)
            setattr(rating, f'current_{component}', updated)
            rating.save()
        if planned and not _replay_matches(overlay, season, rating):
            _fail('The stored rating does not match a replay that clamps after every delta.')


def _later_rows(overlay, season):
    later_year = season.season_year
    ratings = list(
        overlay.season_ratings.filter(season__season_year__gt=later_year).select_related('season')
    )
    memberships = overlay.roster_memberships.filter(season__season_year__gt=later_year).exists()
    events = overlay.development_events.filter(season__season_year__gt=later_year).exists()
    return ratings, memberships, events


def _retirement_matches(overlay, season, end_week):
    if overlay.status != TimelinePlayer.Status.RETIRED or overlay.retired_year != season.season_year:
        return False
    if overlay.roster_memberships.filter(end_week__isnull=True).exists():
        return False
    closed = overlay.roster_memberships.filter(
        season=season,
        reason_ended='retired',
        end_week=end_week,
    )
    if closed.count() != 1:
        return False
    ratings, memberships, events = _later_rows(overlay, season)
    return not ratings and not memberships and not events


def _premature_rating(overlay, season):
    """Return the unmodified next-season rating, when retirement may remove it."""
    ratings, memberships, events = _later_rows(overlay, season)
    if not ratings and not memberships and not events:
        return None
    current = overlay.season_ratings.filter(season=season).first()
    if (
        len(ratings) == 1
        and ratings[0].season.season_year == season.season_year + 1
        and ratings[0].season.phase != Timeline.Phase.COMPLETE
        and not memberships
        and not events
        and current is not None
        and _same_vector(ratings[0], current)
    ):
        return ratings[0]
    _fail(
        'The player has entered a later season. '
        'Name that season once its membership is the one being closed.'
    )


def retire_player(overlay, season, end_week):
    """Retire one player from the season of the membership being closed."""
    with deferred_lifecycle():
        _retire_player(overlay, season, end_week)


def _retire_player(overlay, season, end_week):
    timeline = _lock_timeline(overlay.timeline)
    season = _lock_season(season)
    _season_on_timeline(season, timeline)
    end_week = _week(end_week, 'end_week')
    overlay = TimelinePlayer.objects.select_for_update().get(pk=overlay.pk)
    if _retirement_matches(overlay, season, end_week):
        return
    if overlay.retired_year is not None:
        _fail('The retirement state does not match the request.')
    if overlay.status == TimelinePlayer.Status.PROSPECT:
        _fail('A prospect cannot be retired.')
    open_row = _open_in_season(overlay, season)
    if open_row is None:
        _fail('Retirement closes an open roster membership.')
    early_rating = _premature_rating(overlay, season)
    _require_active(timeline)
    if early_rating is not None:
        early_rating.delete()
    _close_membership(open_row, end_week, 'retired')
    _save_status(overlay, TimelinePlayer.Status.RETIRED, season.season_year)


def _closed_reason(overlay, season, reason, end_week):
    return overlay.roster_memberships.filter(
        season=season,
        reason_ended=reason,
        end_week=end_week,
    )


def _target_open(overlay, season, *, franchise_id, position, role, availability, start_week):
    if overlay.status != _status_for_role(role) or overlay.retired_year is not None:
        return False
    open_row = _only_open(overlay)
    if open_row is None:
        return False
    return _row_matches(
        open_row,
        season=season,
        franchise_id=franchise_id,
        position=position,
        role=role,
        availability=availability,
        start_week=start_week,
    )


def cut_player(overlay, season, end_week, start_week, availability):
    """Move a club player to an unsigned free-agent row."""
    with deferred_lifecycle():
        timeline = _lock_timeline(overlay.timeline)
        season = _lock_season(season)
        _season_on_timeline(season, timeline)
        end_week = _week(end_week, 'end_week')
        start_week = _week(start_week, 'start_week')
        availability = _availability(availability)
        overlay = TimelinePlayer.objects.select_for_update().get(pk=overlay.pk)
        open_row = _only_open(overlay)
        if (
            open_row is not None
            and overlay.status == TimelinePlayer.Status.FREE_AGENT
            and _row_matches(
                open_row,
                season=season,
                franchise_id=None,
                position=open_row.position,
                role=RosterMembership.Role.FREE_AGENT,
                availability=availability,
                start_week=start_week,
            )
            and _closed_reason(overlay, season, 'cut', end_week).filter(
                position=open_row.position,
            ).exists()
        ):
            return
        if (
            open_row is None
            or open_row.season_id != season.id
            or open_row.franchise_id is None
            or open_row.role == RosterMembership.Role.FREE_AGENT
        ):
            _fail('A cut starts from an open club membership.')
        _require_active(timeline)
        _reject_complete_phase(season)
        position = open_row.position
        _close_membership(open_row, end_week, 'cut')
        _open_membership(
            overlay,
            season,
            franchise=None,
            position=position,
            role=RosterMembership.Role.FREE_AGENT,
            availability=availability,
            start_week=start_week,
        )
        _save_status(overlay, TimelinePlayer.Status.FREE_AGENT)


def _sign_matches(overlay, season, *, franchise, role, position, availability, start_week, end_week):
    if not _target_open(
        overlay,
        season,
        franchise_id=franchise.pk,
        position=position,
        role=role,
        availability=availability,
        start_week=start_week,
    ):
        return False
    if end_week is None:
        return not overlay.roster_memberships.filter(
            season=season,
            end_week__isnull=False,
        ).exists()
    return _closed_reason(overlay, season, 'transaction', end_week).exists()


def sign_player(
    overlay,
    season,
    franchise,
    role,
    position,
    availability,
    start_week,
    end_week=None,
):
    """Give a prospect a first membership, or sign a free agent to a club."""
    with deferred_lifecycle():
        timeline = _lock_timeline(overlay.timeline)
        season = _lock_season(season)
        _season_on_timeline(season, timeline)
        if role not in CLUB_ROLES:
            _fail('A signing uses a club role.')
        availability = _availability(availability)
        start_week = _week(start_week, 'start_week')
        if end_week is not None:
            end_week = _week(end_week, 'end_week')
        if franchise is None:
            _fail('A signing names a franchise.')
        overlay = TimelinePlayer.objects.select_for_update().get(pk=overlay.pk)
        if _sign_matches(
            overlay,
            season,
            franchise=franchise,
            role=role,
            position=position,
            availability=availability,
            start_week=start_week,
            end_week=end_week,
        ):
            return
        open_row = _only_open(overlay)
        prospect = (
            overlay.status == TimelinePlayer.Status.PROSPECT
            and overlay.retired_year is None
            and not overlay.roster_memberships.exists()
        )
        free_agent = (
            open_row is not None
            and open_row.season_id == season.id
            and open_row.role == RosterMembership.Role.FREE_AGENT
            and open_row.franchise_id is None
        )
        if prospect:
            if end_week is not None:
                _fail('A prospect signing has no closed membership.')
        elif free_agent:
            if end_week is None:
                _fail('A free-agent signing closes the open free-agent row.')
        else:
            _fail('A signing starts from a prospect or an open free agent.')
        _require_active(timeline)
        _reject_complete_phase(season)
        if free_agent:
            _close_membership(open_row, end_week, 'transaction')
        _open_membership(
            overlay,
            season,
            franchise=franchise,
            position=position,
            role=role,
            availability=availability,
            start_week=start_week,
        )
        _save_status(overlay, _status_for_role(role))


def _replacement_matches(
    overlay,
    season,
    *,
    reason,
    end_week,
    franchise_id,
    role,
    position,
    availability,
    start_week,
    previous_franchise_differs,
):
    if not _target_open(
        overlay,
        season,
        franchise_id=franchise_id,
        position=position,
        role=role,
        availability=availability,
        start_week=start_week,
    ):
        return False
    closed = _closed_reason(overlay, season, reason, end_week)
    if previous_franchise_differs:
        closed = closed.exclude(franchise_id=franchise_id)
    return closed.exists()


def _replace_membership(
    overlay,
    season,
    *,
    reason,
    end_week,
    franchise,
    role,
    position,
    availability,
    start_week,
    require_different_franchise,
):
    timeline = _lock_timeline(overlay.timeline)
    season = _lock_season(season)
    _season_on_timeline(season, timeline)
    if role not in CLUB_ROLES:
        _fail('The replacement uses a club role.')
    availability = _availability(availability)
    start_week = _week(start_week, 'start_week')
    end_week = _week(end_week, 'end_week')
    if franchise is None:
        _fail('The replacement names a franchise.')
    overlay = TimelinePlayer.objects.select_for_update().get(pk=overlay.pk)
    if _replacement_matches(
        overlay,
        season,
        reason=reason,
        end_week=end_week,
        franchise_id=franchise.pk,
        role=role,
        position=position,
        availability=availability,
        start_week=start_week,
        previous_franchise_differs=require_different_franchise,
    ):
        return
    open_row = _open_in_season(overlay, season)
    if open_row is None or open_row.franchise_id is None or open_row.role == RosterMembership.Role.FREE_AGENT:
        _fail('The replacement starts from an open club membership.')
    differs = (
        open_row.franchise_id != franchise.pk
        or open_row.role != role
        or open_row.position != position
    )
    if require_different_franchise:
        if open_row.franchise_id == franchise.pk:
            _fail('An expansion move changes the franchise.')
    elif not differs:
        _fail('A reassignment changes the franchise, role, or position.')
    _require_active(timeline)
    _reject_complete_phase(season)
    _close_membership(open_row, end_week, reason)
    _open_membership(
        overlay,
        season,
        franchise=franchise,
        position=position,
        role=role,
        availability=availability,
        start_week=start_week,
    )
    _save_status(overlay, _status_for_role(role))


def reassign_player(
    overlay,
    season,
    end_week,
    franchise,
    role,
    position,
    availability,
    start_week,
):
    """Close a club row with ``transaction`` and open the replacement."""
    with deferred_lifecycle():
        _replace_membership(
            overlay,
            season,
            reason='transaction',
            end_week=end_week,
            franchise=franchise,
            role=role,
            position=position,
            availability=availability,
            start_week=start_week,
            require_different_franchise=False,
        )


def expansion_move(
    overlay,
    season,
    end_week,
    franchise,
    role,
    position,
    availability,
    start_week,
):
    """Close a club row with ``expansion`` and open the new club row."""
    with deferred_lifecycle():
        _replace_membership(
            overlay,
            season,
            reason='expansion',
            end_week=end_week,
            franchise=franchise,
            role=role,
            position=position,
            availability=availability,
            start_week=start_week,
            require_different_franchise=True,
        )


def set_availability(overlay, season, availability):
    """Change availability on the open membership without a new row."""
    with transaction.atomic():
        timeline = _lock_timeline(overlay.timeline)
        season = _lock_season(season)
        _season_on_timeline(season, timeline)
        availability = _availability(availability)
        overlay = TimelinePlayer.objects.select_for_update().get(pk=overlay.pk)
        open_row = _open_in_season(overlay, season)
        if open_row is None:
            _fail('Availability changes an open roster membership.')
        if open_row.availability == availability:
            return
        _require_active(timeline)
        _reject_complete_phase(season)
        open_row.availability = availability
        open_row.save()


def _ids_through(timeline, year):
    rating_ids = PlayerSeasonRating.objects.filter(
        overlay__timeline=timeline,
        season__season_year__lte=year,
    ).values_list('overlay_id', flat=True)
    membership_ids = RosterMembership.objects.filter(
        overlay__timeline=timeline,
        season__season_year__lte=year,
    ).values_list('overlay_id', flat=True)
    event_ids = DevelopmentEvent.objects.filter(
        overlay__timeline=timeline,
        season__season_year__lte=year,
    ).values_list('overlay_id', flat=True)
    return set(rating_ids) | set(membership_ids) | set(event_ids)


def _continuation_matches(overlay, completed_season, next_season, fields):
    if overlay.status != _status_for_role(fields['role']) or overlay.retired_year is not None:
        return False
    closed = overlay.roster_memberships.filter(
        season=completed_season,
        reason_ended='season_complete',
        end_week=fields['end_week'],
    )
    if closed.count() != 1:
        return False
    return _target_open(
        overlay,
        next_season,
        franchise_id=fields['franchise_id'],
        position=fields['position'],
        role=fields['role'],
        availability=fields['availability'],
        start_week=fields['start_week'],
    )


def _continuation_fields(record):
    role = _field(record, 'role')
    franchise = _field(record, 'franchise')
    if role == RosterMembership.Role.FREE_AGENT:
        if franchise is not None:
            _fail('A free-agent continuation has no franchise.')
        franchise_id = None
    else:
        if role not in CLUB_ROLES:
            _fail('The next membership uses a roster role.')
        if franchise is None:
            _fail('A club continuation names a franchise.')
        franchise_id = franchise.pk
    return {
        'player': _field(record, 'player'),
        'end_week': _week(_field(record, 'end_week'), 'end_week'),
        'franchise': franchise,
        'franchise_id': franchise_id,
        'role': role,
        'position': _field(record, 'position'),
        'availability': _availability(_field(record, 'availability')),
        'start_week': _week(_field(record, 'start_week'), 'start_week'),
    }


def _retirement_fields(record):
    return {
        'player': _field(record, 'player'),
        'end_week': _week(_field(record, 'end_week'), 'end_week'),
    }


def _next_rating_is_clean_copy(overlay, completed_season, next_season, completed_rating):
    next_rating = overlay.season_ratings.filter(season=next_season).first()
    if next_rating is None:
        return
    if not _same_vector(next_rating, completed_rating):
        _fail('The next season rating is still the frozen copy.')
    if overlay.roster_memberships.filter(season=next_season).exists():
        _fail('The next season has no membership before the handoff.')
    if overlay.development_events.filter(season=next_season).exists():
        _fail('The next season has no development before the handoff.')


def advance_season(completed_season, next_season, retirements, continuations):
    """Retire, copy frozen ratings, and open the next season's memberships."""
    with transaction.atomic():
        timeline = _lock_timeline(completed_season.timeline)
        completed_season = _lock_season(completed_season)
        next_season = _lock_season(next_season)
        _season_on_timeline(completed_season, timeline)
        _season_on_timeline(next_season, timeline)
        if completed_season.phase != Timeline.Phase.COMPLETE:
            _fail('The completed season is in the complete phase.')
        if next_season.season_year != completed_season.season_year + 1:
            _fail('The next season is the following year.')
        verify_season_replay(completed_season)
        retirement_rows = [_retirement_fields(record) for record in retirements]
        continuation_rows = [_continuation_fields(record) for record in continuations]
        named = {}
        for row in retirement_rows:
            player_id = _player_pk(row['player'])
            if player_id in named:
                _fail('A player is named once in the season advance.')
            named[player_id] = ('retire', row)
        for row in continuation_rows:
            player_id = _player_pk(row['player'])
            if player_id in named:
                _fail('A player is named once in the season advance.')
            named[player_id] = ('continue', row)
        open_rows = {
            row.overlay_id: row
            for row in RosterMembership.objects.filter(
                overlay__timeline=timeline,
                season=completed_season,
                end_week__isnull=True,
            )
        }
        for overlay_id in open_rows:
            overlay = TimelinePlayer.objects.get(pk=overlay_id)
            if overlay.player_id not in named:
                _fail('Every open membership in the completed season is retired or continued.')
        belonging = _ids_through(timeline, completed_season.season_year)
        rated = set(
            PlayerSeasonRating.objects.filter(
                overlay__timeline=timeline,
                season=completed_season,
            ).values_list('overlay_id', flat=True)
        )
        non_retired = set(
            TimelinePlayer.objects.filter(timeline=timeline)
            .exclude(status=TimelinePlayer.Status.RETIRED)
            .values_list('id', flat=True)
        )
        if (belonging & non_retired) - rated:
            _fail('Every player in the completed season has a season rating.')
        to_retire = []
        to_continue = []
        for player_id, (kind, row) in named.items():
            overlay = _overlay_for(timeline, player_id)
            if overlay.status == TimelinePlayer.Status.PROSPECT:
                _fail('A prospect is not retired or continued.')
            if overlay.retired_year not in (None, completed_season.season_year):
                _fail('A player retired in another year is not part of this advance.')
            if kind == 'retire':
                if _retirement_matches(overlay, completed_season, row['end_week']):
                    continue
                if overlay.id not in open_rows:
                    _fail('Retirement is neither finished nor an open membership.')
                to_retire.append((overlay, row['end_week']))
                continue
            if _continuation_matches(overlay, completed_season, next_season, row):
                continue
            if overlay.id not in open_rows:
                _fail('Continuation is neither finished nor an open membership.')
            completed_rating = overlay.season_ratings.filter(season=completed_season).first()
            if completed_rating is None:
                _fail('Every player in the completed season has a season rating.')
            _next_rating_is_clean_copy(overlay, completed_season, next_season, completed_rating)
            to_continue.append((overlay, row))
        retiring_ids = {overlay.id for overlay, _end_week in to_retire}
        needs_copy = []
        for overlay_id in belonging:
            if overlay_id in retiring_ids:
                continue
            overlay = TimelinePlayer.objects.get(pk=overlay_id)
            if overlay.status == TimelinePlayer.Status.RETIRED:
                continue
            if overlay.season_ratings.filter(season=next_season).exists():
                continue
            completed_rating = overlay.season_ratings.filter(season=completed_season).first()
            if completed_rating is None:
                _fail('Every player in the completed season has a season rating.')
            needs_copy.append((overlay, completed_rating))
        if not to_retire and not to_continue and not needs_copy:
            return
        _require_active(timeline)
        if next_season.phase == Timeline.Phase.COMPLETE:
            _fail('The next season is not complete.')
        with deferred_lifecycle():
            for overlay, end_week in to_retire:
                retire_player(overlay, completed_season, end_week)
            for overlay, completed_rating in needs_copy:
                _save_rating_from(overlay, next_season, completed_rating, 'current_')
            for overlay, row in to_continue:
                open_row = _open_in_season(overlay, completed_season)
                if open_row is None:
                    _fail('Continuation closes the open membership.')
                _close_membership(open_row, row['end_week'], 'season_complete')
                _open_membership(
                    overlay,
                    next_season,
                    franchise=row['franchise'],
                    position=row['position'],
                    role=row['role'],
                    availability=row['availability'],
                    start_week=row['start_week'],
                )
                fresh = TimelinePlayer.objects.get(pk=overlay.pk)
                _save_status(fresh, _status_for_role(row['role']))
