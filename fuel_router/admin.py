from django.contrib import admin
from .models import Truckstop

@admin.register(Truckstop)
class TruckstopAdmin(admin.ModelAdmin):
    list_display  = ('name', 'city', 'state', 'retail_price', 'latitude', 'longitude')
    list_filter   = ('state',)
    search_fields = ('name', 'city', 'address')
    ordering      = ('state', 'city')
