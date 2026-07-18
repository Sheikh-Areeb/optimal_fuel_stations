import math
import requests
import os
from django.conf import settings
from django.core.cache import cache
from .models import Truckstop

def haversine(lat1, lon1, lat2, lon2):
    """Calculates haversine distance in miles between two coordinates."""
    R = 3958.8  # Earth radius in miles
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2 + 
         math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

def geocode_city(city_name):
    """Geocodes a city name to (lat, lon) using Nominatim.
    Results are cached for 7 days to avoid redundant API calls.
    Falls back to a built-in mock dict and the Truckstop DB if the API is unreachable.
    """
    geo_cache_key = f"geocode_{city_name.lower().replace(' ', '_').replace(',', '')}"
    cached = cache.get(geo_cache_key)
    if cached is not None:
        return cached

    # --- Live Nominatim lookup ---
    base_url = getattr(settings, 'NOMINATIM_BASE_URL', 'https://nominatim.openstreetmap.org')
    user_agent = getattr(settings, 'NOMINATIM_USER_AGENT', 'FuelRoutingApp/1.0')
    url = f"{base_url}/search"
    headers = {'User-Agent': user_agent}
    params = {'q': city_name, 'format': 'json', 'limit': 1}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=5)
        if r.status_code == 200 and r.json():
            res = r.json()[0]
            result = (float(res['lat']), float(res['lon']))
            cache.set(geo_cache_key, result, timeout=60 * 60 * 24 * 7)  # 7 days
            return result
    except Exception:
        pass

    # --- Fallback 1: built-in mock dict for common US cities ---
    MOCK_CITIES = {
        'new york, ny': (40.7128, -74.0060), 'new york': (40.7128, -74.0060),
        'chicago, il': (41.8781, -87.6298), 'chicago': (41.8781, -87.6298),
        'los angeles, ca': (34.0522, -118.2437), 'los angeles': (34.0522, -118.2437),
        'houston, tx': (29.7604, -95.3698), 'houston': (29.7604, -95.3698),
        'miami, fl': (25.7617, -80.1918), 'miami': (25.7617, -80.1918),
        'seattle, wa': (47.6062, -122.3321), 'seattle': (47.6062, -122.3321),
        'san francisco, ca': (37.7749, -122.4194), 'san francisco': (37.7749, -122.4194),
        'denver, co': (39.7392, -104.9903), 'denver': (39.7392, -104.9903),
        'boston, ma': (42.3601, -71.0589), 'boston': (42.3601, -71.0589),
        'atlanta, ga': (33.7490, -84.3880), 'atlanta': (33.7490, -84.3880),
        'cleveland, oh': (41.4993, -81.6944), 'cleveland': (41.4993, -81.6944),
    }
    normalized = city_name.strip().lower()
    if normalized in MOCK_CITIES:
        result = MOCK_CITIES[normalized]
        cache.set(geo_cache_key, result, timeout=60 * 60 * 24 * 7)
        return result

    # --- Fallback 2: nearest truckstop city in the database ---
    db_match = Truckstop.objects.filter(city__iexact=city_name).first()
    if db_match:
        result = (db_match.latitude, db_match.longitude)
        cache.set(geo_cache_key, result, timeout=60 * 60 * 24 * 7)
        return result

    return None

def get_osrm_route(start_coords, end_coords):
    """Fetches driving route coordinates and distance from the OSRM server.
    Falls back to straight-line interpolation if OSRM is unreachable.
    """
    base_url = getattr(settings, 'OSRM_BASE_URL', 'http://router.project-osrm.org')
    url = (
        f"{base_url}/route/v1/driving/"
        f"{start_coords[1]},{start_coords[0]};"
        f"{end_coords[1]},{end_coords[0]}"
        "?overview=full&geometries=geojson"
    )

    try:
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            if "routes" in data and len(data["routes"]) > 0:
                route = data["routes"][0]
                distance_miles = route["distance"] * 0.000621371
                coordinates = route["geometry"]["coordinates"]
                return coordinates, distance_miles
    except Exception:
        # Fall back to straight-line interpolation if OSRM is unreachable
        pass

    # Resilient straight-line fallback (interpolates 100 coords between start and end)
    num_points = 100
    lat1, lon1 = start_coords
    lat2, lon2 = end_coords
    points = []
    for i in range(num_points):
        t = i / (num_points - 1)
        lat = lat1 + t * (lat2 - lat1)
        lon = lon1 + t * (lon2 - lon1)
        points.append([lon, lat])

    distance_miles = haversine(lat1, lon1, lat2, lon2)
    return points, distance_miles

def _project_stations(candidate_stops, route_coords, cum_dist, max_distance_miles):
    """
    Inner helper: projects candidate DB stops onto the route polyline.
    Returns a flat list of station dicts that fall within max_distance_miles.

    FIX — detour cost:
      A station sitting D miles off the highway requires a D-mile exit + D-mile
      re-entry (round trip = 2*D miles of fuel).  We add that cost to dist_along
      so the DP solver sees the station's *effective* position on the route and
      never underestimates the fuel needed to visit it.
    """
    results = []
    for stop in candidate_stops:
        stop_lat = stop.latitude
        stop_lon = stop.longitude

        min_seg_dist = float('inf')
        best_dist_along = 0.0

        for i in range(len(route_coords) - 1):
            lon_A, lat_A = route_coords[i]
            lon_B, lat_B = route_coords[i + 1]

            dlon = lon_B - lon_A
            dlat = lat_B - lat_A

            if dlon == 0 and dlat == 0:
                t = 0.0
            else:
                t = ((stop_lon - lon_A) * dlon + (stop_lat - lat_A) * dlat) / (dlon ** 2 + dlat ** 2)
                t = max(0.0, min(1.0, t))

            lat_proj = lat_A + t * dlat
            lon_proj = lon_A + t * dlon

            seg_dist = haversine(stop_lat, stop_lon, lat_proj, lon_proj)
            if seg_dist < min_seg_dist:
                min_seg_dist = seg_dist
                best_dist_along = cum_dist[i] + t * (cum_dist[i + 1] - cum_dist[i])

        if min_seg_dist <= max_distance_miles:
            # Add the round-trip detour miles (exit ramp + re-entry) to effective position.
            # This ensures the DP never assumes the station is free to visit.
            effective_dist_along = best_dist_along + (2 * min_seg_dist)
            results.append({
                'opis_id': stop.opis_id,
                'name': stop.name,
                'address': stop.address,
                'city': stop.city,
                'state': stop.state,
                'price': stop.retail_price,
                'latitude': stop.latitude,
                'longitude': stop.longitude,
                'dist_along': effective_dist_along,   # ← includes detour cost
                'dist_from_route': min_seg_dist        # raw perpendicular distance (for info only)
            })
    return results


def find_stations_along_path(route_coords, initial_radius_miles=15.0, max_radius_miles=50.0):
    """
    Finds fuel stations near the route, accounts for detour costs, and
    deduplicates identical locations keeping only the cheapest.

    Progressive radius widening:
      Starts at initial_radius_miles (default 15).  If no stations are found,
      the search radius expands in 5-mile steps up to max_radius_miles before
      giving up.  This prevents silent route failures on sparse stretches of road
      while still preferring close, convenient stations.
    """
    if not route_coords:
        return []

    lats = [pt[1] for pt in route_coords]
    lons = [pt[0] for pt in route_coords]

    # 1. Bounding box pre-filter (generous buffer to cover the widest possible radius)
    min_lat, max_lat = min(lats), max(lats)
    min_lon, max_lon = min(lons), max(lons)
    # 1 degree ≈ 69 miles — buffer covers max_radius_miles comfortably
    buffer = max_radius_miles / 69.0

    candidate_stops = list(Truckstop.objects.filter(
        latitude__range=(min_lat - buffer, max_lat + buffer),
        longitude__range=(min_lon - buffer, max_lon + buffer)
    ))

    # Compute cumulative distances along the route polyline (built once, reused)
    cum_dist = [0.0]
    for i in range(1, len(route_coords)):
        prev_lon, prev_lat = route_coords[i - 1]
        curr_lon, curr_lat = route_coords[i]
        cum_dist.append(cum_dist[-1] + haversine(prev_lat, prev_lon, curr_lat, curr_lon))

    # 2. Progressive radius widening — expand search until stations are found
    STEP = 5.0  # miles per expansion step
    radius = initial_radius_miles
    stations_along_route = []

    while radius <= max_radius_miles:
        stations_along_route = _project_stations(candidate_stops, route_coords, cum_dist, radius)
        if stations_along_route:
            break  # Found stations — no need to widen further
        radius += STEP

    # 3. Deduplicate: same location listed multiple times → keep cheapest price
    deduped_stations = []
    for s in stations_along_route:
        coord_key = (round(s['latitude'], 5), round(s['longitude'], 5))
        addr_key  = (s['address'].strip().lower(), s['city'].strip().lower(), s['state'].strip().lower())
        name_key  = (s['name'].strip().lower(),    s['city'].strip().lower(), s['state'].strip().lower())

        matched_idx = -1
        for idx, ds in enumerate(deduped_stations):
            ds_coord_key = (round(ds['latitude'], 5), round(ds['longitude'], 5))
            ds_addr_key  = (ds['address'].strip().lower(), ds['city'].strip().lower(), ds['state'].strip().lower())
            ds_name_key  = (ds['name'].strip().lower(),    ds['city'].strip().lower(), ds['state'].strip().lower())

            if coord_key == ds_coord_key or addr_key == ds_addr_key or name_key == ds_name_key:
                matched_idx = idx
                break

        if matched_idx != -1:
            if s['price'] < deduped_stations[matched_idx]['price']:
                deduped_stations[matched_idx] = s
        else:
            deduped_stations.append(s)

    # Sort by effective distance along route
    deduped_stations.sort(key=lambda x: x['dist_along'])
    return deduped_stations

def solve_optimization(start_coords, end_coords, stations, total_dist):
    """
    Dynamic Programming refueling solver.
    Returns: (optimal_stops_list, total_fuel_cost)
    Optimal stops list contains dicts with refueling stop coordinates and amount added.
    """
    # Build list of nodes: Start -> Stations -> Destination
    nodes = []
    nodes.append({
        'dist': 0.0,
        'price': float('inf'),
        'is_start': True,
        'is_dest': False,
        'name': 'Start',
        'city': '',
        'state': '',
        'latitude': start_coords[0],
        'longitude': start_coords[1]
    })
    
    for s in stations:
        nodes.append({
            'dist': s['dist_along'],
            'price': s['price'],
            'is_start': False,
            'is_dest': False,
            'name': s['name'],
            'address': s['address'],
            'city': s['city'],
            'state': s['state'],
            'latitude': s['latitude'],
            'longitude': s['longitude'],
            'opis_id': s['opis_id']
        })
        
    nodes.append({
        'dist': total_dist,
        'price': 0.0,
        'is_start': False,
        'is_dest': True,
        'name': 'Destination',
        'city': '',
        'state': '',
        'latitude': end_coords[0],
        'longitude': end_coords[1]
    })
    
    N = len(nodes)
    
    # 1. Reachability Check: Ensure step-by-step distances are all <= 500 miles
    for i in range(1, N):
        if nodes[i]['dist'] - nodes[i-1]['dist'] > 500.0:
            return None, 0.0
            
    # 2. DP Table: dp[i] = { fuel_level: cost }
    dp = [{} for _ in range(N)]
    transitions = [{} for _ in range(N)]
    
    # Base case: Destination (index N-1) with 0 range left costs 0
    dp[N-1][0.0] = 0.0
    
    # 3. Dynamic Programming Backward Pass
    for i in range(N-2, -1, -1):
        d_i = nodes[i]['dist']
        
        # Calculate candidates for station i:
        # Full tank (500 miles range) or exactly enough to reach any future node j
        candidates_i = [500.0]
        for j in range(i+1, N):
            dist = nodes[j]['dist'] - d_i
            if dist <= 500.0:
                candidates_i.append(dist)
                
        # Deduplicate and sort candidate levels (rounding to 4 decimals prevents float drift)
        candidates_i = sorted(list(set(round(c, 4) for c in candidates_i)))
        
        for L in candidates_i:
            min_cost_state = float('inf')
            best_trans = None
            
            # Transition to next stop j
            for j in range(i+1, N):
                dist = nodes[j]['dist'] - d_i
                if dist <= L:
                    L_arrive = L - dist
                    
                    if j == N-1:
                        # Destination: no refuel needed, cost is 0
                        cost = 0.0
                        if cost < min_cost_state:
                            min_cost_state = cost
                            best_trans = (j, 0.0)
                    else:
                        # Find cheapest L_next at station j such that L_next >= L_arrive
                        p_j = nodes[j]['price']
                        for L_next in dp[j]:
                            if L_next >= round(L_arrive, 4) - 1e-4:
                                refuel_miles = L_next - L_arrive
                                refuel_gallons = refuel_miles / 10.0
                                cost = refuel_gallons * p_j + dp[j][L_next]
                                if cost < min_cost_state:
                                    min_cost_state = cost
                                    best_trans = (j, L_next)
                                    
            if min_cost_state != float('inf'):
                dp[i][L] = min_cost_state
                transitions[i][L] = best_trans
                
    if 500.0 not in dp[0] or dp[0][500.0] == float('inf'):
        return None, 0.0
        
    # 4. Path Reconstruction
    optimal_stops = []
    curr_i = 0
    curr_L = 500.0
    total_cost = dp[0][500.0]
    
    while True:
        trans = transitions[curr_i].get(curr_L)
        if not trans:
            break
            
        next_i, next_L = trans
        
        if not nodes[next_i]['is_dest']:
            dist_traveled = nodes[next_i]['dist'] - nodes[curr_i]['dist']
            L_arrive = curr_L - dist_traveled
            refuel_miles = next_L - L_arrive
            refuel_gallons = refuel_miles / 10.0
            
            if refuel_gallons > 0.001:
                optimal_stops.append({
                    'opis_id': nodes[next_i]['opis_id'],
                    'name': nodes[next_i]['name'],
                    'address': nodes[next_i]['address'],
                    'city': nodes[next_i]['city'],
                    'state': nodes[next_i]['state'],
                    'latitude': nodes[next_i]['latitude'],
                    'longitude': nodes[next_i]['longitude'],
                    'dist_along_route_miles': round(nodes[next_i]['dist'], 2),
                    'fuel_added_gallons': round(refuel_gallons, 2),
                    'price_per_gallon': nodes[next_i]['price'],
                    'cost': round(refuel_gallons * nodes[next_i]['price'], 2)
                })
                
        curr_i = next_i
        curr_L = next_L
        
    return optimal_stops, round(total_cost, 2)
