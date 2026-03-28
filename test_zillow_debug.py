"""Diagnostic script to debug Zillow/pyzill connection issues."""

import sys

# 1. Check versions
print("=== VERSION CHECK ===")
try:
    import curl_cffi
    print(f"curl_cffi: {curl_cffi.__version__}")
except Exception as e:
    print(f"curl_cffi: ERROR - {e}")

try:
    import pyzill
    print(f"pyzill: {pyzill.__spec__}")
except Exception as e:
    print(f"pyzill: ERROR - {e}")

# 2. Make a raw request to see what Zillow returns
print("\n=== RAW ZILLOW REQUEST ===")
from curl_cffi import requests

headers = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en",
    "Content-Type": "application/json",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "origin": "https://www.zillow.com",
    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
}

body = {
    "searchQueryState": {
        "isMapVisible": True,
        "isListVisible": True,
        "mapBounds": {
            "north": 40.8333,
            "east": -111.7957,
            "south": 40.6883,
            "west": -111.9863,
        },
        "filterState": {
            "sortSelection": {"value": "globalrelevanceex"},
            "isAllHomes": {"value": True},
        },
        "mapZoom": 1,
        "pagination": {"currentPage": 1},
        "usersSearchTerm": "Salt Lake City, UT",
    },
    "wants": {
        "cat1": ["listResults", "mapResults"],
        "cat2": ["total"],
    },
    "requestId": 10,
    "isDebugRequest": False,
}

url = "https://www.zillow.com/async-create-search-page-state"

# Try with impersonation
for browser in ["chrome124", "chrome120", "chrome110", "chrome"]:
    try:
        print(f"\nTrying impersonate='{browser}'...")
        resp = requests.put(url, json=body, headers=headers, impersonate=browser)
        print(f"  Status: {resp.status_code}")
        print(f"  Content-Length: {len(resp.content)} bytes")
        print(f"  Content-Type: {resp.headers.get('content-type', 'N/A')}")
        if resp.content:
            text = resp.text[:300]
            print(f"  Body (first 300 chars): {text}")
            if resp.status_code == 200 and len(resp.content) > 100:
                try:
                    data = resp.json()
                    results = data.get("cat1", {}).get("searchResults", {})
                    map_r = results.get("mapResults", [])
                    list_r = results.get("listResults", [])
                    print(f"  SUCCESS! mapResults={len(map_r)}, listResults={len(list_r)}")
                    if map_r:
                        print(f"  First: {map_r[0].get('address', 'N/A')}")
                    break
                except Exception as e:
                    print(f"  JSON parse failed: {e}")
        else:
            print("  Body: EMPTY")
    except Exception as e:
        print(f"  Error: {type(e).__name__}: {e}")

# 3. Try without impersonation (plain request)
print("\n\nTrying plain request (no impersonation)...")
try:
    import httpx
    resp = httpx.Client(follow_redirects=True).put(url, json=body, headers=headers)
    print(f"  Status: {resp.status_code}")
    print(f"  Content-Length: {len(resp.content)} bytes")
    if resp.content:
        print(f"  Body (first 300 chars): {resp.text[:300]}")
except Exception as e:
    print(f"  Error: {type(e).__name__}: {e}")

print("\n=== DONE ===")
print("Share this output to diagnose the issue.")
