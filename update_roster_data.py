# update_roster_data.py
import os
import requests
import time
from datetime import datetime, timedelta
import json
import pytz # For timezone handling
import re

# --- Standalone SQLAlchemy setup ---
from sqlalchemy import create_engine, Column, Integer, String, DateTime, UniqueConstraint, MetaData, Index, ForeignKey, Float
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy.sql import func
from sqlalchemy.exc import OperationalError, IntegrityError

# Get DB URI from environment or default to local SQLite
DATABASE_URI = os.environ.get('DATABASE_URL')
if not DATABASE_URI:
    print("WARNING: DATABASE_URL environment variable not found. Defaulting to local sqlite:///guild_data.db")
    DATABASE_URI = 'sqlite:///guild_data.db'
else:
    if DATABASE_URI.startswith("postgres://"):
        DATABASE_URI = DATABASE_URI.replace("postgres://", "postgresql://", 1)

try:
    engine = create_engine(DATABASE_URI)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    Base = declarative_base()
    metadata = MetaData()
except ImportError as e:
     print(f"Error: Database driver likely missing. {e}")
     exit(1)
except Exception as e:
     print(f"Error creating database engine: {e}")
     exit(1)

# --- Database Models ---
class Character(Base):
    __tablename__ = 'character'
    id = Column(Integer, primary_key=True) # Blizzard Character ID
    name = Column(String(100), nullable=False)
    realm_slug = Column(String(100), nullable=False)
    level = Column(Integer)
    class_id = Column(Integer, ForeignKey('playable_class.id'))
    class_name = Column(String(50))
    spec_name = Column(String(50)) # API Active Spec
    main_spec_override = Column(String(50), nullable=True) # User override
    role = Column(String(15))      # e.g., Tank, Healer, Melee DPS, Ranged DPS
    status = Column(String(15), nullable=False, index=True) # Calculated/User Status field
    item_level = Column(Integer, index=True)
    raid_progression = Column(String(200))
    rank = Column(Integer, index=True)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    raid_attendance_percentage = Column(Float, default=0.0, nullable=True)
    avg_wcl_performance = Column(Float, nullable=True) # Average performance percentile

    attendances = relationship("WCLAttendance", back_populates="character")
    performances = relationship("WCLPerformance", back_populates="character") # New relationship
    playable_class = relationship("PlayableClass", back_populates="characters")

    __table_args__ = ( UniqueConstraint('name', 'realm_slug', name='_name_realm_uc'), )
    def __repr__(self): return f'<Character {self.name}-{self.realm_slug}>'

class WCLReport(Base):
    __tablename__ = 'wcl_report'
    code = Column(String(50), primary_key=True) # WCL Report Code
    title = Column(String(200))
    start_time = Column(DateTime, index=True) # Store as UTC DateTime
    end_time = Column(DateTime)
    owner_name = Column(String(100))
    fetched_at = Column(DateTime, default=datetime.utcnow)
    attendances = relationship("WCLAttendance", back_populates="report")
    performances = relationship("WCLPerformance", back_populates="report") # New relationship
    def __repr__(self): return f'<WCLReport {self.code} ({self.title})>'

class WCLAttendance(Base):
    __tablename__ = 'wcl_attendance'
    id = Column(Integer, primary_key=True) # Simple primary key
    report_code = Column(String(50), ForeignKey('wcl_report.code'), nullable=False, index=True)
    character_id = Column(Integer, ForeignKey('character.id'), nullable=False, index=True) # Link to Blizzard Character ID
    report = relationship("WCLReport", back_populates="attendances")
    character = relationship("Character", back_populates="attendances")
    __table_args__ = ( UniqueConstraint('report_code', 'character_id', name='_report_char_uc'), )
    def __repr__(self): return f'<WCLAttendance Report={self.report_code} CharacterID={self.character_id}>'

# NEW: WCL Performance Table
class WCLPerformance(Base):
    __tablename__ = 'wcl_performance'
    id = Column(Integer, primary_key=True)
    report_code = Column(String(50), ForeignKey('wcl_report.code'), nullable=False, index=True)
    character_id = Column(Integer, ForeignKey('character.id'), nullable=False, index=True)
    encounter_id = Column(Integer, nullable=False) # WCL Encounter ID
    encounter_name = Column(String(100))
    spec_name = Column(String(50)) # Spec used for this performance
    metric = Column(String(20)) # e.g., "dps", "hps", "bossdps"
    rank_percentile = Column(Float)

    report = relationship("WCLReport", back_populates="performances")
    character = relationship("Character", back_populates="performances")
    __table_args__ = ( UniqueConstraint('report_code', 'character_id', 'encounter_id', 'metric', name='_perf_uc'), )
    def __repr__(self): return f'<WCLPerformance Report={self.report_code} CharID={self.character_id} Enc={self.encounter_name} Metric={self.metric} Perf={self.rank_percentile}>'


class PlayableClass(Base):
    __tablename__ = 'playable_class'
    id = Column(Integer, primary_key=True) # Blizzard Class ID
    name = Column(String(50), unique=True, nullable=False)
    specs = relationship("PlayableSpec", back_populates="playable_class")
    characters = relationship("Character", back_populates="playable_class")
    def __repr__(self): return f'<PlayableClass {self.name}>'

class PlayableSpec(Base):
    __tablename__ = 'playable_spec'
    id = Column(Integer, primary_key=True) # Blizzard Spec ID
    name = Column(String(50), nullable=False)
    class_id = Column(Integer, ForeignKey('playable_class.id'), nullable=False)
    playable_class = relationship("PlayableClass", back_populates="specs")
    def __repr__(self): return f'<PlayableSpec {self.name} (Class ID: {self.class_id})>'


# --- Configuration Loading ---
BLIZZARD_CLIENT_ID = os.environ.get('BLIZZARD_CLIENT_ID')
BLIZZARD_CLIENT_SECRET = os.environ.get('BLIZZARD_CLIENT_SECRET')
GUILD_NAME = os.environ.get('GUILD_NAME')
REALM_SLUG = os.environ.get('REALM_SLUG')
REGION = os.environ.get('REGION', 'us').lower()
WCL_CLIENT_ID = os.environ.get('WCL_CLIENT_ID')
WCL_CLIENT_SECRET = os.environ.get('WCL_CLIENT_SECRET')
WCL_GUILD_ID = os.environ.get('WCL_GUILD_ID')

# --- API Configuration ---
VALID_REGIONS = ['us', 'eu', 'kr', 'tw']
if REGION not in VALID_REGIONS: raise ValueError(f"Invalid REGION: {REGION}. Must be one of {VALID_REGIONS}")
BLIZZARD_TOKEN_URL = f"https://{REGION}.battle.net/oauth/token"
BLIZZARD_API_BASE_URL = f"https://{REGION}.api.blizzard.com"
WCL_TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"
WCL_API_ENDPOINT = "https://www.warcraftlogs.com/api/v2/client"

# --- Caching ---
blizzard_access_token_cache = { "token": None, "expires_at": 0 }
wcl_access_token_cache = { "token": None, "expires_at": 0 }
CLASS_MAP = {} # Will be populated by update_static_tables from DB
SPEC_MAP_BY_CLASS = {} # Will be populated by update_static_tables from DB

# --- Timezone ---
CENTRAL_TZ = pytz.timezone('America/Chicago')

# --- Role Definitions ---
TANK_SPECS = ["Blood", "Protection", "Guardian", "Brewmaster", "Vengeance"]
HEALER_SPECS = ["Holy", "Discipline", "Restoration", "Mistweaver", "Preservation"]
MELEE_DPS_SPECS = {
    "Warrior": ["Arms", "Fury"], "Paladin": ["Retribution"], "Death Knight": ["Frost", "Unholy"],
    "Shaman": ["Enhancement"], "Hunter": ["Survival"], "Rogue": ["Assassination", "Outlaw", "Subtlety"],
    "Monk": ["Windwalker"], "Demon Hunter": ["Havoc"], "Druid": ["Feral"]
}
RANGED_DPS_SPECS = {
    "Mage": ["Arcane", "Fire", "Frost"], "Warlock": ["Affliction", "Demonology", "Destruction"],
    "Priest": ["Shadow"], "Hunter": ["Beast Mastery", "Marksmanship"], "Druid": ["Balance"],
    "Shaman": ["Elemental"], "Evoker": ["Devastation", "Augmentation"]
}


# --- API Helper Functions ---

def get_blizzard_access_token():
    """ Retrieves Blizzard access token, uses cache. """
    global blizzard_access_token_cache
    current_time = time.time()
    if blizzard_access_token_cache["token"] and blizzard_access_token_cache["expires_at"] > current_time + 60:
        return blizzard_access_token_cache["token"]
    if not BLIZZARD_CLIENT_ID or not BLIZZARD_CLIENT_SECRET:
        print("Error: BLIZZARD_CLIENT_ID or BLIZZARD_CLIENT_SECRET not set.")
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
            print(f"Error: Could not retrieve Blizzard access token. Response: {token_data}")
            return None
        blizzard_access_token_cache["token"] = access_token
        blizzard_access_token_cache["expires_at"] = current_time + expires_in
        print(f"New Blizzard access token obtained.")
        return access_token
    except requests.exceptions.RequestException as e:
        print(f"Error getting Blizzard access token: {e}")
        if e.response is not None:
            print(f"Response Status: {e.response.status_code}")
            try: print(f"Response Body: {e.response.json()}")
            except: print(f"Response Body: {e.response.text}")
        return None
    except Exception as e:
        print(f"An unexpected error during Blizzard token retrieval: {e}")
        return None

def get_wcl_access_token():
    """ Retrieves Warcraft Logs access token, uses cache. """
    global wcl_access_token_cache
    current_time = time.time()
    if wcl_access_token_cache["token"] and wcl_access_token_cache["expires_at"] > current_time + 60:
        return wcl_access_token_cache["token"]

    if not WCL_CLIENT_ID or not WCL_CLIENT_SECRET:
        print("Error: WCL_CLIENT_ID or WCL_CLIENT_SECRET not set in environment variables.")
        return None

    try:
        print(f"Attempting to get WCL token from: {WCL_TOKEN_URL}")
        response = requests.post(
            WCL_TOKEN_URL,
            auth=(WCL_CLIENT_ID, WCL_CLIENT_SECRET),
            data={'grant_type': 'client_credentials'}
        )
        response.raise_for_status()
        token_data = response.json()
        access_token = token_data.get('access_token')
        expires_in = token_data.get('expires_in', 0)

        if not access_token:
            print(f"Error: Could not retrieve WCL access token. Response: {token_data}")
            return None

        wcl_access_token_cache["token"] = access_token
        wcl_access_token_cache["expires_at"] = current_time + expires_in
        print(f"New Warcraft Logs access token obtained.")
        return access_token
    except requests.exceptions.RequestException as e:
        print(f"Error getting WCL access token: {e}")
        if e.response is not None:
            print(f"WCL Token Response Status: {e.response.status_code}")
            try:
                print(f"WCL Token Response Body: {e.response.json()}")
            except requests.exceptions.JSONDecodeError:
                print(f"WCL Token Response Body: {e.response.text}")
        return None
    except Exception as e:
        print(f"An unexpected error during WCL token retrieval: {e}")
        return None


def make_api_request(api_url, params, headers, is_wcl=False, wcl_query=None, wcl_variables=None, max_retries=3, retry_delay=5):
    """ Helper function to make API requests with retries for transient errors. """
    for attempt in range(max_retries):
        try:
            if is_wcl:
                if not wcl_query:
                    print("Error: WCL query missing for GraphQL request.")
                    return None
                json_payload = {'query': wcl_query}
                if wcl_variables:
                    json_payload['variables'] = wcl_variables
                response = requests.post(api_url, json=json_payload, headers=headers, timeout=30)
            else:
                response = requests.get(api_url, params=params, headers=headers, timeout=30)

            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()

        except requests.exceptions.Timeout:
            print(f"Timeout error during API request to {api_url}. Attempt {attempt + 1}/{max_retries}.")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                print(f"Max retries reached for timeout at {api_url}.")
                return None
        except requests.exceptions.HTTPError as e:
            if e.response.status_code in [500, 502, 503, 504] and attempt < max_retries - 1:
                print(f"HTTP Error {e.response.status_code} for {api_url}. Attempt {attempt + 1}/{max_retries}. Retrying in {retry_delay}s...")
                time.sleep(retry_delay)
            else:
                print(f"HTTP Error during API request: {e}")
                print(f"URL attempted: {e.request.url}")
                print(f"Response Status: {e.response.status_code}")
                try: print(f"Response Body: {e.response.json()}")
                except: print(f"Response Body: {e.response.text}")
                return None
        except requests.exceptions.RequestException as e:
            print(f"Network error during API request: {e}. Attempt {attempt + 1}/{max_retries}.")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
            else:
                print(f"Max retries reached for network error at {api_url}.")
                return None
        except Exception as e:
            print(f"An unexpected error occurred during API request: {e}")
            return None
    return None


def get_static_data(endpoint, use_base_url=True):
    """
    Fetches static data (like class/spec indexes) from Blizzard API.
    Can fetch from a full URL if use_base_url is False.
    """
    access_token = get_blizzard_access_token()
    if not access_token: return None

    if use_base_url:
        api_url = f"{BLIZZARD_API_BASE_URL}/data/wow{endpoint if endpoint.startswith('/') else '/' + endpoint}"
    else:
        api_url = endpoint

    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"namespace": f"static-{REGION}", "locale": "en_US"}
    data = make_api_request(api_url, params, headers)
    return data


def update_static_tables(db_session):
    """ Fetches and updates PlayableClass and PlayableSpec tables. """
    print("Updating static PlayableClass and PlayableSpec tables...")
    class_success = False
    spec_success = False

    # 1. Update Playable Classes
    print("Fetching playable class index...")
    class_index_data = get_static_data('/playable-class/index')
    if class_index_data and 'classes' in class_index_data:
        classes_to_add = []
        for class_info in class_index_data['classes']:
            if class_info.get('id') and class_info.get('name'):
                classes_to_add.append(PlayableClass(id=class_info['id'], name=class_info['name']))
        if classes_to_add:
            db_session.add_all(classes_to_add)
            print(f"PlayableClass table prepared with {len(classes_to_add)} entries.")
            class_success = True
        else:
            print("No class data to add.")
    else:
        print("Error: Failed to fetch or parse playable class index.")

    # 2. Update Playable Specializations
    print("Fetching playable specialization index...")
    spec_index_data = get_static_data('/playable-specialization/index')
    if spec_index_data and 'character_specializations' in spec_index_data:
        specs_to_add = []
        fetch_errors = 0
        processed_count = 0
        spec_list = spec_index_data['character_specializations']
        print(f"Fetched {len(spec_list)} specializations from index. Fetching details for class IDs...")

        for spec_info_from_index in spec_list:
            spec_id = spec_info_from_index.get('id')
            spec_name = spec_info_from_index.get('name')
            detail_href = spec_info_from_index.get('key', {}).get('href')

            if not spec_id or not spec_name or not detail_href: continue

            spec_detail_data = get_static_data(detail_href, use_base_url=False)
            processed_count +=1
            if spec_detail_data:
                class_info = spec_detail_data.get('playable_class', {})
                class_id = class_info.get('id')
                if class_id:
                    specs_to_add.append(PlayableSpec(id=spec_id, name=spec_name, class_id=class_id))
                else:
                    print(f"Warning: No class_id for spec {spec_name} (ID: {spec_id})")
                    fetch_errors +=1
            else:
                print(f"Warning: Failed to fetch details for spec {spec_name} (ID: {spec_id})")
                fetch_errors +=1
            if processed_count % 10 == 0: print(f"Processed details for {processed_count}/{len(spec_list)} specs...")
            time.sleep(0.05)

        if specs_to_add:
            db_session.add_all(specs_to_add)
            print(f"PlayableSpec table prepared with {len(specs_to_add)} entries.")
            spec_success = True
        else:
            print("No spec data to add.")
        if fetch_errors > 0:
            print(f"Warning: Encountered {fetch_errors} errors while fetching spec details.")
    else:
        print("Error: Failed to fetch or parse playable specialization index.")

    return class_success and spec_success


def get_guild_roster():
    """ Fetches the guild roster from Blizzard API. """
    if not GUILD_NAME or not REALM_SLUG: return None
    access_token = get_blizzard_access_token()
    if not access_token: return None
    realm_slug_lower = REALM_SLUG.lower()
    guild_name_segment = GUILD_NAME.lower().replace(' ', '-')
    api_url = f"{BLIZZARD_API_BASE_URL}/data/wow/guild/{realm_slug_lower}/{guild_name_segment}/roster"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"namespace": f"profile-{REGION}", "locale": "en_US"}
    data = make_api_request(api_url, params, headers)
    return data

def get_character_summary(realm_slug, character_name):
    """ Fetches character profile summary from Blizzard API. """
    access_token = get_blizzard_access_token()
    if not access_token: return None
    realm_slug = realm_slug.lower()
    character_name = character_name.lower()
    api_url = f"{BLIZZARD_API_BASE_URL}/profile/wow/character/{realm_slug}/{character_name}"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"namespace": f"profile-{REGION}", "locale": "en_US"}
    data = make_api_request(api_url, params, headers)
    return data


def get_character_raid_progression(realm_slug, character_name):
    """ Fetches character raid encounters from Blizzard API. """
    access_token = get_blizzard_access_token()
    if not access_token: return None
    realm_slug = realm_slug.lower()
    character_name = character_name.lower()
    api_url = f"{BLIZZARD_API_BASE_URL}/profile/wow/character/{realm_slug}/{character_name}/encounters/raids"
    headers = {"Authorization": f"Bearer {access_token}"}
    params = {"namespace": f"profile-{REGION}", "locale": "en_US"}
    data = make_api_request(api_url, params, headers)
    return data


def summarize_raid_progression(raid_data):
    """
    Summarizes raid progression for 'Liberation of Undermine'.
    Returns a tuple: (summary_string, heroic_kills_count)
    """
    target_expansion_name = "The War Within"
    target_raid_name = "Liberation of Undermine"
    short_raid_name = "Undermine"
    if not raid_data or 'expansions' not in raid_data: return None, -1
    heroic_kills = -1; heroic_total = 0; mythic_kills = -1; mythic_total = 0; raid_found = False
    for expansion in raid_data.get('expansions', []):
        exp_details = expansion.get('expansion', {})
        if exp_details.get('name') == target_expansion_name:
            for instance in expansion.get('instances', []):
                instance_details = instance.get('instance', {})
                if instance_details.get('name') == target_raid_name:
                    raid_found = True
                    for mode in instance.get('modes', []):
                        difficulty = mode.get('difficulty', {}); progress = mode.get('progress', {})
                        difficulty_type = difficulty.get('type')
                        if difficulty_type == "HEROIC":
                            heroic_kills = progress.get('completed_count', 0)
                            heroic_total = progress.get('total_count', 0)
                        elif difficulty_type == "MYTHIC":
                            mythic_kills = progress.get('completed_count', 0)
                            mythic_total = progress.get('total_count', 0)
                    break
            break
    if not raid_found: return f"{short_raid_name}: Not Found", -1
    summary_parts = []
    if heroic_kills != -1 and heroic_total > 0: summary_parts.append(f"{heroic_kills}/{heroic_total}H")
    if mythic_kills != -1 and mythic_total > 0: summary_parts.append(f"{mythic_kills}/{mythic_total}M")
    if not summary_parts: summary_output = f"{short_raid_name}: No H/M Data"
    else: summary_output = f"{short_raid_name}: {' '.join(summary_parts)}"
    hc_kills_return = 0 if heroic_kills == -1 else heroic_kills
    return summary_output, hc_kills_return

def fetch_wcl_guild_reports(limit=30):
    """
    Fetches recent raid reports for the guild from WCL API,
    filters for the last 8 raid nights on Wed/Fri in Central Time.
    """
    if not WCL_GUILD_ID:
        print("Error: WCL_GUILD_ID not set.")
        return None
    try:
        guild_id_int = int(WCL_GUILD_ID)
    except ValueError:
        print(f"Error: WCL_GUILD_ID '{WCL_GUILD_ID}' is not valid.")
        return None

    access_token = get_wcl_access_token()
    if not access_token: return None

    query = f"""
    {{
        reportData {{
            reports(guildID: {guild_id_int}, limit: {limit}) {{
                data {{
                    code
                    title
                    startTime # UTC timestamp in milliseconds
                    endTime
                    owner {{ name }}
                }}
            }}
        }}
    }}
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    data = make_api_request(WCL_API_ENDPOINT, params=None, headers=headers, is_wcl=True, wcl_query=query)

    if not data or not data.get('data', {}).get('reportData', {}).get('reports', {}).get('data'):
        print("Failed to fetch or parse WCL guild reports.")
        if data: print(f"WCL Response (or error part): {json.dumps(data, indent=2)}")
        return None

    all_reports = data['data']['reportData']['reports']['data']
    print(f"Fetched {len(all_reports)} total WCL reports. Filtering for Wed/Fri...")

    filtered_reports = []
    all_reports.sort(key=lambda r: r.get('startTime', 0), reverse=True)

    for report in all_reports:
        start_time_ms = report.get('startTime')
        if not start_time_ms: continue
        utc_dt = datetime.fromtimestamp(start_time_ms / 1000, tz=pytz.utc)
        ct_dt = utc_dt.astimezone(CENTRAL_TZ)
        if ct_dt.weekday() == 2 or ct_dt.weekday() == 4: # Wednesday or Friday
             report['start_time_dt'] = utc_dt
             report['end_time_dt'] = datetime.fromtimestamp(report.get('endTime', 0) / 1000, tz=pytz.utc) if report.get('endTime') else None
             filtered_reports.append(report)
             if len(filtered_reports) == 8: break
    print(f"Filtered down to {len(filtered_reports)} Wed/Fri WCL reports.")
    return filtered_reports

def fetch_wcl_report_data(report_code, metric="dps"): # Changed from fetch_wcl_report_details
    """Fetches player actors (for attendance) and rankings for a specific WCL report."""
    if not report_code: return None
    access_token = get_wcl_access_token()
    if not access_token: return None

    query = f"""
    query ReportDetails($reportCode: String!) {{
      reportData {{
        report(code: $reportCode) {{
          masterData {{
            actors(type: "Player") {{ # For attendance
              id
              name
              server
            }}
          }}
          rankings(playerMetric: {metric}, compare: Parsek) {{ # For performance
            data {{
              encounter {{ id name }}
              character {{ id name server }}
              class {{ name }}
              spec {{ name }}
              rankPercent
              total
            }}
          }}
        }}
      }}
    }}
    """
    graphql_variables = {"reportCode": report_code}
    headers = {"Authorization": f"Bearer {access_token}"}
    data = make_api_request(WCL_API_ENDPOINT, params=None, headers=headers, is_wcl=True, wcl_query=query, wcl_variables=graphql_variables)

    actors = None
    rankings = None

    if data and data.get('data', {}).get('reportData', {}).get('report'):
        report_content = data['data']['reportData']['report']
        if report_content.get('masterData', {}).get('actors'):
            actors = report_content['masterData']['actors']
        if report_content.get('rankings', {}).get('data'):
            rankings = report_content['rankings']['data']
    else:
        print(f"Failed to fetch or parse data for WCL report {report_code}.")
        if data: print(f"WCL Response (or error part): {json.dumps(data, indent=2)}")

    return {"actors": actors, "rankings": rankings}

# --- END API Helper Functions ---


# --- Database Update Logic ---
def update_database():
    """ Fetches all data from Blizzard API and updates the database. """
    print("Starting database update process...")
    start_time = time.time()
    db_session = SessionLocal()

    try:
        print(f"Attempting to drop existing tables (WCLPerformance, WCLAttendance, WCLReport, Character, PlayableSpec, PlayableClass)...")
        Base.metadata.bind = engine
        WCLPerformance.__table__.drop(engine, checkfirst=True)
        WCLAttendance.__table__.drop(engine, checkfirst=True)
        WCLReport.__table__.drop(engine, checkfirst=True)
        Character.__table__.drop(engine, checkfirst=True)
        PlayableSpec.__table__.drop(engine, checkfirst=True)
        PlayableClass.__table__.drop(engine, checkfirst=True)
        print("Tables dropped (or did not exist).")

        print("Creating tables...")
        Base.metadata.create_all(bind=engine)
        print("Tables created successfully.")
    except OperationalError as e:
         print(f"Database connection error during drop/create: {e}. Check DATABASE_URL and network.")
         db_session.close(); return
    except Exception as e:
        print(f"Error during table drop/create: {e}")
        db_session.close(); return

    if not update_static_tables(db_session):
        print("Error: Failed to update static class/spec tables. Aborting update.")
        db_session.close(); return
    try:
        db_session.commit()
        print("Static tables (Class/Spec) committed.")
    except Exception as e:
        print(f"Error committing static table data: {e}")
        db_session.rollback(); db_session.close(); return

    roster_data = get_guild_roster()
    if not roster_data or 'members' not in roster_data:
        print("Error: Failed to fetch guild roster. Aborting update.")
        db_session.close(); return

    total_members = len(roster_data['members'])
    print(f"Fetched {total_members} total members from Blizzard roster. Processing rank <= 4...")

    characters_to_insert = []
    blizz_id_to_char_map = {}
    api_call_count = 0
    processed_for_details = 0
    local_class_map = {cls.id: cls.name for cls in db_session.query(PlayableClass).all()}

    for member_entry in roster_data['members']:
        character_info = member_entry.get('character', {})
        rank = member_entry.get('rank')
        char_id = character_info.get('id')
        char_name = character_info.get('name')
        char_realm_slug = character_info.get('realm', {}).get('slug')

        if rank is None or rank > 4 or not char_id or not char_name or not char_realm_slug:
            continue

        processed_for_details += 1
        if processed_for_details % 10 == 1 or processed_for_details == total_members:
             print(f"\nProcessing Blizzard details for {char_name}-{char_realm_slug} (Rank {rank})...")

        class_id = character_info.get('playable_class', {}).get('id')
        class_name_from_roster = local_class_map.get(class_id, f"ID: {class_id}" if class_id else "N/A")

        item_level = None; raid_progression_summary = None; spec_name = None; role = None; main_spec_override = None; heroic_kills = -1

        summary_data = get_character_summary(char_realm_slug, char_name)
        api_call_count += 1
        if summary_data:
            ilvl_raw = summary_data.get('average_item_level')
            item_level = int(ilvl_raw) if isinstance(ilvl_raw, (int, float)) else None
            active_spec_data = summary_data.get('active_spec')
            if active_spec_data and isinstance(active_spec_data, dict):
                spec_name = active_spec_data.get('name')
                try:
                    if spec_name in TANK_SPECS: role = "Tank"
                    elif spec_name in HEALER_SPECS: role = "Healer"
                    elif class_name_from_roster in MELEE_DPS_SPECS and spec_name in MELEE_DPS_SPECS.get(class_name_from_roster, []):
                        role = "Melee DPS"
                    elif class_name_from_roster in RANGED_DPS_SPECS and spec_name in RANGED_DPS_SPECS.get(class_name_from_roster, []):
                        role = "Ranged DPS"
                    elif spec_name: role = "DPS"
                    else: role = "Unknown"
                except Exception as spec_err:
                    print(f"Warning: Could not determine role for {char_name} (Class: {class_name_from_roster}, Spec: {spec_name}): {spec_err}")
                    role = "Unknown"

        raid_data = get_character_raid_progression(char_realm_slug, char_name)
        api_call_count += 1
        if raid_data:
            raid_progression_summary, heroic_kills = summarize_raid_progression(raid_data)
            if raid_progression_summary is None or "Not Found" in raid_progression_summary or "No H/M Data" in raid_progression_summary:
                 raid_progression_summary = None
        else:
            raid_progression_summary = None; heroic_kills = -1

        calculated_status = "Member"
        if item_level is None or item_level < 650: calculated_status = "Wiping Alt"
        elif heroic_kills > 6 : calculated_status = "Wiper"
        elif heroic_kills >= 0 and heroic_kills <= 6: calculated_status = "Member"

        new_char = Character(
            id=char_id, name=char_name, realm_slug=char_realm_slug, level=character_info.get('level'),
            class_id=class_id, class_name=class_name_from_roster,
            spec_name=spec_name, main_spec_override=None, role=role,
            status=calculated_status, item_level=item_level,
            raid_progression=raid_progression_summary, rank=rank,
            raid_attendance_percentage=0.0, avg_wcl_performance=None
        )
        characters_to_insert.append(new_char)
        blizz_id_to_char_map[char_id] = new_char

    print(f"\nProcessed Blizzard details for {len(characters_to_insert)} members (Rank <= 4). Made {api_call_count} API calls.")

    try:
        print(f"Inserting {len(characters_to_insert)} characters into the database...")
        if characters_to_insert:
             db_session.add_all(characters_to_insert)
             db_session.commit()
             print(f"Character insert complete.")
        else:
             print("No characters met the criteria to be inserted.")
    except Exception as e:
        print(f"Error during character insert: {e}")
        db_session.rollback(); db_session.close(); return

    print("\n--- Fetching and Processing Warcraft Logs Data ---")
    wcl_reports_to_process = fetch_wcl_guild_reports()
    wcl_reports_in_db = []
    wcl_attendances_to_insert = []
    wcl_performances_to_insert = []
    character_attendance_raw_counts = {}
    character_performance_scores = {}
    successfully_processed_wcl_reports_for_attendance = 0
    successfully_processed_wcl_reports_for_performance = 0

    if wcl_reports_to_process:
        print(f"Processing {len(wcl_reports_to_process)} WCL reports for attendance & performance...")
        for report_data in wcl_reports_to_process:
            report_code = report_data.get('code')
            if not report_code: continue

            new_report = WCLReport(
                code=report_code, title=report_data.get('title'),
                start_time=report_data.get('start_time_dt'), end_time=report_data.get('end_time_dt'),
                owner_name=report_data.get('owner', {}).get('name')
            )
            wcl_reports_in_db.append(new_report)

            report_details = fetch_wcl_report_data(report_code, metric="dps")
            actors_data = report_details.get("actors")
            rankings_data = report_details.get("rankings")

            if actors_data:
                successfully_processed_wcl_reports_for_attendance += 1
                player_names_in_log = {actor.get('name') for actor in actors_data if actor.get('name')}
                for char_id, character_obj in blizz_id_to_char_map.items():
                    if character_obj.name.lower() in (name.lower() for name in player_names_in_log):
                        wcl_attendances_to_insert.append(WCLAttendance(report_code=report_code, character_id=char_id))
                        character_attendance_raw_counts[char_id] = character_attendance_raw_counts.get(char_id, 0) + 1
            else:
                print(f"Warning: Could not get player list for WCL report {report_code} (attendance).")

            if rankings_data:
                successfully_processed_wcl_reports_for_performance +=1
                for rank_entry in rankings_data:
                    char_info = rank_entry.get('character', {})
                    wcl_char_name = char_info.get('name')
                    matched_char_id = None
                    for blizz_id, char_obj in blizz_id_to_char_map.items():
                        if char_obj.name.lower() == wcl_char_name.lower():
                            matched_char_id = blizz_id
                            break
                    if matched_char_id:
                        if matched_char_id not in character_performance_scores:
                            character_performance_scores[matched_char_id] = []
                        percentile = rank_entry.get('rankPercent')
                        if percentile is not None:
                            character_performance_scores[matched_char_id].append(percentile)
                            wcl_performances_to_insert.append(WCLPerformance(
                                report_code=report_code, character_id=matched_char_id,
                                encounter_id=rank_entry.get('encounter',{}).get('id', 0),
                                encounter_name=rank_entry.get('encounter',{}).get('name', 'Overall'),
                                spec_name=rank_entry.get('spec',{}).get('name'),
                                metric="dps", rank_percentile=percentile
                            ))
            else:
                print(f"Warning: Could not get rankings for WCL report {report_code}.")
            time.sleep(0.2) # Increased delay slightly for WCL API
        try:
            if wcl_reports_in_db:
                print(f"\nInserting {len(wcl_reports_in_db)} WCL reports...")
                db_session.add_all(wcl_reports_in_db)
                db_session.commit()
                print("WCL reports inserted.")
            if wcl_attendances_to_insert:
                print(f"Inserting {len(wcl_attendances_to_insert)} WCL attendance records...")
                db_session.add_all(wcl_attendances_to_insert)
                db_session.commit()
                print("WCL attendance inserted.")
            if wcl_performances_to_insert:
                print(f"Inserting {len(wcl_performances_to_insert)} WCL performance records...")
                db_session.add_all(wcl_performances_to_insert)
                db_session.commit()
                print("WCL performance records inserted.")

            if character_attendance_raw_counts:
                print("Updating character attendance percentages...")
                update_count = 0
                if successfully_processed_wcl_reports_for_attendance > 0:
                    for char_id, raw_count in character_attendance_raw_counts.items():
                        char_to_update = db_session.query(Character).get(char_id)
                        if char_to_update:
                            attendance_percentage = round((raw_count / successfully_processed_wcl_reports_for_attendance) * 100, 2)
                            char_to_update.raid_attendance_percentage = attendance_percentage
                            update_count += 1
                    db_session.commit()
                    print(f"Updated attendance percentage for {update_count} characters based on {successfully_processed_wcl_reports_for_attendance} successfully processed reports.")
                else:
                    print("No WCL reports were successfully processed for attendance details; cannot calculate attendance percentage.")

            if character_performance_scores:
                print("Updating character average WCL performance...")
                update_count = 0
                for char_id, scores in character_performance_scores.items():
                    char_to_update = db_session.query(Character).get(char_id)
                    if char_to_update and scores:
                        avg_perf = round(sum(scores) / len(scores), 2)
                        char_to_update.avg_wcl_performance = avg_perf
                        update_count +=1
                db_session.commit()
                print(f"Updated average performance for {update_count} characters.")

        except IntegrityError as ie: # Catch issues with unique constraints (e.g., duplicate WCLPerformance)
            print(f"Database Integrity Error during WCL data insert/update: {ie}")
            db_session.rollback()
        except Exception as e:
            print(f"Error during WCL data insert/update: {e}")
            db_session.rollback()
        finally:
            db_session.close()
    else:
        print("Skipping WCL processing as no reports were fetched.")
        db_session.close()

    end_time = time.time()
    print(f"\nUpdate process finished in {round(end_time - start_time, 2)} seconds.")


# --- Main Execution ---
if __name__ == "__main__":
    required_vars = ['BLIZZARD_CLIENT_ID', 'BLIZZARD_CLIENT_SECRET', 'GUILD_NAME', 'REALM_SLUG', 'REGION', 'DATABASE_URL', 'WCL_CLIENT_ID', 'WCL_CLIENT_SECRET', 'WCL_GUILD_ID']
    print(f"Checking environment variables...")
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    if missing_vars:
        print(f"Error: Missing required environment variables: {', '.join(missing_vars)}")
        if missing_vars == ['DATABASE_URL'] and DATABASE_URI.startswith('sqlite:///'):
             print("Attempting to use default local SQLite DB: guild_data.db")
             api_keys_missing = [var for var in required_vars if not os.environ.get(var) and var != 'DATABASE_URL']
             if api_keys_missing:
                  print(f"Error: Missing API environment variables needed for fetch: {', '.join(api_keys_missing)}")
                  exit(1)
             else:
                  update_database()
        else:
             exit(1)
    else:
        print("All required environment variables found.")
        update_database()
