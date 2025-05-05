# update_roster_data.py
import os
import requests
import time
from datetime import datetime

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

# Re-define the model here (should match app.py)
class Character(Base):
    """ Defines the structure for storing character data in the database. """
    __tablename__ = 'character'
    id = Column(Integer, primary_key=True) # Use Blizzard's character ID
    name = Column(String(100), nullable=False)
    realm_slug = Column(String(100), nullable=False)
    level = Column(Integer)
    class_name = Column(String(50))
    race_name = Column(String(50))
    item_level = Column(Integer)
    raid_progression = Column(String(200))
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
        # Add more detailed error logging if needed
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
    """ Helper function to make API requests and handle common errors """
    try:
        response = requests.get(api_url, params=params, headers=headers)
        if response.status_code == 404:
             print(f"Warning: 404 Not Found for URL: {response.url}")
             return None
        response.raise_for_status()
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
    except requests.exceptions.RequestException as e:
        print(f"Network error during API request: {e}")
        return None
    except Exception as e:
        print(f"An unexpected error occurred during API request: {e}")
        return None


def get_static_data(endpoint):
    """ Fetches static data (classes, races), uses cache. """
    access_token = get_blizzard_access_token()
    if not access_token: return None
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
    if not CLASS_MAP:
        print("Class map empty, attempting to fetch...")
        class_data = get_static_data('/playable-class/index')
        if class_data and 'classes' in class_data:
            CLASS_MAP = {cls['id']: cls['name'] for cls in class_data['classes']}
            print(f"Class map populated with {len(CLASS_MAP)} entries.")
        else:
            print("Failed to fetch or parse playable class data.")
            success = False
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
    if not GUILD_NAME or not REALM_SLUG: return None
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
    return data

def get_character_summary(realm_slug, character_name):
    """ Fetches character profile summary (for item level). """
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
    data = make_api_request(api_url, params, headers)
    return data


def summarize_raid_progression(raid_data):
    """ Simple function to summarize raid progression from the API data. """
    if not raid_data or 'expansions' not in raid_data:
        return "N/A"
    latest_expansion = raid_data['expansions'][-1] if raid_data['expansions'] else None
    if not latest_expansion or 'raids' not in latest_expansion:
        return "N/A"
    latest_raid = latest_expansion['raids'][-1] if latest_expansion['raids'] else None
    if not latest_raid or 'modes' not in latest_raid:
        return "N/A"
    raid_name = latest_raid.get('instance', {}).get('name', 'Latest Raid')
    progression_summary = []
    difficulty_order = {"LFR": "L", "NORMAL": "N", "HEROIC": "H", "MYTHIC": "M"}
    sorted_modes = sorted(latest_raid['modes'], key=lambda mode: list(difficulty_order.keys()).index(mode.get('difficulty', {}).get('type', '')) if mode.get('difficulty', {}).get('type') in difficulty_order else 99)
    for mode in sorted_modes:
        difficulty_type = mode.get('difficulty', {}).get('type')
        if difficulty_type in difficulty_order:
            progress = mode.get('progress', {})
            completed_count = progress.get('completed_count', 0)
            total_count = progress.get('total_count', 0)
            if total_count > 0:
                 progression_summary.append(f"{completed_count}/{total_count}{difficulty_order[difficulty_type]}")
    if not progression_summary:
        return f"{raid_name}: No Progress"
    return f"{raid_name}: {' '.join(progression_summary)}"

# --- END API Helper Functions ---


# --- Database Update Logic ---
def update_database():
    """ Fetches all data from Blizzard API and updates the database. """
    print("Starting database update process...")
    start_time = time.time()

    # Ensure table exists before proceeding
    try:
        with engine.connect() as connection:
             if not engine.dialect.has_table(connection, Character.__tablename__):
                 print(f"Table '{Character.__tablename__}' not found. Creating table.")
                 Base.metadata.create_all(bind=engine) # Create table if missing
                 print("Table created.")
             else:
                 print(f"Table '{Character.__tablename__}' found.")
    except OperationalError as e:
         print(f"Database connection error: {e}. Check DATABASE_URL and network.")
         return
    except Exception as e:
        print(f"Error checking/creating table: {e}")
        return # Cannot proceed without table

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
    characters_to_update = {} # Key: char_id, Value: dict of details

    api_call_count = 0
    processed_for_details = 0

    for member_entry in roster_data['members']:
        character_info = member_entry.get('character', {})
        rank = member_entry.get('rank')
        char_id = character_info.get('id') # Use ID as primary key
        char_name = character_info.get('name')
        char_realm_slug = character_info.get('realm', {}).get('slug')

        # Filter: Skip if rank > 4 or essential info missing
        if rank is None or rank > 4 or not char_id or not char_name or not char_realm_slug:
            continue

        processed_for_details += 1
        # Limit excessive logging, maybe log every 10th character processed
        if processed_for_details % 10 == 1 or processed_for_details == total_members:
             print(f"Processing details for {char_name}-{char_realm_slug} (Rank {rank})... ({processed_for_details}/{total_members} checked)")


        # Get Class/Race ID and lookup name
        class_id = character_info.get('playable_class', {}).get('id')
        race_id = character_info.get('playable_race', {}).get('id')
        class_name = CLASS_MAP.get(class_id, f"ID: {class_id}" if class_id else "N/A")
        race_name = RACE_MAP.get(race_id, f"ID: {race_id}" if race_id else "N/A")

        # Fetch additional data (ilvl, progression)
        item_level = None # Use None for DB instead of "N/A"
        raid_progression_summary = None

        summary_data = get_character_summary(char_realm_slug, char_name)
        api_call_count += 1
        if summary_data:
            ilvl_raw = summary_data.get('average_item_level')
            item_level = int(ilvl_raw) if isinstance(ilvl_raw, (int, float)) else None

        raid_data = get_character_raid_progression(char_realm_slug, char_name)
        api_call_count += 1
        if raid_data:
            raid_progression_summary = summarize_raid_progression(raid_data)
            # Ensure N/A from summary is stored as None
            if raid_progression_summary == "N/A": raid_progression_summary = None

        # Store details for database update
        characters_to_update[char_id] = {
            'name': char_name,
            'realm_slug': char_realm_slug,
            'level': character_info.get('level'),
            'class_name': class_name,
            'race_name': race_name,
            'item_level': item_level,
            'raid_progression': raid_progression_summary,
            'rank': rank
        }

    print(f"Fetched details for {len(characters_to_update)} members (Rank <= 4). Made {api_call_count} API calls.")

    # --- Update Database ---
    db_session = SessionLocal()
    try:
        print("Updating database...")
        # Get IDs of characters currently in DB with rank <= 4
        existing_char_ids = {id_tuple[0] for id_tuple in db_session.query(Character.id).filter(Character.rank <= 4).all()}
        fetched_char_ids = set(characters_to_update.keys())

        # IDs to delete (were rank <= 4 before, but not in the new fetch with rank <= 4)
        ids_to_delete = existing_char_ids - fetched_char_ids
        if ids_to_delete:
            print(f"Deleting {len(ids_to_delete)} characters no longer meeting rank criteria...")
            db_session.query(Character).filter(Character.id.in_(ids_to_delete)).delete(synchronize_session=False)

        # Update or Insert characters
        updated_count = 0
        inserted_count = 0
        for char_id, details in characters_to_update.items():
            character = db_session.query(Character).filter_by(id=char_id).first()
            if character:
                # Update existing character
                character.level = details['level']
                character.class_name = details['class_name']
                character.race_name = details['race_name']
                character.item_level = details['item_level']
                character.raid_progression = details['raid_progression']
                character.rank = details['rank']
                character.last_updated = datetime.utcnow() # Explicitly set update time
                updated_count += 1
            else:
                # Insert new character
                character = Character(id=char_id, **details)
                db_session.add(character)
                inserted_count += 1

        db_session.commit()
        print(f"Database update complete: {inserted_count} inserted, {updated_count} updated, {len(ids_to_delete)} deleted.")

    except OperationalError as e:
        print(f"Database connection error during update: {e}. Check DATABASE_URL and network.")
        db_session.rollback()
    except Exception as e:
        print(f"Error during database update: {e}")
        db_session.rollback() # Rollback changes on error
    finally:
        db_session.close() # Always close the session

    end_time = time.time()
    print(f"Update process finished in {round(end_time - start_time, 2)} seconds.")


# --- Main Execution ---
if __name__ == "__main__":
    # Ensure all required env vars are set before running
    required_vars = ['BLIZZARD_CLIENT_ID', 'BLIZZARD_CLIENT_SECRET', 'GUILD_NAME', 'REALM_SLUG', 'REGION', 'DATABASE_URL']
    print(f"Checking environment variables...")
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    if missing_vars:
        print(f"Error: Missing required environment variables: {', '.join(missing_vars)}")
        # If DATABASE_URL is missing, maybe we intended to use local SQLite?
        if 'DATABASE_URL' in missing_vars and DATABASE_URI.startswith('sqlite:///'):
             print("Attempting to use default local SQLite DB: guild_data.db")
             # Ensure API keys are present even for local SQLite test
             api_keys_missing = [var for var in required_vars[:-1] if not os.environ.get(var)]
             if api_keys_missing:
                  print(f"Error: Missing API environment variables needed for fetch: {', '.join(api_keys_missing)}")
                  exit(1)
             else:
                  update_database() # Try running with default SQLite
        else:
             exit(1) # Exit if critical vars like API keys or non-default DB URL are missing
    else:
        print("All required environment variables found.")
        update_database()
