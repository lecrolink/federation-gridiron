from django.contrib import messages
from django.shortcuts import render

from leagues.forms import Generate32TeamLeagueForm
from leagues.services.league_generator import generate_league_structure


def generate_32_team_league_view(model_admin, request):
    if request.method == 'POST':
        form = Generate32TeamLeagueForm(request.POST)
        if form.is_valid():
            league = form.cleaned_data['league']
            result = generate_league_structure(
                league=league,
                conference_names=form.cleaned_data['conference_names'],
                division_names=form.cleaned_data['division_names'],
                team_names=form.cleaned_data['team_names'],
            )
            messages.success(
                request,
                f'32-team structure saved for {league.name}: '
                f'{result.conferences_created} conferences created, '
                f'{result.conferences_updated} updated; '
                f'{result.divisions_created} divisions created, '
                f'{result.divisions_updated} updated; '
                f'{result.teams_created} teams created, '
                f'{result.teams_updated} updated.',
            )
            context = {
                **model_admin.admin_site.each_context(request),
                'title': 'Generate 32-Team League',
                'form': form,
                'result': result,
                'league': league,
                'opts': model_admin.model._meta,
                'has_view_permission': model_admin.has_view_permission(request),
            }
            return render(
                request,
                'admin/leagues/league/generate_32_team_league.html',
                context,
            )
    else:
        initial = {}
        league_id = request.GET.get('league')
        if league_id:
            initial['league'] = league_id
        form = Generate32TeamLeagueForm(initial=initial)

    context = {
        **model_admin.admin_site.each_context(request),
        'title': 'Generate 32-Team League',
        'form': form,
        'result': None,
        'league': None,
        'opts': model_admin.model._meta,
        'has_view_permission': model_admin.has_view_permission(request),
    }
    return render(
        request,
        'admin/leagues/league/generate_32_team_league.html',
        context,
    )
