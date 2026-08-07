from django.urls import path
from .consumers import LeagueConsumer
websocket_urlpatterns = [path('ws/league/', LeagueConsumer.as_asgi())]
