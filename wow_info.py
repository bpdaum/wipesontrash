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
# Models managed by this script + dependent models for schema integrity
class PlayableClass(Base):
    __tablename__ = 'playable_class'
    id = Column(Integer, primary_key=True) # Blizzard Class ID
    name = Column(String(50), unique=True, nullable=False)
    specs = relationship("PlayableSpec", back_populates="playable_class", cascade="all, delete-orphan")
    characters = relationship("Character", back_populates="playable_class") 
    def __repr__(self): return f'<PlayableClass {self.name}>'

class PlayableSpec(Base):
    __tablename__ = 'playable_spec'
    id = Column(Integer, primary_key=True) # Blizzard Spec ID
    name = Column(String(50), nullable=False)
    class_id = Column(Integer, ForeignKey('playable_class.id'), nullable=False)
    playable_class = relationship("PlayableClass", back_populates="specs")
    def __repr__(self): return f'<PlayableSpec {self.name} (Class ID: {self.class_id})>'

class PlayableSlot(Base): # Stores both API item slot types and canonical UI slot types
    __tablename__ = 'playable_slot'
    id = Column(Integer, primary_key=True, autoincrement=True) # Auto-generated PK
    type = Column(String(50), unique=True, nullable=False, index=True) # e.g., "HEAD", "FINGER", "FINGER1", "MAIN_HAND"
    name = Column(String(100), nullable=False) # e.g., "Head", "Finger (API)", "Finger 1", "Main Hand"
    display_order = Column(Integer, default=0) # For ordering in UI if needed
    # Relationship: An Item's slot_type points here.
    items = relationship("Item", back_populates="slot", cascade="all, delete-orphan")
    # Relationship: A CharacterBiS's slot_type_ui points here.
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
    # Item.slot_type refers to the API slot type of the item (e.g. "HEAD", "FINGER", "WEAPON")
    slot_type = Column(String(50), ForeignKey('playable_slot.type'), nullable=False, index=True) 
    slot = relationship("PlayableSlot", back_populates="items")
    source_id = Column(Integer, ForeignKey('data_source.id'), nullable=True, index=True)
    source = relationship("DataSource", back_populates="items")
    source_details = Column(String(255)) # e.g., "Boss Name"
    bis_selections = relationship("CharacterBiS", back_populates="item", cascade="all, delete-orphan")
    def __repr__(self): return f'<Item {self.name} (ID: {self.id})>'

class Character(Base): # Defined for schema integrity due to CharacterBiS FK
    __tablename__ = 'character'
    id = Column(Integer, primary_key=True) # Blizzard Character ID
    name = Column(String(100), nullable=False) 
    realm_slug = Column(String(100), nullable=False) 
    class_id = Column(Integer, ForeignKey('playable_class.id')) # Added for FK integrity
    is_active = Column(Boolean, default=True, nullable=False, index=True) # For soft deletes

    playable_class = relationship("PlayableClass", back_populates="characters")
    bis_selections = relationship("CharacterBiS", back_populates="character", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint('name', 'realm_slug', name='_name_realm_uc'),) 

class CharacterBiS(Base):
    __tablename__ = 'character_bis'
    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(Integer, ForeignKey('character.id'), nullable=False, index=True)
    # CharacterBiS.slot_type_ui refers to a canonical UI slot type (e.g. "FINGER1", "MAIN_HAND")
    # This type must exist in the PlayableSlot table for the FK to work.
    slot_type_ui = Column(String(50), ForeignKey('playable_slot.type'), nullable=False, index=True) 
    item_id = Column(Integer, ForeignKey('item.id'), nullable=True) # Blizzard Item ID
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    character = relationship("Character", back_populates="bis_selections")
    # The foreign_keys argument specifies which column in CharacterBiS links to PlayableSlot.type
    slot = relationship("PlayableSlot", foreign_keys=[slot_type_ui]) 
    item = relationship("Item", back_populates="bis_selections")
    
    __table_args__ = (UniqueConstraint('character_id', 'slot_type_ui', name='_character_slot_ui_uc'),)
    def __repr__(self): return f'<CharacterBiS CharID: {self.character_id} SlotUI: {self.slot_type_ui} ItemID: {self.item_id}>'

# --- Data Population Functions ---

def populate_playable_slots(db_session):
    """
    Pre-populates the PlayableSlot table with both API item slot types and canonical UI slot types.
    This table serves as a central reference for Item.slot_type (API types) and CharacterBiS.slot_type_ui (UI types).
    """
    print("Populating Playable Slots...", flush=True)
    # Ensure this list is comprehensive for all types needed by Item.slot_type and CharacterBiS.slot_type_ui
    slots_data = [
        # API Primary Types (referenced by Item.slot_type)
        {"type": "HEAD", "name": "Head (API)", "display_order": 1},
        {"type": "NECK", "name": "Neck (API)", "display_order": 2},
        {"type": "SHOULDER", "name": "Shoulder (API)", "display_order": 3},
        {"type": "BACK", "name": "Back (API - Generic)", "display_order": 4}, # Generic API type for back
        {"type": "CLOAK", "name": "Cloak (API - Specific)", "display_order": 4}, # Specific API type for back
        {"type": "CHEST", "name": "Chest (API - Generic)", "display_order": 5},
        {"type": "ROBE", "name": "Robe (API - Chest variant)", "display_order": 5},
        {"type": "SHIRT", "name": "Shirt (API)", "display_order": 6}, # Usually not for BiS
        {"type": "TABARD", "name": "Tabard (API)", "display_order": 7}, # Usually not for BiS
        {"type": "WRIST", "name": "Wrist (API)", "display_order": 8},
        {"type": "HAND", "name": "Hands (API - Generic)", "display_order": 9}, # Generic API type for hands
        {"type": "HANDS", "name": "Hands (API - Specific)", "display_order": 9}, # Specific API type for hands
        {"type": "WAIST", "name": "Waist (API)", "display_order": 10},
        {"type": "LEGS", "name": "Legs (API)", "display_order": 11},
        {"type": "FEET", "name": "Feet (API)", "display_order": 12},
        {"type": "FINGER", "name": "Finger (API - Generic)", "display_order": 13}, # Generic API type for rings
        {"type": "TRINKET", "name": "Trinket (API - Generic)", "display_order": 15},# Generic API type for trinkets
        {"type": "WEAPON", "name": "Weapon (API - Generic)", "display_order": 17}, # Generic, might be 1H or 2H
        {"type": "ONE_HAND", "name": "One-Hand Weapon (API)", "display_order": 17},
        {"type": "TWOHWEAPON", "name": "Two-Hand Weapon (API)", "display_order": 17}, # Blizzard's type for 2H
        {"type": "MAIN_HAND", "name": "Main Hand (API - Equipment Slot)", "display_order": 17}, # Blizzard equipment slot type
        {"type": "OFF_HAND", "name": "Off Hand (API - Equipment Slot)", "display_order": 18}, # Blizzard equipment slot type
        {"type": "SHIELD", "name": "Shield (API)", "display_order": 18},
        {"type": "HOLDABLE", "name": "Holdable (API - Off-hand)", "display_order": 18},
        
        # Canonical UI Slot Types (referenced by CharacterBiS.slot_type_ui and loot.html)
        # Ensure these names match what loot.html expects for its `data-slot-type`
        # If a UI type is identical to an API type (e.g. "HEAD"), it doesn't need to be duplicated if the name is sufficient.
        # However, for clarity or if display names differ, they can be separate entries.
        # For simplicity, we'll assume the API types above cover most direct needs,
        # and loot.html's canonical list will map to these.
        # The crucial ones for CharacterBiS are the distinct UI slots like FINGER1, FINGER2.
        {"type": "FINGER1", "name": "Finger 1 (UI)", "display_order": 13},
        {"type": "FINGER2", "name": "Finger 2 (UI)", "display_order": 14},
        {"type": "TRINKET1", "name": "Trinket 1 (UI)", "display_order": 15},
        {"type": "TRINKET2", "name": "Trinket 2 (UI)", "display_order": 16},
        # UI "MAIN_HAND" and "OFF_HAND" are distinct from Blizzard's equipment slot types if needed for BiS,
        # but can map to the same 'type' if their names are sufficient.
        # The `canonical_ui_slots` in app.py's /loot route defines what the UI renders.
        # This table just needs to have all `type` values that will be used as FKs.
    ]
    for slot_data in slots_data:
        slot = db_session.query(PlayableSlot).filter_by(type=slot_data["type"]).first()
        if not slot:
            slot = PlayableSlot(type=slot_data["type"], name=slot_data["name"], display_order=slot_data["display_order"])
            db_session.add(slot)
        elif slot.name != slot_data["name"] or slot.display_order != slot_data["display_order"]: # Update if changed
            slot.name = slot_data["name"]
            slot.display_order = slot_data["display_order"]
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
        {"name": "Mythic+ Season 2 Dungeons", "type": "Dungeon"} # Example
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
    """ Fetches and updates PlayableClass and PlayableSpec tables from Blizzard API. """
    print("Updating PlayableClass and PlayableSpec tables from API...", flush=True)
    class_success = False
    spec_success = False

    access_token = get_blizzard_access_token()
    if not access_token:
        print("Error: Could not get Blizzard access token for class/spec update. Aborting.", flush=True)
        return False 

    headers = {"Authorization": f"Bearer {access_token}"}
    static_params = {"namespace": f"static-{REGION}", "locale": "en_US"}

    # 1. Update Playable Classes
    print("Fetching playable class index...", flush=True)
    class_endpoint = '/data/wow/playable-class/index'
    class_url = f"{BLIZZARD_API_BASE_URL}{class_endpoint}"
    class_index_data = make_api_request(api_url=class_url, params=static_params, headers=headers) 

    if class_index_data and 'classes' in class_index_data:
        for class_info_api in class_index_data['classes']:
            if class_info_api.get('id') and class_info_api.get('name'):
                existing_class = db_session.get(PlayableClass, class_info_api['id']) # Use Session.get()
                if existing_class:
                    if existing_class.name != class_info_api['name']:
                        existing_class.name = class_info_api['name']
                else:
                    db_session.add(PlayableClass(id=class_info_api['id'], name=class_info_api['name']))
        class_success = True
        print(f"PlayableClass table updated/prepared.", flush=True)
    else:
        print("Error: Failed to fetch or parse playable class index.", flush=True)

    # 2. Update Playable Specializations
    print("Fetching playable specialization index...", flush=True)
    spec_endpoint = '/data/wow/playable-specialization/index'
    spec_url = f"{BLIZZARD_API_BASE_URL}{spec_endpoint}"
    spec_index_data = make_api_request(api_url=spec_url, params=static_params, headers=headers)
    
    if spec_index_data and 'character_specializations' in spec_index_data:
        fetch_errors = 0
        for spec_info_from_index in spec_index_data['character_specializations']:
            spec_id = spec_info_from_index.get('id')
            spec_name_api = spec_info_from_index.get('name')
            detail_href = spec_info_from_index.get('key', {}).get('href') 

            if not spec_id or not spec_name_api or not detail_href: continue

            spec_detail_data = make_api_request(api_url=detail_href, params=static_params, headers=headers) 
            if spec_detail_data:
                class_id_from_detail = spec_detail_data.get('playable_class', {}).get('id')
                if class_id_from_detail:
                    existing_spec = db_session.get(PlayableSpec, spec_id) # Use Session.get()
                    if existing_spec:
                        if existing_spec.name != spec_name_api or existing_spec.class_id != class_id_from_detail:
                            existing_spec.name = spec_name_api
                            existing_spec.class_id = class_id_from_detail
                    else:
                        db_session.add(PlayableSpec(id=spec_id, name=spec_name_api, class_id=class_id_from_detail))
                else: fetch_errors +=1
            else: fetch_errors +=1
            time.sleep(0.05) 
        spec_success = True
        print(f"PlayableSpec table updated/prepared. Fetch errors: {fetch_errors}", flush=True)
    else:
        print("Error: Failed to fetch or parse playable specialization index.", flush=True)
    
    if class_success and spec_success:
        try:
            db_session.commit()
            print("PlayableClass and PlayableSpec tables committed.", flush=True)
            return True
        except Exception as e:
            db_session.rollback()
            print(f"Error committing static class/spec data: {e}", flush=True)
    else:
        db_session.rollback() 
    return False

def find_journal_instance_id(instance_name_to_find, instance_type="instance"):
    print(f"Attempting to find Journal ID for {instance_type}: '{instance_name_to_find}'", flush=True)
    access_token = get_blizzard_access_token()
    if not access_token: return None
    headers = {"Authorization": f"Bearer {access_token}"}
    static_params = {"namespace": f"static-{REGION}", "locale": "en_US"}
    
    endpoint = f"/data/wow/journal-{instance_type}/index"
    api_url = f"{BLIZZARD_API_BASE_URL}{endpoint}"
    index_data = make_api_request(api_url=api_url, params=static_params, headers=headers)

    if index_data and f"{instance_type}s" in index_data:
        for instance in index_data[f"{instance_type}s"]:
            if instance.get("name", "").lower() == instance_name_to_find.lower():
                instance_id = instance.get("id")
                print(f"Found {instance_type} '{instance_name_to_find}' with ID: {instance_id}", flush=True)
                return instance_id
        print(f"Error: {instance_type.capitalize()} '{instance_name_to_find}' not found in the journal index.", flush=True)
    else:
        print(f"Error: Could not fetch or parse journal {instance_type} index.", flush=True)
    return None

def fetch_and_store_source_items(db_session, source_name_friendly, source_journal_id, data_source_id, source_type="raid"):
    print(f"Fetching items for {source_type}: {source_name_friendly} (Journal ID: {source_journal_id})", flush=True)
    access_token = get_blizzard_access_token()
    if not access_token: return
    headers = {"Authorization": f"Bearer {access_token}"}
    static_params = {"namespace": f"static-{REGION}", "locale": "en_US"}

    instance_api_endpoint_suffix = ""
    if source_type == "raid": instance_api_endpoint_suffix = f"/data/wow/journal-instance/{source_journal_id}"
    elif source_type == "dungeon": instance_api_endpoint_suffix = f"/data/wow/journal-dungeon/{source_journal_id}"
    else: print(f"Error: Unknown source_type '{source_type}'."); return
    
    instance_api_url = f"{BLIZZARD_API_BASE_URL}{instance_api_endpoint_suffix}"
    instance_data = make_api_request(api_url=instance_api_url, params=static_params, headers=headers)

    if not instance_data or "encounters" not in instance_data: 
        print(f"Error: Could not fetch instance data for {source_type} ID {source_journal_id}.", flush=True)
        return

    items_added_count = 0
    for encounter_ref in instance_data["encounters"]:
        encounter_id = encounter_ref.get("id")
        encounter_name = encounter_ref.get("name")
        if not encounter_id or not encounter_name: continue
            
        print(f"  Fetching loot for encounter: {encounter_name} (ID: {encounter_id})", flush=True)
        items_to_process = encounter_ref.get("items")
        if not items_to_process: # If items not in summary, fetch encounter details
            encounter_detail_url = f"{BLIZZARD_API_BASE_URL}/data/wow/journal-encounter/{encounter_id}"
            encounter_detail_data = make_api_request(api_url=encounter_detail_url, params=static_params, headers=headers)
            if not encounter_detail_data or "items" not in encounter_detail_data: continue
            items_to_process = encounter_detail_data["items"]
        
        if not items_to_process: continue

        for item_entry in items_to_process:
            item_ref = item_entry.get("item")
            if not item_ref or "id" not in item_ref: continue
            
            item_id = item_ref["id"]
            # Check if item already exists to avoid re-fetching media if icon_url is present
            existing_item_check = db_session.get(Item, item_id) # Use Session.get()
            if existing_item_check and existing_item_check.icon_url:
                # print(f"    Item ID {item_id} already in DB with icon. Skipping media fetch.", flush=True) # Optional: for less verbose logs
                # Ensure source details are accurate if item can drop from multiple places (not handled here)
                continue # Or update source_details if necessary

            item_detail_url = f"{BLIZZARD_API_BASE_URL}/data/wow/item/{item_id}"
            item_detail_data = make_api_request(api_url=item_detail_url, params=static_params, headers=headers)
            if not item_detail_data: time.sleep(0.05); continue

            item_name = item_detail_data.get("name")
            item_quality = item_detail_data.get("quality", {}).get("name", "Unknown").upper()
            slot_type_api = item_detail_data.get("inventory_type", {}).get("type")
            
            icon_url_fetched = None
            media_key_href = item_detail_data.get("media", {}).get("key", {}).get("href")
            if media_key_href: 
                item_media_data = make_api_request(api_url=media_key_href, params=static_params, headers=headers)
                if item_media_data and "assets" in item_media_data:
                    for asset in item_media_data["assets"]:
                        if asset.get("key") == "icon": icon_url_fetched = asset.get("value"); break
            
            if item_name and item_quality == "EPIC" and slot_type_api and slot_type_api != "NON_EQUIP":
                slot_exists = db_session.query(PlayableSlot).filter_by(type=slot_type_api).first()
                if not slot_exists:
                    print(f"CRITICAL WARNING: API slot type '{slot_type_api}' for item '{item_name}' (ID: {item_id}) not found in PlayableSlot. Add it to populate_playable_slots().", flush=True)
                    continue

                if existing_item_check: # Item exists, but maybe icon was missing
                    if not existing_item_check.icon_url and icon_url_fetched:
                        existing_item_check.icon_url = icon_url_fetched
                        print(f"    Updating icon for existing item ID {item_id}: {item_name}", flush=True)
                        items_added_count +=1 # Counting as an "update" that matters
                else: # New item
                    new_item = Item(
                        id=item_id, name=item_name, quality=item_quality,
                        slot_type=slot_type_api, # Use the API slot type from item data
                        source_id=data_source_id,
                        source_details=f"{source_name_friendly} - {encounter_name}",
                        icon_url=icon_url_fetched
                    )
                    db_session.add(new_item)
                    items_added_count += 1
                    print(f"    Adding new item ID {item_id}: {item_name} (Icon: {'Yes' if icon_url_fetched else 'No'})", flush=True)
            time.sleep(0.05) 

        try: db_session.commit()
        except Exception as e: db_session.rollback(); print(f"    Error committing items for {encounter_name}: {e}", flush=True)
        time.sleep(0.1) 
    print(f"Finished {source_type} {source_name_friendly}. New/Updated items: {items_added_count}", flush=True)


def main():
    print("Starting WoW Info Population Script (Classes, Specs, Items)...", flush=True)
    db_session = SessionLocal()

    print("Ensuring all database tables exist (will create if not present)...", flush=True)
    # Define all tables this script might interact with or that have FK dependencies
    tables_to_ensure = [
        PlayableClass.__table__, PlayableSpec.__table__, PlayableSlot.__table__, 
        DataSource.__table__, Item.__table__, Character.__table__, CharacterBiS.__table__
    ]
    Base.metadata.create_all(engine, tables=tables_to_ensure, checkfirst=True)
    print("Database tables verified/created.", flush=True)

    # Selective clearing: Only clear tables this script is fully responsible for repopulating.
    # PlayableClass and PlayableSpec are additively updated.
    # Item, DataSource, PlayableSlot are cleared and repopulated.
    # Character and CharacterBiS are NOT touched by this script's clearing logic.
    print("Clearing DataSource, Item, PlayableSlot tables...", flush=True)
    try:
        # Order of deletion for FKs: Item depends on PlayableSlot and DataSource.
        # CharacterBiS depends on Item and PlayableSlot, but we are not clearing CharacterBiS here.
        # If CharacterBiS has entries, deleting Item or PlayableSlot will fail FK constraints
        # UNLESS those FKs have ON DELETE CASCADE (not typical for BiS lists).
        # For a clean run, CharacterBiS might need to be cleared by app.py or another process
        # if Item/PlayableSlot definitions change significantly.
        # Assuming for now that clearing these is acceptable or FKs are handled.
        
        # Safest to delete in order: Items first, then their dependencies if they are also being cleared.
        # However, CharacterBiS points to Item and PlayableSlot.
        # If CharacterBiS is NOT empty, these deletes will fail.
        # This script should ideally NOT delete Item/PlayableSlot if CharacterBiS exists
        # and is managed elsewhere.
        # For now, we proceed with the original intent to clear these item-specific tables.
        
        # To safely clear Item, DataSource, PlayableSlot when CharacterBiS might exist:
        # 1. Clear CharacterBiS (if this script were responsible for it)
        # 2. Clear Item
        # 3. Clear PlayableSlot (if no Items reference it anymore)
        # 4. Clear DataSource (if no Items reference it anymore)
        
        # Current simplified approach (might fail if CharacterBiS has data):
        db_session.query(Item).delete(synchronize_session=False)
        # PlayableSlot is tricky because CharacterBiS.slot_type_ui points to it.
        # If we clear PlayableSlot, CharacterBiS entries become orphaned or FK fails.
        # It's better to additively update PlayableSlot like classes/specs.
        # db_session.query(PlayableSlot).delete(synchronize_session=False) # REMOVING THIS DELETE
        db_session.query(DataSource).delete(synchronize_session=False)
        db_session.commit()
        print("DataSource and Item tables cleared. PlayableSlot will be additively updated.", flush=True)
    except Exception as e:
        db_session.rollback()
        print(f"Error clearing tables: {e}. This might be due to existing CharacterBiS entries. Ensure PlayableSlot is not deleted if BiS entries depend on it.", flush=True)
        # db_session.close(); return # Optional: stop if clearing fails critically

    populate_playable_slots(db_session) # Additively updates/populates PlayableSlot
    data_sources = populate_data_sources(db_session) # Clears and repopulates DataSource
    
    if not update_playable_classes_and_specs(db_session): 
        print("Error: Failed to update playable classes and specs. Aborting further item processing.", flush=True)
        db_session.close(); return

    target_raid_name_for_items = "Liberation of Undermine"
    liberation_of_undermine_journal_id = find_journal_instance_id(target_raid_name_for_items, instance_type="instance")
    if liberation_of_undermine_journal_id:
        lou_source_id = data_sources.get("Liberation of Undermine")
        if lou_source_id:
            fetch_and_store_source_items(db_session, "Liberation of Undermine", liberation_of_undermine_journal_id, lou_source_id, source_type="raid")
    else:
        print(f"Could not find Journal ID for '{target_raid_name_for_items}'.", flush=True)

    print("\n--- Processing Mythic+ Dungeons ---", flush=True)
    mplus_source_name = "Mythic+ Season 2 Dungeons" # Example name
    mplus_source_id = data_sources.get(mplus_source_name)
    if not mplus_source_id:
        print(f"Error: '{mplus_source_name}' data source not found.", flush=True)
    else:
        mplus_dungeon_names = [ # Update with actual TWW S1/S2 dungeon names from journal
            "THE MOTHERLODE!!", "Theater of Pain", "Cinderbrew Meadery",
            "Priory of the Sacred Flame", "The Rookery", "Darkflame Cleft",
            "Operation: Floodgate", "Operation: Mechagon" 
        ]
        for dungeon_name in mplus_dungeon_names:
            dungeon_journal_id = find_journal_instance_id(dungeon_name, instance_type="dungeon")
            if dungeon_journal_id:
                fetch_and_store_source_items(db_session, dungeon_name, dungeon_journal_id, mplus_source_id, source_type="dungeon")
            else:
                print(f"Could not find Journal ID for dungeon '{dungeon_name}'.", flush=True)
            time.sleep(0.5) 

    db_session.close()
    print("WoW Info Population Script Finished.", flush=True)

if __name__ == "__main__":
    if not BLIZZARD_CLIENT_ID or not BLIZZARD_CLIENT_SECRET:
        print("FATAL: BLIZZARD_CLIENT_ID or BLIZZARD_CLIENT_SECRET environment variables not set.", flush=True)
        exit(1)
    main()
