import requests

from datetime import datetime, timedelta

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
        provided_key = request.META.get('HTTP_API_KEY')
        
        # Check if the key matches
        return provided_key == api_key


class PowerUsageCalculatorView(APIView):
    permission_classes = [HasAPIKey]
    
    def post(self, request, format=None):
        # Extract required parameters
        try:
            real_power = float(request.data.get('realPower'))
            start_date_str = request.data.get('startDate')
            end_date_str = request.data.get('endDate')
            lat = float(request.data.get('lat'))
            long = float(request.data.get('long'))
            intelligent_settings = request.data.get('intelligentSettings')
            
            # Validate required fields
            if not all([real_power, start_date_str, end_date_str, lat, long]):
                return Response(
                    {'error': 'Missing required parameters'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Parse dates
            start_date = datetime.fromisoformat(start_date_str.replace('Z', '+00:00'))
            end_date = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
            
            # Calculate usage and return results
            results, total_usage = self.calculate_energy_usage(
                real_power, start_date, end_date, lat, long, intelligent_settings
            )
            
            return Response({'results': results, 'totalUsage': total_usage})

        except ValueError as e:
            return Response(
                {'error': f'Invalid input data. {str(e)}'}, 
                status=status.HTTP_400_BAD_REQUEST
            )
            
        except Exception as e:
            return Response(
                {'error': str(e)}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    def calculate_energy_usage(self, real_power, start_date, end_date, lat, long, intelligent_settings=None):
        # Get all days in range
        days = self.get_days_in_range(start_date, end_date)
        
        # Fetch sunrise/sunset data for the entire date range in one request
        sunrise_sunset_data = self.get_sunrise_sunset_data_range(lat, long, start_date, end_date)
        
        # Group days by month
        months = {}
        for day in days:
            month_key = datetime(day.year, day.month, 1)
            if month_key not in months:
                months[month_key] = []
            months[month_key].append(day)
        
        results = []
        total_usage = 0
        
        # Process each month
        for month_start, month_days in months.items():
            monthly_usage = 0
            
            # Calculate usage for each day in the month
            for day in month_days:
                try:
                    day_str = day.strftime('%Y-%m-%d')
                    day_data = sunrise_sunset_data.get(day_str)
                    
                    if not day_data:
                        raise Exception(f"No sunrise/sunset data for {day_str}")
                    
                    # Calculate night hours
                    night_hours = self.calculate_night_hours(
                        day_data['sunrise'], 
                        day_data['sunset']
                    )
                    
                    # Base calculation (no intelligent settings)
                    daily_usage = real_power * night_hours
                    
                    # Apply intelligent settings if provided
                    if intelligent_settings:
                        percentage_of_total = intelligent_settings.get('percentageOfTotal')
                        dimming_power_percentage = intelligent_settings.get('dimmingPowerPercentage', 1)
                        dimming_time_percentage = intelligent_settings.get('dimmingTimePercentage', 0)
                        critical_percentage = intelligent_settings.get('criticalInfrastructurePercentage', 0)
                        
                        if percentage_of_total is not None:
                            # Calculate intelligent infrastructure component
                            intelligent_power = real_power * percentage_of_total
                            standard_power = real_power * (1 - percentage_of_total)
                            
                            # Calculate critical infrastructure (non-dimmable) component
                            critical_power = intelligent_power * critical_percentage
                            dimmable_power = intelligent_power - critical_power
                            
                            # Calculate usage with dimming applied
                            dimming_hours = night_hours * dimming_time_percentage
                            normal_hours = night_hours - dimming_hours
                            
                            dimmable_power_dimmed = dimmable_power * dimming_power_percentage
                            
                            daily_usage = (
                                # Standard infrastructure (always on at full power)
                                (standard_power * night_hours) +
                                # Critical infrastructure (always on at full power)
                                (critical_power * night_hours) +
                                # Dimmable infrastructure at normal hours
                                (dimmable_power * normal_hours) +
                                # Dimmable infrastructure at dimmed hours
                                (dimmable_power_dimmed * dimming_hours)
                            )
                    
                    monthly_usage += daily_usage
                except Exception as e:
                    # Log error and continue with other days
                    print(f"Error processing day {day.isoformat()}: {str(e)}")
            
            # Format date as ISO with time set to 00:00:00.000Z
            month_iso = month_start.strftime('%Y-%m-%dT00:00:00.000Z')
            rounded_usage = round(monthly_usage, 2)
            

            results.append({
                'date': month_iso,
                'usage': rounded_usage
            })
            total_usage += rounded_usage
        
        return results, total_usage
    
    def get_sunrise_sunset_data_range(self, lat, lng, start_date, end_date):
        """Fetch sunrise/sunset data for the entire date range in one request"""
        # Format dates for API
        start_str = start_date.strftime('%Y-%m-%d')
        end_str = end_date.strftime('%Y-%m-%d')
        
        # Make a single API request for the entire date range
        url = f"https://api.sunrisesunset.io/json?lat={lat}&lng={lng}&date_start={start_str}&date_end={end_str}"
        
        response = requests.get(url)
        data = response.json()
        
        if data.get('status') != 'OK':
            raise Exception('Failed to get sunrise/sunset data')
        
        # Organize data by date for easy lookup
        organized_data = {}
        for day_data in data.get('results', []):
            organized_data[day_data['date']] = day_data
        
        return organized_data
    
    def calculate_night_hours(self, sunrise_time, sunset_time):
        """Calculate night hours based on sunrise and sunset times"""
        # Parse times (like "7:12:40 AM")
        def parse_time(time_str):
            time, period = time_str.split(' ')
            hours, minutes, seconds = map(int, time.split(':'))
            
            if period == 'PM' and hours != 12:
                hours += 12
            if period == 'AM' and hours == 12:
                hours = 0
            
            return hours + (minutes / 60) + (seconds / 3600)
        
        sunrise = parse_time(sunrise_time)
        sunset = parse_time(sunset_time)
        
        # Night hours from midnight to sunrise and sunset to midnight
        return sunrise + (24 - sunset)
    
    def get_days_in_range(self, start_date, end_date):
        """Get all days in the date range, respecting partial months"""
        days = []
        current_date = start_date
        
        while current_date <= end_date:
            days.append(current_date)
            current_date += timedelta(days=1)
            
        return days
