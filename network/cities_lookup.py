"""Indian city and pincode coordinate lookup with haversine * 1.3 road distance calculation."""

from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_KM = 6371.0088
ROAD_FACTOR = 1.3


@dataclass(frozen=True)
class CityCoord:
    city: str
    state_code: str
    pincode: str
    lat: float
    lon: float


# Built-in reference coordinates for major Indian commercial/industrial cities
_BUILTIN_CITIES: tuple[CityCoord, ...] = (
    CityCoord("Mumbai", "27", "400001", 18.9388, 72.8354),
    CityCoord("Mumbai Suburban", "27", "400050", 19.0760, 72.8777),
    CityCoord("Navi Mumbai", "27", "400703", 19.0330, 73.0297),
    CityCoord("Thane", "27", "400601", 19.2183, 72.9781),
    CityCoord("Pune", "27", "411001", 18.5204, 73.8567),
    CityCoord("Nashik", "27", "422001", 19.9975, 73.7898),
    CityCoord("Aurangabad", "27", "431001", 19.8762, 75.3433),
    CityCoord("Nagpur", "27", "440001", 21.1458, 79.0882),
    CityCoord("Delhi", "07", "110001", 28.6139, 77.2090),
    CityCoord("Gurugram", "06", "122001", 28.4595, 77.0266),
    CityCoord("Faridabad", "06", "121001", 28.4089, 77.3178),
    CityCoord("Noida", "09", "201301", 28.5355, 77.3910),
    CityCoord("Ghaziabad", "09", "201001", 28.6692, 77.4538),
    CityCoord("Kanpur", "09", "208001", 26.4499, 80.3319),
    CityCoord("Lucknow", "09", "226001", 26.8467, 80.9462),
    CityCoord("Varanasi", "09", "221001", 25.3176, 82.9739),
    CityCoord("Bengaluru", "29", "560001", 12.9716, 77.5946),
    CityCoord("Mysuru", "29", "570001", 12.2958, 76.6394),
    CityCoord("Mangaluru", "29", "575001", 12.9141, 74.8560),
    CityCoord("Hubballi", "29", "580020", 15.3647, 75.1240),
    CityCoord("Chennai", "33", "600001", 13.0827, 80.2707),
    CityCoord("Coimbatore", "33", "641001", 11.0168, 76.9558),
    CityCoord("Madurai", "33", "625001", 9.9252, 78.1198),
    CityCoord("Tiruchirappalli", "33", "620001", 10.7905, 78.7047),
    CityCoord("Salem", "33", "636001", 11.6643, 78.1460),
    CityCoord("Hyderabad", "36", "500001", 17.3850, 78.4867),
    CityCoord("Warangal", "36", "506002", 17.9689, 79.5941),
    CityCoord("Visakhapatnam", "37", "530001", 17.6868, 83.2185),
    CityCoord("Vijayawada", "37", "520001", 16.5062, 80.6480),
    CityCoord("Ahmedabad", "24", "380001", 23.0225, 72.5714),
    CityCoord("Surat", "24", "395001", 21.1702, 72.8311),
    CityCoord("Vadodara", "24", "390001", 22.3072, 73.1812),
    CityCoord("Rajkot", "24", "360001", 22.3039, 70.8022),
    CityCoord("Bharuch", "24", "392001", 21.7051, 72.9959),
    CityCoord("Vapi", "24", "396191", 20.3893, 72.9106),
    CityCoord("Kolkata", "19", "700001", 22.5726, 88.3639),
    CityCoord("Howrah", "19", "711101", 22.5958, 88.2636),
    CityCoord("Durgapur", "19", "713201", 23.5204, 87.3119),
    CityCoord("Siliguri", "19", "734001", 26.7271, 88.3953),
    CityCoord("Jaipur", "08", "302001", 26.9124, 75.7873),
    CityCoord("Jodhpur", "08", "342001", 26.2389, 73.0243),
    CityCoord("Udaipur", "08", "313001", 24.5854, 73.7125),
    CityCoord("Kota", "08", "324001", 25.2138, 75.8648),
    CityCoord("Bhiwadi", "08", "301019", 28.2088, 76.8446),
    CityCoord("Indore", "23", "452001", 22.7196, 75.8577),
    CityCoord("Bhopal", "23", "462001", 23.2599, 77.4126),
    CityCoord("Jabalpur", "23", "482001", 23.1815, 79.9864),
    CityCoord("Gwalior", "23", "474001", 26.2183, 78.1828),
    CityCoord("Ludhiana", "03", "141001", 30.9010, 75.8573),
    CityCoord("Amritsar", "03", "143001", 31.6340, 74.8723),
    CityCoord("Jalandhar", "03", "144001", 31.3260, 75.5762),
    CityCoord("Chandigarh", "04", "160001", 30.7333, 76.7794),
    CityCoord("Kochi", "32", "682001", 9.9312, 76.2673),
    CityCoord("Thiruvananthapuram", "32", "695001", 8.5241, 76.9366),
    CityCoord("Kozhikode", "32", "673001", 11.2588, 75.7804),
    CityCoord("Bhubaneswar", "21", "751001", 20.2961, 85.8245),
    CityCoord("Cuttack", "21", "753001", 20.4625, 85.8830),
    CityCoord("Rourkela", "21", "769001", 22.2604, 84.8536),
    CityCoord("Patna", "10", "800001", 25.5941, 85.1376),
    CityCoord("Jamshedpur", "20", "831001", 22.8046, 86.2029),
    CityCoord("Ranchi", "20", "834001", 23.3441, 85.3096),
    CityCoord("Raipur", "22", "492001", 21.2514, 81.6296),
    CityCoord("Bhilai", "22", "490001", 21.1938, 81.3509),
    CityCoord("Guwahati", "18", "781001", 26.1445, 91.7362),
    CityCoord("Panaji", "30", "403001", 15.4909, 73.8278),
    CityCoord("Dehradun", "05", "248001", 30.3165, 78.0322),
    CityCoord("Haridwar", "05", "249401", 29.9457, 78.1642),
    CityCoord("Baddi", "02", "173205", 30.9578, 76.7914),
    CityCoord("Jammu", "01", "180001", 32.7266, 74.8570),
)

_EXACT_PINCODE: dict[str, CityCoord] = {}
_PREFIX3_PINCODE: dict[str, CityCoord] = {}
_PREFIX2_PINCODE: dict[str, CityCoord] = {}
_CITY_NAME_MAP: dict[str, CityCoord] = {}


def _register_coord(coord: CityCoord) -> None:
    pin = (coord.pincode or "").strip()
    if pin:
        _EXACT_PINCODE.setdefault(pin, coord)
        if len(pin) >= 3:
            _PREFIX3_PINCODE.setdefault(pin[:3], coord)
        if len(pin) >= 2:
            _PREFIX2_PINCODE.setdefault(pin[:2], coord)
    if coord.city:
        _CITY_NAME_MAP.setdefault(coord.city.strip().lower(), coord)


for _c in _BUILTIN_CITIES:
    _register_coord(_c)

# Optional fallback hook to data.generator.cities if available in runtime environment
try:
    import data.generator.cities as _gen_cities  # type: ignore[import-not-found]

    for _attr in ("CITIES", "CITY_LIST", "PINCODE_MAP", "cities"):
        _obj = getattr(_gen_cities, _attr, None)
        if isinstance(_obj, dict):
            for _k, _v in _obj.items():
                if isinstance(_v, dict) and "lat" in _v and "lon" in _v:
                    _register_coord(
                        CityCoord(
                            city=str(_v.get("city", _v.get("name", _k))),
                            state_code=str(_v.get("state_code", "")),
                            pincode=str(_v.get("pincode", _k)),
                            lat=float(_v["lat"]),
                            lon=float(_v["lon"]),
                        )
                    )
                elif hasattr(_v, "lat") and hasattr(_v, "lon"):
                    _register_coord(
                        CityCoord(
                            city=str(getattr(_v, "city", getattr(_v, "name", _k))),
                            state_code=str(getattr(_v, "state_code", "")),
                            pincode=str(getattr(_v, "pincode", _k)),
                            lat=float(getattr(_v, "lat")),
                            lon=float(getattr(_v, "lon")),
                        )
                    )
        elif isinstance(_obj, (list, tuple)):
            for _item in _obj:
                if isinstance(_item, dict) and "lat" in _item and "lon" in _item:
                    _register_coord(
                        CityCoord(
                            city=str(_item.get("city", _item.get("name", ""))),
                            state_code=str(_item.get("state_code", "")),
                            pincode=str(_item.get("pincode", "")),
                            lat=float(_item["lat"]),
                            lon=float(_item["lon"]),
                        )
                    )
                elif hasattr(_item, "lat") and hasattr(_item, "lon"):
                    _register_coord(
                        CityCoord(
                            city=str(getattr(_item, "city", getattr(_item, "name", ""))),
                            state_code=str(getattr(_item, "state_code", "")),
                            pincode=str(getattr(_item, "pincode", "")),
                            lat=float(getattr(_item, "lat")),
                            lon=float(getattr(_item, "lon")),
                        )
                    )
except Exception:
    pass


def resolve_pincode(pincode: str | None) -> CityCoord | None:
    """Resolve an Indian pincode (or city name fallback) to CityCoord."""
    if not pincode:
        return None
    s = pincode.strip()
    if not s:
        return None
    if s in _EXACT_PINCODE:
        return _EXACT_PINCODE[s]
    if len(s) >= 3 and s[:3] in _PREFIX3_PINCODE:
        base = _PREFIX3_PINCODE[s[:3]]
        return CityCoord(city=base.city, state_code=base.state_code, pincode=s, lat=base.lat, lon=base.lon)
    if len(s) >= 2 and s[:2] in _PREFIX2_PINCODE:
        base = _PREFIX2_PINCODE[s[:2]]
        return CityCoord(city=base.city, state_code=base.state_code, pincode=s, lat=base.lat, lon=base.lon)
    low = s.lower()
    if low in _CITY_NAME_MAP:
        return _CITY_NAME_MAP[low]
    return None


def resolve_city(city_name: str | None) -> CityCoord | None:
    if not city_name:
        return None
    return _CITY_NAME_MAP.get(city_name.strip().lower())


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometers between two (lat, lon) coordinates."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return EARTH_RADIUS_KM * c


def road_distance_km(
    from_pincode: str | None,
    to_pincode: str | None,
    road_factor: float = ROAD_FACTOR,
) -> float | None:
    """Estimated road distance (haversine * 1.3 road factor) between two pincodes."""
    if not from_pincode or not to_pincode:
        return None
    if from_pincode.strip() == to_pincode.strip():
        return 0.0
    c1 = resolve_pincode(from_pincode)
    c2 = resolve_pincode(to_pincode)
    if c1 is None or c2 is None:
        return None
    return haversine_km(c1.lat, c1.lon, c2.lat, c2.lon) * road_factor
