# helper_functions.py
import os
import requests
import time
import json

# --- Blizzard API Configuration (to be accessed by functions) ---
# These will be set by the calling script or should be accessible if this becomes a class
BLIZZARD_CLIENT_ID = os.environ.get('BLIZZARD_CLIENT_ID')
BLIZZARD_CLIENT_SECRET = os.environ.get('BLIZZARD_CLIENT_SECRET')
REGION = os.environ.get('REGION', 'us').lower() # Default to 'us' if not set

BLIZZARD_TOKEN_URL = f"https://{REGION}.battle.net/oauth/token"
BLIZZARD_API_BASE_URL = f"https://{REGION}.api.blizzard.com" # Used if not providing full URLs

# --- Warcraft Logs API Configuration ---
WCL_CLIENT_ID = os.environ.get('WCL_CLIENT_ID')
WCL_CLIENT_SECRET = os.environ.get('WCL_CLIENT_SECRET')
WCL_TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"
WCL_API_ENDPOINT = "https://www.warcraftlogs.com/api/v2/client"

# --- Caching (in-memory for the duration of the calling script's execution) ---
blizzard_access_token_cache = { "token": None, "expires_at": 0 }
wcl_access_token_cache = { "token": None, "expires_at": 0 }

def get_blizzard_access_token():
    """ Retrieves Blizzard access token, uses cache. """
    global blizzard_access_token_cache
    # Re-fetch ENV VARS in case they were not set at module import time
    # (e.g. if this module is imported before the main script sets them via a .env loader)
    local_blizzard_client_id = os.environ.get('BLIZZARD_CLIENT_ID')
    local_blizzard_client_secret = os.environ.get('BLIZZARD_CLIENT_SECRET')
    local_region = os.environ.get('REGION', 'us').lower()
    local_blizzard_token_url = f"https://{local_region}.battle.net/oauth/token"


    current_time = time.time()
    if blizzard_access_token_cache["token"] and blizzard_access_token_cache["expires_at"] > current_time + 60:
        return blizzard_access_token_cache["token"]

    if not local_blizzard_client_id or not local_blizzard_client_secret:
        print("Error: BLIZZARD_CLIENT_ID or BLIZZARD_CLIENT_SECRET not set.", flush=True)
        return None
    try:
        response = requests.post(
            local_blizzard_token_url, auth=(local_blizzard_client_id, local_blizzard_client_secret),
            data={'grant_type': 'client_credentials'}
        )
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get('access_token')
        expires_in = token_data.get('expires_in', 0)
        if not access_token:
            print(f"Error: Could not retrieve Blizzard access token. Response: {token_data}", flush=True)
            return None
        blizzard_access_token_cache["token"] = access_token
        blizzard_access_token_cache["expires_at"] = current_time + expires_in
        print(f"New Blizzard access token obtained.", flush=True)
        return access_token
    except requests.exceptions.RequestException as e:
        print(f"Error getting Blizzard access token: {e}", flush=True)
        if e.response is not None:
            print(f"Response Status: {e.response.status_code}", flush=True)
            try: print(f"Response Body: {e.response.json()}", flush=True)
            except: print(f"Response Body: {e.response.text}", flush=True)
        return None
    except Exception as e:
        print(f"An unexpected error during Blizzard token retrieval: {e}", flush=True)
        return None

def get_wcl_access_token():
    """ Retrieves Warcraft Logs access token, uses cache. """
    global wcl_access_token_cache
    local_wcl_client_id = os.environ.get('WCL_CLIENT_ID')
    local_wcl_client_secret = os.environ.get('WCL_CLIENT_SECRET')

    current_time = time.time()
    if wcl_access_token_cache["token"] and wcl_access_token_cache["expires_at"] > current_time + 60:
        return wcl_access_token_cache["token"]

    if not local_wcl_client_id or not local_wcl_client_secret:
        print("Error: WCL_CLIENT_ID or WCL_CLIENT_SECRET not set in environment variables.", flush=True)
        return None

    try:
        print(f"Attempting to get WCL token from: {WCL_TOKEN_URL}", flush=True)
        response = requests.post(
            WCL_TOKEN_URL,
            auth=(local_wcl_client_id, local_wcl_client_secret),
            data={'grant_type': 'client_credentials'}
        )
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get('access_token')
        expires_in = token_data.get('expires_in', 0)

        if not access_token:
            print(f"Error: Could not retrieve WCL access token. Response: {token_data}", flush=True)
            return None

        wcl_access_token_cache["token"] = access_token
        wcl_access_token_cache["expires_at"] = current_time + expires_in
        print(f"New Warcraft Logs access token obtained.", flush=True)
        return access_token
    except requests.exceptions.RequestException as e:
        print(f"Error getting WCL access token: {e}", flush=True)
        if e.response is not None:
            print(f"WCL Token Response Status: {e.response.status_code}", flush=True)
            try:
                print(f"WCL Token Response Body: {e.response.json()}", flush=True)
            except requests.exceptions.JSONDecodeError:
                print(f"WCL Token Response Body: {e.response.text}", flush=True)
        return None
    except Exception as e:
        print(f"An unexpected error during WCL token retrieval: {e}", flush=True)
        return None

def make_api_request(api_url, params, headers, is_wcl=False, wcl_query=None, wcl_variables=None, max_retries=3, retry_delay=5):
    """
    Helper function to make API GET (Blizzard) or POST (WCL GraphQL) requests.
    Includes retries for transient errors.
    """
    for attempt in range(max_retries):
        try:
            if is_wcl:
                if not wcl_query:
                    print("Error: WCL query missing for GraphQL request.", flush=True)
                    return None
                json_payload = {'query': wcl_query}
                if wcl_variables:
                    json_payload['variables'] = wcl_variables
                response = requests.post(api_url, json=json_payload, headers=headers, timeout=45) # Increased timeout for WCL
            else: # Blizzard API GET request
                response = requests.get(api_url, params=params, headers=headers, timeout=30)

            if response.status_code == 404:
                print(f"Warning: 404 Not Found for API URL: {response.url}", flush=True)
                return None # 404 is not typically a transient error to retry
            
            response.raise_for_status() # Raise for other HTTP errors (4xx client, 5xx server)
            return response.json()

        except requests.exceptions.Timeout:
            print(f"Timeout error during API request to {api_url}. Attempt {attempt + 1}/{max_retries}.", flush=True)
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                print(f"Max retries reached for timeout at {api_url}.", flush=True)
                return None
        except requests.exceptions.HTTPError as e:
            # Retry only on specific server-side errors (e.g., 500, 502, 503, 504)
            if e.response.status_code in [500, 502, 503, 504] and attempt < max_retries - 1:
                print(f"HTTP Error {e.response.status_code} for {api_url}. Attempt {attempt + 1}/{max_retries}. Retrying in {retry_delay}s...", flush=True)
                time.sleep(retry_delay)
            else:
                print(f"HTTP Error during API request: {e}", flush=True)
                print(f"URL attempted: {e.request.url}", flush=True)
                print(f"Response Status: {e.response.status_code}", flush=True)
                try: print(f"Response Body: {e.response.json()}", flush=True)
                except: print(f"Response Body: {e.response.text}", flush=True)
                return None # Do not retry other HTTP errors or if max retries reached
        except requests.exceptions.RequestException as e: # Covers other network issues
            print(f"Network error during API request: {e}. Attempt {attempt + 1}/{max_retries}.", flush=True)
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                print(f"Max retries reached for network error at {api_url}.", flush=True)
                return None
        except Exception as e:
            print(f"An unexpected error occurred during API request: {e}", flush=True)
            return None # Catch-all for other unexpected errors
    return None # Should be unreachable if loop completes, but good for safety

if __name__ == '__main__':
    # This script is intended to be imported, not run directly.
    # You can add test calls here if you want to test the functions independently.
    print("Helper functions script. Not intended for direct execution.", flush=True)
    # Example test (requires environment variables to be set):
    # print("Testing Blizzard Token Fetch...")
    # token = get_blizzard_access_token()
    # if token:
    #     print("Blizzard Token obtained successfully (first 10 chars):", token[:10])
    # else:
    #     print("Failed to obtain Blizzard Token.")

    # print("\nTesting WCL Token Fetch...")
    # wcl_token = get_wcl_access_token()
    # if wcl_token:
    #     print("WCL Token obtained successfully (first 10 chars):", wcl_token[:10])
    # else:
    #     print("Failed to obtain WCL Token.")
