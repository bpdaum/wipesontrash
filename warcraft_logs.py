# warcraft_logs.py
import os
import requests
import time
from datetime import datetime, timedelta
import json
import pytz # For timezone handling
import re

# --- Standalone SQLAlchemy setup ---
from sqlalchemy import create_engine, Column, Integer, String, DateTime, UniqueConstraint, MetaData, Index, ForeignKey, Float, Boolean
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from sqlalchemy.sql import func
from sqlalchemy.exc import OperationalError, IntegrityError

# --- Import from helper_functions ---
try:
    from helper_functions import get_wcl_access_token, make_api_request
except ImportError:
    print("Error: helper_functions.py not found. Make sure it's in the same directory.", flush=True)
    exit(1)

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
class PlayableClass(Base): 
    __tablename__ = 'playable_class'
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)
    characters = relationship("Character", back_populates="playable_class")

class Character(Base):
    __tablename__ = 'character'
    id = Column(Integer, primary_key=True) 
    name = Column(String(100), nullable=False)
    realm_slug = Column(String(100), nullable=False) 
    class_id = Column(Integer, ForeignKey('playable_class.id')) 
    class_name = Column(String(50)) 
    raid_attendance_percentage = Column(Float, default=0.0, nullable=True)
    avg_wcl_performance = Column(Float, nullable=True) # Will store role-appropriate average
    is_active = Column(Boolean, default=True, nullable=False, index=True) 

    attendances = relationship("WCLAttendance", back_populates="character", cascade="all, delete-orphan")
    performances = relationship("WCLPerformance", back_populates="character", cascade="all, delete-orphan")
    playable_class = relationship("PlayableClass", back_populates="characters")

    __table_args__ = ( UniqueConstraint('name', 'realm_slug', name='_name_realm_uc'), ) 
    def __repr__(self): return f'<Character DB ID: {self.id} Name: {self.name}>'


class WCLReport(Base):
    __tablename__ = 'wcl_report'
    code = Column(String(50), primary_key=True)
    title = Column(String(200))
    start_time = Column(DateTime, index=True)
    end_time = Column(DateTime)
    owner_name = Column(String(100))
    fetched_at = Column(DateTime, default=datetime.utcnow)
    attendances = relationship("WCLAttendance", back_populates="report", cascade="all, delete-orphan")
    performances = relationship("WCLPerformance", back_populates="report", cascade="all, delete-orphan")
    def __repr__(self): return f'<WCLReport {self.code} ({self.title})>'

class WCLAttendance(Base):
    __tablename__ = 'wcl_attendance'
    id = Column(Integer, primary_key=True)
    report_code = Column(String(50), ForeignKey('wcl_report.code'), nullable=False, index=True)
    character_id = Column(Integer, ForeignKey('character.id'), nullable=False, index=True)
    report = relationship("WCLReport", back_populates="attendances")
    character = relationship("Character", back_populates="attendances")
    __table_args__ = ( UniqueConstraint('report_code', 'character_id', name='_report_char_uc'), )
    def __repr__(self): return f'<WCLAttendance Report={self.report_code} CharacterID={self.character_id}>'

class WCLPerformance(Base):
    __tablename__ = 'wcl_performance'
    id = Column(Integer, primary_key=True)
    report_code = Column(String(50), ForeignKey('wcl_report.code'), nullable=False, index=True)
    character_id = Column(Integer, ForeignKey('character.id'), nullable=False, index=True)
    encounter_id = Column(Integer, nullable=False)
    encounter_name = Column(String(100))
    spec_name = Column(String(50)) 
    metric = Column(String(20)) # Will be 'dps' or 'hps'
    rank_percentile = Column(Float)
    report = relationship("WCLReport", back_populates="performances")
    character = relationship("Character", back_populates="performances")
    __table_args__ = ( UniqueConstraint('report_code', 'character_id', 'encounter_id', 'metric', name='_perf_uc'), )
    def __repr__(self): return f'<WCLPerformance Report={self.report_code} CharID={self.character_id} Enc={self.encounter_name} Metric={self.metric} Perf={self.rank_percentile}>'


# --- Configuration Loading ---
WCL_GUILD_ID = os.environ.get('WCL_GUILD_ID')
REGION = os.environ.get('REGION', 'us').lower() 

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

def determine_role_from_spec_and_class(spec_name, class_name):
    if not spec_name or not class_name: return "Unknown"
    if spec_name in TANK_SPECS: return "Tank"
    if spec_name in HEALER_SPECS: return "Healer"
    if class_name in MELEE_DPS_SPECS and spec_name in MELEE_DPS_SPECS.get(class_name, []):
        return "Melee DPS"
    if class_name in RANGED_DPS_SPECS and spec_name in RANGED_DPS_SPECS.get(class_name, []):
        return "Ranged DPS"
    if spec_name: return "DPS" 
    return "Unknown"

# --- WCL Data Fetching Functions ---
def fetch_wcl_guild_reports_for_processing(limit=50):
    if not WCL_GUILD_ID:
        print("Error: WCL_GUILD_ID not set.", flush=True)
        return None
    try:
        guild_id_int = int(WCL_GUILD_ID)
    except ValueError:
        print(f"Error: WCL_GUILD_ID '{WCL_GUILD_ID}' is not valid.", flush=True)
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
                    startTime
                    endTime
                    owner {{ name }}
                    zone {{ name id }}
                }}
            }}
        }}
    }}
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    wcl_api_v2_client = os.environ.get("WCL_API_ENDPOINT", "https://www.warcraftlogs.com/api/v2/client")
    data = make_api_request(wcl_api_v2_client, params=None, headers=headers, is_wcl=True, wcl_query=query)

    if not data or not data.get('data', {}).get('reportData', {}).get('reports', {}).get('data'):
        print("Failed to fetch or parse WCL guild reports.", flush=True)
        if data: print(f"WCL Response (or error part): {json.dumps(data, indent=2)}", flush=True)
        return None

    all_reports = data['data']['reportData']['reports']['data']
    print(f"Fetched {len(all_reports)} total WCL reports. Filtering for Wed/Fri & 'Liberation of Undermine'...", flush=True)

    filtered_reports = []
    all_reports.sort(key=lambda r: r.get('startTime', 0), reverse=True)
    target_raid_name_wcl = "Liberation of Undermine" 

    for report in all_reports:
        if not report:
            print("Warning: Encountered a None report object in WCL data.", flush=True)
            continue
        start_time_ms = report.get('startTime')
        zone_info = report.get('zone', {})
        zone_name = zone_info.get('name', '') if isinstance(zone_info, dict) else ''

        if not start_time_ms: continue

        utc_dt = datetime.fromtimestamp(start_time_ms / 1000, tz=pytz.utc)
        ct_dt = utc_dt.astimezone(CENTRAL_TZ)

        is_raid_day = ct_dt.weekday() == 2 or ct_dt.weekday() == 4 
        is_target_raid = target_raid_name_wcl.lower() in zone_name.lower()

        if is_raid_day and is_target_raid:
             report['start_time_dt'] = utc_dt
             report['end_time_dt'] = datetime.fromtimestamp(report.get('endTime', 0) / 1000, tz=pytz.utc) if report.get('endTime') else None
             filtered_reports.append(report)
             if len(filtered_reports) >= 16: 
                 break 
    print(f"Filtered down to {len(filtered_reports)} relevant Wed/Fri WCL reports for '{target_raid_name_wcl}'. Taking up to 8.", flush=True)
    return filtered_reports[:16] 

def _parse_rankings_content(report_code, metric_name, rankings_content):
    """Helper to parse the rankings content string/dict."""
    if not rankings_content:
        # print(f"DEBUG: Report {report_code} ({metric_name}): No rankings_content found.", flush=True)
        return None
    
    parsed_rankings = None
    try:
        if isinstance(rankings_content, str):
            parsed_rankings = json.loads(rankings_content)
        elif isinstance(rankings_content, dict):
            parsed_rankings = rankings_content
        else:
            print(f"ERROR: Report {report_code} ({metric_name}): Rankings content is of unexpected type: {type(rankings_content)}", flush=True)
            return None
        
        if parsed_rankings and isinstance(parsed_rankings, dict) and 'data' in parsed_rankings:
            return parsed_rankings['data']
        elif parsed_rankings: 
            print(f"WARNING: Report {report_code} ({metric_name}): Parsed rankings data not in expected format. Data: {parsed_rankings}", flush=True)
        return None
            
    except json.JSONDecodeError as je: 
        print(f"ERROR: Report {report_code} ({metric_name}): Error decoding JSON string: {je}", flush=True)
    except TypeError as te: 
        print(f"ERROR: Report {report_code} ({metric_name}): TypeError during processing: {te}", flush=True)
    except Exception as e:
         print(f"ERROR: Report {report_code} ({metric_name}): Unexpected error parsing: {e}", flush=True)
    return None

def fetch_wcl_report_data_for_processing(report_code):
    """Fetches actors, DPS rankings, and HPS rankings for a specific WCL report."""
    if not report_code: return {"actors": None, "dps_rankings": None, "hps_rankings": None}
    access_token = get_wcl_access_token()
    if not access_token: return {"actors": None, "dps_rankings": None, "hps_rankings": None}

    results = {"actors": None, "dps_rankings": None, "hps_rankings": None}
    headers = {"Authorization": f"Bearer {access_token}"}
    wcl_api_v2_client = os.environ.get("WCL_API_ENDPOINT", "https://www.warcraftlogs.com/api/v2/client")
    graphql_variables = {"reportCode": report_code}

    # Query for actors and DPS rankings in one go
    dps_actors_query = f"""
    query ReportDetails($reportCode: String!) {{
      reportData {{
        report(code: $reportCode) {{
          masterData {{ actors(type: "Player") {{ id name server }} }}
          rankings(playerMetric: dps, compare: Parses)
        }}
      }}
    }}
    """
    print(f"DEBUG: Fetching DPS rankings and actors for report {report_code}", flush=True)
    dps_response = make_api_request(wcl_api_v2_client, None, headers, is_wcl=True, wcl_query=dps_actors_query, wcl_variables=graphql_variables)

    if dps_response and dps_response.get('data', {}).get('reportData', {}).get('report'):
        dps_report_content = dps_response['data']['reportData']['report']
        if dps_report_content.get('masterData', {}).get('actors'):
            results["actors"] = dps_report_content['masterData']['actors']
        results["dps_rankings"] = _parse_rankings_content(report_code, "DPS", dps_report_content.get('rankings'))
    else:
        print(f"ERROR: Report {report_code} (DPS & Actors): Failed to fetch or parse main report structure. Response: {dps_response}", flush=True)

    # Query for HPS rankings (actors already fetched if DPS call was successful)
    hps_query = f"""
    query ReportDetails($reportCode: String!) {{
      reportData {{
        report(code: $reportCode) {{
          rankings(playerMetric: hps, compare: Parses)
        }}
      }}
    }}
    """
    print(f"DEBUG: Fetching HPS rankings for report {report_code}", flush=True)
    hps_response = make_api_request(wcl_api_v2_client, None, headers, is_wcl=True, wcl_query=hps_query, wcl_variables=graphql_variables)
    
    if hps_response and hps_response.get('data', {}).get('reportData', {}).get('report'):
        hps_report_content = hps_response['data']['reportData']['report']
        results["hps_rankings"] = _parse_rankings_content(report_code, "HPS", hps_report_content.get('rankings'))
    else:
        print(f"ERROR: Report {report_code} (HPS): Failed to fetch or parse main report structure. Response: {hps_response}", flush=True)
        
    return results


# --- Main Processing Function ---
def process_and_store_wcl_data():
    start_time = time.time() 
    print("Starting WCL data processing and storage...", flush=True)
    db_session = SessionLocal()

    try:
        print("Clearing WCL-specific tables (WCLPerformance, WCLAttendance, WCLReport)...", flush=True)
        db_session.query(WCLPerformance).delete(synchronize_session=False)
        db_session.query(WCLAttendance).delete(synchronize_session=False)
        db_session.query(WCLReport).delete(synchronize_session=False)
        db_session.commit()
        print("WCL-specific tables cleared.", flush=True)

        active_characters_from_db = db_session.query(Character.id, Character.name, Character.class_name).filter(Character.is_active == True).all()
        if not active_characters_from_db:
            print("No active characters found in the database. Ensure update_roster_data.py has run and characters are active.", flush=True)
            return
        
        char_info_map = {
            char.name.lower(): {'id': char.id, 'class_name': char.class_name}
            for char in active_characters_from_db
        }
        print(f"DEBUG: Built char_info_map with {len(char_info_map)} active characters.", flush=True)
        
        wcl_reports_to_process = fetch_wcl_guild_reports_for_processing()
        
        if not wcl_reports_to_process:
            print("No relevant WCL reports found to process.", flush=True)
            return

        wcl_reports_in_db = []
        wcl_attendances_to_insert = []
        wcl_performances_to_insert = []
        processed_performance_keys = set() 
        character_attendance_raw_counts = {} 
        character_performance_scores = {}    
        
        successfully_processed_wcl_reports_for_attendance = 0
        
        print(f"Processing {len(wcl_reports_to_process)} WCL reports for attendance & performance...", flush=True)
        for report_data_api in wcl_reports_to_process: 
            report_code = report_data_api.get('code')
            if not report_code: continue

            print(f"\n--- Processing Report Code: {report_code} ---", flush=True)
            new_report_db = WCLReport( 
                code=report_code, title=report_data_api.get('title'),
                start_time=report_data_api.get('start_time_dt'), end_time=report_data_api.get('end_time_dt'),
                owner_name=report_data_api.get('owner', {}).get('name')
            )
            wcl_reports_in_db.append(new_report_db)

            report_details = fetch_wcl_report_data_for_processing(report_code) # Fetches both DPS and HPS
            actors_data = report_details.get("actors")
            dps_rankings_data = report_details.get("dps_rankings")
            hps_rankings_data = report_details.get("hps_rankings")

            if actors_data:
                successfully_processed_wcl_reports_for_attendance += 1
                player_names_in_log = {actor.get('name').lower() for actor in actors_data if actor.get('name')}
                for wcl_player_name_lower in player_names_in_log:
                    matched_char_info = char_info_map.get(wcl_player_name_lower)
                    if matched_char_info:
                        matched_char_id = matched_char_info['id']
                        wcl_attendances_to_insert.append(WCLAttendance(report_code=report_code, character_id=matched_char_id))
                        character_attendance_raw_counts[matched_char_id] = character_attendance_raw_counts.get(matched_char_id, 0) + 1
            else:
                print(f"WARNING: Report {report_code}: Could not get player list for attendance. Actors data was: {actors_data}", flush=True)

            # Helper function to process a specific metric's rankings
            def process_metric_rankings(metric_rankings_data, metric_name_for_db, relevant_roles_for_avg):
                if not metric_rankings_data:
                    print(f"DEBUG: Report {report_code}: No {metric_name_for_db.upper()} rankings data to process.", flush=True)
                    return

                print(f"DEBUG: Report {report_code}: Processing {len(metric_rankings_data)} {metric_name_for_db.upper()} fight/encounter entries.", flush=True)
                for fight_summary_entry in metric_rankings_data:
                    encounter_info = fight_summary_entry.get('encounter', {})
                    encounter_id = encounter_info.get('id', 0)
                    encounter_name = encounter_info.get('name', 'Unknown Encounter')
                    roles_data = fight_summary_entry.get('roles', {})
                    if not roles_data: continue

                    # Iterate through all roles present in this fight summary (dps, healers, tanks)
                    for role_category_in_log, role_category_details in roles_data.items():
                        if isinstance(role_category_details, dict) and 'characters' in role_category_details:
                            for char_perf_entry in role_category_details['characters']:
                                wcl_char_name = char_perf_entry.get('name')
                                if not wcl_char_name: continue
                                
                                wcl_char_name_lower = wcl_char_name.lower()
                                matched_char_info = char_info_map.get(wcl_char_name_lower)
                                if not matched_char_info: continue

                                matched_char_id = matched_char_info['id']
                                db_character_class_name = matched_char_info['class_name']
                                wcl_spec_name = char_perf_entry.get('spec')
                                percentile = char_perf_entry.get('rankPercent')

                                if percentile is not None:
                                    performance_key = (report_code, matched_char_id, encounter_id, metric_name_for_db)
                                    if performance_key not in processed_performance_keys:
                                        wcl_performances_to_insert.append(WCLPerformance(
                                            report_code=report_code, character_id=matched_char_id,
                                            encounter_id=encounter_id, encounter_name=encounter_name,
                                            spec_name=wcl_spec_name, metric=metric_name_for_db,
                                            rank_percentile=percentile
                                        ))
                                        processed_performance_keys.add(performance_key)

                                    played_role_in_fight = determine_role_from_spec_and_class(wcl_spec_name, db_character_class_name)
                                    if played_role_in_fight in relevant_roles_for_avg:
                                        if matched_char_id not in character_performance_scores:
                                            character_performance_scores[matched_char_id] = []
                                        character_performance_scores[matched_char_id].append(percentile)
                                        print(f"DEBUG-{metric_name_for_db.upper()}-SCORE: Report {report_code}, Enc '{encounter_name}': Matched {wcl_char_name_lower} (ID: {matched_char_id}), Played Role '{played_role_in_fight}', adding {metric_name_for_db.upper()} percentile: {percentile}", flush=True)
            
            # Process DPS rankings for Tanks and DPS
            process_metric_rankings(dps_rankings_data, "dps", ["Tank", "Melee DPS", "Ranged DPS", "DPS"])
            
            # Process HPS rankings for Healers
            process_metric_rankings(hps_rankings_data, "hps", ["Healer"])

            time.sleep(0.2) 

        # --- DB Inserts and Updates ---
        if wcl_reports_in_db:
            print(f"\nInserting {len(wcl_reports_in_db)} WCL reports...", flush=True)
            db_session.add_all(wcl_reports_in_db)
            db_session.commit() 
            print("WCL reports inserted.", flush=True)
        if wcl_attendances_to_insert:
            print(f"Inserting {len(wcl_attendances_to_insert)} WCL attendance records...", flush=True)
            db_session.add_all(wcl_attendances_to_insert)
            db_session.commit()
            print("WCL attendance inserted.", flush=True)
        
        if wcl_performances_to_insert:
            print(f"Inserting {len(wcl_performances_to_insert)} WCL performance records (DPS & HPS)...", flush=True)
            db_session.add_all(wcl_performances_to_insert)
            db_session.commit()
            print("WCL performance records inserted.", flush=True)
        else:
            print("No new WCL performance records to insert.", flush=True)

        print(f"\nDEBUG: character_performance_scores dictionary (role-appropriate parses) before updating DB: {character_performance_scores}", flush=True)

        if character_attendance_raw_counts and successfully_processed_wcl_reports_for_attendance > 0:
            print("Updating character attendance percentages...", flush=True)
            update_count = 0
            for char_id, raw_count in character_attendance_raw_counts.items():
                char_to_update = db_session.get(Character, char_id) 
                if char_to_update:
                    attendance_percentage = round((raw_count / successfully_processed_wcl_reports_for_attendance) * 100, 2)
                    char_to_update.raid_attendance_percentage = attendance_percentage
                    update_count += 1
            db_session.commit()
            print(f"Updated attendance percentage for {update_count} characters based on {successfully_processed_wcl_reports_for_attendance} reports.", flush=True)
        else:
            print("No WCL reports successfully processed for attendance details or no attendance data; cannot calculate attendance percentage.", flush=True)

        if character_performance_scores:
            print("Updating character average WCL performance (role-appropriate)...", flush=True)
            update_count = 0
            for char_id, scores in character_performance_scores.items():
                char_to_update = db_session.get(Character, char_id) 
                if char_to_update and scores: 
                    avg_perf = round(sum(scores) / len(scores), 2)
                    print(f"DEBUG: Updating char_id {char_id} ({char_to_update.name}) with avg_wcl_performance: {avg_perf} from scores: {scores}", flush=True)
                    char_to_update.avg_wcl_performance = avg_perf
                    update_count +=1
                elif char_to_update and not scores: 
                    print(f"DEBUG: Char_id {char_id} ({char_to_update.name}) in character_performance_scores, but scores list is empty. Not updating.", flush=True)
            db_session.commit()
            print(f"Updated average WCL (role-appropriate) performance for {update_count} characters.", flush=True)
        else:
            print("No relevant performance scores collected for any character; avg_wcl_performance not updated.", flush=True)

    except IntegrityError as ie:
        print(f"Database Integrity Error during WCL data processing: {ie}", flush=True)
        db_session.rollback()
    except Exception as e:
        print(f"Error during WCL data processing: {e}", flush=True)
        import traceback
        traceback.print_exc()
        db_session.rollback()
    finally:
        db_session.close()

    end_time = time.time()
    print(f"\nWCL data processing finished in {round(end_time - start_time, 2)} seconds.", flush=True)

# --- Main Execution ---
if __name__ == "__main__":
    print("Ensuring all database tables exist (as defined in this script's models)...", flush=True)
    Base.metadata.create_all(engine, checkfirst=True)
    print("Database tables verified/created.", flush=True)

    required_vars = ['WCL_CLIENT_ID', 'WCL_CLIENT_SECRET', 'WCL_GUILD_ID', 'DATABASE_URL']
    print(f"Checking environment variables for WCL script...", flush=True)
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    if missing_vars:
        print(f"Error: Missing required environment variables for WCL script: {', '.join(missing_vars)}", flush=True)
        exit(1)
    else:
        print("All required environment variables for WCL script found.", flush=True)
        process_and_store_wcl_data()
