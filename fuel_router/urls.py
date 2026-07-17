from django.urls import path
from .views import RoutePlanView

urlpatterns = [
    path('api/route/', RoutePlanView.as_view(), name='route_plan'),
]
