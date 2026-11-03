"""Provisional city and nickname rows for generator version fg-founding-2.

The lists and their order are part of the generator. HMAC selection uses the
list index, so inserting, removing, or reordering a value changes later draws.
These are fictional names. They are not NFL cities or NFL team nicknames.
"""

from django.db import transaction

from universe.generation.founding import hmac_int
from universe.models import ProvisionalFranchiseName, UniverseFranchise

CITIES = (
    'Aldermere',
    'Ashcombe',
    'Bracken Hill',
    'Calderrun',
    'Cedarwick',
    'Copperline',
    'Deepford',
    'Driftmere',
    'Dunhaven',
    'Eastmarch',
    'Elmstead',
    'Fairhollow',
    'Foxglen',
    'Graylock',
    'Harrowford',
    'Highspire',
    'Ironvale',
    'Ivydale',
    'Juniper Bay',
    'Kestrel Reach',
    'Larkspur',
    'Lowridge',
    'Millhaven',
    'Mossglen',
    'Norbury',
    'Northfen',
    'Oakrend',
    'Orchard Bend',
    'Pellam',
    'Pineharbor',
    'Quarry Gate',
    'Redhollow',
    'Ridgewater',
    'Sable Crossing',
    'Silvermere',
    'Thornbridge',
    'Underhill',
    'Valebrook',
    'Westcairn',
    'Yarrowfield',
)

NICKNAMES = (
    'Albatrosses',
    'Anchors',
    'Boars',
    'Cartographers',
    'Cobblers',
    'Condors',
    'Cutters',
    'Dorymen',
    'Drifters',
    'Embers',
    'Ferrymen',
    'Foxes',
    'Gannets',
    'Glassblowers',
    'Griffins',
    'Harriers',
    'Herons',
    'Jackals',
    'Joiners',
    'Keelmen',
    'Lamplighters',
    'Lancers',
    'Lynx',
    'Millers',
    'Navigators',
    'Ospreys',
    'Oystermen',
    'Pilots',
    'Quarriers',
    'Riggers',
    'Rooks',
    'Schooners',
    'Sentinels',
    'Shearwaters',
    'Shipwrights',
    'Sparrows',
    'Stallions',
    'Surveyors',
    'Terns',
    'Thrushes',
)

EXPANSION_SLUGS = (
    'expansion-1976-01',
    'expansion-1976-02',
    'expansion-1995-01',
    'expansion-1995-02',
    'expansion-1999-01',
    'expansion-2002-01',
)

FIELDS = ('city', 'nickname')


def _pool(field):
    if field == 'city':
        return CITIES
    if field == 'nickname':
        return NICKNAMES
    raise ProvisionalNameError('Only a city or a nickname can be drawn.')


class ProvisionalNameError(Exception):
    """A provisional city or nickname cannot be drawn or replaced."""


def assignment_order():
    """Expansion slugs in lexicographic order, then founding-01 through founding-26.

    The founding numbers are numeric. The combined sequence is not a single
    lexicographic sort of every slug.
    """
    expansion = tuple(sorted(EXPANSION_SLUGS))
    founding = tuple(f'founding-{number:02d}' for number in range(1, 27))
    return expansion + founding


def display_name_for(city, nickname):
    return f'{city} {nickname}'


def _status_field(field):
    return f'{field}_status'


def _require_unique(rows, field):
    seen = set()
    for row in rows.values():
        value = row[field]
        if value in seen:
            raise ProvisionalNameError('Stored provisional names are not unique.')
        seen.add(value)


def _unused(pool, blocked):
    return tuple(value for value in pool if value not in blocked)


def _draw(seed, release_slug, slug, field, blocked):
    pool = _pool(field)
    unused = _unused(pool, blocked)
    if not unused:
        raise ProvisionalNameError('No unused provisional name remains.')
    coordinate = f'provisional:{release_slug}:{slug}:{field}'
    choice = unused[hmac_int(seed, coordinate) % len(unused)]
    blocked.add(choice)
    return choice


def resolve_names(seed, release_slug, stored, *, redraw):
    """Return city and nickname values for all 32 schedule slugs.

    ``stored`` maps placeholder slug to ``city``, ``city_status``,
    ``nickname``, and ``nickname_status``. An empty map is the first write.
    A same-seed call keeps every stored value. A new seed redraws only fields
    that are still provisional and blocks every replaced value.
    """
    order = assignment_order()
    if stored and set(stored) != set(order):
        raise ProvisionalNameError(
            'Provisional names must be exactly the 32 schedule slugs.'
        )
    if stored:
        _require_unique(stored, 'city')
        _require_unique(stored, 'nickname')

    resolved = {slug: {} for slug in order}
    for field in FIELDS:
        status_field = _status_field(field)
        blocked = set()
        for slug in order:
            row = stored.get(slug)
            if row is None or row[status_field] != ProvisionalFranchiseName.Status.REPLACED:
                continue
            value = row[field]
            if value in blocked:
                raise ProvisionalNameError('Stored provisional names are not unique.')
            blocked.add(value)
            resolved[slug][field] = value
            resolved[slug][status_field] = ProvisionalFranchiseName.Status.REPLACED
        for slug in order:
            if field in resolved[slug]:
                continue
            row = stored.get(slug)
            if row is not None and not redraw:
                value = row[field]
                if value in blocked:
                    raise ProvisionalNameError('Stored provisional names are not unique.')
                blocked.add(value)
                resolved[slug][field] = value
                resolved[slug][status_field] = row[status_field]
                continue
            resolved[slug][field] = _draw(seed, release_slug, slug, field, blocked)
            resolved[slug][status_field] = ProvisionalFranchiseName.Status.PROVISIONAL
    return resolved


def replace_provisional_value(name, field, value):
    """Fill one provisional field and mark that field replaced.

    The other field is left as it is. A replaced field is not rewritten.
    """
    if field not in FIELDS:
        raise ProvisionalNameError('Only a city or a nickname can be replaced.')
    status_field = _status_field(field)
    if getattr(name, status_field) != ProvisionalFranchiseName.Status.PROVISIONAL:
        raise ProvisionalNameError('A replaced city or nickname is kept.')
    if not isinstance(value, str) or value == '':
        raise ProvisionalNameError('A replacement needs a nonempty value.')
    siblings = ProvisionalFranchiseName.objects.filter(
        release_id=name.release_id,
    ).exclude(pk=name.pk)
    for other in siblings:
        if getattr(other, field) == value:
            raise ProvisionalNameError(
                f'That {field} is already used by another franchise in this release.'
            )
    setattr(name, field, value)
    setattr(name, status_field, ProvisionalFranchiseName.Status.REPLACED)
    with transaction.atomic():
        name.save()
        franchise = UniverseFranchise.objects.filter(
            release_id=name.release_id,
            placeholder_slug=name.placeholder_slug,
        ).first()
        if franchise is not None:
            franchise.display_name = display_name_for(name.city, name.nickname)
            franchise.save()
    return name
