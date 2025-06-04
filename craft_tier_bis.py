# craft_tier_bis.py
import os
import requests 
import time
from datetime import datetime
import json
import urllib.parse # For URL encoding item names
import sys
import traceback

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
    class_name = Column(String(50)) 
    spec_name = Column(String(50)) 


class CharacterBiS(Base): 
    __tablename__ = 'character_bis'
    id = Column(Integer, primary_key=True, autoincrement=True)
    character_id = Column(Integer, ForeignKey('character.id'), nullable=False, index=True)
    slot_type_ui = Column(String(50), ForeignKey('playable_slot.type'), nullable=False, index=True) 
    item_id = Column(Integer, ForeignKey('item.id'), nullable=True) 

class SuggestedBiS(Base): 
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
    __table_args__ = (UniqueConstraint('class_name', 'spec_name', 'ui_slot_type', 'item_name', name='_suggested_bis_uc'),)


# --- Helper function to fetch item details and icon by ID ---
def get_full_item_details_by_id(item_id, headers, static_params):
    """Fetches full item details and icon URL for a given item ID."""
    item_detail_url = f"{BLIZZARD_API_BASE_URL}/data/wow/item/{item_id}"
    item_data = make_blizzard_api_request_helper(api_url=item_detail_url, params=static_params, headers=headers)
    time.sleep(0.05) 

    if not item_data:
        return None, None, None, None 

    # Ensure name is extracted as en_US if it's a dict, otherwise use as is (assuming locale param worked)
    name_obj = item_data.get("name")
    if isinstance(name_obj, dict):
        item_name = name_obj.get("en_US")
    else: # Should be a string if locale worked
        item_name = name_obj

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

def find_journal_instance_id(instance_name_to_find, instance_type="instance"):
    """Queries the Blizzard API for the journal index and finds the ID for a given instance/dungeon name."""
    print(f"Attempting to find Journal ID for {instance_type}: '{instance_name_to_find}'", flush=True)
    
    access_token = get_blizzard_access_token()
    if not access_token:
        print(f"ERROR: Could not get access token for journal {instance_type} search", flush=True)
        return None
    
    headers = {"Authorization": f"Bearer {access_token}"}
    static_params = {"namespace": f"static-{REGION}", "locale": "en_US"}
    
    endpoint = f"/data/wow/journal-{instance_type}/index"
    index_data = make_blizzard_api_request_helper(
        api_url=f"{BLIZZARD_API_BASE_URL}{endpoint}", 
        params=static_params, 
        headers=headers
    )

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
        if index_data: 
            print(f"DEBUG: Journal {instance_type.capitalize()} Index Response: {json.dumps(index_data, indent=2)}", flush=True)
        return None

# --- Modified and Existing Functions ---

def fetch_and_store_crafted_items(db_session, data_source_id, existing_playable_slot_types_set):
    print("\n--- Processing Crafted Items (via Profession API) ---", flush=True)
    access_token = get_blizzard_access_token()
    if not access_token:
        print("  ERROR: Could not get Blizzard access token for crafted items. Aborting.", flush=True)
        return
    headers = {"Authorization": f"Bearer {access_token}"}
    static_params = {"namespace": f"static-{REGION}", "locale": "en_US"}

    target_professions = { 
        "Blacksmithing": 164, 
        "Leatherworking": 165, 
        "Tailoring": 197,
        "Jewelcrafting": 755, 
        "Engineering": 202 
    }

    processed_items = set()
    
    # Get the current expansion's skill tier (The War Within)
    expansion_name = "Khaz Algar"
    
    for prof_name, prof_id in target_professions.items():
        print(f"\n  Processing {prof_name} recipes...", flush=True)
        
        # Get profession details
        prof_detail_url = f"{BLIZZARD_API_BASE_URL}/data/wow/profession/{prof_id}"
        prof_data = make_blizzard_api_request_helper(api_url=prof_detail_url, params=static_params, headers=headers)
        time.sleep(0.05)
        
        if not prof_data or "skill_tiers" not in prof_data:
            print(f"    ERROR: Could not fetch profession data for {prof_name}", flush=True)
            continue
            
        # Find the Khaz Algar skill tier
        current_tier = None
        for tier in prof_data["skill_tiers"]:
            if expansion_name in tier.get("name", ""):
                current_tier = tier
                break
                
        if not current_tier:
            print(f"    ERROR: Could not find {expansion_name} tier for {prof_name}", flush=True)
            continue
            
        # Get detailed tier data
        tier_url = current_tier.get("key", {}).get("href")
        if not tier_url:
            continue
            
        tier_data = make_blizzard_api_request_helper(api_url=tier_url, params=static_params, headers=headers)
        time.sleep(0.05)
        
        if not tier_data or "categories" not in tier_data:
            continue
            
        # Process each category in the profession
        for category in tier_data["categories"]:
            category_name = category.get("name", "")
            if not any(keyword in category_name.lower() for keyword in ["armor", "weapon", "equipment", "gear"]):
                continue
                
            for recipe in category.get("recipes", []):
                recipe_url = recipe.get("key", {}).get("href")
                if not recipe_url:
                    continue
                    
                recipe_data = make_blizzard_api_request_helper(api_url=recipe_url, params=static_params, headers=headers)
                time.sleep(0.05)
                
                if not recipe_data or "crafted_item" not in recipe_data:
                    continue
                    
                crafted_item = recipe_data["crafted_item"]
                item_id = crafted_item.get("id")
                
                if not item_id or item_id in processed_items:
                    continue
                    
                # Get detailed item data
                item_url = f"{BLIZZARD_API_BASE_URL}/data/wow/item/{item_id}"
                item_data = make_blizzard_api_request_helper(api_url=item_url, params=static_params, headers=headers)
                time.sleep(0.05)
                
                if not item_data:
                    continue
                    
                name = item_data.get("name")
                quality = item_data.get("quality", {}).get("name", "Unknown").upper()
                slot_type = item_data.get("inventory_type", {}).get("type")
                
                # Only process epic and rare quality items that are equippable
                if not all([name, quality, slot_type]) or \
                   quality not in ["EPIC", "RARE"] or \
                   slot_type not in existing_playable_slot_types_set:
                    continue
                    
                # Get item icon
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
                                
                # Check if item already exists
                existing_item = db_session.get(Item, item_id)
                if existing_item:
                    if not existing_item.icon_url and icon_url:
                        existing_item.icon_url = icon_url
                        db_session.add(existing_item)
                else:
                    new_item = Item(
                        id=item_id,
                        name=name,
                        quality=quality,
                        slot_type=slot_type,
                        icon_url=icon_url,
                        source_id=data_source_id,
                        source_details=f"Crafted - {prof_name}"
                    )
                    db_session.add(new_item)
                    
                processed_items.add(item_id)
                print(f"    Added/Updated crafted item: {name} (ID: {item_id})", flush=True)
                
    if processed_items:
        db_session.commit()
        print(f"\nSuccessfully processed {len(processed_items)} crafted items", flush=True)
    else:
        print("\nNo crafted items were found or processed", flush=True)


def fetch_and_store_single_item_from_api(db_session, item_id_to_fetch, existing_playable_slot_types_set, system_data_source_id, item_name_for_log="Unknown"):
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

    # print(f"    SUCCESS: Prepared details for item ID {item_id_to_fetch}: '{name_from_details}', Quality: {quality_from_details}, Slot: {slot_type_from_details}, Icon: {'Yes' if icon_url_from_details else 'No'}", flush=True)
    
    new_item = Item(
        id=item_id_to_fetch,
        name=name_from_details, 
        quality=quality_from_details,
        icon_url=icon_url_from_details,
        slot_type=slot_type_from_details,
        source_id=system_data_source_id, 
        source_details="Added via System Check" 
    )
    return new_item

def ensure_data_integrity(db_session, existing_playable_slot_types_set, system_data_source_id):
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
        if not existing_item or not existing_item.icon_url: 
            item_obj = fetch_and_store_single_item_from_api(db_session, item_id, existing_playable_slot_types_set, system_data_source_id, item_name_for_log=f"CharBiS ID {item_id}")
            if item_obj:
                if not db_session.get(Item, item_obj.id):
                    items_to_add_or_update_in_item_table.append(item_obj)
                elif existing_item and not existing_item.icon_url and item_obj.icon_url: 
                    existing_item.icon_url = item_obj.icon_url
                    existing_item.name = item_obj.name 
                    existing_item.quality = item_obj.quality
                    existing_item.slot_type = item_obj.slot_type
                    items_to_add_or_update_in_item_table.append(existing_item) 
        processed_ids_from_charbis.add(item_id)

    # 2. Check SuggestedBiS
    print("\n  Checking items from SuggestedBiS...", flush=True)
    suggested_items_query = db_session.query(SuggestedBiS.id, SuggestedBiS.item_name, SuggestedBiS.blizzard_item_id).all()
    
    access_token = get_blizzard_access_token() 
    if not access_token:
        print("    ERROR: Could not get Blizzard access token for SuggestedBiS processing. Aborting this section.", flush=True)
    else:
        headers = {"Authorization": f"Bearer {access_token}"}
        static_params_for_detail = {"namespace": f"static-{REGION}", "locale": "en_US"}
        SEARCH_NAME_LOCALE_KEY = "name.en_US"

        for sug_bis_id, sug_item_name, sug_blizz_id_from_sug_table in suggested_items_query:
            sug_item_name_stripped = sug_item_name.strip() if sug_item_name else None
            if not sug_item_name_stripped: continue

            found_item_in_db = None
            if sug_blizz_id_from_sug_table:
                found_item_in_db = db_session.get(Item, sug_blizz_id_from_sug_table)
                if found_item_in_db and not found_item_in_db.icon_url: 
                    _, _, _, icon_url = get_full_item_details_by_id(sug_blizz_id_from_sug_table, headers, static_params_for_detail)
                    if icon_url and not found_item_in_db.icon_url:
                        found_item_in_db.icon_url = icon_url
                        items_to_add_or_update_in_item_table.append(found_item_in_db)
                elif not found_item_in_db : 
                     item_obj = fetch_and_store_single_item_from_api(db_session, sug_blizz_id_from_sug_table, existing_playable_slot_types_set, system_data_source_id, item_name_for_log=sug_item_name_stripped)
                     if item_obj:
                         if not db_session.get(Item, item_obj.id):
                            items_to_add_or_update_in_item_table.append(item_obj)
                         found_item_in_db = item_obj 

            if not found_item_in_db:
                item_from_db_by_name = db_session.query(Item).filter(func.lower(Item.name) == func.lower(sug_item_name_stripped)).first()

                if item_from_db_by_name:
                    found_item_in_db = item_from_db_by_name
                    if not found_item_in_db.icon_url: 
                         _, _, _, icon_url = get_full_item_details_by_id(found_item_in_db.id, headers, static_params_for_detail)
                         if icon_url and not found_item_in_db.icon_url:
                            found_item_in_db.icon_url = icon_url
                            items_to_add_or_update_in_item_table.append(found_item_in_db)
                else:
                    # print(f"      Item '{sug_item_name_stripped}' not in DB by ID or Name. Searching Blizzard API...", flush=True) # Less verbose
                    search_params = {
                        "namespace": f"static-{REGION}", "locale": "en_US",
                        SEARCH_NAME_LOCALE_KEY: sug_item_name_stripped,
                        "orderby": "id", "_page": 1, "_pageSize": 5 
                    }
                    search_api_url = f"{BLIZZARD_API_BASE_URL}/data/wow/search/item"
                    search_results_data = make_blizzard_api_request_helper(api_url=search_api_url, params=search_params, headers=headers)
                    time.sleep(0.05)

                    exact_match_api_data = None
                    if search_results_data and search_results_data.get("results"):
                        # print(f"        DEBUG: Item Search API for '{sug_item_name_stripped}' (SuggestedBiS) returned {len(search_results_data['results'])} result(s):", flush=True)
                        # for i, result_entry_debug in enumerate(search_results_data["results"]):
                        #     api_item_data_candidate_debug = result_entry_debug.get("data")
                        #     if api_item_data_candidate_debug:
                        #         api_item_name_obj_debug = api_item_data_candidate_debug.get("name", {})
                        #         api_item_name_en_us_debug = api_item_name_obj_debug.get("en_US", "N/A").strip()
                        #         api_item_id_debug = api_item_data_candidate_debug.get("id", "N/A")
                        #         print(f"          Result {i+1}: ID={api_item_id_debug}, API Name='{api_item_name_en_us_debug}'", flush=True)
                        #     else:
                        #         print(f"          Result {i+1}: Malformed data entry.", flush=True)

                        for result_entry in search_results_data["results"]:
                            api_item_data_candidate = result_entry.get("data")
                            if api_item_data_candidate:
                                api_item_name_obj = api_item_data_candidate.get("name", {})
                                api_item_name_en_us = api_item_name_obj.get("en_US", "").strip()
                                if api_item_name_en_us.lower() == sug_item_name_stripped.lower():
                                    exact_match_api_data = api_item_data_candidate
                                    # print(f"        EXACT MATCH FOUND via API Search for '{sug_item_name_stripped}' -> API Name: '{api_item_name_en_us}', ID: {exact_match_api_data.get('id')}", flush=True)
                                    break
                    
                    if exact_match_api_data:
                        item_id_from_search = exact_match_api_data.get("id")
                        item_already_exists_with_searched_id = db_session.get(Item, item_id_from_search)
                        if item_already_exists_with_searched_id:
                            found_item_in_db = item_already_exists_with_searched_id
                            if not found_item_in_db.icon_url: 
                                _, _, _, icon_url = get_full_item_details_by_id(found_item_in_db.id, headers, static_params_for_detail)
                                if icon_url and not found_item_in_db.icon_url:
                                    found_item_in_db.icon_url = icon_url
                                    items_to_add_or_update_in_item_table.append(found_item_in_db)
                        else: 
                            item_obj = fetch_and_store_single_item_from_api(db_session, item_id_from_search, existing_playable_slot_types_set, system_data_source_id, item_name_for_log=sug_item_name_stripped)
                            if item_obj:
                                if not db_session.get(Item, item_obj.id):
                                    items_to_add_or_update_in_item_table.append(item_obj)
                                found_item_in_db = item_obj 
            
            if found_item_in_db and found_item_in_db.id != sug_blizz_id_from_sug_table :
                sug_bis_entry_to_update = db_session.get(SuggestedBiS, sug_bis_id)
                if sug_bis_entry_to_update:
                    # print(f"      Updating SuggestedBiS entry for '{sug_item_name_stripped}' with correct Blizzard ID: {found_item_in_db.id} (was {sug_blizz_id_from_sug_table})", flush=True)
                    sug_bis_entry_to_update.blizzard_item_id = found_item_in_db.id
                    db_session.add(sug_bis_entry_to_update) 

    if items_to_add_or_update_in_item_table:
        print(f"    Adding/Updating {len(items_to_add_or_update_in_item_table)} items in the Item table from integrity check...", flush=True)
        for item_to_proc in items_to_add_or_update_in_item_table:
            db_session.add(item_to_proc) 
    
    try:
        db_session.commit() 
        print("    Data integrity checks committed.", flush=True)
    except Exception as e:
        db_session.rollback()
        print(f"    ERROR during data integrity commit: {e}", flush=True)
        traceback.print_exc()
    
    print("--- Finished Data Integrity Check ---", flush=True)

def fetch_and_store_tier_items(db_session, data_source_id, existing_playable_slot_types_set):
    print("\n--- Processing Tier Set Items ---", flush=True)
    access_token = get_blizzard_access_token()
    if not access_token:
        print("  ERROR: Could not get Blizzard access token for tier items. Aborting.", flush=True)
        return
    
    headers = {"Authorization": f"Bearer {access_token}"}
    static_params = {"namespace": f"static-{REGION}", "locale": "en_US"}
    
    # First, get the current raid instance (The War Within)
    instance_name = "Liberation of Undermine"
    instance_id = find_journal_instance_id(instance_name, "instance")
    
    if not instance_id:
        print(f"  ERROR: Could not find journal ID for {instance_name}", flush=True)
        return
    
    # Get the instance data
    instance_api_url = f"{BLIZZARD_API_BASE_URL}/data/wow/journal-instance/{instance_id}"
    instance_data = make_blizzard_api_request_helper(api_url=instance_api_url, params=static_params, headers=headers)
    
    if not instance_data or "encounters" not in instance_data:
        print(f"  ERROR: Could not fetch instance data for {instance_name}", flush=True)
        return
    
    # Set to track processed tier items
    processed_tier_items = set()
    
    # Process each encounter
    for encounter in instance_data["encounters"]:
        encounter_id = encounter.get("id")
        if not encounter_id:
            continue
            
        # Get detailed encounter data
        encounter_url = f"{BLIZZARD_API_BASE_URL}/data/wow/journal-encounter/{encounter_id}"
        encounter_data = make_blizzard_api_request_helper(api_url=encounter_url, params=static_params, headers=headers)
        time.sleep(0.05)
        
        if not encounter_data or "items" not in encounter_data:
            continue
            
        # Process items from the encounter
        for item in encounter_data["items"]:
            item_id = item.get("item", {}).get("id")
            if not item_id or item_id in processed_tier_items:
                continue
                
            # Get detailed item data
            item_url = f"{BLIZZARD_API_BASE_URL}/data/wow/item/{item_id}"
            item_data = make_blizzard_api_request_helper(api_url=item_url, params=static_params, headers=headers)
            time.sleep(0.05)
            
            if not item_data:
                continue
                
            # Check if it's a tier item by looking for set bonus information
            if "preview_item" in item_data and "set" in item_data["preview_item"]:
                name = item_data.get("name")
                quality = item_data.get("quality", {}).get("name", "Unknown").upper()
                slot_type = item_data.get("inventory_type", {}).get("type")
                
                if not all([name, quality, slot_type]) or slot_type not in existing_playable_slot_types_set:
                    continue
                    
                # Get item icon
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
                
                # Check if item already exists
                existing_item = db_session.get(Item, item_id)
                if existing_item:
                    if not existing_item.icon_url and icon_url:
                        existing_item.icon_url = icon_url
                        db_session.add(existing_item)
                else:
                    new_item = Item(
                        id=item_id,
                        name=name,
                        quality=quality,
                        slot_type=slot_type,
                        icon_url=icon_url,
                        source_id=data_source_id,
                        source_details=f"{instance_name} - Tier Set"
                    )
                    db_session.add(new_item)
                
                processed_tier_items.add(item_id)
                print(f"  Added/Updated tier item: {name} (ID: {item_id})", flush=True)
    
    if processed_tier_items:
        db_session.commit()
        print(f"  Successfully processed {len(processed_tier_items)} tier set items", flush=True)
    else:
        print("  No tier set items were found or processed", flush=True)

# --- MAIN EXECUTION for craft_tier_bis.py ---
def main():
    print("Starting Item Database Update Process...", flush=True)
    
    db_session = SessionLocal()
    if not db_session:
        print("Failed to get database session. Exiting.", flush=True)
        sys.exit(1)
        
    try:
        # Get existing playable slot types for validation
        existing_slot_types = set(slot.type for slot in db_session.query(PlayableSlot).all())
        if not existing_slot_types:
            print("ERROR: No playable slots found in database. Please run database initialization first.", flush=True)
            sys.exit(1)
            
        # Get or create the system data source
        system_source = db_session.query(DataSource).filter_by(name="System").first()
        if not system_source:
            system_source = DataSource(name="System", description="Items added by system processes")
            db_session.add(system_source)
            db_session.commit()
            
        # Process tier set items
        fetch_and_store_tier_items(db_session, system_source.id, existing_slot_types)
        
        # Process crafted items
        fetch_and_store_crafted_items(db_session, system_source.id, existing_slot_types)
        
        # Ensure data integrity
        ensure_data_integrity(db_session, existing_slot_types, system_source.id)
        
        print("\nItem Database Update Process completed successfully!", flush=True)
        
    except Exception as e:
        print(f"Error during database update process: {e}", flush=True)
        traceback.print_exc()
    finally:
        db_session.close()

if __name__ == "__main__":
    required_env_vars = ['BLIZZARD_CLIENT_ID', 'BLIZZARD_CLIENT_SECRET', 'DATABASE_URL', 'REGION']
    missing_vars = [var for var in required_env_vars if not os.environ.get(var)]
    if missing_vars:
        print(f"FATAL: Missing required environment variables for craft_tier_bis.py: {', '.join(missing_vars)}", flush=True)
        exit(1)
    
    print("All required environment variables for craft_tier_bis.py found.", flush=True)
    main()
