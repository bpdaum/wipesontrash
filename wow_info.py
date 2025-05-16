# wow_info.py
import os
import requests # Will be used by imported helpers
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
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)
    specs = relationship("PlayableSpec", back_populates="playable_class", cascade="all, delete-orphan")
    characters = relationship("Character", back_populates="playable_class") # Defined for relationship integrity
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
    type = Column(String(50)) # "Raid", "Dungeon"
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

class Character(Base): # Defined for schema integrity due to CharacterBiS FK
    __tablename__ = 'character'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False) # Minimal definition
    realm_slug = Column(String(100), nullable=False) # Minimal definition
    playable_class = relationship("PlayableClass", back_populates="characters")
    bis_selections = relationship("CharacterBiS", back_populates="character", cascade="all, delete-orphan")
    __table_args__ = (UniqueConstraint('name', 'realm_slug', name='_name_realm_uc'),) # Must match other scripts

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

# --- Blizzard API Configuration (accessed via helper_functions) ---
REGION = os.environ.get('REGION', 'us').lower() # Needed for namespace construction in helpers
BLIZZARD_CLIENT_ID = os.environ.get('BLIZZARD_CLIENT_ID')
BLIZZARD_CLIENT_SECRET = os.environ.get('BLIZZARD_CLIENT_SECRET')

# --- Data Population Functions ---

def populate_playable_slots(db_session):
    """Pre-populates the PlayableSlot table with standard equipment slots."""
    print("Populating Playable Slots...", flush=True)
    slots_data = [
        {"type": "HEAD", "name": "Head", "display_order": 1},
        {"type": "NECK", "name": "Neck", "display_order": 2},
        {"type": "SHOULDER", "name": "Shoulder", "display_order": 3},
        {"type": "BACK", "name": "Back", "display_order": 4},      # UI Name
        {"type": "CLOAK", "name": "Back (API)", "display_order": 4}, # Actual API type for some back items
        {"type": "CHEST", "name": "Chest", "display_order": 5},    # UI Name
        {"type": "ROBE", "name": "Chest (Robe API)", "display_order": 5},  # Actual API type for some chest items
        {"type": "SHIRT", "name": "Shirt", "display_order": 6},
        {"type": "TABARD", "name": "Tabard", "display_order": 7},
        {"type": "WRIST", "name": "Wrist", "display_order": 8},
        {"type": "HANDS", "name": "Hands", "display_order": 9},    # UI Name
        {"type": "HAND", "name": "Hands (API)", "display_order": 9},     # Actual API type
        {"type": "WAIST", "name": "Waist", "display_order": 10},
        {"type": "LEGS", "name": "Legs", "display_order": 11},
        {"type": "FEET", "name": "Feet", "display_order": 12},
        {"type": "FINGER", "name": "Finger (API Generic)", "display_order": 13}, # Generic API type
        {"type": "FINGER1", "name": "Finger 1", "display_order": 13},           # UI Specific
        {"type": "FINGER2", "name": "Finger 2", "display_order": 14},           # UI Specific
        {"type": "TRINKET", "name": "Trinket (API Generic)", "display_order": 15},# Generic API type
        {"type": "TRINKET1", "name": "Trinket 1", "display_order": 15},         # UI Specific
        {"type": "TRINKET2", "name": "Trinket 2", "display_order": 16},         # UI Specific
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


def update_playable_classes_and_specs(db_session):
    """ Fetches and updates PlayableClass and PlayableSpec tables from Blizzard API. """
    print("Updating PlayableClass and PlayableSpec tables from API...", flush=True)
    class_success = False
    spec_success = False

    # 1. Update Playable Classes
    print("Fetching playable class index...", flush=True)
    class_index_data = make_blizzard_api_request('/data/wow/playable-class/index')
    if class_index_data and 'classes' in class_index_data:
        classes_to_add_or_update = []
        for class_info_api in class_index_data['classes']:
            if class_info_api.get('id') and class_info_api.get('name'):
                existing_class = db_session.query(PlayableClass).get(class_info_api['id'])
                if existing_class:
                    if existing_class.name != class_info_api['name']:
                        existing_class.name = class_info_api['name']
                        print(f"  Updating class: ID {existing_class.id}, Name: {existing_class.name}", flush=True)
                else:
                    classes_to_add_or_update.append(PlayableClass(id=class_info_api['id'], name=class_info_api['name']))
        if classes_to_add_or_update:
            db_session.add_all(classes_to_add_or_update)
        print(f"PlayableClass table updated/prepared with {len(class_index_data['classes'])} potential entries.", flush=True)
        class_success = True
    else:
        print("Error: Failed to fetch or parse playable class index.", flush=True)

    # 2. Update Playable Specializations
    print("Fetching playable specialization index...", flush=True)
    spec_index_data = make_blizzard_api_request('/data/wow/playable-specialization/index')
    if spec_index_data and 'character_specializations' in spec_index_data:
        specs_to_add_or_update = []
        fetch_errors = 0
        processed_count = 0
        spec_list = spec_index_data['character_specializations']
        print(f"Fetched {len(spec_list)} specializations from index. Fetching details for class IDs...", flush=True)

        for spec_info_from_index in spec_list:
            spec_id = spec_info_from_index.get('id')
            spec_name_api = spec_info_from_index.get('name')
            detail_href = spec_info_from_index.get('key', {}).get('href')

            if not spec_id or not spec_name_api or not detail_href: continue

            spec_detail_data = make_blizzard_api_request(None, full_url=detail_href) # Use full_url for href
            processed_count +=1
            if spec_detail_data:
                class_info = spec_detail_data.get('playable_class', {})
                class_id = class_info.get('id')
                if class_id:
                    existing_spec = db_session.query(PlayableSpec).get(spec_id)
                    if existing_spec:
                        if existing_spec.name != spec_name_api or existing_spec.class_id != class_id:
                            existing_spec.name = spec_name_api
                            existing_spec.class_id = class_id
                            print(f"  Updating spec: ID {existing_spec.id}, Name: {existing_spec.name}", flush=True)
                    else:
                        specs_to_add_or_update.append(PlayableSpec(id=spec_id, name=spec_name_api, class_id=class_id))
                else:
                    print(f"Warning: No class_id for spec {spec_name_api} (ID: {spec_id})", flush=True)
                    fetch_errors +=1
            else:
                print(f"Warning: Failed to fetch details for spec {spec_name_api} (ID: {spec_id})", flush=True)
                fetch_errors +=1
            if processed_count % 10 == 0: print(f"Processed details for {processed_count}/{len(spec_list)} specs...", flush=True)
            time.sleep(0.05)

        if specs_to_add_or_update:
            db_session.add_all(specs_to_add_or_update)
        print(f"PlayableSpec table updated/prepared with {len(specs_to_add_or_update)} new/updated entries.", flush=True)
        spec_success = True
        if fetch_errors > 0:
            print(f"Warning: Encountered {fetch_errors} errors while fetching spec details.", flush=True)
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
            return False
    return False


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

    if not instance_data or "encounters" not in instance_data: # Dungeons also have an "encounters" array for bosses
        print(f"Error: Could not fetch instance data or encounters for {source_type} ID {source_journal_id}. Response: {instance_data}", flush=True)
        return

    items_added_count = 0
    for encounter_ref in instance_data["encounters"]:
        encounter_id = encounter_ref["id"]
        encounter_name = encounter_ref["name"]
        print(f"  Fetching loot for encounter: {encounter_name} (ID: {encounter_id}) in {source_name_friendly}", flush=True)
        
        items_to_process = encounter_ref.get("items")

        if not items_to_process:
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
    print("Starting WoW Info Population Script (Classes, Specs, Items)...", flush=True)
    db_session = SessionLocal()

    # Create all tables defined in Base if they don't exist.
    # This ensures tables are present for other scripts like update_roster_data.py
    print("Ensuring all database tables exist (will create if not present)...", flush=True)
    Base.metadata.create_all(engine, checkfirst=True)
    print("Database tables verified/created.", flush=True)

    # Clear ONLY the tables this script is responsible for before repopulating
    print("Clearing item-related and static WoW data tables (Item, DataSource, PlayableSlot, PlayableSpec, PlayableClass)...", flush=True)
    try:
        # Delete in order of dependency
        db_session.query(Item).delete(synchronize_session=False)
        db_session.query(DataSource).delete(synchronize_session=False)
        db_session.query(PlayableSlot).delete(synchronize_session=False)
        db_session.query(PlayableSpec).delete(synchronize_session=False)
        db_session.query(PlayableClass).delete(synchronize_session=False)
        # CharacterBiS is cleared by app.py or if items are cleared.
        # This script doesn't directly manage CharacterBiS population.
        db_session.commit()
        print("Item-related and static WoW data tables cleared.", flush=True)
    except Exception as e:
        db_session.rollback()
        print(f"Error clearing tables: {e}", flush=True)
        db_session.close()
        return

    populate_playable_slots(db_session) # Populates and commits
    data_sources = populate_data_sources(db_session) # Populates and commits
    
    if not update_playable_classes_and_specs(db_session): # Populates and commits
        print("Error: Failed to update playable classes and specs. Aborting further item processing.", flush=True)
        db_session.close()
        return

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
            "Operation: Floodgate", "Operation: Mechagon" # Mechagon might be split, e.g. "Operation: Mechagon - Workshop"
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
    print("WoW Info Population Script Finished.", flush=True)

if __name__ == "__main__":
    if not BLIZZARD_CLIENT_ID or not BLIZZARD_CLIENT_SECRET:
        print("FATAL: BLIZZARD_CLIENT_ID or BLIZZARD_CLIENT_SECRET environment variables not set.", flush=True)
        exit(1)
    main()
