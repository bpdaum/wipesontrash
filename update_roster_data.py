# update_roster_data.py
import os
import time
from datetime import datetime
import json

# --- Standalone SQLAlchemy setup ---
from sqlalchemy import create_engine, Column, Integer, String, DateTime, UniqueConstraint, MetaData, Index, ForeignKey, Float, Boolean
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy.sql import func
from sqlalchemy.exc import OperationalError, IntegrityError

# --- Import from helper_functions ---
try:
    # Ensure these are available in your helper_functions.py or defined globally if not
    from helper_functions import get_blizzard_access_token, make_api_request, BLIZZARD_API_BASE_URL, REGION
except ImportError:
    print("Error: helper_functions.py or expected variables (BLIZZARD_API_BASE_URL, REGION) not found.", flush=True)
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
    spec_name = Column(String(50)) 
    main_spec_override = Column(String(50), nullable=True) 
    role = Column(String(15))      
    status = Column(String(15), nullable=False, index=True) # Wiper, Member, Wiping Alt
    item_level = Column(Integer, index=True)
    raid_progression = Column(String(200))
    rank = Column(Integer, index=True)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    raid_attendance_percentage = Column(Float, default=0.0, nullable=True)
    avg_wcl_performance = Column(Float, nullable=True)
    
    is_active = Column(Boolean, default=True, nullable=False, index=True) # NEW COLUMN FOR SOFT DELETE

    playable_class = relationship("PlayableClass", back_populates="characters")
    # Child relationships are defined so ORM features work if used, and for schema awareness.
    # This script will not delete from these child tables.
    bis_selections = relationship("CharacterBiS", back_populates="character") 
    attendances = relationship("WCLAttendance", back_populates="character")
    performances = relationship("WCLPerformance", back_populates="character")

    __table_args__ = ( UniqueConstraint('name', 'realm_slug', name='_name_realm_uc'), )
    def __repr__(self): return f'<Character {self.name}-{self.realm_slug} (Active: {self.is_active})>'

# --- Dependent Table Models (ensure these match your actual schema) ---
class PlayableSlot(Base):
    __tablename__ = 'playable_slot'
    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False) # For completeness if create_all is used
    # ... other columns as in wow_info.py ...

class Item(Base):
    __tablename__ = 'item'
    id = Column(Integer, primary_key=True) # Blizzard Item ID
    name = Column(String(255), nullable=False, index=True) # For completeness
    # ... other columns as in wow_info.py ...

class CharacterBiS(Base):
    __tablename__ = 'character_bis'
    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(Integer, ForeignKey('character.id'), nullable=False, index=True)
    slot_type_ui = Column(String(50), ForeignKey('playable_slot.type'), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey('item.id'), nullable=True)
    character = relationship("Character", back_populates="bis_selections")
    __table_args__ = (UniqueConstraint('character_id', 'slot_type_ui', name='_character_slot_ui_uc'),)

class WCLReport(Base):
    __tablename__ = 'wcl_report'
    code = Column(String(50), primary_key=True)
    title = Column(String(200)) # For completeness
    # ... other columns as in warcraft_logs.py ...

class WCLAttendance(Base):
    __tablename__ = 'wcl_attendance'
    id = Column(Integer, primary_key=True)
    report_code = Column(String(50), ForeignKey('wcl_report.code'), nullable=False, index=True)
    character_id = Column(Integer, ForeignKey('character.id'), nullable=False, index=True)
    character = relationship("Character", back_populates="attendances")
    __table_args__ = ( UniqueConstraint('report_code', 'character_id', name='_report_char_uc'), )

class WCLPerformance(Base):
    __tablename__ = 'wcl_performance'
    id = Column(Integer, primary_key=True)
    report_code = Column(String(50), ForeignKey('wcl_report.code'), nullable=False, index=True)
    character_id = Column(Integer, ForeignKey('character.id'), nullable=False, index=True)
    encounter_id = Column(Integer, nullable=False) 
    metric = Column(String(20)) 
    character = relationship("Character", back_populates="performances")
    __table_args__ = ( UniqueConstraint('report_code', 'character_id', 'encounter_id', 'metric', name='_perf_uc'), )

# --- Configuration Loading ---
GUILD_NAME = os.environ.get('GUILD_NAME')
REALM_SLUG = os.environ.get('REALM_SLUG')

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

def determine_role_from_spec_and_class(spec_name, class_name):
    if not spec_name or not class_name or class_name == "N/A": return "Unknown"
    if spec_name in TANK_SPECS: return "Tank"
    if spec_name in HEALER_SPECS: return "Healer"
    if class_name in MELEE_DPS_SPECS and spec_name in MELEE_DPS_SPECS.get(class_name, []):
        return "Melee DPS"
    if class_name in RANGED_DPS_SPECS and spec_name in RANGED_DPS_SPECS.get(class_name, []):
        return "Ranged DPS"
    if spec_name: return "DPS" 
    return "Unknown"

# --- Functions to interact with Blizzard API (Assumed to be correct from previous versions) ---
def get_guild_roster_data():
    if not GUILD_NAME or not REALM_SLUG:
        print("Error: GUILD_NAME or REALM_SLUG not configured.", flush=True)
        return None
    access_token = get_blizzard_access_token() 
    if not access_token: return None
    endpoint = f"/data/wow/guild/{REALM_SLUG.lower()}/{GUILD_NAME.lower().replace(' ', '-')}/roster"
    api_url = f"{BLIZZARD_API_BASE_URL}{endpoint}"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"namespace": f"profile-{REGION}", "locale": "en_US"}
    data = make_api_request(api_url, params, headers) 
    if not data: print(f"Failed to fetch Blizzard guild roster from {api_url}", flush=True)
    return data

def get_character_summary_data(realm_slug, character_name):
    access_token = get_blizzard_access_token() 
    if not access_token: return None
    endpoint = f"/profile/wow/character/{realm_slug.lower()}/{character_name.lower()}"
    api_url = f"{BLIZZARD_API_BASE_URL}{endpoint}"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"namespace": f"profile-{REGION}", "locale": "en_US"}
    return make_api_request(api_url, params, headers) 

def get_character_raid_progression_data(realm_slug, character_name):
    access_token = get_blizzard_access_token() 
    if not access_token: return None
    endpoint = f"/profile/wow/character/{realm_slug.lower()}/{character_name.lower()}/encounters/raids"
    api_url = f"{BLIZZARD_API_BASE_URL}{endpoint}"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"namespace": f"profile-{REGION}", "locale": "en_US"}
    return make_api_request(api_url, params, headers) 

def summarize_raid_progression(raid_data):
    target_expansion_name = "The War Within" 
    target_raid_name = "Liberation of Undermine" 
    short_raid_name = "Undermine" 
    if not raid_data or 'expansions' not in raid_data: return None, -1
    heroic_kills, heroic_total, mythic_kills, mythic_total, raid_found = -1, 0, -1, 0, False
    for expansion in raid_data.get('expansions', []):
        if expansion.get('expansion', {}).get('name') == target_expansion_name:
            for instance in expansion.get('instances', []):
                if instance.get('instance', {}).get('name') == target_raid_name:
                    raid_found = True
                    for mode in instance.get('modes', []):
                        progress = mode.get('progress', {})
                        if mode.get('difficulty', {}).get('type') == "HEROIC":
                            heroic_kills, heroic_total = progress.get('completed_count', 0), progress.get('total_count', 0)
                        elif mode.get('difficulty', {}).get('type') == "MYTHIC":
                            mythic_kills, mythic_total = progress.get('completed_count', 0), progress.get('total_count', 0)
                    break
            if raid_found: break
    if not raid_found: return f"{short_raid_name}: Not Found", -1
    summary_parts = []
    if heroic_kills != -1 and heroic_total > 0: summary_parts.append(f"{heroic_kills}/{heroic_total}H")
    if mythic_kills != -1 and mythic_total > 0: summary_parts.append(f"{mythic_kills}/{mythic_total}M")
    return f"{short_raid_name}: {' '.join(summary_parts) if summary_parts else 'No H/M Data'}", \
           (0 if heroic_kills == -1 else heroic_kills)

# --- Main Database Update Function ---
def update_character_roster():
    print("Starting Character Roster update process (soft delete approach)...", flush=True)
    start_time = time.time()
    db_session = SessionLocal()
    api_calls_for_details = 0 # Initialize api_calls_for_details here

    try:
        # Ensure all table structures exist.
        tables_to_ensure = [
            PlayableClass.__table__, PlayableSlot.__table__, Item.__table__, WCLReport.__table__,
            Character.__table__, CharacterBiS.__table__, WCLAttendance.__table__, WCLPerformance.__table__
        ]
        Base.metadata.create_all(engine, tables=tables_to_ensure, checkfirst=True)
        print("Ensured all relevant table structures exist.", flush=True)

        # 1. Fetch current guild roster from Blizzard API
        roster_data_from_api = get_guild_roster_data()
        if not roster_data_from_api or 'members' not in roster_data_from_api:
            print("Error: Failed to fetch guild roster from API. Aborting update.", flush=True)
            # db_session.close() # Ensure session is closed if we exit early
            return

        api_character_ids = set()
        api_characters_details = {} 

        for member_entry in roster_data_from_api['members']:
            character_info = member_entry.get('character', {})
            char_id = character_info.get('id')
            rank_from_api = member_entry.get('rank')

            if rank_from_api is None or rank_from_api > 4 or not char_id: 
                continue
            
            api_character_ids.add(char_id)
            api_characters_details[char_id] = {
                "name": character_info.get('name'),
                "realm_slug": character_info.get('realm', {}).get('slug'),
                "level": character_info.get('level'),
                "class_id": character_info.get('playable_class', {}).get('id'),
                "rank": rank_from_api
            }
        
        print(f"Fetched {len(api_character_ids)} active members (rank <=4) from API.", flush=True)

        # 2. Fetch existing characters from DB and their user-settable attributes
        db_characters_query = db_session.query(Character.id, Character.main_spec_override, Character.status, Character.is_active).all()
        db_character_map = {
            char.id: {
                "db_object": db_session.query(Character).get(char.id), # Get full object for update
                "main_spec_override": char.main_spec_override,
                "user_set_status": char.status if char.status in ['Wiper', 'Member', 'Wiping Alt'] else None,
                "was_active": char.is_active
            } for char in db_characters_query
        }
        print(f"Fetched {len(db_character_map)} characters' metadata from DB.", flush=True)

        # 3. Get PlayableClass map
        local_class_map = {cls.id: cls.name for cls in db_session.query(PlayableClass).all()}
        if not local_class_map:
             print("CRITICAL ERROR: PlayableClass map is empty. Run wow_info.py first.", flush=True)
             # db_session.close() # Ensure session is closed
             return

        # api_calls_for_details = 0 # Moved initialization to the top of the function

        # 4. Process characters: update existing, insert new
        for char_id, api_details in api_characters_details.items():
            print(f"Processing API character: {api_details['name']}-{api_details['realm_slug']} (ID: {char_id})", flush=True)
            api_calls_for_details += 1
            summary_data = get_character_summary_data(api_details['realm_slug'], api_details['name'])
            prog_data = get_character_raid_progression_data(api_details['realm_slug'], api_details['name'])
            time.sleep(0.05) # API call delay

            api_spec_name, item_level = None, None
            if summary_data:
                item_level = summary_data.get('average_item_level')
                api_spec_name = summary_data.get('active_spec', {}).get('name')
            
            raid_progression_summary, heroic_kills = (None, -1)
            if prog_data:
                raid_progression_summary, heroic_kills = summarize_raid_progression(prog_data)

            db_class_name = local_class_map.get(api_details['class_id'], "Unknown")
            
            spec_override = db_character_map.get(char_id, {}).get('main_spec_override')
            effective_spec_name = spec_override if spec_override else api_spec_name
            final_role = determine_role_from_spec_and_class(effective_spec_name, db_class_name)

            calculated_status_for_active = "Member"
            if item_level is None or item_level < 650: calculated_status_for_active = "Wiping Alt"
            elif heroic_kills > 6 : calculated_status_for_active = "Wiper"
            elif 0 <= heroic_kills <= 6: calculated_status_for_active = "Member"
            
            user_set_status = db_character_map.get(char_id, {}).get('user_set_status')
            final_status = user_set_status if user_set_status else calculated_status_for_active

            character_data = db_character_map.get(char_id)
            if character_data and character_data["db_object"]:
                char_to_update = character_data["db_object"]
                print(f"  Updating existing character: {char_to_update.name}", flush=True)
                char_to_update.name, char_to_update.realm_slug = api_details['name'], api_details['realm_slug']
                char_to_update.level, char_to_update.class_id = api_details['level'], api_details['class_id']
                char_to_update.class_name, char_to_update.spec_name = db_class_name, api_spec_name
                char_to_update.role, char_to_update.status = final_role, final_status
                char_to_update.item_level, char_to_update.raid_progression = item_level, raid_progression_summary
                char_to_update.rank, char_to_update.is_active = api_details['rank'], True
                char_to_update.last_updated = datetime.utcnow()
            else:
                print(f"  Inserting new character: {api_details['name']}", flush=True)
                new_char = Character(
                    id=char_id, name=api_details['name'], realm_slug=api_details['realm_slug'],
                    level=api_details['level'], class_id=api_details['class_id'], class_name=db_class_name,
                    spec_name=api_spec_name, main_spec_override=spec_override, 
                    role=final_role, status=final_status, item_level=item_level,
                    raid_progression=raid_progression_summary, rank=api_details['rank'],
                    is_active=True, last_updated=datetime.utcnow()
                )
                db_session.add(new_char)
        
        # 5. Mark characters no longer in API roster (but in DB) as inactive
        db_character_ids = set(db_character_map.keys())
        ids_to_deactivate = db_character_ids - api_character_ids
        
        for char_id_to_deactivate in ids_to_deactivate:
            character_data = db_character_map.get(char_id_to_deactivate)
            if character_data and character_data["db_object"] and character_data["was_active"]: # Only deactivate if previously active
                char_to_deactivate_obj = character_data["db_object"]
                print(f"  Marking character as inactive: {char_to_deactivate_obj.name} (ID: {char_id_to_deactivate})", flush=True)
                char_to_deactivate_obj.is_active = False
                # Note: rank, item_level, api_spec_name, raid_progression for inactive members will become stale.
                # Status is preserved if it was user-set, otherwise it might reflect their last active state.
                char_to_deactivate_obj.last_updated = datetime.utcnow()
        
        db_session.commit()
        print("Character data updated (soft delete approach). Child table data preserved.", flush=True)

    except OperationalError as oe:
        db_session.rollback()
        print(f"DATABASE OPERATIONAL ERROR: {oe}", flush=True)
    except Exception as e:
        db_session.rollback()
        print(f"UNEXPECTED ERROR during character update: {e}", flush=True)
        import traceback
        traceback.print_exc()
    finally:
        db_session.close()
        print("Database session closed.", flush=True)

    print(f"Character Roster update finished in {round(time.time() - start_time, 2)} seconds. Made {api_calls_for_details} Blizzard API calls for character details.", flush=True)

if __name__ == "__main__":
    required_vars = ['BLIZZARD_CLIENT_ID', 'BLIZZARD_CLIENT_SECRET', 'GUILD_NAME', 'REALM_SLUG', 'REGION', 'DATABASE_URL']
    print("Checking environment variables...", flush=True)
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    if missing_vars:
        print(f"Error: Missing env vars: {', '.join(missing_vars)}", flush=True)
        exit(1)
    
    print("All required environment variables found.", flush=True)
    update_character_roster()
