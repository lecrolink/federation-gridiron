"""Read-only relative roster comparison for one active campaign.

The page ranks the founding clubs in that campaign against each other.
Grades are display letters. They are not stored. The group weights below
are a provisional display total only. The match engine does not read them.

Ties use competition ranking. Equal hidden scores share the best rank, and
the next rank skips the tied places. A missing score ranks after every
real score, and missing scores tie with each other. The shared rank picks
the tier, so tied clubs and tied positions stay in the same tier. Scores
are not rounded before the comparison.

For a field of N clubs, the ranks divide into five tiers as evenly as
possible. A remainder of one widens C. Further remainders widen D, then B,
then F, then A. In a field of 26 that is A 1st–5th, B 6th–10th, C 11th–16th,
D 17th–21st, and F 22nd–26th.
"""

from django.db.models import Q

from play.campaign import campaign_for_user, resolve_campaign
from timelines.models import PlayerSeasonRating, RosterMembership
from universe.models import UniverseFranchise
from universe.people import PLAYER_COMPONENTS, PlayerRatingProfile
from universe.rules import OpeningAssignment


# Provisional display weights for the hidden club total. The match engine
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

COMPARISON_LABEL = 'Relative roster comparison'

_GROUPS = (
    ('quarterback', 'Offense', ('QB',)),
    ('skill', 'Offense', ('HB', 'FB', 'WR', 'TE')),
    ('offensive_line', 'Offense', ('LT', 'LG', 'C', 'RG', 'RT')),
    ('defensive_line', 'Defense', ('DE', 'DT')),
    ('linebacker', 'Defense', ('ILB', 'OLB')),
    ('defensive_back', 'Defense', ('CB', 'FS', 'SS')),
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

_TIER_LETTERS = ('A', 'B', 'C', 'D', 'F')
_TIER_EXTRA_ORDER = (2, 3, 1, 4, 0)


def _check_tables():
    if sum(PROVISIONAL_DISPLAY_GROUP_WEIGHTS.values()) != 100:
        raise RuntimeError('Provisional display weights must sum to 100.')
    if set(PROVISIONAL_DISPLAY_GROUP_WEIGHTS) != {key for key, _unit, _positions in _GROUPS}:
        raise RuntimeError('Provisional display weights do not match the groups.')
    codes = [code for _key, _unit, positions in _GROUPS for code in positions]
    if codes != [
        'QB', 'HB', 'FB', 'WR', 'TE', 'LT', 'LG', 'C', 'RG', 'RT',
        'DE', 'DT', 'ILB', 'OLB', 'CB', 'FS', 'SS', 'K', 'P',
    ]:
        raise RuntimeError('The comparison positions are not the 19 roster positions.')
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
        'label': COMPARISON_LABEL,
        'explanation': '',
        'tiers': '',
        'ties': (
            'Tied scores share a rank, and the next rank skips ahead. '
            'A shared rank gets the same tier. Each position is graded the same way.'
        ),
        'missing_season': False,
    }
    if campaign['state'] != 'ready':
        return page
    resolved = resolve_campaign(user)
    if resolved.season is None:
        page['missing_season'] = True
        return page
    clubs = _clubs(resolved)
    _assign_standings(clubs)
    count = len(clubs)
    page['clubs'] = clubs
    page['explanation'] = (
        'These grades compare these %s clubs\' player ratings. '
        'They do not predict wins and do not include coaches or the schedule.'
    ) % count
    page['tiers'] = _tier_sentence(count)
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
        _club_facts(
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


def _club_facts(franchise, by_position, yours):
    positions = []
    group_means = {}
    for key, unit, codes in _GROUPS:
        graded = []
        for code in codes:
            report = _position_report(by_position.get(code, []))
            graded.append(report)
            upside = 'None' if report['gap'] is None else _upside_word(report['gap'])
            positions.append({
                'code': code,
                'unit': unit,
                'ability': report['ability'],
                'depth': report['depth'],
                'upside': upside,
                'room': report['room'] == 1,
            })
        means = [row['ability'] for row in graded if row['ability'] is not None]
        group_means[key] = None if not means else sum(means) / len(means)
    if any(group_means[key] is None for key in PROVISIONAL_DISPLAY_GROUP_WEIGHTS):
        score = None
    else:
        score = sum(
            PROVISIONAL_DISPLAY_GROUP_WEIGHTS[key] * group_means[key]
            for key in PROVISIONAL_DISPLAY_GROUP_WEIGHTS
        ) / 100
    return {
        'name': franchise.display_name,
        'yours': yours,
        'score': score,
        'positions': positions,
    }


def _assign_standings(clubs):
    field = len(clubs)
    overall = _competition_ranks([
        (index, club['score']) for index, club in enumerate(clubs)
    ])
    for index, club in enumerate(clubs):
        rank = overall[index]
        club['rank'] = rank
        club['rank_label'] = '%s of %s' % (_ordinal(rank), field)
        club['tier'] = _tier(rank, field)
        del club['score']
    codes = [code for _key, _unit, positions in _GROUPS for code in positions]
    for code in codes:
        values = []
        for index, club in enumerate(clubs):
            position = next(row for row in club['positions'] if row['code'] == code)
            values.append((index, position['ability']))
        ranks = _competition_ranks(values)
        for index, club in enumerate(clubs):
            position = next(row for row in club['positions'] if row['code'] == code)
            if position['ability'] is None:
                position['grade'] = 'None'
                position['standing'] = None
            else:
                position['standing'] = ranks[index]
                position['grade'] = _tier(ranks[index], field)
            del position['ability']
    for club in clubs:
        club['strongest'] = _extreme(club['positions'], best=True)
        club['weakest'] = _extreme(club['positions'], best=False)
        club['units'] = _units(club['positions'])
        for position in club['positions']:
            del position['standing']
        del club['positions']
    clubs.sort(key=lambda club: (club['rank'], club['name'].casefold(), club['name']))


def _units(positions):
    units = []
    for label in ('Offense', 'Defense', 'Special teams'):
        rows = [row for row in positions if row['unit'] == label]
        for row in rows:
            del row['unit']
        units.append({'label': label, 'positions': rows})
    return units


def _extreme(positions, best):
    graded = [row for row in positions if row['standing'] is not None]
    holes = [row['code'] for row in positions if row['standing'] is None]
    if best:
        if not graded:
            return 'None'
        best_rank = min(row['standing'] for row in graded)
        return ', '.join(
            row['code'] for row in positions if row['standing'] == best_rank
        )
    if holes:
        return ', '.join(holes)
    if not graded:
        return 'None'
    worst_rank = max(row['standing'] for row in graded)
    return ', '.join(
        row['code'] for row in positions if row['standing'] == worst_rank
    )


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


def _competition_ranks(pairs):
    """Rank higher scores first. Equal scores share the best rank."""
    ranked = {}
    present = sorted(
        ((value, key) for key, value in pairs if value is not None),
        reverse=True,
    )
    index = 0
    next_rank = 1
    while index < len(present):
        value = present[index][0]
        end = index
        while end < len(present) and present[end][0] == value:
            end += 1
        for _value, key in present[index:end]:
            ranked[key] = next_rank
        next_rank = end + 1
        index = end
    for key, value in pairs:
        if value is None:
            ranked[key] = next_rank
    return ranked


def _tier_bounds(field):
    if field <= 0:
        return []
    base, extra = divmod(field, 5)
    sizes = [base, base, base, base, base]
    for offset, index in enumerate(_TIER_EXTRA_ORDER):
        if offset < extra:
            sizes[index] += 1
    bounds = []
    start = 1
    for size, letter in zip(sizes, _TIER_LETTERS):
        if size <= 0:
            continue
        end = start + size - 1
        bounds.append((letter, start, end))
        start = end + 1
    return bounds


def _tier(rank, field):
    for letter, start, end in _tier_bounds(field):
        if start <= rank <= end:
            return letter
    return 'F'


def _tier_sentence(field):
    parts = []
    for letter, start, end in _tier_bounds(field):
        if start == end:
            span = _ordinal(start)
        else:
            span = '%s–%s' % (_ordinal(start), _ordinal(end))
        parts.append('%s is %s' % (letter, span))
    if not parts:
        return ''
    if len(parts) == 1:
        return '%s.' % parts[0]
    return '%s, and %s.' % (', '.join(parts[:-1]), parts[-1])


def _ordinal(number):
    if 10 <= number % 100 <= 20:
        suffix = 'th'
    else:
        suffix = {1: 'st', 2: 'nd', 3: 'rd'}.get(number % 10, 'th')
    return '%s%s' % (number, suffix)


def _upside_word(gap):
    if gap >= 11:
        return 'High'
    if gap >= 5:
        return 'Some'
    return 'Limited'


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
