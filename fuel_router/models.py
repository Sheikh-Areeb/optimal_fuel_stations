from django.db import models

class Truckstop(models.Model):
    # 'opis_id' ensures we deduplicate the same physical location
    opis_id = models.IntegerField(unique=True, db_index=True)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2)
    retail_price = models.FloatField()
    
    # Geographic coordinates loaded via the seeding process
    latitude = models.FloatField(null=True, blank=True, db_index=True)
    longitude = models.FloatField(null=True, blank=True, db_index=True)

    def __str__(self):
        return f"{self.name} (${self.retail_price}/gal) at {self.city}, {self.state}"