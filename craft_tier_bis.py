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
from sqlalchemy.sql import func, and_
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

# --- Database Models ---
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
    # Add other fields if needed for deriving class/spec for SuggestedBiS context
    class_name = Column(String(50)) 
    spec_name = Column(String(50)) # Assuming this might be the active or intended spec


class CharacterBiS(Base): 
    __tablename__ = 'character_bis'
    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(Integer, ForeignKey('character.id'), nullable=False, index=True)
    slot_type_ui = Column(String(50), ForeignKey('playable_slot.type'), nullable=False, index=True) 
    item_id = Column(Integer, ForeignKey('item.id'), nullable=True) 

class SuggestedBiS(Base): # Added model definition
    __tablename__ = 'suggested_bis'
    id = Column(Integer, primary_key=True, autoincrement=True)
    class_name = Column(String(50), nullable=False, index=True)
    spec_name = Column(String(50), nullable=False, index=True)
    ui_slot_type = Column(String(50), ForeignKey('playable_slot.type'), nullable=False, index=True) 
    item_name = Column(String(255), nullable=False)
    blizzard_item_id = Column(Integer, ForeignKey('item.id'), nullable=True, index=True) 
    wowhead_item_id = Column(String(50), nullable=True) 
    item_source = Column(String(255), nullable=True) 
    last_scraped = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow) 

    # Relationships (optional here, but good for completeness if ever queried with joins)
    # slot_relation = relationship("PlayableSlot", foreign_keys=[ui_slot_type])
    # item_relation = relationship("Item", foreign_keys=[blizzard_item_id])
    __table_args__ = (UniqueConstraint('class_name', 'spec_name', 'ui_slot_type', 'item_name', name='_suggested_bis_uc'),)


# --- Helper function to fetch item details and icon by ID ---
def get_full_item_details_by_id(item_id, headers, static_params):
    """Fetches full item details and icon URL for a given item ID."""
    item_detail_url = f"{BLIZZARD_API_BASE_URL}/data/wow/item/{item_id}"
    item_data = make_blizzard_api_request_helper(api_url=item_detail_url, params=static_params, headers=headers)
    time.sleep(0.05) 

    if not item_data:
        return None, None, None, None 

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
    static_params_for_detail = {"namespace": f"static-{REGION}", "locale": "en_US"}

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
    SEARCH_NAME_LOCALE_KEY = "name.en_US" 

    total_crafted_items_processed_session = 0 

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
            
            item_ids_handled_this_profession = set() 

            for skill_tier_summary in prof_detail_data["skill_tiers"]:
                skill_tier_name = skill_tier_summary.get("name", "")
                if CURRENT_EXPANSION_KEYWORD.lower() not in skill_tier_name.lower():
                    continue 
                
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
                        recipe_name_from_ref = recipe_ref.get("name", f"Recipe ID {recipe_id_from_ref}") 
                        
                        if not recipe_id_from_ref: continue

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
                            continue
                        
                        item_name_from_recipe_detail = crafted_item_info.get("name")
                        if not item_name_from_recipe_detail:
                            continue

                        print(f"          Processing recipe for item name: '{item_name_from_recipe_detail}' (from recipe ID {recipe_id_from_ref})", flush=True)

                        search_params = {
                            "namespace": f"static-{REGION}", 
                            "locale": "en_US",             
                            SEARCH_NAME_LOCALE_KEY: item_name_from_recipe_detail, 
                            "orderby": "id",               
                            "_page": 1,
                            "_pageSize": 1                 
                        }
                        search_api_url = f"{BLIZZARD_API_BASE_URL}/data/wow/search/item"
                        search_results_data = make_blizzard_api_request_helper(api_url=search_api_url, params=search_params, headers=headers)
                        time.sleep(0.05)

                        if not search_results_data or not search_results_data.get("results"):
                            print(f"            WARNING: Item Search API returned no results for name '{item_name_from_recipe_detail}'. Skipping.", flush=True)
                            continue
                        
                        item_search_result_data = search_results_data["results"][0].get("data")
                        if not item_search_result_data or "id" not in item_search_result_data:
                            print(f"            WARNING: Item Search API result for '{item_name_from_recipe_detail}' is malformed or missing ID. Result: {item_search_result_data}", flush=True)
                            continue
                            
                        item_id_from_search = item_search_result_data.get("id")

                        if item_id_from_search in item_ids_handled_this_profession:
                            continue
                        
                        existing_item_in_db = db_session.get(Item, item_id_from_search)
                        if existing_item_in_db and existing_item_in_db.icon_url:
                            item_ids_handled_this_profession.add(item_id_from_search)
                            continue
                            
                        name_from_details, quality_from_details, slot_type_from_details, icon_url_from_details = \
                            get_full_item_details_by_id(item_id_from_search, headers, static_params_for_detail)
                        
                        item_ids_handled_this_profession.add(item_id_from_search) 

                        if not name_from_details or not slot_type_from_details:
                            print(f"            WARNING: Failed to get full details (name/slot) for item ID {item_id_from_search} ('{item_name_from_recipe_detail}'). Skipping.", flush=True)
                            continue

                        if quality_from_details in TARGET_ITEM_QUALITIES and slot_type_from_details in EQUIPPABLE_GEAR_SLOT_CATEGORIES:
                            if slot_type_from_details not in existing_playable_slot_types_set:
                                print(f"            CRITICAL: API slot '{slot_type_from_details}' for crafted item '{name_from_details}' (ID:{item_id_from_search}) missing in PlayableSlot table.", flush=True)
                                continue

                            if existing_item_in_db: 
                                if icon_url_from_details and not existing_item_in_db.icon_url:
                                    existing_item_in_db.icon_url = icon_url_from_details
                                if existing_item_in_db.source_id != data_source_id or existing_item_in_db.source_details != prof_name:
                                    existing_item_in_db.source_id = data_source_id
                                    existing_item_in_db.source_details = prof_name
                                existing_item_in_db.name = name_from_details 
                                existing_item_in_db.quality = quality_from_details
                                existing_item_in_db.slot_type = slot_type_from_details
                                db_session.add(existing_item_in_db) 
                                total_crafted_items_processed_session +=1
                                print(f"            Updating existing crafted item ID {item_id_from_search}: {name_from_details}", flush=True)
                            else: 
                                new_item = Item(id=item_id_from_search, name=name_from_details, quality=quality_from_details, 
                                                 slot_type=slot_type_from_details, source_id=data_source_id, 
                                                 source_details=prof_name, icon_url=icon_url_from_details)
                                items_to_commit_for_this_tier.append(new_item)
                                total_crafted_items_processed_session += 1
                                print(f"            Adding new crafted item ID {item_id_from_search}: {name_from_details}", flush=True)
                
                if items_to_commit_for_this_tier: 
                    db_session.add_all(items_to_commit_for_this_tier)
            
            try: 
                db_session.commit() 
                print(f"  Committed items for profession {prof_name} (relevant tiers).", flush=True)
            except IntegrityError as ie:
                db_session.rollback()
                print(f"    DB Integrity Error for profession {prof_name}: {ie}. This might happen if an item was already added concurrently.", flush=True)
            except Exception as e:
                db_session.rollback()
                print(f"    Error committing crafted items for {prof_name}: {e}", flush=True)
            
    print(f"--- Finished processing Crafted Items. Total items processed (added or updated) in this session: {total_crafted_items_processed_session} ---", flush=True)


def fetch_and_store_single_item_from_api(db_session, item_id_to_fetch, existing_playable_slot_types_set, system_data_source_id, item_name_for_log="Unknown"):
    """
    Fetches details for a single item ID from Blizzard API and prepares an Item object.
    Returns an Item object if successful and valid, otherwise None. Does NOT add to session directly.
    """
    access_token = get_blizzard_access_token()
    if not access_token:
        print(f"    ERROR: Could not get Blizzard access token for item ID {item_id_to_fetch}.", flush=True)
        return None
    
    headers = {"Authorization": f"Bearer {access_token}"}
    static_params = {"namespace": f"static-{REGION}", "locale": "en_US"}
    
    name_from_details, quality_from_details, slot_type_from_details, icon_url_from_details = \
        get_full_item_details_by_id(item_id_to_fetch, headers, static_params)

    if not name_from_details or not slot_type_from_details:
        print(f"    WARNING: Item ID {item_id_to_fetch} (Original Name if known: '{item_name_for_log}') missing critical data (name or slot_type) after fetch. Name: '{name_from_details}', Slot: '{slot_type_from_details}'. Skipping.", flush=True)
        return None

    if slot_type_from_details not in existing_playable_slot_types_set:
        print(f"    CRITICAL WARNING: API slot type '{slot_type_from_details}' for item ID {item_id_to_fetch} ('{name_from_details}') is not defined in PlayableSlot table. Item cannot be added.", flush=True)
        return None 

    print(f"    SUCCESS: Prepared details for item ID {item_id_to_fetch}: '{name_from_details}', Quality: {quality_from_details}, Slot: {slot_type_from_details}, Icon: {'Yes' if icon_url_from_details else 'No'}", flush=True)
    
    new_item = Item(
        id=item_id_to_fetch,
        name=name_from_details, # Use name from API details
        quality=quality_from_details,
        icon_url=icon_url_from_details,
        slot_type=slot_type_from_details,
        source_id=system_data_source_id, 
        source_details="Added via System Check" # Generic source for items added this way
    )
    return new_item

def ensure_data_integrity(db_session, existing_playable_slot_types_set, system_data_source_id):
    """
    Ensures items referenced in CharacterBiS and SuggestedBiS are in the Item table.
    """
    print("\n--- Ensuring Data Integrity for CharacterBiS and SuggestedBiS items ---", flush=True)
    
    if not system_data_source_id:
        print("    ERROR: System Data Source ID not provided. Cannot proceed.", flush=True)
        return
    if not existing_playable_slot_types_set:
        print("    CRITICAL ERROR: PlayableSlot types set not provided. Cannot validate item slot types.", flush=True)
        return

    items_to_add_or_update_in_item_table = []
    
    # 1. Check CharacterBiS
    print("  Checking items from CharacterBiS...", flush=True)
    bis_item_ids_from_charbis_query = db_session.query(CharacterBiS.item_id).filter(CharacterBiS.item_id != None).distinct()
    bis_item_ids_from_charbis = {row.item_id for row in bis_item_ids_from_charbis_query.all()}
    
    processed_ids_from_charbis = set()

    for item_id in bis_item_ids_from_charbis:
        if item_id in processed_ids_from_charbis: continue
        existing_item = db_session.get(Item, item_id)
        if not existing_item or not existing_item.icon_url: # If item doesn't exist or lacks icon
            # print(f"    Item ID {item_id} from CharacterBiS needs check/fetch for Item table.", flush=True)
            item_obj = fetch_and_store_single_item_from_api(db_session, item_id, existing_playable_slot_types_set, system_data_source_id, item_name_for_log=f"CharBiS ID {item_id}")
            if item_obj:
                # Check again if it was added by another process or already in session
                if not db_session.get(Item, item_obj.id):
                    items_to_add_or_update_in_item_table.append(item_obj)
                elif existing_item and not existing_item.icon_url and item_obj.icon_url: # Update existing if icon was fetched
                    existing_item.icon_url = item_obj.icon_url
                    existing_item.name = item_obj.name # Ensure name is also up-to-date
                    existing_item.quality = item_obj.quality
                    existing_item.slot_type = item_obj.slot_type
                    items_to_add_or_update_in_item_table.append(existing_item) # Add to list for commit
        processed_ids_from_charbis.add(item_id)

    # 2. Check SuggestedBiS
    print("\n  Checking items from SuggestedBiS...", flush=True)
    # Query for item_name and blizzard_item_id (which might be null)
    suggested_items_query = db_session.query(SuggestedBiS.id, SuggestedBiS.item_name, SuggestedBiS.blizzard_item_id).all()
    
    access_token = get_blizzard_access_token() # Get token once for this section
    if not access_token:
        print("    ERROR: Could not get Blizzard access token for SuggestedBiS processing. Aborting this section.", flush=True)
    else:
        headers = {"Authorization": f"Bearer {access_token}"}
        static_params_for_detail = {"namespace": f"static-{REGION}", "locale": "en_US"}
        SEARCH_NAME_LOCALE_KEY = "name.en_US"

        for sug_bis_id, sug_item_name, sug_blizz_id in suggested_items_query:
            sug_item_name_stripped = sug_item_name.strip() if sug_item_name else None
            if not sug_item_name_stripped: continue

            found_item_in_db = None
            # Try finding by ID first if available in SuggestedBiS
            if sug_blizz_id:
                found_item_in_db = db_session.get(Item, sug_blizz_id)
                if found_item_in_db and not found_item_in_db.icon_url: # Exists but needs icon
                    # print(f"    SuggestedBiS item '{sug_item_name_stripped}' (ID: {sug_blizz_id}) found in Item table, checking icon.", flush=True)
                    _, _, _, icon_url = get_full_item_details_by_id(sug_blizz_id, headers, static_params_for_detail)
                    if icon_url and not found_item_in_db.icon_url:
                        found_item_in_db.icon_url = icon_url
                        items_to_add_or_update_in_item_table.append(found_item_in_db)
                elif not found_item_in_db : # ID from SuggestedBiS not in Item table, fetch by this ID
                     item_obj = fetch_and_store_single_item_from_api(db_session, sug_blizz_id, existing_playable_slot_types_set, system_data_source_id, item_name_for_log=sug_item_name_stripped)
                     if item_obj:
                         if not db_session.get(Item, item_obj.id):
                            items_to_add_or_update_in_item_table.append(item_obj)
                         found_item_in_db = item_obj # So that sug_blizz_id can be updated if it was wrong

            # If not found by ID, or ID was null, try by name
            if not found_item_in_db:
                # print(f"    SuggestedBiS item '{sug_item_name_stripped}' (ID: {sug_blizz_id if sug_blizz_id else 'N/A'}) - trying to find/create by name.", flush=True)
                # Query Item table by name (case-insensitive exact match after stripping whitespace)
                # This requires Item.name to be indexed or can be slow on large tables.
                # For exact match, direct filter is fine.
                item_from_db_by_name = db_session.query(Item).filter(func.lower(Item.name) == func.lower(sug_item_name_stripped)).first()

                if item_from_db_by_name:
                    # print(f"      Found '{sug_item_name_stripped}' in Item table by name (ID: {item_from_db_by_name.id}).", flush=True)
                    found_item_in_db = item_from_db_by_name
                    if not found_item_in_db.icon_url: # Check icon if found by name
                         _, _, _, icon_url = get_full_item_details_by_id(found_item_in_db.id, headers, static_params_for_detail)
                         if icon_url and not found_item_in_db.icon_url:
                            found_item_in_db.icon_url = icon_url
                            items_to_add_or_update_in_item_table.append(found_item_in_db)
                else:
                    # Not in DB by name, try Blizzard Search API by name
                    print(f"      Item '{sug_item_name_stripped}' not in DB by name. Searching Blizzard API...", flush=True)
                    search_params = {
                        "namespace": f"static-{REGION}", "locale": "en_US",
                        SEARCH_NAME_LOCALE_KEY: sug_item_name_stripped,
                        "orderby": "id", "_page": 1, "_pageSize": 1
                    }
                    search_api_url = f"{BLIZZARD_API_BASE_URL}/data/wow/search/item"
                    search_results_data = make_blizzard_api_request_helper(api_url=search_api_url, params=search_params, headers=headers)
                    time.sleep(0.05)

                    if search_results_data and search_results_data.get("results"):
                        item_search_result = search_results_data["results"][0].get("data")
                        if item_search_result and "id" in item_search_result:
                            item_id_from_search = item_search_result.get("id")
                            # print(f"        Found item via API search: '{item_search_result.get('name', {}).get('en_US')}' (ID: {item_id_from_search}). Fetching full details.", flush=True)
                            
                            # Check if this searched ID is already in DB (e.g. different name casing)
                            item_already_exists_with_searched_id = db_session.get(Item, item_id_from_search)
                            if item_already_exists_with_searched_id:
                                found_item_in_db = item_already_exists_with_searched_id
                                if not found_item_in_db.icon_url: # Check icon
                                    _, _, _, icon_url = get_full_item_details_by_id(found_item_in_db.id, headers, static_params_for_detail)
                                    if icon_url and not found_item_in_db.icon_url:
                                        found_item_in_db.icon_url = icon_url
                                        items_to_add_or_update_in_item_table.append(found_item_in_db)
                            else: # Add new item from search result
                                item_obj = fetch_and_store_single_item_from_api(db_session, item_id_from_search, existing_playable_slot_types_set, system_data_source_id, item_name_for_log=sug_item_name_stripped)
                                if item_obj:
                                    if not db_session.get(Item, item_obj.id):
                                        items_to_add_or_update_in_item_table.append(item_obj)
                                    found_item_in_db = item_obj # Use this for updating SuggestedBiS
                        # else:
                            # print(f"        API Search for '{sug_item_name_stripped}' result malformed or no ID.", flush=True)
                    # else:
                        # print(f"        API Search for '{sug_item_name_stripped}' yielded no results.", flush=True)
            
            # Update SuggestedBiS.blizzard_item_id if a valid ID was found/confirmed
            if found_item_in_db and found_item_in_db.id != sug_blizz_id :
                sug_bis_entry_to_update = db_session.get(SuggestedBiS, sug_bis_id)
                if sug_bis_entry_to_update:
                    # print(f"      Updating SuggestedBiS entry for '{sug_item_name_stripped}' with correct Blizzard ID: {found_item_in_db.id} (was {sug_blizz_id})", flush=True)
                    sug_bis_entry_to_update.blizzard_item_id = found_item_in_db.id
                    # No need to add to items_to_add_or_update_in_item_table, just commit session later.
                    db_session.add(sug_bis_entry_to_update) # Ensure it's marked for update

    if items_to_add_or_update_in_item_table:
        print(f"    Adding/Updating {len(items_to_add_or_update_in_item_table)} items in the Item table from integrity check...", flush=True)
        db_session.add_all(items_to_add_or_update_in_item_table)
    
    try:
        db_session.commit() # Commit all changes from CharacterBiS and SuggestedBiS checks
        print("    Data integrity checks committed.", flush=True)
    except Exception as e:
        db_session.rollback()
        print(f"    ERROR during data integrity commit: {e}", flush=True)
        import traceback
        traceback.print_exc()
    
    print("--- Finished Data Integrity Check ---", flush=True)


# --- MAIN EXECUTION for craft_tier_bis.py ---
def main():
    print("Starting Crafted Item and BiS Item Check Script...", flush=True)
    db_session = SessionLocal()

    tables_to_ensure_for_this_script = [
        PlayableSlot.__table__, DataSource.__table__, Item.__table__, 
        Character.__table__, CharacterBiS.__table__, SuggestedBiS.__table__ # Added SuggestedBiS
    ]
    Base.metadata.create_all(engine, tables=tables_to_ensure_for_this_script, checkfirst=True)
    print("DB tables for craft_tier_bis verified/created if they didn't exist.", flush=True)

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

    # Renamed function and passed necessary parameters
    if system_data_source_for_bis:
        ensure_data_integrity(db_session, valid_slot_types_set, system_data_source_for_bis.id)
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
