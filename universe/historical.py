"""Immutable historical schedule templates.

These rows are reference data. They do not belong to a ``UniverseRelease``
or a ``Timeline``. A provenance club label is not a ``UniverseFranchise``
identity or display name. Instance ``save`` and ``delete``, and queryset
``update`` and ``delete``, refuse to change a stored row. ``bulk_create``
is the loader's insert path. Direct SQL bypasses these checks. This module
adds no database trigger.
"""

from django.core.exceptions import ValidationError
from django.db import models


class HistoricalScheduleQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError('A historical schedule template row is written once.')

    def delete(self):
        raise ValidationError('A historical schedule template row cannot be deleted.')


class HistoricalScheduleManager(models.Manager):
    def get_queryset(self):
        return HistoricalScheduleQuerySet(self.model, using=self._db)


class _ImmutableTemplate(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('A historical schedule template row is written once.')
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('A historical schedule template row cannot be deleted.')


class HistoricalScheduleSlot(_ImmutableTemplate):
    """One numbered historical schedule slot for one season.

    ``provenance_club`` cites the source club that occupied the slot. It is
    not a Federation franchise. ``slot_number`` is a neutral label inside
    the source division. ``opening_basis`` says whether the slot is an
    opening placement or, in a later season, a prior-place or expansion slot.
    """

    class ConferenceRole(models.TextChoices):
        AFC = 'afc', 'afc'
        NFC = 'nfc', 'nfc'

    class DivisionRole(models.TextChoices):
        EAST = 'east', 'east'
        CENTRAL = 'central', 'central'
        WEST = 'west', 'west'

    class OpeningBasis(models.TextChoices):
        MERGER_PLACEMENT = 'merger-placement', 'merger-placement'
        PRIOR_PLACE = 'prior-place', 'prior-place'
        EXPANSION = 'expansion', 'expansion'

    season_year = models.PositiveSmallIntegerField()
    conference_role = models.CharField(max_length=16, choices=ConferenceRole.choices)
    division_role = models.CharField(max_length=16, choices=DivisionRole.choices)
    slot_number = models.PositiveSmallIntegerField()
    provenance_club = models.CharField(max_length=64)
    opening_basis = models.CharField(max_length=32, choices=OpeningBasis.choices)
    prior_place = models.PositiveSmallIntegerField(null=True, blank=True)
    prior_conference_role = models.CharField(
        max_length=16,
        choices=ConferenceRole.choices,
        null=True,
        blank=True,
    )
    prior_division_role = models.CharField(
        max_length=16,
        choices=DivisionRole.choices,
        null=True,
        blank=True,
    )
    source_citation = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    objects = HistoricalScheduleManager()

    class Meta:
        ordering = ['season_year', 'id']
        indexes = [
            models.Index(fields=['season_year'], name='historical_slot_season_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['season_year', 'conference_role', 'division_role', 'slot_number'],
                name='unique_historical_slot_position',
            ),
            models.UniqueConstraint(
                fields=['season_year', 'provenance_club'],
                name='unique_historical_slot_provenance',
            ),
            models.CheckConstraint(
                condition=models.Q(season_year__gte=1970, season_year__lte=2029),
                name='historical_slot_season',
            ),
            models.CheckConstraint(
                condition=models.Q(slot_number__gte=1),
                name='historical_slot_number',
            ),
            models.CheckConstraint(
                condition=models.Q(conference_role__in=['afc', 'nfc']),
                name='historical_slot_conference',
            ),
            models.CheckConstraint(
                condition=models.Q(division_role__in=['east', 'central', 'west']),
                name='historical_slot_division',
            ),
            models.CheckConstraint(
                condition=models.Q(opening_basis__in=[
                    'merger-placement',
                    'prior-place',
                    'expansion',
                ]),
                name='historical_slot_basis',
            ),
            models.CheckConstraint(
                condition=~models.Q(provenance_club=''),
                name='historical_slot_provenance',
            ),
            models.CheckConstraint(
                condition=~models.Q(source_citation=''),
                name='historical_slot_citation',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(prior_place__isnull=True)
                    | models.Q(prior_place__gte=1, prior_place__lte=6)
                ),
                name='historical_slot_prior_place',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(prior_conference_role__isnull=True)
                    | models.Q(prior_conference_role__in=['afc', 'nfc'])
                ),
                name='historical_slot_prior_conference',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(prior_division_role__isnull=True)
                    | models.Q(prior_division_role__in=['east', 'central', 'west'])
                ),
                name='historical_slot_prior_division',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(opening_basis='merger-placement')
                    | (
                        models.Q(prior_place__isnull=True)
                        & models.Q(prior_conference_role__isnull=True)
                        & models.Q(prior_division_role__isnull=True)
                    )
                ),
                name='historical_slot_merger_basis',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(opening_basis='expansion')
                    | (
                        models.Q(prior_place__isnull=True)
                        & models.Q(prior_conference_role__isnull=True)
                        & models.Q(prior_division_role__isnull=True)
                    )
                ),
                name='historical_slot_expansion_basis',
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(opening_basis='prior-place')
                    | (
                        models.Q(prior_place__isnull=False)
                        & models.Q(prior_conference_role__isnull=False)
                        & models.Q(prior_division_role__isnull=False)
                    )
                ),
                name='historical_slot_prior_basis',
            ),
        ]

    def __str__(self):
        return (
            f'{self.season_year} {self.conference_role} '
            f'{self.division_role} {self.slot_number}'
        )

    def clean(self):
        super().clean()
        errors = {}
        if self.season_year is None or not 1970 <= self.season_year <= 2029:
            errors['season_year'] = 'A historical template season is from 1970 through 2029.'
        if self.slot_number is None or self.slot_number < 1:
            errors['slot_number'] = 'The slot number starts at 1.'
        if not self.provenance_club:
            errors['provenance_club'] = 'The provenance club is required.'
        if not self.source_citation:
            errors['source_citation'] = 'The source citation is required.'
        if self.conference_role not in self.ConferenceRole.values:
            errors['conference_role'] = 'The conference role is afc or nfc.'
        if self.division_role not in self.DivisionRole.values:
            errors['division_role'] = 'The division role is east, central, or west.'
        if self.opening_basis not in self.OpeningBasis.values:
            errors['opening_basis'] = 'The opening basis is not a known template basis.'
        _prior_fields(self, errors)
        if self.opening_basis in {
            self.OpeningBasis.MERGER_PLACEMENT,
            self.OpeningBasis.EXPANSION,
        }:
            if (
                self.prior_place is not None
                or self.prior_conference_role
                or self.prior_division_role
            ):
                errors['opening_basis'] = (
                    'Merger placement and expansion slots have no prior place.'
                )
        elif self.opening_basis == self.OpeningBasis.PRIOR_PLACE:
            if (
                self.prior_place is None
                or not self.prior_conference_role
                or not self.prior_division_role
            ):
                errors['prior_place'] = (
                    'A prior-place slot records the place and the prior division.'
                )
        if errors:
            raise ValidationError(errors)


class HistoricalScheduleGame(_ImmutableTemplate):
    """One historical regular-season game between two template slots.

    ``week_played`` is the week the game was played. A postponed game can
    also record ``originally_scheduled_week``. Postseason games are not
    template games. The row stores no score and no Federation franchise.
    """

    class Status(models.TextChoices):
        PLAYED = 'played', 'played'
        POSTPONED = 'postponed', 'postponed'
        REPLACEMENT = 'replacement', 'replacement'
        CANCELLED = 'cancelled', 'cancelled'

    season_year = models.PositiveSmallIntegerField()
    home_slot = models.ForeignKey(
        HistoricalScheduleSlot,
        on_delete=models.PROTECT,
        related_name='home_games',
    )
    away_slot = models.ForeignKey(
        HistoricalScheduleSlot,
        on_delete=models.PROTECT,
        related_name='away_games',
    )
    week_played = models.PositiveSmallIntegerField()
    originally_scheduled_week = models.PositiveSmallIntegerField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices)
    source_citation = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    objects = HistoricalScheduleManager()

    class Meta:
        ordering = ['season_year', 'week_played', 'id']
        indexes = [
            models.Index(
                fields=['season_year', 'week_played'],
                name='historical_game_week_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['home_slot', 'away_slot'],
                name='unique_historical_game_directed',
            ),
            models.CheckConstraint(
                condition=~models.Q(home_slot=models.F('away_slot')),
                name='historical_game_distinct_slots',
            ),
            models.CheckConstraint(
                condition=models.Q(season_year__gte=1970, season_year__lte=2029),
                name='historical_game_season',
            ),
            models.CheckConstraint(
                condition=models.Q(week_played__gte=1, week_played__lte=18),
                name='historical_game_week',
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(originally_scheduled_week__isnull=True)
                    | models.Q(
                        originally_scheduled_week__gte=1,
                        originally_scheduled_week__lte=18,
                    )
                ),
                name='historical_game_original_week',
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=[
                    'played',
                    'postponed',
                    'replacement',
                    'cancelled',
                ]),
                name='historical_game_status',
            ),
            models.CheckConstraint(
                condition=~models.Q(source_citation=''),
                name='historical_game_citation',
            ),
        ]

    def __str__(self):
        return f'{self.season_year} week {self.week_played}'

    def clean(self):
        super().clean()
        errors = {}
        if self.season_year is None or not 1970 <= self.season_year <= 2029:
            errors['season_year'] = 'A historical template season is from 1970 through 2029.'
        if self.week_played is None or not 1 <= self.week_played <= 18:
            errors['week_played'] = 'The played week is from 1 through 18.'
        if (
            self.originally_scheduled_week is not None
            and not 1 <= self.originally_scheduled_week <= 18
        ):
            errors['originally_scheduled_week'] = (
                'The originally scheduled week is from 1 through 18.'
            )
        if self.status not in self.Status.values:
            errors['status'] = 'The game status is not a known template status.'
        if not self.source_citation:
            errors['source_citation'] = 'The source citation is required.'
        home = self.home_slot if self.home_slot_id else None
        away = self.away_slot if self.away_slot_id else None
        if home is not None and away is not None:
            if home.pk == away.pk:
                errors['away_slot'] = 'A slot does not play itself.'
            if home.season_year != self.season_year or away.season_year != self.season_year:
                errors['season_year'] = 'Both slots belong to this game\'s season.'
        if errors:
            raise ValidationError(errors)


class HistoricalScheduleBye(_ImmutableTemplate):
    """One historical bye week for one template slot.

    A cancelled or postponed week is not a bye. 1970 has no bye rows.
    """

    season_year = models.PositiveSmallIntegerField()
    slot = models.ForeignKey(
        HistoricalScheduleSlot,
        on_delete=models.PROTECT,
        related_name='byes',
    )
    week = models.PositiveSmallIntegerField()
    source_citation = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    objects = HistoricalScheduleManager()

    class Meta:
        ordering = ['season_year', 'week', 'id']
        indexes = [
            models.Index(
                fields=['season_year', 'week'],
                name='historical_bye_week_idx',
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['season_year', 'slot', 'week'],
                name='unique_historical_bye',
            ),
            models.CheckConstraint(
                condition=models.Q(season_year__gte=1970, season_year__lte=2029),
                name='historical_bye_season',
            ),
            models.CheckConstraint(
                condition=models.Q(week__gte=1, week__lte=18),
                name='historical_bye_week',
            ),
            models.CheckConstraint(
                condition=~models.Q(source_citation=''),
                name='historical_bye_citation',
            ),
        ]

    def __str__(self):
        return f'{self.season_year} slot {self.slot_id} week {self.week}'

    def clean(self):
        super().clean()
        errors = {}
        if self.season_year is None or not 1970 <= self.season_year <= 2029:
            errors['season_year'] = 'A historical template season is from 1970 through 2029.'
        if self.week is None or not 1 <= self.week <= 18:
            errors['week'] = 'The bye week is from 1 through 18.'
        if not self.source_citation:
            errors['source_citation'] = 'The source citation is required.'
        slot = self.slot if self.slot_id else None
        if slot is not None and slot.season_year != self.season_year:
            errors['slot'] = 'The bye belongs to the slot\'s season.'
        if errors:
            raise ValidationError(errors)


def _prior_fields(slot, errors):
    conference = slot.prior_conference_role or None
    division = slot.prior_division_role or None
    if slot.prior_conference_role == '':
        errors['prior_conference_role'] = 'The prior conference is omitted, not blank.'
    if slot.prior_division_role == '':
        errors['prior_division_role'] = 'The prior division is omitted, not blank.'
    if (conference is None) != (division is None):
        errors['prior_conference_role'] = (
            'The prior conference and division are stored together.'
        )
    if conference is not None and conference not in HistoricalScheduleSlot.ConferenceRole.values:
        errors['prior_conference_role'] = 'The prior conference role is afc or nfc.'
    if division is not None and division not in HistoricalScheduleSlot.DivisionRole.values:
        errors['prior_division_role'] = 'The prior division role is east, central, or west.'
    if slot.prior_place is not None and not 1 <= slot.prior_place <= 6:
        errors['prior_place'] = 'A prior place is from 1 through 6.'
