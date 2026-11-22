"""Read the signed-in player's own campaign.

An active campaign is one active participation on one active timeline.
The club is that participation's single open control that has already
started. Roster and schedule rows are limited to that club.
"""

from django.db.models import Q

from timelines.models import (
    RosterMembership,
    ScheduleGame,
    ScheduleWeekAssignment,
    Timeline,
    TimelineFranchiseControl,
    TimelineParticipant,
    TimelineSeason,
)


class ResolvedCampaign:
    """The signed-in user's one active campaign, when that scope is unique."""

    def __init__(self, state, timeline=None, participation=None, control=None, season=None, campaigns=None):
        self.state = state
        self.timeline = timeline
        self.participation = participation
        self.control = control
        self.season = season
        self.campaigns = campaigns or []


def resolve_campaign(user):
    """Resolve one active campaign. The result carries no other account's rows."""
    if not getattr(user, 'is_authenticated', False):
        return ResolvedCampaign('anonymous')
    participations = list(
        TimelineParticipant.objects.filter(
            user=user,
            status=TimelineParticipant.Status.ACTIVE,
            timeline__status=Timeline.Status.ACTIVE,
        ).select_related('timeline').order_by('timeline__name', 'id')
    )
    if not participations:
        return ResolvedCampaign('none')
    if len(participations) != 1:
        return ResolvedCampaign(
            'several',
            campaigns=[row.timeline.name for row in participations],
        )
    participation = participations[0]
    timeline = participation.timeline
    controls = list(
        TimelineFranchiseControl.objects.filter(
            timeline=timeline,
            participant=participation,
            final_controlled_season__isnull=True,
            first_controlled_season__lte=timeline.current_season_year,
        ).select_related('franchise').order_by('id')
    )
    if len(controls) != 1:
        return ResolvedCampaign(
            'no_club',
            timeline=timeline,
            participation=participation,
        )
    season = TimelineSeason.objects.filter(
        timeline=timeline,
        season_year=timeline.current_season_year,
    ).first()
    return ResolvedCampaign(
        'ready',
        timeline=timeline,
        participation=participation,
        control=controls[0],
        season=season,
    )


def campaign_for_user(user):
    resolved = resolve_campaign(user)
    if resolved.state == 'anonymous':
        return {'state': 'anonymous'}
    if resolved.state == 'none':
        return {'state': 'none'}
    if resolved.state == 'several':
        return {'state': 'several', 'campaigns': resolved.campaigns}
    if resolved.state == 'no_club':
        return {
            'state': 'no_club',
            'timeline_name': resolved.timeline.name,
            'season_year': resolved.timeline.current_season_year,
            'phase': phase_label(resolved.timeline.phase),
        }
    season = resolved.season
    phase = season.phase if season is not None else resolved.timeline.phase
    return {
        'state': 'ready',
        'timeline_name': resolved.timeline.name,
        'franchise_name': resolved.control.franchise.display_name,
        'season_year': resolved.timeline.current_season_year,
        'phase': phase_label(phase),
        'roster': _roster(resolved.timeline, season, resolved.control.franchise),
        'schedule': _schedule(resolved.timeline, resolved.control.franchise),
    }


def phase_label(value):
    return str(value).replace('_', ' ').capitalize()


def _roster(timeline, season, franchise):
    if season is None:
        return []
    rows = RosterMembership.objects.filter(
        season=season,
        franchise=franchise,
        end_week__isnull=True,
        overlay__timeline=timeline,
    ).select_related('overlay__player').order_by(
        'position',
        'overlay__player__name',
        'id',
    )
    return [
        {
            'name': row.overlay.player.name,
            'position': row.position,
            'role': phase_label(row.role),
            'availability': phase_label(row.availability),
        }
        for row in rows
    ]


def _schedule(timeline, franchise):
    games = ScheduleGame.objects.filter(
        timeline=timeline,
        season_year=timeline.current_season_year,
    ).filter(
        Q(home=franchise) | Q(away=franchise),
    ).select_related('home', 'away', 'week_assignment')
    rows = []
    for game in games:
        if game.home_id == franchise.pk:
            side = 'Home'
            opponent = game.away.display_name
        else:
            side = 'Away'
            opponent = game.home.display_name
        rows.append({
            'week': _week(game),
            'side': side,
            'opponent': opponent,
        })
    rows.sort(key=lambda row: (row['week'] is None, row['week'] or 0, row['opponent']))
    return rows


def _week(game):
    try:
        return game.week_assignment.week
    except ScheduleWeekAssignment.DoesNotExist:
        return None
