import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application
import league.routing
application = ProtocolTypeRouter({'http': get_asgi_application(), 'websocket': AuthMiddlewareStack(URLRouter(league.routing.websocket_urlpatterns))})
