"""Pure ratings for the 21,155 playable identities.

The release seed is the HMAC key. Each stored universe key is the message
prefix. Potential is drawn first. Initial is that potential minus a gap.
This module does not import the application framework, write a database,
create a model row, read a development rate, or read a decline rate.
"""

from types import MappingProxyType
from typing import NamedTuple

from universe.generation.player_counts import POSITIONS
from universe.generation.player_identities import (
    build_opening_cohort,
    build_prospects,
    hmac_int,
)

RATING_COMPONENTS = (
    'speed',
    'acceleration',
    'agility',
    'strength',
    'stamina',
    'durability',
    'awareness',
    'discipline',
    'processing',
    'throw_power',
    'throw_accuracy',
    'catching',
    'route_running',
    'carrying',
    'elusiveness',
    'run_blocking',
    'pass_blocking',
    'tackling',
    'coverage',
    'pass_rush',
    'run_defense',
    'kick_power',
    'kick_accuracy',
    'punt_power',
    'punt_accuracy',
    'return_vision',
    'special_teams',
    'work_ethic',
    'leadership',
    'temperament',
    'clutch',
    'consistency',
    'adaptability',
)

FAMILY_PRIMARY = 'primary'
FAMILY_SECONDARY = 'secondary'
FAMILY_UNIVERSAL = 'universal'
FAMILY_COGNITIVE = 'cognitive'
FAMILY_PERSONALITY = 'personality'
FAMILY_MINIMAL = 'minimal'
FAMILIES = (
    FAMILY_PRIMARY,
    FAMILY_SECONDARY,
    FAMILY_UNIVERSAL,
    FAMILY_COGNITIVE,
    FAMILY_PERSONALITY,
    FAMILY_MINIMAL,
)

COHORT_STARTER = 'starter'
COHORT_BACKUP = 'backup'
COHORT_TAXI = 'taxi'
COHORT_FREE_AGENT = 'free_agent'
COHORT_DRAFTABLE = 'draftable'
COHORT_UNDRAFTED = 'undrafted'
COHORTS = (
    COHORT_STARTER,
    COHORT_BACKUP,
    COHORT_TAXI,
    COHORT_FREE_AGENT,
    COHORT_DRAFTABLE,
    COHORT_UNDRAFTED,
)

ATHLETIC_COMPONENTS = frozenset((
    'speed',
    'acceleration',
    'agility',
    'strength',
    'stamina',
    'durability',
))
COGNITIVE_COMPONENTS = frozenset((
    'awareness',
    'discipline',
    'processing',
))
PERSONALITY_COMPONENTS = frozenset((
    'work_ethic',
    'leadership',
    'temperament',
    'adaptability',
    'clutch',
    'consistency',
))

MINIMUM_RATING = 1
MAXIMUM_POTENTIAL = 96
PLAYABLE_RATING_COUNT = 21155
COMPONENT_PAIR_COUNT = PLAYABLE_RATING_COUNT * len(RATING_COMPONENTS)

# Inclusive potential. Only primary changes with cohort.
PRIMARY_RANGES = MappingProxyType({
    COHORT_STARTER: (60, 90),
    COHORT_BACKUP: (52, 86),
    COHORT_TAXI: (42, 88),
    COHORT_FREE_AGENT: (42, 88),
    COHORT_DRAFTABLE: (44, 96),
    COHORT_UNDRAFTED: (40, 90),
})
FAMILY_RANGES = MappingProxyType({
    FAMILY_SECONDARY: (44, 76),
    FAMILY_UNIVERSAL: (30, 70),
    FAMILY_COGNITIVE: (36, 76),
    FAMILY_PERSONALITY: (30, 78),
    FAMILY_MINIMAL: (4, 24),
})
ROLE_GAPS = MappingProxyType({
    COHORT_STARTER: 4,
    COHORT_BACKUP: 8,
    COHORT_TAXI: 12,
    COHORT_FREE_AGENT: 8,
    COHORT_DRAFTABLE: 10,
    COHORT_UNDRAFTED: 12,
})

_RAW_FAMILIES = {
    'QB': {
        FAMILY_PRIMARY: (
            'throw_power', 'throw_accuracy', 'awareness', 'processing',
        ),
        FAMILY_SECONDARY: (
            'elusiveness', 'strength', 'discipline', 'clutch', 'consistency',
        ),
        FAMILY_UNIVERSAL: (
            'speed', 'acceleration', 'agility', 'stamina', 'durability',
        ),
        FAMILY_PERSONALITY: (
            'work_ethic', 'leadership', 'temperament', 'adaptability',
        ),
        FAMILY_MINIMAL: (
            'catching', 'route_running', 'carrying', 'run_blocking',
            'pass_blocking', 'tackling', 'coverage', 'pass_rush',
            'run_defense', 'kick_power', 'kick_accuracy', 'punt_power',
            'punt_accuracy', 'return_vision', 'special_teams',
        ),
    },
    'HB': {
        FAMILY_PRIMARY: (
            'speed', 'agility', 'carrying', 'elusiveness',
        ),
        FAMILY_SECONDARY: (
            'acceleration', 'catching', 'route_running', 'stamina',
            'strength', 'return_vision',
        ),
        FAMILY_UNIVERSAL: ('durability',),
        FAMILY_COGNITIVE: ('awareness', 'discipline', 'processing'),
        FAMILY_PERSONALITY: (
            'work_ethic', 'leadership', 'temperament', 'adaptability',
            'consistency', 'clutch',
        ),
        FAMILY_MINIMAL: (
            'throw_power', 'throw_accuracy', 'run_blocking', 'pass_blocking',
            'tackling', 'coverage', 'pass_rush', 'run_defense', 'kick_power',
            'kick_accuracy', 'punt_power', 'punt_accuracy', 'special_teams',
        ),
    },
    'FB': {
        FAMILY_PRIMARY: ('strength', 'run_blocking', 'carrying'),
        FAMILY_SECONDARY: (
            'pass_blocking', 'catching', 'route_running', 'acceleration',
            'agility', 'elusiveness', 'special_teams',
        ),
        FAMILY_UNIVERSAL: ('speed', 'stamina', 'durability'),
        FAMILY_COGNITIVE: ('awareness', 'discipline', 'processing'),
        FAMILY_PERSONALITY: (
            'work_ethic', 'leadership', 'temperament', 'adaptability',
            'consistency', 'clutch',
        ),
        FAMILY_MINIMAL: (
            'throw_power', 'throw_accuracy', 'tackling', 'coverage',
            'pass_rush', 'run_defense', 'kick_power', 'kick_accuracy',
            'punt_power', 'punt_accuracy', 'return_vision',
        ),
    },
    'WR': {
        FAMILY_PRIMARY: ('speed', 'catching', 'route_running'),
        FAMILY_SECONDARY: (
            'acceleration', 'agility', 'elusiveness', 'carrying',
            'return_vision', 'run_blocking',
        ),
        FAMILY_UNIVERSAL: ('strength', 'stamina', 'durability'),
        FAMILY_COGNITIVE: ('awareness', 'discipline', 'processing'),
        FAMILY_PERSONALITY: (
            'work_ethic', 'leadership', 'temperament', 'adaptability',
            'consistency', 'clutch',
        ),
        FAMILY_MINIMAL: (
            'throw_power', 'throw_accuracy', 'pass_blocking', 'tackling',
            'coverage', 'pass_rush', 'run_defense', 'kick_power',
            'kick_accuracy', 'punt_power', 'punt_accuracy', 'special_teams',
        ),
    },
    'TE': {
        FAMILY_PRIMARY: ('catching', 'route_running', 'run_blocking'),
        FAMILY_SECONDARY: (
            'pass_blocking', 'strength', 'agility', 'acceleration',
            'carrying', 'special_teams', 'elusiveness',
        ),
        FAMILY_UNIVERSAL: ('speed', 'stamina', 'durability'),
        FAMILY_COGNITIVE: ('awareness', 'discipline', 'processing'),
        FAMILY_PERSONALITY: (
            'work_ethic', 'leadership', 'temperament', 'adaptability',
            'consistency', 'clutch',
        ),
        FAMILY_MINIMAL: (
            'throw_power', 'throw_accuracy', 'tackling', 'coverage',
            'pass_rush', 'run_defense', 'kick_power', 'kick_accuracy',
            'punt_power', 'punt_accuracy', 'return_vision',
        ),
    },
    'LT': {
        FAMILY_PRIMARY: ('pass_blocking', 'run_blocking', 'strength'),
        FAMILY_SECONDARY: (
            'agility', 'acceleration', 'awareness', 'discipline',
        ),
        FAMILY_UNIVERSAL: ('speed', 'stamina', 'durability'),
        FAMILY_COGNITIVE: ('processing',),
        FAMILY_PERSONALITY: (
            'work_ethic', 'leadership', 'temperament', 'adaptability',
            'consistency', 'clutch',
        ),
        FAMILY_MINIMAL: (
            'throw_power', 'throw_accuracy', 'catching', 'route_running',
            'carrying', 'elusiveness', 'tackling', 'coverage', 'pass_rush',
            'run_defense', 'kick_power', 'kick_accuracy', 'punt_power',
            'punt_accuracy', 'return_vision', 'special_teams',
        ),
    },
    'RT': {
        FAMILY_PRIMARY: ('pass_blocking', 'run_blocking', 'strength'),
        FAMILY_SECONDARY: (
            'agility', 'acceleration', 'awareness', 'discipline',
        ),
        FAMILY_UNIVERSAL: ('speed', 'stamina', 'durability'),
        FAMILY_COGNITIVE: ('processing',),
        FAMILY_PERSONALITY: (
            'work_ethic', 'leadership', 'temperament', 'adaptability',
            'consistency', 'clutch',
        ),
        FAMILY_MINIMAL: (
            'throw_power', 'throw_accuracy', 'catching', 'route_running',
            'carrying', 'elusiveness', 'tackling', 'coverage', 'pass_rush',
            'run_defense', 'kick_power', 'kick_accuracy', 'punt_power',
            'punt_accuracy', 'return_vision', 'special_teams',
        ),
    },
    'LG': {
        FAMILY_PRIMARY: (
            'run_blocking', 'pass_blocking', 'strength', 'agility',
        ),
        FAMILY_SECONDARY: ('acceleration', 'awareness', 'discipline'),
        FAMILY_UNIVERSAL: ('speed', 'stamina', 'durability'),
        FAMILY_COGNITIVE: ('processing',),
        FAMILY_PERSONALITY: (
            'work_ethic', 'leadership', 'temperament', 'adaptability',
            'consistency', 'clutch',
        ),
        FAMILY_MINIMAL: (
            'throw_power', 'throw_accuracy', 'catching', 'route_running',
            'carrying', 'elusiveness', 'tackling', 'coverage', 'pass_rush',
            'run_defense', 'kick_power', 'kick_accuracy', 'punt_power',
            'punt_accuracy', 'return_vision', 'special_teams',
        ),
    },
    'RG': {
        FAMILY_PRIMARY: (
            'run_blocking', 'pass_blocking', 'strength', 'agility',
        ),
        FAMILY_SECONDARY: ('acceleration', 'awareness', 'discipline'),
        FAMILY_UNIVERSAL: ('speed', 'stamina', 'durability'),
        FAMILY_COGNITIVE: ('processing',),
        FAMILY_PERSONALITY: (
            'work_ethic', 'leadership', 'temperament', 'adaptability',
            'consistency', 'clutch',
        ),
        FAMILY_MINIMAL: (
            'throw_power', 'throw_accuracy', 'catching', 'route_running',
            'carrying', 'elusiveness', 'tackling', 'coverage', 'pass_rush',
            'run_defense', 'kick_power', 'kick_accuracy', 'punt_power',
            'punt_accuracy', 'return_vision', 'special_teams',
        ),
    },
    'C': {
        FAMILY_PRIMARY: (
            'run_blocking', 'pass_blocking', 'strength', 'awareness',
        ),
        FAMILY_SECONDARY: (
            'agility', 'acceleration', 'discipline', 'processing',
            'special_teams',
        ),
        FAMILY_UNIVERSAL: ('speed', 'stamina', 'durability'),
        FAMILY_PERSONALITY: (
            'work_ethic', 'leadership', 'temperament', 'adaptability',
            'consistency', 'clutch',
        ),
        FAMILY_MINIMAL: (
            'throw_power', 'throw_accuracy', 'catching', 'route_running',
            'carrying', 'elusiveness', 'tackling', 'coverage', 'pass_rush',
            'run_defense', 'kick_power', 'kick_accuracy', 'punt_power',
            'punt_accuracy', 'return_vision',
        ),
    },
    'DE': {
        FAMILY_PRIMARY: ('pass_rush', 'speed', 'run_defense'),
        FAMILY_SECONDARY: (
            'strength', 'acceleration', 'agility', 'tackling', 'special_teams',
        ),
        FAMILY_UNIVERSAL: ('stamina', 'durability'),
        FAMILY_COGNITIVE: ('awareness', 'discipline', 'processing'),
        FAMILY_PERSONALITY: (
            'work_ethic', 'leadership', 'temperament', 'adaptability',
            'consistency', 'clutch',
        ),
        FAMILY_MINIMAL: (
            'throw_power', 'throw_accuracy', 'catching', 'route_running',
            'carrying', 'elusiveness', 'run_blocking', 'pass_blocking',
            'coverage', 'kick_power', 'kick_accuracy', 'punt_power',
            'punt_accuracy', 'return_vision',
        ),
    },
    'DT': {
        FAMILY_PRIMARY: ('strength', 'run_defense'),
        FAMILY_SECONDARY: ('pass_rush', 'tackling', 'agility'),
        FAMILY_UNIVERSAL: (
            'speed', 'acceleration', 'stamina', 'durability',
        ),
        FAMILY_COGNITIVE: ('awareness', 'discipline', 'processing'),
        FAMILY_PERSONALITY: (
            'work_ethic', 'leadership', 'temperament', 'adaptability',
            'consistency', 'clutch',
        ),
        FAMILY_MINIMAL: (
            'throw_power', 'throw_accuracy', 'catching', 'route_running',
            'carrying', 'elusiveness', 'run_blocking', 'pass_blocking',
            'coverage', 'kick_power', 'kick_accuracy', 'punt_power',
            'punt_accuracy', 'return_vision', 'special_teams',
        ),
    },
    'ILB': {
        FAMILY_PRIMARY: ('tackling', 'run_defense', 'awareness'),
        FAMILY_SECONDARY: (
            'coverage', 'speed', 'processing', 'discipline', 'special_teams',
            'pass_rush',
        ),
        FAMILY_UNIVERSAL: (
            'strength', 'acceleration', 'agility', 'stamina', 'durability',
        ),
        FAMILY_PERSONALITY: (
            'work_ethic', 'leadership', 'temperament', 'adaptability',
            'consistency', 'clutch',
        ),
        FAMILY_MINIMAL: (
            'throw_power', 'throw_accuracy', 'catching', 'route_running',
            'carrying', 'elusiveness', 'run_blocking', 'pass_blocking',
            'kick_power', 'kick_accuracy', 'punt_power', 'punt_accuracy',
            'return_vision',
        ),
    },
    'OLB': {
        FAMILY_PRIMARY: ('pass_rush', 'speed', 'tackling'),
        FAMILY_SECONDARY: (
            'run_defense', 'agility', 'acceleration', 'special_teams',
            'awareness', 'coverage',
        ),
        FAMILY_UNIVERSAL: ('strength', 'stamina', 'durability'),
        FAMILY_COGNITIVE: ('discipline', 'processing'),
        FAMILY_PERSONALITY: (
            'work_ethic', 'leadership', 'temperament', 'adaptability',
            'consistency', 'clutch',
        ),
        FAMILY_MINIMAL: (
            'throw_power', 'throw_accuracy', 'catching', 'route_running',
            'carrying', 'elusiveness', 'run_blocking', 'pass_blocking',
            'kick_power', 'kick_accuracy', 'punt_power', 'punt_accuracy',
            'return_vision',
        ),
    },
    'CB': {
        FAMILY_PRIMARY: ('coverage', 'speed', 'agility'),
        FAMILY_SECONDARY: (
            'acceleration', 'tackling', 'awareness', 'processing',
            'return_vision', 'special_teams', 'catching',
        ),
        FAMILY_UNIVERSAL: ('stamina', 'durability', 'strength'),
        FAMILY_COGNITIVE: ('discipline',),
        FAMILY_PERSONALITY: (
            'work_ethic', 'leadership', 'temperament', 'adaptability',
            'consistency', 'clutch',
        ),
        FAMILY_MINIMAL: (
            'throw_power', 'throw_accuracy', 'route_running', 'carrying',
            'run_blocking', 'pass_blocking', 'pass_rush', 'run_defense',
            'kick_power', 'kick_accuracy', 'punt_power', 'punt_accuracy',
            'elusiveness',
        ),
    },
    'FS': {
        FAMILY_PRIMARY: ('coverage', 'speed', 'awareness'),
        FAMILY_SECONDARY: (
            'tackling', 'agility', 'processing', 'return_vision', 'catching',
            'run_defense', 'special_teams',
        ),
        FAMILY_UNIVERSAL: (
            'acceleration', 'strength', 'stamina', 'durability',
        ),
        FAMILY_COGNITIVE: ('discipline',),
        FAMILY_PERSONALITY: (
            'work_ethic', 'leadership', 'temperament', 'adaptability',
            'consistency', 'clutch',
        ),
        FAMILY_MINIMAL: (
            'throw_power', 'throw_accuracy', 'route_running', 'carrying',
            'elusiveness', 'run_blocking', 'pass_blocking', 'pass_rush',
            'kick_power', 'kick_accuracy', 'punt_power', 'punt_accuracy',
        ),
    },
    'SS': {
        FAMILY_PRIMARY: ('tackling', 'run_defense', 'awareness'),
        FAMILY_SECONDARY: (
            'coverage', 'speed', 'strength', 'processing', 'catching',
            'special_teams', 'return_vision',
        ),
        FAMILY_UNIVERSAL: (
            'acceleration', 'agility', 'stamina', 'durability',
        ),
        FAMILY_COGNITIVE: ('discipline',),
        FAMILY_PERSONALITY: (
            'work_ethic', 'leadership', 'temperament', 'adaptability',
            'consistency', 'clutch',
        ),
        FAMILY_MINIMAL: (
            'throw_power', 'throw_accuracy', 'route_running', 'carrying',
            'elusiveness', 'run_blocking', 'pass_blocking', 'pass_rush',
            'kick_power', 'kick_accuracy', 'punt_power', 'punt_accuracy',
        ),
    },
    'K': {
        FAMILY_PRIMARY: ('kick_power', 'kick_accuracy'),
        FAMILY_SECONDARY: ('clutch', 'consistency'),
        FAMILY_UNIVERSAL: ('stamina', 'durability'),
        FAMILY_COGNITIVE: ('awareness', 'discipline', 'processing'),
        FAMILY_PERSONALITY: (
            'work_ethic', 'leadership', 'temperament', 'adaptability',
        ),
        FAMILY_MINIMAL: (
            'speed', 'acceleration', 'agility', 'strength', 'throw_power',
            'throw_accuracy', 'catching', 'route_running', 'carrying',
            'elusiveness', 'run_blocking', 'pass_blocking', 'tackling',
            'coverage', 'pass_rush', 'run_defense', 'punt_power',
            'punt_accuracy', 'return_vision', 'special_teams',
        ),
    },
    'P': {
        FAMILY_PRIMARY: ('punt_power', 'punt_accuracy'),
        FAMILY_SECONDARY: ('consistency', 'awareness'),
        FAMILY_UNIVERSAL: ('stamina', 'durability'),
        FAMILY_COGNITIVE: ('discipline', 'processing'),
        FAMILY_PERSONALITY: (
            'clutch', 'work_ethic', 'leadership', 'temperament',
            'adaptability',
        ),
        FAMILY_MINIMAL: (
            'speed', 'acceleration', 'agility', 'strength', 'throw_power',
            'throw_accuracy', 'catching', 'route_running', 'carrying',
            'elusiveness', 'run_blocking', 'pass_blocking', 'tackling',
            'coverage', 'pass_rush', 'run_defense', 'kick_power',
            'kick_accuracy', 'return_vision', 'special_teams',
        ),
    },
}


class RatingBuildError(ValueError):
    """The approved map or a generated pair cannot be used."""


class ComponentRating(NamedTuple):
    component: str
    initial: int
    potential: int


class PlayerRating(NamedTuple):
    universe_key: str
    cohort: str
    position: str
    age: int
    peak_age: int
    components: tuple

    def canonical(self):
        """Stable text for a byte-for-byte generation check."""
        pairs = ','.join(
            f'{row.component}:{row.initial}:{row.potential}'
            for row in self.components
        )
        return (
            f'{self.universe_key}|{self.cohort}|{self.position}|'
            f'{self.age}|{self.peak_age}|{pairs}'
        )


def compile_position_families(raw):
    """Freeze a position map. Reject a missing, duplicate, or illegal cell."""
    if set(raw) != set(POSITIONS) or len(raw) != len(POSITIONS):
        raise RatingBuildError('The position map must list the 19 positions.')
    compiled = {}
    for position in POSITIONS:
        groups = raw[position]
        unknown = set(groups) - set(FAMILIES)
        if unknown:
            raise RatingBuildError(
                f'{position} uses an unknown family: {sorted(unknown)}.'
            )
        assignment = {}
        for family, components in groups.items():
            for component in components:
                if component not in RATING_COMPONENTS:
                    raise RatingBuildError(
                        f'{position} lists an unknown component {component}.'
                    )
                if component in assignment:
                    raise RatingBuildError(
                        f'{position} assigns {component} more than once.'
                    )
                if family == FAMILY_UNIVERSAL and (
                    component not in ATHLETIC_COMPONENTS
                ):
                    raise RatingBuildError(
                        f'{position} puts {component} in universal.'
                    )
                if family == FAMILY_COGNITIVE and (
                    component not in COGNITIVE_COMPONENTS
                ):
                    raise RatingBuildError(
                        f'{position} puts {component} in cognitive.'
                    )
                if family == FAMILY_PERSONALITY and (
                    component not in PERSONALITY_COMPONENTS
                ):
                    raise RatingBuildError(
                        f'{position} puts {component} in personality.'
                    )
                assignment[component] = family
        missing = [
            component for component in RATING_COMPONENTS
            if component not in assignment
        ]
        if missing:
            raise RatingBuildError(
                f'{position} is missing {missing}.'
            )
        ordered = {
            component: assignment[component]
            for component in RATING_COMPONENTS
        }
        compiled[position] = MappingProxyType(ordered)
    return MappingProxyType(compiled)


def _require_ranges():
    if tuple(PRIMARY_RANGES) != COHORTS:
        raise RatingBuildError('A primary cohort range is missing.')
    shared = set(FAMILIES) - {FAMILY_PRIMARY}
    if set(FAMILY_RANGES) != shared:
        raise RatingBuildError('A shared family range is missing.')
    if tuple(ROLE_GAPS) != COHORTS:
        raise RatingBuildError('A primary role gap is missing.')
    for low, high in (*PRIMARY_RANGES.values(), *FAMILY_RANGES.values()):
        if (
            not isinstance(low, int)
            or not isinstance(high, int)
            or isinstance(low, bool)
            or isinstance(high, bool)
            or low < MINIMUM_RATING
            or high > MAXIMUM_POTENTIAL
            or low > high
        ):
            raise RatingBuildError('A family range is outside 1–96.')
    for gap in ROLE_GAPS.values():
        if not isinstance(gap, int) or isinstance(gap, bool) or gap < 0:
            raise RatingBuildError('A role gap is not defined.')


POSITION_FAMILIES = compile_position_families(_RAW_FAMILIES)
_require_ranges()


def family_bounds(family, cohort):
    """Inclusive potential for one family. Primary also uses the cohort."""
    if family == FAMILY_PRIMARY:
        try:
            return PRIMARY_RANGES[cohort]
        except KeyError as exc:
            raise RatingBuildError(
                f'Unknown cohort {cohort!r}.'
            ) from exc
    try:
        return FAMILY_RANGES[family]
    except KeyError as exc:
        raise RatingBuildError(
            f'Unknown family {family!r}.'
        ) from exc


def gap_cap(family, cohort, age, peak_age):
    """Approved cap before it is clamped by potential - 1."""
    before = peak_age - age if peak_age > age else 0
    after = age - peak_age if age > peak_age else 0
    if family == FAMILY_MINIMAL:
        return 3
    if family == FAMILY_PRIMARY:
        try:
            role_gap = ROLE_GAPS[cohort]
        except KeyError as exc:
            raise RatingBuildError(
                f'Unknown cohort {cohort!r}.'
            ) from exc
        return role_gap + (2 * before) + after
    if family == FAMILY_SECONDARY:
        return (2 * before) + after
    if family == FAMILY_UNIVERSAL:
        return 4 + before + after
    if family == FAMILY_COGNITIVE:
        return before
    if family == FAMILY_PERSONALITY:
        return 24 - age if age < 24 else 0
    raise RatingBuildError(f'Unknown family {family!r}.')


def approved_peak_age(seed, universe_key):
    """Peak age 24–32 from the identity coordinate. Rates are not read."""
    return 24 + (hmac_int(seed, f'{universe_key}:peak_age') % 9)


def _require_pair(initial, potential):
    if (
        not isinstance(initial, int)
        or not isinstance(potential, int)
        or isinstance(initial, bool)
        or isinstance(potential, bool)
        or initial < MINIMUM_RATING
        or potential > MAXIMUM_POTENTIAL
        or initial > potential
    ):
        raise RatingBuildError(
            f'Illegal rating {initial}/{potential}.'
        )


def rate_player(seed, universe_key, position, cohort, age, peak_age):
    """One identity. Potential is drawn, then initial is potential minus a gap."""
    try:
        assignment = POSITION_FAMILIES[position]
    except KeyError as exc:
        raise RatingBuildError(
            f'Unknown position {position!r}.'
        ) from exc
    if cohort not in COHORTS:
        raise RatingBuildError(f'Unknown cohort {cohort!r}.')
    if (
        isinstance(age, bool)
        or isinstance(peak_age, bool)
        or not isinstance(age, int)
        or not isinstance(peak_age, int)
    ):
        raise RatingBuildError('Age and peak age must be integers.')
    components = []
    for component in RATING_COMPONENTS:
        family = assignment[component]
        low, high = family_bounds(family, cohort)
        potential = low + (
            hmac_int(seed, f'{universe_key}:potential_{component}')
            % (high - low + 1)
        )
        cap = gap_cap(family, cohort, age, peak_age)
        gap_max = potential - 1 if potential - 1 < cap else cap
        # A zero cap still reads the initial coordinate. The remainder is 0.
        gap = hmac_int(
            seed, f'{universe_key}:initial_{component}',
        ) % (gap_max + 1)
        initial = potential - gap
        _require_pair(initial, potential)
        components.append(ComponentRating(component, initial, potential))
    return PlayerRating(
        universe_key=universe_key,
        cohort=cohort,
        position=position,
        age=age,
        peak_age=peak_age,
        components=tuple(components),
    )


def _rate_known_player(seed, player, cohort):
    return rate_player(
        seed,
        player.universe_key,
        player.position,
        cohort,
        player.age,
        approved_peak_age(seed, player.universe_key),
    )


def build_playable_ratings(seed, release_slug):
    """Ratings for the opening cohort and all 60 prospect classes.

    The result order is the identity-builder order. The same seed and slug
    reproduce the same records.
    """
    opening = build_opening_cohort(seed, release_slug)
    prospects = build_prospects(seed, release_slug)
    ratings = [
        _rate_known_player(seed, player, player.slot_kind)
        for player in opening
    ]
    ratings.extend(
        _rate_known_player(seed, player, player.draft_pool)
        for player in prospects
    )
    if len(ratings) != PLAYABLE_RATING_COUNT:
        raise RatingBuildError(
            f'Expected {PLAYABLE_RATING_COUNT} ratings, got {len(ratings)}.'
        )
    keys = [row.universe_key for row in ratings]
    if len(keys) != len(set(keys)):
        raise RatingBuildError('Rating keys must be unique.')
    return tuple(ratings)


def canonical_ratings(ratings):
    """Newline-joined canonical records, in generation order."""
    return '\n'.join(row.canonical() for row in ratings)
