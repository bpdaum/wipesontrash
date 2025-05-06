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
# Includes spec_name and role columns
class Character(Base):
    """ Defines the structure for storing character data in the database. """
    __tablename__ = 'character'
    id = Column(Integer, primary_key=True) # Use Blizzard's character ID
    name = Column(String(100), nullable=False)
    realm_slug = Column(String(100), nullable=False)
    level = Column(Integer)
    class_name = Column(String(50))
    race_name = Column(String(50))
    spec_name = Column(String(50)) # Active Specialization Name
    role = Column(String(10))      # Role (Tank, Healer, DPS)
    item_level = Column(Integer, index=True)
    raid_progression = Column(String(200))
    rank = Column(Integer, index=True)
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
# [PASTE ALL PREVIOUS HELPER FUNCTIONS HERE - UNCHANGED]
# get_blizzard_access_token()
# make_api_request()
# get_static_data()
# populate_static_caches()
# get_guild_roster()
# get_character_summary()
# get_character_raid_progression()
# summarize_raid_progression()
# ... (For brevity, omitting the identical code paste here) ...
# --- END API Helper Functions ---


# --- Database Update Logic ---
def update_database():
    """ Fetches all data from Blizzard API and updates the database. """
    print("Starting database update process...")
    start_time = time.time()

    # --- MODIFIED: Drop and Recreate Table ---
    try:
        print(f"Attempting to drop table '{Character.__tablename__}' if it exists...")
        # Bind the metadata to the engine before dropping/creating
        Base.metadata.bind = engine
        # Drop the specific table defined in the Character model
        Character.__table__.drop(engine, checkfirst=True) # checkfirst=True avoids error if table doesn't exist
        print(f"Table '{Character.__tablename__}' dropped (or did not exist).")

        print(f"Creating table '{Character.__tablename__}'...")
        Base.metadata.create_all(bind=engine) # Create table based on current model definition
        print("Table created successfully.")
    except OperationalError as e:
         print(f"Database connection error during drop/create: {e}. Check DATABASE_URL and network.")
         return
    except Exception as e:
        print(f"Error during table drop/create: {e}")
        return # Cannot proceed without table
    # --- END MODIFICATION ---

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
        char_id = character_info.get('id') # Use ID as primary key
        char_name = character_info.get('name')
        char_realm_slug = character_info.get('realm', {}).get('slug')

        # Filter: Skip if rank > 4 or essential info missing
        if rank is None or rank > 4 or not char_id or not char_name or not char_realm_slug:
            continue

        processed_for_details += 1
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
                    spec_type = None
                    if 'type' in active_spec_data:
                        spec_type = active_spec_data.get('type', '').upper()
                    elif 'media' in active_spec_data and isinstance(active_spec_data['media'], dict) and 'type' in active_spec_data['media']:
                         spec_type = active_spec_data['media'].get('type', '').upper()

                    if spec_type == 'HEALING': role = 'Healer'
                    elif spec_type == 'TANK': role = 'Tank'
                    elif spec_type == 'DAMAGE': role = 'DPS'
                    else:
                        print(f"DEBUG: Spec type '{spec_type}' not found or unexpected for {char_name}. Falling back to name heuristic.")
                        if spec_name in ["Blood", "Protection", "Guardian", "Brewmaster", "Vengeance"]: role = "Tank"
                        elif spec_name in ["Holy", "Discipline", "Restoration", "Mistweaver", "Preservation"]: role = "Healer"
                        elif spec_name: role = "DPS"
                except Exception as spec_err:
                    print(f"Warning: Could not determine role for {char_name} from spec data: {spec_err}")

            print(f"DEBUG: For {char_name}: Spec='{spec_name}', Role='{role}'")

        raid_data = get_character_raid_progression(char_realm_slug, char_name)
        api_call_count += 1
        if raid_data:
            raid_progression_summary = summarize_raid_progression(raid_data)
            if raid_progression_summary is None or "Not Found" in raid_progression_summary or "No H/M Data" in raid_progression_summary:
                 raid_progression_summary = None
        else:
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
            # last_updated is handled by default/onupdate
        ))

    print(f"\nFetched details for {len(characters_to_insert)} members (Rank <= 4). Made {api_call_count} API calls.")

    # --- Insert Data into Newly Created Table ---
    db_session = SessionLocal()
    try:
        print(f"Inserting {len(characters_to_insert)} characters into the database...")
        # Use bulk_save_objects for potentially better performance if supported and needed
        # db_session.bulk_save_objects(characters_to_insert)
        # Or simple add_all
        db_session.add_all(characters_to_insert)
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
    # [PASTE PREVIOUS MAIN EXECUTION BLOCK HERE - UNCHANGED]
    # ... (Checks env vars, calls update_database) ...
    required_vars = ['BLIZZARD_CLIENT_ID', 'BLIZZARD_CLIENT_SECRET', 'GUILD_NAME', 'REALM_SLUG', 'REGION', 'DATABASE_URL']
    print(f"Checking environment variables...")
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    if missing_vars:
        print(f"Error: Missing required environment variables: {', '.join(missing_vars)}")
        if 'DATABASE_URL' in missing_vars and DATABASE_URI.startswith('sqlite:///'):
             print("Attempting to use default local SQLite DB: guild_data.db")
             api_keys_missing = [var for var in required_vars[:-1] if not os.environ.get(var)] # Check others
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
```
*Self-correction: Need to paste the API helper functions and the main execution block into the script above where indicated.*
*(Assume the API helper functions and the `if __name__ == "__main__":` block are copied from the previous `update_roster_data.py` version and pasted into this script where indicated