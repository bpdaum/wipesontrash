# update_roster_data.py
import os
import requests
import time
from datetime import datetime
import json # Import json for pretty printing debug output

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
    # Create SQLAlchemy engine
    engine = create_engine(DATABASE_URI)
    # Create a session factory
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    # Base class for declarative models
    Base = declarative_base()
    # Metadata object (optional, can use Base.metadata)
    metadata = MetaData()
except ImportError as e:
     print(f"Error: Database driver likely missing. Did you 'pip install psycopg2-binary' (for Postgres) or ensure SQLAlchemy is installed? Details: {e}")
     exit(1) # Exit if driver is missing
except Exception as e:
     print(f"Error creating database engine: {e}")
     exit(1)

# --- Database Model ---
# Defines the structure for storing character data in the database.
class Character(Base):
    """ Defines the structure for storing character data in the database. """
    __tablename__ = 'character' # Explicit table name recommended
    id = Column(Integer, primary_key=True) # Use Blizzard's character ID
    name = Column(String(100), nullable=False)
    realm_slug = Column(String(100), nullable=False) # Needed for Armory link
    level = Column(Integer)
    class_name = Column(String(50))
    race_name = Column(String(50))
    spec_name = Column(String(50)) # Active Specialization Name
    role = Column(String(10))      # Role (Tank, Healer, DPS)
    item_level = Column(Integer, index=True) # Index item_level for filtering/sorting
    raid_progression = Column(String(200)) # Store summary string
    rank = Column(Integer, index=True) # Index rank for faster filtering
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow) # Use utcnow

    # Define a unique constraint on name and realm_slug
    __table_args__ = (
        UniqueConstraint('name', 'realm_slug', name='_name_realm_uc'),
    )

    def __repr__(self):
        return f'<Character {self.name}-{self.realm_slug}>'

# --- Configuration Loading ---
BLIZZARD_CLIENT_ID = os.environ.get('BLIZZARD_CLIENT_ID')
BLIZZARD_CLIENT_SECRET = os.environ.get('BLIZZARD_CLIENT_SECRET')
GUILD_NAME = os.environ.get('GUILD_NAME')
REALM_SLUG = os.environ.get('REALM_SLUG')
REGION = os.environ.get('REGION', 'us').lower()

# --- Blizzard API Configuration ---
VALID_REGIONS = ['us', 'eu', 'kr', 'tw']
if REGION not in VALID_REGIONS:
    raise ValueError(f"Invalid REGION: {REGION}. Must be one of {VALID_REGIONS}")
TOKEN_URL = f"https://{REGION}.battle.net/oauth/token"
API_BASE_URL = f"https://{REGION}.api.blizzard.com"

# --- Caching (For API calls within this script run) ---
access_token_cache = { "token": None, "expires_at": 0 }
CLASS_MAP = {}
RACE_MAP = {}

# --- API Helper Functions ---

def get_blizzard_access_token():
    """ Retrieves Blizzard access token, uses cache. """
    global access_token_cache
    current_time = time.time()
    # Check cache first, allowing a 60-second buffer before expiry
    if access_token_cache["token"] and access_token_cache["expires_at"] > current_time + 60:
        return access_token_cache["token"]

    # Check for credentials
    if not BLIZZARD_CLIENT_ID or not BLIZZARD_CLIENT_SECRET:
        print("Error: BLIZZARD_CLIENT_ID or BLIZZARD_CLIENT_SECRET not set in environment variables.")
        return None

    # Request new token
    try:
        response = requests.post(
            TOKEN_URL, auth=(BLIZZARD_CLIENT_ID, BLIZZARD_CLIENT_SECRET),
            data={'grant_type': 'client_credentials'}
        )
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        token_data = response.json()
        access_token = token_data.get('access_token')
        expires_in = token_data.get('expires_in', 0)

        if not access_token:
            print(f"Error: Could not retrieve access token. Response: {token_data}")
            return None

        # Update cache
        access_token_cache["token"] = access_token
        access_token_cache["expires_at"] = current_time + expires_in
        print(f"New Blizzard access token obtained.")
        return access_token

    except requests.exceptions.RequestException as e:
        print(f"Error getting Blizzard access token: {e}")
        # Log response details if available
        if e.response is not None:
            print(f"Response Status: {e.response.status_code}")
            try:
                print(f"Response Body: {e.response.json()}")
            except requests.exceptions.JSONDecodeError:
                print(f"Response Body: {e.response.text}")
        return None
    except Exception as e:
        print(f"An unexpected error during token retrieval: {e}")
        return None

def make_api_request(api_url, params, headers):
    """ Helper function to make API GET requests and handle common errors """
    try:
        response = requests.get(api_url, params=params, headers=headers, timeout=30) # Added timeout
        # Handle 404 gracefully for character data, as it might just mean character not found/inactive
        if response.status_code == 404:
             print(f"Warning: 404 Not Found for URL: {response.url}")
             return None
        response.raise_for_status() # Raise for other errors (401, 403, 5xx)
        return response.json()
    except requests.exceptions.HTTPError as e:
        print(f"HTTP Error during API request: {e}")
        print(f"URL attempted: {e.request.url}")
        print(f"Response Status: {e.response.status_code}")
        try:
            print(f"Response Body: {e.response.json()}")
        except requests.exceptions.JSONDecodeError:
            print(f"Response Body: {e.response.text}")
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


def get_static_data(endpoint):
    """ Fetches static data (classes, races). """
    access_token = get_blizzard_access_token()
    if not access_token: return None
    # Ensure endpoint starts with '/'
    api_url = f"{API_BASE_URL}/data/wow{endpoint if endpoint.startswith('/') else '/' + endpoint}"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"namespace": f"static-{REGION}", "locale": "en_US"}
    print(f"Attempting Static Data URL: {api_url} with Namespace: {params['namespace']}")
    data = make_api_request(api_url, params, headers)
    if data:
        print(f"Successfully fetched static data from {endpoint}.")
    return data


def populate_static_caches():
    """ Populates CLASS_MAP and RACE_MAP if empty. """
    global CLASS_MAP, RACE_MAP
    success = True
    # Populate Class Map
    if not CLASS_MAP:
        print("Class map empty, attempting to fetch...")
        class_data = get_static_data('/playable-class/index')
        if class_data and 'classes' in class_data:
            CLASS_MAP = {cls['id']: cls['name'] for cls in class_data['classes']}
            print(f"Class map populated with {len(CLASS_MAP)} entries.")
        else:
            print("Failed to fetch or parse playable class data.")
            success = False
    # Populate Race Map
    if not RACE_MAP:
        print("Race map empty, attempting to fetch...")
        race_data = get_static_data('/playable-race/index')
        if race_data and 'races' in race_data:
            RACE_MAP = {race['id']: race['name'] for race in race_data['races']}
            print(f"Race map populated with {len(RACE_MAP)} entries.")
        else:
            print("Failed to fetch or parse playable race data.")
            success = False
    return success


def get_guild_roster():
    """ Fetches the guild roster. """
    if not GUILD_NAME or not REALM_SLUG:
        print("Error: Guild Name or Realm Slug not configured.")
        return None
    access_token = get_blizzard_access_token()
    if not access_token: return None
    realm_slug_lower = REALM_SLUG.lower()
    guild_name_segment = GUILD_NAME.lower().replace(' ', '-') # Format for URL
    api_url = f"{API_BASE_URL}/data/wow/guild/{realm_slug_lower}/{guild_name_segment}/roster"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"namespace": f"profile-{REGION}", "locale": "en_US"}
    print(f"Attempting Guild Roster URL: {api_url}")
    data = make_api_request(api_url, params, headers)
    if data:
        print("Successfully fetched guild roster.")
    return data

def get_character_summary(realm_slug, character_name):
    """ Fetches character profile summary (for item level, spec, role). """
    access_token = get_blizzard_access_token()
    if not access_token: return None
    realm_slug = realm_slug.lower()
    character_name = character_name.lower() # API expects lowercase name in URL
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
    character_name = character_name.lower() # API expects lowercase name in URL
    api_url = f"{API_BASE_URL}/profile/wow/character/{realm_slug}/{character_name}/encounters/raids"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"namespace": f"profile-{REGION}", "locale": "en_US"}
    print(f"DEBUG: Fetching raid progression for {character_name} from {api_url}")
    data = make_api_request(api_url, params, headers)
    # (Optional verbose debug logging for raw data)
    if not data:
        print(f"DEBUG: No raid data received for {character_name} (API returned None or error).")
    return data


def summarize_raid_progression(raid_data):
    """
    Summarizes raid progression specifically for 'The War Within' expansion
    and 'Liberation of Undermine' raid, focusing on Heroic and Mythic kills.
    Returns a string like "Undermine: 8/8H 3/8M" or indicates if not found/no progress.
    """
    target_expansion_name = "The War Within" # Update if needed when TWW launches
    target_raid_name = "Liberation of Undermine" # Update if needed when TWW launches
    short_raid_name = "Undermine" # Name used in the output string

    # (Optional verbose debug logging for input data)
    # print(f"DEBUG: Summarizing raid data input for {short_raid_name}:")
    # try: print(json.dumps(raid_data, indent=2))
    # except Exception as e: print(f"(Could not print input as JSON: {e}) Raw input: {raid_data}")

    if not raid_data or 'expansions' not in raid_data:
        print(f"DEBUG ({short_raid_name}): Summarize returning 'Not Found' (no raid_data or expansions key)")
        return f"{short_raid_name}: Not Found"

    heroic_kills = 0
    heroic_total = 0
    mythic_kills = 0
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
                    print(f"DEBUG ({short_raid_name}): Found raid '{target_raid_name}'. Processing modes.")
                    # Process modes for Heroic and Mythic
                    for mode in instance.get('modes', []):
                        difficulty = mode.get('difficulty', {})
                        progress = mode.get('progress', {})
                        difficulty_type = difficulty.get('type')

                        if difficulty_type == "HEROIC":
                            heroic_kills = progress.get('completed_count', 0)
                            heroic_total = progress.get('total_count', 0)
                            print(f"DEBUG ({short_raid_name}): Found Heroic: {heroic_kills}/{heroic_total}")
                        elif difficulty_type == "MYTHIC":
                            mythic_kills = progress.get('completed_count', 0)
                            mythic_total = progress.get('total_count', 0)
                            print(f"DEBUG ({short_raid_name}): Found Mythic: {mythic_kills}/{mythic_total}")
                    break # Stop searching instances once the target raid is found
            break # Stop searching expansions once the target expansion is found

    if not raid_found:
        print(f"DEBUG ({short_raid_name}): Target raid '{target_raid_name}' not found in expansion '{target_expansion_name}'.")
        return f"{short_raid_name}: Not Found"

    # Format the output string
    summary_parts = []
    # Only include difficulty if the raid actually has bosses for that mode
    if heroic_total > 0:
        summary_parts.append(f"{heroic_kills}/{heroic_total}H")
    if mythic_total > 0:
        summary_parts.append(f"{mythic_kills}/{mythic_total}M")

    if not summary_parts:
        summary_output = f"{short_raid_name}: No H/M Data"
        print(f"DEBUG ({short_raid_name}): Summarize returning: {summary_output}")
        return summary_output
    else:
        summary_output = f"{short_raid_name}: {' '.join(summary_parts)}"
        print(f"DEBUG ({short_raid_name}): Summarize returning: {summary_output}")
        return summary_output

# --- END API Helper Functions ---


# --- Database Update Logic ---
def update_database():
    """ Fetches all data from Blizzard API and updates the database. """
    print("Starting database update process...")
    start_time = time.time()

    # --- Drop and Recreate Table ---
    try:
        print(f"Attempting to drop table '{Character.__tablename__}' if it exists...")
        Base.metadata.bind = engine # Bind metadata to engine
        Character.__table__.drop(engine, checkfirst=True) # Drop if exists
        print(f"Table '{Character.__tablename__}' dropped (or did not exist).")

        print(f"Creating table '{Character.__tablename__}'...")
        Base.metadata.create_all(bind=engine) # Recreate table based on current model
        print("Table created successfully.")
    except OperationalError as e:
         print(f"Database connection error during drop/create: {e}. Check DATABASE_URL and network.")
         return
    except Exception as e:
        print(f"Error during table drop/create: {e}")
        return # Cannot proceed without table
    # --- END Drop and Recreate ---

    # Populate static class/race maps first
    if not populate_static_caches():
        print("Error: Failed to populate static caches. Aborting update.")
        return

    # Fetch the main guild roster
    roster_data = get_guild_roster()
    if not roster_data or 'members' not in roster_data:
        print("Error: Failed to fetch guild roster. Aborting update.")
        return

    total_members = len(roster_data['members'])
    print(f"Fetched {total_members} total members from roster. Filtering by rank <= 4...")

    # Store fetched character details temporarily
    characters_to_insert = [] # List to hold Character objects for bulk insert

    api_call_count = 0
    processed_for_details = 0

    for member_entry in roster_data['members']:
        character_info = member_entry.get('character', {})
        rank = member_entry.get('rank')
        char_id = character_info.get('id')
        char_name = character_info.get('name')
        char_realm_slug = character_info.get('realm', {}).get('slug')

        # Filter: Skip if rank > 4 or essential info missing
        if rank is None or rank > 4 or not char_id or not char_name or not char_realm_slug:
            continue

        processed_for_details += 1
        # Log progress periodically
        if processed_for_details % 10 == 1 or processed_for_details == total_members:
             print(f"\nProcessing details for {char_name}-{char_realm_slug} (Rank {rank})... ({processed_for_details}/{total_members} checked)")

        # Get Class/Race ID and lookup name
        class_id = character_info.get('playable_class', {}).get('id')
        race_id = character_info.get('playable_race', {}).get('id')
        class_name = CLASS_MAP.get(class_id, f"ID: {class_id}" if class_id else "N/A")
        race_name = RACE_MAP.get(race_id, f"ID: {race_id}" if race_id else "N/A")

        # Fetch additional data (ilvl, progression, spec, role)
        item_level = None
        raid_progression_summary = None
        spec_name = None
        role = None

        # Fetch summary data (contains ilvl, spec)
        summary_data = get_character_summary(char_realm_slug, char_name)
        api_call_count += 1
        if summary_data:
            # Get Item Level
            ilvl_raw = summary_data.get('average_item_level')
            item_level = int(ilvl_raw) if isinstance(ilvl_raw, (int, float)) else None

            # Get Spec and Role
            active_spec_data = summary_data.get('active_spec')
            if active_spec_data and isinstance(active_spec_data, dict):
                spec_name = active_spec_data.get('name')
                try:
                    # Determine role based on spec type (heuristic)
                    spec_type = None
                    if 'type' in active_spec_data: # Check primary location
                        spec_type = active_spec_data.get('type', '').upper()
                    elif 'media' in active_spec_data and isinstance(active_spec_data['media'], dict) and 'type' in active_spec_data['media']: # Fallback check
                         spec_type = active_spec_data['media'].get('type', '').upper()

                    if spec_type == 'HEALING': role = 'Healer'
                    elif spec_type == 'TANK': role = 'Tank'
                    elif spec_type == 'DAMAGE': role = 'DPS'
                    else: # Fallback based on spec name if type missing/unexpected
                        print(f"DEBUG: Spec type '{spec_type}' not found or unexpected for {char_name}. Falling back to name heuristic.")
                        if spec_name in ["Blood", "Protection", "Guardian", "Brewmaster", "Vengeance"]: role = "Tank"
                        elif spec_name in ["Holy", "Discipline", "Restoration", "Mistweaver", "Preservation"]: role = "Healer"
                        elif spec_name: role = "DPS" # Assume DPS if name exists but no match
                except Exception as spec_err:
                    print(f"Warning: Could not determine role for {char_name} from spec data: {spec_err}")
            print(f"DEBUG: For {char_name}: Spec='{spec_name}', Role='{role}'")

        # Fetch raid progression data
        raid_data = get_character_raid_progression(char_realm_slug, char_name)
        api_call_count += 1
        if raid_data:
            # Summarize the specific raid progression
            raid_progression_summary = summarize_raid_progression(raid_data)
            # Store None in DB if summary function indicates no relevant data
            if raid_progression_summary is None or "Not Found" in raid_progression_summary or "No H/M Data" in raid_progression_summary:
                 raid_progression_summary = None
        else:
            # Handle case where API call failed
            print(f"DEBUG: No raid data returned from API for {char_name}, setting progression to None.")
            raid_progression_summary = None

        # Print value being prepared for DB
        print(f"DEBUG: For {char_name}: Preparing Item Level = {item_level}, Raid Progression = '{raid_progression_summary}', Spec = '{spec_name}', Role = '{role}'")

        # Create Character object for insertion
        characters_to_insert.append(Character(
            id=char_id,
            name=char_name,
            realm_slug=char_realm_slug,
            level=character_info.get('level'),
            class_name=class_name,
            race_name=race_name,
            spec_name=spec_name,
            role=role,
            item_level=item_level,
            raid_progression=raid_progression_summary,
            rank=rank
            # last_updated is handled by default/onupdate in the model
        ))

    print(f"\nFetched details for {len(characters_to_insert)} members (Rank <= 4). Made {api_call_count} API calls.")

    # --- Insert Data into Newly Created Table ---
    db_session = SessionLocal()
    try:
        print(f"Inserting {len(characters_to_insert)} characters into the database...")
        # Using add_all is generally fine for moderate numbers
        db_session.add_all(characters_to_insert)
        # For very large rosters, bulk_insert_mappings might be slightly faster
        # if characters_to_insert: # Check if list is not empty
        #     db_session.bulk_insert_mappings(Character, [vars(c) for c in characters_to_insert])
        db_session.commit()
        print(f"Database insert complete: {len(characters_to_insert)} inserted.")

    except OperationalError as e:
        print(f"Database connection error during insert: {e}. Check DATABASE_URL and network.")
        db_session.rollback()
    except Exception as e:
        print(f"Error during database insert: {e}")
        db_session.rollback() # Rollback changes on error
    finally:
        db_session.close() # Always close the session

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

