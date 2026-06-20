from django import forms
from django.core.exceptions import ValidationError
from django.utils.text import slugify

from leagues.models import League
from leagues.services.league_generator import (
    CONFERENCE_COUNT,
    DIVISION_COUNT,
    TEAM_COUNT,
)


def _parse_non_empty_lines(value: str, expected_count: int, field_label: str) -> list[str]:
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if len(lines) != expected_count:
        raise ValidationError(
            f'Enter exactly {expected_count} non-empty lines for {field_label} '
            f'(found {len(lines)}).'
        )
    return lines


def _validate_unique_slugs(lines: list[str], field_label: str) -> None:
    slugs = [slugify(name) for name in lines]
    if any(not slug for slug in slugs):
        raise ValidationError(
            f'Every {field_label} name must produce a valid slug.'
        )
    if len(slugs) != len(set(slugs)):
        raise ValidationError(
            f'{field_label} names must be unique; duplicate names produce duplicate slugs.'
        )


class Generate32TeamLeagueForm(forms.Form):
    league = forms.ModelChoiceField(
        queryset=League.objects.all().order_by('tier', 'name'),
        help_text='League to generate or update the 32-team structure for.',
    )
    conference_names = forms.CharField(
        widget=forms.Textarea(
            attrs={
                'rows': 2,
                'cols': 40,
                'placeholder': 'One conference name per line (2 lines)',
            },
        ),
        help_text='Exactly 2 non-empty lines — one name per conference.',
    )
    division_names = forms.CharField(
        widget=forms.Textarea(
            attrs={
                'rows': 8,
                'cols': 40,
                'placeholder': 'One division name per line (8 lines)',
            },
        ),
        help_text=(
            'Exactly 8 non-empty lines. Lines 1–4 belong to conference 1; '
            'lines 5–8 belong to conference 2.'
        ),
    )
    team_names = forms.CharField(
        widget=forms.Textarea(
            attrs={
                'rows': 32,
                'cols': 40,
                'placeholder': 'One team name per line (32 lines)',
            },
        ),
        help_text=(
            'Exactly 32 non-empty lines. Teams 1–4 go to division 1, 5–8 to division 2, '
            'and so on through division 8.'
        ),
    )

    def clean_conference_names(self):
        lines = _parse_non_empty_lines(
            self.cleaned_data['conference_names'],
            CONFERENCE_COUNT,
            'conference names',
        )
        _validate_unique_slugs(lines, 'Conference')
        return lines

    def clean_division_names(self):
        lines = _parse_non_empty_lines(
            self.cleaned_data['division_names'],
            DIVISION_COUNT,
            'division names',
        )
        _validate_unique_slugs(lines, 'Division')
        return lines

    def clean_team_names(self):
        lines = _parse_non_empty_lines(
            self.cleaned_data['team_names'],
            TEAM_COUNT,
            'team names',
        )
        _validate_unique_slugs(lines, 'Team')
        return lines
