# wow_info.py
import os
import requests # Will be used by imported helpers
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
    from helper_functions import get_blizzard_access_token, make_api_request
except ImportError:
    print("Error: helper_functions.py not found. Make sure it's in the same directory or Python path.", flush=True)
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
    bis_selections = relationship("CharacterBiS", back_populates="slot", cascade="all, delete-orphan")
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
    id = Column(Integer, primary_key=True) 
    name = Column(String(255), nullable=False, index=True)
    quality = Column(String(20)) 
    icon_url = Column(String(512), nullable=True)
    slot_type = Column(String(50), ForeignKey('playable_slot.type'), nullable=False, index=True) 
    slot = relationship("PlayableSlot", back_populates="items")
    source_id = Column(Integer, ForeignKey('data_source.id'), nullable=True, index=True)
    source = relationship("DataSource", back_populates="items")
    source_details = Column(String(255)) 
    bis_selections = relationship("CharacterBiS", back_populates="item", cascade="all, delete-orphan")
    def __repr__(self): return f'<Item {self.name} (ID: {self.id})>'

class Character(Base): 
    __tablename__ = 'character'
    id = Column(Integer, primary_key=True) 
    name = Column(String(100), nullable=False) 
    realm_slug = Column(String(100), nullable=False) 
    class_id = Column(Integer, ForeignKey('playable_class.id')) 
    is_active = Column(Boolean, default=True, nullable=False, index=True) 

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
    slot = relationship("PlayableSlot", foreign_keys=[slot_type_ui]) 
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
    class_index_data = make_api_request(api_url=class_url, params=static_params, headers=headers) 
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
    spec_index_data = make_api_request(api_url=spec_url, params=static_params, headers=headers)
    if spec_index_data and 'character_specializations' in spec_index_data:
        fetch_errors = 0
        for spec_info in spec_index_data['character_specializations']:
            spec_id, spec_name_api = spec_info.get('id'), spec_info.get('name')
            detail_href = spec_info.get('key', {}).get('href') 
            if not all([spec_id, spec_name_api, detail_href]): continue
            spec_detail = make_api_request(api_url=detail_href, params=static_params, headers=headers) 
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
    print(f"Finding Journal ID for {instance_type}: '{instance_name_to_find}'", flush=True)
    access_token = get_blizzard_access_token()
    if not access_token: return None
    headers = {"Authorization": f"Bearer {access_token}"}
    static_params = {"namespace": f"static-{REGION}", "locale": "en_US"}
    api_url = f"{BLIZZARD_API_BASE_URL}/data/wow/journal-{instance_type}/index"
    index_data = make_api_request(api_url=api_url, params=static_params, headers=headers)
    if index_data and f"{instance_type}s" in index_data:
        for instance in index_data[f"{instance_type}s"]:
            if instance.get("name", "").lower() == instance_name_to_find.lower():
                print(f"Found ID: {instance.get('id')}", flush=True); return instance.get("id")
        print(f"Error: '{instance_name_to_find}' not found.", flush=True)
    else: print(f"Error: Could not fetch journal {instance_type} index.", flush=True)
    return None

def fetch_and_store_source_items(db_session, source_name_friendly, source_journal_id, data_source_id, source_type="raid"):
    print(f"Fetching items for {source_type}: {source_name_friendly} (Journal ID: {source_journal_id})", flush=True)
    access_token = get_blizzard_access_token()
    if not access_token: return
    headers = {"Authorization": f"Bearer {access_token}"}
    static_params = {"namespace": f"static-{REGION}", "locale": "en_US"}

    endpoint_suffix = f"/data/wow/journal-{source_type}/{source_journal_id}"
    instance_api_url = f"{BLIZZARD_API_BASE_URL}{endpoint_suffix}"
    instance_data = make_api_request(api_url=instance_api_url, params=static_params, headers=headers)

    if not instance_data or "encounters" not in instance_data: 
        print(f"Error: No instance data/encounters for {source_type} ID {source_journal_id}.", flush=True); return

    items_processed_count = 0
    for encounter_ref in instance_data["encounters"]:
        enc_id, enc_name = encounter_ref.get("id"), encounter_ref.get("name")
        if not enc_id or not enc_name: continue
        print(f"  Loot for encounter: {enc_name}", flush=True)
        
        items_to_parse = encounter_ref.get("items")
        if not items_to_parse:
            enc_detail_url = f"{BLIZZARD_API_BASE_URL}/data/wow/journal-encounter/{enc_id}"
            enc_detail_data = make_api_request(api_url=enc_detail_url, params=static_params, headers=headers)
            if not enc_detail_data or "items" not in enc_detail_data: continue
            items_to_parse = enc_detail_data["items"]
        if not items_to_parse: continue

        for item_entry in items_to_parse:
            item_ref = item_entry.get("item")
            if not item_ref or "id" not in item_ref: continue
            item_id = item_ref["id"]
            
            existing_item = db_session.get(Item, item_id)
            # If item exists and already has an icon, we might skip fetching its details again
            # unless we want to refresh other info like source_details (not currently done here).
            if existing_item and existing_item.icon_url: 
                # print(f"    DEBUG: Item ID {item_id} ('{existing_item.name}') already in DB with icon. Skipping full fetch.", flush=True)
                continue 

            item_detail_url = f"{BLIZZARD_API_BASE_URL}/data/wow/item/{item_id}"
            item_data = make_api_request(api_url=item_detail_url, params=static_params, headers=headers)
            if not item_data: 
                print(f"    WARNING: Failed to fetch details for item ID {item_id}.", flush=True)
                time.sleep(0.05); continue

            item_name = item_data.get("name")
            item_quality = item_data.get("quality", {}).get("name", "Unknown").upper()
            api_slot_type = item_data.get("inventory_type", {}).get("type")
            
            fetched_icon_url = None # Initialize to None
            media_href = item_data.get("media", {}).get("key", {}).get("href")
            if media_href:
                # print(f"    DEBUG: Fetching media for item ID {item_id} from {media_href}", flush=True)
                media_data = make_api_request(api_url=media_href, params=static_params, headers=headers)
                if media_data and "assets" in media_data:
                    for asset in media_data["assets"]:
                        if asset.get("key") == "icon": 
                            fetched_icon_url = asset.get("value")
                            # print(f"    DEBUG: Icon found for item ID {item_id}: {fetched_icon_url}", flush=True)
                            break
                # elif media_data:
                    # print(f"    DEBUG: Media data for item ID {item_id} fetched but no 'assets' or icon key found. Media: {media_data}", flush=True)
                # else:
                    # print(f"    DEBUG: Failed to fetch media data for item ID {item_id} from {media_href}", flush=True)
            # else:
                # print(f"    DEBUG: No media_href for item ID {item_id}.", flush=True)
            
            if item_name and item_quality == "EPIC" and api_slot_type and api_slot_type != "NON_EQUIP":
                if not db_session.query(PlayableSlot).filter_by(type=api_slot_type).first():
                    print(f"CRITICAL: API slot '{api_slot_type}' for item '{item_name}' (ID:{item_id}) missing in PlayableSlot.", flush=True)
                    continue
                if existing_item: # Item exists, icon_url was missing or we wouldn't be here
                    if fetched_icon_url: # Only update if we successfully fetched a new icon
                        existing_item.icon_url = fetched_icon_url
                        print(f"    Updating icon for existing item ID {item_id}: {item_name}", flush=True)
                        items_processed_count +=1 
                    # else:
                        # print(f"    DEBUG: Existing item ID {item_id}, but no new icon fetched to update.", flush=True)
                else: # New item
                    print(f"    Adding new item ID {item_id}: {item_name} (Slot: {api_slot_type}, Icon: {'Yes' if fetched_icon_url else 'No'})", flush=True)
                    db_session.add(Item(id=item_id, name=item_name, quality=item_quality, slot_type=api_slot_type,
                                     source_id=data_source_id, source_details=f"{source_name_friendly} - {enc_name}",
                                     icon_url=fetched_icon_url))
                    items_processed_count += 1
            time.sleep(0.05) # Be respectful of API limits
        try: 
            db_session.commit()
            print(f"  Committed items for encounter: {enc_name}", flush=True)
        except Exception as e: 
            db_session.rollback()
            print(f"    Error committing items for {enc_name}: {e}", flush=True)
        time.sleep(0.1) # Pause between encounters
    print(f"Finished {source_name_friendly}. Items added/updated with icons: {items_processed_count}", flush=True)

def main():
    print("Starting WoW Info Population Script...", flush=True)
    db_session = SessionLocal()
    print("Ensuring DB tables exist...", flush=True)
    Base.metadata.create_all(engine, tables=[
        PlayableClass.__table__, PlayableSpec.__table__, PlayableSlot.__table__, 
        DataSource.__table__, Item.__table__, Character.__table__, CharacterBiS.__table__
    ], checkfirst=True)
    print("DB tables verified/created.", flush=True)
    
    print("Clearing DataSource and Item tables. PlayableSlot additively updated.", flush=True)
    try:
        # Important: If CharacterBiS has entries pointing to Items, this delete will fail
        # unless the FK constraint has ON DELETE CASCADE or CharacterBiS is cleared first.
        # This script assumes it's okay to clear Items, or that dependent data is handled.
        db_session.query(Item).delete(synchronize_session=False)
        db_session.query(DataSource).delete(synchronize_session=False)
        db_session.commit()
        print("DataSource and Item tables cleared.", flush=True)
    except Exception as e:
        db_session.rollback()
        print(f"Error clearing tables: {e}. This might be due to existing CharacterBiS entries referencing Items.", flush=True)

    populate_playable_slots(db_session) 
    data_sources = populate_data_sources(db_session) 
    
    if not update_playable_classes_and_specs(db_session): 
        print("Error updating classes/specs. Aborting item processing.", flush=True)
        db_session.close(); return

    # Raid Items
    undermine_id = find_journal_instance_id("Liberation of Undermine", "instance")
    if undermine_id and "Liberation of Undermine" in data_sources:
        fetch_and_store_source_items(db_session, "Liberation of Undermine", undermine_id, data_sources["Liberation of Undermine"], "raid")
    
    # M+ Items
    mplus_source_name = "Mythic+ Season 2 Dungeons"
    if mplus_source_name in data_sources:
        mplus_dungeons = ["THE MOTHERLODE!!", "Theater of Pain", "Cinderbrew Meadery", "Priory of the Sacred Flame", 
                          "The Rookery", "Darkflame Cleft", "Operation: Floodgate", "Operation: Mechagon"]
        for d_name in mplus_dungeons:
            d_id = find_journal_instance_id(d_name, "instance")
            if d_id: fetch_and_store_source_items(db_session, d_name, d_id, data_sources[mplus_source_name], "dungeon")
            time.sleep(0.5)
    else: print(f"Data source '{mplus_source_name}' not found.", flush=True)

    db_session.close()
    print("WoW Info Population Script Finished.", flush=True)

if __name__ == "__main__":
    if not BLIZZARD_CLIENT_ID or not BLIZZARD_CLIENT_SECRET:
        print("FATAL: BLIZZARD_CLIENT_ID or BLIZZARD_CLIENT_SECRET missing.", flush=True)
        exit(1)
    main()
