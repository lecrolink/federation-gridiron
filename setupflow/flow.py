"""Placeholder catalog and browser-only state for the setup prototype.

The 24 clubs, the opening staff, and the available replacements are
development stand-ins. This module does not read or write league,
universe, or timeline rows.
"""

from django.core import signing

from timelines.models import (
    DEFENSIVE_EMPHASIS,
    FOURTH_DOWN,
    OFFENSIVE_EMPHASIS,
    PLAYING_TIME,
    TEAM_DIRECTIONS,
    TRADE_APPETITES,
    VETERAN_PREFERENCES,
)
from universe.people import GM_PRIORITIES


COOKIE_NAME = 'fg_setup_prototype'
SALT = 'setupflow.prototype'
MAX_AGE = 60 * 60 * 24 * 7

CONFERENCE_LABELS = {
    'conference-1': 'Conference 1',
    'conference-2': 'Conference 2',
}

# 1970 alignment from the player-universe design: two conferences,
# three divisions of four. Order in each tuple is slot_order.
ALIGNMENT = (
    ('conference-1', 'c1-d1', (
        'founding-01', 'founding-02', 'founding-03', 'founding-04',
    )),
    ('conference-1', 'c1-d2', (
        'founding-05', 'founding-06', 'founding-07', 'founding-08',
    )),
    ('conference-1', 'c1-d3', (
        'founding-09', 'founding-10', 'founding-11', 'founding-12',
    )),
    ('conference-2', 'c2-d1', (
        'founding-13', 'founding-14', 'founding-15', 'founding-16',
    )),
    ('conference-2', 'c2-d2', (
        'founding-17', 'founding-18', 'founding-19', 'founding-20',
    )),
    ('conference-2', 'c2-d3', (
        'founding-21', 'founding-22', 'founding-23', 'founding-24',
    )),
)

AVAILABLE_GMS = (
    {
        'id': 'available-gm-01',
        'name': 'Available general manager 01',
        'note': 'Placeholder profile. Trade market and late-round finds.',
    },
    {
        'id': 'available-gm-02',
        'name': 'Available general manager 02',
        'note': 'Placeholder profile. Cap discipline and character.',
    },
    {
        'id': 'available-gm-03',
        'name': 'Available general manager 03',
        'note': 'Placeholder profile. Quarterback eye and finds speed.',
    },
    {
        'id': 'available-gm-04',
        'name': 'Available general manager 04',
        'note': 'Placeholder profile. Lineman eye and medical caution.',
    },
)

AVAILABLE_COACHES = (
    {
        'id': 'available-hc-01',
        'name': 'Available head coach 01',
        'note': 'Placeholder profile. Run game and discipline.',
    },
    {
        'id': 'available-hc-02',
        'name': 'Available head coach 02',
        'note': 'Placeholder profile. Passing game and develops quarterbacks.',
    },
    {
        'id': 'available-hc-03',
        'name': 'Available head coach 03',
        'note': 'Placeholder profile. Defense and halftime adjustments.',
    },
    {
        'id': 'available-hc-04',
        'name': 'Available head coach 04',
        'note': 'Placeholder profile. Special teams and develops linemen.',
    },
)

CHOICES = {
    'team_direction': TEAM_DIRECTIONS,
    'trade_appetite': TRADE_APPETITES,
    'veteran_preference': VETERAN_PREFERENCES,
    'priority': GM_PRIORITIES,
    'offensive_emphasis': OFFENSIVE_EMPHASIS,
    'defensive_emphasis': DEFENSIVE_EMPHASIS,
    'fourth_down': FOURTH_DOWN,
    'playing_time': PLAYING_TIME,
}

LABELS = {
    'team_direction': {
        'compete': 'Compete now',
        'balanced': 'Balanced',
        'rebuild': 'Rebuild',
    },
    'trade_appetite': {
        'low': 'Low',
        'medium': 'Medium',
        'high': 'High',
    },
    'veteran_preference': {
        'automatic': 'Follow the direction',
        'youth': 'Prefer youth',
        'balanced': 'Balanced ages',
        'veterans': 'Prefer veterans',
    },
    'priority': {
        'quarterback': 'Quarterback',
        'skill': 'Skill positions',
        'offensive_line': 'Offensive line',
        'defensive_line': 'Defensive line',
        'linebacker': 'Linebacker',
        'defensive_back': 'Defensive back',
        'special_teams': 'Special teams',
    },
    'offensive_emphasis': {
        'run': 'Run',
        'balanced': 'Balanced',
        'pass': 'Pass',
    },
    'defensive_emphasis': {
        'stop_run': 'Stop the run',
        'balanced': 'Balanced',
        'pressure_quarterback': 'Pressure the quarterback',
    },
    'fourth_down': {
        'conservative': 'Conservative',
        'balanced': 'Balanced',
        'aggressive': 'Aggressive',
    },
    'playing_time': {
        'automatic': 'Let the coach decide',
        'veterans': 'Veterans',
        'balanced': 'Balanced',
        'develop_youth': 'Develop youth',
    },
}

DIRECTION_NOTES = {
    'compete': 'The general manager leans toward veterans.',
    'balanced': 'The general manager stays between veterans and youth.',
    'rebuild': 'The general manager leans toward youth.',
}

DEFAULTS = {
    'franchise': '',
    'gm_choice': 'keep',
    'gm_replacement': '',
    'hc_choice': 'keep',
    'hc_replacement': '',
    'team_direction': 'balanced',
    'priority_group_1': '',
    'priority_group_2': '',
    'priority_group_3': '',
    'trade_appetite': 'medium',
    'veteran_preference': 'automatic',
    'offensive_emphasis': 'balanced',
    'defensive_emphasis': 'balanced',
    'fourth_down': 'balanced',
    'playing_time': 'automatic',
}

_GM_IDS = {person['id']: person for person in AVAILABLE_GMS}
_HC_IDS = {person['id']: person for person in AVAILABLE_COACHES}
_PRIORITY_FIELDS = (
    'priority_group_1',
    'priority_group_2',
    'priority_group_3',
)


def _build_catalog():
    franchises = {}
    conferences = []
    current = None
    current_slug = None
    for conference_slug, division_slug, slugs in ALIGNMENT:
        if conference_slug != current_slug:
            current = {
                'slug': conference_slug,
                'label': CONFERENCE_LABELS[conference_slug],
                'divisions': [],
            }
            conferences.append(current)
            current_slug = conference_slug
        clubs = []
        for order, slug in enumerate(slugs, start=1):
            number = int(slug.rsplit('-', 1)[1])
            club = {
                'slug': slug,
                'name': f'Founding franchise {number:02d}',
                'number': f'{number:02d}',
                'conference': conference_slug,
                'conference_label': CONFERENCE_LABELS[conference_slug],
                'division': division_slug,
                'slot_order': order,
                'first_season': 1970,
            }
            franchises[slug] = club
            clubs.append(club)
        current['divisions'].append({
            'slug': division_slug,
            'clubs': tuple(clubs),
        })
    for club in franchises.values():
        club['mates'] = tuple(
            mate for mate in franchises.values()
            if mate['division'] == club['division'] and mate['slug'] != club['slug']
        )
    return franchises, tuple(conferences)


FRANCHISES, CONFERENCES = _build_catalog()


def options(group, notes=None):
    notes = notes or {}
    return tuple(
        {
            'value': key,
            'label': LABELS[group][key],
            'note': notes.get(key, ''),
        }
        for key in CHOICES[group]
    )


def priority_options():
    return options('priority')


def field(post, key):
    value = post.get(key, '')
    if not isinstance(value, str):
        return ''
    return value.strip()


def _choice(post, key, allowed, label, errors):
    value = field(post, key)
    if value not in allowed:
        errors.append(f'Choose {label}.')
        return ''
    return value


def validate_staff(post):
    errors = []
    cleaned = {
        'gm_choice': 'keep',
        'gm_replacement': '',
        'hc_choice': 'keep',
        'hc_replacement': '',
    }
    pairs = (
        ('gm_choice', 'gm_replacement', _GM_IDS, 'a general manager', 'the replacement general manager'),
        ('hc_choice', 'hc_replacement', _HC_IDS, 'a head coach', 'the replacement head coach'),
    )
    for choice_key, replacement_key, pool, choice_label, replacement_label in pairs:
        choice = field(post, choice_key)
        replacement = field(post, replacement_key)
        if choice not in ('keep', 'replace'):
            errors.append(f'Choose whether to keep or replace {choice_label}.')
            continue
        cleaned[choice_key] = choice
        if choice == 'keep':
            continue
        if replacement not in pool:
            errors.append(f'Choose {replacement_label}.')
            continue
        cleaned[replacement_key] = replacement
    return errors, cleaned


def validate_gm(post):
    errors = []
    cleaned = {
        'team_direction': '',
        'priority_group_1': '',
        'priority_group_2': '',
        'priority_group_3': '',
        'trade_appetite': '',
        'veteran_preference': '',
    }
    cleaned['team_direction'] = _choice(
        post, 'team_direction', TEAM_DIRECTIONS, 'a team direction', errors,
    )
    cleaned['trade_appetite'] = _choice(
        post, 'trade_appetite', TRADE_APPETITES, 'a trade appetite', errors,
    )
    cleaned['veteran_preference'] = _choice(
        post,
        'veteran_preference',
        VETERAN_PREFERENCES,
        'a veteran preference',
        errors,
    )
    groups = []
    for key in _PRIORITY_FIELDS:
        value = field(post, key)
        if not value:
            groups.append('')
            continue
        if value not in GM_PRIORITIES:
            errors.append('Choose a position group from the list.')
            groups.append('')
            continue
        groups.append(value)
    if groups[1] and not groups[0]:
        errors.append('Set the first priority before the second.')
    if groups[2] and not groups[1]:
        errors.append('Set the second priority before the third.')
    named = [group for group in groups if group]
    if len(named) != len(set(named)):
        errors.append('Name each position group only once.')
    if not errors:
        for key, group in zip(_PRIORITY_FIELDS, groups):
            cleaned[key] = group
    return errors, cleaned


def validate_coach(post):
    errors = []
    cleaned = {
        'offensive_emphasis': _choice(
            post,
            'offensive_emphasis',
            OFFENSIVE_EMPHASIS,
            'an offensive emphasis',
            errors,
        ),
        'defensive_emphasis': _choice(
            post,
            'defensive_emphasis',
            DEFENSIVE_EMPHASIS,
            'a defensive emphasis',
            errors,
        ),
        'fourth_down': _choice(
            post, 'fourth_down', FOURTH_DOWN, 'a fourth-down tendency', errors,
        ),
        'playing_time': _choice(
            post, 'playing_time', PLAYING_TIME, 'a playing-time preference', errors,
        ),
    }
    return errors, cleaned


def sanitize(raw):
    state = dict(DEFAULTS)
    if not isinstance(raw, dict):
        return state
    franchise = raw.get('franchise', '')
    if franchise not in FRANCHISES:
        return state
    state['franchise'] = franchise

    def keep_choice(choice_key, replacement_key, pool):
        choice = raw.get(choice_key, '')
        replacement = raw.get(replacement_key, '')
        if choice == 'replace' and replacement in pool:
            state[choice_key] = 'replace'
            state[replacement_key] = replacement
            return
        state[choice_key] = 'keep'
        state[replacement_key] = ''

    keep_choice('gm_choice', 'gm_replacement', _GM_IDS)
    keep_choice('hc_choice', 'hc_replacement', _HC_IDS)

    def pick(key, allowed):
        value = raw.get(key, '')
        if value in allowed:
            state[key] = value

    pick('team_direction', TEAM_DIRECTIONS)
    pick('trade_appetite', TRADE_APPETITES)
    pick('veteran_preference', VETERAN_PREFERENCES)
    pick('offensive_emphasis', OFFENSIVE_EMPHASIS)
    pick('defensive_emphasis', DEFENSIVE_EMPHASIS)
    pick('fourth_down', FOURTH_DOWN)
    pick('playing_time', PLAYING_TIME)

    groups = []
    for key in _PRIORITY_FIELDS:
        value = raw.get(key, '')
        if value in GM_PRIORITIES and value not in groups:
            groups.append(value)
            continue
        break
    for key, group in zip(_PRIORITY_FIELDS, groups):
        state[key] = group
    return state


def read_state(request):
    raw = request.COOKIES.get(COOKIE_NAME, '')
    if not raw:
        return dict(DEFAULTS)
    try:
        loaded = signing.loads(raw, salt=SALT, max_age=MAX_AGE)
    except signing.BadSignature:
        return dict(DEFAULTS)
    return sanitize(loaded)


def write_state(response, state, *, secure):
    payload = sanitize(state)
    response.set_cookie(
        COOKIE_NAME,
        signing.dumps(payload, salt=SALT, compress=True),
        max_age=MAX_AGE,
        httponly=True,
        samesite='Lax',
        secure=secure,
        path='/',
    )


def clear_state(response):
    response.delete_cookie(COOKIE_NAME, path='/', samesite='Lax')


def select_franchise(state, slug):
    if state.get('franchise') == slug:
        return dict(state)
    fresh = dict(DEFAULTS)
    fresh['franchise'] = slug
    return fresh


def staff_lines(state):
    if state['gm_choice'] == 'replace':
        gm = _GM_IDS[state['gm_replacement']]
        gm_line = gm['name']
        gm_detail = 'Placeholder replacement. No hiring record is written.'
    else:
        gm_line = 'Keep the opening general manager'
        gm_detail = 'The shared employee already on the club.'
    if state['hc_choice'] == 'replace':
        coach = _HC_IDS[state['hc_replacement']]
        hc_line = coach['name']
        hc_detail = 'Placeholder replacement. No hiring record is written.'
    else:
        hc_line = 'Keep the opening head coach'
        hc_detail = 'The shared employee already on the club.'
    return {
        'gm_line': gm_line,
        'gm_detail': gm_detail,
        'hc_line': hc_line,
        'hc_detail': hc_detail,
    }


def instruction_lines(state):
    priorities = [
        LABELS['priority'][state[key]]
        for key in _PRIORITY_FIELDS
        if state[key]
    ]
    return {
        'direction': LABELS['team_direction'][state['team_direction']],
        'direction_note': DIRECTION_NOTES[state['team_direction']],
        'priorities': priorities,
        'trade': LABELS['trade_appetite'][state['trade_appetite']],
        'veterans': LABELS['veteran_preference'][state['veteran_preference']],
        'offense': LABELS['offensive_emphasis'][state['offensive_emphasis']],
        'defense': LABELS['defensive_emphasis'][state['defensive_emphasis']],
        'fourth': LABELS['fourth_down'][state['fourth_down']],
        'playing_time': LABELS['playing_time'][state['playing_time']],
    }


def posted_overlay(state, post, keys):
    shown = dict(state)
    for key in keys:
        shown[key] = field(post, key)
    return shown
