"""Approved fg-founding-2 franchise names.

This is the only copy of the 32 city and nickname pairs. ``fg-founding-2``
reads it. ``fg-founding-1`` does not.
"""

APPROVED_ENTRY_COUNT = 32
SLUG_MAX_LENGTH = 64
CITY_MAX_LENGTH = 255
NICKNAME_MAX_LENGTH = 255
DISPLAY_NAME_MAX_LENGTH = 255
REPLACED = 'replaced'

APPROVED_EXPANSION_YEARS = {
    'expansion-1976-01': 1976,
    'expansion-1976-02': 1976,
    'expansion-1995-01': 1995,
    'expansion-1995-02': 1995,
    'expansion-1999-01': 1999,
    'expansion-2002-01': 2002,
}

# (placeholder slug, city, nickname). The display name is city, one space,
# and nickname. founding-10 uses U+2019 in Nor’easters.
APPROVED_FRANCHISE_NAMES = (
    ('founding-01', 'Chicago', 'Navigators'),
    ('founding-02', 'Cleveland', 'Riffs'),
    ('founding-03', 'Detroit', 'Wheels'),
    ('founding-04', 'Milwaukee', 'Hammers'),
    ('founding-05', 'Baltimore', 'Barons'),
    ('founding-06', 'Cincinnati', 'Centurions'),
    ('founding-07', 'Pittsburgh', 'Predators'),
    ('founding-08', 'Washington', 'Defenders'),
    ('founding-09', 'Minneapolis', 'Loons'),
    ('founding-10', 'Boston', 'Nor\u2019easters'),
    ('founding-11', 'St. Louis', 'Stallions'),
    ('founding-12', 'New York', 'Emperors'),
    ('founding-13', 'Atlanta', 'Iron Horses'),
    ('founding-14', 'Miami', 'Barracudas'),
    ('founding-15', 'Tampa', 'Phantoms'),
    ('founding-16', 'New Orleans', 'River Kings'),
    ('founding-17', 'Denver', 'Lions'),
    ('founding-18', 'Kansas City', 'Prairie Dogs'),
    ('founding-19', 'Phoenix', 'Renegades'),
    ('founding-20', 'San Diego', 'Destroyers'),
    ('founding-21', 'Dallas', 'Outlaws'),
    ('founding-22', 'Los Angeles', 'Condors'),
    ('founding-23', 'Houston', 'Roughnecks'),
    ('founding-24', 'San Francisco', 'Juggernauts'),
    ('founding-25', 'Philadelphia', 'Independents'),
    ('founding-26', 'Seattle', 'Orcas'),
    ('expansion-1976-01', 'Indianapolis', 'Speedsters'),
    ('expansion-1976-02', 'Portland', 'Lumberjacks'),
    ('expansion-1995-01', 'Memphis', 'Marauders'),
    ('expansion-1995-02', 'San Antonio', 'Armadillos'),
    ('expansion-1999-01', 'Charlotte', 'Prospectors'),
    ('expansion-2002-01', 'Niagara', 'Rapids'),
)


class ApprovedNameError(Exception):
    """The approved franchise map cannot be written."""


def display_name(city, nickname):
    return f'{city} {nickname}'


def expected_slugs():
    founding = tuple(f'founding-{number:02d}' for number in range(1, 27))
    return frozenset(founding) | frozenset(APPROVED_EXPANSION_YEARS)


def validate_approved_names(entries, *, expansion_years=None):
    """Reject an incomplete, duplicate, or unexpected franchise-name map.

    ``entries`` is a sequence of ``(slug, city, nickname)`` or
    ``(slug, city, nickname, display_name)``. A supplied display name must
    equal the city, one space, and the nickname.
    """
    years = APPROVED_EXPANSION_YEARS if expansion_years is None else expansion_years
    rows = tuple(entries)
    if len(rows) != APPROVED_ENTRY_COUNT:
        raise ApprovedNameError(
            'The approved franchise map must contain 32 entries.'
        )
    slugs = []
    for row in rows:
        if len(row) not in (3, 4):
            raise ApprovedNameError(
                'An approved franchise entry must carry a slug, city, and nickname.'
            )
        slugs.append(row[0])
    if len(set(slugs)) != len(slugs):
        raise ApprovedNameError('A placeholder slug appears more than once.')
    found = set(slugs)
    allowed = expected_slugs()
    if found - allowed:
        raise ApprovedNameError(
            'An unexpected placeholder slug is in the approved franchise map.'
        )
    if allowed - found:
        raise ApprovedNameError(
            'The approved franchise map is missing a placeholder slug.'
        )
    cities = []
    nicknames = []
    for row in rows:
        slug, city, nickname = row[0], row[1], row[2]
        if not isinstance(city, str) or not isinstance(nickname, str):
            raise ApprovedNameError('An approved city or nickname is empty.')
        if city == '' or nickname == '':
            raise ApprovedNameError('An approved city or nickname is empty.')
        if len(slug) > SLUG_MAX_LENGTH:
            raise ApprovedNameError(
                'An approved city, nickname, or display name exceeds its field limit.'
            )
        shown = row[3] if len(row) == 4 else display_name(city, nickname)
        if shown != display_name(city, nickname):
            raise ApprovedNameError(
                'The display name must be the city, one space, and the nickname.'
            )
        if (
            len(city) > CITY_MAX_LENGTH
            or len(nickname) > NICKNAME_MAX_LENGTH
            or len(shown) > DISPLAY_NAME_MAX_LENGTH
        ):
            raise ApprovedNameError(
                'An approved city, nickname, or display name exceeds its field limit.'
            )
        cities.append(city)
        nicknames.append(nickname)
    if len(set(cities)) != APPROVED_ENTRY_COUNT:
        raise ApprovedNameError('Approved cities are not unique.')
    if len(set(nicknames)) != APPROVED_ENTRY_COUNT:
        raise ApprovedNameError('Approved nicknames are not unique.')
    for slug in slugs:
        if not slug.startswith('expansion-'):
            continue
        parsed = _expansion_year(slug)
        if years.get(slug) != parsed:
            raise ApprovedNameError(
                'An expansion slug does not match its approved expansion year.'
            )
    return rows


def approved_name_rows():
    """Return the 32 approved names with both statuses replaced.

    The map is validated before this returns. The caller writes these rows
    directly and does not draw a city or a nickname.
    """
    validate_approved_names(APPROVED_FRANCHISE_NAMES)
    rows = {}
    for slug, city, nickname in APPROVED_FRANCHISE_NAMES:
        rows[slug] = {
            'city': city,
            'city_status': REPLACED,
            'nickname': nickname,
            'nickname_status': REPLACED,
        }
    return rows


def _expansion_year(slug):
    parts = slug.split('-')
    if len(parts) != 3 or parts[0] != 'expansion':
        raise ApprovedNameError(
            'An expansion slug does not match its approved expansion year.'
        )
    try:
        return int(parts[1])
    except ValueError as exc:
        raise ApprovedNameError(
            'An expansion slug does not match its approved expansion year.'
        ) from exc
