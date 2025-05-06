# app.py
# Import necessary libraries
from flask import Flask, render_template, jsonify
# Import SQLAlchemy for database interaction
from flask_sqlalchemy import SQLAlchemy
# Import desc for descending sort order
from sqlalchemy import desc
import os
import requests # Still needed for type hints or potential future use
import time
from datetime import datetime # Needed for Character model timestamp

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

# --- Database Model ---
# Defines the structure for storing character data in the database.
# This MUST match the definition in update_roster_data.py
class Character(db.Model):
    __tablename__ = 'character' # Explicit table name recommended
    id = db.Column(db.Integer, primary_key=True) # Use Blizzard's character ID
    name = db.Column(db.String(100), nullable=False)
    realm_slug = db.Column(db.String(100), nullable=False) # Needed for Armory link
    level = db.Column(db.Integer)
    class_name = db.Column(db.String(50))
    race_name = db.Column(db.String(50))
    spec_name = db.Column(db.String(50)) # Active Specialization Name
    role = db.Column(db.String(10))      # Role (Tank, Healer, DPS)
    item_level = db.Column(db.Integer, index=True) # Index item_level for filtering/sorting
    raid_progression = db.Column(db.String(200)) # Store summary string
    rank = db.Column(db.Integer, index=True) # Index rank for faster filtering
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow) # Use utcnow

    # Define a unique constraint on name and realm_slug
    __table_args__ = (db.UniqueConstraint('name', 'realm_slug', name='_name_realm_uc'),)

    def __repr__(self):
        return f'<Character {self.name}-{self.realm_slug}>'

# --- Routes ---
@app.route('/')
def home():
    """ Route for the homepage ('/'). """
    display_guild_name = GUILD_NAME if GUILD_NAME else "Your Guild"
    return render_template('index.html', guild_name=display_guild_name)

@app.route('/roster')
def roster_page():
    """
    Route for the roster page ('/roster').
    Fetches character data including spec/role from the PostgreSQL database,
    filtering by rank <= 4 and item_level >= 600. Sorts by rank (asc) then item_level (desc).
    Includes realm_slug for Armory links.
    """
    start_time = time.time()
    error_message = None
    members = []
    min_item_level = 600 # Define the minimum item level threshold
    # Determine locale based on region for Armory URL
    locale = "en-us" # Default
    if REGION == "eu":
        locale = "en-gb"
    elif REGION == "kr":
        locale = "ko-kr"
    elif REGION == "tw":
         locale = "zh-tw"

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
                    Character.item_level.desc().nullslast()
                ).all()

                # Convert SQLAlchemy objects to dictionaries for the template
                for char in db_members:
                    members.append({
                        'name': char.name,
                        'realm_slug': char.realm_slug,
                        'level': char.level,
                        'class': char.class_name,
                        'race': char.race_name,
                        'spec_name': char.spec_name if char.spec_name else "N/A", # Fetch spec
                        'role': char.role if char.role else "N/A",               # Fetch role
                        'item_level': char.item_level if char.item_level is not None else "N/A",
                        'raid_progression': char.raid_progression if char.raid_progression else "N/A",
                        'rank': char.rank
                    })

                if not members:
                     print(f"Warning: Character table exists but found no members matching rank <= 4 AND item_level >= {min_item_level}.")
                     error_message = f"No members found matching rank criteria (<= 4) and item level (>= {min_item_level})."

    except Exception as e:
        # Catch potential database errors
        print(f"Error querying database: {e}")
        error_message = "Error retrieving data from the database. Has the update script run successfully?"


    display_guild_name = GUILD_NAME if GUILD_NAME else "Your Guild"
    end_time = time.time()
    load_duration = round(end_time - start_time, 2)
    print(f"Roster page loaded from DB in {load_duration} seconds.")

    # Pass the filtered and sorted list of member dictionaries to the template
    return render_template('roster.html',
                           guild_name=display_guild_name,
                           members=members,
                           error_message=error_message,
                           load_duration=load_duration, # Pass duration (optional)
                           wow_region=REGION, # Pass region for Armory URL
                           wow_locale=locale) # Pass locale for Armory URL

# --- Main Execution Block ---
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
