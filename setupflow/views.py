"""Mobile-first setup prototype.

Screens walk from the introduction through a founding franchise, the
keep-or-replace staff choice, and the general manager and head coach
instructions. Choices live in a signed browser cookie. The views do not
create a timeline and do not query the database.
"""

from django.http import HttpResponse
from django.middleware.csrf import get_token
from django.shortcuts import redirect
from django.template import Context
from django.template.loader import get_template
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from setupflow.flow import (
    AVAILABLE_COACHES,
    AVAILABLE_GMS,
    CONFERENCES,
    DIRECTION_NOTES,
    FRANCHISES,
    clear_state,
    instruction_lines,
    options,
    posted_overlay,
    priority_options,
    read_state,
    select_franchise,
    staff_lines,
    validate_coach,
    validate_gm,
    validate_staff,
    write_state,
)


def render_page(request, template_name, context, status=200):
    """Render without request context processors.

    The auth processor would touch the session, and the database session
    backend would then read a session row. A plain context keeps this
    prototype off the database.
    """
    payload = {'csrf_token': get_token(request)}
    payload.update(context)
    template = get_template(template_name)
    html = template.template.render(Context(payload))
    return HttpResponse(html, status=status)


def _steps(current, slug, selected):
    club = slug or selected
    linked = bool(selected) and selected == club

    def club_href(name):
        if not linked:
            return ''
        return reverse(name, kwargs={'slug': club})

    items = (
        ('club', 'Club', reverse('setupflow:franchises') if current != 'landing' else ''),
        ('staff', 'Staff', club_href('setupflow:staff')),
        ('gm', 'GM', club_href('setupflow:gm')),
        ('coach', 'Coach', club_href('setupflow:coach')),
        ('review', 'Review', club_href('setupflow:review')),
    )
    steps = []
    for key, label, href in items:
        is_current = key == current or (key == 'club' and current == 'franchises')
        if is_current:
            href = ''
        steps.append({
            'key': key,
            'label': label,
            'href': href,
            'current': is_current,
        })
    return steps


def _chrome(request, current, slug=''):
    state = read_state(request)
    selected = FRANCHISES.get(state['franchise'])
    return state, selected, _steps(current, slug, state['franchise'])


def _missing(request, slug):
    state, selected, steps = _chrome(request, 'club', slug)
    return render_page(request, 'setupflow/missing.html', {
        'slug': slug,
        'selected': selected,
        'steps': steps,
        'show_steps': True,
    }, status=404)


def _require_selected(state, slug):
    if state['franchise'] != slug or slug not in FRANCHISES:
        return redirect('setupflow:overview', slug=slug)
    return None


@require_http_methods(['GET'])
def landing(request):
    state, selected, steps = _chrome(request, 'landing')
    return render_page(request, 'setupflow/landing.html', {
        'selected': selected,
        'steps': steps,
        'show_steps': False,
    })


@require_http_methods(['GET'])
def franchises(request):
    state, selected, steps = _chrome(request, 'franchises')
    return render_page(request, 'setupflow/franchises.html', {
        'conferences': CONFERENCES,
        'selected': selected,
        'steps': steps,
        'show_steps': True,
    })


@require_http_methods(['GET', 'POST'])
def overview(request, slug):
    franchise = FRANCHISES.get(slug)
    if franchise is None:
        return _missing(request, slug)
    state = read_state(request)
    if request.method == 'POST':
        updated = select_franchise(state, slug)
        response = redirect('setupflow:staff', slug=slug)
        write_state(response, updated, secure=request.is_secure())
        return response
    selected = FRANCHISES.get(state['franchise'])
    return render_page(request, 'setupflow/overview.html', {
        'franchise': franchise,
        'selected': selected,
        'taken': state['franchise'] == slug,
        'steps': _steps('club', slug, state['franchise']),
        'show_steps': True,
    })


@require_http_methods(['GET', 'POST'])
def staff(request, slug):
    franchise = FRANCHISES.get(slug)
    if franchise is None:
        return _missing(request, slug)
    state = read_state(request)
    blocked = _require_selected(state, slug)
    if blocked is not None:
        return blocked
    errors = []
    shown = state
    if request.method == 'POST':
        errors, cleaned = validate_staff(request.POST)
        if not errors:
            updated = dict(state)
            updated.update(cleaned)
            response = redirect('setupflow:gm', slug=slug)
            write_state(response, updated, secure=request.is_secure())
            return response
        shown = posted_overlay(state, request.POST, (
            'gm_choice', 'gm_replacement', 'hc_choice', 'hc_replacement',
        ))
    return render_page(request, 'setupflow/staff.html', {
        'franchise': franchise,
        'selected': franchise,
        'state': shown,
        'errors': errors,
        'gms': AVAILABLE_GMS,
        'coaches': AVAILABLE_COACHES,
        'steps': _steps('staff', slug, state['franchise']),
        'show_steps': True,
    })


@require_http_methods(['GET', 'POST'])
def gm(request, slug):
    franchise = FRANCHISES.get(slug)
    if franchise is None:
        return _missing(request, slug)
    state = read_state(request)
    blocked = _require_selected(state, slug)
    if blocked is not None:
        return blocked
    errors = []
    shown = state
    if request.method == 'POST':
        errors, cleaned = validate_gm(request.POST)
        if not errors:
            updated = dict(state)
            updated.update(cleaned)
            response = redirect('setupflow:coach', slug=slug)
            write_state(response, updated, secure=request.is_secure())
            return response
        shown = posted_overlay(state, request.POST, (
            'team_direction',
            'priority_group_1',
            'priority_group_2',
            'priority_group_3',
            'trade_appetite',
            'veteran_preference',
        ))
    return render_page(request, 'setupflow/gm.html', {
        'franchise': franchise,
        'selected': franchise,
        'state': shown,
        'errors': errors,
        'directions': options('team_direction', DIRECTION_NOTES),
        'priorities': priority_options(),
        'trades': options('trade_appetite'),
        'veterans': options('veteran_preference'),
        'steps': _steps('gm', slug, state['franchise']),
        'show_steps': True,
    })


@require_http_methods(['GET', 'POST'])
def coach(request, slug):
    franchise = FRANCHISES.get(slug)
    if franchise is None:
        return _missing(request, slug)
    state = read_state(request)
    blocked = _require_selected(state, slug)
    if blocked is not None:
        return blocked
    errors = []
    shown = state
    if request.method == 'POST':
        errors, cleaned = validate_coach(request.POST)
        if not errors:
            updated = dict(state)
            updated.update(cleaned)
            response = redirect('setupflow:review', slug=slug)
            write_state(response, updated, secure=request.is_secure())
            return response
        shown = posted_overlay(state, request.POST, (
            'offensive_emphasis',
            'defensive_emphasis',
            'fourth_down',
            'playing_time',
        ))
    return render_page(request, 'setupflow/coach.html', {
        'franchise': franchise,
        'selected': franchise,
        'state': shown,
        'errors': errors,
        'offenses': options('offensive_emphasis'),
        'defenses': options('defensive_emphasis'),
        'fourths': options('fourth_down'),
        'playing_times': options('playing_time'),
        'steps': _steps('coach', slug, state['franchise']),
        'show_steps': True,
    })


@require_http_methods(['GET'])
def review(request, slug):
    franchise = FRANCHISES.get(slug)
    if franchise is None:
        return _missing(request, slug)
    state = read_state(request)
    blocked = _require_selected(state, slug)
    if blocked is not None:
        return blocked
    return render_page(request, 'setupflow/review.html', {
        'franchise': franchise,
        'selected': franchise,
        'staff': staff_lines(state),
        'instructions': instruction_lines(state),
        'steps': _steps('review', slug, state['franchise']),
        'show_steps': True,
    })


@require_http_methods(['POST'])
def reset(request):
    response = redirect('setupflow:landing')
    clear_state(response)
    return response

