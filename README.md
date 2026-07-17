# ⛽ Optimal Fuel Stations — Route Planner API

A Django REST API that calculates the **cheapest possible fuel stops** for a truck driving across the United States. Given a start and end city, it finds the optimal gas stations to stop at along the route — minimizing total fuel cost while ensuring the vehicle never runs out of gas.

---

## 🚀 What It Does

You provide two city names (e.g., `Chicago, IL` → `Los Angeles, CA`), and the API returns:

- 🗺️ The full driving route as GPS coordinates
- 📏 Total trip distance in miles
- ⛽ The optimal list of fuel stops (with exact gallons to add at each)
- 💰 The total fuel cost for the entire trip

The vehicle is assumed to have a **500-mile range** on a full tank and gets **10 miles per gallon**.

---

## 🧠 How It Works (High-Level)

```
User Request (start city, end city)
        ↓
  1. Geocode cities → GPS coordinates
        ↓
  2. Get driving route from OSRM → polyline path + distance
        ↓
  3. Find fuel stations within 15 miles of the road
        ↓
  4. Run Dynamic Programming optimization → cheapest refueling plan
        ↓
  5. Return route + optimal stops + total cost
```

---

## 📦 Tech Stack

| Layer            | Technology                            |
|------------------|---------------------------------------|
| Framework        | Django 4.2 + Django REST Framework    |
| Database         | SQLite (production-ready with PostgreSQL) |
| Caching          | Redis (optional) / Django DB Cache   |
| Rate Limiting    | `django-ratelimit` (10 req/min per IP)|
| Geocoding        | OpenStreetMap Nominatim API           |
| Routing          | OSRM (Open Source Routing Machine)    |
| Optimization     | Custom Dynamic Programming algorithm  |

---

## ⚙️ Setup & Installation

### 1. Clone the repository

```bash
git clone https://github.com/Sheikh-Areeb/optimal_fuel_stations.git
cd optimal_fuel_stations
```

### 2. Create a virtual environment and install dependencies

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Mac/Linux

pip install -r requirements.txt
```

### 3. Configure environment variables

Create a `.env` file in the project root:

```env
SECRET_KEY=your-secret-key-here
DEBUG=True
CACHE_BACKEND=locmem          # Use 'redis' if Redis is available
REDIS_URL=redis://127.0.0.1:6379/1
```

### 4. Apply migrations and seed the database

```bash
python manage.py migrate
python manage.py createcachetable        # For DB-based caching
python manage.py load_fuel_data          # Loads fuel_data.csv into the DB
```

### 5. Run the development server

```bash
python manage.py runserver
```

The API will be available at: `http://127.0.0.1:8000/`

---

## 🔌 API Reference

### `GET /api/route/`

Calculates the optimal fuel route between two US cities.

#### Query Parameters

| Parameter | Type   | Required | Description                          |
|-----------|--------|----------|--------------------------------------|
| `start`   | string | ✅ Yes   | Starting city (e.g., `Chicago, IL`)  |
| `finish`  | string | ✅ Yes   | Destination city (e.g., `Miami, FL`) |

#### Example Request

```
GET http://127.0.0.1:8000/api/route/?start=Chicago, IL&finish=Miami, FL
```

#### Example Response (200 OK)

```json
{
  "route_path": [[-87.62, 41.87], [-86.10, 40.20], ...],
  "total_distance_miles": 1380.5,
  "total_fuel_cost": 412.50,
  "optimal_fuel_stops": [
    {
      "opis_id": 5821,
      "name": "Pilot Travel Center",
      "address": "4201 I-65 Exit 22",
      "city": "Indianapolis",
      "state": "IN",
      "latitude": 39.7684,
      "longitude": -86.1581,
      "dist_along_route_miles": 165.3,
      "fuel_added_gallons": 33.5,
      "price_per_gallon": 3.45,
      "cost": 115.58
    }
  ]
}
```

#### Error Responses

| Status Code | Reason                                          |
|-------------|-------------------------------------------------|
| `400`       | Missing `start` or `finish` parameters          |
| `400`       | Could not geocode one of the city names         |
| `422`       | Route exists but fuel stops are too far apart   |
| `424`       | Could not map a driving route                   |
| `429`       | Rate limit exceeded (10 requests/min per IP)    |

---

## 🧪 Running Tests

```bash
python manage.py test fuel_router
```

The test suite covers:
- Haversine distance calculation accuracy
- City geocoding with mock fallback
- OSRM route generation with straight-line fallback
- Station deduplication logic
- DP optimization: no stops needed, impossible routes, and routes with stops

---

## 📊 Data Source

Fuel price data is loaded from `fuel_data.csv`, which contains US truck stop locations with retail diesel prices from the OPIS (Oil Price Information Service) dataset. The management command `load_fuel_data` parses this CSV and geocodes each station's address into latitude/longitude coordinates.

---

## 📐 Vehicle Assumptions

| Parameter     | Value              |
|---------------|--------------------|
| Tank capacity | 500 miles of range |
| Fuel economy  | 10 miles/gallon    |
| Tank size     | 50 gallons         |

---

## 🛡️ Rate Limiting

The API is protected with IP-based rate limiting: **10 requests per minute** per client IP address. Exceeding this returns HTTP `429 Too Many Requests`.

---

## 📁 Project Structure

```
optimal_fuel_stations/
├── fuel_router/
│   ├── models.py       # Truckstop database model
│   ├── views.py        # API endpoint (RoutePlanView)
│   ├── services.py     # Core algorithms (geocoding, routing, DP optimization)
│   ├── urls.py         # URL routing
│   └── tests.py        # Unit + integration tests
├── fuelstations/
│   ├── settings.py     # Django configuration
│   └── urls.py         # Root URL configuration
├── fuel_data.csv       # Raw fuel station dataset
├── manage.py
└── .env                # Environment variables (not committed)
```

---

## 📄 License

MIT License — feel free to use, modify, and distribute.
