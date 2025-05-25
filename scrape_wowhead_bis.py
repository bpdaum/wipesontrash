# scrape_wowhead_bis.py
import os
import time
import json 
import re # For parsing item IDs

# --- HTML Parsing Library ---
# Ensure BeautifulSoup is installed in your environment (pip install beautifulsoup4 lxml)
# and add it to requirements.txt for Heroku.
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
    display_order = Column(Integer, default=0) # Added for consistency if you use it

class Item(Base): 
    __tablename__ = 'item'
    id = Column(Integer, primary_key=True) # Blizzard Item ID
    name = Column(String(255), nullable=False, index=True)
    # Add other columns if needed, e.g., icon_url, slot_type
    # This model is mainly for the FK relationship in SuggestedBiS.
    # If you want to link to your main Item table, ensure this model matches.

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
    ("mage", "arcane", "Mage", "Arcane"),
    ("mage", "fire", "Mage", "Fire"),
    ("mage", "frost", "Mage", "Frost"),
    ("warrior", "arms", "Warrior", "Arms"),
    ("warrior", "fury", "Warrior", "Fury"),
    ("warrior", "protection", "Warrior", "Protection"),
    # Add more as needed
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
}

def get_html_content(url, query_for_tool="Overall Best in Slot Gear table"):
    """
    Fetches HTML content from a URL.
    This is a placeholder for using the 'browsing' tool or a library like 'requests'.
    For actual execution, you'd replace this with:
    from google_search_tool import browsing (or your actual import)
    return browsing.browse(url=url, query=query_for_tool)
    """
    print(f"    Simulating fetch for: {url}", flush=True)
    # In a real scenario, you'd use a tool or library here.
    # For testing, you can load HTML from a local file if you've saved it.
    # Example:
    # try:
    #     with open(f"{class_slug}_{spec_slug}_bis.html", "r", encoding="utf-8") as f:
    #         return f.read()
    # except FileNotFoundError:
    #     print(f"    ERROR: Test HTML file not found for {url}", flush=True)
    #     return None
    
    # Placeholder for when using the actual browsing tool:
    # This function should be replaced by a call to the browsing tool.
    # For now, returning None to indicate that the live fetch is not implemented here.
    print(f"    NOTE: Live HTML fetching not implemented in this environment. Returning None.", flush=True)
    print(f"    You would use a tool like 'browsing.browse(url=\"{url}\", query=\"{query_for_tool}\")' here.", flush=True)
    return None


def parse_wowhead_bis_table(html_content, class_name, spec_name):
    print(f"    Parsing HTML for {class_name} - {spec_name}...", flush=True)
    if not html_content:
        print("    No HTML content to parse.", flush=True)
        return []

    items = []
    try:
        soup = BeautifulSoup(html_content, 'lxml') # Use lxml for speed if available

        # Construct the dynamic ID for the heading
        # e.g., "arcane-mage-best-in-slot-gear"
        heading_id_slug = f"{spec_name.lower().replace(' ', '-')}-{class_name.lower().replace(' ', '-')}-best-in-slot-gear"
        
        # Try to find the heading by ID first
        heading = soup.find('h4', id=heading_id_slug) 
        if not heading:
            # Fallback: try to find a heading with similar text, more fragile
            heading_text_pattern = re.compile(f"Overall {spec_name} {class_name} Best in Slot Gear", re.IGNORECASE)
            heading = soup.find(['h2', 'h3', 'h4'], string=heading_text_pattern)

        if not heading:
            print(f"    Could not find BiS table heading for {spec_name} {class_name} (ID: {heading_id_slug} or text match).", flush=True)
            return []

        print(f"    Found heading: '{heading.text.strip()}'", flush=True)
        
        # Find the table: usually a sibling or wrapped in a div after the heading
        # Wowhead tables often have class 'wh-db-table' or 'listview-table'
        # This might need adjustment based on actual page structure.
        current_element = heading
        bis_table = None
        for _ in range(5): # Look for a table within the next 5 sibling elements
            current_element = current_element.find_next_sibling()
            if not current_element: break
            if current_element.name == 'table' and ('wh-db-table' in current_element.get('class', []) or 'listview-table' in current_element.get('class', [])):
                bis_table = current_element
                break
            # Sometimes the table is wrapped in a div
            if current_element.name == 'div':
                table_in_div = current_element.find('table', class_=lambda x: x and ('wh-db-table' in x or 'listview-table' in x))
                if table_in_div:
                    bis_table = table_in_div
                    break
        
        if not bis_table:
            print(f"    Could not find BiS table after heading for {spec_name} {class_name}.", flush=True)
            return []

        print("    Found BiS table. Processing rows...", flush=True)
        
        # Iterate through rows, skipping header if present (tbody is safer)
        table_body = bis_table.find('tbody')
        rows_to_parse = table_body.find_all('tr') if table_body else bis_table.find_all('tr')

        for row in rows_to_parse:
            cells = row.find_all('td')
            if len(cells) < 2: # Expect at least Slot and Item columns
                continue

            try:
                raw_slot_name = cells[0].get_text(strip=True)
                ui_slot_type = CANONICAL_UI_SLOT_NAMES_MAP.get(raw_slot_name, raw_slot_name) # Normalize

                item_cell = cells[1]
                item_link_tag = item_cell.find('a', href=re.compile(r'/item='))
                
                if not item_link_tag:
                    # print(f"      Skipping row, no item link found: {row.get_text(strip=True, separator='|')}", flush=True)
                    continue

                item_name = item_link_tag.get_text(strip=True)
                
                wowhead_item_id_match = re.search(r'/item=(\d+)', item_link_tag.get('href', ''))
                wowhead_item_id = wowhead_item_id_match.group(1) if wowhead_item_id_match else None
                
                blizzard_item_id = None
                # Try to get Blizzard ID from data-wowhead attribute or rel
                data_wowhead = item_link_tag.get('data-wowhead')
                if data_wowhead: # Format: "item=229343&bonus=..."
                    match = re.search(r'item=(\d+)', data_wowhead)
                    if match: blizzard_item_id = int(match.group(1))
                
                if not blizzard_item_id: # Fallback to 'rel' if data-wowhead not fruitful
                    rel_attr = item_link_tag.get('rel', [])
                    if isinstance(rel_attr, list): # rel can be a list of strings
                        for rel_val in rel_attr:
                            match = re.search(r'item=(\d+)', rel_val)
                            if match: blizzard_item_id = int(match.group(1)); break
                    elif isinstance(rel_attr, str): # or a single string
                        match = re.search(r'item=(\d+)', rel_attr)
                        if match: blizzard_item_id = int(match.group(1))


                item_source = cells[2].get_text(strip=True) if len(cells) > 2 else "Unknown Source"
                
                if ui_slot_type and item_name:
                    items.append({
                        "ui_slot_type": ui_slot_type,
                        "item_name": item_name,
                        "wowhead_item_id": wowhead_item_id,
                        "blizzard_item_id": blizzard_item_id,
                        "item_source": item_source
                    })
                    # print(f"      Extracted: Slot='{ui_slot_type}', Item='{item_name}', WowheadID='{wowhead_item_id}', BlizzardID='{blizzard_item_id}', Source='{item_source}'", flush=True)

            except Exception as e_row:
                print(f"      Error parsing row: {row.get_text(strip=True, separator='|')}. Error: {e_row}", flush=True)
                continue
        
        print(f"    Extracted {len(items)} items for {class_name} - {spec_name}.", flush=True)

    except Exception as e:
        print(f"    General error parsing HTML for {class_name} - {spec_name}: {e}", flush=True)
    
    return items


def scrape_and_store_bis_data():
    print("Starting Wowhead BiS scraping process...", flush=True)
    db_session = SessionLocal()

    print("Clearing existing SuggestedBiS data...", flush=True)
    try:
        num_deleted = db_session.query(SuggestedBiS).delete(synchronize_session=False)
        db_session.commit()
        print(f"  Deleted {num_deleted} old BiS suggestions.", flush=True)
    except Exception as e:
        db_session.rollback()
        print(f"  Error clearing SuggestedBiS table: {e}", flush=True)


    for class_slug, spec_slug, class_display, spec_display in SPECS_TO_SCRAPE:
        wowhead_url = f"{WOWHEAD_BASE_URL}/{class_slug}/{spec_slug}/bis-gear"
        print(f"\nFetching BiS data for: {class_display} - {spec_display} from {wowhead_url}", flush=True)

        html_content = get_html_content(wowhead_url, query_for_tool=f"Overall {spec_display} {class_display} Best in Slot Gear table")
        
        if not html_content:
            print(f"  Could not fetch HTML content for {wowhead_url}. Skipping.", flush=True)
            time.sleep(10) 
            continue

        extracted_items = parse_wowhead_bis_table(html_content, class_display, spec_display)
        
        items_added_for_spec = 0
        if extracted_items:
            for item_data in extracted_items:
                # Ensure blizzard_item_id is an int if it exists, otherwise None
                blizz_id = item_data.get("blizzard_item_id")
                try:
                    blizz_id = int(blizz_id) if blizz_id is not None else None
                except ValueError:
                    print(f"    Warning: Could not convert Blizzard ID '{blizz_id}' to int for item '{item_data.get('item_name')}'. Storing as None.", flush=True)
                    blizz_id = None

                # Check if ui_slot_type exists in PlayableSlot table
                slot_entry = db_session.query(PlayableSlot).filter_by(type=item_data.get("ui_slot_type")).first()
                if not slot_entry:
                    print(f"    WARNING: UI Slot Type '{item_data.get('ui_slot_type')}' for item '{item_data.get('item_name')}' not found in PlayableSlot table. Skipping this item.", flush=True)
                    continue
                
                # Optional: Check if Blizzard Item ID exists in your Item table
                # This is only if you want to ensure items are in your main item DB before suggesting them.
                # if blizz_id and not db_session.get(Item, blizz_id):
                #     print(f"    WARNING: Blizzard Item ID {blizz_id} for '{item_data.get('item_name')}' not found in local Item table. Skipping.", flush=True)
                #     continue


                existing_suggestion = db_session.query(SuggestedBiS).filter_by(
                    class_name=class_display,
                    spec_name=spec_display,
                    ui_slot_type=item_data.get("ui_slot_type"),
                    item_name=item_data.get("item_name") 
                ).first()

                if not existing_suggestion:
                    suggestion = SuggestedBiS(
                        class_name=class_display,
                        spec_name=spec_display,
                        ui_slot_type=item_data.get("ui_slot_type"),
                        item_name=item_data.get("item_name"),
                        blizzard_item_id=blizz_id,
                        wowhead_item_id=item_data.get("wowhead_item_id"),
                        item_source=item_data.get("item_source")
                    )
                    db_session.add(suggestion)
                    items_added_for_spec += 1
            
            if items_added_for_spec > 0:
                try:
                    db_session.commit()
                    print(f"  Committed {items_added_for_spec} new BiS suggestions for {class_display} - {spec_display}.", flush=True)
                except Exception as e:
                    db_session.rollback()
                    print(f"  Error committing BiS suggestions for {class_display} - {spec_display}: {e}", flush=True)
        else:
            print(f"  No items extracted for {class_display} - {spec_display}.", flush=True)

        print("Waiting for 30 seconds before next spec...", flush=True)
        time.sleep(30) 

    db_session.close()
    print("\nWowhead BiS scraping process finished.", flush=True)

if __name__ == "__main__":
    print("Ensuring database tables exist (including SuggestedBiS)...", flush=True)
    Base.metadata.create_all(engine, tables=[PlayableSlot.__table__, Item.__table__, SuggestedBiS.__table__], checkfirst=True) 
    print("Database tables checked/created.", flush=True)
    
    # --- To run the scraper: ---
    # 1. Make sure you have a way to fetch HTML content in get_html_content()
    #    (e.g., by using the 'browsing' tool if available, or requests library).
    # 2. Uncomment the line below.
    scrape_and_store_bis_data() 
    # print("Scraping is commented out by default. Please review and implement parsing logic.", flush=True)
