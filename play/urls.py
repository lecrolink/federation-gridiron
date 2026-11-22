from django.contrib.auth import views as auth_views
from django.urls import path

from play.views import clubs, direction, home

urlpatterns = [
    path('', home, name='home'),
    path('clubs/', clubs, name='clubs'),
    path('direction/', direction, name='direction'),
    path(
        'sign-in/',
        auth_views.LoginView.as_view(
            template_name='play/login.html',
            redirect_authenticated_user=True,
        ),
        name='login',
    ),
    path('sign-out/', auth_views.LogoutView.as_view(), name='logout'),
]
