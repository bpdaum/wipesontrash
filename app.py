# app.py
# Import necessary libraries
from flask import Flask, render_template, jsonify, request, abort
# Import SQLAlchemy for database interaction
from flask_sqlalchemy import SQLAlchemy
# Import desc for descending sort order AND SQLAlchemy column types
from sqlalchemy import desc, Integer, String, DateTime
import os
import requests # Keep for potential future use or type hints
import time
from datetime import datetime # Needed for Character model timestamp
import json # For parsing request body

# --- Configuration Loading ---
# Basic app config (can be expanded)
GUILD_NAME = os.environ.get('GUILD_NAME')
REGION = os.environ.get('REGION', 'us').lower() # Needed for Armory URL construction

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

# --- Database Model (with class_id and override) ---
# Defines the structure for storing character data in the database.
# This MUST match the definition in update_roster_data.py
class Character(db.Model):
    __tablename__ = 'character' # Explicit table name recommended
    id = db.Column(db.Integer, primary_key=True) # Use Blizzard's character ID
    name = db.Column(db.String(100), nullable=False)
    realm_slug = db.Column(db.String(100), nullable=False) # Needed for Armory link
    level = db.Column(db.Integer)
    class_id = db.Column(db.Integer) # NEW: Store the class ID
    class_name = db.Column(db.String(50))
    race_name = db.Column(db.String(50))
    spec_name = db.Column(String(50)) # API Active Spec
    main_spec_override = db.Column(String(50), nullable=True) # NEW: User override
    role = db.Column(String(10))      # Role (Tank, Healer, DPS)
    item_level = db.Column(db.Integer, index=True) # Index item_level for filtering/sorting
    raid_progression = db.Column(db.String(200)) # Store summary string
    rank = db.Column(db.Integer, index=True) # Index rank for faster filtering
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow) # Use utcnow

    # Define a unique constraint on name and realm_slug
    __table_args__ = (db.UniqueConstraint('name', 'realm_slug', name='_name_realm_uc'),)

    def __repr__(self):
        return f'<Character {self.name}-{self.realm_slug}>'

# --- Data Caching (for Specs) ---
# Simple in-memory cache for all specs, populated on first roster load
ALL_SPECS_CACHE = {} # Structure: {class_id: [{id: spec_id, name: spec_name}, ...]}
ALL_SPECS_LAST_FETCHED = 0
CACHE_TTL = 3600 * 6 # Cache specs for 6 hours (adjust as needed)

def get_all_specs():
    """
    Fetches and caches all playable specializations from Blizzard API.
    Requires BLIZZARD_CLIENT_ID and BLIZZARD_CLIENT_SECRET env vars to be set
    for the web dyno if the cache needs to be populated.
    Returns a dictionary mapping class_id to a list of its specs.
    """
    global ALL_SPECS_CACHE, ALL_SPECS_LAST_FETCHED
    current_time = time.time()

    # Check cache validity
    if ALL_SPECS_CACHE and (current_time - ALL_SPECS_LAST_FETCHED < CACHE_TTL):
        print("Using cached specs.")
        return ALL_SPECS_CACHE

    print("Fetching all playable specializations from API...")
    # Need API access here - temporarily copy relevant functions or use a shared utility
    # ---- Temporary API Access ----
    temp_access_token_cache = {"token": None, "expires_at": 0}
    def get_temp_token():
        # Simplified token fetch for this function
        global temp_access_token_cache
        now = time.time()
        if temp_access_token_cache["token"] and temp_access_token_cache["expires_at"] > now + 60:
            return temp_access_token_cache["token"]
        client_id = os.environ.get('BLIZZARD_CLIENT_ID')
        client_secret = os.environ.get('BLIZZARD_CLIENT_SECRET')
        if not client_id or not client_secret:
            print("Error: Blizzard API credentials not configured for spec fetch.")
            return None
        try:
            token_url = f"https://{REGION}.battle.net/oauth/token"
            res = requests.post(token_url, auth=(client_id, client_secret), data={'grant_type': 'client_credentials'}, timeout=10)
            res.raise_for_status()
            data = res.json()
            temp_access_token_cache["token"] = data.get('access_token')
            temp_access_token_cache["expires_at"] = now + data.get('expires_in', 0)
            return temp_access_token_cache["token"]
        except Exception as e:
            print(f"Error fetching temp token for specs: {e}")
            return None

    access_token = get_temp_token()
    if not access_token:
        print("Error: Cannot fetch specs without Blizzard API access token.")
        return ALL_SPECS_CACHE # Return old cache if fetch fails

    api_url = f"{API_BASE_URL}/data/wow/playable-specialization/index"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"namespace": f"static-{REGION}", "locale": "en_US"}
    # ---- End Temporary API Access ----

    try:
        response = requests.get(api_url, params=params, headers=headers, timeout=15)
        response.raise_for_status()
        spec_index_data = response.json()

        if not spec_index_data or 'character_specializations' not in spec_index_data:
            print("Error: Failed to parse playable specialization index from API.")
            return ALL_SPECS_CACHE # Return old cache

        temp_spec_map = {}
        for spec_info in spec_index_data.get('character_specializations', []):
            class_id = spec_info.get('playable_class', {}).get('id')
            spec_id = spec_info.get('id')
            spec_name = spec_info.get('name')

            if class_id and spec_id and spec_name:
                if class_id not in temp_spec_map:
                    temp_spec_map[class_id] = []
                # Store as dictionary for potential future use (e.g., spec ID)
                temp_spec_map[class_id].append({"id": spec_id, "name": spec_name})
                # Sort specs alphabetically within each class
                temp_spec_map[class_id].sort(key=lambda x: x['name'])

        if not temp_spec_map:
            print("Error: Could not build specialization map from fetched data.")
            return ALL_SPECS_CACHE # Return old cache

        ALL_SPECS_CACHE = temp_spec_map
        ALL_SPECS_LAST_FETCHED = current_time
        print(f"All specs cache populated for {len(ALL_SPECS_CACHE)} classes.")
        return ALL_SPECS_CACHE

    except Exception as e:
        print(f"Error fetching/processing all specs: {e}")
        return ALL_SPECS_CACHE # Return old cache on error

# --- Routes ---
@app.route('/')
def home():
    """ Route for the homepage ('/'). """
    display_guild_name = GUILD_NAME if GUILD_NAME else "Your Guild"
    return render_template('index.html', guild_name=display_guild_name)

@app.route('/roster')
def roster_page():
    """
    Route for the roster page ('/roster'). Fetches data from DB.
    Passes all specs data to template for dropdowns.
    """
    start_time = time.time()
    error_message = None
    members = []
    min_item_level = 630 # Define the minimum item level threshold
    # Determine locale based on region for Armory URL
    locale = "en-us" # Default
    if REGION == "eu": locale = "en-gb"
    elif REGION == "kr": locale = "ko-kr"
    elif REGION == "tw": locale = "zh-tw"

    # Fetch all specs needed for dropdowns
    # Requires Blizzard API keys set for the web dyno if cache is empty
    all_specs_by_class = get_all_specs()
    if not all_specs_by_class:
         # Decide how to handle this - maybe disable editing?
         print("Warning: Could not load specialization data for dropdowns.")
         # error_message = "Could not load specialization options." # Optional user message

    try:
        # Ensure the app context is available for database operations
        with app.app_context():
            # Check if the table exists before querying
            if not db.engine.dialect.has_table(db.engine.connect(), Character.__tablename__):
                 error_message = "Database table not found. Please run the initial setup/update script."
                 print(error_message) # Log the error
            else:
                # Query the database
                db_members = Character.query.filter(
                    Character.rank <= 4,
                    Character.item_level != None,
                    Character.item_level >= min_item_level
                ).order_by(
                    Character.rank.asc(),
                    Character.item_level.desc().nullslast() # Handles potential NULLs in sorting
                ).all()

                # Convert SQLAlchemy objects to dictionaries for the template
                for char in db_members:
                    members.append({
                        'id': char.id, # Pass character ID for updates
                        'name': char.name,
                        'realm_slug': char.realm_slug,
                        'level': char.level,
                        'class_id': char.class_id, # Pass class ID for spec dropdown filtering
                        'class': char.class_name,
                        'race': char.race_name,
                        'spec_name': char.spec_name if char.spec_name else "N/A", # API Spec
                        'main_spec_override': char.main_spec_override, # User Override Spec
                        'role': char.role if char.role else "N/A",
                        'item_level': char.item_level if char.item_level is not None else "N/A",
                        'raid_progression': char.raid_progression if char.raid_progression else "N/A",
                        'rank': char.rank
                    })

                if not members and not error_message: # Avoid overwriting DB error
                     print(f"Warning: Character table exists but found no members matching filters.")
                     error_message = f"No members found matching rank criteria (<= 4) and item level (>= {min_item_level})."

    except Exception as e:
        # Catch potential database errors
        print(f"Error querying database: {e}")
        error_message = "Error retrieving data from the database."


    display_guild_name = GUILD_NAME if GUILD_NAME else "Your Guild"
    end_time = time.time()
    load_duration = round(end_time - start_time, 2)
    print(f"Roster page loaded from DB in {load_duration} seconds.")

    # Pass the specs data to the template, safely dumping to JSON
    try:
        all_specs_json = json.dumps(all_specs_by_class)
    except Exception as json_err:
        print(f"Error dumping specs to JSON: {json_err}")
        all_specs_json = '{}' # Pass empty JSON object on error

    return render_template('roster.html',
                           guild_name=display_guild_name,
                           members=members,
                           error_message=error_message,
                           load_duration=load_duration,
                           wow_region=REGION,
                           wow_locale=locale,
                           all_specs_by_class=all_specs_json) # Pass specs as JSON string

# --- Update Spec Route ---
@app.route('/update_spec', methods=['POST'])
def update_spec():
    """ Handles AJAX request to update a character's main_spec_override. """
    if not request.is_json:
        print("Error: Request was not JSON")
        abort(400, description="Request must be JSON") # Bad request

    data = request.get_json()
    character_id = data.get('character_id')
    new_spec_name = data.get('spec_name') # This will be "" if "-- Clear Override --" selected

    # Basic validation
    if character_id is None or not isinstance(character_id, int) or new_spec_name is None:
        print(f"Error: Invalid character_id or spec_name in request data: {data}")
        abort(400, description="Invalid character_id or spec_name")

    try:
        with app.app_context():
            character = Character.query.get(character_id)
            if not character:
                print(f"Error: Character not found with ID: {character_id}")
                abort(404, description="Character not found") # Not found

            # Optional: Validate if the new_spec_name is valid for the character's class
            # Requires fetching the spec map again or ensuring it's available globally/cached
            all_specs = get_all_specs() # Use cached/fetched specs
            if new_spec_name and character.class_id in all_specs:
                 valid_specs = [spec['name'] for spec in all_specs[character.class_id]]
                 if new_spec_name not in valid_specs:
                     print(f"Error: Invalid spec '{new_spec_name}' for class ID {character.class_id}")
                     abort(400, description=f"Invalid spec '{new_spec_name}' for character's class.")

            # Update the override (set to None if empty string received, otherwise use the name)
            character.main_spec_override = new_spec_name if new_spec_name else None
            character.last_updated = datetime.utcnow() # Manually update timestamp

            db.session.commit()
            print(f"Successfully updated spec override for Character ID {character_id} to '{character.main_spec_override}'")
            return jsonify({"success": True, "message": "Main spec updated successfully."})

    except Exception as e:
        db.session.rollback() # Rollback on error
        print(f"Error updating spec for character ID {character_id}: {e}")
        abort(500, description="Database error during update.") # Internal server error

# --- Main Execution Block ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    # When running locally for the first time without DATABASE_URL,
    # the update script should handle table creation.
    # You might need to ensure Blizzard API keys are set locally if the spec cache is empty.
    app.run(host='0.0.0.0', port=port, debug=False)
