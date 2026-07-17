import csv
import time
import requests
import re
from django.core.management.base import BaseCommand
from fuel_router.models import Truckstop

class Command(BaseCommand):
    help = "Deduplicates, geocodes with high precision, and imports fuel stations from a CSV file."

    def add_arguments(self, parser):
        parser.add_argument('csv_file', type=str, help="Path to the fuel stations CSV file.")

    def clean_address(self, address):
        """
        Normalizes messy highway and intersection addresses into standard English
        that search engines like Nominatim easily understand.
        """
        # 1. Convert to uppercase for consistent regex matching
        addr = address.upper()

        # 2. Strip out Exit markers (e.g., "EXIT 211", "EXIT 22A", "EXIT 2W")
        addr = re.sub(r'\bEXIT\s+\d+[A-Z]?\b', '', addr)
        
        # 3. Strip out Mile Markers (e.g., "MM 209", "MILE MARKER 97", "MILE 10")
        addr = re.sub(r'\b(MM|MILE|MILE\s+MARKER)\s+\d+\b', '', addr)

        # 4. Expand Road Types
        addr = re.sub(r'\bI[- ]?(\d+)\b', r'Interstate \1', addr)
        addr = re.sub(r'\bSR[- ]?(\d+)\b', r'State Route \1', addr)
        addr = re.sub(r'\bUS[- ]?(\d+)\b', r'US Highway \1', addr)
        addr = re.sub(r'\bCR[- ]?(\d+)\b', r'County Road \1', addr)

        # 5. Normalize intersection ampersands to "and"
        addr = addr.replace('&', ' and ')

        # 6. Remove commas surrounding the intersection "and"
        # e.g., "Interstate 44, and US Highway 69" -> "Interstate 44 and US Highway 69"
        addr = re.sub(r'\s*,\s*AND\s*|\s*AND\s*,\s*', ' and ', addr, flags=re.IGNORECASE)

        # 7. Clean up trailing/leading commas, double spaces
        addr = re.sub(r'\s+', ' ', addr)              # Collapse multiple spaces
        addr = re.sub(r',\s*,', ',', addr)             # Collapse multiple commas
        addr = re.sub(r'^\s*,\s*|\s*,\s*$', '', addr)  # Strip leading/trailing commas
        addr = addr.strip()

        return addr

    def handle(self, *args, **options):
        csv_path = options['csv_file']
        stations_dict = {}

        self.stdout.write("Reading and deduplicating CSV data...")
        with open(csv_path, mode='r', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    opis_id = int(row['OPIS Truckstop ID'].strip())
                    price = float(row['Retail Price'].strip())
                    
                    if opis_id not in stations_dict or price < stations_dict[opis_id]['price']:
                        stations_dict[opis_id] = {
                            'name': row['Truckstop Name'].strip(),
                            'address': row['Address'].strip(),
                            'city': row['City'].strip(),
                            'state': row['State'].strip(),
                            'price': price
                        }
                except (ValueError, KeyError):
                    continue

        total_stations = len(stations_dict)
        self.stdout.write(f"Found {total_stations} unique stations to process...")

        for index, (opis_id, data) in enumerate(stations_dict.items(), 1):
            lat, lon = None, None
            
            # --- TIER 1: Try Brand Name + City + State (extremely accurate POI lookup) ---
            brand_query = f"{data['name']}, {data['city']}, {data['state']}"
            lat, lon = self.geocode_nominatim(brand_query)
            time.sleep(1)  # Respect rate limit

            # --- TIER 2: Try Cleaned Intersection / Address via Nominatim ---
            if not lat or not lon:
                cleaned_address = self.clean_address(data['address'])
                intersection_query = f"{cleaned_address}, {data['city']}, {data['state']}"
                lat, lon = self.geocode_nominatim(intersection_query)
                time.sleep(1)

            # --- TIER 3: Try Cleaned Address via US Census Geocoder ---
            if not lat or not lon:
                cleaned_address = self.clean_address(data['address'])
                lat, lon = self.geocode_us_census(cleaned_address, data['city'], data['state'])

            # --- TIER 4: Fallback to Brand Name + State (Rescues stubborn highway POIs) ---
            if not lat or not lon:
                brand_state_query = f"{data['name']}, {data['state']}"
                lat, lon = self.geocode_nominatim(brand_state_query)
                time.sleep(1)

            # --- SAVE OR SKIP ---
            if lat and lon:
                Truckstop.objects.update_or_create(
                    opis_id=opis_id,
                    defaults={
                        'name': data['name'],
                        'address': data['address'],
                        'city': data['city'],
                        'state': data['state'],
                        'retail_price': data['price'],
                        'latitude': lat,
                        'longitude': lon
                    }
                )
                self.stdout.write(f"[{index}/{total_stations}] Saved: {data['name']} ({lat}, {lon})")
            else:
                self.stdout.write(
                    self.style.WARNING(
                        f"[{index}/{total_stations}] SKIPPED (No unique match): {data['address']}, {data['city']}, {data['state']}"
                    )
                )

        self.stdout.write(self.style.SUCCESS("Database seeding complete!"))

    def geocode_us_census(self, street, city, state):
        """Free US Government Census Geocoding API (Fast and Unlimited)"""
        url = "https://geocoding.geo.census.gov/geocoder/locations/address"
        params = {
            'street': street,
            'city': city,
            'state': state,
            'benchmark': 'Public_AR_Current',
            'format': 'json'
        }
        try:
            r = requests.get(url, params=params, timeout=5)
            if r.status_code == 200:
                results = r.json().get('result', {}).get('addressMatches', [])
                if results:
                    coords = results[0]['coordinates']
                    return float(coords['y']), float(coords['x'])
        except Exception:
            pass
        return (None, None)

    def geocode_nominatim(self, query):
        """Fallback OpenStreetMap Nominatim Geocoder"""
        url = "https://nominatim.openstreetmap.org/search"
        headers = {'User-Agent': 'FuelRoutingApp/1.0'}
        params = {'q': query, 'format': 'json', 'limit': 1}
        try:
            r = requests.get(url, headers=headers, params=params, timeout=5)
            if r.status_code == 200 and r.json():
                res = r.json()[0]
                return float(res['lat']), float(res['lon'])
        except Exception:
            pass
        return (None, None)
