from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from fuel_router.models import Truckstop
from fuel_router.services import (
    haversine, geocode_city, get_osrm_route, 
    find_stations_along_path, solve_optimization
)

class FuelRouterServiceTests(TestCase):
    
    def setUp(self):
        # Clean database and create a set of test truckstops
        Truckstop.objects.all().delete()
        
        # Station A: Cheap station along route
        self.stop_a = Truckstop.objects.create(
            opis_id=1,
            name="Cheap Stop A",
            address="123 Route St",
            city="Chicago",
            state="IL",
            retail_price=2.50,
            latitude=41.5,
            longitude=-87.5
        )
        
        # Station B: Expensive station along route at the same location (duplicate coordinates)
        self.stop_b = Truckstop.objects.create(
            opis_id=2,
            name="Expensive Stop B",
            address="123 Route St",
            city="Chicago",
            state="IL",
            retail_price=3.50,
            latitude=41.5,
            longitude=-87.5
        )
        
        # Station C: Duplicate name/address but different ID (different opis_id, same details)
        self.stop_c = Truckstop.objects.create(
            opis_id=3,
            name="Cheap Stop A",
            address="123 Route St",
            city="Chicago",
            state="IL",
            retail_price=2.00,  # Even cheaper!
            latitude=41.50001,
            longitude=-87.50001
        )
        
        # Station D: Far away station (should not be included)
        self.stop_d = Truckstop.objects.create(
            opis_id=4,
            name="Far Stop D",
            address="999 Desert Rd",
            city="Las Vegas",
            state="NV",
            retail_price=1.50,
            latitude=36.0,
            longitude=-115.0
        )
        
    def test_haversine(self):
        # Distance between Chicago (41.8781, -87.6298) and New York (40.7128, -74.0060) is ~711 miles
        dist = haversine(41.8781, -87.6298, 40.7128, -74.0060)
        self.assertTrue(700 < dist < 725)

    def test_geocode_city_mock_fallback(self):
        coords = geocode_city("Chicago, IL")
        self.assertIsNotNone(coords)
        # Verify coordinates are approximately Chicago
        self.assertAlmostEqual(coords[0], 41.87, places=1)
        self.assertAlmostEqual(coords[1], -87.62, places=1)

    def test_get_osrm_route_fallback(self):
        start = (41.8781, -87.6298)
        end = (40.7128, -74.0060)
        points, distance = get_osrm_route(start, end)
        self.assertIsNotNone(points)
        self.assertTrue(len(points) > 0)
        self.assertTrue(distance > 0)

    def test_find_stations_along_path_and_deduplication(self):
        # A route path passing right through longitude -87.5, latitude 41.5
        route = [
            [-88.0, 42.0],
            [-87.5, 41.5],
            [-87.0, 41.0]
        ]
        stations = find_stations_along_path(route, initial_radius_miles=10.0, max_radius_miles=10.0)
        
        # The far station D should be excluded.
        # Stations A, B, and C should be matched, but since B and C are duplicates of A (A and B have identical coords, 
        # C has matching name, address, city, state), we expect only ONE unique station to remain.
        # Since C has the cheapest price (2.00 vs 2.50 vs 3.50), C should be the one kept.
        self.assertEqual(len(stations), 1)
        self.assertEqual(stations[0]['opis_id'], 3)
        self.assertEqual(stations[0]['price'], 2.00)

    def test_solve_optimization_no_refuel_needed(self):
        # Route distance: 400 miles (less than 500 miles range)
        start = (40.0, -80.0)
        end = (40.0, -85.9)
        stations = []
        stops, cost = solve_optimization(start, end, stations, 400.0)
        
        self.assertEqual(len(stops), 0)
        self.assertEqual(cost, 0.0)

    def test_solve_optimization_impossible_route(self):
        # Route distance: 800 miles, no fuel stops available
        start = (40.0, -80.0)
        end = (40.0, -90.0)
        stations = []
        stops, cost = solve_optimization(start, end, stations, 800.0)
        
        self.assertIsNone(stops)
        self.assertEqual(cost, 0.0)

    def test_solve_optimization_with_stops(self):
        # Route distance: 800 miles. We have a station at 400 miles.
        start = (40.0, -80.0)
        end = (40.0, -90.0)
        stations = [
            {
                'opis_id': 100,
                'name': 'Midway Oasis',
                'address': 'I-80 Mile 400',
                'city': 'Omaha',
                'state': 'NE',
                'price': 3.00,
                'latitude': 40.0,
                'longitude': -85.0,
                'dist_along': 400.0
            }
        ]
        stops, cost = solve_optimization(start, end, stations, 800.0)
        
        self.assertIsNotNone(stops)
        self.assertEqual(len(stops), 1)
        self.assertEqual(stops[0]['opis_id'], 100)
        # We start with 500 miles. We reach the station at 400 miles, arriving with 100 miles of range left.
        # To complete the remaining 400 miles to reach 800, we need exactly 400 miles of range.
        # We have 100 miles, so we must add 300 miles of range (30 gallons).
        # Cost = 30 gallons * $3.00/gal = $90.00.
        self.assertEqual(stops[0]['fuel_added_gallons'], 30.0)
        self.assertEqual(cost, 90.0)


class FuelRouterAPITests(APITestCase):
    
    def setUp(self):
        # Ensure database is set up with at least Chicago and New York coordinates
        # and truckstops along the way to test API view.
        Truckstop.objects.all().delete()
        # Toledo, OH is ~240 miles from Chicago
        Truckstop.objects.create(
            opis_id=99,
            name="Midway Stop",
            address="I-90 exit 12",
            city="Toledo",
            state="OH",
            retail_price=2.99,
            latitude=41.6528,
            longitude=-83.5379
        )
        # Syracuse, NY is ~420 miles from Toledo, and ~310 miles from Boston
        # This makes the Chicago -> Boston trip feasible (total distance ~980 miles)
        Truckstop.objects.create(
            opis_id=101,
            name="Syracuse Stop",
            address="I-90 exit 36",
            city="Syracuse",
            state="NY",
            retail_price=3.10,
            latitude=43.0481,
            longitude=-76.1474
        )
        
    def test_api_missing_params(self):
        url = reverse('route_plan')
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("error", response.data)

    def test_api_successful_route_short(self):
        """
        Chicago to Cleveland is ~340 miles - fits within the 500-mile tank.
        No fuel stops required, so the response should always be 200 OK
        regardless of which stations happen to lie near the OSRM road path.
        """
        url = reverse('route_plan')
        response = self.client.get(url, {'start': 'Chicago, IL', 'finish': 'Cleveland, OH'})
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Validate response payload structure
        self.assertIn("route_path", response.data)
        self.assertIn("total_distance_miles", response.data)
        self.assertIn("total_fuel_cost", response.data)
        self.assertIn("optimal_fuel_stops", response.data)

        # Under 500 miles with full tank: no stops needed, cost is zero
        self.assertEqual(response.data["optimal_fuel_stops"], [])
        self.assertEqual(response.data["total_fuel_cost"], 0.0)
        self.assertLess(response.data["total_distance_miles"], 500)
