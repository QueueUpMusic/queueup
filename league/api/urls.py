from django.urls import path

from . import account_views, player_views, submission_views, views, voting_views


app_name = 'api-v1'

urlpatterns = [
    path('', views.api_index, name='index'),
    path('session/', views.current_session, name='session'),
    path('profile/', account_views.update_profile, name='profile-update'),
    path('profile/picture/', account_views.profile_picture, name='profile-picture'),
    path('onboarding/', account_views.onboarding_state, name='onboarding'),
    path('onboarding/season-welcome/', account_views.acknowledge_season, name='season-welcome'),
    path('onboarding/voting-guide/', account_views.acknowledge_voting_guide, name='voting-guide'),
    path('onboarding/submission-rules/', account_views.accept_submission_rules, name='submission-rules'),
    path('notifications/', account_views.notification_preferences, name='notifications'),
    path('push/subscriptions/', account_views.push_subscriptions, name='push-subscriptions'),
    path('dashboard/', player_views.dashboard, name='dashboard'),
    path('seasons/', player_views.seasons, name='seasons'),
    path('archive/', player_views.archive, name='archive'),
    path('rounds/<int:pk>/', player_views.round_detail, name='round-detail'),
    path('rounds/<int:pk>/ballot/', player_views.ballot, name='ballot'),
    path('rounds/<int:pk>/results/', player_views.results, name='results'),
    path('leaderboard/', player_views.leaderboard, name='leaderboard'),
    path('spotify/search/', submission_views.spotify_search, name='spotify-search'),
    path('rounds/<int:pk>/submission/', submission_views.submission_status, name='submission-status'),
    path('rounds/<int:pk>/submissions/', submission_views.create_submission, name='submission-create'),
    path(
        'rounds/<int:pk>/votes/<int:submission_id>/',
        voting_views.save_vote,
        name='vote-save',
    ),
    path('profiles/<str:username>/', player_views.profile, name='profile'),
    path(
        'profiles/<str:username>/achievements/',
        player_views.achievements,
        name='achievements',
    ),
]
