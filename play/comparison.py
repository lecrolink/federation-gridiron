"""Read-only roster comparison for the signed-in owner's active campaign.

Grades are display letters. They are not stored, and the weights below are
not simulation coefficients. The page reads the campaign's current ratings,
shared potential, open roster roles, availability, and opening depth.
"""

from django.db.models import Q

from play.campaign import campaign_for_user, resolve_campaign
from timelines.models import PlayerSeasonRating, RosterMembership
from universe.models import UniverseFranchise
from universe.people import PLAYER_COMPONENTS, PlayerRatingProfile
from universe.rules import OpeningAssignment


# Provisional display weights for comparing roster talent. The match engine
# does not read them. They sum to 100.
PROVISIONAL_DISPLAY_GROUP_WEIGHTS = {
    'quarterback': 20,
    'offensive_line': 18,
    'skill': 16,
    'defensive_line': 14,
    'defensive_back': 14,
    'linebacker': 10,
    'special_teams': 8,
}

UNSCOUTED_CAPTION = 'Unscouted estimate. Every club uses the same report.'

_GROUPS = (
    ('quarterback', 'Quarterback', ('QB',)),
    ('skill', 'Skill', ('HB', 'FB', 'WR', 'TE')),
    ('offensive_line', 'Offensive line', ('LT', 'LG', 'C', 'RG', 'RT')),
    ('defensive_line', 'Defensive line', ('DE', 'DT')),
    ('linebacker', 'Linebacker', ('ILB', 'OLB')),
    ('defensive_back', 'Defensive back', ('CB', 'FS', 'SS')),
    ('special_teams', 'Special teams', ('K', 'P')),
)

_POSITION_COMPONENTS = {
    'HB': (
        'speed', 'acceleration', 'agility', 'carrying', 'elusiveness',
        'catching', 'stamina', 'return_vision',
    ),
    'FB': ('strength', 'run_blocking', 'carrying', 'catching'),
    'WR': (
        'speed', 'acceleration', 'agility', 'catching', 'route_running',
        'return_vision',
    ),
    'TE': (
        'catching', 'route_running', 'run_blocking', 'pass_blocking',
        'strength',
    ),
    'LT': ('strength', 'run_blocking', 'pass_blocking', 'awareness', 'discipline'),
    'LG': ('strength', 'run_blocking', 'pass_blocking', 'awareness', 'discipline'),
    'C': ('strength', 'run_blocking', 'pass_blocking', 'awareness', 'discipline'),
    'RG': ('strength', 'run_blocking', 'pass_blocking', 'awareness', 'discipline'),
    'RT': ('strength', 'run_blocking', 'pass_blocking', 'awareness', 'discipline'),
    'DE': ('strength', 'pass_rush', 'run_defense', 'speed'),
    'DT': ('strength', 'pass_rush', 'run_defense', 'speed'),
    'ILB': (
        'tackling', 'coverage', 'run_defense', 'awareness', 'speed',
        'special_teams',
    ),
    'OLB': (
        'tackling', 'coverage', 'run_defense', 'awareness', 'speed',
        'special_teams',
    ),
    'CB': ('speed', 'agility', 'coverage', 'awareness'),
    'FS': ('coverage', 'tackling', 'awareness', 'speed', 'run_defense'),
    'SS': ('coverage', 'tackling', 'awareness', 'speed', 'run_defense'),
    'K': ('kick_power', 'kick_accuracy', 'clutch', 'consistency'),
    'P': ('punt_power', 'punt_accuracy', 'consistency'),
}

_QUARTERBACK_COMPONENTS = (
    'throw_power', 'throw_accuracy', 'awareness', 'processing', 'clutch',
    'consistency',
)

_NEEDED_COMPONENTS = tuple(sorted(
    set(_QUARTERBACK_COMPONENTS)
    | {'speed', 'elusiveness'}
    | {name for names in _POSITION_COMPONENTS.values() for name in names}
))


def _check_tables():
    if sum(PROVISIONAL_DISPLAY_GROUP_WEIGHTS.values()) != 100:
        raise RuntimeError('Provisional display weights must sum to 100.')
    if set(PROVISIONAL_DISPLAY_GROUP_WEIGHTS) != {key for key, _label, _positions in _GROUPS}:
        raise RuntimeError('Provisional display weights do not match the groups.')
    unknown = set(_NEEDED_COMPONENTS) - set(PLAYER_COMPONENTS)
    if unknown:
        raise RuntimeError('A comparison component is not a player rating.')


_check_tables()


def club_comparison(user):
    """Describe every founding club on this campaign's current season."""
    campaign = campaign_for_user(user)
    page = {
        'campaign': campaign,
        'clubs': [],
        'caption': UNSCOUTED_CAPTION,
        'missing_season': False,
    }
    if campaign['state'] != 'ready':
        return page
    resolved = resolve_campaign(user)
    if resolved.season is None:
        page['missing_season'] = True
        return page
    page['clubs'] = _clubs(resolved)
    return page


def _clubs(resolved):
    season = resolved.season
    timeline = resolved.timeline
    franchises = list(
        UniverseFranchise.objects.filter(
            release_id=timeline.release_id,
            origin=UniverseFranchise.Origin.FOUNDING,
            first_active_season__lte=season.season_year,
        ).filter(
            Q(final_active_season__isnull=True)
            | Q(final_active_season__gte=season.season_year)
        ).order_by('display_name', 'id')
    )
    franchise_ids = [row.id for row in franchises]
    memberships = list(
        RosterMembership.objects.filter(
            season_id=season.id,
            overlay__timeline_id=timeline.id,
            end_week__isnull=True,
            franchise_id__in=franchise_ids,
        ).exclude(
            role=RosterMembership.Role.FREE_AGENT,
        ).values(
            'id', 'franchise_id', 'position', 'role', 'availability',
            'overlay_id', 'overlay__player_id',
        )
    )
    overlay_ids = {row['overlay_id'] for row in memberships}
    player_ids = {row['overlay__player_id'] for row in memberships}
    currents = _currents(season.id, overlay_ids)
    potentials = _potentials(player_ids)
    depths = _opening_depths(timeline.release_id, player_ids)
    by_club = {}
    for row in memberships:
        player_id = row['overlay__player_id']
        current = currents.get(row['overlay_id'])
        potential = potentials.get(player_id)
        if current is None or potential is None:
            continue
        measured = _Measured(
            membership_id=row['id'],
            role=row['role'],
            available=row['availability'] == RosterMembership.Availability.AVAILABLE,
            depth=depths.get((row['franchise_id'], player_id)),
            ability=_mean(_shares(current, row['position'])),
            potential=_mean(_shares(potential, row['position'])),
        )
        by_club.setdefault(row['franchise_id'], {}).setdefault(
            row['position'], [],
        ).append(measured)
    yours = resolved.control.franchise_id
    return [
        _club_report(
            franchise,
            by_club.get(franchise.id, {}),
            yours=franchise.id == yours,
        )
        for franchise in franchises
    ]


class _Measured:
    def __init__(self, membership_id, role, available, depth, ability, potential):
        self.membership_id = membership_id
        self.role = role
        self.available = available
        self.depth = depth
        self.ability = ability
        self.potential = potential


def _club_report(franchise, by_position, yours):
    groups = [
        _group_report(key, label, positions, by_position)
        for key, label, positions in _GROUPS
    ]
    overall = _overall(groups)
    for group in groups:
        del group['mean']
    return {
        'name': franchise.display_name,
        'yours': yours,
        'overall': overall,
        'depth_summary': _depth_summary(group['depth'] for group in groups),
        'upside_summary': _upside_summary(groups),
        'groups': groups,
    }


def _group_report(key, label, positions, by_position):
    graded = [_position_report(by_position.get(position, [])) for position in positions]
    means = [row['ability'] for row in graded if row['ability'] is not None]
    short = len(graded) - len(means)
    if means:
        group_mean = sum(means) / len(means)
        ability = _letter(group_mean)
        if short:
            ability = '%s, short %s' % (ability, short)
    else:
        group_mean = None
        ability = 'None' if short == 0 else 'None, short %s' % short
    gaps = [row['gap'] for row in graded if row['gap'] is not None]
    return {
        'key': key,
        'label': label,
        'mean': group_mean,
        'ability': ability,
        'depth': _group_depth([row['depth'] for row in graded]),
        'upside': 'None' if not gaps else _upside_word(_median(gaps)),
        'rooms': sum(row['room'] for row in graded),
    }


def _position_report(players):
    """Grade one position from its available starters, or the first backup."""
    available = [player for player in players if player.available]
    starters = [player for player in available if player.role == RosterMembership.Role.STARTER]
    backups = sorted(
        (player for player in available if player.role == RosterMembership.Role.BACKUP),
        key=_depth_key,
    )
    if starters:
        ability_players = starters
        behind = backups[0] if backups else None
    elif backups:
        ability_players = [backups[0]]
        behind = backups[1] if len(backups) > 1 else None
    else:
        ability_players = []
        behind = None
    if not ability_players:
        return {'ability': None, 'gap': None, 'depth': 'Bare', 'room': 0}
    ability = sum(player.ability for player in ability_players) / len(ability_players)
    gap = sum(player.potential - player.ability for player in ability_players) / len(ability_players)
    if behind is None or behind.ability < ability - 8:
        depth = 'Bare' if behind is None else 'Thin'
    else:
        depth = 'Covered'
    chosen = {player.membership_id for player in ability_players}
    room = 0
    for player in available:
        if player.membership_id in chosen:
            continue
        if player.role not in (
            RosterMembership.Role.BACKUP,
            RosterMembership.Role.TAXI,
        ):
            continue
        if player.potential >= ability + 8:
            room = 1
            break
    return {'ability': ability, 'gap': gap, 'depth': depth, 'room': room}


def _overall(groups):
    if any(group['mean'] is None for group in groups):
        return 'Incomplete'
    weighted = sum(
        PROVISIONAL_DISPLAY_GROUP_WEIGHTS[group['key']] * group['mean']
        for group in groups
    )
    return _letter(weighted / 100)


def _depth_summary(words):
    words = list(words)
    return '%s covered, %s mixed, %s thin' % (
        words.count('Covered'),
        words.count('Mixed'),
        words.count('Thin'),
    )


def _upside_summary(groups):
    words = [group['upside'] for group in groups]
    rooms = sum(group['rooms'] for group in groups)
    text = '%s high, %s some, %s limited' % (
        words.count('High'),
        words.count('Some'),
        words.count('Limited'),
    )
    missing = words.count('None')
    if missing:
        text = '%s, %s none' % (text, missing)
    label = 'room' if rooms == 1 else 'rooms'
    return '%s · %s %s' % (text, rooms, label)


def _group_depth(words):
    bad = sum(word in ('Thin', 'Bare') for word in words)
    if bad == 0:
        return 'Covered'
    if bad * 2 >= len(words):
        return 'Thin'
    return 'Mixed'


def _median(values):
    ordered = sorted(values)
    count = len(ordered)
    middle = count // 2
    if count % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _upside_word(gap):
    if gap >= 11:
        return 'High'
    if gap >= 5:
        return 'Some'
    return 'Limited'


def _letter(mean):
    if mean >= 80:
        return 'A'
    if mean >= 73:
        return 'B'
    if mean >= 66:
        return 'C'
    if mean >= 59:
        return 'D'
    return 'F'


def _shares(components, position):
    if position == 'QB':
        mobility = (components['speed'] + components['elusiveness']) / 2
        return [components[name] for name in _QUARTERBACK_COMPONENTS] + [mobility]
    return [components[name] for name in _POSITION_COMPONENTS[position]]


def _mean(values):
    return sum(values) / len(values)


def _depth_key(player):
    missing = player.depth is None
    return (missing, 0 if missing else player.depth, player.membership_id)


def _currents(season_id, overlay_ids):
    if not overlay_ids:
        return {}
    fields = ['overlay_id'] + ['current_%s' % name for name in _NEEDED_COMPONENTS]
    rows = PlayerSeasonRating.objects.filter(
        season_id=season_id,
        overlay_id__in=overlay_ids,
    ).values(*fields)
    return {
        row['overlay_id']: {
            name: row['current_%s' % name] for name in _NEEDED_COMPONENTS
        }
        for row in rows
    }


def _potentials(player_ids):
    if not player_ids:
        return {}
    fields = ['player_id'] + ['potential_%s' % name for name in _NEEDED_COMPONENTS]
    rows = PlayerRatingProfile.objects.filter(player_id__in=player_ids).values(*fields)
    return {
        row['player_id']: {
            name: row['potential_%s' % name] for name in _NEEDED_COMPONENTS
        }
        for row in rows
    }


def _opening_depths(release_id, player_ids):
    if not player_ids:
        return {}
    rows = OpeningAssignment.objects.filter(
        release_id=release_id,
        player_id__in=player_ids,
        franchise_id__isnull=False,
    ).values('franchise_id', 'player_id', 'depth')
    return {
        (row['franchise_id'], row['player_id']): row['depth']
        for row in rows
    }
