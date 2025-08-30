import httpx
from app.config.config import settings
from app.models.models import User



GOOGLE_URL = 'https://maps.googleapis.com/maps/api/geocode/json'
MAP_BOX = "https://api.mapbox.com/directions/v5/mapbox/driving"

async def get_vendor_coordinates_from_address(restaurant_location: str):
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{GOOGLE_URL}?address={restaurant_location}&key={settings.GOOGLE_MAP_API_KEY}"
        )
        response_data = response.json()
        
        # Extract coordinates from the response
        if response_data.get('status') == 'OK' and response_data.get('results'):
            location = response_data['results'][0]['geometry']['location']
            return {
                'lat': location['lat'],
                'lng': location['lng']
            }
        else:
            # Return None or raise an exception based on your error handling preference
            return None


async def distance_between_user_and_vendor(originLat: float, originLng: float, vendorLat: float, vendorLng: float):
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"{MAP_BOX}/{originLng},{originLat};{vendorLng},{vendorLat}?access_token={settings.MAPBOX_API_KEY}&geometries=geojson"
        )
        response_data = response.json()
        
        # Extract distance and duration from Mapbox Directions API response
        if response_data.get('code') == 'Ok' and response_data.get('routes'):
            route = response_data['routes'][0]
            return {
                'distance': route['distance'],  # in meters
                'duration': route['duration'],  # in seconds
                'distance_km': round(route['distance'] / 1000, 2),  # in kilometers
                'duration_minutes': round(route['duration'] / 60, 1)  # in minutes
            }
        else:
            # Handle error cases
            return None



async def get_distance_between_addresses(vendor_address: str, current_user: User):
    # Get coordinates for vendor location
    vendor_coords = await get_vendor_coordinates_from_address(vendor_address)
    
    # Get current user coordinates (assuming they're already stored)
    user_coords = current_user.current_user_location_coords
    
    if vendor_coords and user_coords:
        # Calculate distance using Mapbox
        distance_info = await distance_between_restaurant_and_vendor(
            user_coords['lat'], 
            user_coords['lng'],
            vendor_coords['lat'], 
            vendor_coords['lng']
        )
        if distance_info:
            return distance_info.get('distance_km', distance_info.get('distance', 0) / 1000)
        return None
    else:
        return None


# current_user_loation = {"latitude": 6.6752236, "longitude": 3.4216024}