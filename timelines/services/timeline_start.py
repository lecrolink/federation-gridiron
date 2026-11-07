"""Start one human 1970 campaign on a published fg-founding-2 release.

The service locks that release for the whole transaction, then either
returns an exact existing campaign or creates one. It does not publish a
release, materialize the 1970 draft class, or copy staff seats.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.db import DEFAULT_DB_ALIAS

from timelines.models import (
    DevelopmentEvent,
    ExpansionPin,
    OwnerDirective,
    PlayerSeasonRating,
    RosterMembership,
    SimulationAuditRecord,
    Timeline,
    TimelineFranchiseControl,
    TimelineParticipant,
    TimelinePlayer,
    TimelineSeason,
)
from timelines.services.player_lifecycle import initialize_opening_state
from universe.generation.opening_draft_order import (
    FOUNDING_SLUGS,
    opening_draft_order,
)
from universe.models import FranchiseSeasonMembership, UniverseFranchise, UniverseRelease
from universe.people import PLAYER_COMPONENTS, PlayerRatingProfile
from universe.release_guard import holding_release_lock
from universe.rules import DraftClass, EraRuleProfile, OpeningAssignment, OpeningStaffAssignment


SEASON_YEAR = 1970
GENERATOR_VERSION = 'fg-founding-2'
PROFILE_KEY = 'y1970'
OPENING_COUNT = 1378
STARTER_COUNT = 494
BACKUP_COUNT = 546
TAXI_COUNT = 208
FREE_AGENT_COUNT = 130
ROSTERED_COUNT = 1248
STAFF_SEATS = 26
DRAFT_ROUNDS = 17
DRAFTABLE_COUNT = 442
UNDRAFTED_COUNT = 52
CLASS_SIZE = 494
ROLE_COUNTS = {
    'starter': STARTER_COUNT,
    'backup': BACKUP_COUNT,
    'taxi': TAXI_COUNT,
    'free_agent': FREE_AGENT_COUNT,
}
DIRECTIVE_DEFAULTS = {
    'team_direction': 'balanced',
    'priority_group_1': None,
    'priority_group_2': None,
    'priority_group_3': None,
    'trade_appetite': 'medium',
    'veteran_preference': 'automatic',
    'draft_participation': 'delegate_to_gm',
    'offensive_emphasis': 'balanced',
    'defensive_emphasis': 'balanced',
    'fourth_down': 'balanced',
    'playing_time': 'automatic',
    'locked': False,
}


class TimelineStartRefused(Exception):
    """The campaign was not started and this call kept no new row."""

    def __init__(self, message, timeline_ids=()):
        super().__init__(message)
        self.timeline_ids = tuple(timeline_ids)


class TimelineStartResult:
    """``created`` inserted one campaign. ``unchanged`` wrote nothing."""

    def __init__(
        self,
        outcome,
        timeline_id,
        participant_id,
        control_id,
        season_id,
        directive_id,
        player_count,
        rating_count,
        membership_count,
        rostered_count,
        free_agent_count,
    ):
        self.outcome = outcome
        self.timeline_id = timeline_id
        self.participant_id = participant_id
        self.control_id = control_id
        self.season_id = season_id
        self.directive_id = directive_id
        self.player_count = player_count
        self.rating_count = rating_count
        self.membership_count = membership_count
        self.rostered_count = rostered_count
        self.free_agent_count = free_agent_count


def start_timeline(
    *,
    slug,
    seed,
    generator_version,
    user_id,
    name,
    franchise_slug,
):
    """Start one campaign, or return unchanged when that campaign already matches."""
    _require_arguments(slug, seed, generator_version, user_id, name, franchise_slug)
    try:
        with holding_release_lock(slug=slug, using=DEFAULT_DB_ALIAS) as release:
            try:
                return _start_locked(
                    release,
                    seed,
                    generator_version,
                    user_id,
                    name,
                    franchise_slug,
                )
            except ValidationError as exc:
                raise TimelineStartRefused(
                    'Timeline start refused: %s' % exc
                ) from exc
    except UniverseRelease.DoesNotExist:
        raise TimelineStartRefused(
            'Timeline start refused: the release is missing.'
        ) from None


def _start_locked(release, seed, generator_version, user_id, name, franchise_slug):
    _require_release(release, seed, generator_version)
    user = _require_user(user_id)
    matches = _identity_matches(release, user, name)
    if len(matches) > 1:
        timeline_ids = tuple(sorted(timeline.pk for timeline in matches))
        listed = ', '.join(str(timeline_id) for timeline_id in timeline_ids)
        raise TimelineStartRefused(
            'Timeline start refused: more than one timeline matches this '
            'release, user, and campaign name. Timeline ids: %s.' % listed,
            timeline_ids=timeline_ids,
        )
    if len(matches) == 1:
        timeline = _lock_timeline(matches[0])
        return _exact_result(timeline, release, user, name, franchise_slug)
    shared = _require_shared(release, franchise_slug)
    return _create(release, user, name, shared)


def _require_arguments(slug, seed, generator_version, user_id, name, franchise_slug):
    if not isinstance(slug, str) or slug == '':
        _refuse('A release slug is required.')
    if isinstance(seed, bool) or not isinstance(seed, int):
        _refuse('The seed must be an integer.')
    if not isinstance(generator_version, str) or generator_version == '':
        _refuse('A generator version is required.')
    if isinstance(user_id, bool) or not isinstance(user_id, int):
        _refuse('A user id is required.')
    if not isinstance(name, str) or name == '':
        _refuse('A campaign name is required.')
    if not isinstance(franchise_slug, str) or franchise_slug == '':
        _refuse('A founding franchise slug is required.')


def _require_release(release, seed, generator_version):
    if release.status != UniverseRelease.Status.PUBLISHED:
        _refuse('The release is not published.')
    if (
        release.seed != seed
        or release.generator_version != generator_version
        or generator_version != GENERATOR_VERSION
    ):
        _refuse('The release identity does not match.')
    if release.first_season_year != SEASON_YEAR or (
        release.founding_franchise_count != len(FOUNDING_SLUGS)
    ):
        _refuse('Only the 1970 start is legal for this slice.')


def _require_user(user_id):
    user = get_user_model().objects.filter(pk=user_id).first()
    if user is None:
        _refuse('The user is missing.')
    return user


def _identity_matches(release, user, name):
    """Campaigns tied to this user whose stored name is exactly ``name``.

    The name is not sent to SQL. Python ``==`` is the comparison, so the
    database collation cannot fold case or accents.
    """
    found = {}
    creator_rows = Timeline.objects.filter(release_id=release.pk, created_by_id=user.pk)
    participant_rows = Timeline.objects.filter(
        release_id=release.pk,
        participants__user_id=user.pk,
    )
    for timeline in list(creator_rows) + list(participant_rows):
        found[timeline.pk] = timeline
    return [timeline for timeline in found.values() if timeline.name == name]


def _exact_result(timeline, release, user, name, franchise_slug):
    seasons = list(
        TimelineSeason.objects.select_for_update().filter(
            timeline_id=timeline.pk,
        ).order_by('pk')
    )
    problem = _campaign_problem(
        timeline,
        seasons,
        release,
        user,
        name,
        franchise_slug,
    )
    if problem is not None:
        _refuse(problem)
    return _result('unchanged', timeline)


def _campaign_problem(timeline, seasons, release, user, name, franchise_slug):
    if timeline.release_id != release.pk or timeline.name != name:
        return 'The matching timeline does not match the requested campaign.'
    if timeline.created_by_id != user.pk:
        return 'The matching timeline does not match the requested campaign.'
    if (
        timeline.start_season_year != SEASON_YEAR
        or timeline.current_season_year != SEASON_YEAR
        or timeline.season_index != 1
        or timeline.phase != Timeline.Phase.DIRECTION
        or timeline.status != Timeline.Status.ACTIVE
        or timeline.completed_at is not None
        or timeline.decision_audit is not None
        or timeline.persist_snap_draws
    ):
        return 'The matching timeline does not match the requested campaign.'
    participants = list(timeline.participants.order_by('pk'))
    if not participants:
        return 'The matching timeline has no participant for this user.'
    if len(participants) != 1:
        return 'The matching timeline does not match the requested campaign.'
    participant = participants[0]
    if (
        participant.user_id != user.pk
        or participant.status != TimelineParticipant.Status.ACTIVE
        or participant.joined_season != SEASON_YEAR
        or participant.departed_at is not None
    ):
        return 'The matching timeline does not match the requested campaign.'
    controls = list(timeline.franchise_controls.order_by('pk'))
    if len(controls) != 1:
        return 'The matching timeline does not match the requested campaign.'
    control = controls[0]
    franchise = _franchise(release, franchise_slug)
    if franchise is None or (
        control.participant_id != participant.pk
        or control.franchise_id != franchise.pk
        or control.first_controlled_season != SEASON_YEAR
        or control.final_controlled_season is not None
        or control.reason_ended is not None
    ):
        return 'The matching timeline does not match the requested campaign.'
    if len(seasons) != 1:
        return 'The matching timeline does not match the requested campaign.'
    season = seasons[0]
    profile = _profile(release)
    if profile is None or not _season_matches(season, profile):
        return 'The matching timeline does not match the requested campaign.'
    directives = list(timeline.directives.order_by('pk'))
    if len(directives) != 1 or not _directive_matches(directives[0], participant, season):
        return 'The matching timeline does not match the requested campaign.'
    if _excluded_rows(timeline):
        return 'The matching timeline does not match the requested campaign.'
    if not _opening_matches(timeline, season, release):
        return 'The matching timeline does not match the requested campaign.'
    return None


def _season_matches(season, profile):
    return (
        season.profile_id == profile.pk
        and season.season_year == SEASON_YEAR
        and season.season_index == 1
        and season.phase == Timeline.Phase.DIRECTION
        and season.expansion_gate == 'not_expansion'
        and season.setup_complete is False
        and season.directives_locked is False
        and season.regular_season_simulated is False
        and season.postseason_complete is False
        and season.completed_at is None
    )


def _directive_matches(directive, participant, season):
    if directive.participant_id != participant.pk or directive.season_id != season.pk:
        return False
    return all(
        getattr(directive, field) == value
        for field, value in DIRECTIVE_DEFAULTS.items()
    )


def _excluded_rows(timeline):
    return (
        timeline.expansion_choices.exists()
        or ExpansionPin.objects.filter(choice__timeline=timeline).exists()
        or timeline.kickoff_states.exists()
        or SimulationAuditRecord.objects.filter(kickoff__timeline=timeline).exists()
        or DevelopmentEvent.objects.filter(overlay__timeline=timeline).exists()
    )


def _opening_matches(timeline, season, release):
    assignments = _assignments(release)
    if assignments is None:
        return False
    expected = {row.player_id: row for row in assignments}
    overlays = list(
        TimelinePlayer.objects.filter(timeline=timeline).prefetch_related(
            'season_ratings',
            'roster_memberships',
        )
    )
    if len(overlays) != len(expected) or {row.player_id for row in overlays} != set(expected):
        return False
    profiles = {
        profile.player_id: profile
        for profile in PlayerRatingProfile.objects.filter(player_id__in=expected)
    }
    if set(profiles) != set(expected):
        return False
    for overlay in overlays:
        assignment = expected[overlay.player_id]
        profile = profiles[overlay.player_id]
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
        if not _membership_matches(membership, season, assignment):
            return False
    return True


def _rating_matches(rating, profile):
    return all(
        getattr(rating, 'current_%s' % component) == getattr(profile, 'initial_%s' % component)
        for component in PLAYER_COMPONENTS
    )


def _membership_matches(membership, season, assignment):
    return (
        membership.season_id == season.pk
        and membership.franchise_id == assignment.franchise_id
        and membership.position == assignment.player.position
        and membership.role == assignment.role
        and membership.availability == RosterMembership.Availability.AVAILABLE
        and membership.start_week == 0
        and membership.end_week is None
        and membership.reason_ended is None
    )


def _status_for(role):
    if role in ('starter', 'backup'):
        return TimelinePlayer.Status.ACTIVE
    return role


def _require_shared(release, franchise_slug):
    franchise = _franchise(release, franchise_slug)
    if franchise is None or franchise.origin != UniverseFranchise.Origin.FOUNDING:
        _refuse('The franchise is not an owner-selectable founding club in 1970.')
    if franchise.first_active_season != SEASON_YEAR:
        _refuse('The franchise is not an owner-selectable founding club in 1970.')
    selectable = FranchiseSeasonMembership.objects.filter(
        franchise_id=franchise.pk,
        season_year=SEASON_YEAR,
        owner_selectable=True,
    ).exists()
    if not selectable:
        _refuse('The franchise is not an owner-selectable founding club in 1970.')
    profile = _profile(release)
    if profile is None:
        _refuse('The y1970 profile is missing.')
    assignments = _assignments(release)
    if assignments is None:
        _refuse('The shared opening data is incomplete.')
    if not _staff_complete(release, franchise):
        _refuse('The shared opening staff is incomplete.')
    draft_class = DraftClass.objects.filter(
        release_id=release.pk,
        season_year=SEASON_YEAR,
    ).first()
    if draft_class is None or (
        draft_class.franchise_count != len(FOUNDING_SLUGS)
        or draft_class.rounds != DRAFT_ROUNDS
        or draft_class.draftable_count != DRAFTABLE_COUNT
        or draft_class.undrafted_count != UNDRAFTED_COUNT
        or draft_class.players.count() != CLASS_SIZE
    ):
        _refuse('The 1970 draft class is incomplete.')
    try:
        order = opening_draft_order(release.seed, release.slug)
    except ValueError as exc:
        _refuse('The opening draft order is not the approved neutral order.')
    slugs = [pick.franchise_slug for pick in order]
    if len(slugs) != len(FOUNDING_SLUGS) or set(slugs) != set(FOUNDING_SLUGS):
        _refuse('The opening draft order is not the approved neutral order.')
    return {
        'franchise': franchise,
        'profile': profile,
        'assignments': assignments,
    }


def _franchise(release, franchise_slug):
    if franchise_slug not in FOUNDING_SLUGS:
        return None
    return UniverseFranchise.objects.filter(
        release_id=release.pk,
        placeholder_slug=franchise_slug,
    ).first()


def _profile(release):
    profile = EraRuleProfile.objects.filter(
        release_id=release.pk,
        profile_key=PROFILE_KEY,
    ).first()
    if profile is None or profile.first_year > SEASON_YEAR or profile.last_year < SEASON_YEAR:
        return None
    return profile


def _assignments(release):
    rows = list(
        OpeningAssignment.objects.filter(release_id=release.pk).select_related(
            'player',
            'player__rating_profile',
            'franchise',
        )
    )
    if len(rows) != OPENING_COUNT:
        return None
    counts = {}
    player_ids = set()
    for row in rows:
        counts[row.role] = counts.get(row.role, 0) + 1
        player_ids.add(row.player_id)
        if row.player.cohort != row.player.Cohort.OPENING:
            return None
        try:
            row.player.rating_profile
        except ObjectDoesNotExist:
            return None
        if row.role == 'free_agent':
            if row.franchise_id is not None or row.depth is not None:
                return None
        elif (
            row.franchise_id is None
            or row.depth is None
            or row.depth < 1
            or row.franchise.origin != UniverseFranchise.Origin.FOUNDING
            or row.franchise.release_id != release.pk
        ):
            return None
    if counts != ROLE_COUNTS or len(player_ids) != OPENING_COUNT:
        return None
    return rows


def _staff_complete(release, franchise):
    seats = OpeningStaffAssignment.objects.filter(release_id=release.pk)
    gm = seats.filter(role='gm')
    coach = seats.filter(role='head_coach')
    return (
        gm.count() == STAFF_SEATS
        and coach.count() == STAFF_SEATS
        and seats.count() == STAFF_SEATS * 2
        and gm.filter(franchise_id=franchise.pk).exists()
        and coach.filter(franchise_id=franchise.pk).exists()
    )


def _create(release, user, name, shared):
    timeline = Timeline(
        created_by=user,
        release=release,
        name=name,
        start_season_year=SEASON_YEAR,
        current_season_year=SEASON_YEAR,
        season_index=1,
        phase=Timeline.Phase.DIRECTION,
        status=Timeline.Status.ACTIVE,
        decision_audit=None,
        persist_snap_draws=False,
    )
    timeline.save()
    participant = TimelineParticipant(
        timeline=timeline,
        user=user,
        status=TimelineParticipant.Status.ACTIVE,
        joined_season=SEASON_YEAR,
    )
    participant.save()
    control = TimelineFranchiseControl(
        timeline=timeline,
        participant=participant,
        franchise=shared['franchise'],
        first_controlled_season=SEASON_YEAR,
    )
    control.save()
    season = TimelineSeason(
        timeline=timeline,
        profile=shared['profile'],
        season_year=SEASON_YEAR,
        season_index=1,
        phase=Timeline.Phase.DIRECTION,
        expansion_gate='not_expansion',
    )
    season.save()
    timeline = _lock_timeline(timeline)
    season = _lock_season(season)
    if TimelinePlayer.objects.filter(timeline_id=timeline.pk).exists():
        _refuse('Opening state is already present on a new timeline.')
    initialize_opening_state(
        timeline,
        season,
        {row.player_id: 'available' for row in shared['assignments']},
    )
    OwnerDirective(
        timeline=timeline,
        participant=participant,
        season=season,
    ).save()
    seasons = list(
        TimelineSeason.objects.select_for_update().filter(pk=season.pk)
    )
    problem = _campaign_problem(
        timeline,
        seasons,
        release,
        user,
        name,
        shared['franchise'].placeholder_slug,
    )
    if problem is not None:
        _refuse(problem)
    return _result('created', timeline)


def _lock_timeline(timeline):
    return Timeline.objects.select_for_update().get(pk=timeline.pk)


def _lock_season(season):
    return TimelineSeason.objects.select_for_update().get(pk=season.pk)


def _result(outcome, timeline):
    participant = timeline.participants.get()
    control = timeline.franchise_controls.get()
    season = timeline.seasons.get()
    directive = timeline.directives.get()
    memberships = RosterMembership.objects.filter(overlay__timeline=timeline)
    return TimelineStartResult(
        outcome=outcome,
        timeline_id=timeline.pk,
        participant_id=participant.pk,
        control_id=control.pk,
        season_id=season.pk,
        directive_id=directive.pk,
        player_count=TimelinePlayer.objects.filter(timeline=timeline).count(),
        rating_count=PlayerSeasonRating.objects.filter(overlay__timeline=timeline).count(),
        membership_count=memberships.count(),
        rostered_count=memberships.exclude(franchise__isnull=True).count(),
        free_agent_count=memberships.filter(franchise__isnull=True).count(),
    )


def _refuse(message, timeline_ids=()):
    raise TimelineStartRefused('Timeline start refused: %s' % message, timeline_ids)
