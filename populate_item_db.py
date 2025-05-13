# populate_item_db.py
import os
import requests
import time
from datetime import datetime
import json

from sqlalchemy import create_engine, Column, Integer, String, DateTime, UniqueConstraint, MetaData, Index, ForeignKey, Float
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy.sql import func
from sqlalchemy.exc import OperationalError, IntegrityError
# import pytz # Not strictly needed in this script if not doing timezone conversions here

# --- Database Setup ---
DATABASE_URI = os.environ.get('DATABASE_URL')
if not DATABASE_URI:
    print("FATAL: DATABASE_URL environment variable not set.", flush=True)
    exit(1)
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

# --- Models ---
# Define all models that are part of your schema so Base.metadata is complete
# This script will only actively manage (delete from, add to) item-related tables.

class PlayableClass(Base):
    __tablename__ = 'playable_class'
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)
    specs = relationship("PlayableSpec", back_populates="playable_class", cascade="all, delete-orphan")
    characters = relationship("Character", back_populates="playable_class")
    def __repr__(self): return f'<PlayableClass {self.name}>'

class PlayableSpec(Base):
    __tablename__ = 'playable_spec'
    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    class_id = Column(Integer, ForeignKey('playable_class.id'), nullable=False)
    playable_class = relationship("PlayableClass", back_populates="specs")
    def __repr__(self): return f'<PlayableSpec {self.name} (Class ID: {self.class_id})>'

class PlayableSlot(Base):
    __tablename__ = 'playable_slot'
    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(50), unique=True, nullable=False, index=True) # Blizzard API's inventory_type.type string
    name = Column(String(100), nullable=False) # User-friendly name
    display_order = Column(Integer, default=0) # For ordering slots in the UI

    items = relationship("Item", back_populates="slot", cascade="all, delete-orphan")
    bis_selections = relationship("CharacterBiS", back_populates="slot", cascade="all, delete-orphan")
    def __repr__(self): return f'<PlayableSlot Name: {self.name} Type:({self.type})>'

class DataSource(Base):
    __tablename__ = 'data_source'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), unique=True, nullable=False) # e.g., "Liberation of Undermine"
    type = Column(String(50)) # e.g., "Raid", "Dungeon"

    items = relationship("Item", back_populates="source", cascade="all, delete-orphan")
    def __repr__(self): return f'<DataSource {self.name}>'

class Item(Base):
    __tablename__ = 'item'
    id = Column(Integer, primary_key=True) # Blizzard Item ID
    name = Column(String(255), nullable=False, index=True)
    quality = Column(String(20)) # e.g., "EPIC"
    icon_url = Column(String(512), nullable=True)
    slot_type = Column(String(50), ForeignKey('playable_slot.type'), nullable=False, index=True)
    slot = relationship("PlayableSlot", back_populates="items")
    source_id = Column(Integer, ForeignKey('data_source.id'), nullable=True, index=True)
    source = relationship("DataSource", back_populates="items")
    source_details = Column(String(255)) # e.g., Boss name
    bis_selections = relationship("CharacterBiS", back_populates="item", cascade="all, delete-orphan")
    def __repr__(self): return f'<Item {self.name} (ID: {self.id})>'

class Character(Base): # Defined for schema creation, not populated by this script
    __tablename__ = 'character'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    realm_slug = Column(String(100), nullable=False)
    # Add other fields that WCLAttendance or WCLPerformance might reference if needed for schema creation
    # For this script, only 'id' is strictly necessary for CharacterBiS's foreign key.
    # However, to be safe and match other scripts, include all fields.
    level = Column(Integer)
    class_id = Column(Integer, ForeignKey('playable_class.id'))
    class_name = Column(String(50))
    spec_name = Column(String(50))
    main_spec_override = Column(String(50), nullable=True)
    role = Column(String(15))
    status = Column(String(15), nullable=False, index=True)
    item_level = Column(Integer, index=True)
    raid_progression = Column(String(200))
    rank = Column(Integer, index=True)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    raid_attendance_percentage = Column(Float, default=0.0, nullable=True)
    avg_wcl_performance = Column(Float, nullable=True)

    playable_class = relationship("PlayableClass", back_populates="characters")
    bis_selections = relationship("CharacterBiS", back_populates="character", cascade="all, delete-orphan")
    attendances = relationship("WCLAttendance", back_populates="character", cascade="all, delete-orphan")
    performances = relationship("WCLPerformance", back_populates="character", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint('name', 'realm_slug', name='_name_realm_uc'),)
    def __repr__(self): return f'<Character {self.name}-{self.realm_slug}>'


class CharacterBiS(Base):
    __tablename__ = 'character_bis'
    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(Integer, ForeignKey('character.id'), nullable=False, index=True)
    slot_type_ui = Column(String(50), ForeignKey('playable_slot.type'), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey('item.id'), nullable=True)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    character = relationship("Character", back_populates="bis_selections")
    slot = relationship("PlayableSlot", foreign_keys=[slot_type_ui])
    item = relationship("Item", back_populates="bis_selections")
    __table_args__ = (UniqueConstraint('character_id', 'slot_type_ui', name='_character_slot_ui_uc'),)
    def __repr__(self): return f'<CharacterBiS CharID: {self.character_id} SlotUI: {self.slot_type_ui} ItemID: {self.item_id}>'

class WCLReport(Base):
    __tablename__ = 'wcl_report'
    code = Column(String(50), primary_key=True)
    title = Column(String(200))
    start_time = Column(DateTime, index=True)
    end_time = Column(DateTime)
    owner_name = Column(String(100))
    fetched_at = Column(DateTime, default=datetime.utcnow)
    attendances = relationship("WCLAttendance", back_populates="report", cascade="all, delete-orphan")
    performances = relationship("WCLPerformance", back_populates="report", cascade="all, delete-orphan")
    def __repr__(self): return f'<WCLReport {self.code} ({self.title})>'

class WCLAttendance(Base):
    __tablename__ = 'wcl_attendance'
    id = Column(Integer, primary_key=True)
    report_code = Column(String(50), ForeignKey('wcl_report.code'), nullable=False, index=True)
    character_id = Column(Integer, ForeignKey('character.id'), nullable=False, index=True)
    report = relationship("WCLReport", back_populates="attendances")
    character = relationship("Character", back_populates="attendances")
    __table_args__ = ( UniqueConstraint('report_code', 'character_id', name='_report_char_uc'), )
    def __repr__(self): return f'<WCLAttendance Report={self.report_code} CharacterID={self.character_id}>'

class WCLPerformance(Base):
    __tablename__ = 'wcl_performance'
    id = Column(Integer, primary_key=True)
    report_code = Column(String(50), ForeignKey('wcl_report.code'), nullable=False, index=True)
    character_id = Column(Integer, ForeignKey('character.id'), nullable=False, index=True)
    encounter_id = Column(Integer, nullable=False)
    encounter_name = Column(String(100))
    spec_name = Column(String(50))
    metric = Column(String(20))
    rank_percentile = Column(Float)
    report = relationship("WCLReport", back_populates="performances")
    character = relationship("Character", back_populates="performances")
    __table_args__ = ( UniqueConstraint('report_code', 'character_id', 'encounter_id', 'metric', name='_perf_uc'), )
    def __repr__(self): return f'<WCLPerformance Report={self.report_code} CharID={self.character_id} Enc={self.encounter_name} Metric={self.metric} Perf={self.rank_percentile}>'


# --- Blizzard API Configuration ---
BLIZZARD_CLIENT_ID = os.environ.get('BLIZZARD_CLIENT_ID')
BLIZZARD_CLIENT_SECRET = os.environ.get('BLIZZARD_CLIENT_SECRET')
REGION = os.environ.get('REGION', 'us').lower()
BLIZZARD_TOKEN_URL = f"https://{REGION}.battle.net/oauth/token"
BLIZZARD_API_BASE_URL = f"https://{REGION}.api.blizzard.com"
blizzard_access_token_cache = { "token": None, "expires_at": 0 }

# --- API Helper Functions ---
def get_blizzard_access_token():
    """ Retrieves Blizzard access token, uses cache. """
    global blizzard_access_token_cache
    current_time = time.time()
    if blizzard_access_token_cache["token"] and blizzard_access_token_cache["expires_at"] > current_time + 60:
        return blizzard_access_token_cache["token"]
    if not BLIZZARD_CLIENT_ID or not BLIZZARD_CLIENT_SECRET:
        print("Error: BLIZZARD_CLIENT_ID or BLIZZARD_CLIENT_SECRET not set.", flush=True)
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

def make_blizzard_api_request(endpoint, params=None, full_url=None, max_retries=3, retry_delay=5):
    """ Helper function to make Blizzard API requests with retries. """
    access_token = get_blizzard_access_token()
    if not access_token: return None
    
    api_url = full_url if full_url else f"{BLIZZARD_API_BASE_URL}{endpoint}"
    headers = {"Authorization": f"Bearer {access_token}"}
    
    if params is None:
        params = {}
    
    if not full_url and "namespace" not in params:
         params["namespace"] = f"static-{REGION}"
    if not full_url and "locale" not in params:
        params["locale"] = "en_US"

    # print(f"DEBUG: Requesting URL: {api_url} with params: {params}", flush=True)

    for attempt in range(max_retries):
        try:
            response = requests.get(api_url, params=params, headers=headers, timeout=30)
            # print(f"DEBUG: API call to {response.url} - Status: {response.status_code}", flush=True)
            if response.status_code == 404:
                print(f"Warning: 404 Not Found for API URL: {response.url}", flush=True)
                return None
            response.raise_for_status()
            return response.json()
        except requests.exceptions.Timeout:
            print(f"Timeout error for {api_url}. Attempt {attempt + 1}/{max_retries}.", flush=True)
            if attempt < max_retries - 1: time.sleep(retry_delay)
            else: print(f"Max retries reached for timeout at {api_url}.", flush=True); return None
        except requests.exceptions.HTTPError as e:
            if e.response.status_code in [500, 502, 503, 504] and attempt < max_retries - 1:
                print(f"HTTP Error {e.response.status_code} for {api_url}. Retrying...", flush=True)
                time.sleep(retry_delay)
            else:
                print(f"HTTP Error for {api_url}: {e}", flush=True)
                if e.response is not None:
                    try: print(f"Error Response Body: {e.response.json()}", flush=True)
                    except: print(f"Error Response Body: {e.response.text}", flush=True)
                return None
        except requests.exceptions.RequestException as e:
            print(f"Network error for {api_url}: {e}. Retrying...", flush=True)
            if attempt < max_retries - 1: time.sleep(retry_delay)
            else: print(f"Max retries reached for network error at {api_url}.", flush=True); return None
        except Exception as e:
            print(f"Unexpected error for {api_url}: {e}", flush=True); return None
    return None

# --- Data Population Functions ---

def populate_playable_slots(db_session):
    """Pre-populates the PlayableSlot table with standard equipment slots."""
    print("Populating Playable Slots...", flush=True)
    slots_data = [
        {"type": "HEAD", "name": "Head", "display_order": 1},
        {"type": "NECK", "name": "Neck", "display_order": 2},
        {"type": "SHOULDER", "name": "Shoulder", "display_order": 3},
        {"type": "BACK", "name": "Back", "display_order": 4},
        {"type": "CLOAK", "name": "Back (Cloak API)", "display_order": 4},
        {"type": "CHEST", "name": "Chest", "display_order": 5},
        {"type": "ROBE", "name": "Chest (Robe API)", "display_order": 5},
        {"type": "SHIRT", "name": "Shirt", "display_order": 6},
        {"type": "TABARD", "name": "Tabard", "display_order": 7},
        {"type": "WRIST", "name": "Wrist", "display_order": 8},
        {"type": "HANDS", "name": "Hands", "display_order": 9},
        {"type": "HAND", "name": "Hands (API)", "display_order": 9},
        {"type": "WAIST", "name": "Waist", "display_order": 10},
        {"type": "LEGS", "name": "Legs", "display_order": 11},
        {"type": "FEET", "name": "Feet", "display_order": 12},
        {"type": "FINGER", "name": "Finger (API Generic)", "display_order": 13},
        {"type": "FINGER1", "name": "Finger 1", "display_order": 13},
        {"type": "FINGER2", "name": "Finger 2", "display_order": 14},
        {"type": "TRINKET", "name": "Trinket (API Generic)", "display_order": 15},
        {"type": "TRINKET1", "name": "Trinket 1", "display_order": 15},
        {"type": "TRINKET2", "name": "Trinket 2", "display_order": 16},
        {"type": "WEAPON", "name": "Weapon (Generic API)", "display_order": 17},
        {"type": "MAIN_HAND", "name": "Main Hand", "display_order": 17},
        {"type": "OFF_HAND", "name": "Off Hand", "display_order": 18},
        {"type": "SHIELD", "name": "Shield", "display_order": 18},
        {"type": "HOLDABLE", "name": "Holdable (Off-hand)", "display_order": 18},
        {"type": "ONE_HAND", "name": "One-Hand", "display_order": 20},
        {"type": "TWO_HAND", "name": "Two-Hand", "display_order": 21},
        {"type": "TWOHWEAPON", "name": "Two-Hand (API)", "display_order": 21}
    ]
    for slot_data in slots_data:
        slot = db_session.query(PlayableSlot).filter_by(type=slot_data["type"]).first()
        if not slot:
            slot = PlayableSlot(type=slot_data["type"], name=slot_data["name"], display_order=slot_data["display_order"])
            db_session.add(slot)
    try:
        db_session.commit()
        print("PlayableSlot table populated/verified.", flush=True)
    except Exception as e:
        db_session.rollback()
        print(f"Error populating PlayableSlot table: {e}", flush=True)


def populate_data_sources(db_session):
    """Pre-populates the DataSource table."""
    print("Populating Data Sources...", flush=True)
    sources_data = [
        {"name": "Liberation of Undermine", "type": "Raid"},
        {"name": "Mythic+ Season 2 Dungeons", "type": "Dungeon"}
    ]
    for source_data in sources_data:
        source = db_session.query(DataSource).filter_by(name=source_data["name"]).first()
        if not source:
            source = DataSource(name=source_data["name"], type=source_data["type"])
            db_session.add(source)
    try:
        db_session.commit()
        print("DataSource table populated/verified.", flush=True)
    except Exception as e:
        db_session.rollback()
        print(f"Error populating DataSource table: {e}", flush=True)
    return {source.name: source.id for source in db_session.query(DataSource).all()}


def find_journal_instance_id(instance_name_to_find, instance_type="instance"):
    """Queries the Blizzard API for the journal index and finds the ID for a given instance/dungeon name."""
    print(f"Attempting to find Journal ID for {instance_type}: '{instance_name_to_find}'", flush=True)
    endpoint = f"/data/wow/journal-{instance_type}/index"
    index_data = make_blizzard_api_request(endpoint)

    if index_data and f"{instance_type}s" in index_data:
        for instance in index_data[f"{instance_type}s"]:
            if instance.get("name", "").lower() == instance_name_to_find.lower():
                instance_id = instance.get("id")
                print(f"Found {instance_type} '{instance_name_to_find}' with ID: {instance_id}", flush=True)
                return instance_id
        print(f"Error: {instance_type.capitalize()} '{instance_name_to_find}' not found in the journal index.", flush=True)
        return None
    else:
        print(f"Error: Could not fetch or parse journal {instance_type} index.", flush=True)
        if index_data: print(f"DEBUG: Journal {instance_type.capitalize()} Index Response: {json.dumps(index_data, indent=2)}", flush=True)
        return None

def fetch_and_store_source_items(db_session, source_name_friendly, source_journal_id, data_source_id, source_type="raid"):
    """Fetches items for a given raid or dungeon and stores them."""
    print(f"Fetching items for {source_type}: {source_name_friendly} (Journal ID: {source_journal_id})", flush=True)

    instance_params = { "namespace": f"static-{REGION}", "locale": "en_US" }
    if source_type == "raid":
        instance_api_endpoint = f"/data/wow/journal-instance/{source_journal_id}"
    elif source_type == "dungeon":
        instance_api_endpoint = f"/data/wow/journal-dungeon/{source_journal_id}"
    else:
        print(f"Error: Unknown source_type '{source_type}' for item fetching.", flush=True)
        return

    instance_data = make_blizzard_api_request(instance_api_endpoint, params=instance_params)

    if not instance_data or "encounters" not in instance_data:
        print(f"Error: Could not fetch instance data or encounters for {source_type} ID {source_journal_id}. Response: {instance_data}", flush=True)
        return

    items_added_count = 0
    for encounter_ref in instance_data["encounters"]:
        encounter_id = encounter_ref["id"]
        encounter_name = encounter_ref["name"]
        print(f"  Fetching loot for encounter: {encounter_name} (ID: {encounter_id}) in {source_name_friendly}", flush=True)
        
        items_to_process = encounter_ref.get("items")

        if not items_to_process: # If not, fetch the detailed encounter data (more common for raids)
            encounter_detail_data = make_blizzard_api_request(f"/data/wow/journal-encounter/{encounter_id}", params=instance_params)
            if not encounter_detail_data or "items" not in encounter_detail_data:
                print(f"    Warning: No 'items' section found for encounter {encounter_name} (ID: {encounter_id})", flush=True)
                continue
            items_to_process = encounter_detail_data["items"]
        
        if not items_to_process:
            print(f"    No items found to process for encounter {encounter_name}", flush=True)
            continue

        for item_entry in items_to_process:
            item_ref = item_entry.get("item")
            if not item_ref or "id" not in item_ref:
                continue
            
            item_id = item_ref["id"]
            item_detail_data = make_blizzard_api_request(f"/data/wow/item/{item_id}", params=instance_params)
            if not item_detail_data:
                time.sleep(0.05)
                continue

            item_name = item_detail_data.get("name")
            item_quality_data = item_detail_data.get("quality", {})
            item_quality = item_quality_data.get("name", "Unknown").upper() if isinstance(item_quality_data, dict) else "Unknown"
            
            inventory_type_info = item_detail_data.get("inventory_type", {})
            slot_type_api = inventory_type_info.get("type") if isinstance(inventory_type_info, dict) else None
            
            icon_url = None
            media_key_href = item_detail_data.get("media", {}).get("key", {}).get("href")
            if media_key_href:
                item_media_data = make_blizzard_api_request(None, full_url=media_key_href, params=instance_params)
                if item_media_data and "assets" in item_media_data:
                    for asset in item_media_data["assets"]:
                        if asset.get("key") == "icon":
                            icon_url = asset.get("value")
                            break
            
            if item_name and item_quality == "EPIC" and slot_type_api:
                if slot_type_api == "NON_EQUIP":
                    continue

                slot_exists = db_session.query(PlayableSlot).filter_by(type=slot_type_api).first()
                if not slot_exists:
                    print(f"      CRITICAL WARNING: API slot type '{slot_type_api}' for item '{item_name}' (ID: {item_id}) not found in PlayableSlot table. Please add it to populate_playable_slots() in this script!", flush=True)
                    continue

                existing_item = db_session.query(Item).filter_by(id=item_id).first()
                if not existing_item:
                    new_item = Item(
                        id=item_id, name=item_name, quality=item_quality,
                        slot_type=slot_type_api,
                        source_id=data_source_id,
                        source_details=f"{source_name_friendly} - {encounter_name}",
                        icon_url=icon_url
                    )
                    db_session.add(new_item)
                    items_added_count += 1
            
            time.sleep(0.05)

        try:
            db_session.commit()
        except IntegrityError as ie:
            db_session.rollback()
            print(f"    DB Integrity Error for encounter {encounter_name}: {ie}", flush=True)
        except Exception as e:
            db_session.rollback()
            print(f"    Error committing items for encounter {encounter_name}: {e}", flush=True)

        print(f"  Finished encounter {encounter_name}. Items added so far for this {source_type}: {items_added_count}", flush=True)
        time.sleep(0.2)

    print(f"Finished processing {source_type} {source_name_friendly}. Total new items added: {items_added_count}", flush=True)


def main():
    """Main function to orchestrate item data population."""
    print("Starting Item Database Population Script...", flush=True)
    db_session = SessionLocal()

    print("Ensuring all database tables exist (will create if not present)...", flush=True)
    # Create all tables defined in Base if they don't exist.
    # This script will NOT drop tables managed by update_roster_data.py
    Base.metadata.create_all(engine, checkfirst=True)
    print("Database tables verified/created.", flush=True)

    # Clear ONLY the tables this script is responsible for before repopulating
    print("Clearing item-related tables (Item, DataSource, PlayableSlot, CharacterBiS)...", flush=True)
    try:
        # Delete in order of dependency
        db_session.query(CharacterBiS).delete(synchronize_session=False)
        db_session.query(Item).delete(synchronize_session=False)
        db_session.query(DataSource).delete(synchronize_session=False)
        db_session.query(PlayableSlot).delete(synchronize_session=False)
        db_session.commit()
        print("Item-related tables cleared.", flush=True)
    except Exception as e:
        db_session.rollback()
        print(f"Error clearing item-related tables: {e}", flush=True)
        db_session.close()
        return

    populate_playable_slots(db_session) # This will re-add them
    data_sources = populate_data_sources(db_session) # This will re-add them

    # --- Populate Raid Items ---
    target_raid_name_for_items = "Liberation of Undermine"
    liberation_of_undermine_journal_id = find_journal_instance_id(target_raid_name_for_items, instance_type="instance")
    
    if liberation_of_undermine_journal_id:
        lou_source_id = data_sources.get("Liberation of Undermine")
        if lou_source_id:
            fetch_and_store_source_items(db_session, "Liberation of Undermine", liberation_of_undermine_journal_id, lou_source_id, source_type="raid")
        else:
            print(f"Error: Data source for '{target_raid_name_for_items}' not found in DataSource table.", flush=True)
    else:
        print(f"Could not find Journal ID for '{target_raid_name_for_items}'. Skipping item fetch for this raid.", flush=True)

    # --- Populate Mythic+ Season 2 Dungeon Items ---
    print("\n--- Processing Mythic+ Season 2 Dungeons ---", flush=True)
    mplus_s2_source_name = "Mythic+ Season 2 Dungeons"
    mplus_s2_source_id = data_sources.get(mplus_s2_source_name)

    if not mplus_s2_source_id:
        print(f"Error: '{mplus_s2_source_name}' data source not found.", flush=True)
    else:
        mplus_s2_dungeon_names = [
            "THE MOTHERLODE!!", "Theater of Pain", "Cinderbrew Meadery",
            "Priory of the Sacred Flame", "The Rookery", "Darkflame Cleft",
            "Operation: Floodgate", "Operation: Mechagon"
        ]
        
        for dungeon_name in mplus_s2_dungeon_names:
            print(f"\nLooking for dungeon: {dungeon_name}", flush=True)
            dungeon_journal_id = find_journal_instance_id(dungeon_name, instance_type="dungeon")
            if dungeon_journal_id:
                fetch_and_store_source_items(db_session, dungeon_name, dungeon_journal_id, mplus_s2_source_id, source_type="dungeon")
            else:
                print(f"Could not find Journal ID for dungeon '{dungeon_name}'. Skipping item fetch.", flush=True)
            time.sleep(1) # Pause between dungeons

    db_session.close()
    print("Item Database Population Script Finished.", flush=True)

if __name__ == "__main__":
    if not BLIZZARD_CLIENT_ID or not BLIZZARD_CLIENT_SECRET:
        print("FATAL: BLIZZARD_CLIENT_ID or BLIZZARD_CLIENT_SECRET environment variables not set.", flush=True)
        exit(1)
    main()
