# scrape_wowhead_bis.py
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


# --- Wowhead Scraping Configuration ---
WOWHEAD_BASE_URL = "https://www.wowhead.com/guide/classes"
SPECS_TO_SCRAPE = [ 
       ("death-knight", "blood", "Death Knight", "Blood"),("death-knight", "frost", "Death Knight", "Frost"),
       ("death-knight", "unholy", "Death Knight", "Unholy"),("demon-hunter", "havoc", "Demon Hunter", "Havoc"),
       ("demon-hunter", "vengeance", "Demon Hunter", "Vengeance"),("druid", "balance", "Druid", "Balance"),
       ("druid", "feral", "Druid", "Feral"),("druid", "guardian", "Druid", "Guardian"),
       ("druid", "restoration", "Druid", "Restoration"),("evoker", "devastation", "Evoker", "Devastation"),
       ("evoker", "preservation", "Evoker", "Preservation"),("evoker", "augmentation", "Evoker", "Augmentation"),
       ("hunter", "beast-mastery", "Hunter", "Beast Mastery"),("hunter", "marksmanship", "Hunter", "Marksmanship"),
       ("hunter", "survival", "Hunter", "Survival"),("mage", "arcane", "Mage", "Arcane"),
       ("mage", "fire", "Mage", "Fire"),("mage", "frost", "Mage", "Frost"),
       ("monk", "brewmaster", "Monk", "Brewmaster"),("monk", "mistweaver", "Monk", "Mistweaver"),
       ("monk", "windwalker", "Monk", "Windwalker"),("paladin", "holy", "Paladin", "Holy"),
       ("paladin", "protection", "Paladin", "Protection"),("paladin", "retribution", "Paladin", "Retribution"),
       ("priest", "discipline", "Priest", "Discipline"),("priest", "holy", "Priest", "Holy"),
       ("priest", "shadow", "Priest", "Shadow"),("rogue", "assassination", "Rogue", "Assassination"),
       ("rogue", "outlaw", "Rogue", "Outlaw"),("rogue", "subtlety", "Rogue", "Subtlety"),
       ("shaman", "elemental", "Shaman", "Elemental"),("shaman", "enhancement", "Shaman", "Enhancement"),
       ("shaman", "restoration", "Shaman", "Restoration"),("warlock", "affliction", "Warlock", "Affliction"),
       ("warlock", "demonology", "Warlock", "Demonology"),("warlock", "destruction", "Warlock", "Destruction"),
       ("warrior", "arms", "Warrior", "Arms"),("warrior", "fury", "Warrior", "Fury"),
       ("warrior", "protection", "Warrior", "Protection"),
]
CANONICAL_UI_SLOT_NAMES_MAP = {
    "Head": "HEAD", "Neck": "NECK", "Shoulder": "SHOULDER", "Shoulders": "SHOULDER",
    "Back": "BACK", "Cloak": "BACK", "Chest": "CHEST", "Wrist": "WRIST", "Bracers": "WRIST",
    "Hands": "HANDS", "Gloves": "HANDS", "Waist": "WAIST", "Belt": "WAIST",
    "Legs": "LEGS", "Feet": "FEET", "Boots": "FEET",
    "Finger 1": "FINGER1", "Ring 1": "FINGER1", "Finger1": "FINGER1",
    "Finger 2": "FINGER2", "Ring 2": "FINGER2", "Finger2": "FINGER2",
    "Trinket 1": "TRINKET1", "Trinket1": "TRINKET1",
    "Trinket 2": "TRINKET2", "Trinket2": "TRINKET2",
    "Main Hand": "MAIN_HAND", "Main-Hand": "MAIN_HAND", "One-Hand": "MAIN_HAND", "Two-Hand": "MAIN_HAND",
    "Off Hand": "OFF_HAND", "Off-Hand": "OFF_HAND",
    "Weapon": "MAIN_HAND", "Ranged": "MAIN_HAND", "Shield": "OFF_HAND",
    "Dagger": "MAIN_HAND", "Fist Weapon": "MAIN_HAND", "Mace": "MAIN_HAND", "Sword": "MAIN_HAND",
    "Polearm": "MAIN_HAND", "Staff": "MAIN_HAND", "Axe": "MAIN_HAND",
    "Gun": "MAIN_HAND", "Bow": "MAIN_HAND", "Crossbow": "MAIN_HAND",
    "Wand": "MAIN_HAND", 
}

def get_html_content(url, class_slug_for_file=None, spec_slug_for_file=None):
    """
    Fetches HTML content from a URL.
    Optionally uses a local cache file based on class and spec slugs.
    """
    local_file_path = None
    if class_slug_for_file and spec_slug_for_file:
        # Construct a file path for caching. Ensure it's a valid path.
        # Example: 'cache_dir/wowhead_cache_death-knight_blood.html'
        # For simplicity, placing in current dir. For production, consider a dedicated cache folder.
        local_file_path = f"wowhead_cache_{class_slug_for_file}_{spec_slug_for_file}.html"

    # Try to load from local cache if path is defined
    if local_file_path:
        try:
            with open(local_file_path, "r", encoding="utf-8") as f:
                print(f"    SUCCESS: Loaded HTML from local cache: {local_file_path}", flush=True)
                return f.read()
        except FileNotFoundError:
            print(f"    INFO: Local cache file not found: {local_file_path}. Fetching from web.", flush=True)
        except Exception as e:
            print(f"    WARNING: Error reading local cache file {local_file_path}: {e}", flush=True)

    # Fetch from web if cache not used or failed
    print(f"    Fetching HTML from: {url}", flush=True)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    try:
        response = requests.get(url, headers=headers, timeout=20) # Increased timeout
        response.raise_for_status() # Will raise an HTTPError for bad responses (4XX or 5XX)
        
        # Save to local cache if path is defined
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
    except Exception as e_general: # Catch any other unexpected errors during fetch
        print(f"    UNEXPECTED ERROR fetching URL {url}: {e_general}", flush=True)
        return None

def parse_wowhead_bis_table(html_content, class_name, spec_name):
    """
    Parses the HTML content of a Wowhead BiS page to extract item information.
    """
    print(f"    Parsing HTML for {class_name} - {spec_name}...", flush=True)
    if not html_content:
        print("    No HTML content to parse.", flush=True)
        return []

    items = []
    parser_to_use = 'lxml' # Prefer lxml for speed and robustness
    try:
        soup = BeautifulSoup(html_content, parser_to_use) 
    except Exception as e_parser: # Fallback to html.parser if lxml is not available or fails
        print(f"    WARNING: Failed to initialize BeautifulSoup with '{parser_to_use}' parser: {e_parser}", flush=True)
        parser_to_use = 'html.parser' 
        print(f"    Attempting fallback to '{parser_to_use}' parser.", flush=True)
        try:
            soup = BeautifulSoup(html_content, parser_to_use)
        except Exception as e_fallback_parser:
            print(f"    ERROR: Failed to initialize BeautifulSoup with fallback '{parser_to_use}' parser: {e_fallback_parser}", flush=True)
            return [] # Cannot parse if both fail

    try:
        # Define patterns for finding the relevant heading.
        # This makes it more flexible if the exact wording or heading level changes slightly.
        relevant_heading_texts = [
            f"Overall {spec_name} {class_name} Best in Slot Gear",
            f"{spec_name} {class_name} Best in Slot Gear" # A slightly more generic fallback
        ]
        
        found_heading = None
        for text_pattern_str in relevant_heading_texts:
            # Compile a regex pattern for case-insensitive matching.
            text_pattern = re.compile(text_pattern_str, re.IGNORECASE)
            # Look for h3 first as often used, then h4, then h2 as fallbacks.
            for heading_tag_name in ['h3', 'h4', 'h2']: 
                headings = soup.find_all(heading_tag_name, string=text_pattern)
                if headings:
                    found_heading = headings[0] # Take the first match
                    print(f"    Found heading: '{found_heading.get_text(strip=True)}' using tag '{heading_tag_name}'", flush=True)
                    break # Exit inner loop (tag names)
            if found_heading:
                break # Exit outer loop (text patterns)
        
        if not found_heading:
            print(f"    Could not find a suitable BiS table heading for {spec_name} {class_name} using patterns: {relevant_heading_texts}", flush=True)
            return []

        # MODIFIED SECTION: Use find_next to locate the table wrapper.
        # This is more robust than relying on strict sibling relationships.
        table_wrapper = found_heading.find_next('div', class_='markup-table-wrapper')
            
        if not table_wrapper:
            print(f"    Could not find 'div.markup-table-wrapper' after the heading (using find_next) for {spec_name} {class_name}.", flush=True)
            return []
            
        bis_table = table_wrapper.find('table')
        if not bis_table:
            print(f"    Could not find 'table' within 'div.markup-table-wrapper' for {spec_name} {class_name}.", flush=True)
            return []

        print("    Found BiS table within 'markup-table-wrapper'. Processing rows...", flush=True)
        
        # Find table body, or fall back to all rows in the table if no tbody.
        table_body = bis_table.find('tbody')
        rows_to_parse = table_body.find_all('tr') if table_body else bis_table.find_all('tr')

        for row_idx, row in enumerate(rows_to_parse):
            cells = row.find_all(['td', 'th']) # Header rows might use <th>
            
            # Ensure there are enough cells to parse.
            if len(cells) < 2: 
                # print(f"      Skipping row {row_idx+1}: Not enough cells ({len(cells)}). Content: {row.get_text(strip=True, separator='|')}", flush=True)
                continue

            try:
                raw_slot_name = cells[0].get_text(strip=True)
                
                # Skip header rows based on common header text.
                if raw_slot_name.lower() in ["slot", "item", "source", "notes"]:
                    # print(f"      Skipping header row: {raw_slot_name}", flush=True)
                    continue

                # Map the raw slot name from Wowhead to our canonical UI slot type.
                ui_slot_type = CANONICAL_UI_SLOT_NAMES_MAP.get(raw_slot_name, raw_slot_name) 

                item_cell = cells[1]
                # Find the item link, which usually contains item IDs.
                item_link_tag = item_cell.find('a', href=re.compile(r'/item='))
                
                if not item_link_tag: 
                    # print(f"      Skipping row {row_idx+1} for slot '{raw_slot_name}': No item link found.", flush=True)
                    continue

                # Extract item name.
                item_name = item_link_tag.get_text(strip=True)
                if not item_name: # Fallback if name is inside a specific span
                    span_text = item_link_tag.find('span', class_='tinyicontxt')
                    if span_text: item_name = span_text.get_text(strip=True)
                if not item_name: item_name = "Unknown Item - Parse Error" # Default if still not found

                # Extract Wowhead item ID from the href.
                wowhead_item_id_match = re.search(r'/item=(\d+)', item_link_tag.get('href', ''))
                wowhead_item_id = wowhead_item_id_match.group(1) if wowhead_item_id_match else None
                
                # Attempt to extract Blizzard item ID from 'data-wowhead' or 'rel' attributes.
                blizzard_item_id = None
                data_wowhead = item_link_tag.get('data-wowhead') # Often like "item=12345&..."
                if data_wowhead: 
                    match = re.search(r'item=(\d+)', data_wowhead)
                    if match: blizzard_item_id = int(match.group(1))
                
                if not blizzard_item_id: # Fallback to 'rel' attribute
                    rel_attr = item_link_tag.get('rel', [])
                    # Ensure rel_attr is a string for regex search
                    rel_str = "".join(rel_attr) if isinstance(rel_attr, list) else str(rel_attr)
                    match = re.search(r'item=(\d+)', rel_str)
                    if match: blizzard_item_id = int(match.group(1))
                
                # Fallback: if Blizzard ID is still None, use Wowhead ID if it's numeric.
                if not blizzard_item_id and wowhead_item_id: 
                    try: blizzard_item_id = int(wowhead_item_id) 
                    except ValueError: print(f"    Warning: Could not convert wowhead_item_id '{wowhead_item_id}' to int for Blizzard ID fallback.", flush=True)

                # Extract item source.
                item_source = cells[2].get_text(strip=True) if len(cells) > 2 else "Wowhead Guide" # Default source
                
                if ui_slot_type and item_name:
                    items.append({
                        "ui_slot_type": ui_slot_type, "item_name": item_name,
                        "wowhead_item_id": wowhead_item_id, "blizzard_item_id": blizzard_item_id,
                        "item_source": item_source
                    })
                    # print(f"      Extracted: Slot='{ui_slot_type}', Item='{item_name}', BlizzID='{blizzard_item_id}', WHID='{wowhead_item_id}', Src='{item_source}'", flush=True)

            except Exception as e_row:
                print(f"      Error parsing row: {row.get_text(strip=True, separator='|')}. Error: {e_row}", flush=True)
        print(f"    Extracted {len(items)} items for {class_name} - {spec_name}.", flush=True)
    except Exception as e: # Catch-all for other parsing errors
        print(f"    General error parsing HTML for {class_name} - {spec_name}: {e}", flush=True)
    return items

def scrape_and_store_bis_data():
    """
    Main function to orchestrate the scraping and storing of BiS data.
    """
    print("Starting Wowhead BiS scraping process...", flush=True)
    db_session = SessionLocal() # Create a new session

    # Clear existing data from the SuggestedBiS table before starting.
    print("Clearing existing SuggestedBiS data...", flush=True)
    try:
        num_deleted = db_session.query(SuggestedBiS).delete(synchronize_session=False)
        db_session.commit()
        print(f"  Deleted {num_deleted} old BiS suggestions.", flush=True)
    except Exception as e:
        db_session.rollback()
        print(f"  Error clearing SuggestedBiS table: {e}", flush=True)

    # Iterate through the defined specs to scrape.
    for class_slug, spec_slug, class_display, spec_display in SPECS_TO_SCRAPE:
        wowhead_url = f"{WOWHEAD_BASE_URL}/{class_slug}/{spec_slug}/bis-gear"
        print(f"\nFetching BiS data for: {class_display} - {spec_display} from {wowhead_url}", flush=True)
        
        # Get HTML content, using cache if available.
        html_content = get_html_content(wowhead_url, class_slug, spec_slug) 
        
        if not html_content:
            print(f"  Could not fetch HTML content for {wowhead_url}. Skipping.", flush=True)
            time.sleep(10) # Wait before trying next spec to avoid hammering site on error
            continue

        extracted_items = parse_wowhead_bis_table(html_content, class_display, spec_display)
        
        items_added_for_spec = 0
        if extracted_items:
            for item_data in extracted_items:
                blizz_id = item_data.get("blizzard_item_id")
                # Ensure Blizzard ID is an integer or None.
                try:
                    blizz_id = int(blizz_id) if blizz_id is not None else None
                except ValueError:
                    print(f"    Warning: Could not convert Blizzard ID '{blizz_id}' for item '{item_data.get('item_name')}'. Storing as None.", flush=True)
                    blizz_id = None

                # Verify the UI slot type exists in our PlayableSlot table.
                slot_entry = db_session.query(PlayableSlot).filter_by(type=item_data.get("ui_slot_type")).first()
                if not slot_entry:
                    print(f"    WARNING: UI Slot Type '{item_data.get('ui_slot_type')}' for item '{item_data.get('item_name')}' not found in PlayableSlot. Skipping.", flush=True)
                    continue
                
                # Check if this exact suggestion already exists to avoid duplicates.
                # This unique constraint is also handled by the DB table definition.
                existing_suggestion = db_session.query(SuggestedBiS).filter_by(
                    class_name=class_display, spec_name=spec_display,
                    ui_slot_type=item_data.get("ui_slot_type"), item_name=item_data.get("item_name") 
                ).first()

                if not existing_suggestion:
                    suggestion = SuggestedBiS(
                        class_name=class_display, spec_name=spec_display,
                        ui_slot_type=item_data.get("ui_slot_type"), item_name=item_data.get("item_name"),
                        blizzard_item_id=blizz_id, wowhead_item_id=item_data.get("wowhead_item_id"),
                        item_source=item_data.get("item_source"),
                        last_scraped=datetime.utcnow() # Timestamp the scrape
                    )
                    db_session.add(suggestion)
                    items_added_for_spec += 1
                elif existing_suggestion: # If it exists, update its details
                    existing_suggestion.blizzard_item_id = blizz_id
                    existing_suggestion.wowhead_item_id = item_data.get("wowhead_item_id")
                    existing_suggestion.item_source = item_data.get("item_source")
                    existing_suggestion.last_scraped = datetime.utcnow()

            # Commit changes for the current spec.
            if items_added_for_spec > 0 or (extracted_items and not items_added_for_spec): # Commit if new items or if existing items might have been updated
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
        
        # Be respectful to Wowhead's servers.
        print("Waiting for 30 seconds before next spec...", flush=True)
        time.sleep(30) 

    db_session.close() # Close the session after all specs are processed.
    print("\nWowhead BiS scraping process finished.", flush=True)

if __name__ == "__main__":
    print("Ensuring database tables exist (including SuggestedBiS)...", flush=True)
    # Create tables if they don't exist. This is safe to run multiple times.
    # Only creating tables specifically used/referenced by this script.
    Base.metadata.create_all(engine, tables=[PlayableSlot.__table__, Item.__table__, SuggestedBiS.__table__], checkfirst=True) 
    print("Database tables checked/created.", flush=True)
    
    scrape_and_store_bis_data()
