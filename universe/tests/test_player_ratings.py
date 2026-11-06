"""Locks for the approved pure rating builder.

No database. The builder does not create rows or open a connection.
"""

import socket
from collections import Counter
from pathlib import Path

from django.test import SimpleTestCase

from universe.generation.player_identities import (
    build_player_universe,
    hmac_int,
)
from universe.generation.player_ratings import (
    ATHLETIC_COMPONENTS,
    COGNITIVE_COMPONENTS,
    COMPONENT_PAIR_COUNT,
    COHORTS,
    FAMILIES,
    FAMILY_COGNITIVE,
    FAMILY_MINIMAL,
    FAMILY_PERSONALITY,
    FAMILY_PRIMARY,
    FAMILY_RANGES,
    FAMILY_SECONDARY,
    FAMILY_UNIVERSAL,
    MAXIMUM_POTENTIAL,
    MINIMUM_RATING,
    PERSONALITY_COMPONENTS,
    PLAYABLE_RATING_COUNT,
    POSITION_FAMILIES,
    PRIMARY_RANGES,
    RATING_COMPONENTS,
    ROLE_GAPS,
    RatingBuildError,
    _RAW_FAMILIES,
    build_playable_ratings,
    canonical_ratings,
    compile_position_families,
    family_bounds,
    gap_cap,
    rate_player,
)
from universe.people import PLAYER_COMPONENTS

SEED = 1970
OTHER_SEED = 1971
RELEASE_SLUG = 'fg-1970-1'
OTHER_SLUG = 'fg-1970-2'

_ORIGINAL_CONNECT = socket.socket.connect


def _blocked_connect(self, address):
    raise AssertionError(f'Outbound connections are blocked: {address}')


def setUpModule():
    socket.socket.connect = _blocked_connect


def tearDownModule():
    socket.socket.connect = _ORIGINAL_CONNECT


def _percentile(values, percent):
    ordered = sorted(values)
    index = (len(ordered) - 1) * percent / 100
    low = int(index)
    high = min(low + 1, len(ordered) - 1)
    fraction = index - low
    return ordered[low] * (1 - fraction) + ordered[high] * fraction


def _pairs(record):
    return tuple(
        (row.component, row.initial, row.potential)
        for row in record.components
    )


# Approved seed-1970, slug-fg-1970-1 examples. Initial, then potential.
EXAMPLES = {
    'Micah Lamont': (
        ('speed', 68, 70),
        ('acceleration', 56, 63),
        ('agility', 46, 57),
        ('strength', 64, 70),
        ('stamina', 60, 67),
        ('durability', 64, 69),
        ('awareness', 67, 78),
        ('discipline', 65, 73),
        ('processing', 51, 67),
        ('throw_power', 49, 60),
        ('throw_accuracy', 72, 73),
        ('catching', 3, 6),
        ('route_running', 11, 12),
        ('carrying', 8, 10),
        ('elusiveness', 65, 72),
        ('run_blocking', 9, 9),
        ('pass_blocking', 5, 8),
        ('tackling', 16, 16),
        ('coverage', 19, 19),
        ('pass_rush', 14, 15),
        ('run_defense', 2, 5),
        ('kick_power', 15, 17),
        ('kick_accuracy', 12, 13),
        ('punt_power', 17, 18),
        ('punt_accuracy', 19, 20),
        ('return_vision', 6, 6),
        ('special_teams', 16, 16),
        ('work_ethic', 39, 39),
        ('leadership', 57, 57),
        ('temperament', 74, 74),
        ('clutch', 65, 74),
        ('consistency', 59, 68),
        ('adaptability', 38, 38),
    ),
    'Homer Stanton': (
        ('speed', 56, 56),
        ('acceleration', 59, 59),
        ('agility', 66, 66),
        ('strength', 61, 67),
        ('stamina', 32, 36),
        ('durability', 39, 41),
        ('awareness', 64, 64),
        ('discipline', 47, 47),
        ('processing', 58, 58),
        ('throw_power', 22, 24),
        ('throw_accuracy', 7, 8),
        ('catching', 13, 14),
        ('route_running', 12, 14),
        ('carrying', 3, 4),
        ('elusiveness', 20, 21),
        ('run_blocking', 62, 64),
        ('pass_blocking', 77, 84),
        ('tackling', 5, 7),
        ('coverage', 17, 19),
        ('pass_rush', 6, 9),
        ('run_defense', 20, 23),
        ('kick_power', 15, 16),
        ('kick_accuracy', 7, 9),
        ('punt_power', 14, 15),
        ('punt_accuracy', 12, 12),
        ('return_vision', 21, 21),
        ('special_teams', 6, 7),
        ('work_ethic', 64, 64),
        ('leadership', 60, 60),
        ('temperament', 59, 59),
        ('clutch', 69, 69),
        ('consistency', 76, 76),
        ('adaptability', 73, 73),
    ),
    'Seth Colfax': (
        ('speed', 70, 73),
        ('acceleration', 54, 59),
        ('agility', 71, 73),
        ('strength', 58, 64),
        ('stamina', 67, 68),
        ('durability', 65, 65),
        ('awareness', 45, 46),
        ('discipline', 47, 48),
        ('processing', 66, 72),
        ('throw_power', 16, 17),
        ('throw_accuracy', 16, 17),
        ('catching', 55, 57),
        ('route_running', 16, 19),
        ('carrying', 12, 12),
        ('elusiveness', 6, 8),
        ('run_blocking', 15, 17),
        ('pass_blocking', 1, 4),
        ('tackling', 48, 49),
        ('coverage', 38, 44),
        ('pass_rush', 3, 5),
        ('run_defense', 53, 56),
        ('kick_power', 17, 18),
        ('kick_accuracy', 21, 24),
        ('punt_power', 10, 11),
        ('punt_accuracy', 7, 9),
        ('return_vision', 42, 46),
        ('special_teams', 43, 47),
        ('work_ethic', 65, 66),
        ('leadership', 35, 36),
        ('temperament', 59, 59),
        ('clutch', 41, 41),
        ('consistency', 46, 46),
        ('adaptability', 33, 33),
    ),
    'Edward Quintana': (
        ('speed', 43, 51),
        ('acceleration', 52, 54),
        ('agility', 47, 49),
        ('strength', 47, 47),
        ('stamina', 32, 35),
        ('durability', 32, 33),
        ('awareness', 42, 43),
        ('discipline', 71, 71),
        ('processing', 67, 67),
        ('throw_power', 19, 20),
        ('throw_accuracy', 4, 4),
        ('catching', 20, 23),
        ('route_running', 19, 20),
        ('carrying', 14, 14),
        ('elusiveness', 16, 18),
        ('run_blocking', 10, 11),
        ('pass_blocking', 5, 8),
        ('tackling', 43, 44),
        ('coverage', 4, 4),
        ('pass_rush', 69, 74),
        ('run_defense', 62, 63),
        ('kick_power', 17, 18),
        ('kick_accuracy', 6, 7),
        ('punt_power', 3, 6),
        ('punt_accuracy', 8, 8),
        ('return_vision', 23, 23),
        ('special_teams', 68, 68),
        ('work_ethic', 51, 51),
        ('leadership', 44, 44),
        ('temperament', 43, 43),
        ('clutch', 43, 43),
        ('consistency', 53, 53),
        ('adaptability', 48, 48),
    ),
    'Randall Redfern': (
        ('speed', 23, 32),
        ('acceleration', 57, 63),
        ('agility', 51, 66),
        ('strength', 33, 53),
        ('stamina', 33, 37),
        ('durability', 35, 41),
        ('awareness', 29, 45),
        ('discipline', 66, 76),
        ('processing', 45, 50),
        ('throw_power', 21, 24),
        ('throw_accuracy', 17, 18),
        ('catching', 8, 8),
        ('route_running', 6, 8),
        ('carrying', 12, 15),
        ('elusiveness', 19, 20),
        ('run_blocking', 52, 71),
        ('pass_blocking', 68, 85),
        ('tackling', 9, 9),
        ('coverage', 19, 19),
        ('pass_rush', 21, 22),
        ('run_defense', 7, 7),
        ('kick_power', 3, 5),
        ('kick_accuracy', 5, 5),
        ('punt_power', 18, 19),
        ('punt_accuracy', 19, 22),
        ('return_vision', 20, 22),
        ('special_teams', 4, 4),
        ('work_ethic', 55, 55),
        ('leadership', 42, 43),
        ('temperament', 62, 62),
        ('clutch', 58, 58),
        ('consistency', 44, 44),
        ('adaptability', 34, 35),
    ),
    'Randall Hale': (
        ('speed', 38, 40),
        ('acceleration', 38, 48),
        ('agility', 77, 82),
        ('strength', 61, 77),
        ('stamina', 58, 60),
        ('durability', 42, 48),
        ('awareness', 70, 76),
        ('discipline', 76, 76),
        ('processing', 54, 57),
        ('throw_power', 10, 11),
        ('throw_accuracy', 4, 7),
        ('catching', 6, 6),
        ('route_running', 24, 24),
        ('carrying', 8, 9),
        ('elusiveness', 21, 24),
        ('run_blocking', 67, 87),
        ('pass_blocking', 61, 67),
        ('tackling', 23, 23),
        ('coverage', 22, 24),
        ('pass_rush', 20, 23),
        ('run_defense', 14, 15),
        ('kick_power', 2, 4),
        ('kick_accuracy', 24, 24),
        ('punt_power', 7, 8),
        ('punt_accuracy', 10, 10),
        ('return_vision', 10, 10),
        ('special_teams', 11, 12),
        ('work_ethic', 74, 76),
        ('leadership', 39, 42),
        ('temperament', 29, 32),
        ('clutch', 77, 77),
        ('consistency', 52, 53),
        ('adaptability', 29, 31),
    ),
    'Gareth Samson': (
        ('speed', 15, 16),
        ('acceleration', 6, 8),
        ('agility', 10, 13),
        ('strength', 7, 8),
        ('stamina', 46, 50),
        ('durability', 29, 40),
        ('awareness', 63, 66),
        ('discipline', 71, 76),
        ('processing', 42, 43),
        ('throw_power', 4, 4),
        ('throw_accuracy', 17, 18),
        ('catching', 20, 21),
        ('route_running', 19, 22),
        ('carrying', 14, 14),
        ('elusiveness', 10, 13),
        ('run_blocking', 17, 18),
        ('pass_blocking', 11, 13),
        ('tackling', 3, 6),
        ('coverage', 14, 17),
        ('pass_rush', 19, 21),
        ('run_defense', 9, 9),
        ('kick_power', 64, 84),
        ('kick_accuracy', 67, 84),
        ('punt_power', 14, 16),
        ('punt_accuracy', 6, 7),
        ('return_vision', 19, 22),
        ('special_teams', 3, 6),
        ('work_ethic', 61, 61),
        ('leadership', 64, 64),
        ('temperament', 67, 68),
        ('clutch', 61, 65),
        ('consistency', 61, 64),
        ('adaptability', 36, 36),
    ),
}

HEADLINES = {
    'starter': (62.67, 70.71, 77.45, 70.51, 68.5, 75.0, 81.5, 74.99),
    'backup': (53.42, 62.33, 71.0, 62.18, 61.5, 68.58, 77.0, 68.85),
    'taxi': (44.0, 54.0, 65.38, 54.3, 56.57, 66.0, 75.0, 65.72),
    'free_agent': (48.48, 57.42, 67.78, 58.16, 55.23, 63.5, 73.55, 64.38),
    'draftable': (46.0, 59.0, 72.0, 58.94, 58.33, 70.0, 81.67, 69.93),
    'undrafted': (40.0, 53.0, 65.5, 52.79, 53.67, 64.75, 76.0, 64.86),
}

DENSITY = {
    'draftable': (4830, 2287, 786, 145),
    'undrafted': (466, 140, 32, 0),
}

ENDPOINTS = {
    'primary': (6, 96, 40, 96),
    'secondary': (22, 76, 44, 76),
    'universal': (15, 70, 30, 70),
    'cognitive': (25, 76, 36, 76),
    'personality': (27, 78, 30, 78),
    'minimal': (1, 24, 4, 24),
}


class RatingMapTests(SimpleTestCase):
    def test_component_order_matches_the_stored_profile_fields(self):
        self.assertEqual(RATING_COMPONENTS, PLAYER_COMPONENTS)
        self.assertEqual(len(RATING_COMPONENTS), 33)

    def test_every_position_assigns_every_component_once(self):
        self.assertEqual(len(POSITION_FAMILIES), 19)
        assigned = 0
        for position, families in POSITION_FAMILIES.items():
            self.assertEqual(tuple(families), RATING_COMPONENTS)
            self.assertEqual(len(set(families)), 33)
            assigned += len(families)
            for family in families.values():
                self.assertIn(family, FAMILIES)
        self.assertEqual(assigned, 627)

    def test_restricted_families_hold_only_their_components(self):
        for position, families in POSITION_FAMILIES.items():
            for component, family in families.items():
                if family == FAMILY_UNIVERSAL:
                    self.assertIn(component, ATHLETIC_COMPONENTS)
                if family == FAMILY_COGNITIVE:
                    self.assertIn(component, COGNITIVE_COMPONENTS)
                if family == FAMILY_PERSONALITY:
                    self.assertIn(component, PERSONALITY_COMPONENTS)

    def test_position_distinctions_are_locked(self):
        family = POSITION_FAMILIES
        self.assertEqual(family['DE']['speed'], FAMILY_PRIMARY)
        self.assertEqual(family['DT']['speed'], FAMILY_UNIVERSAL)
        self.assertEqual(family['DE']['pass_rush'], FAMILY_PRIMARY)
        self.assertEqual(family['DT']['pass_rush'], FAMILY_SECONDARY)
        self.assertEqual(family['DT']['strength'], FAMILY_PRIMARY)
        self.assertEqual(family['DE']['strength'], FAMILY_SECONDARY)
        self.assertEqual(family['DE']['run_defense'], FAMILY_PRIMARY)
        self.assertEqual(family['DT']['run_defense'], FAMILY_PRIMARY)

        self.assertEqual(family['ILB']['pass_rush'], FAMILY_SECONDARY)
        self.assertEqual(family['OLB']['pass_rush'], FAMILY_PRIMARY)
        self.assertEqual(family['ILB']['speed'], FAMILY_SECONDARY)
        self.assertEqual(family['OLB']['speed'], FAMILY_PRIMARY)
        self.assertEqual(family['ILB']['run_defense'], FAMILY_PRIMARY)
        self.assertEqual(family['OLB']['run_defense'], FAMILY_SECONDARY)
        self.assertEqual(family['ILB']['coverage'], FAMILY_SECONDARY)
        self.assertEqual(family['OLB']['coverage'], FAMILY_SECONDARY)

        self.assertEqual(family['FS']['coverage'], FAMILY_PRIMARY)
        self.assertEqual(family['SS']['coverage'], FAMILY_SECONDARY)
        self.assertEqual(family['FS']['tackling'], FAMILY_SECONDARY)
        self.assertEqual(family['SS']['tackling'], FAMILY_PRIMARY)
        self.assertEqual(family['FS']['run_defense'], FAMILY_SECONDARY)
        self.assertEqual(family['SS']['run_defense'], FAMILY_PRIMARY)

        self.assertEqual(family['LT']['agility'], FAMILY_SECONDARY)
        self.assertEqual(family['RT']['agility'], FAMILY_SECONDARY)
        self.assertEqual(family['LG']['agility'], FAMILY_PRIMARY)
        self.assertEqual(family['RG']['agility'], FAMILY_PRIMARY)
        self.assertEqual(family['C']['awareness'], FAMILY_PRIMARY)
        self.assertEqual(family['C']['special_teams'], FAMILY_SECONDARY)
        self.assertEqual(family['LT']['special_teams'], FAMILY_MINIMAL)
        self.assertEqual(family['LG']['special_teams'], FAMILY_MINIMAL)

        self.assertEqual(family['K']['kick_power'], FAMILY_PRIMARY)
        self.assertEqual(family['K']['punt_power'], FAMILY_MINIMAL)
        self.assertEqual(family['P']['punt_power'], FAMILY_PRIMARY)
        self.assertEqual(family['P']['kick_power'], FAMILY_MINIMAL)
        self.assertEqual(family['K']['clutch'], FAMILY_SECONDARY)
        self.assertEqual(family['P']['clutch'], FAMILY_PERSONALITY)

        for position in ('CB', 'FS', 'SS'):
            self.assertEqual(family[position]['catching'], FAMILY_SECONDARY)
        self.assertEqual(family['TE']['elusiveness'], FAMILY_SECONDARY)
        self.assertEqual(family['WR']['run_blocking'], FAMILY_SECONDARY)
        self.assertEqual(family['WR']['pass_blocking'], FAMILY_MINIMAL)

    def test_ranges_and_gap_formulas_are_locked(self):
        self.assertEqual(PRIMARY_RANGES['starter'], (60, 90))
        self.assertEqual(PRIMARY_RANGES['backup'], (52, 86))
        self.assertEqual(PRIMARY_RANGES['taxi'], (42, 88))
        self.assertEqual(PRIMARY_RANGES['free_agent'], (42, 88))
        self.assertEqual(PRIMARY_RANGES['draftable'], (44, 96))
        self.assertEqual(PRIMARY_RANGES['undrafted'], (40, 90))
        self.assertEqual(FAMILY_RANGES['secondary'], (44, 76))
        self.assertEqual(FAMILY_RANGES['universal'], (30, 70))
        self.assertEqual(FAMILY_RANGES['cognitive'], (36, 76))
        self.assertEqual(FAMILY_RANGES['personality'], (30, 78))
        self.assertEqual(FAMILY_RANGES['minimal'], (4, 24))
        self.assertEqual(MAXIMUM_POTENTIAL, 96)
        self.assertEqual(
            ROLE_GAPS,
            {
                'starter': 4,
                'backup': 8,
                'taxi': 12,
                'free_agent': 8,
                'draftable': 10,
                'undrafted': 12,
            },
        )
        self.assertEqual(gap_cap('primary', 'starter', 24, 32), 20)
        self.assertEqual(gap_cap('primary', 'backup', 22, 32), 28)
        self.assertEqual(gap_cap('primary', 'taxi', 21, 32), 34)
        self.assertEqual(gap_cap('primary', 'free_agent', 34, 24), 18)
        self.assertEqual(gap_cap('primary', 'draftable', 21, 32), 32)
        self.assertEqual(gap_cap('primary', 'undrafted', 21, 32), 34)
        self.assertEqual(gap_cap('secondary', 'starter', 21, 32), 22)
        self.assertEqual(gap_cap('universal', 'draftable', 21, 32), 15)
        self.assertEqual(gap_cap('cognitive', 'free_agent', 34, 24), 0)
        self.assertEqual(gap_cap('cognitive', 'draftable', 21, 32), 11)
        self.assertEqual(gap_cap('personality', 'starter', 30, 24), 0)
        self.assertEqual(gap_cap('personality', 'draftable', 21, 32), 3)
        self.assertEqual(gap_cap('minimal', 'starter', 21, 32), 3)
        self.assertEqual(family_bounds('primary', 'free_agent'), (42, 88))
        self.assertEqual(
            family_bounds('secondary', 'starter'),
            family_bounds('secondary', 'undrafted'),
        )

    def test_bad_maps_and_inputs_fail_closed(self):
        raw = {
            position: {
                family: tuple(components)
                for family, components in groups.items()
            }
            for position, groups in _RAW_FAMILIES.items()
        }
        quarterback = dict(raw['QB'])
        quarterback['minimal'] = quarterback['minimal'] + ('speed',)
        raw['QB'] = quarterback
        with self.assertRaises(RatingBuildError):
            compile_position_families(raw)

        raw['QB'] = {
            family: tuple(
                component for component in components if component != 'speed'
            )
            for family, components in _RAW_FAMILIES['QB'].items()
        }
        with self.assertRaises(RatingBuildError):
            compile_position_families(raw)

        with self.assertRaises(RatingBuildError):
            rate_player(SEED, 'k' * 64, 'XX', 'starter', 25, 28)
        with self.assertRaises(RatingBuildError):
            rate_player(SEED, 'k' * 64, 'QB', 'archive', 25, 28)
        with self.assertRaises(RatingBuildError):
            gap_cap('mystery', 'starter', 25, 28)
        with self.assertRaises(RatingBuildError):
            family_bounds('primary', 'archive')

    def test_module_has_no_database_or_rate_inputs(self):
        source = Path(
            '/workspace/universe/generation/player_ratings.py',
        ).read_text(encoding='utf-8')
        lowered = source.lower()
        self.assertNotIn('django', lowered)
        self.assertNotIn('development_rate', lowered)
        self.assertNotIn('decline_rate', lowered)
        self.assertNotIn('board_rank', lowered)
        self.assertNotIn('socket', lowered)
        self.assertNotIn('sql', lowered)


class PlayableRatingTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.universe = build_player_universe(SEED, RELEASE_SLUG)
        cls.ratings = build_playable_ratings(SEED, RELEASE_SLUG)
        cls.by_key = {row.universe_key: row for row in cls.ratings}

    def _identity_rows(self):
        rows = list(self.universe.opening)
        rows.extend(self.universe.prospects)
        return rows

    def test_population_counts_and_legal_range(self):
        identities = self._identity_rows()
        self.assertEqual(len(self.ratings), PLAYABLE_RATING_COUNT)
        self.assertEqual(len(identities), PLAYABLE_RATING_COUNT)
        self.assertEqual(
            [row.universe_key for row in self.ratings],
            [row.universe_key for row in identities],
        )
        counts = Counter(row.cohort for row in self.ratings)
        self.assertEqual(counts['starter'], 494)
        self.assertEqual(counts['backup'], 546)
        self.assertEqual(counts['taxi'], 208)
        self.assertEqual(counts['free_agent'], 130)
        self.assertEqual(counts['draftable'], 16183)
        self.assertEqual(counts['undrafted'], 3594)
        self.assertEqual(set(counts), set(COHORTS))

        years = set()
        pairs = 0
        banned = 0
        family_bounds = {
            family: [999, -1, 999, -1] for family in FAMILIES
        }
        primary_initial = {cohort: [] for cohort in COHORTS}
        primary_potential = {cohort: [] for cohort in COHORTS}
        for identity, rating in zip(identities, self.ratings):
            self.assertEqual(rating.position, identity.position)
            self.assertEqual(rating.age, identity.age)
            self.assertEqual(len(rating.components), 33)
            self.assertEqual(
                tuple(row.component for row in rating.components),
                RATING_COMPONENTS,
            )
            if hasattr(identity, 'season_year'):
                years.add(identity.season_year)
            values = []
            for row in rating.components:
                pairs += 1
                self.assertIsInstance(row.initial, int)
                self.assertIsInstance(row.potential, int)
                self.assertGreaterEqual(row.initial, MINIMUM_RATING)
                self.assertLessEqual(row.potential, MAXIMUM_POTENTIAL)
                self.assertLessEqual(row.initial, row.potential)
                if row.initial in (0, 97, 98, 99, 100) or row.potential in (
                    0, 97, 98, 99, 100,
                ):
                    banned += 1
                family = POSITION_FAMILIES[rating.position][row.component]
                bounds = family_bounds[family]
                bounds[0] = min(bounds[0], row.initial)
                bounds[1] = max(bounds[1], row.initial)
                bounds[2] = min(bounds[2], row.potential)
                bounds[3] = max(bounds[3], row.potential)
                if family == FAMILY_PRIMARY:
                    values.append((row.initial, row.potential))
                    low, high = PRIMARY_RANGES[rating.cohort]
                    self.assertGreaterEqual(row.potential, low)
                    self.assertLessEqual(row.potential, high)
            initial_mean = sum(item[0] for item in values) / len(values)
            potential_mean = sum(item[1] for item in values) / len(values)
            primary_initial[rating.cohort].append(initial_mean)
            primary_potential[rating.cohort].append(potential_mean)

        self.assertEqual(pairs, COMPONENT_PAIR_COUNT)
        self.assertEqual(pairs, 698115)
        self.assertEqual(banned, 0)
        self.assertEqual(min(years), 1970)
        self.assertEqual(max(years), 2029)
        for family, expected in ENDPOINTS.items():
            self.assertEqual(tuple(family_bounds[family]), expected)
        for cohort, expected in HEADLINES.items():
            actual = (
                round(_percentile(primary_initial[cohort], 10), 2),
                round(_percentile(primary_initial[cohort], 50), 2),
                round(_percentile(primary_initial[cohort], 90), 2),
                round(
                    sum(primary_initial[cohort]) / len(primary_initial[cohort]),
                    2,
                ),
                round(_percentile(primary_potential[cohort], 10), 2),
                round(_percentile(primary_potential[cohort], 50), 2),
                round(_percentile(primary_potential[cohort], 90), 2),
                round(
                    sum(primary_potential[cohort])
                    / len(primary_potential[cohort]),
                    2,
                ),
            )
            self.assertEqual(actual, expected)
        for cohort, expected in DENSITY.items():
            potentials = primary_potential[cohort]
            actual = tuple(
                sum(1 for value in potentials if value >= threshold)
                for threshold in (75, 80, 85, 90)
            )
            self.assertEqual(actual, expected)

    def test_worked_examples_and_zero_gap_coordinate(self):
        found = {}
        for identity in self.universe.opening:
            if (
                identity.slot_kind == 'starter'
                and identity.position == 'QB'
                and identity.depth == 1
                and identity.name == 'Micah Lamont'
            ):
                found[identity.name] = identity
            elif (
                identity.slot_kind == 'backup'
                and identity.position == 'LT'
                and identity.name == 'Homer Stanton'
                and 'Homer Stanton' not in found
            ):
                found[identity.name] = identity
            elif (
                identity.slot_kind == 'taxi'
                and identity.position == 'FS'
                and identity.name == 'Seth Colfax'
                and 'Seth Colfax' not in found
            ):
                found[identity.name] = identity
            elif (
                identity.slot_kind == 'free_agent'
                and identity.position == 'DE'
                and identity.name == 'Edward Quintana'
                and 'Edward Quintana' not in found
            ):
                found[identity.name] = identity
        for identity in self.universe.prospects:
            if (
                identity.season_year == 1970
                and identity.index == 0
                and identity.position == 'LT'
            ):
                found[identity.name] = identity
            elif (
                identity.season_year == 2029
                and identity.index == 0
                and identity.position == 'RG'
            ):
                found[identity.name] = identity
            elif (
                identity.season_year == 1970
                and identity.index == 283
                and identity.position == 'K'
                and identity.draft_pool == 'undrafted'
            ):
                found[identity.name] = identity
        self.assertEqual(set(found), set(EXAMPLES))
        self.assertEqual(found['Randall Redfern'].season_year, 1970)
        self.assertEqual(found['Randall Redfern'].index, 0)
        self.assertEqual(found['Randall Hale'].season_year, 2029)
        self.assertEqual(found['Randall Hale'].index, 0)
        self.assertEqual(found['Gareth Samson'].season_year, 1970)
        self.assertEqual(found['Gareth Samson'].index, 283)
        self.assertIsNone(found['Gareth Samson'].debut_year)
        for name, expected in EXAMPLES.items():
            rating = self.by_key[found[name].universe_key]
            self.assertEqual(_pairs(rating), expected)
        micah = found['Micah Lamont']
        rating = self.by_key[micah.universe_key]
        work = rating.components[RATING_COMPONENTS.index('work_ethic')]
        self.assertEqual(work.initial, work.potential)
        self.assertEqual(
            hmac_int(SEED, f'{micah.universe_key}:initial_work_ethic') % 1,
            0,
        )
        self.assertEqual(rating.peak_age, 31)
        self.assertEqual(micah.age, 24)

    def test_same_inputs_are_byte_for_byte_deterministic(self):
        again = build_playable_ratings(SEED, RELEASE_SLUG)
        self.assertEqual(
            canonical_ratings(again),
            canonical_ratings(self.ratings),
        )

    def test_a_different_seed_changes_ratings_only(self):
        changed = 0
        for rating in self.ratings:
            other = rate_player(
                OTHER_SEED,
                rating.universe_key,
                rating.position,
                rating.cohort,
                rating.age,
                rating.peak_age,
            )
            self.assertEqual(other.universe_key, rating.universe_key)
            self.assertEqual(other.cohort, rating.cohort)
            self.assertEqual(len(other.components), 33)
            for row in other.components:
                self.assertGreaterEqual(row.initial, 1)
                self.assertLessEqual(row.potential, 96)
                self.assertLessEqual(row.initial, row.potential)
            if other.components != rating.components:
                changed += 1
        self.assertEqual(changed, PLAYABLE_RATING_COUNT)
        self.assertEqual(POSITION_FAMILIES['QB']['throw_power'], FAMILY_PRIMARY)
        self.assertEqual(PRIMARY_RANGES['draftable'], (44, 96))

    def test_a_different_slug_changes_keys_and_ratings(self):
        other = build_playable_ratings(SEED, OTHER_SLUG)
        self.assertEqual(len(other), PLAYABLE_RATING_COUNT)
        self.assertEqual(
            Counter(row.cohort for row in other),
            Counter(row.cohort for row in self.ratings),
        )
        self.assertNotEqual(
            [row.universe_key for row in other],
            [row.universe_key for row in self.ratings],
        )
        self.assertNotEqual(
            canonical_ratings(other),
            canonical_ratings(self.ratings),
        )

    def test_joe_lecrone_is_absent(self):
        for identity in self._identity_rows():
            self.assertNotEqual(
                (identity.given_name, identity.surname),
                ('Joe', 'LeCrone'),
            )
        self.assertEqual(len(self.by_key), PLAYABLE_RATING_COUNT)
