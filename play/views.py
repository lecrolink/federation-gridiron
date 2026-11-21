from django.shortcuts import render

from play.campaign import campaign_for_user


def home(request):
    return render(request, 'play/home.html', {
        'campaign': campaign_for_user(request.user),
    })
