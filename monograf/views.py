import json
import pytz
import requests

from calendar import monthrange
from datetime import date, datetime, timedelta
from dateutil import parser
from dateutil.relativedelta import relativedelta

from django.utils import timezone
from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import BasePermission


class HasAPIKey(BasePermission):
    """
    Custom permission to check if the request has a valid API key.
    """
    def has_permission(self, request, view):
        # Get API key from environment variable (or set it directly here, less secure)
        api_key = settings.API_KEY
        
        # Get the provided API key from the request header
        provided_key = request.META.get('API_KEY')
        
        # Check if the key matches
        return provided_key == api_key


class PowerUsageCalculatorView(APIView):
    permission_classes = [HasAPIKey]
    
    def post(self, request, format=None):
        try:
            data = request.data
            
            # Extract and validate required fields
            real_power = data.get('realPower')
            start_date_str = data.get('startDate')
            end_date_str = data.get('endDate')
            latitude = data.get('lat')
            longitude = data.get('long')
            
            if not all([real_power, start_date_str, end_date_str, latitude, longitude]):
                return Response(
                    {"error": "Missing required fields: realPower, startDate, endDate, lat, or long"}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Parse dates
            start_date = parser.parse(start_date_str).replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = parser.parse(end_date_str).replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Extract intelligent settings if provided
            intelligent_settings = data.get('intelligentSettings', {})
            percentage_of_total = intelligent_settings.get('percentageOfTotal', 0)
            dimming_power_percentage = intelligent_settings.get('dimmingPowerPercentage', 0)
            dimming_time_percentage = intelligent_settings.get('dimmingTimePercentage', 0)
            critical_infra_percentage = intelligent_settings.get('criticalInfrastructurePercentage', 0)
            
            # Calculate monthly usage with sunrise/sunset data
            results = self._calculate_monthly_usage(
                real_power, 
                start_date, 
                end_date,
                latitude,
                longitude,
                percentage_of_total,
                dimming_power_percentage,
                dimming_time_percentage,
                critical_infra_percentage
            )
            
            return Response({"results": results})
            
        except Exception as e:
            return Response(
                {"error": str(e)}, 
                status=status.HTTP_400_BAD_REQUEST
            )
    
    def _get_daylight_hours(self, lat, lng, month_date):
        """
        Get average daylight hours for a given month using the sunrise-sunset API.
        Uses the 15th day of the month as a representative day.
        """
        # Use the middle of the month as representative
        sample_date = month_date.replace(day=15)
        
        try:
            # Make API request to get sunrise/sunset data
            url = f"https://api.sunrisesunset.io/json?lat={lat}&lng={lng}&date={sample_date.strftime('%Y-%m-%d')}"
            response = requests.get(url, timeout=10)
            data = response.json()
            
            if data['status'] == 'OK':
                # Parse daylight hours from API response
                day_length = data['results']['day_length']
                # Convert "HH:MM:SS" to hours as float
                hours, minutes, seconds = map(int, day_length.split(':'))
                daylight_hours = hours + (minutes / 60) + (seconds / 3600)
                return daylight_hours
            else:
                # Fallback to average if API fails
                return 12  # Default fallback value
        except Exception as e:
            # Use approximate daylight hours based on month and latitude as fallback
            # This is a simplistic model if the API call fails
            month = month_date.month
            abs_lat = abs(lat)
            
            # Very simplistic daylight model - just a reasonable fallback
            if month in [12, 1, 2]:  # Winter
                return max(8, 12 - abs_lat/10)
            elif month in [6, 7, 8]:  # Summer
                return min(16, 12 + abs_lat/10)
            else:  # Spring/Fall
                return 12
    
    def _calculate_monthly_usage(self, 
                               real_power, 
                               start_date, 
                               end_date,
                               latitude,
                               longitude,
                               percentage_of_total=0,
                               dimming_power_percentage=0,
                               dimming_time_percentage=0,
                               critical_infra_percentage=0):
        """
        Calculate energy usage for each month between start_date and end_date.
        Uses sunrise/sunset data to determine actual daylight hours.
        Takes into account intelligent infrastructure settings if provided.
        
        Returns list of dictionaries with date and usage in kWh.
        """
        results = []
        current_date = start_date.replace(day=1)  # Start from 1st of month
        
        while current_date <= end_date:
            # Calculate days in current month that fall within the date range
            days_in_month = monthrange(current_date.year, current_date.month)[1]
            month_end = current_date.replace(day=days_in_month)
            
            # Adjust for start and end dates
            if current_date.month == start_date.month and current_date.year == start_date.year:
                days_to_count = days_in_month - start_date.day + 1
            elif current_date.month == end_date.month and current_date.year == end_date.year:
                days_to_count = end_date.day
            else:
                days_to_count = days_in_month
            
            # Get daylight hours for this month and location
            daylight_hours = self._get_daylight_hours(latitude, longitude, current_date)
            night_hours = 24 - daylight_hours
            
            # Basic calculation: power (kW) * 24 hours * days = energy (kWh)
            if percentage_of_total > 0:
                # Calculate with intelligent infrastructure
                standard_infra = real_power * (1 - percentage_of_total)
                intelligent_infra = real_power * percentage_of_total
                critical_infra = intelligent_infra * critical_infra_percentage
                dimmable_infra = intelligent_infra * (1 - critical_infra_percentage)
                
                # Calculate dimmed hours based on nighttime
                dimmed_hours = night_hours * dimming_time_percentage
                normal_hours = 24 - dimmed_hours
                
                # Calculate usage
                # Standard infrastructure runs 24/7 at full power
                standard_usage = standard_infra * 24 * days_to_count
                
                # Critical infrastructure runs 24/7 at full power
                critical_usage = critical_infra * 24 * days_to_count
                
                # Dimmable infrastructure runs at full power during normal hours
                # and at reduced power during dimmed hours
                dimmable_normal_usage = dimmable_infra * normal_hours * days_to_count
                dimmable_dimmed_usage = dimmable_infra * dimming_power_percentage * dimmed_hours * days_to_count
                
                total_usage = int(standard_usage + critical_usage + dimmable_normal_usage + dimmable_dimmed_usage)
            else:
                # Simple calculation without intelligent infrastructure
                total_usage = int(real_power * 24 * days_to_count)
            
            # Format the result
            results.append({
                "date": current_date.strftime('%Y-%m-%dT00:00:00.000Z'),
                "usage": total_usage
            })
            
            # Move to next month
            current_date = (current_date + relativedelta(months=1))
        
        return results
