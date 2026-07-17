from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django_ratelimit.decorators import ratelimit
from django.utils.decorators import method_decorator
from django.core.cache import cache

from .services import geocode_city, get_osrm_route, find_stations_along_path, solve_optimization

class RoutePlanView(APIView):
    
    # Limit users to 10 requests per minute per IP address
    @method_decorator(ratelimit(key='ip', rate='10/m', method='GET', block=True))
    def get(self, request):
        start = request.query_params.get('start')
        finish = request.query_params.get('finish')

        if not start or not finish:
            return Response(
                {"error": "Query parameters 'start' and 'finish' are required."}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        # 1. Check Redis Cache
        cache_key = f"route_plan_{start.lower().replace(' ', '_')}_{finish.lower().replace(' ', '_')}"
        cached_response = cache.get(cache_key)
        if cached_response:
            return Response(cached_response, status=status.HTTP_200_OK)

        # 2. Geocode Cities
        start_coords = geocode_city(start)
        end_coords = geocode_city(finish)
        if not start_coords or not end_coords:
            return Response(
                {"error": f"Failed to geocode locations. Verify spelling (e.g. 'Chicago, IL')."}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        # 3. Generate route via OSRM
        route_coords, total_dist = get_osrm_route(start_coords, end_coords)
        if not route_coords:
            return Response(
                {"error": "Failed to map a route between these coordinates."}, 
                status=status.HTTP_424_FAILED_DEPENDENCY
            )

        # 4. Find stations close to the road
        stations = find_stations_along_path(route_coords)

        # 5. Calculate cheapest fuel stops
        optimal_stops, total_cost = solve_optimization(start_coords, end_coords, stations, total_dist)
        
        if optimal_stops is None:
            return Response(
                {"error": "A route exists, but fuel stops are too far apart for the vehicle's 500-mile range."},
                status=status.HTTP_422_UNPROCESSABLE_ENTITY
            )

        # Output payload structures
        response_payload = {
            "route_path": route_coords, # Array of [lon, lat] coordinates (GeoJSON compliant)
            "total_distance_miles": round(total_dist, 2),
            "total_fuel_cost": total_cost,
            "optimal_fuel_stops": optimal_stops
        }

        # Cache the successful payload for 24 hours
        cache.set(cache_key, response_payload, timeout=86400)

        return Response(response_payload, status=status.HTTP_200_OK)