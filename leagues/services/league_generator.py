from dataclasses import dataclass

from django.db import transaction
from django.utils.text import slugify

from leagues.models import Conference, Division, League, Team

CONFERENCE_COUNT = 2
DIVISION_COUNT = 8
TEAM_COUNT = 32
DIVISIONS_PER_CONFERENCE = 4
TEAMS_PER_DIVISION = 4


@dataclass
class GenerationResult:
    conferences_created: int = 0
    conferences_updated: int = 0
    divisions_created: int = 0
    divisions_updated: int = 0
    teams_created: int = 0
    teams_updated: int = 0


def _slug_from_name(name: str) -> str:
    slug = slugify(name)
    if not slug:
        raise ValueError(f'Could not generate a slug from name: {name!r}')
    return slug


@transaction.atomic
def generate_league_structure(
    league: League,
    conference_names: list[str],
    division_names: list[str],
    team_names: list[str],
) -> GenerationResult:
    result = GenerationResult()
    conferences: list[Conference] = []

    for index, name in enumerate(conference_names):
        slug = _slug_from_name(name)
        conference, created = Conference.objects.update_or_create(
            league=league,
            slug=slug,
            defaults={
                'name': name,
                'sort_order': index,
                'active': True,
            },
        )
        conferences.append(conference)
        if created:
            result.conferences_created += 1
        else:
            result.conferences_updated += 1

    divisions: list[Division] = []
    for index, name in enumerate(division_names):
        conference = conferences[index // DIVISIONS_PER_CONFERENCE]
        slug = _slug_from_name(name)
        division, created = Division.objects.update_or_create(
            conference=conference,
            slug=slug,
            defaults={
                'name': name,
                'sort_order': index % DIVISIONS_PER_CONFERENCE,
                'active': True,
            },
        )
        divisions.append(division)
        if created:
            result.divisions_created += 1
        else:
            result.divisions_updated += 1

    for index, name in enumerate(team_names):
        division = divisions[index // TEAMS_PER_DIVISION]
        conference = division.conference
        slug = _slug_from_name(name)
        team, created = Team.objects.update_or_create(
            league=league,
            slug=slug,
            defaults={
                'name': name,
                'conference': conference,
                'division': division,
                'sort_order': index % TEAMS_PER_DIVISION,
                'active': True,
            },
        )
        if created:
            result.teams_created += 1
        else:
            result.teams_updated += 1

    return result
