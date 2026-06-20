from django import forms
from django.contrib import admin
from django.urls import path

from .admin_views import generate_32_team_league_view
from .models import Conference, Division, League, MovementPath, Team


class LeagueAdminForm(forms.ModelForm):
    class Meta:
        model = League
        fields = '__all__'


@admin.register(League)
class LeagueAdmin(admin.ModelAdmin):
    form = LeagueAdminForm
    change_list_template = 'admin/leagues/league/change_list.html'
    list_display = ('name', 'slug', 'tier', 'parent_league', 'active', 'updated_at')
    search_fields = ('name', 'slug', 'description')
    list_filter = ('active', 'tier')
    ordering = ('tier', 'name')
    prepopulated_fields = {'slug': ('name',)}

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                'generate-32-team-league/',
                self.admin_site.admin_view(self.generate_32_team_league_view),
                name='leagues_league_generate_32_team_league',
            ),
        ]
        return custom_urls + urls

    def generate_32_team_league_view(self, request):
        return generate_32_team_league_view(self, request)


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
