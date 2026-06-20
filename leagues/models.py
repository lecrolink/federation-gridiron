from django.db import models


class League(models.Model):
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
    tier = models.PositiveIntegerField()
    parent_league = models.ForeignKey(
        'self',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='child_leagues',
    )
    description = models.TextField(blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['tier', 'name']

    def __str__(self):
        return self.name

    def path_to_root(self):
        """Return leagues from this league up to the top-level parent."""
        path = [self]
        current = self
        while current.parent_league_id is not None:
            current = current.parent_league
            path.append(current)
        return path

    @property
    def root_league(self):
        path = self.path_to_root()
        return path[-1]


class Conference(models.Model):
    league = models.ForeignKey(
        League,
        on_delete=models.CASCADE,
        related_name='conferences',
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['league', 'sort_order', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['league', 'slug'],
                name='unique_conference_slug_per_league',
            ),
        ]

    def __str__(self):
        return f'{self.league.name} — {self.name}'


class Division(models.Model):
    conference = models.ForeignKey(
        Conference,
        on_delete=models.CASCADE,
        related_name='divisions',
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['conference', 'sort_order', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['conference', 'slug'],
                name='unique_division_slug_per_conference',
            ),
        ]

    def __str__(self):
        return f'{self.conference.name} — {self.name}'


class Team(models.Model):
    league = models.ForeignKey(
        League,
        on_delete=models.CASCADE,
        related_name='teams',
    )
    conference = models.ForeignKey(
        Conference,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='teams',
    )
    division = models.ForeignKey(
        Division,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='teams',
    )
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255)
    city = models.CharField(max_length=255, blank=True)
    region = models.CharField(max_length=255, blank=True)
    country = models.CharField(max_length=255, blank=True)
    sort_order = models.PositiveIntegerField(default=0)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['league', 'sort_order', 'name']
        constraints = [
            models.UniqueConstraint(
                fields=['league', 'slug'],
                name='unique_team_slug_per_league',
            ),
        ]

    def __str__(self):
        return self.name


class MovementPath(models.Model):
    class MovementType(models.TextChoices):
        PROMOTION = 'promotion', 'Promotion'
        RELEGATION = 'relegation', 'Relegation'
        LATERAL = 'lateral', 'Lateral'
        SPECIAL = 'special', 'Special'

    from_league = models.ForeignKey(
        League,
        on_delete=models.CASCADE,
        related_name='movement_paths_from',
    )
    from_team = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movement_paths_from',
    )
    to_league = models.ForeignKey(
        League,
        on_delete=models.CASCADE,
        related_name='movement_paths_to',
    )
    to_team = models.ForeignKey(
        Team,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='movement_paths_to',
    )
    movement_type = models.CharField(
        max_length=20,
        choices=MovementType.choices,
    )
    active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['from_league', 'to_league', 'movement_type']

    def __str__(self):
        return f'{self.from_league.name} → {self.to_league.name} ({self.get_movement_type_display()})'
