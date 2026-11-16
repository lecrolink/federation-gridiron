from django.urls import path

from setupflow import views

app_name = 'setupflow'

urlpatterns = [
    path('', views.landing, name='landing'),
    path('franchises/', views.franchises, name='franchises'),
    path('franchises/<slug:slug>/', views.overview, name='overview'),
    path('franchises/<slug:slug>/staff/', views.staff, name='staff'),
    path('franchises/<slug:slug>/gm/', views.gm, name='gm'),
    path('franchises/<slug:slug>/coach/', views.coach, name='coach'),
    path('franchises/<slug:slug>/review/', views.review, name='review'),
    path('reset/', views.reset, name='reset'),
]
