from django.urls import path

from . import player_views, views


app_name = 'api-v1'

urlpatterns = [
    path('', views.api_index, name='index'),
    path('session/', views.current_session, name='session'),
    path('dashboard/', player_views.dashboard, name='dashboard'),
    path('seasons/', player_views.seasons, name='seasons'),
    path('archive/', player_views.archive, name='archive'),
    path('rounds/<int:pk>/', player_views.round_detail, name='round-detail'),
    path('rounds/<int:pk>/ballot/', player_views.ballot, name='ballot'),
    path('rounds/<int:pk>/results/', player_views.results, name='results'),
    path('leaderboard/', player_views.leaderboard, name='leaderboard'),
    path('profiles/<str:username>/', player_views.profile, name='profile'),
    path(
        'profiles/<str:username>/achievements/',
        player_views.achievements,
        name='achievements',
    ),
]
