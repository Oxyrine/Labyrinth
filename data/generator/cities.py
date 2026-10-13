"""60 Indian industrial/commercial cities with pincodes, GST state codes, and distance helpers."""

from __future__ import annotations

import math
from typing import TypedDict


class CityInfo(TypedDict):
    city: str
    pincode: str
    state_code: str
    lat: float
    lon: float


CITIES: list[CityInfo] = [
    {"city": "Pune Chakan", "pincode": "410501", "state_code": "27", "lat": 18.7606, "lon": 73.8636},
    {"city": "Pune Ranjangaon", "pincode": "412220", "state_code": "27", "lat": 18.7537, "lon": 74.2415},
    {"city": "Pune Pimpri", "pincode": "411018", "state_code": "27", "lat": 18.6298, "lon": 73.7997},
    {"city": "Mumbai Nariman", "pincode": "400001", "state_code": "27", "lat": 18.9388, "lon": 72.8354},
    {"city": "Navi Mumbai TTC", "pincode": "400708", "state_code": "27", "lat": 19.1551, "lon": 72.9990},
    {"city": "Thane Wagle", "pincode": "400604", "state_code": "27", "lat": 19.1970, "lon": 72.9510},
    {"city": "Nashik Satpur", "pincode": "422007", "state_code": "27", "lat": 19.9975, "lon": 73.7398},
    {"city": "Aurangabad Waluj", "pincode": "431136", "state_code": "27", "lat": 19.8347, "lon": 75.2351},
    {"city": "Nagpur Butibori", "pincode": "441122", "state_code": "27", "lat": 20.9272, "lon": 79.0035},
    {"city": "Kolhapur Shiroli", "pincode": "416122", "state_code": "27", "lat": 16.7480, "lon": 74.2775},
    {"city": "Chennai Central", "pincode": "600001", "state_code": "33", "lat": 13.0827, "lon": 80.2707},
    {"city": "Chennai Ambattur", "pincode": "600058", "state_code": "33", "lat": 13.0982, "lon": 80.1614},
    {"city": "Sriperumbudur SIPCOT", "pincode": "602105", "state_code": "33", "lat": 12.9675, "lon": 79.9419},
    {"city": "Coimbatore Peelamedu", "pincode": "641004", "state_code": "33", "lat": 11.0283, "lon": 77.0025},
    {"city": "Hosur SIPCOT", "pincode": "635126", "state_code": "33", "lat": 12.7409, "lon": 77.8253},
    {"city": "Madurai Kappalur", "pincode": "625008", "state_code": "33", "lat": 9.8665, "lon": 78.0379},
    {"city": "Tiruchirappalli BHEL", "pincode": "620014", "state_code": "33", "lat": 10.7610, "lon": 78.7865},
    {"city": "Salem Steel Plant", "pincode": "636013", "state_code": "33", "lat": 11.6643, "lon": 78.0650},
    {"city": "Bengaluru MG Road", "pincode": "560001", "state_code": "29", "lat": 12.9716, "lon": 77.5946},
    {"city": "Bengaluru Peenya", "pincode": "560058", "state_code": "29", "lat": 13.0285, "lon": 77.5197},
    {"city": "Bengaluru Whitefield", "pincode": "560066", "state_code": "29", "lat": 12.9698, "lon": 77.7499},
    {"city": "Mysuru Hebbal", "pincode": "570016", "state_code": "29", "lat": 12.3563, "lon": 76.6095},
    {"city": "Hubballi Tarihal", "pincode": "580026", "state_code": "29", "lat": 15.3647, "lon": 75.1240},
    {"city": "Mangaluru Baikampady", "pincode": "575011", "state_code": "29", "lat": 12.9538, "lon": 74.8126},
    {"city": "Belagavi Udyambag", "pincode": "590008", "state_code": "29", "lat": 15.8220, "lon": 74.4920},
    {"city": "Ahmedabad GPO", "pincode": "380001", "state_code": "24", "lat": 23.0225, "lon": 72.5714},
    {"city": "Sanand GIDC", "pincode": "382110", "state_code": "24", "lat": 22.9918, "lon": 72.3815},
    {"city": "Vadodara Makarpura", "pincode": "390010", "state_code": "24", "lat": 22.2486, "lon": 73.1926},
    {"city": "Surat Hazira", "pincode": "394270", "state_code": "24", "lat": 21.1162, "lon": 72.6556},
    {"city": "Rajkot Aji GIDC", "pincode": "360003", "state_code": "24", "lat": 22.2974, "lon": 70.8286},
    {"city": "Vapi GIDC", "pincode": "396195", "state_code": "24", "lat": 20.3694, "lon": 72.9272},
    {"city": "Bharuch Dahej", "pincode": "392130", "state_code": "24", "lat": 21.7131, "lon": 72.5839},
    {"city": "New Delhi Connaught", "pincode": "110001", "state_code": "07", "lat": 28.6315, "lon": 77.2167},
    {"city": "Delhi Okhla", "pincode": "110020", "state_code": "07", "lat": 28.5355, "lon": 77.2732},
    {"city": "Gurugram Manesar", "pincode": "122051", "state_code": "06", "lat": 28.3547, "lon": 76.9367},
    {"city": "Faridabad NIT", "pincode": "121001", "state_code": "06", "lat": 28.3970, "lon": 77.3025},
    {"city": "Panipat Industrial", "pincode": "132103", "state_code": "06", "lat": 29.3909, "lon": 76.9635},
    {"city": "Noida Phase II", "pincode": "201305", "state_code": "09", "lat": 28.5313, "lon": 77.4050},
    {"city": "Ghaziabad Sahibabad", "pincode": "201010", "state_code": "09", "lat": 28.6692, "lon": 77.3598},
    {"city": "Kanpur Dada Nagar", "pincode": "208022", "state_code": "09", "lat": 26.4499, "lon": 80.2810},
    {"city": "Lucknow Amausi", "pincode": "226008", "state_code": "09", "lat": 26.7606, "lon": 80.8893},
    {"city": "Hyderabad Balanagar", "pincode": "500001", "state_code": "36", "lat": 17.3850, "lon": 78.4867},
    {"city": "Hyderabad Jeedimetla", "pincode": "500055", "state_code": "36", "lat": 17.5204, "lon": 78.4526},
    {"city": "Visakhapatnam Gajuwaka", "pincode": "530026", "state_code": "37", "lat": 17.6868, "lon": 83.2185},
    {"city": "Vijayawada Autonagar", "pincode": "520007", "state_code": "37", "lat": 16.4971, "lon": 80.6844},
    {"city": "Kolkata Dalhousie", "pincode": "700001", "state_code": "19", "lat": 22.5726, "lon": 88.3639},
    {"city": "Howrah Industrial", "pincode": "711101", "state_code": "19", "lat": 22.5958, "lon": 88.2636},
    {"city": "Durgapur Steel", "pincode": "713203", "state_code": "19", "lat": 23.5204, "lon": 87.3119},
    {"city": "Jaipur Sitapura", "pincode": "302022", "state_code": "08", "lat": 26.7796, "lon": 75.8453},
    {"city": "Bhiwadi RIICO", "pincode": "301019", "state_code": "08", "lat": 28.2096, "lon": 76.8606},
    {"city": "Udaipur Madri", "pincode": "313003", "state_code": "08", "lat": 24.5854, "lon": 73.7495},
    {"city": "Ludhiana Focal Point", "pincode": "141010", "state_code": "03", "lat": 30.8809, "lon": 75.9144},
    {"city": "Jalandhar Industrial", "pincode": "144004", "state_code": "03", "lat": 31.3260, "lon": 75.5762},
    {"city": "Indore Pithampur", "pincode": "454775", "state_code": "23", "lat": 22.6134, "lon": 75.6823},
    {"city": "Bhopal Mandideep", "pincode": "462046", "state_code": "23", "lat": 23.0954, "lon": 77.5150},
    {"city": "Jamshedpur Adityapur", "pincode": "831013", "state_code": "20", "lat": 22.7916, "lon": 86.1511},
    {"city": "Raipur Urla", "pincode": "492003", "state_code": "22", "lat": 21.3090, "lon": 81.6177},
    {"city": "Bhubaneswar Mancheswar", "pincode": "751010", "state_code": "21", "lat": 20.3080, "lon": 85.8548},
    {"city": "Kochi Kalamassery", "pincode": "683104", "state_code": "32", "lat": 10.0531, "lon": 76.3270},
    {"city": "Haridwar SIDCUL", "pincode": "249403", "state_code": "05", "lat": 29.9560, "lon": 78.0750},
]

CITY_BY_PINCODE: dict[str, CityInfo] = {c["pincode"]: c for c in CITIES}


def haversine_km(pin1: str, pin2: str) -> float:
    """Compute great-circle distance in kilometers between two Indian pincodes."""
    if pin1 == pin2:
        return 0.0
    c1 = CITY_BY_PINCODE.get(pin1)
    c2 = CITY_BY_PINCODE.get(pin2)
    if c1 is None or c2 is None:
        raise KeyError(f"Unknown pincode in haversine_km: {pin1!r} or {pin2!r}")

    r_earth = 6371.0
    lat1 = math.radians(c1["lat"])
    lon1 = math.radians(c1["lon"])
    lat2 = math.radians(c2["lat"])
    lon2 = math.radians(c2["lon"])

    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2.0) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return round(r_earth * c, 2)


def road_distance_km(pin1: str, pin2: str) -> float:
    """Compute road distance approximation in kilometers (haversine * 1.3)."""
    if pin1 == pin2:
        return 5.0
    return round(haversine_km(pin1, pin2) * 1.3, 2)
