"""Load the immutable 1970 historical schedule template.

The checked-in source is the audited regular-season table. This loader
does not fetch it. An empty 1970 template is validated and then written
in one transaction. An exact rerun returns ``unchanged`` and writes
nothing. A partial or different 1970 set is left as it was. The loader
does not delete or repair rows, and it does not write project schedules,
timeline schedules, franchises, memberships, or release fingerprints.
"""

from collections import Counter, defaultdict
from typing import NamedTuple

from django.db import IntegrityError, transaction

from universe.generation.historical_1970_data import (
    GAMES,
    SEASON_YEAR,
    SLOTS,
    SOURCE_CITATION,
)
from universe.historical import (
    HistoricalScheduleBye,
    HistoricalScheduleGame,
    HistoricalScheduleSlot,
)


class HistoricalScheduleError(Exception):
    """The 1970 historical template cannot be written."""


_DIFFER = (
    'Stored 1970 historical template rows differ from the checked-in source. '
    'The loader will not delete or replace them.'
)

_CONFERENCE_ORDER = {'afc': 0, 'nfc': 1}
_DIVISION_ORDER = {'east': 0, 'central': 1, 'west': 2}
_DIVISION_SIZES = {
    ('afc', 'east'): 5,
    ('afc', 'central'): 4,
    ('afc', 'west'): 4,
    ('nfc', 'east'): 5,
    ('nfc', 'central'): 4,
    ('nfc', 'west'): 4,
}
_MERGER = HistoricalScheduleSlot.OpeningBasis.MERGER_PLACEMENT
_PLAYED = HistoricalScheduleGame.Status.PLAYED


class TemplateSlot(NamedTuple):
    conference_role: str
    division_role: str
    slot_number: int
    provenance_club: str


class TemplateGame(NamedTuple):
    week_played: int
    home: TemplateSlot
    away: TemplateSlot


class Template(NamedTuple):
    slots: tuple
    games: tuple


def load_historical_schedule_1970():
    """Write the 1970 template. Return ``created`` or ``unchanged``."""
    template = _checked_template()
    try:
        with transaction.atomic():
            state = _stored_state(template)
            if state == 'match':
                return 'unchanged'
            if state == 'differ':
                raise HistoricalScheduleError(_DIFFER)
            _insert(template)
            if _stored_state(template) != 'match':
                raise HistoricalScheduleError(
                    'The stored 1970 template did not match the checked-in source.'
                )
            _assert_inserted_order(template)
            return 'created'
    except IntegrityError as exc:
        raise HistoricalScheduleError(
            'A historical template insert violated a database constraint. '
            'The loader rolled back every template row.'
        ) from exc


def _checked_template():
    slots = tuple(
        TemplateSlot(conference, division, number, club)
        for conference, division, number, club in SLOTS
    )
    by_club = {slot.provenance_club: slot for slot in slots}
    try:
        games = tuple(
            TemplateGame(week, by_club[home], by_club[away])
            for week, home, away in GAMES
        )
    except KeyError as exc:
        raise HistoricalScheduleError(
            'A 1970 game uses a slot outside the template.'
        ) from exc
    template = Template(slots, games)
    _validate(template)
    return template


def _slot_key(slot):
    return (_CONFERENCE_ORDER[slot.conference_role], _DIVISION_ORDER[slot.division_role], slot.slot_number)


def _validate(template):
    if not SOURCE_CITATION.strip():
        raise HistoricalScheduleError('The 1970 source citation is missing.')
    if SEASON_YEAR != 1970:
        raise HistoricalScheduleError('This loader stores the 1970 template only.')
    slots = template.slots
    games = template.games
    if len(slots) != 26 or len(set(slots)) != 26:
        raise HistoricalScheduleError('The 1970 template needs exactly 26 slots.')
    if len(games) != 182:
        raise HistoricalScheduleError('The 1970 template needs exactly 182 games.')
    sizes = Counter((slot.conference_role, slot.division_role) for slot in slots)
    if sizes != Counter(_DIVISION_SIZES):
        raise HistoricalScheduleError('The 1970 divisions are not 5, 4, and 4 in each conference.')
    numbers = defaultdict(set)
    clubs = set()
    for slot in slots:
        if slot.conference_role not in _CONFERENCE_ORDER or slot.division_role not in _DIVISION_ORDER:
            raise HistoricalScheduleError('A 1970 slot uses an unknown conference or division.')
        expected = _DIVISION_SIZES[(slot.conference_role, slot.division_role)]
        if not 1 <= slot.slot_number <= expected:
            raise HistoricalScheduleError('A 1970 slot number is outside its division.')
        numbers[(slot.conference_role, slot.division_role)].add(slot.slot_number)
        if not slot.provenance_club or slot.provenance_club in clubs:
            raise HistoricalScheduleError('Each 1970 provenance club is stored once.')
        clubs.add(slot.provenance_club)
    if any(len(values) != _DIVISION_SIZES[key] for key, values in numbers.items()):
        raise HistoricalScheduleError('A 1970 division is missing a slot number.')
    if tuple(sorted(slots, key=_slot_key)) != slots:
        raise HistoricalScheduleError('The 1970 slots are not in template order.')

    played = Counter()
    homes = Counter()
    aways = Counter()
    week_counts = Counter()
    week_slots = defaultdict(set)
    directed = set()
    meetings = defaultdict(list)
    weeks = set()
    for game in games:
        if game.home == game.away:
            raise HistoricalScheduleError('A 1970 slot plays itself.')
        if game.home not in slots or game.away not in slots:
            raise HistoricalScheduleError('A 1970 game uses a slot outside the template.')
        if not 1 <= game.week_played <= 14:
            raise HistoricalScheduleError('A 1970 game is outside Weeks 1-14.')
        pair = (game.home, game.away)
        if pair in directed:
            raise HistoricalScheduleError('A 1970 directed game is repeated.')
        directed.add(pair)
        for slot in (game.home, game.away):
            if slot in week_slots[game.week_played]:
                raise HistoricalScheduleError('A 1970 slot plays twice in one week.')
            week_slots[game.week_played].add(slot)
            played[slot] += 1
        homes[game.home] += 1
        aways[game.away] += 1
        week_counts[game.week_played] += 1
        weeks.add(game.week_played)
        meetings[frozenset((game.home, game.away))].append(game)
    if weeks != set(range(1, 15)) or any(week_counts[week] != 13 for week in weeks):
        raise HistoricalScheduleError('Each 1970 week has exactly 13 games.')
    if set(played) != set(slots) or any(played[slot] != 14 for slot in slots):
        raise HistoricalScheduleError('Each 1970 slot plays exactly 14 games.')
    if any(homes[slot] != 7 or aways[slot] != 7 for slot in slots):
        raise HistoricalScheduleError('Each 1970 slot plays 7 home and 7 away.')
    if len(directed) != 182:
        raise HistoricalScheduleError('A 1970 template game is not unique.')
    home_and_home = 0
    singles = 0
    for rows in meetings.values():
        if len(rows) == 2:
            homes_in_pair = {row.home for row in rows}
            pair_slots = {rows[0].home, rows[0].away}
            if homes_in_pair != pair_slots:
                raise HistoricalScheduleError('A 1970 home-and-home pair is not both directions.')
            home_and_home += 1
        elif len(rows) == 1:
            singles += 1
        else:
            raise HistoricalScheduleError('A 1970 opponent pair is neither single nor home-and-home.')
    if len(meetings) != 138 or home_and_home != 44 or singles != 94:
        raise HistoricalScheduleError(
            'The 1970 template needs 138 opponent pairs, 44 home-and-home and 94 single.'
        )
    ordered = tuple(sorted(
        games,
        key=lambda game: (game.week_played, _slot_key(game.home), _slot_key(game.away)),
    ))
    if ordered != games:
        raise HistoricalScheduleError('The 1970 games are not in template order.')


def _stored_state(template):
    slot_rows = list(HistoricalScheduleSlot.objects.filter(season_year=SEASON_YEAR))
    game_rows = list(
        HistoricalScheduleGame.objects.filter(season_year=SEASON_YEAR).select_related(
            'home_slot',
            'away_slot',
        )
    )
    bye_count = HistoricalScheduleBye.objects.filter(season_year=SEASON_YEAR).count()
    if not slot_rows and not game_rows and bye_count == 0:
        return 'absent'
    if bye_count:
        return 'differ'
    expected_slots = {_stored_slot_key(slot) for slot in template.slots}
    stored_slots = {_row_slot_key(row) for row in slot_rows}
    if len(slot_rows) != len(expected_slots) or stored_slots != expected_slots:
        return 'differ'
    expected_games = {_stored_game_key(game) for game in template.games}
    stored_games = {_row_game_key(row) for row in game_rows}
    if len(game_rows) != len(expected_games) or stored_games != expected_games:
        return 'differ'
    return 'match'


def _stored_slot_key(slot):
    return (
        SEASON_YEAR,
        slot.conference_role,
        slot.division_role,
        slot.slot_number,
        slot.provenance_club,
        _MERGER,
        None,
        None,
        None,
        SOURCE_CITATION,
    )


def _row_slot_key(row):
    return (
        row.season_year,
        row.conference_role,
        row.division_role,
        row.slot_number,
        row.provenance_club,
        row.opening_basis,
        row.prior_place,
        row.prior_conference_role,
        row.prior_division_role,
        row.source_citation,
    )


def _stored_game_key(game):
    return (
        SEASON_YEAR,
        _stored_slot_key(game.home),
        _stored_slot_key(game.away),
        game.week_played,
        None,
        _PLAYED,
        SOURCE_CITATION,
    )


def _row_game_key(row):
    return (
        row.season_year,
        _row_slot_key(row.home_slot),
        _row_slot_key(row.away_slot),
        row.week_played,
        row.originally_scheduled_week,
        row.status,
        row.source_citation,
    )


def _insert(template):
    slot_models = [
        HistoricalScheduleSlot(
            season_year=SEASON_YEAR,
            conference_role=slot.conference_role,
            division_role=slot.division_role,
            slot_number=slot.slot_number,
            provenance_club=slot.provenance_club,
            opening_basis=_MERGER,
            prior_place=None,
            prior_conference_role=None,
            prior_division_role=None,
            source_citation=SOURCE_CITATION,
        )
        for slot in template.slots
    ]
    HistoricalScheduleSlot.objects.bulk_create(slot_models)
    stored = {
        (row.conference_role, row.division_role, row.slot_number): row
        for row in HistoricalScheduleSlot.objects.filter(season_year=SEASON_YEAR)
    }
    game_models = [
        HistoricalScheduleGame(
            season_year=SEASON_YEAR,
            home_slot=stored[(game.home.conference_role, game.home.division_role, game.home.slot_number)],
            away_slot=stored[(game.away.conference_role, game.away.division_role, game.away.slot_number)],
            week_played=game.week_played,
            originally_scheduled_week=None,
            status=_PLAYED,
            source_citation=SOURCE_CITATION,
        )
        for game in template.games
    ]
    HistoricalScheduleGame.objects.bulk_create(game_models)


def _assert_inserted_order(template):
    stored_slots = list(
        HistoricalScheduleSlot.objects.filter(season_year=SEASON_YEAR).order_by('id')
    )
    expected_slots = [
        (slot.conference_role, slot.division_role, slot.slot_number, slot.provenance_club)
        for slot in template.slots
    ]
    actual_slots = [
        (row.conference_role, row.division_role, row.slot_number, row.provenance_club)
        for row in stored_slots
    ]
    if actual_slots != expected_slots:
        raise HistoricalScheduleError('The 1970 slot insert order was not preserved.')
    stored_games = list(
        HistoricalScheduleGame.objects.filter(season_year=SEASON_YEAR)
        .select_related('home_slot', 'away_slot')
        .order_by('id')
    )
    expected_games = [
        (
            game.week_played,
            game.home.provenance_club,
            game.away.provenance_club,
        )
        for game in template.games
    ]
    actual_games = [
        (row.week_played, row.home_slot.provenance_club, row.away_slot.provenance_club)
        for row in stored_games
    ]
    if actual_games != expected_games:
        raise HistoricalScheduleError('The 1970 game insert order was not preserved.')
