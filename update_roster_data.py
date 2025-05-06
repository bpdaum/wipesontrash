# update_roster_data.py
import os
import requests
import time
from datetime import datetime
import json

# --- Standalone SQLAlchemy setup for PostgreSQL/SQLite ---
from sqlalchemy import create_engine, Column, Integer, String, DateTime, UniqueConstraint, MetaData, Index
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.sql import func # for current_timestamp
from sqlalchemy.exc import OperationalError # To catch DB connection errors

# Get DB URI from environment or default to local SQLite
DATABASE_URI = os.environ.get('DATABASE_URL')
if not DATABASE_URI:
    print("WARNING: DATABASE_URL environment variable not found. Defaulting to local sqlite:///guild_data.db")
    DATABASE_URI = 'sqlite:///guild_data.db'
else:
    # Heroku Postgres URLs start with 'postgres://', SQLAlchemy prefers 'postgresql://'
    if DATABASE_URI.startswith("postgres://"):
        DATABASE_URI = DATABASE_URI.replace("postgres://", "postgresql://", 1)

try:
    engine = create_engine(DATABASE_URI)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base = declarative_base()
    metadata = MetaData() # Use MetaData for table existence check
except ImportError as e:
     print(f"Error: Database driver likely missing. Did you 'pip install psycopg2-binary' (for Postgres) or ensure SQLAlchemy is installed? Details: {e}")
     exit(1) # Exit if driver is missing
except Exception as e:
     print(f"Error creating database engine: {e}")
     exit(1)

# --- Database Model ---
# Includes class_id, status; removes race_name
class Character(Base):
    """ Defines the structure for storing character data in the database. """
    __tablename__ = 'character'
    id = Column(Integer, primary_key=True) # Use Blizzard's character ID
    name = Column(String(100), nullable=False)
    realm_slug = Column(String(100), nullable=False)
    level = Column(Integer)
    class_id = Column(Integer) # Store the class ID
    class_name = Column(String(50))
    # race_name removed
    spec_name = Column(String(50)) # API Active Spec
    main_spec_override = Column(String(50), nullable=True) # User override
    role = Column(String(10))      # Role (Tank, Healer, DPS)
    status = Column(String(15), nullable=False, index=True) # Calculated Status field
    item_level = Column(Integer, index=True)
    raid_progression = Column(String(200))
    rank = Column(Integer, index=True)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow) # Use utcnow

    # Define a unique constraint on name and realm_slug
    __table_args__ = ( UniqueConstraint('name', 'realm_slug', name='_name_realm_uc'), )
    def __repr__(self): return f'<Character {self.name}-{self.realm_slug}>'

# --- Configuration Loading ---
BLIZZARD_CLIENT_ID = os.environ.get('BLIZZARD_CLIENT_ID')
BLIZZARD_CLIENT_SECRET = os.environ.get('BLIZZARD_CLIENT_SECRET')
GUILD_NAME = os.environ.get('GUILD_NAME')
REALM_SLUG = os.environ.get('REALM_SLUG')
REGION = os.environ.get('REGION', 'us').lower()

# --- Blizzard API Configuration ---
VALID_REGIONS = ['us', 'eu', 'kr', 'tw']
if REGION not in VALID_REGIONS: raise ValueError(f"Invalid REGION: {REGION}. Must be one of {VALID_REGIONS}")
TOKEN_URL = f"https://{REGION}.battle.net/oauth/token"
API_BASE_URL = f"https://{REGION}.api.blizzard.com"

# --- Caching (For API calls within this script run) ---
access_token_cache = { "token": None, "expires_at": 0 }
CLASS_MAP = {}
# RACE_MAP = {} # Removed
SPEC_MAP_BY_CLASS = {} # Cache for all specs {class_id: [{id: spec_id, name: spec_name}, ...]}

# --- API Helper Functions ---

def get_blizzard_access_token():
    """ Retrieves Blizzard access token, uses cache. """
    global access_token_cache
    current_time = time.time()
    if access_token_cache["token"] and access_token_cache["expires_at"] > current_time + 60:
        return access_token_cache["token"]
    if not BLIZZARD_CLIENT_ID or not BLIZZARD_CLIENT_SECRET:
        print("Error: BLIZZARD_CLIENT_ID or BLIZZARD_CLIENT_SECRET not set.")
        return None
    try:
        response = requests.post(
            TOKEN_URL, auth=(BLIZZARD_CLIENT_ID, BLIZZARD_CLIENT_SECRET),
            data={'grant_type': 'client_credentials'}
        )
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get('access_token')
        expires_in = token_data.get('expires_in', 0)
        if not access_token:
            print(f"Error: Could not retrieve access token. Response: {token_data}")
            return None
        access_token_cache["token"] = access_token
        access_token_cache["expires_at"] = current_time + expires_in
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
        print(f"An unexpected error during token retrieval: {e}")
        return None

def make_api_request(api_url, params, headers):
    """ Helper function to make API GET requests and handle common errors """
    try:
        response = requests.get(api_url, params=params, headers=headers, timeout=30) # Added timeout
        if response.status_code == 404:
             print(f"Warning: 404 Not Found for URL: {response.url}")
             return None
        response.raise_for_status() # Raise for other errors (401, 403, 5xx)
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
    """
    Fetches static data (classes, specs).
    Can fetch from a full URL if use_base_url is False.
    """
    access_token = get_blizzard_access_token()
    if not access_token: return None

    if use_base_url:
        api_url = f"{API_BASE_URL}/data/wow{endpoint if endpoint.startswith('/') else '/' + endpoint}"
    else:
        api_url = endpoint # Use the provided endpoint as the full URL

    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"namespace": f"static-{REGION}", "locale": "en_US"}
    # Reduce logging verbosity for static data calls unless debugging
    # print(f"Attempting Static Data URL: {api_url} with Namespace: {params['namespace']}")
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

        # Fetch detail data for this specific spec using its href
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
        time.sleep(0.05) # Small delay to avoid hitting rate limits

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

    # Populate Class Map
    if not CLASS_MAP:
        print("Class map empty, attempting to fetch...")
        class_data = get_static_data('/playable-class/index')
        if class_data and 'classes' in class_data:
            CLASS_MAP = {cls['id']: cls['name'] for cls in class_data['classes']}
            print(f"Class map populated with {len(CLASS_MAP)} entries.")
        else:
            print("Failed to fetch or parse playable class data.")
            class_success = False

    # Populate Spec Map
    spec_success = populate_spec_cache()

    return class_success and spec_success


def get_guild_roster():
    """ Fetches the guild roster. """
    if not GUILD_NAME or not REALM_SLUG:
        print("Error: Guild Name or Realm Slug not configured.")
        return None
    access_token = get_blizzard_access_token()
    if not access_token: return None
    realm_slug_lower = REALM_SLUG.lower()
    guild_name_segment = GUILD_NAME.lower().replace(' ', '-')
    api_url = f"{API_BASE_URL}/data/wow/guild/{realm_slug_lower}/{guild_name_segment}/roster"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"namespace": f"profile-{REGION}", "locale": "en_US"}
    print(f"Attempting Guild Roster URL: {api_url}")
    data = make_api_request(api_url, params, headers)
    if data:
        print("Successfully fetched guild roster.")
    else:
        print("Failed to fetch guild roster.")
    return data

def get_character_summary(realm_slug, character_name):
    """ Fetches character profile summary (for item level, spec, role). """
    access_token = get_blizzard_access_token()
    if not access_token: return None
    realm_slug = realm_slug.lower()
    character_name = character_name.lower()
    api_url = f"{API_BASE_URL}/profile/wow/character/{realm_slug}/{character_name}"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"namespace": f"profile-{REGION}", "locale": "en_US"}
    data = make_api_request(api_url, params, headers)
    return data


def get_character_raid_progression(realm_slug, character_name):
    """ Fetches character raid encounters. """
    access_token = get_blizzard_access_token()
    if not access_token: return None
    realm_slug = realm_slug.lower()
    character_name = character_name.lower()
    api_url = f"{API_BASE_URL}/profile/wow/character/{realm_slug}/{character_name}/encounters/raids"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"namespace": f"profile-{REGION}", "locale": "en_US"}
    # print(f"DEBUG: Fetching raid progression for {character_name} from {api_url}") # Reduce logging
    data = make_api_request(api_url, params, headers)
    if not data:
        print(f"DEBUG: No raid data received for {character_name} (API returned None or error).")
    return data


def summarize_raid_progression(raid_data):
    """
    Summarizes raid progression specifically for 'The War Within' expansion
    and 'Liberation of Undermine' raid, focusing on Heroic and Mythic kills.
    Returns a tuple: (summary_string, heroic_kills_count)
    Returns (None, -1) if raid/expansion not found or error.
    """
    target_expansion_name = "The War Within" # Update if needed when TWW launches
    target_raid_name = "Liberation of Undermine" # Update if needed when TWW launches
    short_raid_name = "Undermine" # Name used in the output string

    if not raid_data or 'expansions' not in raid_data:
        # print(f"DEBUG ({short_raid_name}): Summarize returning None, -1 (no raid_data or expansions key)")
        return None, -1 # Indicate error or not found

    heroic_kills = -1 # Use -1 to indicate data not found for this difficulty
    heroic_total = 0
    mythic_kills = -1
    mythic_total = 0
    raid_found = False

    # Find the target expansion
    for expansion in raid_data.get('expansions', []):
        exp_details = expansion.get('expansion', {})
        if exp_details.get('name') == target_expansion_name:
            # Find the target raid instance within the expansion
            for instance in expansion.get('instances', []):
                instance_details = instance.get('instance', {})
                if instance_details.get('name') == target_raid_name:
                    raid_found = True
                    # print(f"DEBUG ({short_raid_name}): Found raid '{target_raid_name}'. Processing modes.")
                    # Process modes for Heroic and Mythic
                    for mode in instance.get('modes', []):
                        difficulty = mode.get('difficulty', {})
                        progress = mode.get('progress', {})
                        difficulty_type = difficulty.get('type')
                        if difficulty_type == "HEROIC":
                            heroic_kills = progress.get('completed_count', 0) # Get kills, default 0 if missing
                            heroic_total = progress.get('total_count', 0)
                            # print(f"DEBUG ({short_raid_name}): Found Heroic: {heroic_kills}/{heroic_total}")
                        elif difficulty_type == "MYTHIC":
                            mythic_kills = progress.get('completed_count', 0)
                            mythic_total = progress.get('total_count', 0)
                            # print(f"DEBUG ({short_raid_name}): Found Mythic: {mythic_kills}/{mythic_total}")
                    break # Stop searching instances once the target raid is found
            break # Stop searching expansions once the target expansion is found

    if not raid_found:
        # print(f"DEBUG ({short_raid_name}): Target raid '{target_raid_name}' not found.")
        return f"{short_raid_name}: Not Found", -1 # Return specific string and -1 kills

    # Format the output string
    summary_parts = []
    if heroic_kills != -1 and heroic_total > 0: summary_parts.append(f"{heroic_kills}/{heroic_total}H")
    if mythic_kills != -1 and mythic_total > 0: summary_parts.append(f"{mythic_kills}/{mythic_total}M")

    if not summary_parts:
        summary_output = f"{short_raid_name}: No H/M Data"
        hc_kills_return = 0 if heroic_kills == -1 else heroic_kills
    else:
        summary_output = f"{short_raid_name}: {' '.join(summary_parts)}"
        hc_kills_return = heroic_kills if heroic_kills != -1 else 0

    # print(f"DEBUG ({short_raid_name}): Summarize returning: ('{summary_output}', {hc_kills_return})")
    return summary_output, hc_kills_return

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
            # print(f"DEBUG: For {char_name}: API Spec='{spec_name}', Role='{role}'") # Reduce logging

        raid_data = get_character_raid_progression(char_realm_slug, char_name)
        api_call_count += 1
        if raid_data:
            raid_progression_summary, heroic_kills = summarize_raid_progression(raid_data)
            if raid_progression_summary is None or "Not Found" in raid_progression_summary or "No H/M Data" in raid_progression_summary:
                 raid_progression_summary = None
        else:
            raid_progression_summary = None
            heroic_kills = -1

        # Calculate Status based on ilvl and heroic kills
        calculated_status = "Member" # Default
        if item_level is None or item_level < 650:
            calculated_status = "Wiping Alt"
        elif heroic_kills > 6 : # Must have found heroic data (>=0) and have > 6 kills
             calculated_status = "Wiper"
        # elif heroic_kills >= 0 and heroic_kills <= 6: # If ilvl >= 650 and H kills <= 6
        #      calculated_status = "Member" # This is covered by the default
        print(f"DEBUG: For {char_name}: iLvl={item_level}, HKills={heroic_kills} -> Status='{calculated_status}'")

        # print(f"DEBUG: For {char_name}: Preparing Item Level = {item_level}, Raid Progression = '{raid_progression_summary}', Spec = '{spec_name}', Role = '{role}', ClassID = {class_id}, Status = '{calculated_status}'")

        characters_to_insert.append(Character(
            id=char_id, name=char_name, realm_slug=char_realm_slug, level=character_info.get('level'),
            class_id=class_id, class_name=class_name,
            spec_name=spec_name, main_spec_override=None, role=role,
            status=calculated_status, # Use calculated status
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

    end_time = time.time()
    print(f"Update process finished in {round(end_time - start_time, 2)} seconds.")


# --- Main Execution ---
if __name__ == "__main__":
    # Check environment variables before running
    required_vars = ['BLIZZARD_CLIENT_ID', 'BLIZZARD_CLIENT_SECRET', 'GUILD_NAME', 'REALM_SLUG', 'REGION', 'DATABASE_URL']
    print(f"Checking environment variables...")
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    if missing_vars:
        print(f"Error: Missing required environment variables: {', '.join(missing_vars)}")
        # Allow fallback to SQLite only if DATABASE_URL is the *only* missing var
        if missing_vars == ['DATABASE_URL'] and DATABASE_URI.startswith('sqlite:///'):
             print("Attempting to use default local SQLite DB: guild_data.db")
             # Check if API keys are still present for the fetch
             api_keys_missing = [var for var in required_vars[:-1] if not os.environ.get(var)]
             if api_keys_missing:
                  print(f"Error: Missing API environment variables needed for fetch: {', '.join(api_keys_missing)}")
                  exit(1)
             else:
                  update_database() # Try running with default SQLite
        else:
             # Exit if API keys or non-default DB URL are missing
             exit(1)
    else:
        print("All required environment variables found.")
        update_database()

