from django.contrib import admin

from .models import Conference, Division, League, MovementPath, Team


@admin.register(League)
class LeagueAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'tier', 'parent_league', 'active', 'updated_at')
    search_fields = ('name', 'slug', 'description')
    list_filter = ('active', 'tier')
    ordering = ('tier', 'name')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Conference)
class ConferenceAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'league', 'sort_order', 'active', 'updated_at')
    search_fields = ('name', 'slug', 'league__name')
    list_filter = ('active', 'league')
    ordering = ('league', 'sort_order', 'name')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Division)
class DivisionAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'conference', 'sort_order', 'active', 'updated_at')
    search_fields = ('name', 'slug', 'conference__name', 'conference__league__name')
    list_filter = ('active', 'conference__league', 'conference')
    ordering = ('conference', 'sort_order', 'name')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = (
        'name',
        'slug',
        'league',
        'conference',
        'division',
        'city',
        'country',
        'sort_order',
        'active',
        'updated_at',
    )
    search_fields = ('name', 'slug', 'city', 'region', 'country', 'league__name')
    list_filter = ('active', 'league', 'conference', 'division', 'country')
    ordering = ('league', 'sort_order', 'name')
    prepopulated_fields = {'slug': ('name',)}


@admin.register(MovementPath)
class MovementPathAdmin(admin.ModelAdmin):
    list_display = (
        'from_league',
        'from_team',
        'to_league',
        'to_team',
        'movement_type',
        'active',
        'updated_at',
    )
    search_fields = (
        'from_league__name',
        'to_league__name',
        'from_team__name',
        'to_team__name',
        'notes',
    )
    list_filter = ('active', 'movement_type', 'from_league', 'to_league')
    ordering = ('from_league', 'to_league', 'movement_type')
