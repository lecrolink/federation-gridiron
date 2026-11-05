"""Shared era rules, draft classes, expansion, and the 1970 opening state.

Published-release guards are application checks. They are not database
triggers. Confirmed years and arithmetic stay release data. This module
does not generate people or insert rows.
"""

from django.db import models

from universe.models import UniverseFranchise, UniverseRelease
from universe.people import DEFENSE_SCHEMES, OFFENSE_SCHEMES, POSITIONS, ReleaseChild


MARKETS = ('controlled', 'transitional', 'modern')

STYLE_KEYS = (
    'run-first',
    'passing-rules',
    'timing-specialists',
    'cap-and-size',
    'spread-influence',
    'modern-passing',
)

WAIVER_RULES = (
    'all_releases',
    'veterans_until_deadline',
    'veterans_until_cutdown',
)

FREE_AGENCY_TYPES = ('reserve', 'restricted', 'unrestricted_and_restricted')

FA_COMPENSATION = ('none', 'tender_pick', 'restricted_only')

CAP_STATUS = ('off', 'on')

FRANCHISE_TAGS = ('off', 'top_five_position_average')

# twelve_team_fill_to_six is the approved 1999–2001 value: 12 teams, 6
# qualifiers per conference, every division winner, wild cards filling each
# conference to 6, byes for the top two seeds in each conference, and a first
# round of 3 vs 6 and 4 vs 5. In those seasons conference-1 supplies 4
# division winners and 2 wild cards, and conference-2 supplies 3 and 3.
# Order inside either group is outside this value.
PLAYOFF_FORMATS = (
    'eight_team_3_and_1',
    'eight_team_2_and_2',
    'twelve_team_fill_to_six',
    'twelve_team_4_and_2',
)

OVERTIME_RULES = ('tie', 'sudden_death', 'both_teams_possess')

SACK_LEADERBOARDS = ('unofficial', 'official')

OPENING_ROLES = ('starter', 'backup', 'taxi', 'free_agent')

OPENING_STAFF_ROLES = (
    'gm',
    'head_coach',
    'offensive_coordinator',
    'defensive_coordinator',
)


def _choices(values):
    return [(value, value) for value in values]


def _quota_name(kind, position):
    return f'quota_{kind}_{position.lower()}'


def _sum_of(field_names):
    total = models.F(field_names[0])
    for name in field_names[1:]:
        total = total + models.F(name)
    return total


def _product(left, right):
    return models.F(left) * models.F(right)


ACTIVE_QUOTA_FIELDS = tuple(_quota_name('active', position) for position in POSITIONS)
TAXI_QUOTA_FIELDS = tuple(_quota_name('taxi', position) for position in POSITIONS)


class EraRuleProfile(ReleaseChild):
    release = models.ForeignKey(
        UniverseRelease,
        on_delete=models.CASCADE,
        related_name='era_profiles',
    )
    profile_key = models.CharField(max_length=32)
    first_year = models.PositiveSmallIntegerField()
    last_year = models.PositiveSmallIntegerField()
    market = models.CharField(max_length=16, choices=_choices(MARKETS))
    style_key = models.CharField(max_length=32, choices=_choices(STYLE_KEYS))
    active_limit = models.PositiveSmallIntegerField()
    taxi_limit = models.PositiveSmallIntegerField()
    ir_max = models.PositiveSmallIntegerField()
    ir_min_weeks = models.PositiveSmallIntegerField()
    ir_returns_allowed = models.PositiveSmallIntegerField()
    draft_rounds = models.PositiveSmallIntegerField()
    rookie_contract_years_min = models.PositiveSmallIntegerField()
    rookie_contract_years_max = models.PositiveSmallIntegerField()
    veteran_contract_years_min = models.PositiveSmallIntegerField()
    veteran_contract_years_max = models.PositiveSmallIntegerField()
    accrued_seasons_for_ufa = models.PositiveSmallIntegerField(null=True, blank=True)
    trade_deadline_week = models.PositiveSmallIntegerField()
    future_pick_years = models.PositiveSmallIntegerField()
    waiver_rule = models.CharField(max_length=32, choices=_choices(WAIVER_RULES))
    free_agency_type = models.CharField(max_length=32, choices=_choices(FREE_AGENCY_TYPES))
    fa_compensation = models.CharField(max_length=32, choices=_choices(FA_COMPENSATION))
    cap_status = models.CharField(max_length=8, choices=_choices(CAP_STATUS))
    cap_limit = models.BigIntegerField(null=True, blank=True)
    minimum_salary = models.BigIntegerField(null=True, blank=True)
    franchise_tag = models.CharField(max_length=32, choices=_choices(FRANCHISE_TAGS))
    playoff_format = models.CharField(max_length=32, choices=_choices(PLAYOFF_FORMATS))
    schedule_length = models.PositiveSmallIntegerField()
    overtime_rule = models.CharField(max_length=32, choices=_choices(OVERTIME_RULES))
    sack_leaderboard = models.CharField(max_length=16, choices=_choices(SACK_LEADERBOARDS))
    rookie_slot_scale = models.JSONField(null=True, blank=True)

    class Meta:
        app_label = 'universe'
        ordering = ['release', 'first_year']
        constraints = [
            models.UniqueConstraint(
                fields=['release', 'profile_key'],
                name='unique_era_profile_key',
            ),
            models.CheckConstraint(
                condition=~models.Q(profile_key=''),
                name='universe_era_key',
            ),
            models.CheckConstraint(
                condition=models.Q(last_year__gte=models.F('first_year')),
                name='universe_era_years',
            ),
            models.CheckConstraint(
                condition=models.Q(market__in=MARKETS),
                name='universe_era_market',
            ),
            models.CheckConstraint(
                condition=models.Q(style_key__in=STYLE_KEYS),
                name='universe_era_style',
            ),
            models.CheckConstraint(
                condition=models.Q(active_limit__gte=1),
                name='universe_era_active_limit',
            ),
            models.CheckConstraint(
                condition=models.Q(draft_rounds__gte=1),
                name='universe_era_rounds',
            ),
            models.CheckConstraint(
                condition=models.Q(schedule_length__gte=1),
                name='universe_era_schedule',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    rookie_contract_years_min__gte=1,
                    rookie_contract_years_max__gte=models.F('rookie_contract_years_min'),
                ),
                name='universe_era_rookie_span',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    veteran_contract_years_min__gte=1,
                    veteran_contract_years_max__gte=models.F('veteran_contract_years_min'),
                ),
                name='universe_era_veteran_span',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    trade_deadline_week__gte=1,
                    trade_deadline_week__lte=models.F('schedule_length'),
                ),
                name='universe_era_deadline',
            ),
            models.CheckConstraint(
                condition=models.Q(waiver_rule__in=WAIVER_RULES),
                name='universe_era_waiver',
            ),
            models.CheckConstraint(
                condition=models.Q(free_agency_type__in=FREE_AGENCY_TYPES),
                name='universe_era_free_agency',
            ),
            models.CheckConstraint(
                condition=models.Q(fa_compensation__in=FA_COMPENSATION),
                name='universe_era_fa_comp',
            ),
            models.CheckConstraint(
                condition=models.Q(cap_status__in=CAP_STATUS),
                name='universe_era_cap_status',
            ),
            models.CheckConstraint(
                condition=models.Q(franchise_tag__in=FRANCHISE_TAGS),
                name='universe_era_tag',
            ),
            models.CheckConstraint(
                condition=models.Q(playoff_format__in=PLAYOFF_FORMATS),
                name='universe_era_playoff',
            ),
            models.CheckConstraint(
                condition=models.Q(overtime_rule__in=OVERTIME_RULES),
                name='universe_era_overtime',
            ),
            models.CheckConstraint(
                condition=models.Q(sack_leaderboard__in=SACK_LEADERBOARDS),
                name='universe_era_sacks',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(cap_status='on')
                    | models.Q(market='modern')
                ),
                name='universe_era_cap_market',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(cap_status='off')
                    | models.Q(cap_limit__isnull=True)
                ),
                name='universe_era_cap_off',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(market='controlled')
                    | models.Q(accrued_seasons_for_ufa__isnull=True)
                ),
                name='universe_era_ufa_closed',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(market='controlled')
                    | models.Q(accrued_seasons_for_ufa__isnull=False)
                ),
                name='universe_era_ufa_open',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(cap_limit__isnull=True)
                    | models.Q(cap_limit__gte=1)
                ),
                name='universe_era_cap_positive',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(minimum_salary__isnull=True)
                    | models.Q(minimum_salary__gte=0)
                ),
                name='universe_era_minimum',
            ),
            models.CheckConstraint(
                condition=models.Q(active_limit=_sum_of(ACTIVE_QUOTA_FIELDS)),
                name='universe_era_active_sum',
            ),
            models.CheckConstraint(
                condition=models.Q(taxi_limit=_sum_of(TAXI_QUOTA_FIELDS)),
                name='universe_era_taxi_sum',
            ),
        ]
        indexes = [
            models.Index(
                fields=['release', 'first_year', 'last_year'],
                name='universe_era_span_idx',
            ),
        ]

    def __str__(self):
        return self.profile_key

    def bound_release(self):
        if not self.release_id:
            return None
        return self.release

    def extra_clean(self, errors):
        if not self.release_id:
            return
        release = self.release
        if (
            self.first_year < release.first_season_year
            or self.last_year > release.last_season_year
        ):
            errors['first_year'] = 'The profile span stays inside the release.'


for _position in POSITIONS:
    EraRuleProfile.add_to_class(
        _quota_name('active', _position),
        models.PositiveSmallIntegerField(),
    )
    EraRuleProfile.add_to_class(
        _quota_name('taxi', _position),
        models.PositiveSmallIntegerField(),
    )


SCHEME_SIDES = ('offense', 'defense')


class EraAllowedScheme(ReleaseChild):
    """One offense or defense scheme that may be newly assigned in this era.

    The row belongs to an ``EraRuleProfile``. It does not store a scheme on
    a staff season rating, and it does not rewrite historical employment.
    Hiring and season-advancement checks are later services.
    """

    profile = models.ForeignKey(
        EraRuleProfile,
        on_delete=models.CASCADE,
        related_name='allowed_schemes',
    )
    side = models.CharField(max_length=16)
    scheme = models.CharField(max_length=32)

    class Meta:
        app_label = 'universe'
        ordering = ['profile', 'side', 'scheme']
        constraints = [
            models.UniqueConstraint(
                fields=['profile', 'side', 'scheme'],
                name='unique_era_allowed_scheme',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(side='offense', scheme__in=OFFENSE_SCHEMES)
                    | models.Q(side='defense', scheme__in=DEFENSE_SCHEMES)
                ),
                name='universe_era_scheme_side',
            ),
        ]

    def __str__(self):
        return f'{self.side} {self.scheme}'

    def bound_release(self):
        if not self.profile_id:
            return None
        return self.profile.release

    def guard_field(self):
        return 'profile'

    def extra_clean(self, errors):
        if self.side == 'offense' and self.scheme not in OFFENSE_SCHEMES:
            errors['scheme'] = 'An offensive row must use an offensive scheme.'
        elif self.side == 'defense' and self.scheme not in DEFENSE_SCHEMES:
            errors['scheme'] = 'A defensive row must use a defensive scheme.'
        elif self.side not in SCHEME_SIDES:
            errors['side'] = 'The scheme side is offense or defense.'


class EraCollegeWeight(ReleaseChild):
    profile = models.ForeignKey(
        EraRuleProfile,
        on_delete=models.CASCADE,
        related_name='college_weights',
    )
    college = models.ForeignKey(
        'universe.College',
        on_delete=models.CASCADE,
        related_name='era_weights',
    )
    weight = models.PositiveIntegerField()

    class Meta:
        app_label = 'universe'
        ordering = ['profile', 'college']
        constraints = [
            models.UniqueConstraint(
                fields=['profile', 'college'],
                name='unique_era_college_weight',
            ),
            models.CheckConstraint(
                condition=models.Q(weight__gte=1),
                name='universe_college_weight_positive',
            ),
        ]

    def __str__(self):
        return f'{self.profile_id} {self.college_id}'

    def bound_release(self):
        if not self.profile_id:
            return None
        return self.profile.release

    def guard_field(self):
        return 'profile'

    def extra_clean(self, errors):
        if not self.profile_id or not self.college_id:
            return
        if self.profile.release_id != self.college.release_id:
            errors['college'] = 'The college must belong to the profile\'s release.'


class DraftClass(ReleaseChild):
    release = models.ForeignKey(
        UniverseRelease,
        on_delete=models.CASCADE,
        related_name='draft_classes',
    )
    profile = models.ForeignKey(
        EraRuleProfile,
        on_delete=models.PROTECT,
        related_name='draft_classes',
    )
    season_year = models.PositiveSmallIntegerField()
    franchise_count = models.PositiveSmallIntegerField()
    rounds = models.PositiveSmallIntegerField()
    draftable_count = models.PositiveSmallIntegerField()
    undrafted_multiplier = models.PositiveSmallIntegerField()
    undrafted_count = models.PositiveSmallIntegerField()

    class Meta:
        app_label = 'universe'
        ordering = ['release', 'season_year']
        constraints = [
            models.UniqueConstraint(
                fields=['release', 'season_year'],
                name='unique_draft_class_year',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    franchise_count__gte=1,
                    rounds__gte=1,
                    undrafted_multiplier__gte=1,
                ),
                name='universe_draft_class_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(draftable_count=_product('rounds', 'franchise_count')),
                name='universe_draft_class_draftable',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    undrafted_count=_product('undrafted_multiplier', 'franchise_count'),
                ),
                name='universe_draft_class_undrafted',
            ),
        ]
        indexes = [
            models.Index(fields=['season_year'], name='universe_draft_year_idx'),
        ]

    def __str__(self):
        return f'{self.release_id} {self.season_year}'

    def bound_release(self):
        if not self.release_id:
            return None
        return self.release

    def extra_clean(self, errors):
        if not self.release_id or not self.profile_id:
            return
        release = self.release
        if (
            self.season_year < release.first_season_year
            or self.season_year > release.last_season_year
        ):
            errors['season_year'] = 'A draft class year is inside the release.'
        if self.profile.release_id != self.release_id:
            errors['profile'] = 'The era profile must belong to this class\'s release.'
        elif (
            self.season_year < self.profile.first_year
            or self.season_year > self.profile.last_year
        ):
            errors['profile'] = 'The era profile must cover this class year.'
        elif self.rounds != self.profile.draft_rounds:
            errors['rounds'] = 'Draft rounds follow the era profile for that year.'
        event = ExpansionEvent.objects.filter(
            release_id=self.release_id,
            season_year=self.season_year,
        ).first()
        if event is not None and self.undrafted_multiplier != event.undrafted_multiplier:
            errors['undrafted_multiplier'] = (
                'An expansion class uses that event\'s undrafted multiplier.'
            )


class ExpansionEvent(ReleaseChild):
    release = models.ForeignKey(
        UniverseRelease,
        on_delete=models.CASCADE,
        related_name='expansion_events',
    )
    season_year = models.PositiveSmallIntegerField()
    existing_club_count = models.PositiveSmallIntegerField()
    new_club_count = models.PositiveSmallIntegerField()
    active_target = models.PositiveSmallIntegerField()
    taxi_capacity = models.PositiveSmallIntegerField()
    max_losses_per_existing = models.PositiveSmallIntegerField()
    min_losses_per_existing = models.PositiveSmallIntegerField()
    clubs_at_max_loss = models.PositiveSmallIntegerField()
    clubs_at_min_loss = models.PositiveSmallIntegerField()
    veteran_picks_per_new = models.PositiveSmallIntegerField()
    veteran_picks_total = models.PositiveSmallIntegerField()
    rookie_picks_per_new = models.PositiveSmallIntegerField()
    undrafted_multiplier = models.PositiveSmallIntegerField()
    undrafted_pool = models.PositiveSmallIntegerField()
    undrafted_reserved_per_new = models.PositiveSmallIntegerField()
    expansion_draft_order_rule = models.TextField(blank=True)
    roster_building_rule = models.TextField(blank=True)
    free_agent_rule = models.TextField(blank=True)
    rookie_draft_slot_rule = models.TextField(blank=True)
    gm_hiring_rule = models.TextField(blank=True)
    hc_hiring_rule = models.TextField(blank=True)

    class Meta:
        app_label = 'universe'
        ordering = ['release', 'season_year']
        constraints = [
            models.UniqueConstraint(
                fields=['release', 'season_year'],
                name='unique_expansion_event_year',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    new_club_count__gte=1,
                    undrafted_multiplier__gte=1,
                ),
                name='universe_expand_positive',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    min_losses_per_existing__lt=models.F('max_losses_per_existing'),
                ),
                name='universe_expand_loss_order',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    veteran_picks_total=_product('veteran_picks_per_new', 'new_club_count'),
                ),
                name='universe_expand_veteran_total',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    existing_club_count=(
                        models.F('clubs_at_max_loss') + models.F('clubs_at_min_loss')
                    ),
                ),
                name='universe_expand_club_split',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    veteran_picks_total=(
                        _product('clubs_at_max_loss', 'max_losses_per_existing')
                        + _product('clubs_at_min_loss', 'min_losses_per_existing')
                    ),
                ),
                name='universe_expand_loss_total',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    active_target=(
                        models.F('veteran_picks_per_new')
                        + models.F('rookie_picks_per_new')
                        + models.F('undrafted_reserved_per_new')
                    ),
                ),
                name='universe_expand_active_target',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    undrafted_pool__gte=_product(
                        'undrafted_reserved_per_new',
                        'new_club_count',
                    ),
                ),
                name='universe_expand_reserve_pool',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    undrafted_pool=_product(
                        'undrafted_multiplier',
                        'existing_club_count',
                    ) + _product('undrafted_multiplier', 'new_club_count'),
                ),
                name='universe_expand_undrafted_pool',
            ),
        ]
        indexes = [
            models.Index(fields=['season_year'], name='universe_expand_year_idx'),
        ]

    def __str__(self):
        return f'{self.release_id} {self.season_year}'

    def bound_release(self):
        if not self.release_id:
            return None
        return self.release

    def extra_clean(self, errors):
        if not self.release_id:
            return
        release = self.release
        if (
            self.season_year <= release.first_season_year
            or self.season_year > release.last_season_year
        ):
            errors['season_year'] = (
                'An expansion year is inside the release and after its first season.'
            )


class ExpansionClub(ReleaseChild):
    event = models.ForeignKey(
        ExpansionEvent,
        on_delete=models.CASCADE,
        related_name='clubs',
    )
    franchise = models.ForeignKey(
        UniverseFranchise,
        on_delete=models.PROTECT,
        related_name='expansion_clubs',
    )
    selection_order = models.PositiveSmallIntegerField()

    class Meta:
        app_label = 'universe'
        ordering = ['event', 'selection_order']
        constraints = [
            models.UniqueConstraint(
                fields=['event', 'franchise'],
                name='unique_expansion_club',
            ),
            models.UniqueConstraint(
                fields=['event', 'selection_order'],
                name='unique_expansion_club_order',
            ),
            models.CheckConstraint(
                condition=models.Q(selection_order__gte=1),
                name='universe_expand_club_order',
            ),
        ]

    def __str__(self):
        return f'{self.event_id} {self.selection_order}'

    def bound_release(self):
        if not self.event_id:
            return None
        return self.event.release

    def guard_field(self):
        return 'event'

    def extra_clean(self, errors):
        if not self.event_id or not self.franchise_id:
            return
        if self.franchise.release_id != self.event.release_id:
            errors['franchise'] = 'The franchise must belong to the event\'s release.'
        if self.franchise.origin != UniverseFranchise.Origin.EXPANSION:
            errors['franchise'] = 'An expansion club is an expansion franchise.'
        if self.franchise.first_active_season != self.event.season_year:
            errors['franchise'] = 'The franchise enters in the event year.'
        if self.selection_order > self.event.new_club_count:
            errors['selection_order'] = (
                'Selection order stays within the number of entering clubs.'
            )


class OpeningAssignment(ReleaseChild):
    release = models.ForeignKey(
        UniverseRelease,
        on_delete=models.CASCADE,
        related_name='opening_assignments',
    )
    player = models.ForeignKey(
        'universe.UniversePlayer',
        on_delete=models.CASCADE,
        related_name='opening_assignment',
    )
    franchise = models.ForeignKey(
        UniverseFranchise,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='opening_assignments',
    )
    role = models.CharField(max_length=16, choices=_choices(OPENING_ROLES))
    depth = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        app_label = 'universe'
        ordering = ['release', 'franchise', 'role', 'depth']
        constraints = [
            models.UniqueConstraint(
                fields=['release', 'player'],
                name='unique_opening_player',
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=OPENING_ROLES),
                name='universe_opening_role',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        role='free_agent',
                        franchise__isnull=True,
                        depth__isnull=True,
                    )
                    | (
                        ~models.Q(role='free_agent')
                        & models.Q(franchise__isnull=False, depth__gte=1)
                    )
                ),
                name='universe_opening_free_agent',
            ),
        ]
        indexes = [
            models.Index(
                fields=['franchise', 'role'],
                name='universe_openassign_idx',
            ),
        ]

    def __str__(self):
        return f'{self.player_id} {self.role}'

    def bound_release(self):
        if not self.release_id:
            return None
        return self.release

    def extra_clean(self, errors):
        if not self.release_id or not self.player_id:
            return
        if self.player.release_id != self.release_id:
            errors['player'] = 'The player must belong to this assignment\'s release.'
        elif self.player.cohort != 'opening':
            errors['player'] = 'Only the opening cohort is assigned before the draft.'
        if self.franchise_id:
            if self.franchise.release_id != self.release_id:
                errors['franchise'] = 'The franchise must belong to this release.'
            elif self.franchise.origin != UniverseFranchise.Origin.FOUNDING:
                errors['franchise'] = 'An opening roster uses a founding franchise.'


class OpeningContract(ReleaseChild):
    assignment = models.OneToOneField(
        OpeningAssignment,
        on_delete=models.CASCADE,
        related_name='contract',
    )
    years = models.PositiveSmallIntegerField()
    salary_per_year = models.BigIntegerField()
    guaranteed = models.BigIntegerField()
    signing_bonus = models.BigIntegerField()
    start_year = models.PositiveSmallIntegerField()
    end_year = models.PositiveSmallIntegerField()

    class Meta:
        app_label = 'universe'
        constraints = [
            models.CheckConstraint(
                condition=models.Q(years__gte=1),
                name='universe_opening_contract_years',
            ),
            models.CheckConstraint(
                condition=models.Q(end_year__gte=models.F('start_year')),
                name='universe_opening_contract_span',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    salary_per_year__gte=0,
                    guaranteed__gte=0,
                    signing_bonus__gte=0,
                ),
                name='universe_opening_contract_money',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    guaranteed__lte=_product('salary_per_year', 'years'),
                ),
                name='universe_opening_contract_guarantee',
            ),
        ]

    def __str__(self):
        return f'opening contract {self.assignment_id}'

    def bound_release(self):
        if not self.assignment_id:
            return None
        return self.assignment.release

    def guard_field(self):
        return 'assignment'

    def extra_clean(self, errors):
        if not self.assignment_id:
            return
        assignment = self.assignment
        if assignment.role == 'free_agent' or not assignment.franchise_id:
            errors['assignment'] = 'An opening free agent has no opening contract.'
            return
        release = assignment.release
        if self.start_year > release.first_season_year:
            errors['start_year'] = (
                'An opening contract starts by the release\'s first playable season.'
            )
        if self.end_year < release.first_season_year:
            errors['end_year'] = (
                'An opening contract runs through the release\'s first playable season.'
            )
        profile = EraRuleProfile.objects.filter(
            release_id=release.id,
            first_year__lte=release.first_season_year,
            last_year__gte=release.first_season_year,
        ).first()
        if profile is not None and (
            self.years < profile.veteran_contract_years_min
            or self.years > profile.veteran_contract_years_max
        ):
            errors['years'] = 'Opening contract length follows the opening era profile.'


class OpeningStaffAssignment(ReleaseChild):
    release = models.ForeignKey(
        UniverseRelease,
        on_delete=models.CASCADE,
        related_name='opening_staff',
    )
    franchise = models.ForeignKey(
        UniverseFranchise,
        on_delete=models.PROTECT,
        related_name='opening_staff',
    )
    gm = models.ForeignKey(
        'universe.UniverseGM',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='opening_assignment',
    )
    coach = models.ForeignKey(
        'universe.UniverseCoach',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='opening_assignment',
    )
    role = models.CharField(max_length=32, choices=_choices(OPENING_STAFF_ROLES))

    class Meta:
        app_label = 'universe'
        ordering = ['release', 'franchise', 'role']
        constraints = [
            models.UniqueConstraint(
                fields=['release', 'franchise', 'role'],
                name='unique_opening_staff_role',
            ),
            models.UniqueConstraint(
                fields=['gm'],
                name='unique_opening_staff_gm',
            ),
            models.UniqueConstraint(
                fields=['coach'],
                name='unique_opening_staff_coach',
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=OPENING_STAFF_ROLES),
                name='universe_opening_staff_role',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(role='gm', gm__isnull=False, coach__isnull=True)
                    | (
                        ~models.Q(role='gm')
                        & models.Q(gm__isnull=True, coach__isnull=False)
                    )
                ),
                name='universe_opening_staff_person',
            ),
        ]
        indexes = [
            models.Index(
                fields=['release', 'role'],
                name='universe_openstaff_idx',
            ),
        ]

    def __str__(self):
        return f'{self.franchise_id} {self.role}'

    def bound_release(self):
        if not self.release_id:
            return None
        return self.release

    def extra_clean(self, errors):
        if not self.release_id or not self.franchise_id:
            return
        if self.franchise.release_id != self.release_id:
            errors['franchise'] = 'The franchise must belong to this release.'
        elif self.franchise.origin != UniverseFranchise.Origin.FOUNDING:
            errors['franchise'] = 'Opening staff are employed by founding franchises.'
        if self.gm_id:
            if self.gm.release_id != self.release_id:
                errors['gm'] = 'The GM must belong to this release.'
            if self.role != 'gm':
                errors['role'] = 'A GM assignment uses the GM role.'
        if self.coach_id:
            if self.coach.release_id != self.release_id:
                errors['coach'] = 'The coach must belong to this release.'
            specialty = self.coach.specialty
            if self.role == 'offensive_coordinator' and specialty not in (
                'offense',
                'both',
            ):
                errors['role'] = (
                    'An offensive coordinator supports offense or both.'
                )
            if self.role == 'defensive_coordinator' and specialty not in (
                'defense',
                'both',
            ):
                errors['role'] = (
                    'A defensive coordinator supports defense or both.'
                )
            if self.role == 'gm':
                errors['role'] = 'A coach assignment is not the GM role.'
