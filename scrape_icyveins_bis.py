# scrape_icyveins_bis.py
import os
import time
import json 
import re 
import requests 
from datetime import datetime 

# --- HTML Parsing Library ---
try:
    from bs4 import BeautifulSoup
except ImportError:
    print("BeautifulSoup4 library not found. Please install it: pip install beautifulsoup4 lxml", flush=True)
    exit(1)

from sqlalchemy import create_engine, Column, Integer, String, DateTime, UniqueConstraint, ForeignKey
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy.sql import func

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
class PlayableSlot(Base): 
    __tablename__ = 'playable_slot'
    id = Column(Integer, primary_key=True, autoincrement=True) 
    type = Column(String(50), unique=True, nullable=False, index=True) 
    name = Column(String(100), nullable=False)
    display_order = Column(Integer, default=0)

class Item(Base): 
    __tablename__ = 'item'
    id = Column(Integer, primary_key=True) 
    name = Column(String(255), nullable=False, index=True)

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

    slot_relation = relationship("PlayableSlot", foreign_keys=[ui_slot_type])
    item_relation = relationship("Item", foreign_keys=[blizzard_item_id])
    __table_args__ = (UniqueConstraint('class_name', 'spec_name', 'ui_slot_type', 'item_name', name='_suggested_bis_uc'),)
    def __repr__(self):
        return f'<SuggestedBiS {self.class_name} {self.spec_name} {self.ui_slot_type}: {self.item_name}>'


# --- Icy Veins Scraping Configuration ---
ICYVEINS_BASE_URL = "https://www.icy-veins.com" 
SPECS_TO_SCRAPE = [ 
    ("death-knight", "blood", "Death Knight", "Blood", "tank"),
    ("death-knight", "frost", "Death Knight", "Frost", "dps"),
    ("death-knight", "unholy", "Death Knight", "Unholy", "dps"),
    ("demon-hunter", "havoc", "Demon Hunter", "Havoc", "dps"),
    ("demon-hunter", "vengeance", "Demon Hunter", "Vengeance", "tank"),
    ("druid", "balance", "Druid", "Balance", "dps"),
    ("druid", "feral", "Druid", "Feral", "dps"),
    ("druid", "guardian", "Druid", "Guardian", "tank"),
    ("druid", "restoration", "Druid", "Restoration", "healing"), 
    ("evoker", "devastation", "Evoker", "Devastation", "dps"),
    ("evoker", "preservation", "Evoker", "Preservation", "healing"), 
    ("evoker", "augmentation", "Evoker", "Augmentation", "dps"),
    ("hunter", "beast-mastery", "Hunter", "Beast Mastery", "dps"),
    ("hunter", "marksmanship", "Hunter", "Marksmanship", "dps"),
    ("hunter", "survival", "Hunter", "Survival", "dps"),
    ("mage", "arcane", "Mage", "Arcane", "dps"),
    ("mage", "fire", "Mage", "Fire", "dps"),
    ("mage", "frost", "Mage", "Frost", "dps"),
    ("monk", "brewmaster", "Monk", "Brewmaster", "tank"),
    ("monk", "mistweaver", "Monk", "Mistweaver", "healing"), 
    ("monk", "windwalker", "Monk", "Windwalker", "dps"),
    ("paladin", "holy", "Paladin", "Holy", "healing"), 
    ("paladin", "protection", "Paladin", "Protection", "tank"),
    ("paladin", "retribution", "Paladin", "Retribution", "dps"),
    ("priest", "discipline", "Priest", "Discipline", "healing"), 
    ("priest", "holy", "Priest", "Holy", "healing"), 
    ("priest", "shadow", "Priest", "Shadow", "dps"),
    ("rogue", "assassination", "Rogue", "Assassination", "dps"),
    ("rogue", "outlaw", "Rogue", "Outlaw", "dps"),
    ("rogue", "subtlety", "Rogue", "Subtlety", "dps"),
    ("shaman", "elemental", "Shaman", "Elemental", "dps"),
    ("shaman", "enhancement", "Shaman", "Enhancement", "dps"),
    ("shaman", "restoration", "Shaman", "Restoration", "healing"), 
    ("warlock", "affliction", "Warlock", "Affliction", "dps"),
    ("warlock", "demonology", "Warlock", "Demonology", "dps"),
    ("warlock", "destruction", "Warlock", "Destruction", "dps"),
    ("warrior", "arms", "Warrior", "Arms", "dps"),
    ("warrior", "fury", "Warrior", "Fury", "dps"),
    ("warrior", "protection", "Warrior", "Protection", "tank"),
]

CANONICAL_UI_SLOT_NAMES_MAP = {
    "Head": "HEAD", "Helm": "HEAD", 
    "Neck": "NECK", 
    "Shoulder": "SHOULDER", "Shoulders": "SHOULDER",
    "Back": "BACK", "Cloak": "BACK", 
    "Chest": "CHEST", 
    "Wrist": "WRIST", "Bracers": "WRIST", "Wrists": "WRIST", 
    "Hands": "HANDS", "Gloves": "HANDS", 
    "Waist": "WAIST", "Belt": "WAIST",
    "Legs": "LEGS", 
    "Feet": "FEET", "Boots": "FEET",
    "Finger 1": "FINGER1", "Ring 1": "FINGER1", "Finger1": "FINGER1", "Ring #1": "FINGER1", 
    "Finger 2": "FINGER2", "Ring 2": "FINGER2", "Finger2": "FINGER2", "Ring #2": "FINGER2", 
    "Ring": "FINGER1", 
    "Trinket 1": "TRINKET1", "Trinket1": "TRINKET1", "Trinket #1": "TRINKET1", 
    "Trinket 2": "TRINKET2", "Trinket2": "TRINKET2", "Trinket #2": "TRINKET2", 
    "Trinket": "TRINKET1", "Trinkets": "TRINKET1", 
    "Main Hand": "MAIN_HAND", "Main-Hand": "MAIN_HAND", "Mainhand Weapon": "MAIN_HAND", "Weapon Main-Hand": "MAIN_HAND", 
    "One-Hand": "MAIN_HAND", "One-Handed Weapon": "MAIN_HAND", "1H Weapon": "MAIN_HAND", 
    "Two-Hand": "MAIN_HAND", "Weapon": "MAIN_HAND", "2H Weapon": "MAIN_HAND", "Two-Handed Weapon": "MAIN_HAND", "Weapon (Two-Hand)": "MAIN_HAND", # Added Weapon (Two-Hand)
    "Off Hand": "OFF_HAND", "Off-Hand": "OFF_HAND", "Offhand Weapon": "OFF_HAND", "Offhand": "OFF_HAND", "Weapon Off-Hand": "OFF_HAND", 
    "Shield": "OFF_HAND",
    "Dagger": "MAIN_HAND", "Fist Weapon": "MAIN_HAND", "Mace": "MAIN_HAND", "Sword": "MAIN_HAND",
    "Polearm": "MAIN_HAND", "Staff": "MAIN_HAND", "Axe": "MAIN_HAND",
    "Gun": "MAIN_HAND", "Bow": "MAIN_HAND", "Crossbow": "MAIN_HAND", "Ranged": "MAIN_HAND",
    "Wand": "MAIN_HAND", 
}

def get_html_content(url, class_slug_for_file=None, spec_slug_for_file=None):
    local_file_path = None
    if class_slug_for_file and spec_slug_for_file:
        local_file_path = f"icyveins_cache_{class_slug_for_file}_{spec_slug_for_file}.html"

    if local_file_path:
        try:
            with open(local_file_path, "r", encoding="utf-8") as f:
                print(f"    SUCCESS: Loaded HTML from local cache: {local_file_path}", flush=True)
                return f.read()
        except FileNotFoundError:
            print(f"    INFO: Local cache file not found: {local_file_path}. Fetching from web.", flush=True)
        except Exception as e:
            print(f"    WARNING: Error reading local cache file {local_file_path}: {e}", flush=True)

    print(f"    Fetching HTML from: {url}", flush=True)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        response = requests.get(url, headers=headers, timeout=20) 
        response.raise_for_status() 
        
        if local_file_path:
            try:
                with open(local_file_path, "w", encoding="utf-8") as f:
                    f.write(response.text)
                print(f"    SUCCESS: Saved HTML to local cache: {local_file_path}", flush=True)
            except Exception as e:
                print(f"    WARNING: Error writing to local cache file {local_file_path}: {e}", flush=True)
        
        return response.text
    except requests.exceptions.RequestException as e:
        print(f"    ERROR: Could not fetch URL {url}. Error: {e}", flush=True)
        return None
    except Exception as e_general: 
        print(f"    UNEXPECTED ERROR fetching URL {url}: {e_general}", flush=True)
        return None

def parse_icyveins_bis_table(html_content, class_name, spec_name):
    print(f"    Parsing HTML for {class_name} - {spec_name} (Icy Veins - Multi-Table Check Strategy)...", flush=True)
    if not html_content:
        print("    No HTML content to parse.", flush=True)
        return []

    all_extracted_items = [] 
    parser_to_use = 'lxml' 
    try:
        soup = BeautifulSoup(html_content, parser_to_use) 
    except Exception as e_parser: 
        print(f"    WARNING: Failed to initialize BeautifulSoup with '{parser_to_use}' parser: {e_parser}", flush=True)
        parser_to_use = 'html.parser' 
        print(f"    Attempting fallback to '{parser_to_use}' parser.", flush=True)
        try:
            soup = BeautifulSoup(html_content, parser_to_use)
        except Exception as e_fallback_parser:
            print(f"    ERROR: Failed to initialize BeautifulSoup with fallback '{parser_to_use}' parser: {e_fallback_parser}", flush=True)
            return [] 

    try:
        all_tables_on_page = soup.find_all('table') 
            
        if not all_tables_on_page:
            print(f"    ERROR: Could not find any 'table' elements on the page for {spec_name} {class_name}.", flush=True)
            return []
        
        print(f"    Found {len(all_tables_on_page)} table(s) on the page. Checking up to the first 3.", flush=True)
        
        best_item_count = -1 

        for table_index, bis_table in enumerate(all_tables_on_page[:3]): 
            print(f"\n    --- Processing Table #{table_index + 1} ---", flush=True)
            print(f"    Table classes: {bis_table.get('class')}", flush=True)
            
            current_table_items = [] 
            table_body = bis_table.find('tbody')
            rows_to_parse = table_body.find_all('tr') if table_body else bis_table.find_all('tr')

            if not rows_to_parse:
                print(f"    WARNING: Table #{table_index + 1} for {spec_name} {class_name} has no rows (or no tbody with rows).", flush=True)
                continue 

            ring_count = 0
            trinket_count = 0

            for row_idx, row in enumerate(rows_to_parse):
                cells = row.find_all(['td', 'th']) 
                if len(cells) < 2: continue

                try:
                    raw_slot_name_from_table = cells[0].get_text(strip=True)
                    if raw_slot_name_from_table.lower() in ["slot", "item", "source", "notes", "type", "name", "details"]:
                        continue

                    ui_slot_type = ""
                    if raw_slot_name_from_table in CANONICAL_UI_SLOT_NAMES_MAP:
                        ui_slot_type = CANONICAL_UI_SLOT_NAMES_MAP[raw_slot_name_from_table]
                    elif raw_slot_name_from_table == "Ring": 
                        ring_count += 1
                        ui_slot_type = f"FINGER{ring_count}"
                    elif raw_slot_name_from_table == "Trinket": 
                        trinket_count += 1
                        ui_slot_type = f"TRINKET{trinket_count}"
                    else: 
                        ui_slot_type = CANONICAL_UI_SLOT_NAMES_MAP.get(raw_slot_name_from_table, raw_slot_name_from_table)

                    item_cell = cells[1]
                    item_name, blizzard_item_id, wowhead_id_from_href = None, None, None

                    link_tag = item_cell.find('a', attrs={'data-wowhead': re.compile(r'item=\d+')})
                    if not link_tag:
                        link_tag = item_cell.find('a', href=re.compile(r'wowhead.com/item='))

                    if link_tag: 
                        name_span = link_tag.find('span') 
                        if name_span and name_span.get_text(strip=True): item_name = name_span.get_text(strip=True)
                        if not item_name: item_name = link_tag.get_text(strip=True)
                        if not item_name: 
                            img_tag = link_tag.find('img', alt=True)
                            if img_tag and img_tag.get('alt', '').strip(): item_name = img_tag['alt'].strip().replace(" Icon", "")
                        
                        data_wowhead_attr = link_tag.get('data-wowhead')
                        if data_wowhead_attr:
                            match = re.search(r'item=(\d+)', data_wowhead_attr)
                            if match: blizzard_item_id = int(match.group(1))
                        
                        href_attr = link_tag.get('href', '')
                        href_match = re.search(r'item=(\d+)', href_attr)
                        if href_match:
                            wowhead_id_from_href = href_match.group(1)
                            if not blizzard_item_id: 
                                try: blizzard_item_id = int(wowhead_id_from_href)
                                except ValueError: pass 
                    else: 
                        cell_text_content = item_cell.get_text(strip=True)
                        if cell_text_content and cell_text_content not in ["]", ":10520]", "", "None", "-", "N/A"]:
                            item_name_candidate = cell_text_content.split('(', 1)[0].strip()
                            if item_name_candidate: item_name = item_name_candidate
                            else: continue 
                        else: continue

                    if not item_name: item_name = "Unknown Item - Parse Error"

                    item_source_text = "Icy Veins Guide" 
                    if len(cells) > 2:
                        source_cell_text = cells[2].get_text(strip=True)
                        if source_cell_text: item_source_text = source_cell_text
                    
                    if ui_slot_type and item_name and item_name != "Unknown Item - Parse Error":
                        current_table_items.append({
                            "ui_slot_type": ui_slot_type, "item_name": item_name,
                            "wowhead_item_id": wowhead_id_from_href, "blizzard_item_id": blizzard_item_id,    
                            "item_source": item_source_text
                        })
                except Exception as e_row:
                    print(f"      Error parsing row content for slot '{cells[0].get_text(strip=True)}': {e_row}", flush=True)
            
            print(f"    Table #{table_index + 1} yielded {len(current_table_items)} items.", flush=True)
            if len(current_table_items) >= 10: 
                print(f"    Table #{table_index + 1} has >= 10 items. Assuming this is the correct BiS table.", flush=True)
                all_extracted_items = current_table_items
                break 
            elif len(current_table_items) > best_item_count:
                print(f"    Table #{table_index + 1} has {len(current_table_items)} items, which is more than previous best ({best_item_count}). Storing these as potential BiS.", flush=True)
                best_item_count = len(current_table_items)
                all_extracted_items = current_table_items
        
        if not all_extracted_items:
            print(f"    No items extracted from any of the first {min(len(all_tables_on_page), 3)} tables for {class_name} - {spec_name}.", flush=True)
        elif len(all_extracted_items) < 10:
            print(f"    WARNING: Best table found for {class_name} - {spec_name} only yielded {len(all_extracted_items)} items (less than 10).", flush=True)
        
        print(f"    Finished processing tables. Final extracted item count: {len(all_extracted_items)} for {class_name} - {spec_name}.", flush=True)

    except Exception as e: 
        print(f"    General error parsing HTML for {class_name} - {spec_name}: {e}", flush=True)
    return all_extracted_items

def scrape_and_store_bis_data():
    print("Starting Icy Veins BiS scraping process...", flush=True)
    db_session = SessionLocal() 

    print("Clearing existing SuggestedBiS data...", flush=True)
    try:
        num_deleted = db_session.query(SuggestedBiS).delete(synchronize_session=False)
        db_session.commit()
        print(f"  Deleted {num_deleted} old BiS suggestions.", flush=True)
    except Exception as e:
        db_session.rollback()
        print(f"  Error clearing SuggestedBiS table: {e}", flush=True)

    for class_slug, spec_slug, class_display, spec_display, role_slug in SPECS_TO_SCRAPE:
        icyveins_url = f"{ICYVEINS_BASE_URL}/wow/{spec_slug}-{class_slug}-pve-{role_slug}-gear-best-in-slot"
        
        print(f"\nFetching BiS data for: {class_display} - {spec_display} ({role_slug.upper()}) from {icyveins_url}", flush=True)
        
        html_content = get_html_content(icyveins_url, class_slug, spec_slug) 
        
        if not html_content:
            print(f"  Could not fetch HTML content for {icyveins_url}. Skipping.", flush=True)
            time.sleep(10) 
            continue

        extracted_items = parse_icyveins_bis_table(html_content, class_display, spec_display)
        
        items_added_for_spec = 0
        if extracted_items:
            for item_data in extracted_items:
                blizz_id = item_data.get("blizzard_item_id")
                
                slot_entry = db_session.query(PlayableSlot).filter_by(type=item_data.get("ui_slot_type")).first()
                if not slot_entry:
                    print(f"    WARNING: UI Slot Type '{item_data.get('ui_slot_type')}' for item '{item_data.get('item_name')}' not found in PlayableSlot table. Skipping.", flush=True)
                    continue
                
                existing_suggestion = db_session.query(SuggestedBiS).filter_by(
                    class_name=class_display, spec_name=spec_display,
                    ui_slot_type=item_data.get("ui_slot_type"), 
                    item_name=item_data.get("item_name") 
                ).first()

                if not existing_suggestion:
                    suggestion = SuggestedBiS(
                        class_name=class_display, spec_name=spec_display,
                        ui_slot_type=item_data.get("ui_slot_type"), 
                        item_name=item_data.get("item_name"),
                        blizzard_item_id=blizz_id, 
                        wowhead_item_id=item_data.get("wowhead_item_id"), 
                        item_source=item_data.get("item_source"),
                        last_scraped=datetime.utcnow() 
                    )
                    db_session.add(suggestion)
                    items_added_for_spec += 1
                elif existing_suggestion: 
                    existing_suggestion.blizzard_item_id = blizz_id
                    existing_suggestion.wowhead_item_id = item_data.get("wowhead_item_id")
                    existing_suggestion.item_source = item_data.get("item_source")
                    existing_suggestion.last_scraped = datetime.utcnow()

            if items_added_for_spec > 0 or (extracted_items and not items_added_for_spec): 
                try:
                    db_session.commit()
                    if items_added_for_spec > 0:
                         print(f"  Committed {items_added_for_spec} new BiS suggestions for {class_display} - {spec_display}.", flush=True)
                    else:
                         print(f"  No new BiS suggestions to add for {class_display} - {spec_display} (existing entries might have been updated).", flush=True)
                except Exception as e:
                    db_session.rollback()
                    print(f"  Error committing BiS suggestions for {class_display} - {spec_display}: {e}", flush=True)
        else:
            print(f"  No items extracted for {class_display} - {spec_display}.", flush=True)
        
        print("Waiting for 20 seconds before next spec...", flush=True) 
        time.sleep(20) 

    db_session.close() 
    print("\nIcy Veins BiS scraping process finished.", flush=True)

if __name__ == "__main__":
    print("Ensuring database tables exist (including SuggestedBiS)...", flush=True)
    Base.metadata.create_all(engine, tables=[PlayableSlot.__table__, Item.__table__, SuggestedBiS.__table__], checkfirst=True) 
    print("Database tables checked/created.", flush=True)
    
    scrape_and_store_bis_data()
