"""Save the signed-in owner's direction decisions for the active campaign.

The row is the existing OwnerDirective for that participant and season.
Posted values are accepted only from the model's choices. The timeline,
season phase, lock, club, roster, and schedule stay as they are.
"""

from django import forms
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from play.campaign import campaign_for_user, resolve_campaign
from timelines.models import (
    DEFENSIVE_EMPHASIS,
    DRAFT_PARTICIPATION,
    FOURTH_DOWN,
    OFFENSIVE_EMPHASIS,
    OwnerDirective,
    PLAYING_TIME,
    TEAM_DIRECTIONS,
    TRADE_APPETITES,
    Timeline,
    TimelineFranchiseControl,
    TimelineParticipant,
    TimelineSeason,
    VETERAN_PREFERENCES,
)
from universe.people import GM_PRIORITIES


REQUIRED_FIELDS = (
    'team_direction',
    'trade_appetite',
    'veteran_preference',
    'draft_participation',
    'offensive_emphasis',
    'defensive_emphasis',
    'fourth_down',
    'playing_time',
)
PRIORITY_FIELDS = (
    'priority_group_1',
    'priority_group_2',
    'priority_group_3',
)

_DIRECTION_LABELS = {
    'compete': 'Compete',
    'balanced': 'Balanced',
    'rebuild': 'Rebuild',
}
_TRADE_LABELS = {
    'low': 'Low',
    'medium': 'Medium',
    'high': 'High',
}
_VETERAN_LABELS = {
    'automatic': 'Automatic',
    'youth': 'Youth',
    'balanced': 'Balanced',
    'veterans': 'Veterans',
}
_DRAFT_LABELS = {
    'owner_selects': 'Owner selects',
    'delegate_to_gm': 'Delegate to the GM',
}
_OFFENSE_LABELS = {
    'run': 'Run',
    'balanced': 'Balanced',
    'pass': 'Pass',
}
_DEFENSE_LABELS = {
    'stop_run': 'Stop the run',
    'balanced': 'Balanced',
    'pressure_quarterback': 'Pressure the quarterback',
}
_FOURTH_LABELS = {
    'conservative': 'Conservative',
    'balanced': 'Balanced',
    'aggressive': 'Aggressive',
}
_PLAYING_TIME_LABELS = {
    'automatic': 'Automatic',
    'veterans': 'Veterans',
    'balanced': 'Balanced',
    'develop_youth': 'Develop youth',
}
_PRIORITY_LABELS = {
    'quarterback': 'Quarterback',
    'skill': 'Skill positions',
    'offensive_line': 'Offensive line',
    'defensive_line': 'Defensive line',
    'linebacker': 'Linebackers',
    'defensive_back': 'Defensive backs',
    'special_teams': 'Special teams',
}


def _options(values, labels):
    if set(values) != set(labels):
        raise RuntimeError('A direction choice has no label.')
    return [(value, labels[value]) for value in values]


class DirectionForm(forms.Form):
    """The owner-facing instruction fields. Identity and lock are absent."""

    team_direction = forms.ChoiceField(
        label='Team direction',
        choices=_options(TEAM_DIRECTIONS, _DIRECTION_LABELS),
    )
    priority_group_1 = forms.ChoiceField(
        label='First priority',
        required=False,
        choices=[('', 'No priority')] + _options(GM_PRIORITIES, _PRIORITY_LABELS),
        help_text='Choose these in order. Leave the later ones empty until the earlier one is set.',
    )
    priority_group_2 = forms.ChoiceField(
        label='Second priority',
        required=False,
        choices=[('', 'No priority')] + _options(GM_PRIORITIES, _PRIORITY_LABELS),
    )
    priority_group_3 = forms.ChoiceField(
        label='Third priority',
        required=False,
        choices=[('', 'No priority')] + _options(GM_PRIORITIES, _PRIORITY_LABELS),
    )
    trade_appetite = forms.ChoiceField(
        label='Trade appetite',
        choices=_options(TRADE_APPETITES, _TRADE_LABELS),
    )
    veteran_preference = forms.ChoiceField(
        label='Veteran preference',
        choices=_options(VETERAN_PREFERENCES, _VETERAN_LABELS),
        help_text='Automatic leaves the lean with the GM, derived from team direction.',
    )
    draft_participation = forms.ChoiceField(
        label='Draft participation',
        choices=_options(DRAFT_PARTICIPATION, _DRAFT_LABELS),
    )
    offensive_emphasis = forms.ChoiceField(
        label='Offensive emphasis',
        choices=_options(OFFENSIVE_EMPHASIS, _OFFENSE_LABELS),
    )
    defensive_emphasis = forms.ChoiceField(
        label='Defensive emphasis',
        choices=_options(DEFENSIVE_EMPHASIS, _DEFENSE_LABELS),
    )
    fourth_down = forms.ChoiceField(
        label='Fourth down',
        choices=_options(FOURTH_DOWN, _FOURTH_LABELS),
    )
    playing_time = forms.ChoiceField(
        label='Playing time',
        choices=_options(PLAYING_TIME, _PLAYING_TIME_LABELS),
        help_text='Automatic leaves the lean with the head coach, derived from team direction.',
    )

    def clean(self):
        cleaned = super().clean()
        first = cleaned.get('priority_group_1') or ''
        second = cleaned.get('priority_group_2') or ''
        third = cleaned.get('priority_group_3') or ''
        if second and not first:
            self.add_error(
                'priority_group_2',
                'Choose a first priority before a second one.',
            )
        if third and not second:
            self.add_error(
                'priority_group_3',
                'Choose a second priority before a third one.',
            )
        chosen = [value for value in (first, second, third) if value]
        if len(chosen) != len(set(chosen)):
            self.add_error(None, 'Choose each priority group once.')
        return cleaned


class DirectionSave:
    def __init__(self, saved, form=None):
        self.saved = saved
        self.form = form


def direction_page(user):
    """Describe the directive this account may view."""
    campaign = campaign_for_user(user)
    page = {
        'campaign': campaign,
        'form': None,
        'lines': None,
        'editable': False,
        'blocked': '',
    }
    if campaign['state'] != 'ready':
        return page
    resolved = resolve_campaign(user)
    directive = _stored_directive(resolved)
    if directive is None:
        page['blocked'] = 'missing'
        return page
    if not _phase_is_direction(resolved):
        page['blocked'] = 'phase'
        page['lines'] = stored_lines(directive)
        return page
    if directive.locked or resolved.season.directives_locked:
        page['blocked'] = 'locked'
        page['lines'] = stored_lines(directive)
        return page
    page['editable'] = True
    page['form'] = DirectionForm(initial=_initial(directive))
    return page


def save_direction(user, posted):
    """Update one unlocked direction row. Any other scope writes nothing."""
    with transaction.atomic():
        resolved = resolve_campaign(user)
        if resolved.state != 'ready' or resolved.season is None:
            return DirectionSave(False)
        timeline = Timeline.objects.select_for_update().get(pk=resolved.timeline.pk)
        participation = TimelineParticipant.objects.select_for_update().get(
            pk=resolved.participation.pk,
        )
        season = TimelineSeason.objects.select_for_update().get(pk=resolved.season.pk)
        if not _still_owned(user, timeline, participation, season):
            return DirectionSave(False)
        controls = list(
            TimelineFranchiseControl.objects.select_for_update().filter(
                timeline=timeline,
                participant=participation,
                final_controlled_season__isnull=True,
                first_controlled_season__lte=timeline.current_season_year,
            )
        )
        directives = list(
            OwnerDirective.objects.select_for_update().filter(
                timeline=timeline,
                participant=participation,
                season=season,
            )
        )
        if len(controls) != 1 or len(directives) != 1:
            return DirectionSave(False)
        directive = directives[0]
        if (
            directive.locked
            or season.directives_locked
            or timeline.phase != Timeline.Phase.DIRECTION
            or season.phase != Timeline.Phase.DIRECTION
        ):
            return DirectionSave(False)
        form = DirectionForm(posted)
        if not form.is_valid():
            return DirectionSave(False, form)
        _apply(directive, form.cleaned_data)
        try:
            with transaction.atomic():
                directive.save()
        except (IntegrityError, ValidationError):
            form.add_error(None, 'These decisions could not be saved.')
            return DirectionSave(False, form)
        timeline.refresh_from_db()
        season.refresh_from_db()
        if (
            timeline.phase != Timeline.Phase.DIRECTION
            or season.phase != Timeline.Phase.DIRECTION
        ):
            transaction.set_rollback(True)
            return DirectionSave(False)
        return DirectionSave(True)


def stored_lines(directive):
    form = DirectionForm(initial=_initial(directive))
    lines = []
    for name, field in form.fields.items():
        current = form.initial.get(name, '')
        lines.append({
            'label': field.label,
            'value': dict(field.choices).get(current, 'No priority'),
        })
    return lines


def _initial(directive):
    values = {name: getattr(directive, name) for name in REQUIRED_FIELDS}
    for name in PRIORITY_FIELDS:
        values[name] = getattr(directive, name) or ''
    return values


def _apply(directive, cleaned):
    for name in REQUIRED_FIELDS:
        setattr(directive, name, cleaned[name])
    for name in PRIORITY_FIELDS:
        setattr(directive, name, cleaned[name] or None)


def _stored_directive(resolved):
    if resolved.state != 'ready' or resolved.season is None:
        return None
    rows = list(
        OwnerDirective.objects.filter(
            timeline=resolved.timeline,
            participant=resolved.participation,
            season=resolved.season,
        )
    )
    if len(rows) != 1:
        return None
    return rows[0]


def _phase_is_direction(resolved):
    return (
        resolved.timeline.phase == Timeline.Phase.DIRECTION
        and resolved.season is not None
        and resolved.season.phase == Timeline.Phase.DIRECTION
    )


def _still_owned(user, timeline, participation, season):
    return (
        participation.user_id == user.pk
        and participation.status == TimelineParticipant.Status.ACTIVE
        and participation.timeline_id == timeline.pk
        and timeline.status == Timeline.Status.ACTIVE
        and season.timeline_id == timeline.pk
        and season.season_year == timeline.current_season_year
    )
