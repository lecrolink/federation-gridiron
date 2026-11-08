"""Draft-only 1970–2029 schedule for generator version fg-founding-2.

fg-founding-1 is unchanged and does not call this module. This generator
writes 32 placeholder franchises, 1,797 memberships, four expansion events,
and the opening GM and head-coach pools. The 1976, 1977–1994, and 1999–2001
seats are the corrected alignment. A published release is left unchanged;
generating these seats is a new draft catalog. It does not write players,
coordinators, expansion hires, era profiles, historical standings, or
fingerprints, and it does not publish.

Staff keys use the same HMAC coordinates as fg-founding-1. Head-coach
indexes 0 through 25 use ages 49 through 65. Indexes 0 through 23 therefore
match fg-founding-1 for the same seed and release slug.
"""

from django.core.exceptions import ValidationError
from django.db import DEFAULT_DB_ALIAS, IntegrityError
from django.db.models import Q
from django.db.models.deletion import ProtectedError

from timelines.models import Timeline
from universe.release_guard import (
    assert_release_lock_held,
    database_alias,
    holding_release_lock,
    parent_release_id,
)
from universe.generation.founding import (
    AGE_MAX,
    AGE_MIN,
    COACH_FORMER_ROLES,
    EMPLOYED_HEAD_COACH_AGE_MIN,
    FIRST_SEASON_YEAR,
    FoundingGenerationError,
    GM_FORMER_ROLES,
    HEAD_COACH_SPECIALTY,
    LAST_SEASON_YEAR,
    MAX_SIGNATURE_ATTEMPTS,
    OFFENSE_AT_FOUNDING,
    POOL_SIZE,
    ROLE_GM,
    ROLE_HEAD_COACH,
    SEASON_COUNT,
    _pick,
    _pick_pair,
    _signature,
    _resolve_release_id,
    _tag_draw,
    _validate_seed,
    choose_name,
    hmac_int,
    universe_key,
)
from universe.generation.provisional_names import (
    ProvisionalNameError,
    assignment_order,
    display_name_for,
    resolve_names,
)
from universe.models import (
    FranchiseHistoryEvent,
    FranchiseSeasonMembership,
    HistoricalStanding,
    ProvisionalFranchiseName,
    UniverseFranchise,
    UniverseRelease,
)
from universe.people import (
    DEFENSE_SCHEMES,
    GM_PRIORITIES,
    UniverseAward,
    UniverseCoach,
    UniverseGM,
    UniversePlayer,
    UniverseProSeason,
    UniverseProStint,
    UniverseStaffIdentity,
    UniverseStaffPost,
)
from universe.rules import (
    ExpansionClub,
    ExpansionEvent,
    OpeningAssignment,
    OpeningStaffAssignment,
)

GENERATOR_VERSION = 'fg-founding-2'
OPENING_SEATS = 26
FOUNDING_FRANCHISE_COUNT = 26
PRIOR_GENERATOR_VERSION = 'fg-founding-1'
OPENING_ROLES = (ROLE_GM, ROLE_HEAD_COACH)

RULE_FIELDS = (
    'expansion_draft_order_rule',
    'roster_building_rule',
    'free_agent_rule',
    'rookie_draft_slot_rule',
    'gm_hiring_rule',
    'hc_hiring_rule',
)

RULE_TEXT = {
    'expansion_draft_order_rule': (
        'Selection snakes in placeholder-slug order through the new clubs. '
        'Odd rounds run the lower slug first. Even rounds reverse. '
        'A year with one new club has no second club to reverse.'
    ),
    'roster_building_rule': (
        'The active roster is veterans from existing clubs, drafted rookies, '
        'and reserved undrafted prospects. Taxi slots are optional and do not '
        'count toward the active target.'
    ),
    'free_agent_rule': (
        'A free-agent signing may replace a reserved undrafted signing. '
        'Free agents are not required, and their absence does not stop the season.'
    ),
    'rookie_draft_slot_rule': (
        'Rookie picks for the new clubs come at the end of each round.'
    ),
    'gm_hiring_rule': (
        'The new club hires a GM from the shared pool. '
        'This record does not choose which person.'
    ),
    'hc_hiring_rule': (
        'The new club hires a head coach from the shared pool. '
        'This record does not choose which person.'
    ),
}

EXPANSION_EVENTS = (
    {
        'season_year': 1976,
        'existing_club_count': 26,
        'new_club_count': 2,
        'active_target': 40,
        'taxi_capacity': 8,
        'max_losses_per_existing': 2,
        'min_losses_per_existing': 1,
        'clubs_at_max_loss': 6,
        'clubs_at_min_loss': 20,
        'veteran_picks_per_new': 16,
        'veteran_picks_total': 32,
        'rookie_picks_per_new': 17,
        'undrafted_multiplier': 2,
        'undrafted_pool': 56,
        'undrafted_reserved_per_new': 7,
        'clubs': ('expansion-1976-01', 'expansion-1976-02'),
    },
    {
        'season_year': 1995,
        'existing_club_count': 28,
        'new_club_count': 2,
        'active_target': 53,
        'taxi_capacity': 8,
        'max_losses_per_existing': 2,
        'min_losses_per_existing': 1,
        'clubs_at_max_loss': 24,
        'clubs_at_min_loss': 4,
        'veteran_picks_per_new': 26,
        'veteran_picks_total': 52,
        'rookie_picks_per_new': 7,
        'undrafted_multiplier': 2,
        'undrafted_pool': 60,
        'undrafted_reserved_per_new': 20,
        'clubs': ('expansion-1995-01', 'expansion-1995-02'),
    },
    {
        'season_year': 1999,
        'existing_club_count': 30,
        'new_club_count': 1,
        'active_target': 53,
        'taxi_capacity': 8,
        'max_losses_per_existing': 1,
        'min_losses_per_existing': 0,
        'clubs_at_max_loss': 26,
        'clubs_at_min_loss': 4,
        'veteran_picks_per_new': 26,
        'veteran_picks_total': 26,
        'rookie_picks_per_new': 7,
        'undrafted_multiplier': 2,
        'undrafted_pool': 62,
        'undrafted_reserved_per_new': 20,
        'clubs': ('expansion-1999-01',),
    },
    {
        'season_year': 2002,
        'existing_club_count': 31,
        'new_club_count': 1,
        'active_target': 53,
        'taxi_capacity': 8,
        'max_losses_per_existing': 1,
        'min_losses_per_existing': 0,
        'clubs_at_max_loss': 26,
        'clubs_at_min_loss': 5,
        'veteran_picks_per_new': 26,
        'veteran_picks_total': 26,
        'rookie_picks_per_new': 7,
        'undrafted_multiplier': 2,
        'undrafted_pool': 64,
        'undrafted_reserved_per_new': 20,
        'clubs': ('expansion-2002-01',),
    },
)

MEMBERSHIP_COUNT = 1797
YEAR_COUNTS = {
    1970: 26,
    1975: 26,
    1976: 28,
    1994: 28,
    1995: 30,
    1998: 30,
    1999: 31,
    2001: 31,
    2002: 32,
    2029: 32,
}


def _teams(*numbers):
    return tuple(f'founding-{number:02d}' for number in numbers)


_ALIGN_1970 = (
    ('c1-d1', _teams(1, 2, 3, 4)),
    ('c1-d2', _teams(5, 6, 7, 8)),
    ('c1-d3', _teams(9, 10, 11, 12, 25)),
    ('c2-d1', _teams(13, 14, 15, 16)),
    ('c2-d2', _teams(17, 18, 19, 20)),
    ('c2-d3', _teams(21, 22, 23, 24, 26)),
)
# 1976 only. d1 is the four-club Central role, d2 the five-club West role,
# and d3 the five-club East role. The two expansion slugs exchange
# conferences for this season and are not historical franchises.
_ALIGN_1976 = (
    ('c1-d1', _teams(1, 2, 3, 4)),
    ('c1-d2', _teams(5, 6, 7, 8) + ('expansion-1976-02',)),
    ('c1-d3', _teams(9, 10, 11, 12, 25)),
    ('c2-d1', _teams(13, 14, 15, 16)),
    ('c2-d2', _teams(17, 18, 19, 20) + ('expansion-1976-01',)),
    ('c2-d3', _teams(21, 22, 23, 24, 26)),
)
# 1977–1994. Conference-1 stays 4/5/5. Conference-2 is 5/4/5.
# expansion-1976-01 returns to conference-1. expansion-1976-02 returns
# to conference-2.
_ALIGN_1977 = (
    ('c1-d1', _teams(1, 2, 3, 4)),
    ('c1-d2', _teams(5, 6, 7, 8) + ('expansion-1976-01',)),
    ('c1-d3', _teams(9, 10, 11, 12, 25)),
    ('c2-d1', _teams(13, 14, 15, 16) + ('expansion-1976-02',)),
    ('c2-d2', _teams(17, 18, 19, 20)),
    ('c2-d3', _teams(21, 22, 23, 24, 26)),
)
_ALIGN_1995 = (
    ('c1-d1', _teams(1, 2, 3, 4, 9)),
    ('c1-d2', _teams(5, 6, 7, 8, 11)),
    ('c1-d3', ('founding-10', 'expansion-1976-01', 'founding-12', 'founding-25', 'expansion-1995-01')),
    ('c2-d1', _teams(13, 14, 15, 16, 21)),
    ('c2-d2', _teams(17, 18, 19, 20, 23)),
    ('c2-d3', ('founding-22', 'expansion-1976-02', 'founding-24', 'founding-26', 'expansion-1995-02')),
)
# Conference-1 is 6/5/5. d1 is the six-club Central role and holds
# expansion-1999-01. Conference-2 stays the kept three divisions of five.
_ALIGN_1999 = (
    ('c1-d1', _teams(1, 2, 3, 4) + ('expansion-1995-01', 'expansion-1999-01')),
    ('c1-d2', _teams(5, 6, 7, 8) + ('expansion-1976-01',)),
    ('c1-d3', _teams(9, 10, 11, 12, 25)),
    ('c2-d1', _teams(13, 14, 15, 16, 21)),
    ('c2-d2', _teams(17, 18, 19, 20, 23)),
    ('c2-d3', ('founding-22', 'expansion-1976-02', 'founding-24', 'founding-26', 'expansion-1995-02')),
)
_ALIGN_2002 = (
    ('c1-d1', _teams(1, 2, 3, 4)),
    ('c1-d2', _teams(5, 6, 7, 8)),
    ('c1-d3', ('founding-10', 'expansion-1976-01', 'founding-12', 'founding-25')),
    ('c1-d4', ('founding-09', 'founding-11', 'expansion-1995-01', 'expansion-1999-01')),
    ('c2-d1', _teams(13, 14, 15, 16)),
    ('c2-d2', _teams(17, 18, 19, 20)),
    ('c2-d3', ('founding-22', 'expansion-1976-02', 'founding-24', 'founding-26')),
    ('c2-d4', ('founding-21', 'founding-23', 'expansion-1995-02', 'expansion-2002-01')),
)

ALIGNMENTS = (
    (1970, 1975, _ALIGN_1970),
    (1976, 1976, _ALIGN_1976),
    (1977, 1994, _ALIGN_1977),
    (1995, 1998, _ALIGN_1995),
    (1999, 2001, _ALIGN_1999),
    (2002, 2029, _ALIGN_2002),
)

DEBUT_YEAR = {
    slug: 1970 for slug in (f'founding-{number:02d}' for number in range(1, 27))
}
DEBUT_YEAR.update({
    'expansion-1976-01': 1976,
    'expansion-1976-02': 1976,
    'expansion-1995-01': 1995,
    'expansion-1995-02': 1995,
    'expansion-1999-01': 1999,
    'expansion-2002-01': 2002,
})


class ScheduleGenerationError(Exception):
    """The schedule slice cannot be written."""


def _split_division(label):
    conference_number, division = label.split('-')
    return f'conference-{conference_number[1]}', division


def membership_rows():
    """Every franchise-season seat from 1970 through 2029."""
    rows = []
    seen = set()
    for first_year, last_year, alignment in ALIGNMENTS:
        for season_year in range(first_year, last_year + 1):
            for label, slugs in alignment:
                conference_slug, division_slug = _split_division(label)
                for slot_order, slug in enumerate(slugs, start=1):
                    if season_year < DEBUT_YEAR[slug]:
                        raise ScheduleGenerationError(
                            f'{slug} is seated before its debut.'
                        )
                    key = (season_year, conference_slug, division_slug, slot_order)
                    if key in seen:
                        raise ScheduleGenerationError(
                            'Two clubs share a conference, division, and slot.'
                        )
                    seen.add(key)
                    rows.append({
                        'slug': slug,
                        'season_year': season_year,
                        'conference_slug': conference_slug,
                        'division_slug': division_slug,
                        'slot_order': slot_order,
                    })
    if len(rows) != MEMBERSHIP_COUNT:
        raise ScheduleGenerationError(
            f'The schedule has {len(rows)} memberships, not {MEMBERSHIP_COUNT}.'
        )
    return rows


def _birth_year_for(seed, staff_key, role, index, season_year):
    if role == ROLE_HEAD_COACH and index < OPENING_SEATS:
        low, high = EMPLOYED_HEAD_COACH_AGE_MIN, AGE_MAX
    else:
        low, high = AGE_MIN, AGE_MAX
    age = low + (hmac_int(seed, f'{staff_key}:birth_year') % (high - low + 1))
    return season_year - age


def build_opening_staff(seed, release_slug, season_year):
    """One hundred GM records, then one hundred head-coach records."""
    rows = []
    for role, former_roles in (
        (ROLE_GM, GM_FORMER_ROLES),
        (ROLE_HEAD_COACH, COACH_FORMER_ROLES),
    ):
        seen = set()
        for index in range(POOL_SIZE):
            staff_key = universe_key(seed, release_slug, role, index)
            record = {
                'role': role,
                'index': index,
                'universe_key': staff_key,
                'name': choose_name(seed, staff_key),
                'birth_year': _birth_year_for(
                    seed, staff_key, role, index, season_year,
                ),
                'debut_year': season_year,
                'former_role': _pick(
                    seed, staff_key, 'former_role', former_roles, 0,
                ),
            }
            if role == ROLE_GM:
                priority_1, priority_2 = _pick_pair(
                    seed,
                    staff_key,
                    'positional_priority_1',
                    'positional_priority_2',
                    GM_PRIORITIES,
                    0,
                )
                record['positional_priority_1'] = priority_1
                record['positional_priority_2'] = priority_2
            else:
                record['specialty'] = HEAD_COACH_SPECIALTY
                record['preferred_offense'] = _pick(
                    seed, staff_key, 'preferred_offense', OFFENSE_AT_FOUNDING, 0,
                )
                record['preferred_defense'] = _pick(
                    seed, staff_key, 'preferred_defense', DEFENSE_SCHEMES, 0,
                )
            placed = False
            for attempt in range(MAX_SIGNATURE_ATTEMPTS):
                tags = _tag_draw(seed, staff_key, role, attempt)
                signature = _signature(tags)
                if signature in seen:
                    continue
                seen.add(signature)
                record.update(tags)
                record['signature_attempt'] = attempt
                placed = True
                break
            if not placed:
                raise ScheduleGenerationError(
                    f'No unique {role} signature for index {index}.'
                )
            rows.append(record)
    return rows


def _owned_keys(seed, release_slug):
    return {
        universe_key(seed, release_slug, role, index)
        for role in OPENING_ROLES
        for index in range(POOL_SIZE)
    }


def _stored_names(release):
    stored = {}
    for name in release.provisional_franchise_names.all():
        stored[name.placeholder_slug] = {
            'city': name.city,
            'city_status': name.city_status,
            'nickname': name.nickname,
            'nickname_status': name.nickname_status,
        }
    return stored


def _preserved_rules(release):
    preserved = {}
    for event in release.expansion_events.all():
        preserved[event.season_year] = {
            field: getattr(event, field) for field in RULE_FIELDS
        }
    return preserved


def _rule_value(preserved, season_year, field):
    current = preserved.get(season_year, {}).get(field, '')
    if current:
        return current
    return RULE_TEXT[field]


def _expected_slice(seed, release_slug, names, preserved_rules):
    staff = build_opening_staff(seed, release_slug, FIRST_SEASON_YEAR)
    franchises = []
    for slug in assignment_order():
        name = names[slug]
        franchises.append({
            'slug': slug,
            'display_name': display_name_for(name['city'], name['nickname']),
            'origin': (
                UniverseFranchise.Origin.FOUNDING
                if DEBUT_YEAR[slug] == FIRST_SEASON_YEAR
                else UniverseFranchise.Origin.EXPANSION
            ),
            'first_active_season': DEBUT_YEAR[slug],
        })
    events = []
    for spec in EXPANSION_EVENTS:
        event = {key: value for key, value in spec.items() if key != 'clubs'}
        for field in RULE_FIELDS:
            event[field] = _rule_value(preserved_rules, spec['season_year'], field)
        event['clubs'] = tuple(sorted(spec['clubs']))
        events.append(event)
    return {
        'franchises': franchises,
        'memberships': membership_rows(),
        'staff': staff,
        'keys': {row['universe_key'] for row in staff},
        'names': names,
        'events': events,
    }


def _require_horizon(release):
    if (
        release.founding_franchise_count != FOUNDING_FRANCHISE_COUNT
        or release.first_season_year != FIRST_SEASON_YEAR
        or release.season_count != SEASON_COUNT
        or release.last_season_year != LAST_SEASON_YEAR
    ):
        raise ScheduleGenerationError(
            'The draft release must use 26 founding franchises and the '
            '1970–2029 horizon.'
        )


def _refuse_other_generator(release):
    if release.generator_version != PRIOR_GENERATOR_VERSION:
        return
    if (
        release.staff_identities.exists()
        or release.gms.exists()
        or release.coaches.exists()
        or release.opening_staff.exists()
        or release.franchises.exists()
    ):
        raise ScheduleGenerationError(
            'fg-founding-1 already wrote this draft. fg-founding-2 will not '
            'replace those rows.'
        )


def _replacement_keys(release):
    _refuse_other_generator(release)
    if release.generator_version != GENERATOR_VERSION:
        if (
            release.staff_identities.exists()
            or release.gms.exists()
            or release.coaches.exists()
            or release.opening_staff.exists()
            or release.franchises.exists()
            or release.expansion_events.exists()
        ):
            raise ScheduleGenerationError(
                'This draft uses another generator version and already has '
                'rows. This generator will not guess which rows belong to it.'
            )
        return set()
    return _owned_keys(release.seed, release.slug)


def _names_match(release, expected):
    stored = _stored_names(release)
    return stored == expected['names']


def _franchises_match(release, expected):
    franchises = {
        franchise.placeholder_slug: franchise
        for franchise in release.franchises.all()
    }
    if set(franchises) != {row['slug'] for row in expected['franchises']}:
        return False
    for row in expected['franchises']:
        franchise = franchises[row['slug']]
        if any((
            franchise.display_name != row['display_name'],
            franchise.origin != row['origin'],
            franchise.first_active_season != row['first_active_season'],
            franchise.final_active_season is not None,
            franchise.catalog_team_id is not None,
            franchise.history_predecessor_id is not None,
        )):
            return False
    return True


def _memberships_match(release, expected):
    found = {
        (slug, season_year, conference_slug, division_slug, slot_order, bool(owner_selectable))
        for slug, season_year, conference_slug, division_slug, slot_order, owner_selectable
        in release.franchises.values_list(
            'placeholder_slug',
            'memberships__season_year',
            'memberships__conference_slug',
            'memberships__division_slug',
            'memberships__slot_order',
            'memberships__owner_selectable',
        )
    }
    wanted = {
        (
            row['slug'],
            row['season_year'],
            row['conference_slug'],
            row['division_slug'],
            row['slot_order'],
            True,
        )
        for row in expected['memberships']
    }
    return found == wanted


def _staff_match(release, expected):
    identities = {
        identity.universe_key: identity
        for identity in UniverseStaffIdentity.objects.filter(
            release=release,
        ).select_related('gm_profile', 'coach_profile')
    }
    if set(identities) != expected['keys']:
        return False
    for row in expected['staff']:
        identity = identities[row['universe_key']]
        if identity.name != row['name'] or identity.birth_year != row['birth_year']:
            return False
        if row['role'] == ROLE_GM:
            try:
                profile = identity.gm_profile
            except UniverseGM.DoesNotExist:
                return False
            if hasattr(identity, 'coach_profile'):
                try:
                    identity.coach_profile
                except UniverseCoach.DoesNotExist:
                    pass
                else:
                    return False
            if any((
                profile.debut_year != row['debut_year'],
                profile.former_role != row['former_role'],
                profile.positional_priority_1 != row['positional_priority_1'],
                profile.positional_priority_2 != row['positional_priority_2'],
                profile.strength_1 != row['strength_1'],
                profile.strength_2 != row['strength_2'],
                profile.weakness != row['weakness'],
                profile.trait_1 != row['trait_1'],
                profile.trait_2 != row['trait_2'],
                profile.name != row['name'],
                profile.birth_year != row['birth_year'],
                profile.universe_key != row['universe_key'],
            )):
                return False
        else:
            try:
                profile = identity.coach_profile
            except UniverseCoach.DoesNotExist:
                return False
            try:
                identity.gm_profile
            except UniverseGM.DoesNotExist:
                pass
            else:
                return False
            if any((
                profile.debut_year != row['debut_year'],
                profile.specialty != row['specialty'],
                profile.former_role != row['former_role'],
                profile.preferred_offense != row['preferred_offense'],
                profile.preferred_defense != row['preferred_defense'],
                profile.strength_1 != row['strength_1'],
                profile.strength_2 != row['strength_2'],
                profile.weakness != row['weakness'],
                profile.trait_1 != row['trait_1'],
                profile.trait_2 != row['trait_2'],
                profile.name != row['name'],
                profile.birth_year != row['birth_year'],
                profile.universe_key != row['universe_key'],
            )):
                return False
    return _assignments_match(release, expected['staff'])


def _assignments_match(release, staff):
    expected = {}
    gm_rows = [row for row in staff if row['role'] == ROLE_GM]
    coach_rows = [row for row in staff if row['role'] == ROLE_HEAD_COACH]
    for index in range(OPENING_SEATS):
        slug = f'founding-{index + 1:02d}'
        expected[(slug, ROLE_GM)] = gm_rows[index]['universe_key']
        expected[(slug, ROLE_HEAD_COACH)] = coach_rows[index]['universe_key']
    found = {}
    assignments = OpeningStaffAssignment.objects.filter(release=release).select_related(
        'franchise', 'gm', 'coach',
    )
    for assignment in assignments:
        slug = assignment.franchise.placeholder_slug
        if assignment.role == ROLE_GM:
            if assignment.coach_id is not None or assignment.gm_id is None:
                return False
            person_key = assignment.gm.universe_key
        elif assignment.role == ROLE_HEAD_COACH:
            if assignment.gm_id is not None or assignment.coach_id is None:
                return False
            person_key = assignment.coach.universe_key
        else:
            return False
        found[(slug, assignment.role)] = person_key
    return found == expected


def _events_match(release, expected):
    events = list(release.expansion_events.order_by('season_year'))
    if len(events) != len(expected['events']):
        return False
    for event, spec in zip(events, expected['events']):
        for field, value in spec.items():
            if field == 'clubs':
                continue
            if getattr(event, field) != value:
                return False
        clubs = list(event.clubs.select_related('franchise').order_by('selection_order'))
        found = tuple(club.franchise.placeholder_slug for club in clubs)
        orders = tuple(club.selection_order for club in clubs)
        if found != spec['clubs'] or orders != tuple(range(1, len(spec['clubs']) + 1)):
            return False
    return True


def _slice_matches(release, expected):
    return all((
        _names_match(release, expected),
        _franchises_match(release, expected),
        _memberships_match(release, expected),
        _staff_match(release, expected),
        _events_match(release, expected),
    ))


def _refuse_blockers(release, franchises, old_keys):
    slugs = set(assignment_order())
    franchise_ids = [franchise.pk for franchise in franchises]
    extra = release.franchises.exclude(placeholder_slug__in=slugs)
    if extra.exists():
        raise ScheduleGenerationError(
            'A franchise slug is outside this generator\'s schedule.'
        )
    if any(franchise.catalog_team_id or franchise.history_predecessor_id for franchise in franchises):
        raise ScheduleGenerationError(
            'A franchise is linked to a catalog team or a predecessor.'
        )
    for franchise in franchises:
        expected_origin = (
            UniverseFranchise.Origin.FOUNDING
            if franchise.placeholder_slug.startswith('founding-')
            else UniverseFranchise.Origin.EXPANSION
        )
        if franchise.origin != expected_origin:
            raise ScheduleGenerationError('A schedule slug has the wrong origin.')
        if franchise.first_active_season != DEBUT_YEAR.get(franchise.placeholder_slug):
            raise ScheduleGenerationError('A schedule slug has the wrong debut.')
    foreign_events = release.expansion_events.exclude(
        season_year__in=[spec['season_year'] for spec in EXPANSION_EVENTS],
    )
    if foreign_events.exists():
        raise ScheduleGenerationError(
            'An expansion event is outside 1976, 1995, 1999, and 2002.'
        )
    blockers = (
        ('history event', FranchiseHistoryEvent.objects.filter(franchise_id__in=franchise_ids)),
        ('historical standing', HistoricalStanding.objects.filter(franchise_id__in=franchise_ids)),
        ('player stint', UniverseProStint.objects.filter(franchise_id__in=franchise_ids)),
        ('player season', UniverseProSeason.objects.filter(franchise_id__in=franchise_ids)),
        ('staff post', UniverseStaffPost.objects.filter(franchise_id__in=franchise_ids)),
        ('opening player assignment', OpeningAssignment.objects.filter(franchise_id__in=franchise_ids)),
    )
    if franchise_ids:
        for label, queryset in blockers:
            if queryset.exists():
                raise ScheduleGenerationError(
                    f'A franchise has a {label} this generator does not own.'
                )
        if UniversePlayer.objects.filter(origin_franchise_id__in=franchise_ids).exists():
            raise ScheduleGenerationError(
                'A franchise is a player origin this generator does not own.'
            )
        if UniverseAward.objects.filter(franchise_id__in=franchise_ids).exists():
            raise ScheduleGenerationError(
                'A franchise has an award this generator does not own.'
            )
    coordinator = OpeningStaffAssignment.objects.filter(release=release).exclude(
        role__in=OPENING_ROLES,
    )
    if coordinator.exists():
        raise ScheduleGenerationError(
            'A coordinator seat is attached to this release.'
        )
    if franchise_ids and old_keys:
        foreign_seat = OpeningStaffAssignment.objects.filter(
            franchise_id__in=franchise_ids,
        ).exclude(
            Q(gm__universe_key__in=old_keys) | Q(coach__universe_key__in=old_keys),
        )
        if foreign_seat.exists():
            raise ScheduleGenerationError(
                'An opening seat points outside this generator.'
            )
    if franchise_ids and not old_keys:
        if OpeningStaffAssignment.objects.filter(franchise_id__in=franchise_ids).exists():
            raise ScheduleGenerationError(
                'Opening staff are attached to franchises this version cannot identify.'
            )
    if not old_keys:
        return
    foreign_identity = UniverseStaffIdentity.objects.filter(release=release).exclude(
        universe_key__in=old_keys,
    )
    if foreign_identity.exists():
        raise ScheduleGenerationError(
            'A staff identity is outside this generator\'s pool.'
        )
    if UniverseStaffPost.objects.filter(
        Q(gm__universe_key__in=old_keys) | Q(coach__universe_key__in=old_keys),
    ).exists():
        raise ScheduleGenerationError('A generated person has a staff post.')
    if UniverseAward.objects.filter(
        Q(gm__universe_key__in=old_keys) | Q(coach__universe_key__in=old_keys),
    ).exists():
        raise ScheduleGenerationError('A generated person has an award.')


def _slice_existed(release, franchises, old_keys):
    if franchises:
        return True
    if old_keys and UniverseStaffIdentity.objects.filter(
        release=release,
        universe_key__in=old_keys,
    ).exists():
        return True
    return release.provisional_franchise_names.exists() or release.expansion_events.exists()


def _delete_slice(release, franchises, old_keys):
    assert_release_lock_held(release.pk, using=database_alias(release))
    release.expansion_events.all().delete()
    if old_keys:
        OpeningStaffAssignment.objects.filter(
            Q(gm__universe_key__in=old_keys) | Q(coach__universe_key__in=old_keys),
        ).delete()
        UniverseStaffIdentity.objects.filter(
            release=release,
            universe_key__in=old_keys,
        ).delete()
    franchise_ids = [franchise.pk for franchise in franchises]
    if franchise_ids:
        UniverseFranchise.objects.filter(pk__in=franchise_ids).delete()


def _save_all(rows):
    for row in rows:
        try:
            row.full_clean()
        except ValidationError as exc:
            raise ScheduleGenerationError(
                f'A generated row failed validation: {exc}'
            ) from exc
    if rows:
        alias = database_alias(rows[0])
        release_id = parent_release_id(rows[0], using=alias)
        if release_id is None:
            raise ScheduleGenerationError('A generated row has no parent release.')
        assert_release_lock_held(release_id, using=alias)
        type(rows[0]).objects.bulk_create(rows)


def _sync_names(release, names):
    existing = {
        name.placeholder_slug: name
        for name in release.provisional_franchise_names.all()
    }
    if not existing:
        created = []
        for slug, row in names.items():
            created.append(ProvisionalFranchiseName(
                release=release,
                placeholder_slug=slug,
                city=row['city'],
                city_status=row['city_status'],
                nickname=row['nickname'],
                nickname_status=row['nickname_status'],
            ))
        _save_all(created)
        return
    changed = []
    for slug, row in names.items():
        current = existing[slug]
        for field in ('city', 'nickname'):
            status_field = f'{field}_status'
            if getattr(current, status_field) == ProvisionalFranchiseName.Status.REPLACED:
                if getattr(current, field) != row[field]:
                    raise ScheduleGenerationError(
                        'A replaced city or nickname is kept.'
                    )
                continue
            if getattr(current, field) != row[field]:
                setattr(current, field, row[field])
                setattr(current, status_field, row[status_field])
                changed.append(current)
    if changed:
        assert_release_lock_held(release.pk, using=database_alias(release))
        ProvisionalFranchiseName.objects.bulk_update(
            changed,
            ['city', 'city_status', 'nickname', 'nickname_status'],
        )


def _write_slice(release, expected):
    _sync_names(release, expected['names'])
    franchises = [
        UniverseFranchise(
            release=release,
            placeholder_slug=row['slug'],
            display_name=row['display_name'],
            origin=row['origin'],
            first_active_season=row['first_active_season'],
            final_active_season=None,
            catalog_team=None,
            history_predecessor=None,
        )
        for row in expected['franchises']
    ]
    _save_all(franchises)
    stored_franchises = {
        franchise.placeholder_slug: franchise
        for franchise in release.franchises.all()
    }
    memberships = [
        FranchiseSeasonMembership(
            franchise=stored_franchises[row['slug']],
            season_year=row['season_year'],
            conference_slug=row['conference_slug'],
            division_slug=row['division_slug'],
            slot_order=row['slot_order'],
            owner_selectable=True,
        )
        for row in expected['memberships']
    ]
    # The seat batch is checked in membership_rows before insert. full_clean
    # cannot see unsaved siblings, so it is not the uniqueness guard.
    assert_release_lock_held(release.pk, using=database_alias(release))
    FranchiseSeasonMembership.objects.bulk_create(memberships, batch_size=500)

    identities = [
        UniverseStaffIdentity(
            release=release,
            universe_key=row['universe_key'],
            name=row['name'],
            birth_year=row['birth_year'],
        )
        for row in expected['staff']
    ]
    _save_all(identities)
    stored_identities = {
        identity.universe_key: identity
        for identity in UniverseStaffIdentity.objects.filter(
            release=release,
            universe_key__in=expected['keys'],
        )
    }
    gm_profiles = []
    coach_profiles = []
    for row in expected['staff']:
        identity = stored_identities[row['universe_key']]
        if row['role'] == ROLE_GM:
            gm_profiles.append(UniverseGM(
                release=release,
                identity=identity,
                universe_key=row['universe_key'],
                name=row['name'],
                birth_year=row['birth_year'],
                debut_year=row['debut_year'],
                former_role=row['former_role'],
                positional_priority_1=row['positional_priority_1'],
                positional_priority_2=row['positional_priority_2'],
                strength_1=row['strength_1'],
                strength_2=row['strength_2'],
                weakness=row['weakness'],
                trait_1=row['trait_1'],
                trait_2=row['trait_2'],
            ))
        else:
            coach_profiles.append(UniverseCoach(
                release=release,
                identity=identity,
                universe_key=row['universe_key'],
                name=row['name'],
                birth_year=row['birth_year'],
                debut_year=row['debut_year'],
                specialty=row['specialty'],
                former_role=row['former_role'],
                preferred_offense=row['preferred_offense'],
                preferred_defense=row['preferred_defense'],
                strength_1=row['strength_1'],
                strength_2=row['strength_2'],
                weakness=row['weakness'],
                trait_1=row['trait_1'],
                trait_2=row['trait_2'],
            ))
    _save_all(gm_profiles)
    _save_all(coach_profiles)
    stored_gms = {
        gm.universe_key: gm
        for gm in UniverseGM.objects.filter(release=release, universe_key__in=expected['keys'])
    }
    stored_coaches = {
        coach.universe_key: coach
        for coach in UniverseCoach.objects.filter(
            release=release,
            universe_key__in=expected['keys'],
        )
    }
    assignments = []
    for index in range(OPENING_SEATS):
        slug = f'founding-{index + 1:02d}'
        franchise = stored_franchises[slug]
        gm_key = next(
            row['universe_key']
            for row in expected['staff']
            if row['role'] == ROLE_GM and row['index'] == index
        )
        coach_key = next(
            row['universe_key']
            for row in expected['staff']
            if row['role'] == ROLE_HEAD_COACH and row['index'] == index
        )
        assignments.append(OpeningStaffAssignment(
            release=release,
            franchise=franchise,
            role=ROLE_GM,
            gm=stored_gms[gm_key],
            coach=None,
        ))
        assignments.append(OpeningStaffAssignment(
            release=release,
            franchise=franchise,
            role=ROLE_HEAD_COACH,
            gm=None,
            coach=stored_coaches[coach_key],
        ))
    _save_all(assignments)

    for spec in expected['events']:
        event = ExpansionEvent(
            release=release,
            season_year=spec['season_year'],
            existing_club_count=spec['existing_club_count'],
            new_club_count=spec['new_club_count'],
            active_target=spec['active_target'],
            taxi_capacity=spec['taxi_capacity'],
            max_losses_per_existing=spec['max_losses_per_existing'],
            min_losses_per_existing=spec['min_losses_per_existing'],
            clubs_at_max_loss=spec['clubs_at_max_loss'],
            clubs_at_min_loss=spec['clubs_at_min_loss'],
            veteran_picks_per_new=spec['veteran_picks_per_new'],
            veteran_picks_total=spec['veteran_picks_total'],
            rookie_picks_per_new=spec['rookie_picks_per_new'],
            undrafted_multiplier=spec['undrafted_multiplier'],
            undrafted_pool=spec['undrafted_pool'],
            undrafted_reserved_per_new=spec['undrafted_reserved_per_new'],
            expansion_draft_order_rule=spec['expansion_draft_order_rule'],
            roster_building_rule=spec['roster_building_rule'],
            free_agent_rule=spec['free_agent_rule'],
            rookie_draft_slot_rule=spec['rookie_draft_slot_rule'],
            gm_hiring_rule=spec['gm_hiring_rule'],
            hc_hiring_rule=spec['hc_hiring_rule'],
        )
        _save_all([event])
        stored_event = release.expansion_events.get(season_year=spec['season_year'])
        clubs = []
        for selection_order, slug in enumerate(spec['clubs'], start=1):
            clubs.append(ExpansionClub(
                event=stored_event,
                franchise=stored_franchises[slug],
                selection_order=selection_order,
            ))
        _save_all(clubs)


def _validate_schedule_slice(release):
    if release.franchises.count() != 32:
        raise ScheduleGenerationError('The schedule did not write 32 franchises.')
    if release.franchises.filter(origin=UniverseFranchise.Origin.FOUNDING).count() != 26:
        raise ScheduleGenerationError('The schedule did not write 26 founding franchises.')
    if FranchiseSeasonMembership.objects.filter(franchise__release=release).count() != MEMBERSHIP_COUNT:
        raise ScheduleGenerationError('The schedule did not write 1,797 memberships.')
    if FranchiseSeasonMembership.objects.filter(
        franchise__release=release,
        owner_selectable=False,
    ).exists():
        raise ScheduleGenerationError('A membership is not owner selectable.')
    for season_year, count in YEAR_COUNTS.items():
        found = FranchiseSeasonMembership.objects.filter(
            franchise__release=release,
            season_year=season_year,
        ).count()
        if found != count:
            raise ScheduleGenerationError(
                f'{season_year} has {found} clubs, not {count}.'
            )
    checks = (
        (1970, 'founding-09', 'conference-1', 'd3', 1),
        (1975, 'founding-25', 'conference-1', 'd3', 5),
        (1975, 'founding-26', 'conference-2', 'd3', 5),
        (1976, 'founding-09', 'conference-1', 'd3', 1),
        (1976, 'expansion-1976-01', 'conference-2', 'd2', 5),
        (1976, 'expansion-1976-02', 'conference-1', 'd2', 5),
        (1977, 'expansion-1976-01', 'conference-1', 'd2', 5),
        (1977, 'expansion-1976-02', 'conference-2', 'd1', 5),
        (1994, 'expansion-1976-01', 'conference-1', 'd2', 5),
        (1994, 'expansion-1976-02', 'conference-2', 'd1', 5),
        (1995, 'expansion-1995-02', 'conference-2', 'd3', 5),
        (1998, 'founding-09', 'conference-1', 'd1', 5),
        (1999, 'founding-09', 'conference-1', 'd3', 1),
        (1999, 'expansion-1999-01', 'conference-1', 'd1', 6),
        (1999, 'expansion-1976-01', 'conference-1', 'd2', 5),
        (2001, 'expansion-1995-02', 'conference-2', 'd3', 5),
        (2002, 'expansion-2002-01', 'conference-2', 'd4', 4),
        (2029, 'founding-09', 'conference-1', 'd4', 1),
    )
    for season_year, slug, conference_slug, division_slug, slot_order in checks:
        membership = FranchiseSeasonMembership.objects.get(
            franchise__release=release,
            franchise__placeholder_slug=slug,
            season_year=season_year,
        )
        if any((
            membership.conference_slug != conference_slug,
            membership.division_slug != division_slug,
            membership.slot_order != slot_order,
        )):
            raise ScheduleGenerationError(
                f'{slug} is in the wrong seat in {season_year}.'
            )
    shape_checks = (
        (1976, 'conference-1', (('d1', 4), ('d2', 5), ('d3', 5))),
        (1976, 'conference-2', (('d1', 4), ('d2', 5), ('d3', 5))),
        (1994, 'conference-1', (('d1', 4), ('d2', 5), ('d3', 5))),
        (1994, 'conference-2', (('d1', 5), ('d2', 4), ('d3', 5))),
        (1999, 'conference-1', (('d1', 6), ('d2', 5), ('d3', 5))),
        (1999, 'conference-2', (('d1', 5), ('d2', 5), ('d3', 5))),
        (2002, 'conference-1', (('d1', 4), ('d2', 4), ('d3', 4), ('d4', 4))),
    )
    for season_year, conference_slug, divisions in shape_checks:
        for division_slug, size in divisions:
            found = FranchiseSeasonMembership.objects.filter(
                franchise__release=release,
                season_year=season_year,
                conference_slug=conference_slug,
                division_slug=division_slug,
            ).count()
            if found != size:
                raise ScheduleGenerationError(
                    f'{season_year} {conference_slug} {division_slug} has '
                    f'{found} clubs, not {size}.'
                )
    if FranchiseSeasonMembership.objects.filter(
        franchise__release=release,
        season_year__in=(1999, 2000, 2001),
        division_slug='d4',
    ).exists():
        raise ScheduleGenerationError('1999–2001 uses a fourth division.')
    if FranchiseSeasonMembership.objects.filter(
        franchise__release=release,
        franchise__placeholder_slug='expansion-2002-01',
        season_year__lt=2002,
    ).exists():
        raise ScheduleGenerationError('The 2002 club has a membership before its debut.')
    if release.expansion_events.count() != 4 or ExpansionClub.objects.filter(
        event__release=release,
    ).count() != 6:
        raise ScheduleGenerationError('The schedule did not write four expansion events.')
    if UniverseStaffIdentity.objects.filter(release=release).count() != POOL_SIZE * 2:
        raise ScheduleGenerationError('The schedule did not write 200 staff identities.')
    if OpeningStaffAssignment.objects.filter(release=release, role=ROLE_GM).count() != OPENING_SEATS:
        raise ScheduleGenerationError('The schedule did not seat 26 GMs.')
    if OpeningStaffAssignment.objects.filter(
        release=release,
        role=ROLE_HEAD_COACH,
    ).count() != OPENING_SEATS:
        raise ScheduleGenerationError('The schedule did not seat 26 head coaches.')
    if OpeningStaffAssignment.objects.filter(release=release).exclude(
        franchise__placeholder_slug__in=[f'founding-{number:02d}' for number in range(1, 27)],
    ).exists():
        raise ScheduleGenerationError('An expansion club has an opening seat.')
    if release.provisional_franchise_names.count() != 32:
        raise ScheduleGenerationError('The schedule did not write 32 provisional names.')
    cities = list(release.provisional_franchise_names.values_list('city', flat=True))
    nicknames = list(release.provisional_franchise_names.values_list('nickname', flat=True))
    if len(set(cities)) != 32 or len(set(nicknames)) != 32:
        raise ScheduleGenerationError('Provisional cities or nicknames are not unique.')
    if UniversePlayer.objects.filter(release=release).exists():
        raise ScheduleGenerationError('This generator wrote a player.')
    if HistoricalStanding.objects.filter(franchise__release=release).exists():
        raise ScheduleGenerationError('This generator wrote a historical standing.')


def generate_schedule_release(league_id, seed):
    """Write the fg-founding-2 slice. Return created, rebuilt, or unchanged."""
    try:
        _validate_seed(seed)
        release_id = _resolve_release_id(league_id)
    except FoundingGenerationError as exc:
        raise ScheduleGenerationError(str(exc)) from exc
    try:
        with holding_release_lock(release_id, using=DEFAULT_DB_ALIAS) as release:
            if release.status != UniverseRelease.Status.DRAFT:
                raise ScheduleGenerationError('A published release cannot be changed.')
            _require_horizon(release)
            if Timeline.objects.filter(release=release).exists():
                raise ScheduleGenerationError('A timeline points at this release.')
            fingerprint = release.catalog_fingerprint
            validation = release.validation_fingerprint
            schedule_version = release.expansion_schedule_version
            try:
                stored = _stored_names(release)
                redraw = bool(stored) and release.seed != seed
                names = resolve_names(
                    seed,
                    release.slug,
                    stored,
                    redraw=redraw,
                )
            except ProvisionalNameError as exc:
                raise ScheduleGenerationError(str(exc)) from exc
            expected = _expected_slice(
                seed,
                release.slug,
                names,
                _preserved_rules(release),
            )
            rows_match = _slice_matches(release, expected)
            metadata_match = (
                release.generator_version == GENERATOR_VERSION
                and release.seed == seed
            )
            if rows_match and metadata_match:
                return 'unchanged'
            if rows_match:
                release.generator_version = GENERATOR_VERSION
                release.seed = seed
                release.save(update_fields=['generator_version', 'seed'])
                return 'rebuilt'
            franchises = list(release.franchises.all())
            old_keys = _replacement_keys(release)
            _refuse_blockers(release, franchises, old_keys)
            existed = _slice_existed(release, franchises, old_keys)
            _delete_slice(release, franchises, old_keys)
            _write_slice(release, expected)
            _validate_schedule_slice(release)
            release.generator_version = GENERATOR_VERSION
            release.seed = seed
            release.save(update_fields=['generator_version', 'seed'])
            release.refresh_from_db()
            if (
                release.catalog_fingerprint != fingerprint
                or release.validation_fingerprint != validation
                or release.expansion_schedule_version != schedule_version
                or release.status != UniverseRelease.Status.DRAFT
            ):
                raise ScheduleGenerationError(
                    'Fingerprints, publication, or the expansion schedule version changed.'
                )
            return 'rebuilt' if existed else 'created'
    except ProtectedError as exc:
        raise ScheduleGenerationError(
            'Another row still references this generator\'s slice.'
        ) from exc
    except IntegrityError as exc:
        raise ScheduleGenerationError(
            'A generated key already exists outside this release.'
        ) from exc
