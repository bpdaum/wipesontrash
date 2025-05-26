# craft_tier_bis.py
import os
import requests 
import time
from datetime import datetime
import json
import urllib.parse # For URL encoding item names

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

class DataSource(Base):
    __tablename__ = 'data_source'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), unique=True, nullable=False) 
    type = Column(String(50)) 

class Item(Base):
    __tablename__ = 'item'
    id = Column(Integer, primary_key=True) # Blizzard Item ID
    name = Column(String(255), nullable=False, index=True)
    quality = Column(String(20)) 
    icon_url = Column(String(512), nullable=True)
    slot_type = Column(String(50), ForeignKey('playable_slot.type'), nullable=False, index=True) 
    source_id = Column(Integer, ForeignKey('data_source.id'), nullable=True, index=True) 
    source_details = Column(String(255), nullable=True) 

class Character(Base): 
    __tablename__ = 'character'
    id = Column(Integer, primary_key=True)

class CharacterBiS(Base): 
    __tablename__ = 'character_bis'
    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(Integer, ForeignKey('character.id'), nullable=False, index=True)
    slot_type_ui = Column(String(50), ForeignKey('playable_slot.type'), nullable=False, index=True) 
    item_id = Column(Integer, ForeignKey('item.id'), nullable=True) 

# --- Helper function to fetch item details and icon by ID ---
def get_full_item_details_by_id(item_id, headers, static_params):
    """Fetches full item details and icon URL for a given item ID."""
    item_detail_url = f"{BLIZZARD_API_BASE_URL}/data/wow/item/{item_id}"
    item_data = make_blizzard_api_request_helper(api_url=item_detail_url, params=static_params, headers=headers)
    time.sleep(0.05) # API call delay

    if not item_data:
        return None, None, None, None # name, quality, slot_type, icon_url

    item_name = item_data.get("name")
    quality = item_data.get("quality", {}).get("name", "Unknown").upper()
    slot_type = item_data.get("inventory_type", {}).get("type")
    
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
    return item_name, quality, slot_type, icon_url

# --- Modified and Existing Functions ---

def fetch_and_store_crafted_items(db_session, data_source_id, existing_playable_slot_types_set):
    print("\n--- Processing Crafted Items (via Item Name Search) ---", flush=True)
    access_token = get_blizzard_access_token()
    if not access_token:
        print("  ERROR: Could not get Blizzard access token for crafted items. Aborting.", flush=True)
        return
    headers = {"Authorization": f"Bearer {access_token}"}
    # Static params for item detail/media fetches, search API has its own specific params.
    static_params_for_detail = {"namespace": f"static-{REGION}", "locale": "en_US"}


    target_professions = { # Profession Name: API Profession ID
        "Blacksmithing": 164, "Leatherworking": 165, "Tailoring": 197,
        "Jewelcrafting": 755, "Engineering": 202 
    }
    TARGET_ITEM_QUALITIES = ["EPIC", "RARE"] # Consider if Uncommon items are needed
    EQUIPPABLE_GEAR_SLOT_CATEGORIES = [
        "HEAD", "NECK", "SHOULDER", "BACK", "CLOAK", "CHEST", "ROBE", "WRIST",
        "HANDS", "HAND", "WAIST", "LEGS", "FEET", "FINGER", "TRINKET",
        "WEAPON", "ONE_HAND", "TWOHWEAPON", "MAIN_HAND", "OFF_HAND", "SHIELD", "HOLDABLE",
        "RANGEDRIGHT", "RANGED"
    ]
    # Keyword to identify relevant expansion's crafting recipes (adjust as needed for new expansions)
    CURRENT_EXPANSION_KEYWORD = "Khaz Algar" 
    # Locale for name search parameter key, e.g., name.en_US, name.de_DE
    # For simplicity, using en_US for search, API usually handles this well.
    # More robust would be to use REGION_LOCALE_MAP if available.
    SEARCH_NAME_LOCALE_KEY = "name.en_US" 

    total_crafted_items_processed_session = 0 # Tracks items added or updated

    prof_index_url = f"{BLIZZARD_API_BASE_URL}/data/wow/profession/index"
    prof_index_data = make_blizzard_api_request_helper(api_url=prof_index_url, params=static_params_for_detail, headers=headers)

    if not prof_index_data or "professions" not in prof_index_data:
        print("  ERROR: Could not fetch profession index.", flush=True)
        return

    for prof_summary in prof_index_data["professions"]:
        prof_name = prof_summary.get("name")
        prof_id = prof_summary.get("id")

        if prof_name in target_professions and target_professions[prof_name] == prof_id:
            print(f"  Processing Profession: {prof_name} (ID: {prof_id})", flush=True)
            
            prof_detail_url = f"{BLIZZARD_API_BASE_URL}/data/wow/profession/{prof_id}"
            prof_detail_data = make_blizzard_api_request_helper(api_url=prof_detail_url, params=static_params_for_detail, headers=headers)
            time.sleep(0.05)

            if not prof_detail_data or "skill_tiers" not in prof_detail_data:
                print(f"    ERROR: Could not fetch details or skill tiers for {prof_name}.", flush=True)
                continue
            
            item_ids_handled_this_profession = set() # Tracks item IDs to avoid re-processing within this profession

            for skill_tier_summary in prof_detail_data["skill_tiers"]:
                skill_tier_name = skill_tier_summary.get("name", "")
                if CURRENT_EXPANSION_KEYWORD.lower() not in skill_tier_name.lower():
                    continue # Skip tiers not matching current expansion
                
                print(f"    Processing Skill Tier: {skill_tier_name}", flush=True)
                skill_tier_id = skill_tier_summary.get("id")
                if not skill_tier_id: continue

                skill_tier_detail_url = f"{BLIZZARD_API_BASE_URL}/data/wow/profession/{prof_id}/skill-tier/{skill_tier_id}"
                skill_tier_data = make_blizzard_api_request_helper(api_url=skill_tier_detail_url, params=static_params_for_detail, headers=headers)
                time.sleep(0.05)

                if not skill_tier_data or "categories" not in skill_tier_data:
                    continue
                
                items_to_commit_for_this_tier = []

                for category in skill_tier_data["categories"]:
                    if "recipes" not in category: continue
                    for recipe_ref in category["recipes"]:
                        recipe_id_from_ref = recipe_ref.get("id")
                        recipe_name_from_ref = recipe_ref.get("name", f"Recipe ID {recipe_id_from_ref}") # Name from recipe list
                        
                        if not recipe_id_from_ref: continue

                        # Fetch recipe details to get the crafted item's name
                        recipe_detail_url = f"{BLIZZARD_API_BASE_URL}/data/wow/recipe/{recipe_id_from_ref}"
                        recipe_data = make_blizzard_api_request_helper(api_url=recipe_detail_url, params=static_params_for_detail, headers=headers)
                        time.sleep(0.05)

                        if not recipe_data: 
                            print(f"          WARNING: Failed to fetch details for recipe ID {recipe_id_from_ref} ('{recipe_name_from_ref}').", flush=True)
                            continue
                        
                        crafted_item_info = recipe_data.get("crafted_item") or \
                                           recipe_data.get("alliance_crafted_item") or \
                                           recipe_data.get("horde_crafted_item")
                        
                        if not crafted_item_info or "name" not in crafted_item_info:
                            # print(f"          INFO: Recipe {recipe_id_from_ref} ('{recipe_name_from_ref}') does not have a crafted item name. Skipping.", flush=True)
                            continue
                        
                        item_name_from_recipe_detail = crafted_item_info.get("name")
                        if not item_name_from_recipe_detail:
                            # print(f"          INFO: Recipe {recipe_id_from_ref} ('{recipe_name_from_ref}') - crafted item name is empty. Skipping.", flush=True)
                            continue

                        print(f"          Processing recipe for item name: '{item_name_from_recipe_detail}' (from recipe ID {recipe_id_from_ref})", flush=True)

                        # Search for the item by name using Blizzard Item Search API
                        search_params = {
                            "namespace": f"static-{REGION}", # Search namespace is static
                            "locale": "en_US",             # Response locale for search results
                            SEARCH_NAME_LOCALE_KEY: item_name_from_recipe_detail, # Name to search
                            "orderby": "id",               # Order by ID (often helps with exact matches)
                            "_page": 1,
                            "_pageSize": 1                 # We only want the top hit
                        }
                        search_api_url = f"{BLIZZARD_API_BASE_URL}/data/wow/search/item"
                        search_results_data = make_blizzard_api_request_helper(api_url=search_api_url, params=search_params, headers=headers)
                        time.sleep(0.05)

                        if not search_results_data or not search_results_data.get("results"):
                            print(f"            WARNING: Item Search API returned no results for name '{item_name_from_recipe_detail}'. Skipping.", flush=True)
                            continue
                        
                        # Assuming the first result is the correct one
                        item_search_result_data = search_results_data["results"][0].get("data")
                        if not item_search_result_data or "id" not in item_search_result_data:
                            print(f"            WARNING: Item Search API result for '{item_name_from_recipe_detail}' is malformed or missing ID. Result: {item_search_result_data}", flush=True)
                            continue
                            
                        item_id_from_search = item_search_result_data.get("id")

                        if item_id_from_search in item_ids_handled_this_profession:
                            # print(f"          DEBUG: Item ID {item_id_from_search} (found for '{item_name_from_recipe_detail}') already handled in this profession run. Skipping.", flush=True)
                            continue
                        
                        existing_item_in_db = db_session.get(Item, item_id_from_search)
                        if existing_item_in_db and existing_item_in_db.icon_url:
                            # print(f"          DEBUG: Item ID {item_id_from_search} ('{existing_item_in_db.name}') already in DB with icon. Skipping full fetch.", flush=True)
                            item_ids_handled_this_profession.add(item_id_from_search)
                            continue
                            
                        # Fetch full item details using the ID obtained from the search
                        name_from_details, quality_from_details, slot_type_from_details, icon_url_from_details = \
                            get_full_item_details_by_id(item_id_from_search, headers, static_params_for_detail)
                        
                        item_ids_handled_this_profession.add(item_id_from_search) # Mark as handled

                        if not name_from_details or not slot_type_from_details:
                            print(f"            WARNING: Failed to get full details (name/slot) for item ID {item_id_from_search} ('{item_name_from_recipe_detail}'). Skipping.", flush=True)
                            continue

                        if quality_from_details in TARGET_ITEM_QUALITIES and slot_type_from_details in EQUIPPABLE_GEAR_SLOT_CATEGORIES:
                            if slot_type_from_details not in existing_playable_slot_types_set:
                                print(f"            CRITICAL: API slot '{slot_type_from_details}' for crafted item '{name_from_details}' (ID:{item_id_from_search}) missing in PlayableSlot table.", flush=True)
                                continue

                            if existing_item_in_db: # Existed but icon was missing or details needed update
                                if icon_url_from_details and not existing_item_in_db.icon_url:
                                    existing_item_in_db.icon_url = icon_url_from_details
                                # Ensure source is correct for crafted items
                                if existing_item_in_db.source_id != data_source_id or existing_item_in_db.source_details != prof_name:
                                    existing_item_in_db.source_id = data_source_id
                                    existing_item_in_db.source_details = prof_name
                                # Update other fields if necessary (e.g., if name from details is more accurate)
                                existing_item_in_db.name = name_from_details 
                                existing_item_in_db.quality = quality_from_details
                                existing_item_in_db.slot_type = slot_type_from_details
                                db_session.add(existing_item_in_db) 
                                total_crafted_items_processed_session +=1
                                print(f"            Updating existing crafted item ID {item_id_from_search}: {name_from_details}", flush=True)
                            else: # New item to add
                                new_item = Item(id=item_id_from_search, name=name_from_details, quality=quality_from_details, 
                                                 slot_type=slot_type_from_details, source_id=data_source_id, 
                                                 source_details=prof_name, icon_url=icon_url_from_details)
                                items_to_commit_for_this_tier.append(new_item)
                                total_crafted_items_processed_session += 1
                                print(f"            Adding new crafted item ID {item_id_from_search}: {name_from_details}", flush=True)
                
                if items_to_commit_for_this_tier: 
                    db_session.add_all(items_to_commit_for_this_tier)
            
            try: 
                db_session.commit() # Commit after each profession to manage transaction size
                print(f"  Committed items for profession {prof_name} (relevant tiers).", flush=True)
            except IntegrityError as ie:
                db_session.rollback()
                print(f"    DB Integrity Error for profession {prof_name}: {ie}. This might happen if an item was already added concurrently.", flush=True)
            except Exception as e:
                db_session.rollback()
                print(f"    Error committing crafted items for {prof_name}: {e}", flush=True)
            
    print(f"--- Finished processing Crafted Items. Total items processed (added or updated) in this session: {total_crafted_items_processed_session} ---", flush=True)


def fetch_and_store_single_item_from_api(db_session, item_id_to_fetch, existing_playable_slot_types_set, system_data_source_id):
    """
    Fetches details for a single item ID from Blizzard API and prepares an Item object.
    This function is primarily used by ensure_character_bis_items_in_db.
    Returns an Item object if successful and valid, otherwise None. Does NOT add to session.
    """
    # print(f"  Attempting to fetch details for missing BiS item ID: {item_id_to_fetch}", flush=True)
    access_token = get_blizzard_access_token()
    if not access_token:
        print(f"    ERROR: Could not get Blizzard access token for item ID {item_id_to_fetch}.", flush=True)
        return None
    
    headers = {"Authorization": f"Bearer {access_token}"}
    static_params = {"namespace": f"static-{REGION}", "locale": "en_US"}
    
    item_name, quality, slot_type, icon_url = get_full_item_details_by_id(item_id_to_fetch, headers, static_params)

    if not item_name or not slot_type:
        print(f"    WARNING: Item ID {item_id_to_fetch} missing critical data (name or slot_type) after fetch. Name: '{item_name}', Slot: '{slot_type}'. Skipping.", flush=True)
        return None

    if slot_type not in existing_playable_slot_types_set:
        print(f"    CRITICAL WARNING: API slot type '{slot_type}' for item ID {item_id_to_fetch} ('{item_name}') is not defined in PlayableSlot table. Item cannot be added. Please update populate_playable_slots().", flush=True)
        return None 

    print(f"    SUCCESS: Prepared details for item ID {item_id_to_fetch}: '{item_name}', Quality: {quality}, Slot: {slot_type}, Icon: {'Yes' if icon_url else 'No'}", flush=True)
    
    new_item = Item(
        id=item_id_to_fetch,
        name=item_name,
        quality=quality,
        icon_url=icon_url,
        slot_type=slot_type,
        source_id=system_data_source_id, 
        source_details="Added via CharacterBiS check" 
    )
    return new_item

def ensure_character_bis_items_in_db(db_session, existing_playable_slot_types_set, system_data_source_id):
    """
    Checks items in CharacterBiS table. If an item_id is not in the Item table,
    fetches its details from Blizzard API and adds it.
    """
    print("\n--- Ensuring CharacterBiS items are in Item Table ---", flush=True)
    
    if not system_data_source_id: # Check if system_data_source_id was passed correctly
        print("    ERROR: System Data Source ID not provided to ensure_character_bis_items_in_db. Cannot proceed.", flush=True)
        return

    try:
        bis_item_ids_query = db_session.query(CharacterBiS.item_id).filter(CharacterBiS.item_id != None).distinct()
        bis_item_ids = {row.item_id for row in bis_item_ids_query.all()}
        
        if not bis_item_ids:
            print("    No items found in CharacterBiS table to check.", flush=True)
            return

        print(f"    Found {len(bis_item_ids)} distinct item IDs in CharacterBiS table.", flush=True)

        existing_item_ids_query = db_session.query(Item.id).all()
        existing_item_ids_set = {row.id for row in existing_item_ids_query}
        # print(f"    Found {len(existing_item_ids_set)} item IDs in the main Item table.", flush=True) # Less verbose

        if not existing_playable_slot_types_set: # Ensure this was passed
            print("    CRITICAL ERROR: PlayableSlot types set not provided. Cannot validate item slot types.", flush=True)
            return

        items_to_add_to_db = []
        for bis_item_id in bis_item_ids:
            if bis_item_id not in existing_item_ids_set:
                # print(f"  Item ID {bis_item_id} from CharacterBiS is missing from Item table. Attempting to fetch...", flush=True)
                new_item_obj = fetch_and_store_single_item_from_api(db_session, bis_item_id, existing_playable_slot_types_set, system_data_source_id)
                if new_item_obj:
                    if not db_session.get(Item, new_item_obj.id): # Double check if added in this session already
                        items_to_add_to_db.append(new_item_obj)
                    # else:
                        # print(f"    INFO: Item ID {new_item_obj.id} was already added to session or DB. Skipping duplicate add.", flush=True)

        if items_to_add_to_db:
            print(f"    Adding {len(items_to_add_to_db)} missing BiS items to the Item table...", flush=True)
            db_session.add_all(items_to_add_to_db)
            db_session.commit() # Commit these specifically added BiS items
            print(f"    Successfully added {len(items_to_add_to_db)} BiS items.", flush=True)
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

    tables_to_ensure_for_this_script = [
        PlayableSlot.__table__, DataSource.__table__, Item.__table__, 
        Character.__table__, CharacterBiS.__table__ 
    ]
    Base.metadata.create_all(engine, tables=tables_to_ensure_for_this_script, checkfirst=True)
    print("DB tables for craft_tier_bis verified/created if they didn't exist.", flush=True)

    # Fetch Data Source IDs and PlayableSlot types needed by the functions
    crafting_source = db_session.query(DataSource).filter_by(name="Crafting - TWW S1").first()
    system_data_source_for_bis = db_session.query(DataSource).filter_by(name="Manually Added via BiS Check").first()
    
    playable_slot_types_query = db_session.query(PlayableSlot.type).all()
    valid_slot_types_set = {row.type for row in playable_slot_types_query}

    if not valid_slot_types_set:
        print("CRITICAL ERROR: PlayableSlot table is empty or types could not be fetched. This is required for item validation. Ensure wow_info.py's populate_playable_slots has run.", flush=True)
        db_session.close()
        return
        
    if crafting_source:
        fetch_and_store_crafted_items(db_session, crafting_source.id, valid_slot_types_set)
    else:
        print("Data source 'Crafting - TWW S1' not found. Cannot process crafted items. Ensure wow_info.py has run and populated DataSources.", flush=True)

    if system_data_source_for_bis:
        ensure_character_bis_items_in_db(db_session, valid_slot_types_set, system_data_source_for_bis.id)
    else:
        print("Data source 'Manually Added via BiS Check' not found. Cannot run BiS item check. Ensure wow_info.py has run and populated DataSources.", flush=True)


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
