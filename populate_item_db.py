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

# --- Models (Referencing item_database_models_with_bis) ---
class PlayableSlot(Base):
    __tablename__ = 'playable_slot'
    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    display_order = Column(Integer, default=0)
    items = relationship("Item", back_populates="slot")
    def __repr__(self): return f'<PlayableSlot {self.name} ({self.type})>'

class DataSource(Base):
    __tablename__ = 'data_source'
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), unique=True, nullable=False)
    type = Column(String(50)) # "Raid", "Dungeon"
    items = relationship("Item", back_populates="source")
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
    def __repr__(self): return f'<Item {self.name} (ID: {self.id})>'

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
    
    # Ensure params is a dictionary
    if params is None:
        params = {}
    
    # Add default namespace if not a full URL request and not already present
    if not full_url and "namespace" not in params:
         params["namespace"] = f"static-{REGION}" # Default to static for journal/item data
    if not full_url and "locale" not in params:
        params["locale"] = "en_US"

    for attempt in range(max_retries):
        try:
            # print(f"DEBUG: Requesting URL: {api_url} with params: {params}", flush=True)
            response = requests.get(api_url, params=params, headers=headers, timeout=30)
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
                print(f"HTTP Error for {api_url}: {e}", flush=True); return None
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
        {"type": "HEAD", "name": "Head", "display_order": 1}, {"type": "NECK", "name": "Neck", "display_order": 2},
        {"type": "SHOULDER", "name": "Shoulder", "display_order": 3}, {"type": "BACK", "name": "Back", "display_order": 4},
        {"type": "CHEST", "name": "Chest", "display_order": 5}, {"type": "SHIRT", "name": "Shirt", "display_order": 6},
        {"type": "TABARD", "name": "Tabard", "display_order": 7}, {"type": "WRIST", "name": "Wrist", "display_order": 8},
        {"type": "HANDS", "name": "Hands", "display_order": 9}, {"type": "WAIST", "name": "Waist", "display_order": 10},
        {"type": "LEGS", "name": "Legs", "display_order": 11}, {"type": "FEET", "name": "Feet", "display_order": 12},
        {"type": "FINGER1", "name": "Finger 1", "display_order": 13}, {"type": "FINGER2", "name": "Finger 2", "display_order": 14},
        {"type": "TRINKET1", "name": "Trinket 1", "display_order": 15}, {"type": "TRINKET2", "name": "Trinket 2", "display_order": 16},
        {"type": "WEAPONMAINHAND", "name": "Main Hand", "display_order": 17},
        {"type": "WEAPONOFFHAND", "name": "Off Hand", "display_order": 18},
        {"type": "RANGED", "name": "Ranged/Relic", "display_order": 19}
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
        {"name": "Mythic+ Season 2", "type": "Dungeon"}
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


def fetch_and_store_raid_items(db_session, raid_name, raid_journal_id, data_source_id):
    """Fetches items for a given raid and stores them."""
    print(f"Fetching items for raid: {raid_name} (Journal ID: {raid_journal_id})", flush=True)

    instance_data = make_blizzard_api_request(f"/data/wow/journal-instance/{raid_journal_id}")
    if not instance_data or "encounters" not in instance_data:
        print(f"Error: Could not fetch instance data or encounters for raid ID {raid_journal_id}", flush=True)
        return

    items_added_count = 0
    for encounter_ref in instance_data["encounters"]:
        encounter_id = encounter_ref["id"]
        encounter_name = encounter_ref["name"]
        print(f"  Fetching loot for encounter: {encounter_name} (ID: {encounter_id})", flush=True)

        encounter_detail_data = make_blizzard_api_request(f"/data/wow/journal-encounter/{encounter_id}")
        if not encounter_detail_data or "items" not in encounter_detail_data:
            print(f"    Warning: No 'items' section found for encounter {encounter_name} (ID: {encounter_id})", flush=True)
            continue
        
        items_to_process = encounter_detail_data["items"]

        for item_entry in items_to_process:
            item_ref = item_entry.get("item")
            if not item_ref or "id" not in item_ref:
                continue
            
            item_id = item_ref["id"]
            item_detail_data = make_blizzard_api_request(f"/data/wow/item/{item_id}")
            if not item_detail_data:
                print(f"      Warning: Could not fetch details for item ID {item_id}", flush=True)
                time.sleep(0.05) # Shorter delay for item detail failures
                continue

            item_name = item_detail_data.get("name")
            item_quality_data = item_detail_data.get("quality", {})
            item_quality = item_quality_data.get("name", "Unknown").upper() if isinstance(item_quality_data, dict) else "Unknown"
            
            inventory_type_info = item_detail_data.get("inventory_type", {})
            slot_type_api = inventory_type_info.get("type") if isinstance(inventory_type_info, dict) else None
            
            icon_url = None
            media_key_href = item_detail_data.get("media", {}).get("key", {}).get("href")
            if media_key_href:
                item_media_data = make_blizzard_api_request(None, full_url=media_key_href)
                if item_media_data and "assets" in item_media_data:
                    for asset in item_media_data["assets"]:
                        if asset.get("key") == "icon":
                            icon_url = asset.get("value")
                            break
            
            if item_name and item_quality == "EPIC" and slot_type_api:
                existing_item = db_session.query(Item).filter_by(id=item_id).first()
                if not existing_item:
                    new_item = Item(
                        id=item_id, name=item_name, quality=item_quality,
                        slot_type=slot_type_api, source_id=data_source_id,
                        source_details=encounter_name, icon_url=icon_url
                    )
                    db_session.add(new_item)
                    items_added_count += 1
            
            time.sleep(0.05) # Be respectful to API

        try:
            db_session.commit()
        except IntegrityError as ie:
            db_session.rollback()
            print(f"    DB Integrity Error for encounter {encounter_name}: {ie}", flush=True)
        except Exception as e:
            db_session.rollback()
            print(f"    Error committing items for encounter {encounter_name}: {e}", flush=True)

        print(f"  Finished encounter {encounter_name}. Items added so far for this raid: {items_added_count}", flush=True)
        time.sleep(0.2) # Longer delay between encounters

    print(f"Finished processing raid {raid_name}. Total new items added: {items_added_count}", flush=True)


def main():
    """Main function to orchestrate item data population."""
    print("Starting Item Database Population Script...", flush=True)
    db_session = SessionLocal()

    print("Ensuring all database tables exist (will create if not present)...", flush=True)
    Base.metadata.create_all(engine) # This is idempotent
    print("Database tables verified/created.", flush=True)

    populate_playable_slots(db_session)
    data_sources = populate_data_sources(db_session)

    # *** UPDATED Journal ID ***
    liberation_of_undermine_journal_id = 15522
    lou_source_id = data_sources.get("Liberation of Undermine")

    if lou_source_id:
        fetch_and_store_raid_items(db_session, "Liberation of Undermine", liberation_of_undermine_journal_id, lou_source_id)
    else:
        print("Error: 'Liberation of Undermine' data source not found.", flush=True)

    # Placeholder for Mythic+ Season 2 Dungeons
    # ... (Mythic+ logic would go here) ...

    db_session.close()
    print("Item Database Population Script Finished.", flush=True)

if __name__ == "__main__":
    if not BLIZZARD_CLIENT_ID or not BLIZZARD_CLIENT_SECRET:
        print("FATAL: BLIZZARD_CLIENT_ID or BLIZZARD_CLIENT_SECRET environment variables not set.", flush=True)
        exit(1)
    main()
