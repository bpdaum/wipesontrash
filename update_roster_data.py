# update_roster_data.py
import os
import time
from datetime import datetime
import json

# --- Standalone SQLAlchemy setup ---
from sqlalchemy import create_engine, Column, Integer, String, DateTime, UniqueConstraint, MetaData, Index, ForeignKey, Float
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy.sql import func
from sqlalchemy.exc import OperationalError, IntegrityError

# --- Import from helper_functions ---
try:
    from helper_functions import get_blizzard_access_token, make_api_request
except ImportError:
    print("Error: helper_functions.py not found. Make sure it's in the same directory or Python path.", flush=True)
    exit(1)

# --- Database Setup ---
DATABASE_URI = os.environ.get('DATABASE_URL')
if not DATABASE_URI:
    print("WARNING: DATABASE_URL environment variable not found. Defaulting to local sqlite:///guild_data.db", flush=True)
    DATABASE_URI = 'sqlite:///guild_data.db'
else:
    if DATABASE_URI.startswith("postgres://"):
        DATABASE_URI = DATABASE_URI.replace("postgres://", "postgresql://", 1)

try:
    engine = create_engine(DATABASE_URI)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base = declarative_base()
except Exception as e:
     print(f"Error creating database engine: {e}", flush=True)
     exit(1)

# --- Database Models ---
# Define models that this script interacts with or are dependencies for Character.
# PlayableClass is read from, Character is written to.

class PlayableClass(Base):
    __tablename__ = 'playable_class'
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)
    characters = relationship("Character", back_populates="playable_class")
    def __repr__(self): return f'<PlayableClass {self.name}>'

class Character(Base):
    __tablename__ = 'character'
    id = Column(Integer, primary_key=True) # Blizzard Character ID
    name = Column(String(100), nullable=False)
    realm_slug = Column(String(100), nullable=False)
    level = Column(Integer)
    class_id = Column(Integer, ForeignKey('playable_class.id'))
    class_name = Column(String(50))
    spec_name = Column(String(50)) # API Active Spec
    main_spec_override = Column(String(50), nullable=True) # User override
    role = Column(String(15))      # e.g., Tank, Healer, Melee DPS, Ranged DPS
    status = Column(String(15), nullable=False, index=True) # Calculated/User Status field
    item_level = Column(Integer, index=True)
    raid_progression = Column(String(200))
    rank = Column(Integer, index=True)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    # WCL fields will be updated by warcraft_logs.py, define them here for schema completeness if CharacterBiS depends on them
    raid_attendance_percentage = Column(Float, default=0.0, nullable=True)
    avg_wcl_performance = Column(Float, nullable=True)

    playable_class = relationship("PlayableClass", back_populates="characters")
    # Define relationships to WCL tables if they exist in your full schema,
    # even if this script doesn't populate them, for SQLAlchemy metadata awareness.
    # attendances = relationship("WCLAttendance", back_populates="character", cascade="all, delete-orphan")
    # performances = relationship("WCLPerformance", back_populates="character", cascade="all, delete-orphan")
    # bis_selections = relationship("CharacterBiS", back_populates="character", cascade="all, delete-orphan")


    __table_args__ = ( UniqueConstraint('name', 'realm_slug', name='_name_realm_uc'), )
    def __repr__(self): return f'<Character {self.name}-{self.realm_slug}>'


# --- Configuration Loading ---
GUILD_NAME = os.environ.get('GUILD_NAME')
REALM_SLUG = os.environ.get('REALM_SLUG')
REGION = os.environ.get('REGION', 'us').lower() # Used for API calls via helpers

# --- Role Definitions ---
TANK_SPECS = ["Blood", "Protection", "Guardian", "Brewmaster", "Vengeance"]
HEALER_SPECS = ["Holy", "Discipline", "Restoration", "Mistweaver", "Preservation"]
MELEE_DPS_SPECS = {
    "Warrior": ["Arms", "Fury"], "Paladin": ["Retribution"], "Death Knight": ["Frost", "Unholy"],
    "Shaman": ["Enhancement"], "Hunter": ["Survival"], "Rogue": ["Assassination", "Outlaw", "Subtlety"],
    "Monk": ["Windwalker"], "Demon Hunter": ["Havoc"], "Druid": ["Feral"]
}
RANGED_DPS_SPECS = {
    "Mage": ["Arcane", "Fire", "Frost"], "Warlock": ["Affliction", "Demonology", "Destruction"],
    "Priest": ["Shadow"], "Hunter": ["Beast Mastery", "Marksmanship"], "Druid": ["Balance"],
    "Shaman": ["Elemental"], "Evoker": ["Devastation", "Augmentation"]
}

# --- Helper function for role determination ---
def determine_role_from_spec_and_class(spec_name, class_name):
    """Determines a character's role based on their spec and class."""
    if not spec_name or not class_name or class_name == "N/A": return "Unknown"
    if spec_name in TANK_SPECS: return "Tank"
    if spec_name in HEALER_SPECS: return "Healer"
    if class_name in MELEE_DPS_SPECS and spec_name in MELEE_DPS_SPECS.get(class_name, []):
        return "Melee DPS"
    if class_name in RANGED_DPS_SPECS and spec_name in RANGED_DPS_SPECS.get(class_name, []):
        return "Ranged DPS"
    if spec_name: return "DPS" # Fallback for generic DPS
    return "Unknown"

# --- Functions to interact with Blizzard API (using helpers) ---
def get_guild_roster_data():
    """ Fetches the guild roster from Blizzard API using helper_functions. """
    if not GUILD_NAME or not REALM_SLUG:
        print("Error: GUILD_NAME or REALM_SLUG not configured.", flush=True)
        return None
    access_token = get_blizzard_access_token() # From helper_functions
    if not access_token: return None

    endpoint = f"/data/wow/guild/{REALM_SLUG.lower()}/{GUILD_NAME.lower().replace(' ', '-')}/roster"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"namespace": f"profile-{REGION}", "locale": "en_US"}
    
    print(f"Fetching Blizzard Guild Roster from: {BLIZZARD_API_BASE_URL}{endpoint}", flush=True)
    data = make_api_request(endpoint, params, headers) # From helper_functions
    if data: print("Successfully fetched Blizzard guild roster.", flush=True)
    else: print("Failed to fetch Blizzard guild roster.", flush=True)
    return data

def get_character_summary_data(realm_slug, character_name):
    """ Fetches character profile summary from Blizzard API using helper_functions. """
    access_token = get_blizzard_access_token() # From helper_functions
    if not access_token: return None

    endpoint = f"/profile/wow/character/{realm_slug.lower()}/{character_name.lower()}"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"namespace": f"profile-{REGION}", "locale": "en_US"}
    data = make_api_request(endpoint, params, headers) # From helper_functions
    return data

def get_character_raid_progression_data(realm_slug, character_name):
    """ Fetches character raid encounters from Blizzard API using helper_functions. """
    access_token = get_blizzard_access_token() # From helper_functions
    if not access_token: return None
    endpoint = f"/profile/wow/character/{realm_slug.lower()}/{character_name.lower()}/encounters/raids"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"namespace": f"profile-{REGION}", "locale": "en_US"}
    data = make_api_request(endpoint, params, headers) # From helper_functions
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


# --- Main Database Update Function ---
def update_character_roster():
    """
    Updates the Character table with the latest roster info from Blizzard API,
    preserving user-set spec overrides and statuses.
    """
    print("Starting Character Roster update process...", flush=True)
    start_time = time.time()
    db_session = SessionLocal()

    # 1. Preserve existing overrides
    existing_spec_overrides = {}
    existing_user_statuses = {} # Only statuses that users can set
    user_settable_status_values = ['Wiper', 'Member', 'Wiping Alt'] # Match what app.py allows

    try:
        if engine.dialect.has_table(engine.connect(), Character.__tablename__):
            print("Fetching existing spec overrides and user-set statuses...", flush=True)
            for char_id, spec_override, status_val in db_session.query(Character.id, Character.main_spec_override, Character.status).all():
                if spec_override:
                    existing_spec_overrides[char_id] = spec_override
                if status_val in user_settable_status_values: # Only preserve if it's a user-settable one
                    existing_user_statuses[char_id] = status_val
            print(f"Found {len(existing_spec_overrides)} spec overrides and {len(existing_user_statuses)} user-set statuses to preserve.", flush=True)
        else:
            print("Character table does not exist yet, no overrides/statuses to preserve.", flush=True)
    except Exception as e:
        print(f"Error fetching existing overrides/statuses: {e}", flush=True)
        # Continue, but overrides might be lost if table drop fails later

    # 2. Drop and recreate ONLY the Character table
    try:
        print(f"Attempting to drop table '{Character.__tablename__}' if it exists...", flush=True)
        Character.__table__.drop(engine, checkfirst=True)
        print(f"Table '{Character.__tablename__}' dropped (or did not exist).", flush=True)
        print(f"Creating table '{Character.__tablename__}'...", flush=True)
        # Ensure PlayableClass table exists before Character table due to ForeignKey
        PlayableClass.metadata.create_all(engine, checkfirst=True) # Create if not exists
        Character.__table__.create(engine, checkfirst=True)
        print("Character table created successfully.", flush=True)
    except Exception as e:
        print(f"Error during Character table drop/create: {e}", flush=True)
        db_session.close(); return

    # 3. Fetch class map from DB (populated by wow_info.py)
    local_class_map = {cls.id: cls.name for cls in db_session.query(PlayableClass).all()}
    if not local_class_map:
        print("CRITICAL ERROR: PlayableClass table is empty or not found. Run wow_info.py first. Aborting roster update.", flush=True)
        db_session.close(); return

    # 4. Fetch and process roster
    roster_data = get_guild_roster_data()
    if not roster_data or 'members' not in roster_data:
        print("Error: Failed to fetch guild roster. Aborting update.", flush=True)
        db_session.close(); return

    characters_to_insert = []
    api_call_count = 0

    for member_entry in roster_data['members']:
        character_info = member_entry.get('character', {})
        rank = member_entry.get('rank')
        char_id = character_info.get('id')
        char_name = character_info.get('name')
        char_realm_slug = character_info.get('realm', {}).get('slug')

        if rank is None or rank > 4 or not char_id or not char_name or not char_realm_slug:
            continue
        
        print(f"Processing: {char_name}-{char_realm_slug}", flush=True)
        api_call_count +=1

        class_id = character_info.get('playable_class', {}).get('id')
        db_class_name = local_class_map.get(class_id, "Unknown")

        api_spec_name = None
        role_from_api_spec = "Unknown"
        item_level = None
        raid_progression_summary = None
        heroic_kills = -1

        summary_data = get_character_summary_data(char_realm_slug, char_name)
        if summary_data:
            item_level = summary_data.get('average_item_level')
            active_spec = summary_data.get('active_spec', {})
            api_spec_name = active_spec.get('name')
            role_from_api_spec = determine_role_from_spec_and_class(api_spec_name, db_class_name)

        prog_data = get_character_raid_progression_data(char_realm_slug, char_name)
        if prog_data:
            raid_progression_summary, heroic_kills = summarize_raid_progression(prog_data)

        # Determine final spec and role (considering override)
        spec_override = existing_spec_overrides.get(char_id)
        effective_spec_name = spec_override if spec_override else api_spec_name
        final_role = determine_role_from_spec_and_class(effective_spec_name, db_class_name)

        # Determine status
        calculated_status = "Member"
        if item_level is None or item_level < 650: calculated_status = "Wiping Alt"
        elif heroic_kills > 6: calculated_status = "Wiper"
        elif heroic_kills >= 0 and heroic_kills <= 6: calculated_status = "Member"
        
        final_status = existing_user_statuses.get(char_id, calculated_status)

        characters_to_insert.append(Character(
            id=char_id, name=char_name, realm_slug=char_realm_slug, level=character_info.get('level'),
            class_id=class_id, class_name=db_class_name,
            spec_name=api_spec_name, # Store the spec from API
            main_spec_override=spec_override, # Apply preserved override
            role=final_role, # Role based on effective spec
            status=final_status, # Apply preserved or calculated status
            item_level=item_level,
            raid_progression=raid_progression_summary,
            rank=rank,
            # WCL fields are not handled by this script
            raid_attendance_percentage=None, # Will be updated by warcraft_logs.py
            avg_wcl_performance=None      # Will be updated by warcraft_logs.py
        ))
        time.sleep(0.1) # API call delay

    try:
        if characters_to_insert:
            db_session.add_all(characters_to_insert)
            db_session.commit()
            print(f"Successfully updated/inserted {len(characters_to_insert)} characters.", flush=True)
        else:
            print("No characters to update or insert.", flush=True)
    except Exception as e:
        print(f"Error committing character data: {e}", flush=True)
        db_session.rollback()
    finally:
        db_session.close()

    print(f"Character Roster update finished in {round(time.time() - start_time, 2)} seconds. Made {api_call_count} Blizzard API calls for character details.", flush=True)


if __name__ == "__main__":
    required_vars = ['BLIZZARD_CLIENT_ID', 'BLIZZARD_CLIENT_SECRET', 'GUILD_NAME', 'REALM_SLUG', 'REGION', 'DATABASE_URL']
    print(f"Checking environment variables for update_roster_data.py...", flush=True)
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    if missing_vars:
        print(f"Error: Missing required environment variables: {', '.join(missing_vars)}", flush=True)
        exit(1)
    else:
        print("All required environment variables found.", flush=True)
        update_character_roster()
