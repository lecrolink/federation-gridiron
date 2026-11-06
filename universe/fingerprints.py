"""Canonical SHA-256 fingerprints for one universe release.

catalog-sha256-v1 is the structural map. validation-sha256-v1 is the shared
content a timeline uses. Both bind the release identity. Only
``fg-founding-2`` selects this pair. The functions read rows and return
digests. They do not write fingerprints, change publication, or create a
timeline.

External league, conference, division, team, and movement rows are not
hashed, except the release's root-league slug and a franchise's catalog-team
slug from the approved scheme. Display uses release-owned names and
memberships.
"""

import hashlib
import json

from django.db.models import Q

from universe.models import (
    FranchiseHistoryEvent,
    FranchiseSeasonMembership,
    HistoricalStanding,
    ProvisionalFranchiseName,
    UniverseFranchise,
    UniverseRelease,
)
from universe.people import (
    COACH_ABILITIES,
    College,
    CoachRatingProfile,
    GM_ABILITIES,
    GMRatingProfile,
    PLAYER_COMPONENTS,
    POSITIONS,
    PlayerRatingProfile,
    UniverseAward,
    UniverseCoach,
    UniverseCollegeSeason,
    UniverseGM,
    UniversePlayer,
    UniverseProSeason,
    UniverseProStint,
    UniverseStaffIdentity,
    UniverseStaffPost,
)
from universe.rules import (
    DraftClass,
    EraAllowedScheme,
    EraCollegeWeight,
    EraRuleProfile,
    ExpansionClub,
    ExpansionEvent,
    OpeningAssignment,
    OpeningContract,
    OpeningStaffAssignment,
)


class FingerprintSchemeError(Exception):
    """This generator version has no approved fingerprint scheme."""


class FingerprintSchemaError(Exception):
    """The payload or the models do not match the approved v1 field list."""


CATALOG_SCHEME = 'catalog-sha256-v1'
VALIDATION_SCHEME = 'validation-sha256-v1'
SCHEME_BY_GENERATOR = {
    'fg-founding-2': (CATALOG_SCHEME, VALIDATION_SCHEME),
}

CATALOG_RECORD_KEYS = (
    'expansion_clubs',
    'expansion_events',
    'franchises',
    'memberships',
)

VALIDATION_RECORD_KEYS = (
    'awards',
    'coach_profiles',
    'coach_rating_profiles',
    'college_seasons',
    'college_weights',
    'colleges',
    'draft_classes',
    'era_profiles',
    'era_schemes',
    'expansion_rules',
    'franchise_names',
    'gm_profiles',
    'gm_rating_profiles',
    'historical_standings',
    'history_events',
    'opening_assignments',
    'opening_contracts',
    'opening_staff',
    'player_rating_profiles',
    'players',
    'pro_seasons',
    'pro_stints',
    'provisional_names',
    'staff_identities',
    'staff_posts',
)

APPROVED_PLAYER_COMPONENTS = (
    'speed', 'acceleration', 'agility', 'strength', 'stamina', 'durability',
    'awareness', 'discipline', 'processing', 'throw_power', 'throw_accuracy',
    'catching', 'route_running', 'carrying', 'elusiveness', 'run_blocking',
    'pass_blocking', 'tackling', 'coverage', 'pass_rush', 'run_defense',
    'kick_power', 'kick_accuracy', 'punt_power', 'punt_accuracy',
    'return_vision', 'special_teams', 'work_ethic', 'leadership',
    'temperament', 'clutch', 'consistency', 'adaptability',
)
APPROVED_GM_ABILITIES = (
    'talent_evaluation', 'scheme_fit', 'negotiation', 'cap_management',
    'draft_strategy', 'free_agency', 'roster_construction',
    'player_development', 'discipline', 'leadership', 'risk_tolerance',
    'patience', 'coach_evaluation', 'veteran_preference',
    'pick_trade_willingness',
)
APPROVED_COACH_ABILITIES = (
    'offensive_scheme', 'defensive_scheme', 'game_management',
    'clock_management', 'fourth_down_aggression', 'aggressiveness',
    'in_game_adjustments', 'adaptability', 'player_development',
    'preparation', 'motivation', 'leadership', 'discipline',
    'fatigue_management', 'injury_management', 'staff_management',
    'veteran_preference',
)
APPROVED_POSITIONS = (
    'QB', 'HB', 'FB', 'WR', 'TE', 'LT', 'LG', 'C', 'RG', 'RT',
    'DE', 'DT', 'ILB', 'OLB', 'CB', 'FS', 'SS', 'K', 'P',
)

SEASON_STAT_FIELDS = (
    'games', 'starts', 'offensive_snaps', 'defensive_snaps',
    'special_teams_snaps', 'penalties', 'penalty_yards', 'fumbles',
    'fumbles_lost', 'pass_attempts', 'completions', 'pass_yards',
    'pass_touchdowns', 'interceptions_thrown', 'sacks_taken', 'sack_yards',
    'rush_attempts', 'rush_yards', 'rush_touchdowns', 'targets', 'receptions',
    'receiving_yards', 'receiving_touchdowns', 'drops', 'run_block_snaps',
    'pass_block_snaps', 'tackles', 'tackles_for_loss', 'sacks',
    'quarterback_hits', 'forced_fumbles', 'fumble_recoveries',
    'passes_defended', 'defensive_touchdowns', 'coverage_snaps',
    'targets_in_coverage', 'completions_allowed', 'interceptions',
    'yards_allowed', 'sacks_allowed', 'fg_made', 'fg_attempts', 'fg_long',
    'xp_made', 'xp_attempts', 'kickoff_touchbacks', 'punts', 'punt_yards',
    'punts_inside_20', 'punt_touchbacks', 'punt_long',
)

EXPANSION_PROSE_FIELDS = (
    'expansion_draft_order_rule',
    'roster_building_rule',
    'free_agent_rule',
    'rookie_draft_slot_rule',
    'gm_hiring_rule',
    'hc_hiring_rule',
)

def _rating_names(components):
    names = []
    for component in components:
        names.append(f'initial_{component}')
        names.append(f'potential_{component}')
    return tuple(names)


def _quota_names():
    names = []
    for position in APPROVED_POSITIONS:
        lowered = position.lower()
        names.append(f'quota_active_{lowered}')
        names.append(f'quota_taxi_{lowered}')
    return tuple(names)


def _types(pairs):
    return dict(pairs)


def _field_types():
    player_ratings = [('universe_key', 'str')]
    for name in _rating_names(APPROVED_PLAYER_COMPONENTS):
        player_ratings.append((name, 'int'))
    gm_ratings = [('universe_key', 'str'), ('initial_reputation', 'int')]
    for name in _rating_names(APPROVED_GM_ABILITIES):
        gm_ratings.append((name, 'int'))
    coach_ratings = [('universe_key', 'str'), ('initial_reputation', 'int')]
    for name in _rating_names(APPROVED_COACH_ABILITIES):
        coach_ratings.append((name, 'int'))
    era = [
        ('profile_key', 'str'),
        ('first_year', 'int'),
        ('last_year', 'int'),
        ('market', 'str'),
        ('style_key', 'str'),
        ('active_limit', 'int'),
        ('taxi_limit', 'int'),
        ('ir_max', 'int'),
        ('ir_min_weeks', 'int'),
        ('ir_returns_allowed', 'int'),
        ('draft_rounds', 'int'),
        ('rookie_contract_years_min', 'int'),
        ('rookie_contract_years_max', 'int'),
        ('veteran_contract_years_min', 'int'),
        ('veteran_contract_years_max', 'int'),
        ('accrued_seasons_for_ufa', 'int?'),
        ('trade_deadline_week', 'int'),
        ('future_pick_years', 'int'),
        ('waiver_rule', 'str'),
        ('free_agency_type', 'str'),
        ('fa_compensation', 'str'),
        ('cap_status', 'str'),
        ('cap_limit', 'int?'),
        ('minimum_salary', 'int?'),
        ('franchise_tag', 'str'),
        ('playoff_format', 'str'),
        ('schedule_length', 'int'),
        ('overtime_rule', 'str'),
        ('sack_leaderboard', 'str'),
        ('rookie_slot_scale', 'json_null'),
    ]
    for name in _quota_names():
        era.append((name, 'int'))
    college_season = [
        ('universe_key', 'str'),
        ('season_year', 'int'),
        ('class_standing', 'str'),
    ]
    college_season.extend((name, 'int') for name in SEASON_STAT_FIELDS)
    pro_season = [
        ('universe_key', 'str'),
        ('franchise_slug', 'str'),
        ('season_year', 'int'),
    ]
    pro_season.extend((name, 'int') for name in SEASON_STAT_FIELDS)
    return {
        'identity': _types((
            ('expansion_schedule_version', 'int'),
            ('first_season_year', 'int'),
            ('founding_franchise_count', 'int'),
            ('generator_version', 'str'),
            ('last_season_year', 'int'),
            ('root_league_slug', 'str'),
            ('season_count', 'int'),
            ('seed', 'int'),
            ('slug', 'str'),
        )),
        'franchises': _types((
            ('placeholder_slug', 'str'),
            ('origin', 'str'),
            ('first_active_season', 'int'),
            ('final_active_season', 'int?'),
            ('predecessor_slug', 'str?'),
            ('catalog_team_slug', 'str?'),
        )),
        'memberships': _types((
            ('placeholder_slug', 'str'),
            ('season_year', 'int'),
            ('conference_slug', 'str'),
            ('division_slug', 'str'),
            ('slot_order', 'int'),
            ('owner_selectable', 'bool'),
        )),
        'expansion_events': _types((
            ('season_year', 'int'),
            ('existing_club_count', 'int'),
            ('new_club_count', 'int'),
            ('active_target', 'int'),
            ('taxi_capacity', 'int'),
            ('max_losses_per_existing', 'int'),
            ('min_losses_per_existing', 'int'),
            ('clubs_at_max_loss', 'int'),
            ('clubs_at_min_loss', 'int'),
            ('veteran_picks_per_new', 'int'),
            ('veteran_picks_total', 'int'),
            ('rookie_picks_per_new', 'int'),
            ('undrafted_multiplier', 'int'),
            ('undrafted_pool', 'int'),
            ('undrafted_reserved_per_new', 'int'),
        )),
        'expansion_clubs': _types((
            ('season_year', 'int'),
            ('placeholder_slug', 'str'),
            ('selection_order', 'int'),
        )),
        'expansion_rules': _types(
            (('season_year', 'int'),) + tuple(
                (name, 'str') for name in EXPANSION_PROSE_FIELDS
            ),
        ),
        'franchise_names': _types((
            ('placeholder_slug', 'str'),
            ('display_name', 'str'),
        )),
        'provisional_names': _types((
            ('placeholder_slug', 'str'),
            ('city', 'str'),
            ('city_status', 'str'),
            ('nickname', 'str'),
            ('nickname_status', 'str'),
        )),
        'history_events': _types((
            ('placeholder_slug', 'str'),
            ('season_year', 'int'),
            ('kind', 'str'),
            ('previous_name', 'str'),
            ('new_name', 'str'),
            ('city', 'str'),
            ('notes', 'str'),
        )),
        'historical_standings': _types((
            ('placeholder_slug', 'str'),
            ('season_year', 'int'),
            ('wins', 'int'),
            ('losses', 'int'),
            ('ties', 'int'),
            ('draft_order', 'int?'),
        )),
        'colleges': _types((
            ('slug', 'str'),
            ('name', 'str'),
        )),
        'college_weights': _types((
            ('profile_key', 'str'),
            ('college_slug', 'str'),
            ('weight', 'int'),
        )),
        'era_schemes': _types((
            ('profile_key', 'str'),
            ('side', 'str'),
            ('scheme', 'str'),
        )),
        'era_profiles': _types(era),
        'draft_classes': _types((
            ('season_year', 'int'),
            ('profile_key', 'str'),
            ('franchise_count', 'int'),
            ('rounds', 'int'),
            ('draftable_count', 'int'),
            ('undrafted_multiplier', 'int'),
            ('undrafted_count', 'int'),
        )),
        'players': _types((
            ('universe_key', 'str'),
            ('name', 'str'),
            ('birth_year', 'int'),
            ('birth_day', 'int'),
            ('position', 'str'),
            ('secondary_position_1', 'str?'),
            ('secondary_position_2', 'str?'),
            ('handedness', 'str'),
            ('height_in', 'int'),
            ('weight_lb', 'int'),
            ('cohort', 'str'),
            ('draft_pool', 'str?'),
            ('first_pro_year', 'int?'),
            ('origin_draft_year', 'int?'),
            ('origin_draft_round', 'int?'),
            ('origin_draft_pick', 'int?'),
            ('peak_age', 'int'),
            ('development_rate', 'int'),
            ('decline_rate', 'int'),
            ('board_rank', 'int?'),
            ('trait_1', 'str?'),
            ('trait_2', 'str?'),
            ('trait_3', 'str?'),
            ('college_slug', 'str'),
            ('origin_franchise_slug', 'str?'),
            ('draft_class_year', 'int?'),
            ('origin_profile_key', 'str'),
        )),
        'player_rating_profiles': _types(player_ratings),
        'opening_assignments': _types((
            ('universe_key', 'str'),
            ('franchise_slug', 'str?'),
            ('role', 'str'),
            ('depth', 'int?'),
        )),
        'opening_contracts': _types((
            ('universe_key', 'str'),
            ('years', 'int'),
            ('salary_per_year', 'int'),
            ('guaranteed', 'int'),
            ('signing_bonus', 'int'),
            ('start_year', 'int'),
            ('end_year', 'int'),
        )),
        'opening_staff': _types((
            ('franchise_slug', 'str'),
            ('role', 'str'),
            ('gm_universe_key', 'str?'),
            ('coach_universe_key', 'str?'),
        )),
        'staff_identities': _types((
            ('universe_key', 'str'),
            ('name', 'str'),
            ('birth_year', 'int'),
        )),
        'gm_profiles': _types((
            ('universe_key', 'str'),
            ('name', 'str'),
            ('birth_year', 'int'),
            ('debut_year', 'int'),
            ('former_role', 'str'),
            ('positional_priority_1', 'str'),
            ('positional_priority_2', 'str?'),
            ('strength_1', 'str'),
            ('strength_2', 'str'),
            ('weakness', 'str'),
            ('trait_1', 'str'),
            ('trait_2', 'str'),
        )),
        'gm_rating_profiles': _types(gm_ratings),
        'coach_profiles': _types((
            ('universe_key', 'str'),
            ('name', 'str'),
            ('birth_year', 'int'),
            ('debut_year', 'int'),
            ('specialty', 'str'),
            ('former_role', 'str'),
            ('preferred_offense', 'str'),
            ('preferred_defense', 'str'),
            ('strength_1', 'str'),
            ('strength_2', 'str'),
            ('weakness', 'str'),
            ('trait_1', 'str'),
            ('trait_2', 'str'),
        )),
        'coach_rating_profiles': _types(coach_ratings),
        'staff_posts': _types((
            ('person_kind', 'str'),
            ('universe_key', 'str'),
            ('role', 'str'),
            ('start_year', 'int'),
            ('end_year', 'int'),
            ('franchise_slug', 'str?'),
            ('college_slug', 'str?'),
            ('organization_name', 'str'),
            ('wins', 'int'),
            ('losses', 'int'),
            ('ties', 'int'),
            ('playoff_appearances', 'int'),
            ('championships', 'int'),
            ('ended_by', 'str'),
        )),
        'college_seasons': _types(college_season),
        'pro_stints': _types((
            ('universe_key', 'str'),
            ('franchise_slug', 'str'),
            ('start_year', 'int'),
            ('end_year', 'int'),
            ('role', 'str'),
        )),
        'pro_seasons': _types(pro_season),
        'awards': _types((
            ('season_year', 'int'),
            ('kind', 'str'),
            ('slot', 'str'),
            ('franchise_slug', 'str'),
            ('recipient_kind', 'str'),
            ('universe_key', 'str'),
        )),
    }


FIELD_TYPES = _field_types()


def _typed(value, kind, label):
    if kind == 'json_null':
        if value is not None:
            raise FingerprintSchemaError(f'{label} is unsupported unless null.')
        return None
    if kind == 'bool':
        if not isinstance(value, bool):
            raise FingerprintSchemaError(f'{label} must be a boolean.')
        return value
    if kind in ('int', 'int?'):
        if value is None and kind == 'int?':
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise FingerprintSchemaError(f'{label} must be an integer.')
        return value
    if kind in ('str', 'str?'):
        if value is None and kind == 'str?':
            return None
        if not isinstance(value, str):
            raise FingerprintSchemaError(f'{label} must be a string.')
        return value
    raise FingerprintSchemaError(f'{label} has an unknown value kind.')


def validate_record(group, record):
    """Return a record with exactly the frozen keys and value kinds."""
    expected = FIELD_TYPES[group]
    if not isinstance(record, dict):
        raise FingerprintSchemaError(f'{group} record must be an object.')
    if set(record) != set(expected):
        missing = sorted(set(expected) - set(record))
        extra = sorted(set(record) - set(expected))
        raise FingerprintSchemaError(
            f'{group} keys differ. missing={missing} extra={extra}'
        )
    return {
        key: _typed(record[key], kind, f'{group}.{key}')
        for key, kind in expected.items()
    }


def _record_sort_key(record):
    return json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    ).encode('utf-8')


def canonical_bytes(scheme, identity, records):
    """Encode one approved document. Reject a drifted group or field list."""
    check_v1_model_coverage()
    if scheme == CATALOG_SCHEME:
        groups = CATALOG_RECORD_KEYS
    elif scheme == VALIDATION_SCHEME:
        groups = VALIDATION_RECORD_KEYS
    else:
        raise FingerprintSchemaError(f'Unsupported fingerprint scheme {scheme}.')
    if set(records) != set(groups):
        raise FingerprintSchemaError(
            f'{scheme} records keys differ from the frozen list.'
        )
    for group, rows in records.items():
        if not isinstance(rows, (list, tuple)):
            raise FingerprintSchemaError(f'{group} must be an array.')
    packed_records = {}
    for group in groups:
        packed = [validate_record(group, row) for row in records[group]]
        packed.sort(key=_record_sort_key)
        packed_records[group] = packed
    document = {
        'scheme': scheme,
        'identity': validate_record('identity', identity),
        'records': packed_records,
    }
    try:
        text = json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
        )
    except TypeError as exc:
        raise FingerprintSchemaError('A fingerprint value cannot be encoded.') from exc
    return (text + '\n').encode('utf-8')


def _coverage():
    quota = set(_quota_names())
    stats = set(SEASON_STAT_FIELDS)
    player_rating = set(_rating_names(APPROVED_PLAYER_COMPONENTS))
    gm_rating = set(_rating_names(APPROVED_GM_ABILITIES)) | {'initial_reputation'}
    coach_rating = set(_rating_names(APPROVED_COACH_ABILITIES)) | {'initial_reputation'}
    era = {
        'profile_key', 'first_year', 'last_year', 'market', 'style_key',
        'active_limit', 'taxi_limit', 'ir_max', 'ir_min_weeks',
        'ir_returns_allowed', 'draft_rounds', 'rookie_contract_years_min',
        'rookie_contract_years_max', 'veteran_contract_years_min',
        'veteran_contract_years_max', 'accrued_seasons_for_ufa',
        'trade_deadline_week', 'future_pick_years', 'waiver_rule',
        'free_agency_type', 'fa_compensation', 'cap_status', 'cap_limit',
        'minimum_salary', 'franchise_tag', 'playoff_format', 'schedule_length',
        'overtime_rule', 'sack_leaderboard', 'rookie_slot_scale',
    } | quota
    rows = (
        (UniverseRelease, {
            'slug', 'generator_version', 'seed', 'founding_franchise_count',
            'expansion_schedule_version', 'first_season_year', 'season_count',
            'last_season_year',
        }, {
            'id', 'root_league', 'catalog_fingerprint', 'validation_fingerprint',
            'status', 'created_at', 'published_at',
        }),
        (UniverseFranchise, {
            'placeholder_slug', 'display_name', 'origin', 'first_active_season',
            'final_active_season',
        }, {'id', 'release', 'catalog_team', 'history_predecessor'}),
        (ProvisionalFranchiseName, {
            'placeholder_slug', 'city', 'city_status', 'nickname', 'nickname_status',
        }, {'id', 'release'}),
        (FranchiseSeasonMembership, {
            'season_year', 'conference_slug', 'division_slug', 'slot_order',
            'owner_selectable',
        }, {'id', 'franchise'}),
        (FranchiseHistoryEvent, {
            'season_year', 'kind', 'previous_name', 'new_name', 'city', 'notes',
        }, {'id', 'franchise'}),
        (HistoricalStanding, {
            'season_year', 'wins', 'losses', 'ties', 'draft_order',
        }, {'id', 'franchise'}),
        (College, {'slug', 'name'}, {'id', 'release'}),
        (UniversePlayer, {
            'universe_key', 'name', 'birth_year', 'birth_day', 'position',
            'secondary_position_1', 'secondary_position_2', 'handedness',
            'height_in', 'weight_lb', 'cohort', 'draft_pool', 'first_pro_year',
            'origin_draft_year', 'origin_draft_round', 'origin_draft_pick',
            'peak_age', 'development_rate', 'decline_rate', 'board_rank',
            'trait_1', 'trait_2', 'trait_3',
        }, {
            'id', 'release', 'college', 'origin_franchise', 'draft_class',
            'origin_profile',
        }),
        (PlayerRatingProfile, player_rating, {'id', 'player'}),
        (UniverseCollegeSeason, {'season_year', 'class_standing'} | stats, {'id', 'player'}),
        (UniverseProStint, {'start_year', 'end_year', 'role'}, {'id', 'player', 'franchise'}),
        (UniverseProSeason, {'season_year'} | stats, {'id', 'player', 'franchise'}),
        (UniverseStaffIdentity, {'universe_key', 'name', 'birth_year'}, {'id', 'release'}),
        (UniverseGM, {
            'universe_key', 'name', 'birth_year', 'debut_year', 'former_role',
            'positional_priority_1', 'positional_priority_2', 'strength_1',
            'strength_2', 'weakness', 'trait_1', 'trait_2',
        }, {'id', 'release', 'identity'}),
        (GMRatingProfile, gm_rating, {'id', 'gm'}),
        (UniverseCoach, {
            'universe_key', 'name', 'birth_year', 'debut_year', 'specialty',
            'former_role', 'preferred_offense', 'preferred_defense',
            'strength_1', 'strength_2', 'weakness', 'trait_1', 'trait_2',
        }, {'id', 'release', 'identity'}),
        (CoachRatingProfile, coach_rating, {'id', 'coach'}),
        (UniverseStaffPost, {
            'organization_name', 'role', 'start_year', 'end_year', 'wins',
            'losses', 'ties', 'playoff_appearances', 'championships', 'ended_by',
        }, {'id', 'gm', 'coach', 'franchise', 'college'}),
        (UniverseAward, {'season_year', 'kind', 'slot'}, {
            'id', 'release', 'franchise', 'player', 'gm', 'coach',
        }),
        (EraRuleProfile, era, {'id', 'release'}),
        (EraAllowedScheme, {'side', 'scheme'}, {'id', 'profile'}),
        (EraCollegeWeight, {'weight'}, {'id', 'profile', 'college'}),
        (DraftClass, {
            'season_year', 'franchise_count', 'rounds', 'draftable_count',
            'undrafted_multiplier', 'undrafted_count',
        }, {'id', 'release', 'profile'}),
        (ExpansionEvent, {
            'season_year', 'existing_club_count', 'new_club_count',
            'active_target', 'taxi_capacity', 'max_losses_per_existing',
            'min_losses_per_existing', 'clubs_at_max_loss', 'clubs_at_min_loss',
            'veteran_picks_per_new', 'veteran_picks_total', 'rookie_picks_per_new',
            'undrafted_multiplier', 'undrafted_pool', 'undrafted_reserved_per_new',
        } | set(EXPANSION_PROSE_FIELDS), {'id', 'release'}),
        (ExpansionClub, {'selection_order'}, {'id', 'event', 'franchise'}),
        (OpeningAssignment, {'role', 'depth'}, {'id', 'release', 'player', 'franchise'}),
        (OpeningContract, {
            'years', 'salary_per_year', 'guaranteed', 'signing_bonus',
            'start_year', 'end_year',
        }, {'id', 'assignment'}),
        (OpeningStaffAssignment, {'role'}, {'id', 'release', 'franchise', 'gm', 'coach'}),
    )
    return rows


def check_v1_model_coverage():
    """Raise if the approved lists and the concrete models disagree."""
    problems = []
    if tuple(PLAYER_COMPONENTS) != APPROVED_PLAYER_COMPONENTS:
        problems.append('PLAYER_COMPONENTS differs from the approved list.')
    if tuple(GM_ABILITIES) != APPROVED_GM_ABILITIES:
        problems.append('GM_ABILITIES differs from the approved list.')
    if tuple(COACH_ABILITIES) != APPROVED_COACH_ABILITIES:
        problems.append('COACH_ABILITIES differs from the approved list.')
    if tuple(POSITIONS) != APPROVED_POSITIONS:
        problems.append('POSITIONS differs from the approved list.')
    if len(VALIDATION_RECORD_KEYS) != 25 or len(set(VALIDATION_RECORD_KEYS)) != 25:
        problems.append('validation records must have exactly 25 keys.')
    if 'staff_identities' not in VALIDATION_RECORD_KEYS:
        problems.append('staff_identities is missing from validation records.')
    if set(FIELD_TYPES) != set(VALIDATION_RECORD_KEYS) | set(CATALOG_RECORD_KEYS) | {'identity'}:
        problems.append('FIELD_TYPES does not match the frozen record groups.')
    for model, included, excluded in _coverage():
        concrete = {field.name for field in model._meta.concrete_fields}
        if concrete != included | excluded or included & excluded:
            problems.append(
                f'{model.__name__} fields differ. '
                f'missing={sorted(concrete - included - excluded)} '
                f'extra={sorted((included | excluded) - concrete)}'
            )
    if problems:
        raise FingerprintSchemaError(' '.join(problems))


def _identity(release):
    return {
        'slug': release.slug,
        'seed': release.seed,
        'generator_version': release.generator_version,
        'founding_franchise_count': release.founding_franchise_count,
        'expansion_schedule_version': release.expansion_schedule_version,
        'first_season_year': release.first_season_year,
        'season_count': release.season_count,
        'last_season_year': release.last_season_year,
        'root_league_slug': release.root_league.slug,
    }


def _rows(queryset, mapping):
    records = []
    for source in queryset:
        record = {}
        for key, source_key in mapping:
            record[key] = source[source_key]
        records.append(record)
    return records


def _person(gm_key, coach_key, label):
    if gm_key and coach_key:
        raise FingerprintSchemaError(f'{label} has two people.')
    if gm_key:
        return 'gm', gm_key
    if coach_key:
        return 'coach', coach_key
    raise FingerprintSchemaError(f'{label} has no person.')


def _load(release):
    franchise_rows = list(UniverseFranchise.objects.filter(release=release).values(
        'placeholder_slug', 'origin', 'first_active_season', 'final_active_season',
        'display_name', 'history_predecessor__placeholder_slug', 'catalog_team__slug',
    ))
    expansion_rows = list(ExpansionEvent.objects.filter(release=release).values(
        'season_year', 'existing_club_count', 'new_club_count', 'active_target',
        'taxi_capacity', 'max_losses_per_existing', 'min_losses_per_existing',
        'clubs_at_max_loss', 'clubs_at_min_loss', 'veteran_picks_per_new',
        'veteran_picks_total', 'rookie_picks_per_new', 'undrafted_multiplier',
        'undrafted_pool', 'undrafted_reserved_per_new', *EXPANSION_PROSE_FIELDS,
    ))
    era_names = [
        name for name in FIELD_TYPES['era_profiles']
    ]
    rating_names = [name for name in FIELD_TYPES['player_rating_profiles'] if name != 'universe_key']
    gm_rating_names = [name for name in FIELD_TYPES['gm_rating_profiles'] if name != 'universe_key']
    coach_rating_names = [
        name for name in FIELD_TYPES['coach_rating_profiles'] if name != 'universe_key'
    ]
    stat_names = list(SEASON_STAT_FIELDS)
    return {
        'franchises': franchise_rows,
        'memberships': list(FranchiseSeasonMembership.objects.filter(
            franchise__release=release,
        ).values(
            'franchise__placeholder_slug', 'season_year', 'conference_slug',
            'division_slug', 'slot_order', 'owner_selectable',
        )),
        'expansion_events': expansion_rows,
        'expansion_clubs': list(ExpansionClub.objects.filter(event__release=release).values(
            'event__season_year', 'franchise__placeholder_slug', 'selection_order',
        )),
        'names': list(ProvisionalFranchiseName.objects.filter(release=release).values(
            'placeholder_slug', 'city', 'city_status', 'nickname', 'nickname_status',
        )),
        'history': list(FranchiseHistoryEvent.objects.filter(
            franchise__release=release,
        ).values(
            'franchise__placeholder_slug', 'season_year', 'kind', 'previous_name',
            'new_name', 'city', 'notes',
        )),
        'standings': list(HistoricalStanding.objects.filter(
            franchise__release=release,
        ).values(
            'franchise__placeholder_slug', 'season_year', 'wins', 'losses', 'ties',
            'draft_order',
        )),
        'colleges': list(College.objects.filter(release=release).values('slug', 'name')),
        'weights': list(EraCollegeWeight.objects.filter(profile__release=release).values(
            'profile__profile_key', 'college__slug', 'weight',
        )),
        'schemes': list(EraAllowedScheme.objects.filter(profile__release=release).values(
            'profile__profile_key', 'side', 'scheme',
        )),
        'eras': list(EraRuleProfile.objects.filter(release=release).values(*era_names)),
        'classes': list(DraftClass.objects.filter(release=release).values(
            'season_year', 'profile__profile_key', 'franchise_count', 'rounds',
            'draftable_count', 'undrafted_multiplier', 'undrafted_count',
        )),
        'players': list(UniversePlayer.objects.filter(release=release).values(
            'universe_key', 'name', 'birth_year', 'birth_day', 'position',
            'secondary_position_1', 'secondary_position_2', 'handedness',
            'height_in', 'weight_lb', 'cohort', 'draft_pool', 'first_pro_year',
            'origin_draft_year', 'origin_draft_round', 'origin_draft_pick',
            'peak_age', 'development_rate', 'decline_rate', 'board_rank',
            'trait_1', 'trait_2', 'trait_3', 'college__slug',
            'origin_franchise__placeholder_slug', 'draft_class__season_year',
            'origin_profile__profile_key',
        )),
        'player_ratings': list(PlayerRatingProfile.objects.filter(
            player__release=release,
        ).values('player__universe_key', *rating_names)),
        'assignments': list(OpeningAssignment.objects.filter(release=release).values(
            'player__universe_key', 'franchise__placeholder_slug', 'role', 'depth',
        )),
        'contracts': list(OpeningContract.objects.filter(
            assignment__release=release,
        ).values(
            'assignment__player__universe_key', 'years', 'salary_per_year',
            'guaranteed', 'signing_bonus', 'start_year', 'end_year',
        )),
        'opening_staff': list(OpeningStaffAssignment.objects.filter(release=release).values(
            'franchise__placeholder_slug', 'role', 'gm__universe_key',
            'coach__universe_key',
        )),
        'identities': list(UniverseStaffIdentity.objects.filter(release=release).values(
            'universe_key', 'name', 'birth_year',
        )),
        'gms': list(UniverseGM.objects.filter(release=release).values(
            'universe_key', 'name', 'birth_year', 'debut_year', 'former_role',
            'positional_priority_1', 'positional_priority_2', 'strength_1',
            'strength_2', 'weakness', 'trait_1', 'trait_2',
        )),
        'gm_ratings': list(GMRatingProfile.objects.filter(gm__release=release).values(
            'gm__universe_key', *gm_rating_names,
        )),
        'coaches': list(UniverseCoach.objects.filter(release=release).values(
            'universe_key', 'name', 'birth_year', 'debut_year', 'specialty',
            'former_role', 'preferred_offense', 'preferred_defense',
            'strength_1', 'strength_2', 'weakness', 'trait_1', 'trait_2',
        )),
        'coach_ratings': list(CoachRatingProfile.objects.filter(
            coach__release=release,
        ).values('coach__universe_key', *coach_rating_names)),
        'posts': list(UniverseStaffPost.objects.filter(
            Q(gm__release=release) | Q(coach__release=release),
        ).values(
            'gm__universe_key', 'coach__universe_key', 'role', 'start_year',
            'end_year', 'franchise__placeholder_slug', 'college__slug',
            'organization_name', 'wins', 'losses', 'ties', 'playoff_appearances',
            'championships', 'ended_by',
        )),
        'college_seasons': list(UniverseCollegeSeason.objects.filter(
            player__release=release,
        ).values('player__universe_key', 'season_year', 'class_standing', *stat_names)),
        'stints': list(UniverseProStint.objects.filter(player__release=release).values(
            'player__universe_key', 'franchise__placeholder_slug', 'start_year',
            'end_year', 'role',
        )),
        'pro_seasons': list(UniverseProSeason.objects.filter(
            player__release=release,
        ).values(
            'player__universe_key', 'franchise__placeholder_slug', 'season_year',
            *stat_names,
        )),
        'awards': list(UniverseAward.objects.filter(release=release).values(
            'season_year', 'kind', 'slot', 'franchise__placeholder_slug',
            'player__universe_key', 'gm__universe_key', 'coach__universe_key',
        )),
    }


def _catalog_records(loaded):
    return {
        'franchises': [
            {
                'placeholder_slug': row['placeholder_slug'],
                'origin': row['origin'],
                'first_active_season': row['first_active_season'],
                'final_active_season': row['final_active_season'],
                'predecessor_slug': row['history_predecessor__placeholder_slug'],
                'catalog_team_slug': row['catalog_team__slug'],
            }
            for row in loaded['franchises']
        ],
        'memberships': _rows(loaded['memberships'], (
            ('placeholder_slug', 'franchise__placeholder_slug'),
            ('season_year', 'season_year'),
            ('conference_slug', 'conference_slug'),
            ('division_slug', 'division_slug'),
            ('slot_order', 'slot_order'),
            ('owner_selectable', 'owner_selectable'),
        )),
        'expansion_events': [
            {key: row[key] for key in FIELD_TYPES['expansion_events']}
            for row in loaded['expansion_events']
        ],
        'expansion_clubs': _rows(loaded['expansion_clubs'], (
            ('season_year', 'event__season_year'),
            ('placeholder_slug', 'franchise__placeholder_slug'),
            ('selection_order', 'selection_order'),
        )),
    }


def _validation_records(loaded):
    posts = []
    for row in loaded['posts']:
        kind, key = _person(row['gm__universe_key'], row['coach__universe_key'], 'staff post')
        posts.append({
            'person_kind': kind,
            'universe_key': key,
            'role': row['role'],
            'start_year': row['start_year'],
            'end_year': row['end_year'],
            'franchise_slug': row['franchise__placeholder_slug'],
            'college_slug': row['college__slug'],
            'organization_name': row['organization_name'],
            'wins': row['wins'],
            'losses': row['losses'],
            'ties': row['ties'],
            'playoff_appearances': row['playoff_appearances'],
            'championships': row['championships'],
            'ended_by': row['ended_by'],
        })
    awards = []
    for row in loaded['awards']:
        kind, key = _person_award(row)
        awards.append({
            'season_year': row['season_year'],
            'kind': row['kind'],
            'slot': row['slot'],
            'franchise_slug': row['franchise__placeholder_slug'],
            'recipient_kind': kind,
            'universe_key': key,
        })
    stat_keys = list(SEASON_STAT_FIELDS)
    college_seasons = []
    for row in loaded['college_seasons']:
        record = {
            'universe_key': row['player__universe_key'],
            'season_year': row['season_year'],
            'class_standing': row['class_standing'],
        }
        for name in stat_keys:
            record[name] = row[name]
        college_seasons.append(record)
    pro_seasons = []
    for row in loaded['pro_seasons']:
        record = {
            'universe_key': row['player__universe_key'],
            'franchise_slug': row['franchise__placeholder_slug'],
            'season_year': row['season_year'],
        }
        for name in stat_keys:
            record[name] = row[name]
        pro_seasons.append(record)
    player_ratings = []
    for row in loaded['player_ratings']:
        record = {'universe_key': row['player__universe_key']}
        for name in FIELD_TYPES['player_rating_profiles']:
            if name != 'universe_key':
                record[name] = row[name]
        player_ratings.append(record)
    gm_ratings = []
    for row in loaded['gm_ratings']:
        record = {'universe_key': row['gm__universe_key']}
        for name in FIELD_TYPES['gm_rating_profiles']:
            if name != 'universe_key':
                record[name] = row[name]
        gm_ratings.append(record)
    coach_ratings = []
    for row in loaded['coach_ratings']:
        record = {'universe_key': row['coach__universe_key']}
        for name in FIELD_TYPES['coach_rating_profiles']:
            if name != 'universe_key':
                record[name] = row[name]
        coach_ratings.append(record)
    return {
        'awards': awards,
        'coach_profiles': loaded['coaches'],
        'coach_rating_profiles': coach_ratings,
        'college_seasons': college_seasons,
        'college_weights': _rows(loaded['weights'], (
            ('profile_key', 'profile__profile_key'),
            ('college_slug', 'college__slug'),
            ('weight', 'weight'),
        )),
        'colleges': loaded['colleges'],
        'draft_classes': _rows(loaded['classes'], (
            ('season_year', 'season_year'),
            ('profile_key', 'profile__profile_key'),
            ('franchise_count', 'franchise_count'),
            ('rounds', 'rounds'),
            ('draftable_count', 'draftable_count'),
            ('undrafted_multiplier', 'undrafted_multiplier'),
            ('undrafted_count', 'undrafted_count'),
        )),
        'era_profiles': loaded['eras'],
        'era_schemes': _rows(loaded['schemes'], (
            ('profile_key', 'profile__profile_key'),
            ('side', 'side'),
            ('scheme', 'scheme'),
        )),
        'expansion_rules': [
            {
                'season_year': row['season_year'],
                **{name: row[name] for name in EXPANSION_PROSE_FIELDS},
            }
            for row in loaded['expansion_events']
        ],
        'franchise_names': [
            {
                'placeholder_slug': row['placeholder_slug'],
                'display_name': row['display_name'],
            }
            for row in loaded['franchises']
        ],
        'gm_profiles': loaded['gms'],
        'gm_rating_profiles': gm_ratings,
        'historical_standings': _rows(loaded['standings'], (
            ('placeholder_slug', 'franchise__placeholder_slug'),
            ('season_year', 'season_year'),
            ('wins', 'wins'),
            ('losses', 'losses'),
            ('ties', 'ties'),
            ('draft_order', 'draft_order'),
        )),
        'history_events': _rows(loaded['history'], (
            ('placeholder_slug', 'franchise__placeholder_slug'),
            ('season_year', 'season_year'),
            ('kind', 'kind'),
            ('previous_name', 'previous_name'),
            ('new_name', 'new_name'),
            ('city', 'city'),
            ('notes', 'notes'),
        )),
        'opening_assignments': _rows(loaded['assignments'], (
            ('universe_key', 'player__universe_key'),
            ('franchise_slug', 'franchise__placeholder_slug'),
            ('role', 'role'),
            ('depth', 'depth'),
        )),
        'opening_contracts': _rows(loaded['contracts'], (
            ('universe_key', 'assignment__player__universe_key'),
            ('years', 'years'),
            ('salary_per_year', 'salary_per_year'),
            ('guaranteed', 'guaranteed'),
            ('signing_bonus', 'signing_bonus'),
            ('start_year', 'start_year'),
            ('end_year', 'end_year'),
        )),
        'opening_staff': _rows(loaded['opening_staff'], (
            ('franchise_slug', 'franchise__placeholder_slug'),
            ('role', 'role'),
            ('gm_universe_key', 'gm__universe_key'),
            ('coach_universe_key', 'coach__universe_key'),
        )),
        'player_rating_profiles': player_ratings,
        'players': [
            {
                'universe_key': row['universe_key'],
                'name': row['name'],
                'birth_year': row['birth_year'],
                'birth_day': row['birth_day'],
                'position': row['position'],
                'secondary_position_1': row['secondary_position_1'],
                'secondary_position_2': row['secondary_position_2'],
                'handedness': row['handedness'],
                'height_in': row['height_in'],
                'weight_lb': row['weight_lb'],
                'cohort': row['cohort'],
                'draft_pool': row['draft_pool'],
                'first_pro_year': row['first_pro_year'],
                'origin_draft_year': row['origin_draft_year'],
                'origin_draft_round': row['origin_draft_round'],
                'origin_draft_pick': row['origin_draft_pick'],
                'peak_age': row['peak_age'],
                'development_rate': row['development_rate'],
                'decline_rate': row['decline_rate'],
                'board_rank': row['board_rank'],
                'trait_1': row['trait_1'],
                'trait_2': row['trait_2'],
                'trait_3': row['trait_3'],
                'college_slug': row['college__slug'],
                'origin_franchise_slug': row['origin_franchise__placeholder_slug'],
                'draft_class_year': row['draft_class__season_year'],
                'origin_profile_key': row['origin_profile__profile_key'],
            }
            for row in loaded['players']
        ],
        'pro_seasons': pro_seasons,
        'pro_stints': _rows(loaded['stints'], (
            ('universe_key', 'player__universe_key'),
            ('franchise_slug', 'franchise__placeholder_slug'),
            ('start_year', 'start_year'),
            ('end_year', 'end_year'),
            ('role', 'role'),
        )),
        'provisional_names': loaded['names'],
        'staff_identities': loaded['identities'],
        'staff_posts': posts,
    }


def _person_award(row):
    found = []
    if row['player__universe_key']:
        found.append(('player', row['player__universe_key']))
    if row['gm__universe_key']:
        found.append(('gm', row['gm__universe_key']))
    if row['coach__universe_key']:
        found.append(('coach', row['coach__universe_key']))
    if len(found) != 1:
        raise FingerprintSchemaError('An award needs exactly one recipient.')
    return found[0]


def release_documents(release):
    """Return the catalog and validation byte strings for a mapped release."""
    pair = SCHEME_BY_GENERATOR.get(release.generator_version)
    if pair is None:
        raise FingerprintSchemeError(
            f'Fingerprint scheme is unavailable for generator version '
            f'{release.generator_version}.'
        )
    loaded = _load(release)
    identity = _identity(release)
    catalog_scheme, validation_scheme = pair
    return (
        canonical_bytes(catalog_scheme, identity, _catalog_records(loaded)),
        canonical_bytes(validation_scheme, identity, _validation_records(loaded)),
    )


def release_digests(release):
    """Return lowercase SHA-256 hex digests. Do not store them."""
    catalog, validation = release_documents(release)
    return (
        hashlib.sha256(catalog).hexdigest(),
        hashlib.sha256(validation).hexdigest(),
    )
