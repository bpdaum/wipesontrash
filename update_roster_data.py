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

# --- Database Model (with class_id and override) ---
class Character(Base):
    __tablename__ = 'character'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    realm_slug = Column(String(100), nullable=False)
    level = Column(Integer)
    class_id = Column(Integer) # NEW: Store the class ID
    class_name = Column(String(50))
    race_name = Column(String(50))
    spec_name = Column(String(50)) # API Active Spec
    main_spec_override = Column(String(50), nullable=True) # NEW: User override
    role = Column(String(10))
    item_level = Column(Integer, index=True)
    raid_progression = Column(String(200))
    rank = Column(Integer, index=True)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (UniqueConstraint('name', 'realm_slug', name='_name_realm_uc'),)

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

# --- Caching ---
access_token_cache = { "token": None, "expires_at": 0 }
CLASS_MAP = {}
RACE_MAP = {}
SPEC_MAP_BY_CLASS = {} # NEW: Cache for all specs {class_id: [{id: spec_id, name: spec_name}, ...]}

# --- API Helper Functions ---
# [PASTE ALL PREVIOUS HELPER FUNCTIONS HERE]
# get_blizzard_access_token()
# make_api_request()
# get_static_data()
# get_guild_roster()
# get_character_summary()
# get_character_raid_progression()
# summarize_raid_progression()
# ... (For brevity, omitting the identical code paste here) ...

# --- NEW: Populate All Specs Cache ---
def populate_spec_cache():
    """ Populates the SPEC_MAP_BY_CLASS cache if empty. """
    global SPEC_MAP_BY_CLASS
    if SPEC_MAP_BY_CLASS: # Don't refetch if already populated
        return True

    print("Specialization map empty, attempting to fetch...")
    spec_index_data = get_static_data('/playable-specialization/index')

    if not spec_index_data or 'character_specializations' not in spec_index_data:
        print("Error: Failed to fetch or parse playable specialization index.")
        return False

    temp_spec_map = {}
    # Structure: { "character_specializations": [ { "id": X, "playable_class": { "id": Y }, "name": "SpecName" }, ... ] }
    for spec_info in spec_index_data.get('character_specializations', []):
        class_id = spec_info.get('playable_class', {}).get('id')
        spec_id = spec_info.get('id')
        spec_name = spec_info.get('name')

        if class_id and spec_id and spec_name:
            if class_id not in temp_spec_map:
                temp_spec_map[class_id] = []
            temp_spec_map[class_id].append({"id": spec_id, "name": spec_name})

    if not temp_spec_map:
        print("Error: Could not build specialization map from fetched data.")
        return False

    SPEC_MAP_BY_CLASS = temp_spec_map
    print(f"Specialization map populated for {len(SPEC_MAP_BY_CLASS)} classes.")
    # print(f"DEBUG Spec Map Sample: {list(SPEC_MAP_BY_CLASS.items())[:2]}") # Optional debug print
    return True


# --- Modified: populate_static_caches ---
def populate_static_caches():
    """ Populates CLASS_MAP, RACE_MAP, and SPEC_MAP_BY_CLASS if empty. """
    global CLASS_MAP, RACE_MAP # Spec map is handled by populate_spec_cache
    class_success = True
    race_success = True
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

    # Populate Race Map
    if not RACE_MAP:
        print("Race map empty, attempting to fetch...")
        race_data = get_static_data('/playable-race/index')
        if race_data and 'races' in race_data:
            RACE_MAP = {race['id']: race['name'] for race in race_data['races']}
            print(f"Race map populated with {len(RACE_MAP)} entries.")
        else:
            print("Failed to fetch or parse playable race data.")
            race_success = False

    # Populate Spec Map
    spec_success = populate_spec_cache()

    return class_success and race_success and spec_success


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

    # Populate static caches first (now includes specs)
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

    characters_to_insert = [] # List to hold Character objects for insertion

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
        if processed_for_details % 10 == 1 or processed_for_details == total_members:
             print(f"\nProcessing details for {char_name}-{char_realm_slug} (Rank {rank})... ({processed_for_details}/{total_members} checked)")

        # Get Class/Race ID and lookup name
        class_id = character_info.get('playable_class', {}).get('id') # ** GET CLASS ID **
        race_id = character_info.get('playable_race', {}).get('id')
        class_name = CLASS_MAP.get(class_id, f"ID: {class_id}" if class_id else "N/A")
        race_name = RACE_MAP.get(race_id, f"ID: {race_id}" if race_id else "N/A")

        # Fetch additional data
        item_level = None
        raid_progression_summary = None
        spec_name = None # API active spec
        role = None
        main_spec_override = None # Start with no override

        summary_data = get_character_summary(char_realm_slug, char_name)
        api_call_count += 1
        if summary_data:
            # Get Item Level
            ilvl_raw = summary_data.get('average_item_level')
            item_level = int(ilvl_raw) if isinstance(ilvl_raw, (int, float)) else None

            # Get API Active Spec and Role
            active_spec_data = summary_data.get('active_spec')
            if active_spec_data and isinstance(active_spec_data, dict):
                spec_name = active_spec_data.get('name') # Store the API active spec name
                try:
                    # Determine role based on spec type
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
                except Exception as spec_err:
                    print(f"Warning: Could not determine role for {char_name} from spec data: {spec_err}")
            print(f"DEBUG: For {char_name}: API Spec='{spec_name}', Role='{role}'")

        # Fetch Raid Progression
        raid_data = get_character_raid_progression(char_realm_slug, char_name)
        api_call_count += 1
        if raid_data:
            raid_progression_summary = summarize_raid_progression(raid_data)
            if raid_progression_summary is None or "Not Found" in raid_progression_summary or "No H/M Data" in raid_progression_summary:
                 raid_progression_summary = None
        else:
            raid_progression_summary = None

        # Print value being prepared for DB (override will be None on initial insert)
        print(f"DEBUG: For {char_name}: Preparing Item Level = {item_level}, Raid Progression = '{raid_progression_summary}', Spec = '{spec_name}', Role = '{role}', ClassID = {class_id}")

        # Create Character object for insertion
        # Note: main_spec_override is intentionally left as None here.
        # Updates will happen via the web UI and the /update_spec route.
        characters_to_insert.append(Character(
            id=char_id,
            name=char_name,
            realm_slug=char_realm_slug,
            level=character_info.get('level'),
            class_id=class_id, # Save class ID
            class_name=class_name,
            race_name=race_name,
            spec_name=spec_name, # Save API active spec
            main_spec_override=None, # Initialize override as None
            role=role,
            item_level=item_level,
            raid_progression=raid_progression_summary,
            rank=rank
        ))

    print(f"\nFetched details for {len(characters_to_insert)} members (Rank <= 4). Made {api_call_count} API calls.")

    # --- Insert Data into Newly Created Table ---
    db_session = SessionLocal()
    try:
        print(f"Inserting {len(characters_to_insert)} characters into the database...")
        if characters_to_insert: # Check if list is not empty
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
    # [PASTE PREVIOUS MAIN EXECUTION BLOCK HERE - UNCHANGED]
    # ... (Checks env vars, calls update_database) ...
    required_vars = ['BLIZZARD_CLIENT_ID', 'BLIZZARD_CLIENT_SECRET', 'GUILD_NAME', 'REALM_SLUG', 'REGION', 'DATABASE_URL']
    print(f"Checking environment variables...")
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    if missing_vars:
        print(f"Error: Missing required environment variables: {', '.join(missing_vars)}")
        if missing_vars == ['DATABASE_URL'] and DATABASE_URI.startswith('sqlite:///'):
             print("Attempting to use default local SQLite DB: guild_data.db")
             api_keys_missing = [var for var in required_vars[:-1] if not os.environ.get(var)]
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