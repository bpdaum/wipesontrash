# wow_info.py
import os
import requests 
import time
from datetime import datetime
import json

# --- Standalone SQLAlchemy setup ---
from sqlalchemy import create_engine, Column, Integer, String, DateTime, UniqueConstraint, MetaData, Index, ForeignKey, Float, Boolean
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy.sql import func
from sqlalchemy.exc import OperationalError, IntegrityError

# --- Blizzard API Configuration ---
REGION = os.environ.get('REGION', 'us').lower() 
BLIZZARD_CLIENT_ID = os.environ.get('BLIZZARD_CLIENT_ID')
BLIZZARD_CLIENT_SECRET = os.environ.get('BLIZZARD_CLIENT_SECRET')
BLIZZARD_API_BASE_URL = f"https://{REGION}.api.blizzard.com"

# --- Import from helper_functions ---
try:
    from helper_functions import get_blizzard_access_token, make_api_request as make_blizzard_api_request_helper # Renamed to avoid conflict
except ImportError:
    print("Error: helper_functions.py or expected variables not found.", flush=True)
    exit(1)

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

# --- Database Models ---
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
    type = Column(String(50), unique=True, nullable=False, index=True) 
    name = Column(String(100), nullable=False) 
    display_order = Column(Integer, default=0) 
    items = relationship("Item", back_populates="slot", cascade="all, delete-orphan")
    # Relationship for CharacterBiS referencing PlayableSlot via slot_type_ui
    bis_selections = relationship("CharacterBiS", back_populates="slot", foreign_keys="[CharacterBiS.slot_type_ui]")
    def __repr__(self): return f'<PlayableSlot Name: {self.name} Type:({self.type})>'

class DataSource(Base):
    __tablename__ = 'data_source'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), unique=True, nullable=False) 
    type = Column(String(50)) 
    items = relationship("Item", back_populates="source", cascade="all, delete-orphan")
    def __repr__(self): return f'<DataSource {self.name}>'

class Item(Base):
    __tablename__ = 'item'
    id = Column(Integer, primary_key=True) # Blizzard Item ID
    name = Column(String(255), nullable=False, index=True)
    quality = Column(String(20)) 
    icon_url = Column(String(512), nullable=True)
    slot_type = Column(String(50), ForeignKey('playable_slot.type'), nullable=False, index=True) # API inventory_type.type
    
    slot = relationship("PlayableSlot", back_populates="items")
    source_id = Column(Integer, ForeignKey('data_source.id'), nullable=True, index=True) # Can be null if item added ad-hoc
    source = relationship("DataSource", back_populates="items")
    source_details = Column(String(255), nullable=True) # Can be null
    
    bis_selections = relationship("CharacterBiS", back_populates="item", cascade="all, delete-orphan")
    def __repr__(self): return f'<Item {self.name} (ID: {self.id})>'

class Character(Base): 
    __tablename__ = 'character'
    id = Column(Integer, primary_key=True) 
    name = Column(String(100), nullable=False) 
    realm_slug = Column(String(100), nullable=False) 
    class_id = Column(Integer, ForeignKey('playable_class.id')) 
    is_active = Column(Boolean, default=True, nullable=False, index=True) # Added for completeness of schema

    # Other fields from app.py for schema completeness if create_all is used here
    level = Column(Integer, nullable=True)
    class_name = Column(String(50), nullable=True)
    spec_name = Column(String(50), nullable=True)
    main_spec_override = Column(String(50), nullable=True)
    role = Column(String(15), nullable=True)
    status = Column(String(15), nullable=True, index=True)
    item_level = Column(Integer, nullable=True, index=True)
    raid_progression = Column(String(200), nullable=True)
    rank = Column(Integer, nullable=True, index=True)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    raid_attendance_percentage = Column(Float, default=0.0, nullable=True)
    avg_wcl_performance = Column(Float, nullable=True)

    playable_class = relationship("PlayableClass", back_populates="characters")
    bis_selections = relationship("CharacterBiS", back_populates="character", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint('name', 'realm_slug', name='_name_realm_uc'),) 

class CharacterBiS(Base):
    __tablename__ = 'character_bis'
    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(Integer, ForeignKey('character.id'), nullable=False, index=True)
    slot_type_ui = Column(String(50), ForeignKey('playable_slot.type'), nullable=False, index=True) 
    item_id = Column(Integer, ForeignKey('item.id'), nullable=True) 
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    character = relationship("Character", back_populates="bis_selections")
    # Correctly reference PlayableSlot via slot_type_ui
    slot = relationship("PlayableSlot", foreign_keys=[slot_type_ui], back_populates="bis_selections") 
    item = relationship("Item", back_populates="bis_selections")
    
    __table_args__ = (UniqueConstraint('character_id', 'slot_type_ui', name='_character_slot_ui_uc'),)
    def __repr__(self): return f'<CharacterBiS CharID: {self.character_id} SlotUI: {self.slot_type_ui} ItemID: {self.item_id}>'

# --- Data Population Functions ---

def populate_playable_slots(db_session):
    print("Populating Playable Slots...", flush=True)
    slots_data = [
        {"type": "HEAD", "name": "Head (API)", "display_order": 1},
        {"type": "NECK", "name": "Neck (API)", "display_order": 2},
        {"type": "SHOULDER", "name": "Shoulder (API)", "display_order": 3},
        {"type": "BACK", "name": "Back (API - Generic)", "display_order": 4}, 
        {"type": "CLOAK", "name": "Cloak (API - Specific)", "display_order": 4}, 
        {"type": "CHEST", "name": "Chest (API - Generic)", "display_order": 5},
        {"type": "ROBE", "name": "Robe (API - Chest variant)", "display_order": 5},
        {"type": "SHIRT", "name": "Shirt (API)", "display_order": 6}, 
        {"type": "TABARD", "name": "Tabard (API)", "display_order": 7}, 
        {"type": "WRIST", "name": "Wrist (API)", "display_order": 8},
        {"type": "HAND", "name": "Hands (API - Generic)", "display_order": 9}, 
        {"type": "HANDS", "name": "Hands (API - Specific)", "display_order": 9}, 
        {"type": "WAIST", "name": "Waist (API)", "display_order": 10},
        {"type": "LEGS", "name": "Legs (API)", "display_order": 11},
        {"type": "FEET", "name": "Feet (API)", "display_order": 12},
        {"type": "FINGER", "name": "Finger (API - Generic)", "display_order": 13}, 
        {"type": "TRINKET", "name": "Trinket (API - Generic)", "display_order": 15},
        {"type": "WEAPON", "name": "Weapon (API - Generic)", "display_order": 17}, 
        {"type": "ONE_HAND", "name": "One-Hand Weapon (API)", "display_order": 17},
        {"type": "TWOHWEAPON", "name": "Two-Hand Weapon (API)", "display_order": 17}, 
        {"type": "MAIN_HAND", "name": "Main Hand (API - Equipment Slot)", "display_order": 17}, 
        {"type": "OFF_HAND", "name": "Off Hand (API - Equipment Slot)", "display_order": 18}, 
        {"type": "SHIELD", "name": "Shield (API)", "display_order": 18},
        {"type": "HOLDABLE", "name": "Holdable (API - Off-hand)", "display_order": 18},
        {"type": "RANGEDRIGHT", "name": "Ranged Weapon (API - RANGEDRIGHT)", "display_order": 17},
        {"type": "RANGED", "name": "Ranged (API - Generic Equipment Slot)", "display_order": 17},
        # UI Specific Slots (ensure these types are what your UI uses for CharacterBiS.slot_type_ui)
        {"type": "FINGER1", "name": "Finger 1 (UI)", "display_order": 13}, 
        {"type": "FINGER2", "name": "Finger 2 (UI)", "display_order": 14}, 
        {"type": "TRINKET1", "name": "Trinket 1 (UI)", "display_order": 15}, 
        {"type": "TRINKET2", "name": "Trinket 2 (UI)", "display_order": 16}, 
    ]
    for slot_data in slots_data:
        slot = db_session.query(PlayableSlot).filter_by(type=slot_data["type"]).first()
        if not slot:
            slot = PlayableSlot(type=slot_data["type"], name=slot_data["name"], display_order=slot_data["display_order"])
            db_session.add(slot)
        elif slot.name != slot_data["name"] or slot.display_order != slot_data["display_order"]: 
            slot.name = slot_data["name"]
            slot.display_order = slot_data["display_order"]
    try:
        db_session.commit()
        print("PlayableSlot table populated/verified.", flush=True)
    except Exception as e:
        db_session.rollback()
        print(f"Error populating PlayableSlot table: {e}", flush=True)

def populate_data_sources(db_session):
    print("Populating Data Sources...", flush=True)
    sources_data = [
        {"name": "Liberation of Undermine", "type": "Raid"},
        {"name": "Mythic+ Dungeons - TWW S1", "type": "Dungeon"}, 
        {"name": "Crafting - TWW S1", "type": "Crafting"},
        {"name": "Manually Added via BiS Check", "type": "System"} # For items added by the new function
    ]
    for source_data in sources_data:
        source = db_session.query(DataSource).filter_by(name=source_data["name"]).first()
        if not source:
            source = DataSource(name=source_data["name"], type=source_data["type"])
            db_session.add(source)
        elif source.type != source_data["type"]: 
            source.type = source_data["type"]
    try:
        db_session.commit()
        print("DataSource table populated/verified.", flush=True)
    except Exception as e:
        db_session.rollback()
        print(f"Error populating DataSource table: {e}", flush=True)
    return {source.name: source.id for source in db_session.query(DataSource).all()}

def update_playable_classes_and_specs(db_session):
    print("Updating PlayableClass and PlayableSpec tables from API...", flush=True)
    class_success = False
    spec_success = False
    access_token = get_blizzard_access_token()
    if not access_token:
        print("Error: Could not get Blizzard access token for class/spec update. Aborting.", flush=True)
        return False 
    headers = {"Authorization": f"Bearer {access_token}"}
    static_params = {"namespace": f"static-{REGION}", "locale": "en_US"}

    class_url = f"{BLIZZARD_API_BASE_URL}/data/wow/playable-class/index"
    class_index_data = make_blizzard_api_request_helper(api_url=class_url, params=static_params, headers=headers) 
    if class_index_data and 'classes' in class_index_data:
        for class_info_api in class_index_data['classes']:
            if class_info_api.get('id') and class_info_api.get('name'):
                existing_class = db_session.get(PlayableClass, class_info_api['id'])
                if existing_class:
                    if existing_class.name != class_info_api['name']: existing_class.name = class_info_api['name']
                else: db_session.add(PlayableClass(id=class_info_api['id'], name=class_info_api['name']))
        class_success = True
        print(f"PlayableClass table updated/prepared.", flush=True)
    else: print("Error: Failed to fetch or parse playable class index.", flush=True)

    spec_url = f"{BLIZZARD_API_BASE_URL}/data/wow/playable-specialization/index"
    spec_index_data = make_blizzard_api_request_helper(api_url=spec_url, params=static_params, headers=headers)
    if spec_index_data and 'character_specializations' in spec_index_data:
        fetch_errors = 0
        for spec_info in spec_index_data['character_specializations']:
            spec_id, spec_name_api = spec_info.get('id'), spec_info.get('name')
            detail_href = spec_info.get('key', {}).get('href') 
            if not all([spec_id, spec_name_api, detail_href]): continue
            spec_detail = make_blizzard_api_request_helper(api_url=detail_href, params=static_params, headers=headers) 
            if spec_detail:
                class_id = spec_detail.get('playable_class', {}).get('id')
                if class_id:
                    existing_spec = db_session.get(PlayableSpec, spec_id)
                    if existing_spec:
                        if existing_spec.name != spec_name_api or existing_spec.class_id != class_id:
                            existing_spec.name, existing_spec.class_id = spec_name_api, class_id
                    else: db_session.add(PlayableSpec(id=spec_id, name=spec_name_api, class_id=class_id))
                else: fetch_errors += 1
            else: fetch_errors += 1
            time.sleep(0.05)
        spec_success = True
        print(f"PlayableSpec table updated/prepared. Fetch errors: {fetch_errors}", flush=True)
    else: print("Error: Failed to fetch or parse playable specialization index.", flush=True)
    
    if class_success and spec_success:
        try: db_session.commit(); print("PlayableClass and PlayableSpec committed.", flush=True); return True
        except Exception as e: db_session.rollback(); print(f"Error committing class/spec: {e}", flush=True)
    else: db_session.rollback() 
    return False

def find_journal_instance_id(instance_name_to_find, instance_type="instance"):
    print(f"Attempting to find Journal ID for {instance_type}: '{instance_name_to_find}'", flush=True)
    access_token = get_blizzard_access_token()
    if not access_token:
        print(f"  ERROR: Could not get Blizzard access token for find_journal_instance_id.", flush=True)
        return None
    headers = {"Authorization": f"Bearer {access_token}"}
    static_params = {"namespace": f"static-{REGION}", "locale": "en_US"}
    
    index_data = None
    target_api_url_index = ""

    if instance_type == "instance": 
        target_api_url_index = f"{BLIZZARD_API_BASE_URL}/data/wow/journal-instance/index"
        index_data = make_blizzard_api_request_helper(api_url=target_api_url_index, params=static_params, headers=headers)
        if index_data and f"{instance_type}s" in index_data:
            for instance in index_data[f"{instance_type}s"]:
                if instance.get("name", "").lower() == instance_name_to_find.lower():
                    instance_id = instance.get("id")
                    print(f"  SUCCESS: Found {instance_type} '{instance_name_to_find}' with ID: {instance_id} (via direct /journal-instance/index)", flush=True)
                    return instance_id
            print(f"  INFO: Raid '{instance_name_to_find}' not found in direct /journal-instance/index. Listing available from that index:", flush=True)
            available_instance_names = [inst.get("name", "Unknown API Name") for inst in index_data[f"{instance_type}s"]]
            for name in sorted(available_instance_names): print(f"    - \"{name}\"", flush=True)
            return None 
        else:
            print(f"  ERROR: Could not fetch or parse journal {instance_type} (raid) index. URL: {target_api_url_index}", flush=True)
            if index_data is not None: print(f"  DEBUG: Raid Index Response: {json.dumps(index_data, indent=2)}", flush=True)
            return None

    elif instance_type == "dungeon": 
        target_api_url_dungeon_index = f"{BLIZZARD_API_BASE_URL}/data/wow/journal-dungeon/index"
        dungeon_index_data = make_blizzard_api_request_helper(api_url=target_api_url_dungeon_index, params=static_params, headers=headers)

        if dungeon_index_data and f"{instance_type}s" in dungeon_index_data:
            print(f"  INFO: Successfully fetched direct /journal-dungeon/index.", flush=True)
            for instance in dungeon_index_data[f"{instance_type}s"]:
                if instance.get("name", "").lower() == instance_name_to_find.lower():
                    instance_id = instance.get("id")
                    print(f"  SUCCESS: Found dungeon '{instance_name_to_find}' with ID: {instance_id} (via direct /journal-dungeon/index)", flush=True)
                    return instance_id
            print(f"  INFO: Dungeon '{instance_name_to_find}' not found in direct /journal-dungeon/index. Will proceed to expansion trawl.", flush=True)
        else:
            print(f"  INFO: Direct /journal-dungeon/index failed or was empty (URL: {target_api_url_dungeon_index}). Attempting fallback via expansions...", flush=True)
            if dungeon_index_data is not None: print(f"  DEBUG: Direct Dungeon Index Response: {json.dumps(dungeon_index_data, indent=2)}", flush=True)
        
        print(f"  Attempting dungeon search via expansions for '{instance_name_to_find}'...", flush=True)
        exp_index_url = f"{BLIZZARD_API_BASE_URL}/data/wow/journal-expansion/index"
        exp_index_data = make_blizzard_api_request_helper(api_url=exp_index_url, params=static_params, headers=headers)
        
        if not exp_index_data or ("tiers" not in exp_index_data and "expansions" not in exp_index_data) : 
                 print(f"  ERROR: Could not fetch or parse journal expansion index, or it's empty. URL: {exp_index_url}", flush=True)
                 if exp_index_data is not None: print(f"  DEBUG: Expansion Index Response: {json.dumps(exp_index_data, indent=2)}", flush=True)
                 return None

        all_dungeon_names_from_expansions = set()
        expansion_list_key = "tiers" if "tiers" in exp_index_data else "expansions" 
        
        for expansion_summary in exp_index_data.get(expansion_list_key, []):
            exp_id = expansion_summary.get("id")
            exp_name = expansion_summary.get("name", f"Expansion ID {exp_id}")
            if not exp_id: continue
            
            exp_detail_url = f"{BLIZZARD_API_BASE_URL}/data/wow/journal-expansion/{exp_id}"
            exp_detail_data = make_blizzard_api_request_helper(api_url=exp_detail_url, params=static_params, headers=headers)
            time.sleep(0.05) 

            if exp_detail_data and "dungeons" in exp_detail_data:
                for dungeon in exp_detail_data["dungeons"]:
                    dungeon_name_from_api = dungeon.get("name", "Unknown API Name")
                    all_dungeon_names_from_expansions.add(dungeon_name_from_api)
                    if dungeon_name_from_api.lower() == instance_name_to_find.lower():
                        dungeon_id = dungeon.get("id")
                        print(f"  SUCCESS: Found dungeon '{instance_name_to_find}' with ID: {dungeon_id} (via expansion: {exp_name})", flush=True)
                        return dungeon_id
        
        print(f"  ERROR: Dungeon '{instance_name_to_find}' not found even after checking all expansions.", flush=True)
        if all_dungeon_names_from_expansions:
            print(f"  Available dungeon names collected from all expansions:", flush=True)
            for name in sorted(list(all_dungeon_names_from_expansions)):
                print(f"    - \"{name}\"", flush=True)
        else:
            print("  INFO: No dungeons found in any expansion data.", flush=True)
        return None 
    return None

def fetch_and_store_source_items(db_session, source_name_friendly, source_journal_id, data_source_id, source_type="raid", target_encounter_names=None):
    print(f"Fetching items for {source_type}: {source_name_friendly} (Journal ID: {source_journal_id})", flush=True)
    if target_encounter_names:
        print(f"  Targeting specific encounters: {', '.join(target_encounter_names)}", flush=True)

    access_token = get_blizzard_access_token()
    if not access_token: return
    headers = {"Authorization": f"Bearer {access_token}"}
    static_params = {"namespace": f"static-{REGION}", "locale": "en_US"}

    instance_api_endpoint_suffix = f"/data/wow/journal-instance/{source_journal_id}"
    if source_type == "dungeon": 
        print(f"  NOTE: Using /journal-instance/{source_journal_id} endpoint for dungeon details.", flush=True)
    
    instance_api_url = f"{BLIZZARD_API_BASE_URL}{instance_api_endpoint_suffix}"
    instance_data = make_blizzard_api_request_helper(api_url=instance_api_url, params=static_params, headers=headers)

    if not instance_data or "encounters" not in instance_data: 
        print(f"Error: No instance data/encounters for {source_type} ID {source_journal_id}. URL: {instance_api_url}", flush=True); return

    items_processed_this_source = 0 
    for encounter_ref in instance_data["encounters"]:
        enc_id, enc_name = encounter_ref.get("id"), encounter_ref.get("name")
        if not enc_id or not enc_name: continue

        if target_encounter_names and enc_name not in target_encounter_names:
            continue
        
        print(f"  Loot for encounter: {enc_name} (ID: {enc_id})", flush=True)
        
        items_to_parse = encounter_ref.get("items")
        if not items_to_parse: # If not, fetch the detailed encounter data
            enc_detail_url = f"{BLIZZARD_API_BASE_URL}/data/wow/journal-encounter/{enc_id}"
            enc_detail_data = make_blizzard_api_request_helper(api_url=enc_detail_url, params=static_params, headers=headers)
            if not enc_detail_data or "items" not in enc_detail_data:
                print(f"    WARNING: No 'items' in encounter_ref OR in detailed fetch for {enc_name} (ID: {enc_id}). Detail response: {json.dumps(enc_detail_data) if enc_detail_data else 'None'}", flush=True)
                continue
            items_to_parse = enc_detail_data["items"]
        
        if not items_to_parse: 
            print(f"    INFO: No items found to process for encounter {enc_name} after all checks.", flush=True)
            continue
        
        print(f"    Found {len(items_to_parse)} potential item entries for {enc_name}.", flush=True)
        
        # Set to track item IDs already processed or added in this encounter's batch
        item_ids_already_handled_in_this_encounter = set()
        items_to_commit_for_this_encounter = []


        for item_entry_index, item_entry in enumerate(items_to_parse):
            item_ref = item_entry.get("item")
            if not item_ref or "id" not in item_ref: 
                # print(f"      DEBUG: Item Entry {item_entry_index} for {enc_name}: Skipping, no item_ref or id. Entry: {item_entry}", flush=True)
                continue
            item_id = item_ref["id"]
            
            if item_id in item_ids_already_handled_in_this_encounter:
                # print(f"      DEBUG: Item ID {item_id} already handled in this encounter's batch. Skipping.", flush=True)
                continue
            
            # print(f"      DEBUG: Item Entry {item_entry_index} for {enc_name}: Processing item_id: {item_id}", flush=True)
            existing_item = db_session.get(Item, item_id) # Check if item exists in DB
            
            if existing_item and existing_item.icon_url: 
                # print(f"      DEBUG: Item {item_id} ('{existing_item.name}') exists with icon, skipping further API calls for it.", flush=True)
                item_ids_already_handled_in_this_encounter.add(item_id) # Mark as handled
                continue 

            # If item doesn't exist, or exists but lacks an icon, fetch details from API
            item_detail_url = f"{BLIZZARD_API_BASE_URL}/data/wow/item/{item_id}"
            item_data = make_blizzard_api_request_helper(api_url=item_detail_url, params=static_params, headers=headers)
            time.sleep(0.05) # API call delay

            if not item_data: 
                print(f"    WARNING: Failed to fetch details for item ID {item_id}. URL: {item_detail_url}", flush=True)
                item_ids_already_handled_in_this_encounter.add(item_id) # Mark as handled to avoid re-fetch attempt
                continue

            item_name = item_data.get("name")
            item_quality_data = item_data.get("quality", {})
            item_quality = item_quality_data.get("name", "Unknown").upper() if isinstance(item_quality_data, dict) else "Unknown"
            api_slot_type = item_data.get("inventory_type", {}).get("type") 
            
            # print(f"      FETCHED Item {item_id} - Name: '{item_name}', Quality: '{item_quality}', API Slot: '{api_slot_type}'", flush=True)

            fetched_icon_url = None 
            media_href = item_data.get("media", {}).get("key", {}).get("href")
            if media_href:
                media_data = make_blizzard_api_request_helper(api_url=media_href, params=static_params, headers=headers)
                time.sleep(0.05) # API call delay
                if media_data and "assets" in media_data:
                    for asset in media_data["assets"]:
                        if asset.get("key") == "icon": fetched_icon_url = asset.get("value"); break
            
            item_ids_already_handled_in_this_encounter.add(item_id) # Mark as handled

            if item_name and item_quality in ["EPIC", "RARE"] and api_slot_type and api_slot_type != "NON_EQUIP": 
                # print(f"      QUALIFIED Item {item_id} ('{item_name}') for DB (Quality: {item_quality}, Slot: {api_slot_type}).", flush=True)
                if not db_session.query(PlayableSlot).filter_by(type=api_slot_type).first():
                    print(f"CRITICAL: API slot '{api_slot_type}' for item '{item_name}' (ID:{item_id}) missing in PlayableSlot. Add to populate_playable_slots().", flush=True)
                    continue

                if existing_item: # Item exists in DB, needs icon update
                    if fetched_icon_url and not existing_item.icon_url : 
                        existing_item.icon_url = fetched_icon_url
                        # existing_item is already part of the session, changes will be picked up.
                        # No need to add to items_to_commit_for_this_encounter unless it wasn't dirty.
                        # For safety, ensure it's managed:
                        db_session.add(existing_item) 
                        print(f"    Updating icon for existing item ID {item_id}: {item_name}", flush=True)
                        items_processed_this_source +=1 
                else: # Item does not exist in DB, create new
                    # print(f"    Adding new item ID {item_id}: {item_name} (Slot: {api_slot_type}, Icon: {'Yes' if fetched_icon_url else 'No'})", flush=True)
                    new_item_instance = Item(id=item_id, name=item_name, quality=item_quality, slot_type=api_slot_type,
                                     source_id=data_source_id, source_details=f"{source_name_friendly} - {enc_name}",
                                     icon_url=fetched_icon_url)
                    items_to_commit_for_this_encounter.append(new_item_instance)
                    items_processed_this_source += 1
            # else:
                # print(f"      SKIPPED Item {item_id} ('{item_name}'). Criteria Check -> Name: {bool(item_name)}, Quality EPIC/RARE: {item_quality in ['EPIC', 'RARE']}, Slot Valid: {bool(api_slot_type and api_slot_type != 'NON_EQUIP')}", flush=True)
        
        # After processing all items for the current encounter
        if items_to_commit_for_this_encounter:
            db_session.add_all(items_to_commit_for_this_encounter)
            # print(f"    Queued {len(items_to_commit_for_this_encounter)} new items for encounter '{enc_name}' for commit.", flush=True)

        try: 
            db_session.commit() # Commit changes for this encounter (new items and updates to existing)
            # print(f"    Committed changes for encounter '{enc_name}'.", flush=True)
        except IntegrityError as ie: # Catch unique constraint violations specifically
            db_session.rollback()
            print(f"    DB Integrity Error for encounter {enc_name}: {ie}. This might happen if an item was already added by another process or if API data has unexpected duplicates not caught by the per-encounter set.", flush=True)
        except Exception as e: 
            db_session.rollback()
            print(f"    Error committing items for encounter {enc_name}: {e}", flush=True)
        
        # time.sleep(0.1) # Small delay between encounters if needed

    print(f"Finished {source_name_friendly}. Total items processed (added or icon updated): {items_processed_this_source}", flush=True)


def fetch_and_store_crafted_items(db_session, data_source_id):
    print("\n--- Processing Crafted Items (Scoped to 'Khaz Algar') ---", flush=True)
    access_token = get_blizzard_access_token()
    if not access_token:
        print("  ERROR: Could not get Blizzard access token for crafted items. Aborting.", flush=True)
        return
    headers = {"Authorization": f"Bearer {access_token}"}
    static_params = {"namespace": f"static-{REGION}", "locale": "en_US"}

    target_professions = {
        "Blacksmithing": 164, "Leatherworking": 165, "Tailoring": 197,
        "Jewelcrafting": 755, "Engineering": 202 
    }
    TARGET_ITEM_QUALITIES = ["EPIC", "RARE"]
    EQUIPPABLE_GEAR_SLOT_CATEGORIES = [
        "HEAD", "NECK", "SHOULDER", "BACK", "CLOAK", "CHEST", "ROBE", "WRIST",
        "HANDS", "HAND", "WAIST", "LEGS", "FEET", "FINGER", "TRINKET",
        "WEAPON", "ONE_HAND", "TWOHWEAPON", "MAIN_HAND", "OFF_HAND", "SHIELD", "HOLDABLE",
        "RANGEDRIGHT", "RANGED"
    ]
    CURRENT_EXPANSION_KEYWORD = "Khaz Algar" 

    total_crafted_items_added_session = 0

    prof_index_url = f"{BLIZZARD_API_BASE_URL}/data/wow/profession/index"
    prof_index_data = make_blizzard_api_request_helper(api_url=prof_index_url, params=static_params, headers=headers)

    if not prof_index_data or "professions" not in prof_index_data:
        print("  ERROR: Could not fetch profession index.", flush=True)
        return

    for prof_summary in prof_index_data["professions"]:
        prof_name = prof_summary.get("name")
        prof_id = prof_summary.get("id")

        if prof_name in target_professions and target_professions[prof_name] == prof_id:
            print(f"  Processing Profession: {prof_name} (ID: {prof_id})", flush=True)
            
            prof_detail_url = f"{BLIZZARD_API_BASE_URL}/data/wow/profession/{prof_id}"
            prof_detail_data = make_blizzard_api_request_helper(api_url=prof_detail_url, params=static_params, headers=headers)
            time.sleep(0.05)

            if not prof_detail_data or "skill_tiers" not in prof_detail_data:
                print(f"    ERROR: Could not fetch details or skill tiers for {prof_name}.", flush=True)
                continue
            
            # Set to track item IDs already handled within this profession's processing run
            item_ids_handled_this_profession = set()

            for skill_tier_summary in prof_detail_data["skill_tiers"]:
                skill_tier_name = skill_tier_summary.get("name", "")
                # print(f"    DEBUG: Checking Skill Tier: '{skill_tier_name}' for keyword '{CURRENT_EXPANSION_KEYWORD}'", flush=True)
                if CURRENT_EXPANSION_KEYWORD.lower() not in skill_tier_name.lower():
                    # print(f"    Skipping skill tier '{skill_tier_name}' (does not contain '{CURRENT_EXPANSION_KEYWORD}').", flush=True)
                    continue
                
                print(f"    Processing Skill Tier: {skill_tier_name}", flush=True)
                skill_tier_id = skill_tier_summary.get("id")
                if not skill_tier_id: continue

                skill_tier_detail_url = f"{BLIZZARD_API_BASE_URL}/data/wow/profession/{prof_id}/skill-tier/{skill_tier_id}"
                skill_tier_data = make_blizzard_api_request_helper(api_url=skill_tier_detail_url, params=static_params, headers=headers)
                time.sleep(0.05)

                if not skill_tier_data or "categories" not in skill_tier_data:
                    # print(f"      No categories found for skill tier {skill_tier_name}", flush=True)
                    continue
                
                items_to_commit_for_this_profession_tier = []

                for category in skill_tier_data["categories"]:
                    if "recipes" not in category: continue
                    for recipe_ref in category["recipes"]:
                        recipe_id = recipe_ref.get("id")
                        # recipe_name_for_debug = recipe_ref.get("name", f"Recipe ID {recipe_id}") 
                        if not recipe_id: continue

                        # print(f"        DEBUG: Processing Recipe: {recipe_name_for_debug} (ID: {recipe_id})", flush=True)
                        recipe_detail_url = f"{BLIZZARD_API_BASE_URL}/data/wow/recipe/{recipe_id}"
                        recipe_data = make_blizzard_api_request_helper(api_url=recipe_detail_url, params=static_params, headers=headers)
                        time.sleep(0.05)

                        if not recipe_data: 
                            # print(f"          WARNING: Failed to fetch recipe details for ID {recipe_id} ({recipe_name_for_debug})", flush=True)
                            continue
                        
                        crafted_item_ref = recipe_data.get("crafted_item") or \
                                           recipe_data.get("alliance_crafted_item") or \
                                           recipe_data.get("horde_crafted_item")
                        
                        if not crafted_item_ref or "id" not in crafted_item_ref: 
                            # print(f"          DEBUG: Recipe {recipe_id} ({recipe_name_for_debug}) does not produce a direct item or item ID missing.", flush=True)
                            continue
                        
                        item_id = crafted_item_ref["id"]
                        # print(f"          DEBUG: Recipe {recipe_id} crafts item ID: {item_id}", flush=True)
                        
                        if item_id in item_ids_handled_this_profession:
                            # print(f"          DEBUG: Crafted item ID {item_id} already handled in this profession's run. Skipping.", flush=True)
                            continue
                        
                        existing_item = db_session.get(Item, item_id)
                        if existing_item and existing_item.icon_url: 
                            # print(f"          DEBUG: Crafted item ID {item_id} already exists in DB with icon. Skipping API fetch.", flush=True)
                            item_ids_handled_this_profession.add(item_id)
                            continue

                        item_detail_url = f"{BLIZZARD_API_BASE_URL}/data/wow/item/{item_id}"
                        item_data = make_blizzard_api_request_helper(api_url=item_detail_url, params=static_params, headers=headers)
                        if not item_data: 
                            # print(f"            WARNING: Failed to fetch item details for crafted item ID {item_id}.", flush=True)
                            item_ids_handled_this_profession.add(item_id) # Mark as handled to avoid re-fetch
                            continue

                        item_name = item_data.get("name")
                        item_quality = item_data.get("quality", {}).get("name", "Unknown").upper()
                        api_slot_type = item_data.get("inventory_type", {}).get("type")
                        
                        # print(f"            FETCHED Crafted Item {item_id} - Name: '{item_name}', Quality: '{item_quality}', API Slot: '{api_slot_type}'", flush=True)
                        item_ids_handled_this_profession.add(item_id) # Mark as handled

                        if item_name and item_quality in TARGET_ITEM_QUALITIES and \
                           api_slot_type and api_slot_type in EQUIPPABLE_GEAR_SLOT_CATEGORIES:
                            
                            # print(f"            QUALIFIED Crafted Item {item_id} ('{item_name}') for DB.", flush=True)
                            fetched_icon_url = None
                            media_href = item_data.get("media", {}).get("key", {}).get("href")
                            if media_href:
                                media_data = make_blizzard_api_request_helper(api_url=media_href, params=static_params, headers=headers)
                                time.sleep(0.05)
                                if media_data and "assets" in media_data:
                                    for asset in media_data["assets"]:
                                        if asset.get("key") == "icon": fetched_icon_url = asset.get("value"); break
                            
                            if not db_session.query(PlayableSlot).filter_by(type=api_slot_type).first():
                                print(f"CRITICAL: API slot '{api_slot_type}' for crafted item '{item_name}' (ID:{item_id}) missing in PlayableSlot.", flush=True)
                                continue

                            if existing_item: # Exists in DB, but icon was missing
                                if fetched_icon_url and not existing_item.icon_url:
                                    existing_item.icon_url = fetched_icon_url
                                    if existing_item.source_id != data_source_id or existing_item.source_details != prof_name: # Update source if different
                                        existing_item.source_id = data_source_id
                                        existing_item.source_details = prof_name
                                    db_session.add(existing_item) # Ensure it's part of UoW
                                    # print(f"        Updating icon/source for existing crafted item ID {item_id}: {item_name}", flush=True)
                                    total_crafted_items_added_session +=1 # Count as processed
                            else: # New item
                                # print(f"        Adding new crafted item ID {item_id}: {item_name} (Slot: {api_slot_type}, Icon: {'Yes' if fetched_icon_url else 'No'})", flush=True)
                                new_crafted_item = Item(id=item_id, name=item_name, quality=item_quality, slot_type=api_slot_type,
                                                 source_id=data_source_id, source_details=prof_name,
                                                 icon_url=fetched_icon_url)
                                items_to_commit_for_this_profession_tier.append(new_crafted_item)
                                total_crafted_items_added_session += 1
                        # else:
                            # print(f"            SKIPPED Crafted Item {item_id} ('{item_name}'). Criteria Check -> Name: {bool(item_name)}, Quality: {item_quality in TARGET_ITEM_QUALITIES}, Slot Valid: {bool(api_slot_type and api_slot_type in EQUIPPABLE_GEAR_SLOT_CATEGORIES)}", flush=True)
            
                if items_to_commit_for_this_profession_tier: 
                    db_session.add_all(items_to_commit_for_this_profession_tier)
                    # print(f"    Queued {len(items_to_commit_for_this_profession_tier)} items from skill tier '{skill_tier_name}' for commit.", flush=True)
            
            # Commit after each profession to manage transaction size
            try: 
                db_session.commit()
                print(f"  Committed items for profession {prof_name} (Khaz Algar tiers).", flush=True)
            except IntegrityError as ie:
                db_session.rollback()
                print(f"    DB Integrity Error for profession {prof_name}: {ie}.", flush=True)
            except Exception as e:
                db_session.rollback()
                print(f"    Error committing crafted items for {prof_name}: {e}", flush=True)
            
    print(f"--- Finished processing Crafted Items. Total items processed (added or icon updated) in this session: {total_crafted_items_added_session} ---", flush=True)

# --- NEW FUNCTIONS FOR BiS ITEM CHECK ---
def fetch_and_store_single_item_from_api(db_session, item_id_to_fetch, existing_playable_slot_types_set, system_data_source_id):
    print(f"  Attempting to fetch details for missing BiS item ID: {item_id_to_fetch}", flush=True)
    access_token = get_blizzard_access_token()
    if not access_token:
        print(f"    ERROR: Could not get Blizzard access token for item ID {item_id_to_fetch}.", flush=True)
        return None
    
    headers = {"Authorization": f"Bearer {access_token}"}
    static_params = {"namespace": f"static-{REGION}", "locale": "en_US"}
    
    item_api_url = f"{BLIZZARD_API_BASE_URL}/data/wow/item/{item_id_to_fetch}"
    item_data = make_blizzard_api_request_helper(api_url=item_api_url, params=static_params, headers=headers)
    time.sleep(0.05) 

    if not item_data or "id" not in item_data:
        print(f"    ERROR: Failed to fetch or parse item data for ID {item_id_to_fetch}. Response: {item_data}", flush=True)
        return None

    item_name = item_data.get("name")
    quality_data = item_data.get("quality", {})
    item_quality = quality_data.get("name", "Unknown").upper() if isinstance(quality_data, dict) else "Unknown"
    
    inventory_type_data = item_data.get("inventory_type", {})
    api_slot_type = inventory_type_data.get("type") if isinstance(inventory_type_data, dict) else None

    if not item_name or not api_slot_type:
        print(f"    WARNING: Item ID {item_id_to_fetch} missing critical data (name or slot_type). Name: '{item_name}', Slot: '{api_slot_type}'. Skipping.", flush=True)
        return None

    if api_slot_type not in existing_playable_slot_types_set:
        print(f"    CRITICAL WARNING: API slot type '{api_slot_type}' for item ID {item_id_to_fetch} ('{item_name}') is not defined in PlayableSlot table. Item cannot be added. Please update populate_playable_slots().", flush=True)
        return None 

    icon_url = None
    media_href = item_data.get("media", {}).get("key", {}).get("href")
    if media_href:
        media_data = make_blizzard_api_request_helper(api_url=media_href, params=static_params, headers=headers)
        time.sleep(0.05) 
        if media_data and "assets" in media_data:
            for asset in media_data["assets"]:
                if asset.get("key") == "icon":
                    icon_url = asset.get("value")
                    break
    
    print(f"    SUCCESS: Fetched details for item ID {item_id_to_fetch}: '{item_name}', Quality: {item_quality}, Slot: {api_slot_type}, Icon: {'Yes' if icon_url else 'No'}", flush=True)
    
    new_item = Item(
        id=item_id_to_fetch,
        name=item_name,
        quality=item_quality,
        icon_url=icon_url,
        slot_type=api_slot_type,
        source_id=system_data_source_id, 
        source_details="Added via CharacterBiS check" 
    )
    return new_item

def ensure_character_bis_items_in_db(db_session):
    print("\n--- Ensuring CharacterBiS items are in Item Table ---", flush=True)
    
    system_data_source = db_session.query(DataSource).filter_by(name="Manually Added via BiS Check").first()
    if not system_data_source:
        print("    ERROR: 'Manually Added via BiS Check' data source not found. Cannot proceed with BiS item check.", flush=True)
        return
    system_data_source_id = system_data_source.id

    try:
        bis_item_ids_query = db_session.query(CharacterBiS.item_id).filter(CharacterBiS.item_id != None).distinct()
        bis_item_ids = {row.item_id for row in bis_item_ids_query.all()}
        
        if not bis_item_ids:
            print("    No items found in CharacterBiS table to check.", flush=True)
            return

        print(f"    Found {len(bis_item_ids)} distinct item IDs in CharacterBiS table.", flush=True)

        existing_item_ids_query = db_session.query(Item.id).all()
        existing_item_ids_set = {row.id for row in existing_item_ids_query}
        print(f"    Found {len(existing_item_ids_set)} item IDs in the main Item table.", flush=True)

        playable_slot_types_query = db_session.query(PlayableSlot.type).all()
        valid_slot_types_set = {row.type for row in playable_slot_types_query}
        if not valid_slot_types_set:
            print("    CRITICAL ERROR: PlayableSlot table is empty. Cannot validate item slot types.", flush=True)
            return

        items_to_add_to_db = []
        for bis_item_id in bis_item_ids:
            if bis_item_id not in existing_item_ids_set:
                print(f"  Item ID {bis_item_id} from CharacterBiS is missing from Item table. Attempting to fetch...", flush=True)
                new_item_obj = fetch_and_store_single_item_from_api(db_session, bis_item_id, valid_slot_types_set, system_data_source_id)
                if new_item_obj:
                    # Double check if it was somehow added by another concurrent process or previous step within this session run
                    if not db_session.get(Item, new_item_obj.id):
                        items_to_add_to_db.append(new_item_obj)
                    else:
                        print(f"    INFO: Item ID {new_item_obj.id} was already added to session or DB, likely by another part of the script. Skipping duplicate add.", flush=True)


        if items_to_add_to_db:
            print(f"    Adding {len(items_to_add_to_db)} missing BiS items to the Item table...", flush=True)
            db_session.add_all(items_to_add_to_db)
            db_session.commit()
            print(f"    Successfully added {len(items_to_add_to_db)} items.", flush=True)
        else:
            print("    All items from CharacterBiS are already present in the Item table or could not be fetched/validated/already queued.", flush=True)

    except Exception as e:
        db_session.rollback()
        print(f"    ERROR during CharacterBiS item check: {e}", flush=True)
        import traceback
        traceback.print_exc()
    
    print("--- Finished CharacterBiS item check ---", flush=True)


# --- MAIN EXECUTION ---
def main():
    print("Starting WoW Info Population Script...", flush=True)
    db_session = SessionLocal()
    print("Ensuring DB tables exist...", flush=True)
    all_tables_to_ensure = [
        PlayableClass.__table__, PlayableSpec.__table__, PlayableSlot.__table__, 
        DataSource.__table__, Item.__table__, Character.__table__, CharacterBiS.__table__
    ]
    Base.metadata.create_all(engine, tables=all_tables_to_ensure, checkfirst=True)
    print("DB tables verified/created.", flush=True)
    
    print("Clearing Item and DataSource tables. PlayableSlot will be additively updated/verified.", flush=True)
    try:        
        db_session.query(Item).delete(synchronize_session=False)
        db_session.query(DataSource).delete(synchronize_session=False) 
        db_session.commit()
        print("Item and DataSource tables cleared.", flush=True)
    except Exception as e:
        db_session.rollback()
        print(f"Error clearing tables: {e}", flush=True)

    populate_playable_slots(db_session) 
    data_sources = populate_data_sources(db_session) 
    
    if not update_playable_classes_and_specs(db_session): 
        print("Error updating classes/specs. Some item processing might be affected.", flush=True)

    undermine_id = find_journal_instance_id("Liberation of Undermine", "instance")
    if undermine_id and "Liberation of Undermine" in data_sources:
        fetch_and_store_source_items(db_session, "Liberation of Undermine", undermine_id, data_sources["Liberation of Undermine"], "raid")
    else:
        print("Could not process Liberation of Undermine raid items (ID or DataSource missing).", flush=True)
    
    print("\n--- Processing Mythic+ Dungeons ---", flush=True)
    mplus_dungeon_names = [ 
        "Operation: Mechagon", "THE MOTHERLODE!!", "Theater of Pain",  
        "Cinderbrew Meadery", "Priory of the Sacred Flame", "The Rookery", 
        "Darkflame Cleft", "Operation: Floodgate"
    ]
    mplus_source_name = "Mythic+ Dungeons - TWW S1" 
    
    if mplus_source_name in data_sources:
        mplus_source_id_val = data_sources[mplus_source_name]
        mechagon_target_encounters = ["Tussle Tonks", "K.U.-J.0.", "Machinist's Garden", "King Mechagon"]
        
        for d_name in mplus_dungeon_names:
            d_id = find_journal_instance_id(d_name, "dungeon")
            if d_id: 
                if d_name == "Operation: Mechagon":
                    fetch_and_store_source_items(db_session, d_name, d_id, mplus_source_id_val, "dungeon", target_encounter_names=mechagon_target_encounters)
                else:
                    fetch_and_store_source_items(db_session, d_name, d_id, mplus_source_id_val, "dungeon")
            else:
                print(f"Could not find journal ID for M+ dungeon '{d_name}'. Skipping.", flush=True)
            time.sleep(0.5) 
    else: 
        print(f"Data source '{mplus_source_name}' not found. Cannot process M+ dungeon items.", flush=True)

    if "Crafting - TWW S1" in data_sources:
        fetch_and_store_crafted_items(db_session, data_sources["Crafting - TWW S1"])
    else:
        print(f"Data source 'Crafting - TWW S1' not found. Cannot process crafted items.", flush=True)

    ensure_character_bis_items_in_db(db_session)

    db_session.close()
    print("WoW Info Population Script Finished.", flush=True)

if __name__ == "__main__":
    if not BLIZZARD_CLIENT_ID or not BLIZZARD_CLIENT_SECRET:
        print("FATAL: BLIZZARD_CLIENT_ID or BLIZZARD_CLIENT_SECRET missing.", flush=True)
        exit(1)
    main()
