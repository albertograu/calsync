#!/usr/bin/env python3
"""Simple iCloud CalDAV connection test"""

import os
import sys

# Simple .env parser
def load_env():
    env_vars = {}
    try:
        with open('.env', 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    env_vars[key] = value
    except FileNotFoundError:
        print("❌ .env file not found")
        sys.exit(1)
    return env_vars

env_vars = load_env()
username = env_vars.get('ICLOUD_USERNAME')
password = env_vars.get('ICLOUD_PASSWORD')

print(f"🔍 Testing iCloud CalDAV Connection")
print(f"Username: {username}")
print(f"Password: {'*' * len(password) if password else 'NOT SET'}")
print()

if not username or not password:
    print("❌ Missing iCloud credentials in .env file")
    exit(1)

# Test connection
print("🌐 Connecting to iCloud CalDAV...")
try:
    # Import here to avoid dependency issues if not installed
    try:
        import caldav
    except ImportError:
        print("❌ caldav module not found. Testing with basic HTTP...")
        import requests
        response = requests.get(
            "https://caldav.icloud.com",
            auth=(username, password)
        )
        if response.status_code == 401:
            raise Exception("401 Unauthorized - Bad credentials")
        elif response.status_code == 200:
            print("✅ Basic HTTP connection successful")
        else:
            print(f"⚠️ HTTP Status: {response.status_code}")
        exit(0)
    
    client = caldav.DAVClient(
        url="https://caldav.icloud.com",
        username=username,
        password=password
    )
    
    print("🔑 Getting principal...")
    principal = client.principal()
    print("✅ Successfully connected to iCloud CalDAV!")
    
    print("📅 Getting calendars...")
    calendars = principal.calendars()
    print(f"✅ Found {len(calendars)} calendars:")
    
    for i, cal in enumerate(calendars):
        try:
            props = cal.get_properties([caldav.dav.DisplayName()])
            name = props.get(caldav.dav.DisplayName.tag, f"Calendar {i+1}")
            print(f"  • {name} - {cal.url}")
        except Exception as e:
            print(f"  • Calendar {i+1} - {cal.url} (name error: {e})")
            
    print("\n🎉 iCloud connection test successful!")
    
except caldav.lib.error.AuthorizationError as e:
    print(f"❌ Authorization Error: {e}")
    print("\n🔧 Troubleshooting:")
    print("1. Verify your Apple ID email is correct")
    print("2. Generate a NEW app-specific password:")
    print("   • Go to: https://appleid.apple.com/account/manage")
    print("   • Sign in with your Apple ID")
    print("   • Go to 'App-Specific Passwords'") 
    print("   • Generate a new password for 'CalDAV' or 'Calendar'")
    print("   • Update your .env file with the new password")
    print("3. Make sure 2-Factor Authentication is enabled on your Apple ID")
    
except Exception as e:
    print(f"❌ Connection Error: {e}")
    print("\n🔧 Check your internet connection and credentials")