# app.py
# Import necessary libraries
from flask import Flask, render_template, jsonify, request, abort
# Import SQLAlchemy for database interaction
from flask_sqlalchemy import SQLAlchemy
# Import desc for descending sort order AND SQLAlchemy column types
# Import func for count aggregation
from sqlalchemy import desc, Integer, String, DateTime, UniqueConstraint, func, Float
from sqlalchemy.orm import relationship # Ensure relationship is imported for model definitions
import os
import requests # Keep for potential future use or type hints
import time
from datetime import datetime, date # Added date for calendar
import calendar # For calendar generation
import pytz # For timezone handling
import json # For parsing request body
import re # Import regex for parsing progression string

# --- Configuration Loading ---
# Basic app config (can be expanded)
GUILD_NAME = os.environ.get('GUILD_NAME')
REGION = os.environ.get('REGION', 'us').lower() # Needed for Armory URL construction
# API Keys are needed here ONLY if the spec cache needs to be populated by the web app
BLIZZARD_CLIENT_ID = os.environ.get('BLIZZARD_CLIENT_ID')
BLIZZARD_CLIENT_SECRET = os.environ.get('BLIZZARD_CLIENT_SECRET')

# --- Flask Application Setup ---
app = Flask(__name__)

# --- Database Configuration ---
# Get the DATABASE_URL from Heroku environment variables
DATABASE_URL = os.environ.get('DATABASE_URL')
if not DATABASE_URL:
    # Default to a local SQLite file if DATABASE_URL is not set (for local dev)
    print("WARNING: DATABASE_URL environment variable not found. Defaulting to local sqlite:///guild_data.db")
    DATABASE_URL = 'sqlite:///guild_data.db'
else:
    # Heroku Postgres URLs start with 'postgres://', SQLAlchemy prefers 'postgresql://'
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False # Disable modification tracking
db = SQLAlchemy(app) # Initialize SQLAlchemy with the Flask app

# --- Database Models ---
class PlayableClass(db.Model):
    __tablename__ = 'playable_class'
    id = db.Column(db.Integer, primary_key=True) # Blizzard Class ID
    name = db.Column(db.String(50), unique=True, nullable=False)
    # Relationship to PlayableSpec
    specs = db.relationship("PlayableSpec", back_populates="playable_class")
    # Relationship to Character
    characters = db.relationship("Character", back_populates="playable_class")

    def __repr__(self): return f'<PlayableClass {self.name}>'

class PlayableSpec(db.Model):
    __tablename__ = 'playable_spec'
    id = db.Column(db.Integer, primary_key=True) # Blizzard Spec ID
    name = db.Column(db.String(50), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey('playable_class.id'), nullable=False)
    # Relationship to PlayableClass
    playable_class = db.relationship("PlayableClass", back_populates="specs")

    def __repr__(self): return f'<PlayableSpec {self.name} (Class ID: {self.class_id})>'

class Character(db.Model):
    __tablename__ = 'character' # Explicit table name recommended
    # Use imported types (Integer, String, DateTime)
    id = db.Column(Integer, primary_key=True) # Use Blizzard's character ID
    name = db.Column(String(100), nullable=False)
    realm_slug = db.Column(String(100), nullable=False) # Needed for Armory link
    level = db.Column(Integer)
    class_id = db.Column(Integer, db.ForeignKey('playable_class.id'))
    class_name = db.Column(String(50))
    # race_name removed
    spec_name = db.Column(String(50)) # API Active Spec
    main_spec_override = db.Column(String(50), nullable=True) # User override
    role = db.Column(String(15))      # Role (Tank, Healer, Melee DPS, Ranged DPS)
    status = db.Column(String(15), nullable=False, index=True) # Calculated/User Status field
    item_level = db.Column(Integer, index=True) # Index item_level for filtering/sorting
    raid_progression = db.Column(String(200)) # Store summary string
    rank = db.Column(Integer, index=True) # Index rank for faster filtering
    last_updated = db.Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow) # Use utcnow
    # WCL Data
    raid_attendance_percentage = db.Column(Float, default=0.0, nullable=True)
    avg_wcl_performance = db.Column(Float, nullable=True)

    # Define a unique constraint on name and realm_slug
    __table_args__ = (db.UniqueConstraint('name', 'realm_slug', name='_name_realm_uc'),)
    playable_class = db.relationship("PlayableClass", back_populates="characters") # Relationship
    # attendances = db.relationship("WCLAttendance", back_populates="character") # Assuming WCLAttendance model exists

    def __repr__(self):
        return f'<Character {self.name}-{self.realm_slug}>'

# WCL Report Model (ensure it's defined for querying)
class WCLReport(db.Model):
    __tablename__ = 'wcl_report'
    code = db.Column(String(50), primary_key=True)
    title = db.Column(String(200))
    start_time = db.Column(DateTime, index=True) # Stored as UTC
    end_time = db.Column(DateTime)
    owner_name = db.Column(String(100))
    fetched_at = db.Column(DateTime, default=datetime.utcnow)
    # attendances = db.relationship("WCLAttendance", back_populates="report") # Assuming WCLAttendance model exists

    def __repr__(self): return f'<WCLReport {self.code} ({self.title})>'


# --- Data Caching & API Config (for Specs) ---
ALL_SPECS_CACHE = {}
ALL_SPECS_LAST_FETCHED = 0
CACHE_TTL = 3600 * 6
WEB_APP_ACCESS_TOKEN_CACHE = {"token": None, "expires_at": 0}
API_BASE_URL = f"https://{REGION}.api.blizzard.com"
TOKEN_URL = f"https://{REGION}.battle.net/oauth/token"
CENTRAL_TZ = pytz.timezone('America/Chicago')


# --- API Helper Functions (for Web App Context - Spec Fetch) ---
def get_web_app_token():
    """ Gets/refreshes token specifically for web app needs (like spec fetching). """
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

def make_web_api_request(api_url, params, headers):
    """ Helper function to make API requests within web app context """
    try:
        response = requests.get(api_url, params=params, headers=headers, timeout=15)
        if response.status_code == 404:
             print(f"Warning (Web App): 404 Not Found for URL: {response.url}")
             return None
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error in make_web_api_request for {api_url}: {e}")
        return None

def get_all_specs():
    """
    Fetches and caches all playable specializations from the database.
    If DB is empty or cache is stale, it will attempt to use Blizzard API (if configured).
    """
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
                    if spec.class_id not in temp_spec_map:
                        temp_spec_map[spec.class_id] = []
                    temp_spec_map[spec.class_id].append({"id": spec.id, "name": spec.name})
                for cid in temp_spec_map:
                    temp_spec_map[cid].sort(key=lambda x: x['name'])
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
    """ Route for the homepage ('/'). Fetches raid status counts and max progression. """
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
    """ Route for the roster page ('/roster'). """
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
    """ Route for the raid calendar page. """
    display_guild_name = GUILD_NAME if GUILD_NAME else "Your Guild"
    current_year = datetime.utcnow().year
    reports_by_date = {} # { "YYYY-MM-DD": [report1, report2, ...] }

    try:
        with app.app_context():
            if db.engine.dialect.has_table(db.engine.connect(), WCLReport.__tablename__):
                three_months_ago = datetime.utcnow() - timedelta(days=90)
                recent_reports = WCLReport.query.filter(WCLReport.start_time >= three_months_ago)\
                                               .order_by(WCLReport.start_time.desc()).all()
                for report in recent_reports:
                    if report.start_time:
                        ct_start_time = report.start_time.replace(tzinfo=pytz.utc).astimezone(CENTRAL_TZ)
                        if ct_start_time.weekday() == 2 or ct_start_time.weekday() == 4:
                            date_str = ct_start_time.strftime('%Y-%m-%d')
                            if date_str not in reports_by_date:
                                reports_by_date[date_str] = []
                            reports_by_date[date_str].append({
                                'code': report.code,
                                'title': report.title,
                                'startTime': int(report.start_time.timestamp() * 1000)
                            })
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

