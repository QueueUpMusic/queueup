from django.urls import path

from . import views


app_name = 'api-v1'

urlpatterns = [
    path('', views.api_index, name='index'),
    path('session/', views.current_session, name='session'),
]
