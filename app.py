# app.py
# Import necessary libraries
from flask import Flask, render_template, jsonify, request, abort
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import desc, Integer, String, DateTime, UniqueConstraint, func, Float
from sqlalchemy.orm import relationship
import os
import requests
import time
from datetime import datetime, date, timedelta
import calendar
import pytz
import json
import re

# --- Configuration Loading ---
GUILD_NAME = os.environ.get('GUILD_NAME')
REGION = os.environ.get('REGION', 'us').lower()
BLIZZARD_CLIENT_ID = os.environ.get('BLIZZARD_CLIENT_ID')
BLIZZARD_CLIENT_SECRET = os.environ.get('BLIZZARD_CLIENT_SECRET')

# --- Flask Application Setup ---
app = Flask(__name__)

# --- Database Configuration ---
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    print("WARNING: DATABASE_URL environment variable not found. Defaulting to local sqlite:///guild_data.db")
    DATABASE_URL = 'sqlite:///guild_data.db'
else:
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- Database Models ---
# These should match the definitions in update_roster_data.py and populate_item_db.py

class PlayableClass(db.Model):
    __tablename__ = 'playable_class'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False)
    specs = db.relationship("PlayableSpec", back_populates="playable_class")
    characters = db.relationship("Character", back_populates="playable_class")
    def __repr__(self): return f'<PlayableClass {self.name}>'

class PlayableSpec(db.Model):
    __tablename__ = 'playable_spec'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey('playable_class.id'), nullable=False)
    playable_class = db.relationship("PlayableClass", back_populates="specs")
    def __repr__(self): return f'<PlayableSpec {self.name} (Class ID: {self.class_id})>'

class Character(db.Model):
    __tablename__ = 'character'
    id = db.Column(Integer, primary_key=True)
    name = db.Column(String(100), nullable=False)
    realm_slug = db.Column(String(100), nullable=False)
    level = db.Column(Integer)
    class_id = db.Column(Integer, db.ForeignKey('playable_class.id'))
    class_name = db.Column(String(50))
    spec_name = db.Column(String(50))
    main_spec_override = db.Column(String(50), nullable=True)
    role = db.Column(String(15))
    status = db.Column(String(15), nullable=False, index=True)
    item_level = db.Column(Integer, index=True)
    raid_progression = db.Column(String(200))
    rank = db.Column(Integer, index=True)
    last_updated = db.Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    raid_attendance_percentage = db.Column(Float, default=0.0, nullable=True)
    avg_wcl_performance = db.Column(Float, nullable=True)
    playable_class = db.relationship("PlayableClass", back_populates="characters")
    bis_selections = db.relationship("CharacterBiS", back_populates="character") # Relationship to BiS selections
    __table_args__ = (db.UniqueConstraint('name', 'realm_slug', name='_name_realm_uc'),)
    def __repr__(self): return f'<Character {self.name}-{self.realm_slug}>'

class PlayableSlot(db.Model):
    __tablename__ = 'playable_slot'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    type = db.Column(db.String(50), unique=True, nullable=False, index=True)
    name = db.Column(db.String(100), nullable=False)
    display_order = db.Column(db.Integer, default=0)
    items = db.relationship("Item", back_populates="slot")
    bis_selections = db.relationship("CharacterBiS", back_populates="slot")
    def __repr__(self): return f'<PlayableSlot {self.name} ({self.type})>'

class DataSource(db.Model):
    __tablename__ = 'data_source'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(200), unique=True, nullable=False)
    type = db.Column(db.String(50))
    items = db.relationship("Item", back_populates="source")
    def __repr__(self): return f'<DataSource {self.name}>'

class Item(db.Model):
    __tablename__ = 'item'
    id = db.Column(db.Integer, primary_key=True) # Blizzard Item ID
    name = db.Column(db.String(255), nullable=False, index=True)
    quality = db.Column(db.String(20))
    icon_url = db.Column(db.String(512), nullable=True)
    slot_type = db.Column(db.String(50), db.ForeignKey('playable_slot.type'), nullable=False, index=True)
    slot = db.relationship("PlayableSlot", back_populates="items")
    source_id = db.Column(db.Integer, db.ForeignKey('data_source.id'), nullable=True, index=True)
    source = db.relationship("DataSource", back_populates="items")
    source_details = db.Column(db.String(255))
    bis_selections = db.relationship("CharacterBiS", back_populates="item")
    def __repr__(self): return f'<Item {self.name} (ID: {self.id})>'

class CharacterBiS(db.Model):
    __tablename__ = 'character_bis'
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    character_id = db.Column(db.Integer, db.ForeignKey('character.id'), nullable=False, index=True)
    slot_type = db.Column(db.String(50), db.ForeignKey('playable_slot.type'), nullable=False, index=True)
    item_id = db.Column(db.Integer, db.ForeignKey('item.id'), nullable=True) # Allow NULL if no BiS selected
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    character = db.relationship("Character", back_populates="bis_selections")
    slot = db.relationship("PlayableSlot", back_populates="bis_selections")
    item = db.relationship("Item", back_populates="bis_selections")
    __table_args__ = (db.UniqueConstraint('character_id', 'slot_type', name='_character_slot_uc'),)
    def __repr__(self): return f'<CharacterBiS CharID: {self.character_id} Slot: {self.slot_type} ItemID: {self.item_id}>'

class WCLReport(db.Model):
    __tablename__ = 'wcl_report'
    code = db.Column(String(50), primary_key=True)
    title = db.Column(String(200))
    start_time = db.Column(DateTime, index=True)
    end_time = db.Column(DateTime)
    owner_name = db.Column(String(100))
    fetched_at = db.Column(DateTime, default=datetime.utcnow)
    def __repr__(self): return f'<WCLReport {self.code} ({self.title})>'


# --- Data Caching & API Config ---
ALL_SPECS_CACHE = {}
ALL_SPECS_LAST_FETCHED = 0
CACHE_TTL = 3600 * 6
WEB_APP_ACCESS_TOKEN_CACHE = {"token": None, "expires_at": 0}
API_BASE_URL = f"https://{REGION}.api.blizzard.com"
TOKEN_URL = f"https://{REGION}.battle.net/oauth/token"
CENTRAL_TZ = pytz.timezone('America/Chicago')


# --- API Helper Functions (for Web App Context) ---
def get_web_app_token():
    global WEB_APP_ACCESS_TOKEN_CACHE
    now = time.time()
    if WEB_APP_ACCESS_TOKEN_CACHE["token"] and WEB_APP_ACCESS_TOKEN_CACHE["expires_at"] > now + 60:
        return WEB_APP_ACCESS_TOKEN_CACHE["token"]
    client_id = BLIZZARD_CLIENT_ID
    client_secret = BLIZZARD_CLIENT_SECRET
    if not client_id or not client_secret:
        print("Error: Blizzard API credentials not configured for web app token fetch.")
        return None
    try:
        res = requests.post(TOKEN_URL, auth=(client_id, client_secret), data={'grant_type': 'client_credentials'}, timeout=10)
        res.raise_for_status()
        data = res.json()
        access_token = data.get('access_token')
        if not access_token:
             print(f"Error fetching web app token: No access_token in response {data}")
             return None
        WEB_APP_ACCESS_TOKEN_CACHE["token"] = access_token
        WEB_APP_ACCESS_TOKEN_CACHE["expires_at"] = now + data.get('expires_in', 0)
        print("Fetched new web app access token.")
        return WEB_APP_ACCESS_TOKEN_CACHE["token"]
    except Exception as e:
        print(f"Error fetching web app token: {e}")
        if hasattr(e, 'response') and e.response is not None:
             print(f"Response Status: {e.response.status_code}")
             try: print(f"Response Body: {e.response.json()}")
             except: print(f"Response Body: {e.response.text}")
        return None

def make_blizzard_api_request(endpoint, params=None, full_url=None): # Renamed for clarity
    access_token = get_web_app_token()
    if not access_token: return None
    api_url = full_url if full_url else f"{API_BASE_URL}{endpoint}"
    headers = {"Authorization": f"Bearer {access_token}"}
    if params is None: params = {}
    if not full_url and "namespace" not in params: params["namespace"] = f"profile-{REGION}"
    if not full_url and "locale" not in params: params["locale"] = "en_US"
    try:
        response = requests.get(api_url, params=params, headers=headers, timeout=15)
        if response.status_code == 404: return None
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error in make_blizzard_api_request for {api_url}: {e}")
        return None

def get_all_specs():
    global ALL_SPECS_CACHE, ALL_SPECS_LAST_FETCHED
    current_time = time.time()
    if ALL_SPECS_CACHE and (current_time - ALL_SPECS_LAST_FETCHED < CACHE_TTL):
        print("Using in-memory cached specs.")
        return ALL_SPECS_CACHE
    print("Fetching specs from database...")
    temp_spec_map = {}
    try:
        with app.app_context():
            if not db.engine.dialect.has_table(db.engine.connect(), PlayableSpec.__tablename__):
                print("PlayableSpec table does not exist. Cannot load specs from DB.")
                return {}
            all_db_specs = PlayableSpec.query.all()
            if all_db_specs:
                for spec in all_db_specs:
                    if spec.class_id not in temp_spec_map: temp_spec_map[spec.class_id] = []
                    temp_spec_map[spec.class_id].append({"id": spec.id, "name": spec.name})
                for cid in temp_spec_map: temp_spec_map[cid].sort(key=lambda x: x['name'])
                ALL_SPECS_CACHE = temp_spec_map
                ALL_SPECS_LAST_FETCHED = current_time
                print(f"Specs populated from database for {len(ALL_SPECS_CACHE)} classes.")
                return ALL_SPECS_CACHE
            else:
                print("PlayableSpec table is empty in the database. No specs to load.")
                return {}
    except Exception as e:
        print(f"Error fetching specs from database: {e}")
        return {}

# --- Routes ---
@app.route('/')
def home():
    # [PASTE PREVIOUS home() route code HERE - UNCHANGED]
    # ...
    display_guild_name = GUILD_NAME if GUILD_NAME else "Your Guild"
    current_year = datetime.utcnow().year
    raid_status_counts = {'Tank': 0, 'Healer': 0, 'Melee DPS': 0, 'Ranged DPS': 0, 'DPS':0, 'Total': 0}
    max_heroic_kills = 0; max_mythic_kills = 0
    heroic_total_bosses = 8; mythic_total_bosses = 8
    target_raid_short_name = "Undermine"
    try:
        with app.app_context():
             if db.engine.dialect.has_table(db.engine.connect(), Character.__tablename__):
                wipers = Character.query.filter(Character.status == 'Wiper').all()
                raid_status_counts['Total'] = len(wipers)
                for wiper in wipers:
                    if wiper.role == 'Tank': raid_status_counts['Tank'] += 1
                    elif wiper.role == 'Healer': raid_status_counts['Healer'] += 1
                    elif wiper.role == 'Melee DPS': raid_status_counts['Melee DPS'] += 1
                    elif wiper.role == 'Ranged DPS': raid_status_counts['Ranged DPS'] += 1
                    elif wiper.role == 'DPS': raid_status_counts['DPS'] += 1
                    prog_str = wiper.raid_progression
                    if prog_str and prog_str.startswith(target_raid_short_name + ":"):
                        heroic_match = re.search(r'(\d+)/(\d+)H', prog_str)
                        mythic_match = re.search(r'(\d+)/(\d+)M', prog_str)
                        if heroic_match:
                            kills = int(heroic_match.group(1)); total = int(heroic_match.group(2))
                            max_heroic_kills = max(max_heroic_kills, kills); heroic_total_bosses = total
                        if mythic_match:
                            kills = int(mythic_match.group(1)); total = int(mythic_match.group(2))
                            max_mythic_kills = max(max_mythic_kills, kills); mythic_total_bosses = total
             else: print("Warning: Character table not found when fetching raid status counts.")
    except Exception as e: print(f"Error fetching raid status counts/progression: {e}")
    return render_template(
        'index.html', guild_name=display_guild_name, current_year=current_year,
        raid_status_counts=raid_status_counts, max_heroic_kills=max_heroic_kills,
        heroic_total_bosses=heroic_total_bosses, max_mythic_kills=max_mythic_kills,
        mythic_total_bosses=mythic_total_bosses
    )


@app.route('/roster')
def roster_page():
    # [PASTE PREVIOUS roster_page() route code HERE - UNCHANGED]
    # ...
    start_time = time.time()
    error_message = None
    members = []
    min_item_level = 600
    locale = "en-us" # Default
    if REGION == "eu": locale = "en-gb"
    elif REGION == "kr": locale = "ko-kr"
    elif REGION == "tw": locale = "zh-tw"
    all_specs_by_class = get_all_specs()
    if not all_specs_by_class: print("Warning: Could not load specialization data for dropdowns.")
    try:
        with app.app_context():
            if not db.engine.dialect.has_table(db.engine.connect(), Character.__tablename__):
                 error_message = "Database table not found. Please run the initial setup/update script."
            else:
                db_members = Character.query.filter(
                    Character.rank <= 4, Character.item_level != None, Character.item_level >= min_item_level
                ).order_by(Character.rank.asc(), Character.item_level.desc().nullslast()).all()
                for char in db_members:
                    members.append({
                        'id': char.id, 'name': char.name, 'realm_slug': char.realm_slug, 'level': char.level,
                        'class_id': char.class_id, 'class': char.class_name,
                        'spec_name': char.spec_name if char.spec_name else "N/A",
                        'main_spec_override': char.main_spec_override,
                        'role': char.role if char.role else "N/A", 'status': char.status,
                        'item_level': char.item_level if char.item_level is not None else "N/A",
                        'raid_progression': char.raid_progression if char.raid_progression else "N/A",
                        'rank': char.rank,
                        'raid_attendance_percentage': char.raid_attendance_percentage if char.raid_attendance_percentage is not None else 0.0
                    })
                if not members and not error_message:
                     error_message = f"No members found matching rank criteria (<= 4) and item level (>= {min_item_level})."
    except Exception as e:
        print(f"Error querying database for roster: {e}")
        error_message = "Error retrieving data from the database."
    display_guild_name = GUILD_NAME if GUILD_NAME else "Your Guild"
    load_duration = round(time.time() - start_time, 2)
    print(f"Roster page loaded in {load_duration} seconds.")
    all_specs_json = json.dumps(all_specs_by_class) if all_specs_by_class else '{}'
    return render_template('roster.html',
                           guild_name=display_guild_name, members=members, error_message=error_message,
                           load_duration=load_duration, wow_region=REGION, wow_locale=locale,
                           all_specs_by_class=all_specs_json)

@app.route('/raids')
def raids_page():
    # [PASTE PREVIOUS raids_page() route code HERE - UNCHANGED]
    # ...
    display_guild_name = GUILD_NAME if GUILD_NAME else "Your Guild"
    current_year = datetime.utcnow().year
    reports_by_date = {}
    try:
        with app.app_context():
            if db.engine.dialect.has_table(db.engine.connect(), WCLReport.__tablename__):
                days_to_fetch = 180
                start_date_filter = datetime.utcnow() - timedelta(days=days_to_fetch)
                recent_reports_query = WCLReport.query.filter(WCLReport.start_time >= start_date_filter)\
                                               .order_by(WCLReport.start_time.desc())
                recent_reports = recent_reports_query.all()
                print(f"Found {len(recent_reports)} WCL reports in the last {days_to_fetch} days.")
                for report in recent_reports:
                    if report.start_time:
                        ct_start_time = report.start_time.replace(tzinfo=pytz.utc).astimezone(CENTRAL_TZ)
                        if ct_start_time.weekday() == 2 or ct_start_time.weekday() == 4:
                            date_str = ct_start_time.strftime('%Y-%m-%d')
                            if date_str not in reports_by_date:
                                reports_by_date[date_str] = []
                            reports_by_date[date_str].append({
                                'code': report.code,
                                'title': report.title if report.title else "Untitled Report",
                                'startTime': int(report.start_time.timestamp() * 1000)
                            })
                            reports_by_date[date_str].sort(key=lambda x: x['startTime'])
            else:
                print("Warning: WCLReport table not found for raids page.")
    except Exception as e:
        print(f"Error fetching WCL reports for raids page: {e}")
    return render_template(
        'raids.html',
        guild_name=display_guild_name,
        current_year=current_year,
        reports_by_date_json = json.dumps(reports_by_date)
    )


@app.route('/loot')
def loot_page():
    display_guild_name = GUILD_NAME if GUILD_NAME else "Your Guild"
    current_year = datetime.utcnow().year
    wipers = []
    playable_slots = []
    try:
        with app.app_context():
            if db.engine.dialect.has_table(db.engine.connect(), Character.__tablename__):
                wipers_query = Character.query.filter_by(status='Wiper')\
                                            .order_by(Character.name.asc()).all()
                for char in wipers_query:
                    wipers.append({'id': char.id, 'name': char.name, 'class_name': char.class_name})
            else:
                print("Warning: Character table not found for loot page.")

            if db.engine.dialect.has_table(db.engine.connect(), PlayableSlot.__tablename__):
                playable_slots_query = PlayableSlot.query.order_by(PlayableSlot.display_order.asc()).all()
                for slot in playable_slots_query:
                    if slot.type not in ["SHIRT", "TABARD"]:
                        playable_slots.append({'type': slot.type, 'name': slot.name})
            else:
                print("Warning: PlayableSlot table not found for loot page.")
    except Exception as e:
        print(f"Error fetching data for loot page: {e}")

    return render_template(
        'loot.html',
        guild_name=display_guild_name,
        current_year=current_year,
        wipers=wipers,
        playable_slots=playable_slots
    )

@app.route('/api/character_equipped_items/<int:character_id>')
def api_character_equipped_items(character_id):
    character = Character.query.get(character_id)
    if not character:
        return jsonify({"error": "Character not found"}), 404
    endpoint = f"/profile/wow/character/{character.realm_slug.lower()}/{character.name.lower()}/equipment"
    api_params = {"namespace": f"profile-{REGION}", "locale": "en_US"}
    equipment_data = make_blizzard_api_request(endpoint, params=api_params)
    if equipment_data and "equipped_items" in equipment_data:
        equipped_map = {}
        for item_entry in equipment_data["equipped_items"]:
            slot_type = item_entry.get("slot", {}).get("type")
            item_id = item_entry.get("item", {}).get("id")
            item_name = item_entry.get("name")
            icon_url = None # Placeholder for now
            if slot_type and item_id and item_name:
                equipped_map[slot_type] = {
                    "item_id": item_id, "name": item_name, "icon_url": icon_url
                }
        return jsonify(equipped_map)
    elif equipment_data and "error" in equipment_data:
        return jsonify({"error": f"Blizzard API error: {equipment_data.get('error_description', 'Unknown error')}"}), 500
    else:
        print(f"Could not fetch equipment for character ID {character_id}. API response: {equipment_data}")
        return jsonify({"error": "Could not fetch equipment"}), 500

@app.route('/api/available_items/<slot_type>')
def api_available_items(slot_type):
    """ Fetches all epic items for a given slot type from the database. """
    try:
        with app.app_context():
            # Query items matching the slot_type and quality 'EPIC'
            # Also filter by source if needed, e.g., only raid items
            items_query = Item.query.join(DataSource).filter(
                Item.slot_type == slot_type,
                Item.quality == 'EPIC',
                # Example: Filter by raid source if data_source table is populated
                # DataSource.type == 'Raid' # Or specific raid name
            ).order_by(Item.name.asc()).all()

            items_data = [{
                "id": item.id,
                "name": item.name,
                "icon_url": item.icon_url,
                "source_details": item.source_details
            } for item in items_query]
            return jsonify(items_data)
    except Exception as e:
        print(f"Error fetching available items for slot {slot_type}: {e}")
        return jsonify({"error": "Could not fetch available items"}), 500

@app.route('/api/bis_selection/<int:character_id>/<slot_type>', methods=['GET'])
def get_bis_selection(character_id, slot_type):
    """ Fetches the saved BiS item for a character and slot. """
    try:
        with app.app_context():
            bis_entry = CharacterBiS.query.filter_by(character_id=character_id, slot_type=slot_type).first()
            if bis_entry and bis_entry.item_id:
                return jsonify({"item_id": bis_entry.item_id, "slot_type": bis_entry.slot_type})
            else:
                return jsonify({"item_id": None, "message": "No BiS selection found"}), 404 # Or return empty if preferred
    except Exception as e:
        print(f"Error fetching BiS selection for char {character_id}, slot {slot_type}: {e}")
        return jsonify({"error": "Could not fetch BiS selection"}), 500


@app.route('/api/bis_selection', methods=['POST'])
def save_bis_selection():
    """ Saves or updates a BiS selection for a character and slot. """
    if not request.is_json:
        return jsonify({"success": False, "message": "Request must be JSON"}), 400
    data = request.get_json()
    character_id = data.get('character_id')
    slot_type = data.get('slot_type')
    item_id = data.get('item_id') # Can be None if clearing selection

    if not character_id or not slot_type:
        return jsonify({"success": False, "message": "Missing character_id or slot_type"}), 400
    if item_id is not None and not isinstance(item_id, int): # Allow item_id to be None
         return jsonify({"success": False, "message": "Invalid item_id"}), 400

    try:
        with app.app_context():
            # Check if character and slot exist
            character = Character.query.get(character_id)
            slot = PlayableSlot.query.filter_by(type=slot_type).first()
            if not character: return jsonify({"success": False, "message": "Character not found"}), 404
            if not slot: return jsonify({"success": False, "message": "Slot type not found"}), 404
            if item_id and not Item.query.get(item_id): return jsonify({"success": False, "message": "Item not found"}), 404


            bis_entry = CharacterBiS.query.filter_by(character_id=character_id, slot_type=slot_type).first()
            if item_id is None: # User wants to clear the BiS selection
                if bis_entry:
                    db.session.delete(bis_entry)
                    message = "BiS selection cleared."
                else:
                    message = "No BiS selection to clear." # Or just return success
            elif bis_entry: # Update existing BiS
                bis_entry.item_id = item_id
                bis_entry.last_updated = datetime.utcnow()
                message = "BiS selection updated."
            else: # Create new BiS entry
                bis_entry = CharacterBiS(character_id=character_id, slot_type=slot_type, item_id=item_id)
                db.session.add(bis_entry)
                message = "BiS selection saved."
            db.session.commit()
            return jsonify({"success": True, "message": message})
    except Exception as e:
        db.session.rollback()
        print(f"Error saving BiS selection: {e}")
        return jsonify({"success": False, "message": "Database error during save."}), 500


# --- Update Spec Route ---
@app.route('/update_spec', methods=['POST'])
def update_spec():
    if not request.is_json: abort(400, description="Request must be JSON")
    data = request.get_json()
    character_id = data.get('character_id')
    new_spec_name = data.get('spec_name')
    if character_id is None or not isinstance(character_id, int) or new_spec_name is None: abort(400, description="Invalid character_id or spec_name")
    try:
        with app.app_context():
            character = Character.query.get(character_id)
            if not character: abort(404, description="Character not found")
            all_specs = get_all_specs()
            if new_spec_name and character.class_id in all_specs:
                 valid_specs = [spec['name'] for spec in all_specs[character.class_id]]
                 if new_spec_name not in valid_specs:
                     abort(400, description=f"Invalid spec '{new_spec_name}' for character's class.")
            elif new_spec_name and character.class_id not in all_specs:
                 print(f"Warning: Cannot validate spec '{new_spec_name}' because spec cache/DB is empty for class ID {character.class_id}.")

            character.main_spec_override = new_spec_name if new_spec_name else None
            character.last_updated = datetime.utcnow()
            db.session.commit()
            print(f"Successfully updated spec override for Character ID {character_id} to '{character.main_spec_override}'")
            return jsonify({"success": True, "message": "Main spec updated successfully."})
    except Exception as e:
        db.session.rollback()
        print(f"Error updating spec for character ID {character_id}: {e}")
        abort(500, description="Database error during update.")


# --- Update Status Route ---
@app.route('/update_status', methods=['POST'])
def update_status():
    if not request.is_json:
        abort(400, description="Request must be JSON")
    data = request.get_json()
    character_id = data.get('character_id')
    new_status = data.get('status')
    valid_user_statuses = ['Wiper', 'Member', 'Wiping Alt']
    if not character_id or not isinstance(character_id, int) or not new_status or new_status not in valid_user_statuses:
        print(f"Error: Invalid character_id or status in request data: {data}")
        abort(400, description="Invalid character_id or status")
    try:
        with app.app_context():
            character = Character.query.get(character_id)
            if not character:
                print(f"Error: Character not found with ID: {character_id}")
                abort(404, description="Character not found")
            character.status = new_status
            character.last_updated = datetime.utcnow()
            db.session.commit()
            print(f"Successfully updated status for Character ID {character_id} to '{character.status}'")
            return jsonify({"success": True, "message": "Status updated successfully."})
    except Exception as e:
        db.session.rollback()
        print(f"Error updating status for character ID {character_id}: {e}")
        abort(500, description="Database error during update.")


# --- Main Execution Block ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

