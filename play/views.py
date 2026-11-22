from django.contrib import messages
from django.shortcuts import redirect, render

from play.campaign import campaign_for_user
from play.direction import direction_page, save_direction


def home(request):
    return render(request, 'play/home.html', {
        'campaign': campaign_for_user(request.user),
    })


def direction(request):
    if request.method == 'POST':
        outcome = save_direction(request.user, request.POST)
        if outcome.saved:
            messages.success(request, 'Direction saved.')
            return redirect('direction')
        page = direction_page(request.user)
        if outcome.form is not None:
            page['form'] = outcome.form
            page['editable'] = True
            return render(request, 'play/direction.html', page)
        return render(request, 'play/direction.html', page, status=403)
    return render(request, 'play/direction.html', direction_page(request.user))
