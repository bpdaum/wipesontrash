# update_roster_data.py
import os
import requests
import time
from datetime import datetime
import json

# --- Standalone SQLAlchemy setup ---
from sqlalchemy import create_engine, Column, Integer, String, DateTime, UniqueConstraint, MetaData, Index
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.sql import func
from sqlalchemy.exc import OperationalError

# Get DB URI from environment or default to local SQLite
DATABASE_URI = os.environ.get('DATABASE_URL')
if not DATABASE_URI:
    print("WARNING: DATABASE_URL environment variable not found. Defaulting to local sqlite:///guild_data.db")
    DATABASE_URI = 'sqlite:///guild_data.db'
else:
    if DATABASE_URI.startswith("postgres://"):
        DATABASE_URI = DATABASE_URI.replace("postgres://", "postgresql://", 1)

try:
    engine = create_engine(DATABASE_URI)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base = declarative_base()
    metadata = MetaData()
except ImportError as e:
     print(f"Error: Database driver likely missing. {e}")
     exit(1)
except Exception as e:
     print(f"Error creating database engine: {e}")
     exit(1)

# --- Database Model ---
class Character(Base):
    __tablename__ = 'character'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    realm_slug = Column(String(100), nullable=False)
    level = Column(Integer)
    class_id = Column(Integer)
    class_name = Column(String(50))
    spec_name = Column(String(50))
    main_spec_override = Column(String(50), nullable=True)
    role = Column(String(10))
    status = Column(String(15), nullable=False, index=True)
    item_level = Column(Integer, index=True)
    raid_progression = Column(String(200))
    rank = Column(Integer, index=True)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = ( UniqueConstraint('name', 'realm_slug', name='_name_realm_uc'), )
    def __repr__(self): return f'<Character {self.name}-{self.realm_slug}>'

# --- Configuration Loading ---
BLIZZARD_CLIENT_ID = os.environ.get('BLIZZARD_CLIENT_ID')
BLIZZARD_CLIENT_SECRET = os.environ.get('BLIZZARD_CLIENT_SECRET')
GUILD_NAME = os.environ.get('GUILD_NAME')
REALM_SLUG = os.environ.get('REALM_SLUG')
REGION = os.environ.get('REGION', 'us').lower()

# --- Warcraft Logs Configuration ---
WCL_CLIENT_ID = os.environ.get('WCL_CLIENT_ID')
WCL_CLIENT_SECRET = os.environ.get('WCL_CLIENT_SECRET')
WCL_GUILD_ID = os.environ.get('WCL_GUILD_ID') # Your Warcraft Logs Guild ID

# --- Blizzard API Configuration ---
VALID_REGIONS = ['us', 'eu', 'kr', 'tw']
if REGION not in VALID_REGIONS: raise ValueError(f"Invalid REGION: {REGION}. Must be one of {VALID_REGIONS}")
BLIZZARD_TOKEN_URL = f"https://{REGION}.battle.net/oauth/token"
BLIZZARD_API_BASE_URL = f"https://{REGION}.api.blizzard.com"

# --- Warcraft Logs API Endpoints ---
WCL_TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"
WCL_API_ENDPOINT = "https://www.warcraftlogs.com/api/v2/client"


# --- Caching ---
blizzard_access_token_cache = { "token": None, "expires_at": 0 }
wcl_access_token_cache = { "token": None, "expires_at": 0 }
CLASS_MAP = {}
SPEC_MAP_BY_CLASS = {}

# --- API Helper Functions ---

def get_blizzard_access_token():
    """ Retrieves Blizzard access token, uses cache. """
    global blizzard_access_token_cache
    current_time = time.time()
    if blizzard_access_token_cache["token"] and blizzard_access_token_cache["expires_at"] > current_time + 60:
        return blizzard_access_token_cache["token"]
    if not BLIZZARD_CLIENT_ID or not BLIZZARD_CLIENT_SECRET:
        print("Error: BLIZZARD_CLIENT_ID or BLIZZARD_CLIENT_SECRET not set.")
        return None
    try:
        response = requests.post(
            BLIZZARD_TOKEN_URL, auth=(BLIZZARD_CLIENT_ID, BLIZZARD_CLIENT_SECRET),
            data={'grant_type': 'client_credentials'}
        )
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get('access_token')
        expires_in = token_data.get('expires_in', 0)
        if not access_token:
            print(f"Error: Could not retrieve Blizzard access token. Response: {token_data}")
            return None
        blizzard_access_token_cache["token"] = access_token
        blizzard_access_token_cache["expires_at"] = current_time + expires_in
        print(f"New Blizzard access token obtained.")
        return access_token
    except requests.exceptions.RequestException as e:
        print(f"Error getting Blizzard access token: {e}")
        if e.response is not None:
            print(f"Response Status: {e.response.status_code}")
            try: print(f"Response Body: {e.response.json()}")
            except: print(f"Response Body: {e.response.text}")
        return None
    except Exception as e:
        print(f"An unexpected error during Blizzard token retrieval: {e}")
        return None

def get_wcl_access_token():
    """ Retrieves Warcraft Logs access token, uses cache. """
    global wcl_access_token_cache
    current_time = time.time()
    if wcl_access_token_cache["token"] and wcl_access_token_cache["expires_at"] > current_time + 60:
        return wcl_access_token_cache["token"]

    if not WCL_CLIENT_ID or not WCL_CLIENT_SECRET:
        print("Error: WCL_CLIENT_ID or WCL_CLIENT_SECRET not set in environment variables.")
        return None

    try:
        print(f"Attempting to get WCL token from: {WCL_TOKEN_URL}")
        response = requests.post(
            WCL_TOKEN_URL,
            auth=(WCL_CLIENT_ID, WCL_CLIENT_SECRET),
            data={'grant_type': 'client_credentials'}
        )
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get('access_token')
        expires_in = token_data.get('expires_in', 0)

        if not access_token:
            print(f"Error: Could not retrieve WCL access token. Response: {token_data}")
            return None

        wcl_access_token_cache["token"] = access_token
        wcl_access_token_cache["expires_at"] = current_time + expires_in
        print(f"New Warcraft Logs access token obtained.")
        return access_token
    except requests.exceptions.RequestException as e:
        print(f"Error getting WCL access token: {e}")
        if e.response is not None:
            print(f"WCL Token Response Status: {e.response.status_code}")
            try:
                print(f"WCL Token Response Body: {e.response.json()}")
            except requests.exceptions.JSONDecodeError:
                print(f"WCL Token Response Body: {e.response.text}")
        return None
    except Exception as e:
        print(f"An unexpected error during WCL token retrieval: {e}")
        return None


def make_api_request(api_url, params, headers, is_wcl=False, wcl_query=None):
    """ Helper function to make API GET (Blizzard) or POST (WCL GraphQL) requests and handle common errors """
    try:
        if is_wcl: # GraphQL POST request for WCL
            if not wcl_query:
                print("Error: WCL query missing for GraphQL request.")
                return None
            json_payload = {'query': wcl_query}
            response = requests.post(api_url, json=json_payload, headers=headers, timeout=30)
        else: # REST GET request for Blizzard
            response = requests.get(api_url, params=params, headers=headers, timeout=30)

        if response.status_code == 404:
             print(f"Warning: 404 Not Found for URL: {response.url}")
             return None
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error during API request: {e}")
        print(f"URL attempted: {e.request.url}")
        print(f"Response Status: {e.response.status_code}")
        try: print(f"Response Body: {e.response.json()}")
        except: print(f"Response Body: {e.response.text}")
        return None
    except requests.exceptions.Timeout:
        print(f"Timeout error during API request to {api_url}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"Network error during API request: {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred during API request: {e}")
        return None


def get_static_data(endpoint, use_base_url=True):
    """ Fetches static data (classes, specs) from Blizzard API. """
    access_token = get_blizzard_access_token()
    if not access_token: return None
    if use_base_url:
        api_url = f"{BLIZZARD_API_BASE_URL}/data/wow{endpoint if endpoint.startswith('/') else '/' + endpoint}"
    else:
        api_url = endpoint
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"namespace": f"static-{REGION}", "locale": "en_US"}
    # print(f"Attempting Static Data URL: {api_url} with Namespace: {params['namespace']}") # Reduced verbosity
    data = make_api_request(api_url, params, headers)
    # if data and use_base_url: print(f"Successfully fetched static data from {endpoint}.")
    # elif not data: print(f"Failed to fetch static data from {endpoint or api_url}.")
    return data


def populate_spec_cache():
    """
    Populates the SPEC_MAP_BY_CLASS cache if empty.
    Fetches spec index, then fetches details for each spec to get class ID.
    """
    global SPEC_MAP_BY_CLASS
    if SPEC_MAP_BY_CLASS:
        return True

    print("Specialization map empty, attempting to fetch index...")
    spec_index_data = get_static_data('/playable-specialization/index')

    if not spec_index_data:
        print("Error: Failed to fetch playable specialization index.")
        return False

    spec_list_key = 'character_specializations'
    if spec_list_key not in spec_index_data:
        print(f"Error: '{spec_list_key}' key not found in the specialization index response.")
        return False

    spec_list = spec_index_data.get(spec_list_key, [])
    if not spec_list:
        print("Warning: Specialization list received from API is empty.")
        return False

    print(f"Fetched {len(spec_list)} specializations from index. Fetching details...")

    temp_spec_map = {}
    processed_count = 0
    fetch_errors = 0

    for spec_info_from_index in spec_list:
        spec_id = spec_info_from_index.get('id')
        spec_name = spec_info_from_index.get('name')
        detail_href = spec_info_from_index.get('key', {}).get('href')

        if not spec_id or not spec_name or not detail_href:
            print(f"Warning: Skipping spec entry in index due to missing data: {spec_info_from_index}")
            continue

        spec_detail_data = get_static_data(detail_href, use_base_url=False)
        processed_count += 1

        if not spec_detail_data:
            print(f"Warning: Failed to fetch details for spec ID {spec_id} ({spec_name}). Skipping.")
            fetch_errors += 1
            continue

        class_info = spec_detail_data.get('playable_class', {})
        class_id = class_info.get('id')

        if not class_id:
            print(f"Warning: Skipping spec {spec_name} because class ID was missing in detail response: {spec_detail_data}")
            fetch_errors += 1
            continue

        if class_id not in temp_spec_map:
            temp_spec_map[class_id] = []
        temp_spec_map[class_id].append({"id": spec_id, "name": spec_name})

        if processed_count % 10 == 0:
             print(f"Processed details for {processed_count}/{len(spec_list)} specs...")
        time.sleep(0.05) # Small delay

    # Sort specs within each class list now
    for cid in temp_spec_map:
        temp_spec_map[cid].sort(key=lambda x: x['name'])

    if not temp_spec_map:
        print("Error: Could not build specialization map (map is empty after processing details).")
        return False
    if fetch_errors > 0:
        print(f"Warning: Encountered {fetch_errors} errors fetching spec details.")

    SPEC_MAP_BY_CLASS = temp_spec_map
    print(f"Specialization map populated for {len(SPEC_MAP_BY_CLASS)} classes.")
    return True


def populate_static_caches():
    """ Populates CLASS_MAP and SPEC_MAP_BY_CLASS if empty. """
    global CLASS_MAP
    class_success = True
    spec_success = True

    if not CLASS_MAP:
        print("Class map empty, attempting to fetch...")
        class_data = get_static_data('/playable-class/index')
        if class_data and 'classes' in class_data:
            CLASS_MAP = {cls['id']: cls['name'] for cls in class_data['classes']}
            print(f"Class map populated with {len(CLASS_MAP)} entries.")
        else:
            print("Failed to fetch or parse playable class data.")
            class_success = False

    spec_success = populate_spec_cache()

    return class_success and spec_success


def get_guild_roster():
    """ Fetches the guild roster from Blizzard API. """
    if not GUILD_NAME or not REALM_SLUG:
        print("Error: Guild Name or Realm Slug not configured.")
        return None
    access_token = get_blizzard_access_token()
    if not access_token: return None
    realm_slug_lower = REALM_SLUG.lower()
    guild_name_segment = GUILD_NAME.lower().replace(' ', '-')
    api_url = f"{BLIZZARD_API_BASE_URL}/data/wow/guild/{realm_slug_lower}/{guild_name_segment}/roster"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"namespace": f"profile-{REGION}", "locale": "en_US"}
    print(f"Attempting Blizzard Guild Roster URL: {api_url}")
    data = make_api_request(api_url, params, headers)
    if data: print("Successfully fetched Blizzard guild roster.")
    else: print("Failed to fetch Blizzard guild roster.")
    return data

def get_character_summary(realm_slug, character_name):
    """ Fetches character profile summary from Blizzard API. """
    access_token = get_blizzard_access_token()
    if not access_token: return None
    realm_slug = realm_slug.lower()
    character_name = character_name.lower()
    api_url = f"{BLIZZARD_API_BASE_URL}/profile/wow/character/{realm_slug}/{character_name}"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"namespace": f"profile-{REGION}", "locale": "en_US"}
    data = make_api_request(api_url, params, headers)
    return data


def get_character_raid_progression(realm_slug, character_name):
    """ Fetches character raid encounters from Blizzard API. """
    access_token = get_blizzard_access_token()
    if not access_token: return None
    realm_slug = realm_slug.lower()
    character_name = character_name.lower()
    api_url = f"{BLIZZARD_API_BASE_URL}/profile/wow/character/{realm_slug}/{character_name}/encounters/raids"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"namespace": f"profile-{REGION}", "locale": "en_US"}
    data = make_api_request(api_url, params, headers)
    return data


def summarize_raid_progression(raid_data):
    """ Summarizes raid progression for 'Liberation of Undermine'. """
    target_expansion_name = "The War Within"
    target_raid_name = "Liberation of Undermine"
    short_raid_name = "Undermine"
    if not raid_data or 'expansions' not in raid_data: return None, -1
    heroic_kills = -1; heroic_total = 0; mythic_kills = -1; mythic_total = 0; raid_found = False
    for expansion in raid_data.get('expansions', []):
        exp_details = expansion.get('expansion', {})
        if exp_details.get('name') == target_expansion_name:
            for instance in expansion.get('instances', []):
                instance_details = instance.get('instance', {})
                if instance_details.get('name') == target_raid_name:
                    raid_found = True
                    for mode in instance.get('modes', []):
                        difficulty = mode.get('difficulty', {}); progress = mode.get('progress', {})
                        difficulty_type = difficulty.get('type')
                        if difficulty_type == "HEROIC":
                            heroic_kills = progress.get('completed_count', 0)
                            heroic_total = progress.get('total_count', 0)
                        elif difficulty_type == "MYTHIC":
                            mythic_kills = progress.get('completed_count', 0)
                            mythic_total = progress.get('total_count', 0)
                    break
            break
    if not raid_found: return f"{short_raid_name}: Not Found", -1
    summary_parts = []
    if heroic_kills != -1 and heroic_total > 0: summary_parts.append(f"{heroic_kills}/{heroic_total}H")
    if mythic_kills != -1 and mythic_total > 0: summary_parts.append(f"{mythic_kills}/{mythic_total}M")
    if not summary_parts: summary_output = f"{short_raid_name}: No H/M Data"
    else: summary_output = f"{short_raid_name}: {' '.join(summary_parts)}"
    hc_kills_return = 0 if heroic_kills == -1 else heroic_kills
    return summary_output, hc_kills_return

# --- Fetch WCL Guild Reports ---
def fetch_wcl_guild_reports():
    """Fetches recent raid reports for the guild from Warcraft Logs API."""
    if not WCL_GUILD_ID:
        print("Error: WCL_GUILD_ID not set in environment variables.")
        return None
    try:
        guild_id_int = int(WCL_GUILD_ID)
    except ValueError:
        print(f"Error: WCL_GUILD_ID '{WCL_GUILD_ID}' is not a valid integer.")
        return None

    access_token = get_wcl_access_token()
    if not access_token:
        print("Error: Cannot fetch WCL reports without a WCL access token.")
        return None

    query = f"""
    {{
        reportData {{
            reports(guildID: {guild_id_int}, limit: 10) {{
                data {{
                    code
                    title
                    startTime
                    endTime
                    owner {{ name }}
                }}
            }}
        }}
    }}
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    print(f"Attempting WCL Guild Reports from: {WCL_API_ENDPOINT}")
    data = make_api_request(WCL_API_ENDPOINT, params=None, headers=headers, is_wcl=True, wcl_query=query)

    if data and data.get('data', {}).get('reportData', {}).get('reports', {}).get('data'):
        print(f"Successfully fetched {len(data['data']['reportData']['reports']['data'])} WCL reports.")
        return data['data']['reportData']['reports']['data']
    else:
        print("Failed to fetch or parse WCL guild reports.")
        if data: print(f"WCL Response (or error part): {json.dumps(data, indent=2)}")
        return None

# --- END API Helper Functions ---


# --- Database Update Logic ---
def update_database():
    """ Fetches all data from Blizzard API and updates the database. """
    print("Starting database update process...")
    start_time = time.time()

    # --- Drop and Recreate Table ---
    try:
        print(f"Attempting to drop table '{Character.__tablename__}' if it exists...")
        Base.metadata.bind = engine
        Character.__table__.drop(engine, checkfirst=True)
        print(f"Table '{Character.__tablename__}' dropped (or did not exist).")
        print(f"Creating table '{Character.__tablename__}'...")
        Base.metadata.create_all(bind=engine)
        print("Table created successfully.")
    except OperationalError as e:
         print(f"Database connection error during drop/create: {e}. Check DATABASE_URL and network.")
         return
    except Exception as e:
        print(f"Error during table drop/create: {e}")
        return
    # --- END Drop and Recreate ---

    if not populate_static_caches():
        print("Error: Failed to populate static caches. Aborting update.")
        return

    roster_data = get_guild_roster()
    if not roster_data or 'members' not in roster_data:
        print("Error: Failed to fetch guild roster. Aborting update.")
        return

    total_members = len(roster_data['members'])
    print(f"Fetched {total_members} total members from roster. Filtering by rank <= 4...")

    characters_to_insert = []
    api_call_count = 0
    processed_for_details = 0

    for member_entry in roster_data['members']:
        character_info = member_entry.get('character', {})
        rank = member_entry.get('rank')
        char_id = character_info.get('id')
        char_name = character_info.get('name')
        char_realm_slug = character_info.get('realm', {}).get('slug')

        if rank is None or rank > 4 or not char_id or not char_name or not char_realm_slug:
            continue

        processed_for_details += 1
        if processed_for_details % 10 == 1 or processed_for_details == total_members:
             print(f"\nProcessing details for {char_name}-{char_realm_slug} (Rank {rank})... ({processed_for_details}/{total_members} checked)")

        class_id = character_info.get('playable_class', {}).get('id')
        class_name = CLASS_MAP.get(class_id, f"ID: {class_id}" if class_id else "N/A")

        item_level = None
        raid_progression_summary = None
        spec_name = None
        role = None
        main_spec_override = None
        heroic_kills = -1

        summary_data = get_character_summary(char_realm_slug, char_name)
        api_call_count += 1
        if summary_data:
            ilvl_raw = summary_data.get('average_item_level')
            item_level = int(ilvl_raw) if isinstance(ilvl_raw, (int, float)) else None
            active_spec_data = summary_data.get('active_spec')
            if active_spec_data and isinstance(active_spec_data, dict):
                spec_name = active_spec_data.get('name')
                try: # Determine role
                    spec_type = None
                    if 'type' in active_spec_data: spec_type = active_spec_data.get('type', '').upper()
                    elif 'media' in active_spec_data and isinstance(active_spec_data['media'], dict): spec_type = active_spec_data['media'].get('type', '').upper()
                    if spec_type == 'HEALING': role = 'Healer'
                    elif spec_type == 'TANK': role = 'Tank'
                    elif spec_type == 'DAMAGE': role = 'DPS'
                    else: # Fallback
                        if spec_name in ["Blood", "Protection", "Guardian", "Brewmaster", "Vengeance"]: role = "Tank"
                        elif spec_name in ["Holy", "Discipline", "Restoration", "Mistweaver", "Preservation"]: role = "Healer"
                        elif spec_name: role = "DPS"
                except Exception as spec_err: print(f"Warning: Could not determine role for {char_name}: {spec_err}")
            # print(f"DEBUG: For {char_name}: API Spec='{spec_name}', Role='{role}'")

        raid_data = get_character_raid_progression(char_realm_slug, char_name)
        api_call_count += 1
        if raid_data:
            raid_progression_summary, heroic_kills = summarize_raid_progression(raid_data)
            if raid_progression_summary is None or "Not Found" in raid_progression_summary or "No H/M Data" in raid_progression_summary:
                 raid_progression_summary = None
        else:
            raid_progression_summary = None
            heroic_kills = -1

        # Calculate Initial Status
        calculated_status = "Member" # Default
        if item_level is None or item_level < 650:
            calculated_status = "Wiping Alt"
        elif heroic_kills > 6 :
             calculated_status = "Wiper"
        elif heroic_kills >= 0 and heroic_kills <= 6:
             calculated_status = "Member"
        # print(f"DEBUG: For {char_name}: iLvl={item_level}, HKills={heroic_kills} -> Initial Status='{calculated_status}'")

        # print(f"DEBUG: For {char_name}: Preparing Item Level = {item_level}, Raid Progression = '{raid_progression_summary}', Spec = '{spec_name}', Role = '{role}', ClassID = {class_id}, Status = '{calculated_status}'")

        characters_to_insert.append(Character(
            id=char_id, name=char_name, realm_slug=char_realm_slug, level=character_info.get('level'),
            class_id=class_id, class_name=class_name,
            spec_name=spec_name, main_spec_override=None, role=role,
            status=calculated_status, # Use calculated status as initial value
            item_level=item_level, raid_progression=raid_progression_summary, rank=rank
        ))

    print(f"\nFetched details for {len(characters_to_insert)} members (Rank <= 4). Made {api_call_count} API calls.")

    # --- Insert Data ---
    db_session = SessionLocal()
    try:
        print(f"Inserting {len(characters_to_insert)} characters into the database...")
        if characters_to_insert:
             db_session.add_all(characters_to_insert)
             db_session.commit()
             print(f"Database insert complete: {len(characters_to_insert)} inserted.")
        else:
             print("No characters met the criteria to be inserted.")
    except OperationalError as e:
        print(f"Database connection error during insert: {e}. Check DATABASE_URL and network.")
        db_session.rollback()
    except Exception as e:
        print(f"Error during database insert: {e}")
        db_session.rollback()
    finally:
        db_session.close()

    # --- Fetch and Print WCL Reports ---
    print("\n--- Fetching Warcraft Logs Reports ---")
    wcl_reports = fetch_wcl_guild_reports()
    if wcl_reports:
        print(f"Found {len(wcl_reports)} recent WCL reports:")
        for report in wcl_reports:
            start_time_ms = report.get('startTime', 0)
            start_date = datetime.fromtimestamp(start_time_ms / 1000).strftime('%Y-%m-%d %H:%M')
            print(f"  - Title: {report.get('title')}, Code: {report.get('code')}, Start: {start_date}, Owner: {report.get('owner',{}).get('name')}")
    else:
        print("No WCL reports found or an error occurred.")
    # --- END: Fetch WCL Reports ---

    end_time = time.time()
    print(f"\nUpdate process finished in {round(end_time - start_time, 2)} seconds.")


# --- Main Execution ---
if __name__ == "__main__":
    required_vars = ['BLIZZARD_CLIENT_ID', 'BLIZZARD_CLIENT_SECRET', 'GUILD_NAME', 'REALM_SLUG', 'REGION', 'DATABASE_URL', 'WCL_CLIENT_ID', 'WCL_CLIENT_SECRET', 'WCL_GUILD_ID']
    print(f"Checking environment variables...")
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    if missing_vars:
        print(f"Error: Missing required environment variables: {', '.join(missing_vars)}")
        if missing_vars == ['DATABASE_URL'] and DATABASE_URI.startswith('sqlite:///'):
             print("Attempting to use default local SQLite DB: guild_data.db")
             api_keys_missing = [var for var in required_vars if not os.environ.get(var) and var != 'DATABASE_URL']
             if api_keys_missing:
                  print(f"Error: Missing API environment variables needed for fetch: {', '.join(api_keys_missing)}")
                  exit(1)
             else:
                  update_database()
        else:
             exit(1)
    else:
        print("All required environment variables found.")
        update_database()
