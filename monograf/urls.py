from django.urls import path

from monograf.views import PowerUsageCalculatorView


urlpatterns = [
    path('calculate-power-usage/', PowerUsageCalculatorView.as_view(), name='calculate-power-usage'),
]
