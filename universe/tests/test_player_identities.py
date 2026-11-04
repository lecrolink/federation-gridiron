"""Locks for opening identities, prospect identities, and class headers.

No database. The builders under test do not create rows.
"""

import hashlib
import hmac
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from universe.generation import names as staff_names
from universe.generation.player_counts import (
    EXPANSION_YEARS,
    POSITIONS,
    active_quota,
    draft_class,
    draft_classes,
    opening_cohort_count,
    playable_identity_count,
    prospect_totals,
    taxi_quota,
)
from universe.generation.player_identities import (
    BODY_BANDS_THROUGH_1977,
    FREE_AGENT_DEBUT_YEAR,
    FREE_AGENT_POSITIONS,
    OPENING_AGE_BANDS,
    OTHER_LEFT_HAND_PER_THOUSAND,
    PROSPECT_AGE_BAND,
    ROSTERED_DEBUT_YEAR,
    SLOT_BACKUP,
    SLOT_FREE_AGENT,
    SLOT_STARTER,
    SLOT_TAXI,
    build_player_universe,
    hmac_int,
    joe_threshold,
    player_key,
    resolve_given_name,
)
from universe.generation.player_names import (
    COLLEGES,
    GIVEN_NAMES,
    JOE_GIVEN_INDEX,
    SURNAMES,
)

SEED = 1
OTHER_SEED = 2
RELEASE_SLUG = 'fg-1970-1'

GIVEN_FINGERPRINT = (
    '36d21fab5f8cda92a84e92ca9749e45df53afd6fae16408e833f35b919bfce7b'
)
SURNAME_FINGERPRINT = (
    '95cee1e6de6f16046539d315cffee3ee7c36d0881f8d8fdb885cad981693a93b'
)
COLLEGE_FINGERPRINT = (
    'a5702a1a13027481dafc3fc8d30062bb2740807f0e56731f85a1a70ae384351e'
)

APPROVED_BODY_BANDS = {
    'QB': (71, 76, 195, 225),
    'HB': (67, 71, 175, 210),
    'FB': (70, 74, 210, 240),
    'WR': (69, 75, 165, 205),
    'TE': (74, 77, 225, 255),
    'LT': (74, 78, 255, 310),
    'LG': (74, 78, 255, 310),
    'C': (74, 78, 255, 310),
    'RG': (74, 78, 255, 310),
    'RT': (74, 78, 255, 310),
    'DE': (73, 77, 235, 275),
    'DT': (73, 77, 265, 320),
    'ILB': (71, 75, 210, 240),
    'OLB': (71, 75, 210, 240),
    'CB': (68, 72, 165, 195),
    'FS': (70, 74, 180, 210),
    'SS': (70, 74, 180, 210),
    'K': (69, 74, 165, 205),
    'P': (69, 74, 165, 205),
}


def _fingerprint(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _inline_threshold(birth_year):
    """The approved Joe table, written out in the test."""
    if isinstance(birth_year, bool) or not isinstance(birth_year, int):
        raise ValueError('Birth year must be an integer.')
    if 1920 <= birth_year <= 1935:
        raise ValueError('no approved threshold')
    if birth_year < 1920 or birth_year > 2008:
        raise ValueError('outside 1920 through 2008')
    if birth_year <= 1949:
        return 16
    if birth_year <= 1969:
        return 6
    if birth_year <= 1989:
        return 2
    return 0


def _inline_given_name(seed, stored_key, birth_year):
    """Joe's promotion rule, independent of resolve_given_name."""
    threshold = _inline_threshold(birth_year)
    index = hmac_int(seed, f'{stored_key}:given_name') % 250
    if index == 140:
        return 'Joe'
    ticket = hmac_int(seed, f'{stored_key}:given_joe') % 1000
    if ticket < threshold:
        return 'Joe'
    return GIVEN_NAMES[index]


def _find_seed(predicate):
    for seed in range(200000):
        if predicate(seed):
            return seed
    raise AssertionError('No seed matched the Joe draw.')


class IsolatedDatabaseTests(SimpleTestCase):
    def test_settings_do_not_name_the_remote_database(self):
        database = settings.DATABASES['default']
        self.assertEqual(database['ENGINE'], 'django.db.backends.sqlite3')
        self.assertEqual(database['NAME'], ':memory:')
        self.assertNotIn('pythonanywhere', str(settings.DATABASES))
        self.assertNotIn('lecrolink', str(settings.DATABASES))
        self.assertEqual(
            settings.TEST_RUNNER,
            'federationgridiron.isolated_test_runner.IsolatedTestRunner',
        )


class ApprovedListTests(SimpleTestCase):
    def test_given_names_surnames_and_colleges_match_the_approved_lists(self):
        self.assertEqual(len(GIVEN_NAMES), 250)
        self.assertEqual(len(set(GIVEN_NAMES)), 250)
        self.assertEqual(GIVEN_NAMES, tuple(sorted(GIVEN_NAMES)))
        self.assertEqual(_fingerprint('|'.join(GIVEN_NAMES)), GIVEN_FINGERPRINT)
        self.assertEqual(GIVEN_NAMES[0], 'Aaron')
        self.assertEqual(GIVEN_NAMES[-1], 'Zachary')
        self.assertEqual(JOE_GIVEN_INDEX, 140)
        self.assertEqual(GIVEN_NAMES[139], 'Jasper')
        self.assertEqual(GIVEN_NAMES[140], 'Joe')
        self.assertEqual(GIVEN_NAMES[141], 'John')
        for excluded in ('Joseph', 'Joey', 'Kermit', 'LeCrone'):
            self.assertNotIn(excluded, GIVEN_NAMES)

        self.assertEqual(len(SURNAMES), 1000)
        self.assertEqual(len(set(SURNAMES)), 1000)
        self.assertEqual(SURNAMES, tuple(sorted(SURNAMES)))
        self.assertEqual(_fingerprint('|'.join(SURNAMES)), SURNAME_FINGERPRINT)
        self.assertEqual(SURNAMES[0], 'Abbott')
        self.assertEqual(SURNAMES[-1], 'Zwicker')
        self.assertNotIn('LeCrone', SURNAMES)
        self.assertFalse(
            {name.casefold() for name in GIVEN_NAMES}
            & {name.casefold() for name in SURNAMES}
        )

        staff_given = {name.casefold() for name in staff_names.GIVEN_NAMES}
        staff_surnames = {name.casefold() for name in staff_names.SURNAMES}
        self.assertFalse({name.casefold() for name in GIVEN_NAMES} & staff_given)
        self.assertFalse({name.casefold() for name in GIVEN_NAMES} & staff_surnames)
        self.assertFalse({name.casefold() for name in SURNAMES} & staff_given)
        self.assertFalse({name.casefold() for name in SURNAMES} & staff_surnames)

        self.assertEqual(len(COLLEGES), 64)
        self.assertEqual(len({row[1] for row in COLLEGES}), 64)
        self.assertEqual(
            _fingerprint('|'.join(f'{name}|{slug}|{kind}' for name, slug, kind in COLLEGES)),
            COLLEGE_FINGERPRINT,
        )
        kinds = {}
        for name, slug, kind in COLLEGES:
            self.assertEqual(slug, name.lower().replace(' ', '-'))
            kinds[kind] = kinds.get(kind, 0) + 1
        self.assertEqual(kinds, {
            'University': 32,
            'College': 11,
            'Institute': 11,
            'School': 10,
        })
        self.assertEqual(COLLEGES[0][0], 'Alcott University')
        self.assertEqual(COLLEGES[-1][0], 'Hildreth Institute')

    def test_builders_do_not_import_django_or_emit_an_archive_count(self):
        root = Path(__file__).resolve().parents[1] / 'generation'
        for filename in ('player_identities.py', 'player_names.py', 'player_counts.py'):
            source = (root / filename).read_text(encoding='utf-8')
            self.assertNotIn('import django', source)
            self.assertNotIn('from django', source)
            self.assertNotIn('1118', source)


class PlayerKeyTests(SimpleTestCase):
    def test_stored_key_is_the_utf8_hmac_sha256_hex_digest(self):
        coordinate = 'p-fg-1970-1-opening-00000'
        for seed in (SEED, 0, -3):
            digest = hmac.new(
                str(seed).encode('utf-8'),
                coordinate.encode('utf-8'),
                hashlib.sha256,
            ).hexdigest()
            self.assertEqual(len(digest), 64)
            self.assertEqual(player_key(seed, coordinate), digest)
            self.assertEqual(
                hmac_int(seed, coordinate),
                int.from_bytes(bytes.fromhex(digest)[:8], 'big'),
            )

    def test_a_boolean_seed_is_rejected(self):
        with self.assertRaises(ValueError):
            player_key(True, 'p-fg-1970-1-opening-00000')
        with self.assertRaises(ValueError):
            build_player_universe('1', RELEASE_SLUG)
        with self.assertRaises(ValueError):
            build_player_universe(SEED, '')
        with self.assertRaises(ValueError):
            build_player_universe(SEED, 'fg 1970')


class JoePromotionTests(SimpleTestCase):
    def test_thresholds_follow_the_drawn_birth_year(self):
        for birth_year, threshold in (
            (1936, 16), (1949, 16),
            (1950, 6), (1969, 6),
            (1970, 2), (1989, 2),
            (1990, 0), (2008, 0),
        ):
            self.assertEqual(joe_threshold(birth_year), threshold)
            self.assertEqual(_inline_threshold(birth_year), threshold)
        for birth_year in (1920, 1935, 1919, 2009, True):
            with self.assertRaises(ValueError):
                joe_threshold(birth_year)
            with self.assertRaises(ValueError):
                resolve_given_name(SEED, 'stored-key', birth_year)

    def test_promotion_uses_the_ticket_unless_the_uniform_draw_is_joe(self):
        stored_key = 'stored-key'

        def index(seed):
            return hmac_int(seed, f'{stored_key}:given_name') % 250

        def ticket(seed):
            return hmac_int(seed, f'{stored_key}:given_joe') % 1000

        ticket_zero = _find_seed(lambda seed: index(seed) != 140 and ticket(seed) == 0)
        self.assertEqual(resolve_given_name(ticket_zero, stored_key, 1936), 'Joe')
        self.assertEqual(_inline_given_name(ticket_zero, stored_key, 1936), 'Joe')

        ticket_16 = _find_seed(lambda seed: index(seed) != 140 and ticket(seed) == 16)
        self.assertEqual(
            resolve_given_name(ticket_16, stored_key, 1936),
            GIVEN_NAMES[index(ticket_16)],
        )
        self.assertNotEqual(resolve_given_name(ticket_16, stored_key, 1936), 'Joe')
        self.assertEqual(
            resolve_given_name(ticket_16, stored_key, 1949),
            GIVEN_NAMES[index(ticket_16)],
        )

        uniform_joe = _find_seed(lambda seed: index(seed) == 140 and ticket(seed) >= 16)
        self.assertEqual(resolve_given_name(uniform_joe, stored_key, 1936), 'Joe')
        self.assertEqual(_inline_given_name(uniform_joe, stored_key, 1936), 'Joe')

        ticket_five = _find_seed(lambda seed: index(seed) != 140 and ticket(seed) == 5)
        ticket_six = _find_seed(lambda seed: index(seed) != 140 and ticket(seed) == 6)
        self.assertEqual(resolve_given_name(ticket_five, stored_key, 1950), 'Joe')
        self.assertEqual(
            resolve_given_name(ticket_six, stored_key, 1969),
            GIVEN_NAMES[index(ticket_six)],
        )

        ticket_one = _find_seed(lambda seed: index(seed) != 140 and ticket(seed) == 1)
        ticket_two = _find_seed(lambda seed: index(seed) != 140 and ticket(seed) == 2)
        self.assertEqual(resolve_given_name(ticket_one, stored_key, 1970), 'Joe')
        self.assertEqual(
            resolve_given_name(ticket_two, stored_key, 1989),
            GIVEN_NAMES[index(ticket_two)],
        )

        for birth_year in (1990, 2008):
            self.assertEqual(
                resolve_given_name(ticket_zero, stored_key, birth_year),
                GIVEN_NAMES[index(ticket_zero)],
            )
            self.assertEqual(
                _inline_given_name(ticket_zero, stored_key, birth_year),
                GIVEN_NAMES[index(ticket_zero)],
            )


class PlayerUniverseTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.universe = build_player_universe(SEED, RELEASE_SLUG)

    def test_the_same_seed_rebuilds_the_same_keys_and_attributes(self):
        again = build_player_universe(SEED, RELEASE_SLUG)
        self.assertEqual(again, self.universe)
        self.assertEqual(again.opening[0].universe_key, self.universe.opening[0].universe_key)
        self.assertEqual(again.prospects[0], self.universe.prospects[0])
        self.assertEqual(again.class_headers[0], self.universe.class_headers[0])

    def test_a_changed_seed_changes_keys_and_attributes(self):
        other = build_player_universe(OTHER_SEED, RELEASE_SLUG)
        original = self.universe.opening[0]
        changed = other.opening[0]
        self.assertEqual(original.coordinate, changed.coordinate)
        self.assertNotEqual(original.universe_key, changed.universe_key)
        self.assertNotEqual(original, changed)
        self.assertTrue(any((
            original.birth_year != changed.birth_year,
            original.birth_day != changed.birth_day,
            original.given_name != changed.given_name,
            original.surname != changed.surname,
            original.college_slug != changed.college_slug,
            original.handedness != changed.handedness,
            original.height_in != changed.height_in,
            original.weight_lb != changed.weight_lb,
        )))
        self.assertNotEqual(
            other.prospects[0].universe_key,
            self.universe.prospects[0].universe_key,
        )
        self.assertNotEqual(
            other.class_headers[0].universe_key,
            self.universe.class_headers[0].universe_key,
        )

    def test_opening_count_slot_order_and_body_rules(self):
        opening = self.universe.opening
        self.assertEqual(len(opening), 1378)
        self.assertEqual(opening_cohort_count(), 1378)
        self.assertEqual(BODY_BANDS_THROUGH_1977, APPROVED_BODY_BANDS)
        self.assertEqual(FREE_AGENT_POSITIONS, ('QB', 'WR', 'C', 'DE', 'CB'))
        self.assertEqual(OPENING_AGE_BANDS, {
            SLOT_STARTER: (24, 30),
            SLOT_BACKUP: (22, 32),
            SLOT_TAXI: (21, 24),
            SLOT_FREE_AGENT: (27, 34),
        })

        expected = []
        active = active_quota(1970)
        taxi = taxi_quota(1970)
        for number in range(1, 27):
            club_slug = f'founding-{number:02d}'
            for position in POSITIONS:
                for depth in range(1, active[position] + 1):
                    kind = SLOT_STARTER if depth == 1 else SLOT_BACKUP
                    expected.append((club_slug, kind, position, depth, True, ROSTERED_DEBUT_YEAR))
            for position in POSITIONS:
                for depth in range(1, taxi[position] + 1):
                    expected.append((
                        club_slug, SLOT_TAXI, position, depth, True, ROSTERED_DEBUT_YEAR,
                    ))
            for position in ('QB', 'WR', 'C', 'DE', 'CB'):
                expected.append((
                    club_slug, SLOT_FREE_AGENT, position, None, False, FREE_AGENT_DEBUT_YEAR,
                ))
        self.assertEqual(len(expected), 1378)

        colleges = {slug: name for name, slug, _kind in COLLEGES}
        per_club = {}
        for index, (player, slot) in enumerate(zip(opening, expected)):
            club_slug, kind, position, _depth, _on_roster, _debut_year = slot
            self.assertEqual(player.index, index)
            self.assertEqual(
                (
                    player.club_slug, player.slot_kind, player.position,
                    player.depth, player.on_roster, player.debut_year,
                ),
                slot,
            )
            self.assertEqual(
                player.coordinate,
                f'p-{RELEASE_SLUG}-opening-{player.index:05d}',
            )
            self.assertEqual(player.universe_key, player_key(SEED, player.coordinate))
            self.assertEqual(len(player.universe_key), 64)
            age_low, age_high = OPENING_AGE_BANDS[kind]
            age = age_low + (
                hmac_int(SEED, f'{player.universe_key}:birth_year')
                % (age_high - age_low + 1)
            )
            self.assertEqual(player.age, age)
            self.assertEqual(player.birth_year, 1970 - age)
            self.assertGreaterEqual(player.birth_year, 1936)
            self.assertLessEqual(player.birth_year, 1949)
            self.assertEqual(
                player.birth_day,
                1 + (hmac_int(SEED, f'{player.universe_key}:birth_day') % 365),
            )
            self.assertEqual(
                player.given_name,
                _inline_given_name(SEED, player.universe_key, player.birth_year),
            )
            self.assertEqual(
                player.surname,
                SURNAMES[hmac_int(SEED, f'{player.universe_key}:surname') % 1000],
            )
            self.assertNotEqual(player.name, 'Joe LeCrone')
            college_index = hmac_int(SEED, f'{player.universe_key}:college') % 64
            college_name, college_slug, _kind = COLLEGES[college_index]
            self.assertEqual(player.college_slug, college_slug)
            self.assertEqual(player.college_name, college_name)
            self.assertEqual(colleges[player.college_slug], player.college_name)
            rate = 150 if position in ('QB', 'K', 'P') else OTHER_LEFT_HAND_PER_THOUSAND
            hand_ticket = hmac_int(SEED, f'{player.universe_key}:handedness') % 1000
            self.assertEqual(
                player.handedness,
                'left' if hand_ticket < rate else 'right',
            )
            height_low, height_high, weight_low, weight_high = APPROVED_BODY_BANDS[position]
            self.assertEqual(
                player.height_in,
                height_low + (
                    hmac_int(SEED, f'{player.universe_key}:height_in')
                    % (height_high - height_low + 1)
                ),
            )
            self.assertEqual(
                player.weight_lb,
                weight_low + (
                    hmac_int(SEED, f'{player.universe_key}:weight_lb')
                    % (weight_high - weight_low + 1)
                ),
            )
            self.assertGreaterEqual(player.height_in, 64)
            self.assertLessEqual(player.height_in, 84)
            self.assertGreaterEqual(player.weight_lb, 160)
            self.assertLessEqual(player.weight_lb, 360)
            self.assertFalse(hasattr(player, 'peak_age'))
            counts = per_club.setdefault(club_slug, {
                SLOT_STARTER: 0, SLOT_BACKUP: 0, SLOT_TAXI: 0, SLOT_FREE_AGENT: 0,
            })
            counts[kind] += 1

        self.assertEqual(set(per_club), {f'founding-{number:02d}' for number in range(1, 27)})
        for counts in per_club.values():
            self.assertEqual(counts, {
                SLOT_STARTER: 19,
                SLOT_BACKUP: 21,
                SLOT_TAXI: 8,
                SLOT_FREE_AGENT: 5,
            })
        self.assertEqual(opening[0].club_slug, 'founding-01')
        self.assertEqual(opening[0].slot_kind, SLOT_STARTER)
        self.assertEqual(opening[0].position, 'QB')
        self.assertEqual(opening[0].depth, 1)
        self.assertEqual(opening[52].slot_kind, SLOT_FREE_AGENT)
        self.assertEqual(opening[52].position, 'CB')
        self.assertFalse(opening[52].on_roster)
        self.assertEqual(opening[53].club_slug, 'founding-02')
        self.assertEqual(opening[-1].index, 1377)
        self.assertEqual(opening[-1].club_slug, 'founding-26')

    def test_sixty_class_headers_and_prospect_identities(self):
        headers = self.universe.class_headers
        classes = draft_classes()
        self.assertEqual(len(headers), 60)
        self.assertEqual([header.season_year for header in headers], list(range(1970, 2030)))
        for header, count in zip(headers, classes):
            self.assertEqual(header.season_year, count.season_year)
            self.assertEqual(header.franchise_count, count.franchise_count)
            self.assertEqual(header.rounds, count.rounds)
            self.assertEqual(header.draftable_count, count.draftable_count)
            self.assertEqual(header.undrafted_count, count.undrafted_count)
            self.assertEqual(header.class_size, count.class_size)
            self.assertEqual(
                header.coordinate,
                f'p-{RELEASE_SLUG}-class-{count.season_year}',
            )
            self.assertEqual(header.universe_key, player_key(SEED, header.coordinate))
            self.assertEqual(len(header.universe_key), 64)

        prospects = self.universe.prospects
        self.assertEqual(len(prospects), 19777)
        self.assertEqual(prospect_totals()[2], 19777)
        self.assertEqual(PROSPECT_AGE_BAND, (21, 23))
        by_year = {}
        for prospect in prospects:
            by_year.setdefault(prospect.season_year, []).append(prospect)

        age_low, age_high = PROSPECT_AGE_BAND
        for header in headers:
            group = by_year[header.season_year]
            self.assertEqual(len(group), header.class_size)
            for index, prospect in enumerate(group):
                self.assertEqual(prospect.index, index)
                self.assertEqual(
                    prospect.coordinate,
                    f'p-{RELEASE_SLUG}-draft-{header.season_year}-{index:05d}',
                )
                self.assertEqual(
                    prospect.universe_key,
                    player_key(SEED, prospect.coordinate),
                )
                age = age_low + (
                    hmac_int(SEED, f'{prospect.universe_key}:birth_year')
                    % (age_high - age_low + 1)
                )
                self.assertEqual(prospect.age, age)
                self.assertEqual(prospect.birth_year, header.season_year - age)
                self.assertGreaterEqual(prospect.birth_year, 1936)
                self.assertLessEqual(prospect.birth_year, 2008)
                self.assertEqual(
                    prospect.birth_day,
                    1 + (hmac_int(SEED, f'{prospect.universe_key}:birth_day') % 365),
                )
                self.assertEqual(
                    prospect.given_name,
                    _inline_given_name(SEED, prospect.universe_key, prospect.birth_year),
                )
                self.assertEqual(
                    prospect.surname,
                    SURNAMES[hmac_int(SEED, f'{prospect.universe_key}:surname') % 1000],
                )
                self.assertNotEqual(prospect.name, 'Joe LeCrone')
                college_name, college_slug, _kind = COLLEGES[
                    hmac_int(SEED, f'{prospect.universe_key}:college') % 64
                ]
                self.assertEqual(prospect.college_name, college_name)
                self.assertEqual(prospect.college_slug, college_slug)
                self.assertIsNone(prospect.position)
                self.assertIsNone(prospect.draft_pool)
                self.assertIsNone(prospect.draft_round)
                self.assertIsNone(prospect.draft_pick)
                self.assertIsNone(prospect.destination_slug)
                self.assertIsNone(prospect.debut_year)
                self.assertFalse(hasattr(prospect, 'height_in'))
                self.assertFalse(hasattr(prospect, 'handedness'))
                self.assertFalse(hasattr(prospect, 'peak_age'))

        self.assertEqual(len(by_year[1970]), 494)
        self.assertEqual(draft_class(1970).class_size, 494)
        for year in EXPANSION_YEARS:
            self.assertEqual(len(by_year[year]), draft_class(year).class_size)

    def test_locked_playable_totals_exclude_the_reserved_player(self):
        opening = self.universe.opening
        prospects = self.universe.prospects
        self.assertEqual(len(opening), 1378)
        self.assertEqual(len(prospects), 19777)
        self.assertEqual(len(opening) + len(prospects), 21155)
        self.assertEqual(playable_identity_count(), 21155)
        self.assertEqual(self.universe.playable_count, 21155)
        self.assertEqual(len(self.universe.class_headers), 60)
        self.assertNotIn('archive', self.universe._fields)
        names = [player.name for player in opening]
        names.extend(player.name for player in prospects)
        self.assertNotIn('Joe LeCrone', names)
        birth_years = [player.birth_year for player in opening]
        birth_years.extend(player.birth_year for player in prospects)
        self.assertTrue(all(1936 <= year <= 2008 for year in birth_years))
        self.assertFalse(any(1920 <= year <= 1935 for year in birth_years))
        keys = [player.universe_key for player in opening]
        keys.extend(player.universe_key for player in prospects)
        keys.extend(header.universe_key for header in self.universe.class_headers)
        self.assertEqual(len(keys), 1378 + 19777 + 60)
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(all(len(key) == 64 for key in keys))
