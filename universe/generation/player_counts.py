"""Documented 1970–2029 player counts.

Pure arithmetic from the schedule and the era roster defaults. This module
does not import Django, write a database, create a player, assign a draft
destination, or simulate a game. Archive headcount is not a result here:
the 5-year mean is still an assumption.
"""

from typing import NamedTuple


FIRST_SEASON_YEAR = 1970
LAST_SEASON_YEAR = 2029
SEASON_COUNT = LAST_SEASON_YEAR - FIRST_SEASON_YEAR + 1

FOUNDING_CLUBS = 26
OPENING_ACTIVE_LIMIT = 40
OPENING_TAXI_LIMIT = 8
FREE_AGENTS_PER_FOUNDING_CLUB = 5
UNDRAFTED_MULTIPLIER = 2

# An expansion draft redistributes players. It does not mint identities.
SHARED_PEOPLE_CREATED_BY_EXPANSION_DRAFT = 0
EXPANSION_YEARS = (1976, 1995, 1999, 2002)

# The 1970 rookie class is a draft class. It is not on the opening rosters.
OPENING_ROSTER_INCLUDES_ROOKIE_CLASS = False

POSITIONS = (
    'QB', 'HB', 'FB', 'WR', 'TE', 'LT', 'LG', 'C', 'RG', 'RT',
    'DE', 'DT', 'ILB', 'OLB', 'CB', 'FS', 'SS', 'K', 'P',
)

# Per-franchise counts. 1970–1977 is the fixed opening quota. The later rows
# are the documented fills that sum to the 45, 47, and 53 active limits.
ACTIVE_QUOTA_1970 = {
    'QB': 2, 'HB': 2, 'FB': 2, 'WR': 4, 'TE': 2,
    'LT': 2, 'LG': 1, 'C': 2, 'RG': 1, 'RT': 2,
    'DE': 3, 'DT': 4, 'ILB': 2, 'OLB': 4, 'CB': 3,
    'FS': 1, 'SS': 1, 'K': 1, 'P': 1,
}
ACTIVE_QUOTA_45 = {
    'QB': 2, 'HB': 2, 'FB': 2, 'WR': 6, 'TE': 2,
    'LT': 2, 'LG': 2, 'C': 2, 'RG': 1, 'RT': 2,
    'DE': 3, 'DT': 4, 'ILB': 2, 'OLB': 4, 'CB': 4,
    'FS': 1, 'SS': 2, 'K': 1, 'P': 1,
}
ACTIVE_QUOTA_47 = {
    'QB': 2, 'HB': 2, 'FB': 1, 'WR': 7, 'TE': 2,
    'LT': 2, 'LG': 2, 'C': 2, 'RG': 2, 'RT': 2,
    'DE': 3, 'DT': 4, 'ILB': 2, 'OLB': 4, 'CB': 5,
    'FS': 1, 'SS': 2, 'K': 1, 'P': 1,
}
ACTIVE_QUOTA_53 = {
    'QB': 2, 'HB': 2, 'FB': 1, 'WR': 9, 'TE': 2,
    'LT': 2, 'LG': 2, 'C': 2, 'RG': 2, 'RT': 2,
    'DE': 4, 'DT': 4, 'ILB': 3, 'OLB': 4, 'CB': 6,
    'FS': 2, 'SS': 2, 'K': 1, 'P': 1,
}
TAXI_QUOTA = {
    'QB': 1, 'HB': 1, 'FB': 0, 'WR': 1, 'TE': 0,
    'LT': 0, 'LG': 1, 'C': 0, 'RG': 1, 'RT': 0,
    'DE': 1, 'DT': 0, 'ILB': 1, 'OLB': 0, 'CB': 0,
    'FS': 1, 'SS': 0, 'K': 0, 'P': 0,
}


class DraftClassCount(NamedTuple):
    season_year: int
    franchise_count: int
    rounds: int
    draftable_count: int
    undrafted_count: int

    @property
    def class_size(self):
        return self.draftable_count + self.undrafted_count


def _require_season(year):
    if isinstance(year, bool) or not isinstance(year, int):
        raise ValueError('The player schedule is 1970 through 2029.')
    if year < FIRST_SEASON_YEAR or year > LAST_SEASON_YEAR:
        raise ValueError('The player schedule is 1970 through 2029.')


def n_year(year):
    """Active franchises in one season of the 1970–2029 schedule."""
    _require_season(year)
    if year <= 1975:
        return 26
    if year <= 1994:
        return 28
    if year <= 1998:
        return 30
    if year <= 2001:
        return 31
    return 32


def draft_rounds(year):
    """Rookie-draft length for one season. This is the era default."""
    _require_season(year)
    if year <= 1977:
        return 17
    if year <= 1986:
        return 12
    if year <= 1994:
        return 8
    return 7


def draft_class(year):
    """One shared rookie class. Size follows N(year), including expansion."""
    clubs = n_year(year)
    rounds = draft_rounds(year)
    return DraftClassCount(
        season_year=year,
        franchise_count=clubs,
        rounds=rounds,
        draftable_count=rounds * clubs,
        undrafted_count=UNDRAFTED_MULTIPLIER * clubs,
    )


def draft_classes():
    """The 60 classes from 1970 through 2029, one per season."""
    return tuple(
        draft_class(year)
        for year in range(FIRST_SEASON_YEAR, LAST_SEASON_YEAR + 1)
    )


def prospect_totals():
    """Draftable, undrafted, and combined prospect counts across 60 classes."""
    classes = draft_classes()
    draftable = sum(row.draftable_count for row in classes)
    undrafted = sum(row.undrafted_count for row in classes)
    return draftable, undrafted, draftable + undrafted


def opening_rostered_count():
    """1970 pre-draft roster slots: 26 clubs times 40 active plus 8 taxi."""
    return FOUNDING_CLUBS * (OPENING_ACTIVE_LIMIT + OPENING_TAXI_LIMIT)


def opening_free_agent_count():
    """Five free agents for each of the 26 founding clubs."""
    return FOUNDING_CLUBS * FREE_AGENTS_PER_FOUNDING_CLUB


def opening_cohort_count():
    """Opening veterans and free agents. The 1970 class is not included."""
    return opening_rostered_count() + opening_free_agent_count()


def playable_identity_count():
    """Opening cohort plus every 1970–2029 prospect."""
    return opening_cohort_count() + prospect_totals()[2]


def quota_total(quota):
    """Sum one per-franchise position quota."""
    return sum(quota.values())


def active_quota(year):
    """The documented per-franchise active quota for one season.

    The result is a fresh dict. Changing it does not change the stored quota.
    """
    _require_season(year)
    if year <= 1977:
        source = ACTIVE_QUOTA_1970
    elif year <= 1986:
        source = ACTIVE_QUOTA_45
    elif year <= 1994:
        source = ACTIVE_QUOTA_47
    else:
        source = ACTIVE_QUOTA_53
    return dict(source)


def taxi_quota(year):
    """The documented taxi quota. It is the same eight in every season.

    The result is a fresh dict. Changing it does not change the stored quota.
    """
    _require_season(year)
    return dict(TAXI_QUOTA)
