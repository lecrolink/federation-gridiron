"""Shared player, coach, and GM identities for one universe release.

Published-release guards in ``clean()``, ``save()``, and instance
``delete()`` are application checks. They are not database triggers.
"""

from django.core.exceptions import ValidationError
from django.db import models

from universe.models import (
    ARCHIVE_SEASON_COUNT,
    UniverseFranchise,
    UniverseRelease,
    _reject_published_child,
)


PLAYER_COMPONENTS = (
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

GM_ABILITIES = (
    'talent_evaluation',
    'scheme_fit',
    'negotiation',
    'cap_management',
    'draft_strategy',
    'free_agency',
    'roster_construction',
    'player_development',
    'discipline',
    'leadership',
    'risk_tolerance',
    'patience',
    'coach_evaluation',
    'veteran_preference',
    'pick_trade_willingness',
)

COACH_ABILITIES = (
    'offensive_scheme',
    'defensive_scheme',
    'game_management',
    'clock_management',
    'fourth_down_aggression',
    'aggressiveness',
    'in_game_adjustments',
    'adaptability',
    'player_development',
    'preparation',
    'motivation',
    'leadership',
    'discipline',
    'fatigue_management',
    'injury_management',
    'staff_management',
    'veteran_preference',
)

POSITIONS = (
    'QB', 'HB', 'FB', 'WR', 'TE', 'LT', 'LG', 'C', 'RG', 'RT',
    'DE', 'DT', 'ILB', 'OLB', 'CB', 'FS', 'SS', 'K', 'P',
)

PLAYER_TRAITS = (
    'film_junkie', 'gym_rat', 'hothead', 'quiet_pro', 'mentor', 'big_play',
)

GM_PRIORITIES = (
    'quarterback', 'skill', 'offensive_line', 'defensive_line',
    'linebacker', 'defensive_back', 'special_teams',
)

GM_TAGS = (
    'quarterback_eye', 'lineman_eye', 'finds_speed', 'finds_size',
    'trade_market', 'cap_discipline', 'loyalty', 'medical_caution',
    'late_round', 'character',
)

GM_TRAITS = (
    'patient', 'impatient', 'secretive', 'public', 'delegator', 'hands_on',
    'stubborn', 'adaptable', 'gambler', 'conservative',
)

COACH_TAGS = (
    'run_game', 'passing_game', 'red_zone', 'defense', 'special_teams',
    'develops_quarterbacks', 'develops_linemen', 'halftime_adjustments',
    'discipline', 'closer',
)

COACH_TRAITS = (
    'players_coach', 'disciplinarian', 'innovator', 'conservative',
    'volatile', 'even_keel', 'workaholic', 'delegator',
)

OFFENSE_SCHEMES = (
    'power_run', 'option', 'pro_style', 'west_coast', 'air_raid',
)

DEFENSE_SCHEMES = (
    'stop_run', 'balanced', 'pressure_quarterback',
)

GM_POST_ROLES = ('gm', 'personnel_director', 'scout', 'college_coach')

COACH_POST_ROLES = (
    'head_coach',
    'offensive_coordinator',
    'defensive_coordinator',
    'position_coach',
    'college_head_coach',
)

STAFF_POST_ROLES = GM_POST_ROLES + COACH_POST_ROLES

COLLEGE_POST_ROLES = ('college_coach', 'college_head_coach')

# Pro chairs are a Federation franchise or an outside organization.
# College chairs are a shared college or an outside organization name.
# Scout and position-coach posts may use any one of those employers.
PRO_CHAIR_ROLES = (
    'gm',
    'personnel_director',
    'head_coach',
    'offensive_coordinator',
    'defensive_coordinator',
)

PLAYER_AWARDS = (
    'mvp', 'opoy', 'dpoy', 'oroy', 'droy',
    'all_league_1', 'all_league_2', 'all_star',
)

ALL_LEAGUE_KINDS = ('all_league_1', 'all_league_2')


def _choices(values):
    return [(value, value) for value in values]


def _archive_bounds(release):
    last_year = release.first_season_year - 1
    first_year = release.first_season_year - ARCHIVE_SEASON_COUNT
    return first_year, last_year


def _in_archive(release, year):
    first_year, last_year = _archive_bounds(release)
    return first_year <= year <= last_year


def _rating_constraints(components, prefix, reputation=False):
    constraints = []
    for component in components:
        initial = f'initial_{component}'
        potential = f'potential_{component}'
        constraints.append(
            models.CheckConstraint(
                condition=(
                    models.Q(**{f'{initial}__lte': 100})
                    & models.Q(**{f'{potential}__lte': 100})
                    & models.Q(**{f'{initial}__lte': models.F(potential)})
                ),
                name=f'{prefix}_{component}',
            )
        )
    if reputation:
        constraints.append(
            models.CheckConstraint(
                condition=models.Q(initial_reputation__lte=100),
                name=f'{prefix}_reputation',
            )
        )
    return constraints


def _distinct_nullable(left, right, name):
    return models.CheckConstraint(
        condition=(
            models.Q(**{f'{left}__isnull': True})
            | models.Q(**{f'{right}__isnull': True})
            | ~models.Q(**{left: models.F(right)})
        ),
        name=name,
    )


def _stat_constraints(prefix):
    return [
        models.CheckConstraint(
            condition=models.Q(starts__lte=models.F('games')),
            name=f'{prefix}_starts',
        ),
        models.CheckConstraint(
            condition=models.Q(fumbles_lost__lte=models.F('fumbles')),
            name=f'{prefix}_fumbles',
        ),
        models.CheckConstraint(
            condition=models.Q(completions__lte=models.F('pass_attempts')),
            name=f'{prefix}_completions',
        ),
        models.CheckConstraint(
            condition=models.Q(receptions__lte=models.F('targets')),
            name=f'{prefix}_receptions',
        ),
        models.CheckConstraint(
            condition=models.Q(fg_made__lte=models.F('fg_attempts')),
            name=f'{prefix}_fg',
        ),
        models.CheckConstraint(
            condition=models.Q(xp_made__lte=models.F('xp_attempts')),
            name=f'{prefix}_xp',
        ),
        models.CheckConstraint(
            condition=models.Q(punts_inside_20__lte=models.F('punts')),
            name=f'{prefix}_inside_20',
        ),
        models.CheckConstraint(
            condition=models.Q(punt_touchbacks__lte=models.F('punts')),
            name=f'{prefix}_punt_tb',
        ),
        models.CheckConstraint(
            condition=models.Q(sacks__lte=250),
            name=f'{prefix}_sacks',
        ),
        models.CheckConstraint(
            condition=models.Q(
                completions_allowed__lte=models.F('targets_in_coverage'),
            ),
            name=f'{prefix}_coverage_allowed',
        ),
        models.CheckConstraint(
            condition=models.Q(coverage_snaps__lte=models.F('defensive_snaps')),
            name=f'{prefix}_coverage_snaps',
        ),
        models.CheckConstraint(
            condition=models.Q(run_block_snaps__lte=models.F('offensive_snaps')),
            name=f'{prefix}_run_blocks',
        ),
        models.CheckConstraint(
            condition=models.Q(pass_block_snaps__lte=models.F('offensive_snaps')),
            name=f'{prefix}_pass_blocks',
        ),
    ]


class ReleaseChild(models.Model):
    class Meta:
        abstract = True

    def bound_release(self):
        raise NotImplementedError

    def guard_field(self):
        return 'release'

    def extra_clean(self, errors):
        return None

    def clean(self):
        super().clean()
        errors = {}
        self.extra_clean(errors)
        release = self.bound_release()
        if release is not None:
            _reject_published_child(
                self,
                release,
                errors,
                new_row_field=self.guard_field(),
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        release = self.bound_release()
        if release is not None and release.status == UniverseRelease.Status.PUBLISHED:
            raise ValidationError(
                'A published release cannot lose shared rows through model delete.'
            )
        return super().delete(*args, **kwargs)


class SeasonStatColumns(models.Model):
    games = models.PositiveSmallIntegerField(default=0)
    starts = models.PositiveSmallIntegerField(default=0)
    offensive_snaps = models.PositiveSmallIntegerField(default=0)
    defensive_snaps = models.PositiveSmallIntegerField(default=0)
    special_teams_snaps = models.PositiveSmallIntegerField(default=0)
    penalties = models.PositiveSmallIntegerField(default=0)
    penalty_yards = models.PositiveSmallIntegerField(default=0)
    fumbles = models.PositiveSmallIntegerField(default=0)
    fumbles_lost = models.PositiveSmallIntegerField(default=0)
    pass_attempts = models.PositiveSmallIntegerField(default=0)
    completions = models.PositiveSmallIntegerField(default=0)
    pass_yards = models.IntegerField(default=0)
    pass_touchdowns = models.PositiveSmallIntegerField(default=0)
    interceptions_thrown = models.PositiveSmallIntegerField(default=0)
    sacks_taken = models.PositiveSmallIntegerField(default=0)
    sack_yards = models.PositiveSmallIntegerField(default=0)
    rush_attempts = models.PositiveSmallIntegerField(default=0)
    rush_yards = models.IntegerField(default=0)
    rush_touchdowns = models.PositiveSmallIntegerField(default=0)
    targets = models.PositiveSmallIntegerField(default=0)
    receptions = models.PositiveSmallIntegerField(default=0)
    receiving_yards = models.IntegerField(default=0)
    receiving_touchdowns = models.PositiveSmallIntegerField(default=0)
    drops = models.PositiveSmallIntegerField(default=0)
    run_block_snaps = models.PositiveSmallIntegerField(default=0)
    pass_block_snaps = models.PositiveSmallIntegerField(default=0)
    tackles = models.PositiveSmallIntegerField(default=0)
    tackles_for_loss = models.PositiveSmallIntegerField(default=0)
    sacks = models.PositiveSmallIntegerField(default=0)
    quarterback_hits = models.PositiveSmallIntegerField(default=0)
    forced_fumbles = models.PositiveSmallIntegerField(default=0)
    fumble_recoveries = models.PositiveSmallIntegerField(default=0)
    passes_defended = models.PositiveSmallIntegerField(default=0)
    defensive_touchdowns = models.PositiveSmallIntegerField(default=0)
    coverage_snaps = models.PositiveSmallIntegerField(default=0)
    targets_in_coverage = models.PositiveSmallIntegerField(default=0)
    completions_allowed = models.PositiveSmallIntegerField(default=0)
    interceptions = models.PositiveSmallIntegerField(default=0)
    yards_allowed = models.IntegerField(default=0)
    sacks_allowed = models.PositiveSmallIntegerField(default=0)
    fg_made = models.PositiveSmallIntegerField(default=0)
    fg_attempts = models.PositiveSmallIntegerField(default=0)
    fg_long = models.PositiveSmallIntegerField(default=0)
    xp_made = models.PositiveSmallIntegerField(default=0)
    xp_attempts = models.PositiveSmallIntegerField(default=0)
    kickoff_touchbacks = models.PositiveSmallIntegerField(default=0)
    punts = models.PositiveSmallIntegerField(default=0)
    punt_yards = models.IntegerField(default=0)
    punts_inside_20 = models.PositiveSmallIntegerField(default=0)
    punt_touchbacks = models.PositiveSmallIntegerField(default=0)
    punt_long = models.PositiveSmallIntegerField(default=0)

    class Meta:
        abstract = True


class College(ReleaseChild):
    release = models.ForeignKey(
        UniverseRelease,
        on_delete=models.CASCADE,
        related_name='colleges',
    )
    slug = models.SlugField(max_length=64)
    name = models.CharField(max_length=255)

    class Meta:
        app_label = 'universe'
        ordering = ['release', 'slug']
        constraints = [
            models.UniqueConstraint(
                fields=['release', 'slug'],
                name='unique_college_slug_per_release',
            ),
            models.CheckConstraint(
                condition=~models.Q(name=''),
                name='universe_college_name_nonempty',
            ),
        ]
        indexes = [
            models.Index(fields=['slug'], name='universe_college_slug_idx'),
        ]

    def __str__(self):
        return self.slug

    def bound_release(self):
        if not self.release_id:
            return None
        return self.release


class UniversePlayer(ReleaseChild):
    class Handedness(models.TextChoices):
        LEFT = 'left', 'Left'
        RIGHT = 'right', 'Right'

    class Cohort(models.TextChoices):
        OPENING = 'opening', 'Opening'
        ARCHIVE = 'archive', 'Archive'
        DRAFT = 'draft', 'Draft'

    class DraftPool(models.TextChoices):
        DRAFTABLE = 'draftable', 'Draftable'
        UNDRAFTED = 'undrafted', 'Undrafted'

    release = models.ForeignKey(
        UniverseRelease,
        on_delete=models.CASCADE,
        related_name='players',
    )
    college = models.ForeignKey(
        College,
        on_delete=models.PROTECT,
        related_name='players',
    )
    origin_franchise = models.ForeignKey(
        UniverseFranchise,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='origin_players',
    )
    draft_class = models.ForeignKey(
        'universe.DraftClass',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='players',
    )
    origin_profile = models.ForeignKey(
        'universe.EraRuleProfile',
        on_delete=models.PROTECT,
        related_name='origin_players',
    )
    universe_key = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    birth_year = models.PositiveSmallIntegerField()
    birth_day = models.PositiveSmallIntegerField()
    position = models.CharField(max_length=4, choices=_choices(POSITIONS))
    secondary_position_1 = models.CharField(
        max_length=4,
        choices=_choices(POSITIONS),
        null=True,
        blank=True,
    )
    secondary_position_2 = models.CharField(
        max_length=4,
        choices=_choices(POSITIONS),
        null=True,
        blank=True,
    )
    handedness = models.CharField(max_length=8, choices=Handedness.choices)
    height_in = models.PositiveSmallIntegerField()
    weight_lb = models.PositiveSmallIntegerField()
    cohort = models.CharField(max_length=16, choices=Cohort.choices)
    draft_pool = models.CharField(
        max_length=16,
        choices=DraftPool.choices,
        null=True,
        blank=True,
    )
    first_pro_year = models.PositiveSmallIntegerField(null=True, blank=True)
    origin_draft_year = models.PositiveSmallIntegerField(null=True, blank=True)
    origin_draft_round = models.PositiveSmallIntegerField(null=True, blank=True)
    origin_draft_pick = models.PositiveSmallIntegerField(null=True, blank=True)
    peak_age = models.PositiveSmallIntegerField()
    development_rate = models.PositiveSmallIntegerField()
    decline_rate = models.PositiveSmallIntegerField()
    board_rank = models.PositiveSmallIntegerField(null=True, blank=True)
    trait_1 = models.CharField(
        max_length=32,
        choices=_choices(PLAYER_TRAITS),
        null=True,
        blank=True,
    )
    trait_2 = models.CharField(
        max_length=32,
        choices=_choices(PLAYER_TRAITS),
        null=True,
        blank=True,
    )
    trait_3 = models.CharField(
        max_length=32,
        choices=_choices(PLAYER_TRAITS),
        null=True,
        blank=True,
    )

    class Meta:
        app_label = 'universe'
        ordering = ['universe_key']
        indexes = [
            models.Index(
                fields=['release', 'cohort'],
                name='universe_player_cohort_idx',
            ),
            models.Index(fields=['position'], name='universe_player_position_idx'),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(position__in=POSITIONS),
                name='universe_player_position',
            ),
            models.CheckConstraint(
                condition=models.Q(birth_year__gte=1920, birth_year__lte=2008),
                name='universe_player_birth_year',
            ),
            models.CheckConstraint(
                condition=models.Q(birth_day__gte=1, birth_day__lte=365),
                name='universe_player_birth_day',
            ),
            models.CheckConstraint(
                condition=models.Q(height_in__gte=64, height_in__lte=84),
                name='universe_player_height',
            ),
            models.CheckConstraint(
                condition=models.Q(weight_lb__gte=160, weight_lb__lte=360),
                name='universe_player_weight',
            ),
            models.CheckConstraint(
                condition=models.Q(peak_age__gte=24, peak_age__lte=32),
                name='universe_player_peak_age',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    development_rate__gte=1,
                    development_rate__lte=100,
                    decline_rate__gte=1,
                    decline_rate__lte=100,
                ),
                name='universe_player_development',
            ),
            models.CheckConstraint(
                condition=models.Q(cohort__in=['opening', 'archive', 'draft']),
                name='universe_player_cohort',
            ),
            models.CheckConstraint(
                condition=models.Q(handedness__in=['left', 'right']),
                name='universe_player_handedness',
            ),
            models.CheckConstraint(
                condition=(
                    (
                        models.Q(cohort='draft')
                        & models.Q(draft_pool__in=['draftable', 'undrafted'])
                    )
                    | (
                        ~models.Q(cohort='draft')
                        & models.Q(draft_pool__isnull=True)
                    )
                ),
                name='universe_player_draft_pool',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(board_rank__isnull=True)
                    | models.Q(draft_pool='draftable', board_rank__gte=1)
                ),
                name='universe_player_board_rank',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(cohort='draft')
                    | (
                        models.Q(origin_draft_round__isnull=True)
                        & models.Q(origin_draft_pick__isnull=True)
                        & models.Q(origin_franchise__isnull=True)
                    )
                ),
                name='universe_player_prospect_draft',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(origin_draft_round__isnull=True)
                    | models.Q(
                        origin_draft_round__gte=1,
                        origin_draft_round__lte=17,
                    )
                ),
                name='universe_player_draft_round',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(origin_draft_pick__isnull=True)
                    | models.Q(origin_draft_pick__gte=1)
                ),
                name='universe_player_draft_pick',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(secondary_position_1__isnull=True)
                    | ~models.Q(secondary_position_1=models.F('position'))
                ),
                name='universe_player_secondary_1',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(secondary_position_2__isnull=True)
                    | ~models.Q(secondary_position_2=models.F('position'))
                ),
                name='universe_player_secondary_2',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(secondary_position_2__isnull=True)
                    | models.Q(secondary_position_1__isnull=False)
                ),
                name='universe_player_secondary_order',
            ),
            _distinct_nullable(
                'secondary_position_1',
                'secondary_position_2',
                'universe_player_secondary_distinct',
            ),
            _distinct_nullable('trait_1', 'trait_2', 'universe_player_trait_12'),
            _distinct_nullable('trait_1', 'trait_3', 'universe_player_trait_13'),
            _distinct_nullable('trait_2', 'trait_3', 'universe_player_trait_23'),
            models.CheckConstraint(
                condition=(
                    models.Q(trait_2__isnull=True)
                    | models.Q(trait_1__isnull=False)
                )
                & (
                    models.Q(trait_3__isnull=True)
                    | models.Q(trait_2__isnull=False)
                ),
                name='universe_player_trait_order',
            ),
            models.CheckConstraint(
                condition=~models.Q(universe_key='') & ~models.Q(name=''),
                name='universe_player_identity_nonempty',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(cohort='draft', draft_class__isnull=False)
                    | (
                        ~models.Q(cohort='draft')
                        & models.Q(draft_class__isnull=True)
                    )
                ),
                name='universe_player_draft_class',
            ),
            models.UniqueConstraint(
                fields=['draft_class', 'board_rank'],
                name='unique_player_class_board',
            ),
        ]

    def __str__(self):
        return self.universe_key

    def bound_release(self):
        if not self.release_id:
            return None
        return self.release

    def extra_clean(self, errors):
        if self.college_id and self.release_id and self.college.release_id != self.release_id:
            errors['college'] = 'The college must belong to this player\'s release.'
        if (
            self.origin_franchise_id
            and self.release_id
            and self.origin_franchise.release_id != self.release_id
        ):
            errors['origin_franchise'] = (
                'The origin franchise must belong to this player\'s release.'
            )
        if not self.release_id:
            return
        release = self.release
        if not self.origin_profile_id:
            errors['origin_profile'] = 'Every player records an origin era profile.'
        elif self.origin_profile.release_id != self.release_id:
            errors['origin_profile'] = (
                'The origin profile must belong to this player\'s release.'
            )
        if self.cohort == self.Cohort.DRAFT:
            if not self.draft_class_id:
                errors['draft_class'] = 'A draft prospect belongs to one draft class.'
            elif self.draft_class.release_id != self.release_id:
                errors['draft_class'] = (
                    'The draft class must belong to this player\'s release.'
                )
            if self.draft_pool == self.DraftPool.UNDRAFTED and self.first_pro_year is not None:
                errors['first_pro_year'] = (
                    'An undrafted prospect has no shared professional debut.'
                )
            class_year = self.draft_class.season_year if self.draft_class_id else None
            if self.draft_pool == self.DraftPool.DRAFTABLE:
                if self.first_pro_year is None or self.origin_draft_year is None:
                    errors['origin_draft_year'] = (
                        'A draftable prospect records the class year, not a result.'
                    )
                elif (
                    self.first_pro_year != self.origin_draft_year
                    or self.first_pro_year < release.first_season_year
                    or self.first_pro_year > release.last_season_year
                ):
                    errors['first_pro_year'] = (
                        'A draftable prospect\'s class year is inside the release.'
                    )
            if (
                class_year is not None
                and self.origin_draft_year != class_year
                and 'origin_draft_year' not in errors
            ):
                errors['origin_draft_year'] = (
                    'The shared class year is not a timeline draft result.'
                )
            if (
                self.draft_pool == self.DraftPool.UNDRAFTED
                and class_year is not None
                and (
                    class_year < release.first_season_year
                    or class_year > release.last_season_year
                )
            ):
                errors['draft_class'] = 'A draft class year is inside the release.'
        elif self.cohort in (self.Cohort.OPENING, self.Cohort.ARCHIVE):
            if self.draft_class_id:
                errors['draft_class'] = (
                    'Opening and archive players are not in a playable draft class.'
                )
            if self.first_pro_year is None or not _in_archive(release, self.first_pro_year):
                errors['first_pro_year'] = (
                    'Opening and archive players debut in the archive window.'
                )
            if (
                self.origin_draft_year is not None
                and not _in_archive(release, self.origin_draft_year)
            ):
                errors['origin_draft_year'] = (
                    'A shared historical draft year is inside the archive window.'
                )
        if self.trait_2 and not self.trait_1:
            errors['trait_2'] = 'Fill trait tags from the first column.'
        if self.trait_3 and not self.trait_2:
            errors['trait_3'] = 'Fill trait tags from the first column.'


class PlayerRatingProfile(ReleaseChild):
    player = models.OneToOneField(
        UniversePlayer,
        on_delete=models.CASCADE,
        related_name='rating_profile',
    )

    class Meta:
        app_label = 'universe'
        constraints = _rating_constraints(PLAYER_COMPONENTS, 'player_rating')

    def __str__(self):
        return self.player.universe_key if self.player_id else 'player rating'

    def bound_release(self):
        if not self.player_id:
            return None
        return self.player.release

    def guard_field(self):
        return 'player'


for _component in PLAYER_COMPONENTS:
    PlayerRatingProfile.add_to_class(
        f'initial_{_component}',
        models.PositiveSmallIntegerField(),
    )
    PlayerRatingProfile.add_to_class(
        f'potential_{_component}',
        models.PositiveSmallIntegerField(),
    )


class UniverseCollegeSeason(SeasonStatColumns, ReleaseChild):
    class ClassStanding(models.TextChoices):
        FRESHMAN = 'freshman', 'Freshman'
        SOPHOMORE = 'sophomore', 'Sophomore'
        JUNIOR = 'junior', 'Junior'
        SENIOR = 'senior', 'Senior'

    player = models.ForeignKey(
        UniversePlayer,
        on_delete=models.CASCADE,
        related_name='college_seasons',
    )
    season_year = models.PositiveSmallIntegerField()
    class_standing = models.CharField(max_length=16, choices=ClassStanding.choices)

    class Meta:
        app_label = 'universe'
        ordering = ['player', 'season_year']
        constraints = [
            models.UniqueConstraint(
                fields=['player', 'season_year'],
                name='unique_college_season_player_year',
            ),
            *_stat_constraints('college_stat'),
        ]
        indexes = [
            models.Index(
                fields=['player', 'season_year'],
                name='universe_college_season_idx',
            ),
        ]

    def __str__(self):
        return f'{self.player_id} {self.season_year}'

    def bound_release(self):
        if not self.player_id:
            return None
        return self.player.release

    def guard_field(self):
        return 'player'

    def extra_clean(self, errors):
        if not self.player_id:
            return
        anchor = self.player.first_pro_year or self.player.origin_draft_year
        if anchor is None:
            return
        if self.season_year < anchor - 4 or self.season_year > anchor - 1:
            errors['season_year'] = (
                'A college season falls in the four years before the player\'s anchor.'
            )


class UniverseProStint(ReleaseChild):
    class Role(models.TextChoices):
        STARTER = 'starter', 'Starter'
        BACKUP = 'backup', 'Backup'
        TAXI = 'taxi', 'Taxi'

    player = models.ForeignKey(
        UniversePlayer,
        on_delete=models.CASCADE,
        related_name='pro_stints',
    )
    franchise = models.ForeignKey(
        UniverseFranchise,
        on_delete=models.PROTECT,
        related_name='pro_stints',
    )
    start_year = models.PositiveSmallIntegerField()
    end_year = models.PositiveSmallIntegerField()
    role = models.CharField(max_length=16, choices=Role.choices)

    class Meta:
        app_label = 'universe'
        ordering = ['player', 'start_year']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_year__gte=models.F('start_year')),
                name='universe_pro_stint_span',
            ),
        ]
        indexes = [
            models.Index(
                fields=['player', 'start_year'],
                name='universe_pro_stint_player_idx',
            ),
            models.Index(
                fields=['franchise', 'start_year'],
                name='universe_prostint_fr_idx',
            ),
        ]

    def __str__(self):
        return f'{self.player_id} {self.start_year}'

    def bound_release(self):
        if not self.player_id:
            return None
        return self.player.release

    def guard_field(self):
        return 'player'

    def extra_clean(self, errors):
        if not self.player_id or not self.franchise_id:
            return
        player = self.player
        if player.cohort == UniversePlayer.Cohort.DRAFT:
            errors['player'] = 'Draft prospects have no shared professional stint.'
        if player.release_id != self.franchise.release_id:
            errors['franchise'] = 'The franchise must belong to the player\'s release.'
        if player.release_id and (
            not _in_archive(player.release, self.start_year)
            or not _in_archive(player.release, self.end_year)
        ):
            errors['start_year'] = (
                'A shared professional stint stays inside the archive window.'
            )


class UniverseProSeason(SeasonStatColumns, ReleaseChild):
    player = models.ForeignKey(
        UniversePlayer,
        on_delete=models.CASCADE,
        related_name='pro_seasons',
    )
    franchise = models.ForeignKey(
        UniverseFranchise,
        on_delete=models.PROTECT,
        related_name='pro_seasons',
    )
    season_year = models.PositiveSmallIntegerField()

    class Meta:
        app_label = 'universe'
        ordering = ['player', 'season_year']
        constraints = [
            models.UniqueConstraint(
                fields=['player', 'season_year', 'franchise'],
                name='unique_pro_season_player_year',
            ),
            *_stat_constraints('pro_stat'),
        ]
        indexes = [
            models.Index(
                fields=['franchise', 'season_year'],
                name='universe_proseason_fr_idx',
            ),
            models.Index(
                fields=['player', 'season_year'],
                name='universe_pro_season_player_idx',
            ),
        ]

    def __str__(self):
        return f'{self.player_id} {self.season_year}'

    def bound_release(self):
        if not self.player_id:
            return None
        return self.player.release

    def guard_field(self):
        return 'player'

    def extra_clean(self, errors):
        if not self.player_id or not self.franchise_id:
            return
        player = self.player
        if player.cohort == UniversePlayer.Cohort.DRAFT:
            errors['player'] = 'Draft prospects have no shared professional season.'
        if player.release_id != self.franchise.release_id:
            errors['franchise'] = 'The franchise must belong to the player\'s release.'
        if player.release_id and not _in_archive(player.release, self.season_year):
            errors['season_year'] = (
                'A shared professional season stays inside the archive window.'
            )


class UniverseGM(ReleaseChild):
    class FormerRole(models.TextChoices):
        PLAYER = 'player', 'Player'
        SCOUT = 'scout', 'Scout'
        COLLEGE_COACH = 'college_coach', 'College coach'
        PERSONNEL = 'personnel', 'Personnel'
        CAP_ANALYST = 'cap_analyst', 'Cap analyst'

    release = models.ForeignKey(
        UniverseRelease,
        on_delete=models.CASCADE,
        related_name='gms',
    )
    universe_key = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    birth_year = models.PositiveSmallIntegerField()
    debut_year = models.PositiveSmallIntegerField()
    former_role = models.CharField(max_length=32, choices=FormerRole.choices)
    positional_priority_1 = models.CharField(max_length=32, choices=_choices(GM_PRIORITIES))
    positional_priority_2 = models.CharField(
        max_length=32,
        choices=_choices(GM_PRIORITIES),
        null=True,
        blank=True,
    )
    strength_1 = models.CharField(max_length=32, choices=_choices(GM_TAGS))
    strength_2 = models.CharField(max_length=32, choices=_choices(GM_TAGS))
    weakness = models.CharField(max_length=32, choices=_choices(GM_TAGS))
    trait_1 = models.CharField(max_length=32, choices=_choices(GM_TRAITS))
    trait_2 = models.CharField(max_length=32, choices=_choices(GM_TRAITS))

    class Meta:
        app_label = 'universe'
        ordering = ['universe_key']
        indexes = [
            models.Index(
                fields=['release', 'debut_year'],
                name='universe_gm_debut_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(strength_1=models.F('strength_2')),
                name='universe_gm_strengths_differ',
            ),
            models.CheckConstraint(
                condition=~models.Q(trait_1=models.F('trait_2')),
                name='universe_gm_traits_differ',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(weakness=models.F('strength_1'))
                    & ~models.Q(weakness=models.F('strength_2'))
                ),
                name='universe_gm_weakness_distinct',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(positional_priority_2__isnull=True)
                    | ~models.Q(positional_priority_1=models.F('positional_priority_2'))
                ),
                name='universe_gm_priorities_differ',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(debut_year__gte=models.F('birth_year') + 20)
                    & models.Q(debut_year__lte=models.F('birth_year') + 80)
                ),
                name='universe_gm_debut_age',
            ),
            models.CheckConstraint(
                condition=~models.Q(universe_key='') & ~models.Q(name=''),
                name='universe_gm_identity_nonempty',
            ),
        ]

    def __str__(self):
        return self.universe_key

    def bound_release(self):
        if not self.release_id:
            return None
        return self.release

    def extra_clean(self, errors):
        if not self.release_id:
            return
        release = self.release
        if (
            self.debut_year < release.first_season_year
            or self.debut_year > release.last_season_year
        ):
            errors['debut_year'] = 'The debut must fall inside the release.'


class GMRatingProfile(ReleaseChild):
    gm = models.OneToOneField(
        UniverseGM,
        on_delete=models.CASCADE,
        related_name='rating_profile',
    )
    initial_reputation = models.PositiveSmallIntegerField()

    class Meta:
        app_label = 'universe'
        constraints = _rating_constraints(
            GM_ABILITIES,
            'gm_rating',
            reputation=True,
        )

    def __str__(self):
        return self.gm.universe_key if self.gm_id else 'gm rating'

    def bound_release(self):
        if not self.gm_id:
            return None
        return self.gm.release

    def guard_field(self):
        return 'gm'


for _ability in GM_ABILITIES:
    GMRatingProfile.add_to_class(
        f'initial_{_ability}',
        models.PositiveSmallIntegerField(),
    )
    GMRatingProfile.add_to_class(
        f'potential_{_ability}',
        models.PositiveSmallIntegerField(),
    )


class UniverseCoach(ReleaseChild):
    class Specialty(models.TextChoices):
        OFFENSE = 'offense', 'Offense'
        DEFENSE = 'defense', 'Defense'
        BOTH = 'both', 'Both'

    class FormerRole(models.TextChoices):
        PLAYER = 'player', 'Player'
        COLLEGE_COACH = 'college_coach', 'College coach'
        COORDINATOR = 'coordinator', 'Coordinator'
        POSITION_COACH = 'position_coach', 'Position coach'

    release = models.ForeignKey(
        UniverseRelease,
        on_delete=models.CASCADE,
        related_name='coaches',
    )
    universe_key = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=255)
    birth_year = models.PositiveSmallIntegerField()
    debut_year = models.PositiveSmallIntegerField()
    specialty = models.CharField(max_length=16, choices=Specialty.choices)
    former_role = models.CharField(max_length=32, choices=FormerRole.choices)
    preferred_offense = models.CharField(max_length=32, choices=_choices(OFFENSE_SCHEMES))
    preferred_defense = models.CharField(max_length=32, choices=_choices(DEFENSE_SCHEMES))
    strength_1 = models.CharField(max_length=32, choices=_choices(COACH_TAGS))
    strength_2 = models.CharField(max_length=32, choices=_choices(COACH_TAGS))
    weakness = models.CharField(max_length=32, choices=_choices(COACH_TAGS))
    trait_1 = models.CharField(max_length=32, choices=_choices(COACH_TRAITS))
    trait_2 = models.CharField(max_length=32, choices=_choices(COACH_TRAITS))

    class Meta:
        app_label = 'universe'
        ordering = ['universe_key']
        indexes = [
            models.Index(
                fields=['release', 'debut_year', 'specialty'],
                name='universe_coach_debut_idx',
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(specialty__in=['offense', 'defense', 'both']),
                name='universe_coach_specialty',
            ),
            models.CheckConstraint(
                condition=~models.Q(strength_1=models.F('strength_2')),
                name='universe_coach_strengths_differ',
            ),
            models.CheckConstraint(
                condition=~models.Q(trait_1=models.F('trait_2')),
                name='universe_coach_traits_differ',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(weakness=models.F('strength_1'))
                    & ~models.Q(weakness=models.F('strength_2'))
                ),
                name='universe_coach_weakness_distinct',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(preferred_offense='west_coast')
                    | models.Q(debut_year__gte=1987)
                ),
                name='universe_coach_west_coast',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(preferred_offense='air_raid')
                    | models.Q(debut_year__gte=2004)
                ),
                name='universe_coach_air_raid',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(debut_year__gte=models.F('birth_year') + 20)
                    & models.Q(debut_year__lte=models.F('birth_year') + 80)
                ),
                name='universe_coach_debut_age',
            ),
            models.CheckConstraint(
                condition=~models.Q(universe_key='') & ~models.Q(name=''),
                name='universe_coach_identity_nonempty',
            ),
        ]

    def __str__(self):
        return self.universe_key

    def bound_release(self):
        if not self.release_id:
            return None
        return self.release

    def extra_clean(self, errors):
        if not self.release_id:
            return
        release = self.release
        if (
            self.debut_year < release.first_season_year
            or self.debut_year > release.last_season_year
        ):
            errors['debut_year'] = 'The debut must fall inside the release.'


class CoachRatingProfile(ReleaseChild):
    coach = models.OneToOneField(
        UniverseCoach,
        on_delete=models.CASCADE,
        related_name='rating_profile',
    )
    initial_reputation = models.PositiveSmallIntegerField()

    class Meta:
        app_label = 'universe'
        constraints = _rating_constraints(
            COACH_ABILITIES,
            'coach_rating',
            reputation=True,
        )

    def __str__(self):
        return self.coach.universe_key if self.coach_id else 'coach rating'

    def bound_release(self):
        if not self.coach_id:
            return None
        return self.coach.release

    def guard_field(self):
        return 'coach'


for _ability in COACH_ABILITIES:
    CoachRatingProfile.add_to_class(
        f'initial_{_ability}',
        models.PositiveSmallIntegerField(),
    )
    CoachRatingProfile.add_to_class(
        f'potential_{_ability}',
        models.PositiveSmallIntegerField(),
    )


class UniverseStaffPost(ReleaseChild):
    class Role(models.TextChoices):
        GM = 'gm', 'GM'
        PERSONNEL_DIRECTOR = 'personnel_director', 'Personnel director'
        SCOUT = 'scout', 'Scout'
        COLLEGE_COACH = 'college_coach', 'College coach'
        HEAD_COACH = 'head_coach', 'Head coach'
        OFFENSIVE_COORDINATOR = 'offensive_coordinator', 'Offensive coordinator'
        DEFENSIVE_COORDINATOR = 'defensive_coordinator', 'Defensive coordinator'
        POSITION_COACH = 'position_coach', 'Position coach'
        COLLEGE_HEAD_COACH = 'college_head_coach', 'College head coach'

    class EndedBy(models.TextChoices):
        RESIGNED = 'resigned', 'Resigned'
        FIRED = 'fired', 'Fired'
        PROMOTED = 'promoted', 'Promoted'
        RETIRED = 'retired', 'Retired'
        POST_COMPLETED = 'post_completed', 'Post completed'

    gm = models.ForeignKey(
        UniverseGM,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='staff_posts',
    )
    coach = models.ForeignKey(
        UniverseCoach,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='staff_posts',
    )
    franchise = models.ForeignKey(
        UniverseFranchise,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='staff_posts',
    )
    college = models.ForeignKey(
        College,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='staff_posts',
    )
    organization_name = models.CharField(max_length=255, blank=True, default='')
    role = models.CharField(max_length=32, choices=Role.choices)
    start_year = models.PositiveSmallIntegerField()
    end_year = models.PositiveSmallIntegerField()
    wins = models.PositiveSmallIntegerField(default=0)
    losses = models.PositiveSmallIntegerField(default=0)
    ties = models.PositiveSmallIntegerField(default=0)
    playoff_appearances = models.PositiveSmallIntegerField(default=0)
    championships = models.PositiveSmallIntegerField(default=0)
    ended_by = models.CharField(max_length=32, choices=EndedBy.choices)

    class Meta:
        app_label = 'universe'
        ordering = ['start_year', 'role']
        constraints = [
            models.CheckConstraint(
                condition=(
                    (
                        models.Q(gm__isnull=False, coach__isnull=True)
                    )
                    | (
                        models.Q(gm__isnull=True, coach__isnull=False)
                    )
                ),
                name='universe_staff_post_one_person',
            ),
            models.CheckConstraint(
                condition=models.Q(end_year__gte=models.F('start_year')),
                name='universe_staff_post_span',
            ),
            models.CheckConstraint(
                condition=models.Q(
                    championships__lte=models.F('playoff_appearances'),
                ),
                name='universe_staff_post_titles',
            ),
            models.CheckConstraint(
                condition=models.Q(role__in=STAFF_POST_ROLES),
                name='universe_staff_post_role',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        franchise__isnull=False,
                        college__isnull=True,
                        organization_name='',
                    )
                    | models.Q(
                        franchise__isnull=True,
                        college__isnull=False,
                        organization_name='',
                    )
                    | (
                        models.Q(franchise__isnull=True, college__isnull=True)
                        & ~models.Q(organization_name='')
                    )
                ),
                name='universe_staff_post_employer',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(role__in=COLLEGE_POST_ROLES)
                    | models.Q(franchise__isnull=True)
                ),
                name='universe_staff_post_college_role',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(role__in=PRO_CHAIR_ROLES)
                    | models.Q(college__isnull=True)
                ),
                name='universe_staff_post_pro_chair',
            ),
        ]
        indexes = [
            models.Index(
                fields=['gm', 'start_year'],
                name='universe_staff_post_gm_idx',
            ),
            models.Index(
                fields=['coach', 'start_year'],
                name='universe_staff_post_coach_idx',
            ),
            models.Index(
                fields=['franchise', 'start_year'],
                name='universe_staffpost_fr_idx',
            ),
            models.Index(
                fields=['college', 'start_year'],
                name='universe_staffpost_col_idx',
            ),
        ]

    def __str__(self):
        return f'{self.role} {self.start_year}'

    def bound_release(self):
        if self.gm_id:
            return self.gm.release
        if self.coach_id:
            return self.coach.release
        return None

    def guard_field(self):
        if self.coach_id and not self.gm_id:
            return 'coach'
        return 'gm'

    def extra_clean(self, errors):
        if self.organization_name:
            self.organization_name = self.organization_name.strip()
        release = self.bound_release()
        if release is None:
            return
        person = self.gm if self.gm_id else self.coach if self.coach_id else None
        if person is not None and self.end_year > person.debut_year - 1:
            errors['end_year'] = (
                'A shared staff post ends no later than the year before debut.'
            )
        if self.franchise_id and self.franchise.release_id != release.id:
            errors['franchise'] = 'The franchise must belong to the same release.'
        elif (
            self.franchise_id
            and self.end_year >= release.first_season_year
            and 'end_year' not in errors
        ):
            errors['end_year'] = (
                'A Federation franchise post ends before the first playable season.'
            )
        if self.college_id and self.college.release_id != release.id:
            errors['college'] = 'The college must belong to the same release.'
        employers = (
            bool(self.franchise_id),
            bool(self.college_id),
            bool(self.organization_name),
        )
        if sum(employers) != 1:
            errors['organization_name'] = (
                'A staff post names exactly one employer: a franchise, a college, '
                'or an external organization.'
            )
        if self.role in COLLEGE_POST_ROLES and self.franchise_id:
            errors['franchise'] = 'A college post does not use a Federation franchise.'
        if self.role in PRO_CHAIR_ROLES and self.college_id:
            errors['college'] = (
                'That pro chair names a franchise or an external organization.'
            )
        if self.gm_id and self.role not in GM_POST_ROLES:
            errors['role'] = 'That role belongs on a coach post.'
        if self.coach_id and self.role not in COACH_POST_ROLES:
            errors['role'] = 'That role belongs on a GM post.'
        if self.coach_id:
            specialty = self.coach.specialty
            if self.role == 'offensive_coordinator' and specialty not in (
                'offense',
                'both',
            ):
                errors['role'] = (
                    'An offensive coordinator post requires specialty offense or both.'
                )
            if self.role == 'defensive_coordinator' and specialty not in (
                'defense',
                'both',
            ):
                errors['role'] = (
                    'A defensive coordinator post requires specialty defense or both.'
                )


class UniverseAward(ReleaseChild):
    class Kind(models.TextChoices):
        MVP = 'mvp', 'MVP'
        OPOY = 'opoy', 'OPOY'
        DPOY = 'dpoy', 'DPOY'
        OROY = 'oroy', 'OROY'
        DROY = 'droy', 'DROY'
        ALL_LEAGUE_1 = 'all_league_1', 'All-league first'
        ALL_LEAGUE_2 = 'all_league_2', 'All-league second'
        ALL_STAR = 'all_star', 'All-star'
        COACH_OF_YEAR = 'coach_of_year', 'Coach of the year'
        EXECUTIVE_OF_YEAR = 'executive_of_year', 'Executive of the year'

    release = models.ForeignKey(
        UniverseRelease,
        on_delete=models.CASCADE,
        related_name='awards',
    )
    franchise = models.ForeignKey(
        UniverseFranchise,
        on_delete=models.PROTECT,
        related_name='awards',
    )
    player = models.ForeignKey(
        UniversePlayer,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='awards',
    )
    gm = models.ForeignKey(
        UniverseGM,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='awards',
    )
    coach = models.ForeignKey(
        UniverseCoach,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='awards',
    )
    season_year = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=32, choices=Kind.choices)
    slot = models.CharField(
        max_length=4,
        blank=True,
        default='',
        choices=[('', '')] + _choices(POSITIONS),
    )

    class Meta:
        app_label = 'universe'
        ordering = ['season_year', 'kind', 'slot']
        constraints = [
            models.UniqueConstraint(
                fields=['release', 'season_year', 'kind', 'slot'],
                name='unique_award_per_release_year',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        player__isnull=False,
                        gm__isnull=True,
                        coach__isnull=True,
                    )
                    | models.Q(
                        player__isnull=True,
                        gm__isnull=False,
                        coach__isnull=True,
                    )
                    | models.Q(
                        player__isnull=True,
                        gm__isnull=True,
                        coach__isnull=False,
                    )
                ),
                name='universe_award_one_recipient',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(kind__in=ALL_LEAGUE_KINDS, slot__in=POSITIONS)
                    | (
                        ~models.Q(kind__in=ALL_LEAGUE_KINDS)
                        & models.Q(slot='')
                    )
                ),
                name='universe_award_slot',
            ),
            models.CheckConstraint(
                condition=(
                    (
                        models.Q(kind='coach_of_year')
                        & models.Q(coach__isnull=False)
                    )
                    | (
                        models.Q(kind='executive_of_year')
                        & models.Q(gm__isnull=False)
                    )
                    | (
                        models.Q(kind__in=PLAYER_AWARDS)
                        & models.Q(player__isnull=False)
                    )
                ),
                name='universe_award_kind_recipient',
            ),
        ]
        indexes = [
            models.Index(
                fields=['season_year', 'kind'],
                name='universe_award_year_idx',
            ),
        ]

    def __str__(self):
        return f'{self.season_year} {self.kind}'

    def bound_release(self):
        if not self.release_id:
            return None
        return self.release

    def extra_clean(self, errors):
        if not self.release_id or not self.franchise_id:
            return
        if self.franchise.release_id != self.release_id:
            errors['franchise'] = 'The franchise must belong to this award\'s release.'
        if not _in_archive(self.release, self.season_year):
            errors['season_year'] = (
                'A shared award stays inside the archive window.'
            )
        if self.player_id:
            if self.player.release_id != self.release_id:
                errors['player'] = 'The player must belong to this award\'s release.'
            elif self.player.cohort == UniversePlayer.Cohort.DRAFT:
                errors['player'] = 'Draft prospects have no shared award.'
        if self.gm_id and self.gm.release_id != self.release_id:
            errors['gm'] = 'The GM must belong to this award\'s release.'
        if self.coach_id and self.coach.release_id != self.release_id:
            errors['coach'] = 'The coach must belong to this award\'s release.'
