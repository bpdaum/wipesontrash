# craft_tier_bis.py
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
    from helper_functions import get_blizzard_access_token, make_api_request as make_blizzard_api_request_helper
except ImportError:
    print("Error: helper_functions.py or expected variables not found.", flush=True)
    exit(1)

# --- Database Setup ---
DATABASE_URI = os.environ.get('DATABASE_URL')
if not DATABASE_URI:
    print("FATAL: DATABASE_URL environment variable not set for craft_tier_bis.py.", flush=True)
    exit(1)
else:
    if DATABASE_URI.startswith("postgres://"):
        DATABASE_URI = DATABASE_URI.replace("postgres://", "postgresql://", 1)

try:
    engine = create_engine(DATABASE_URI)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base = declarative_base()
except Exception as e:
     print(f"Error creating database engine in craft_tier_bis.py: {e}", flush=True)
     exit(1)

# --- Database Models (Subset needed for these functions) ---
class PlayableSlot(Base): 
    __tablename__ = 'playable_slot'
    id = Column(Integer, primary_key=True, autoincrement=True) 
    type = Column(String(50), unique=True, nullable=False, index=True) 
    name = Column(String(100), nullable=False) 
    display_order = Column(Integer, default=0) 
    # No need for items relationship here if only used for type validation by fetch_and_store_single_item_from_api

class DataSource(Base):
    __tablename__ = 'data_source'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), unique=True, nullable=False) 
    type = Column(String(50)) 
    # No need for items relationship if only querying for ID

class Item(Base):
    __tablename__ = 'item'
    id = Column(Integer, primary_key=True) # Blizzard Item ID
    name = Column(String(255), nullable=False, index=True)
    quality = Column(String(20)) 
    icon_url = Column(String(512), nullable=True)
    slot_type = Column(String(50), ForeignKey('playable_slot.type'), nullable=False, index=True) 
    source_id = Column(Integer, ForeignKey('data_source.id'), nullable=True, index=True) 
    source_details = Column(String(255), nullable=True) 
    # No need for relationships if only adding items

class Character(Base): # Defined for CharacterBiS foreign key
    __tablename__ = 'character'
    id = Column(Integer, primary_key=True)
    # Minimal definition for relationship, full model in wow_info.py or app.py

class CharacterBiS(Base): # Defined for querying item_ids
    __tablename__ = 'character_bis'
    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(Integer, ForeignKey('character.id'), nullable=False, index=True)
    slot_type_ui = Column(String(50), ForeignKey('playable_slot.type'), nullable=False, index=True) 
    item_id = Column(Integer, ForeignKey('item.id'), nullable=True) 
    # No need for relationships if only querying item_id

# --- Moved Functions ---

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
            
            item_ids_handled_this_profession = set()

            for skill_tier_summary in prof_detail_data["skill_tiers"]:
                skill_tier_name = skill_tier_summary.get("name", "")
                if CURRENT_EXPANSION_KEYWORD.lower() not in skill_tier_name.lower():
                    continue
                
                print(f"    Processing Skill Tier: {skill_tier_name}", flush=True)
                skill_tier_id = skill_tier_summary.get("id")
                if not skill_tier_id: continue

                skill_tier_detail_url = f"{BLIZZARD_API_BASE_URL}/data/wow/profession/{prof_id}/skill-tier/{skill_tier_id}"
                skill_tier_data = make_blizzard_api_request_helper(api_url=skill_tier_detail_url, params=static_params, headers=headers)
                time.sleep(0.05)

                if not skill_tier_data or "categories" not in skill_tier_data:
                    continue
                
                items_to_commit_for_this_profession_tier = []

                for category in skill_tier_data["categories"]:
                    if "recipes" not in category: continue
                    for recipe_ref in category["recipes"]:
                        recipe_id = recipe_ref.get("id")
                        if not recipe_id: continue

                        recipe_detail_url = f"{BLIZZARD_API_BASE_URL}/data/wow/recipe/{recipe_id}"
                        recipe_data = make_blizzard_api_request_helper(api_url=recipe_detail_url, params=static_params, headers=headers)
                        time.sleep(0.05)

                        if not recipe_data: 
                            continue
                        
                        crafted_item_ref = recipe_data.get("crafted_item") or \
                                           recipe_data.get("alliance_crafted_item") or \
                                           recipe_data.get("horde_crafted_item")
                        
                        if not crafted_item_ref or "id" not in crafted_item_ref: 
                            continue
                        
                        item_id = crafted_item_ref["id"]
                        
                        if item_id in item_ids_handled_this_profession:
                            continue
                        
                        existing_item = db_session.get(Item, item_id)
                        if existing_item and existing_item.icon_url: 
                            item_ids_handled_this_profession.add(item_id)
                            continue

                        item_detail_url = f"{BLIZZARD_API_BASE_URL}/data/wow/item/{item_id}"
                        item_data = make_blizzard_api_request_helper(api_url=item_detail_url, params=static_params, headers=headers)
                        if not item_data: 
                            item_ids_handled_this_profession.add(item_id) 
                            continue

                        item_name = item_data.get("name")
                        item_quality = item_data.get("quality", {}).get("name", "Unknown").upper()
                        api_slot_type = item_data.get("inventory_type", {}).get("type")
                        
                        item_ids_handled_this_profession.add(item_id) 

                        if item_name and item_quality in TARGET_ITEM_QUALITIES and \
                           api_slot_type and api_slot_type in EQUIPPABLE_GEAR_SLOT_CATEGORIES:
                            
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

                            if existing_item: 
                                if fetched_icon_url and not existing_item.icon_url:
                                    existing_item.icon_url = fetched_icon_url
                                    if existing_item.source_id != data_source_id or existing_item.source_details != prof_name: 
                                        existing_item.source_id = data_source_id
                                        existing_item.source_details = prof_name
                                    db_session.add(existing_item) 
                                    total_crafted_items_added_session +=1 
                            else: 
                                new_crafted_item = Item(id=item_id, name=item_name, quality=item_quality, slot_type=api_slot_type,
                                                 source_id=data_source_id, source_details=prof_name,
                                                 icon_url=fetched_icon_url)
                                items_to_commit_for_this_profession_tier.append(new_crafted_item)
                                total_crafted_items_added_session += 1
            
                if items_to_commit_for_this_profession_tier: 
                    db_session.add_all(items_to_commit_for_this_profession_tier)
            
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
        print("    ERROR: 'Manually Added via BiS Check' data source not found. This should have been created by wow_info.py. Cannot proceed with BiS item check.", flush=True)
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
                    if not db_session.get(Item, new_item_obj.id):
                        items_to_add_to_db.append(new_item_obj)
                    else:
                        print(f"    INFO: Item ID {new_item_obj.id} was already added to session or DB. Skipping duplicate add.", flush=True)

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


# --- MAIN EXECUTION for craft_tier_bis.py ---
def main():
    print("Starting Crafted Item and BiS Item Check Script...", flush=True)
    db_session = SessionLocal()

    # Ensure tables exist (especially if this script is run independently)
    # Define only the tables this script directly interacts with or has FKs to.
    tables_to_ensure_for_this_script = [
        PlayableSlot.__table__, DataSource.__table__, Item.__table__, 
        Character.__table__, CharacterBiS.__table__ 
    ]
    Base.metadata.create_all(engine, tables=tables_to_ensure_for_this_script, checkfirst=True)
    print("DB tables for craft_tier_bis verified/created if they didn't exist.", flush=True)

    # Fetch Data Source IDs needed by the functions
    crafting_source = db_session.query(DataSource).filter_by(name="Crafting - TWW S1").first()
    if crafting_source:
        fetch_and_store_crafted_items(db_session, crafting_source.id)
    else:
        print("Data source 'Crafting - TWW S1' not found. Cannot process crafted items. Ensure wow_info.py has run.", flush=True)

    ensure_character_bis_items_in_db(db_session)

    db_session.close()
    print("Crafted Item and BiS Item Check Script Finished.", flush=True)

if __name__ == "__main__":
    required_env_vars = ['BLIZZARD_CLIENT_ID', 'BLIZZARD_CLIENT_SECRET', 'DATABASE_URL', 'REGION']
    missing_vars = [var for var in required_env_vars if not os.environ.get(var)]
    if missing_vars:
        print(f"FATAL: Missing required environment variables for craft_tier_bis.py: {', '.join(missing_vars)}", flush=True)
        exit(1)
    
    print("All required environment variables for craft_tier_bis.py found.", flush=True)
    main()
