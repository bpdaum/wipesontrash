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
    avg_wcl_performance = Column(Float, nullable=True)
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
    metric = Column(String(20)) 
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
             print(f"  -> Keeping Report: {report['code']} - {report['title']} (Zone: {zone_name}, Started: {ct_dt.strftime('%Y-%m-%d %H:%M %Z')})", flush=True)
             if len(filtered_reports) >= 8: 
                 break 
    print(f"Filtered down to {len(filtered_reports)} relevant Wed/Fri WCL reports for '{target_raid_name_wcl}'. Taking up to 8.", flush=True)
    return filtered_reports[:8] 


def fetch_wcl_report_data_for_processing(report_code, metric="dps"):
    if not report_code: return None
    access_token = get_wcl_access_token() 
    if not access_token: return None

    query = f"""
    query ReportDetails($reportCode: String!) {{
      reportData {{
        report(code: $reportCode) {{
          masterData {{
            actors(type: "Player") {{
              id
              name
              server
            }}
          }}
          rankings(playerMetric: {metric}, compare: Parses) 
        }}
      }}
    }}
    """
    graphql_variables = {"reportCode": report_code}
    headers = {"Authorization": f"Bearer {access_token}"}
    wcl_api_v2_client = os.environ.get("WCL_API_ENDPOINT", "https://www.warcraftlogs.com/api/v2/client")
    data = make_api_request(wcl_api_v2_client, params=None, headers=headers, is_wcl=True, wcl_query=query, wcl_variables=graphql_variables)

    actors = None
    parsed_rankings_data = None 

    if data and data.get('data', {}).get('reportData', {}).get('report'):
        report_content = data['data']['reportData']['report']
        if report_content.get('masterData', {}).get('actors'):
            actors = report_content['masterData']['actors']
        
        rankings_content = report_content.get('rankings') 
        
        if rankings_content:
            parsed_rankings = None 
            try:
                if isinstance(rankings_content, str):
                    parsed_rankings = json.loads(rankings_content)
                elif isinstance(rankings_content, dict):
                    parsed_rankings = rankings_content
                else:
                    print(f"ERROR: Report {report_code}: Rankings content is of unexpected type: {type(rankings_content)}", flush=True)
                
                if parsed_rankings and isinstance(parsed_rankings, dict) and 'data' in parsed_rankings: 
                    parsed_rankings_data = parsed_rankings['data']
                elif parsed_rankings: 
                    print(f"WARNING: Report {report_code}: Parsed rankings data is not in expected format (missing 'data' key or 'data' is None). Parsed data: {parsed_rankings}", flush=True)
            
            except json.JSONDecodeError as je: 
                print(f"ERROR: Report {report_code}: Error decoding WCL rankings JSON string: {je}", flush=True)
            except TypeError as te: 
                print(f"ERROR: Report {report_code}: TypeError during WCL rankings processing: {te}", flush=True)
            except Exception as e:
                 print(f"ERROR: Report {report_code}: Unexpected error parsing WCL rankings: {e}", flush=True)
    else:
        print(f"ERROR: Report {report_code}: Failed to fetch or parse report data structure. WCL Response: {json.dumps(data, indent=2) if data else 'No data'}", flush=True)

    return {"actors": actors, "rankings": parsed_rankings_data}


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

        characters_in_db_query = db_session.query(Character.id, Character.name, Character.realm_slug, Character.is_active).filter(Character.is_active == True).all()
        if not characters_in_db_query:
            print("No active characters found in the database. Ensure update_roster_data.py has run and characters are active.", flush=True)
            return
        
        char_name_to_id_map = {char.name.lower(): char.id for char in characters_in_db_query}
        print(f"DEBUG: Built char_name_to_id_map with {len(char_name_to_id_map)} active characters. Sample (up to 5 keys): {list(char_name_to_id_map.keys())[:5]}", flush=True)
        
        wcl_reports_to_process = fetch_wcl_guild_reports_for_processing()
        
        if not wcl_reports_to_process:
            print("No relevant WCL reports found to process.", flush=True)
            return

        wcl_reports_in_db = []
        wcl_attendances_to_insert = []
        wcl_performances_to_insert = []
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

            report_details = fetch_wcl_report_data_for_processing(report_code, metric="dps")
            actors_data = report_details.get("actors")
            rankings_data = report_details.get("rankings") 

            if actors_data:
                successfully_processed_wcl_reports_for_attendance += 1
                player_names_in_log = {actor.get('name').lower() for actor in actors_data if actor.get('name')}
                
                for wcl_player_name_lower in player_names_in_log:
                    matched_char_id = char_name_to_id_map.get(wcl_player_name_lower)
                    if matched_char_id:
                        wcl_attendances_to_insert.append(WCLAttendance(report_code=report_code, character_id=matched_char_id))
                        character_attendance_raw_counts[matched_char_id] = character_attendance_raw_counts.get(matched_char_id, 0) + 1
            else:
                print(f"WARNING: Report {report_code}: Could not get player list for attendance. Actors data was: {actors_data}", flush=True)

            if rankings_data: 
                print(f"DEBUG: Report {report_code}: Processing {len(rankings_data)} fight/encounter summary entries.", flush=True)
                for fight_summary_entry in rankings_data: 
                    encounter_info = fight_summary_entry.get('encounter', {})
                    encounter_id = encounter_info.get('id', 0) 
                    encounter_name = encounter_info.get('name', 'Unknown Encounter')

                    roles_data = fight_summary_entry.get('roles', {})
                    if not roles_data:
                        print(f"DEBUG: Report {report_code}, Encounter '{encounter_name}': No 'roles' data in this fight summary.", flush=True)
                        continue

                    for role_name, role_details in roles_data.items(): 
                        if isinstance(role_details, dict) and 'characters' in role_details:
                            for char_perf_entry in role_details['characters']:
                                wcl_char_name = char_perf_entry.get('name')
                                if not wcl_char_name:
                                    print(f"DEBUG: Report {report_code}, Encounter '{encounter_name}', Role '{role_name}': Skipping character entry with no name: {char_perf_entry}", flush=True)
                                    continue
                                
                                wcl_char_name_lower = wcl_char_name.lower()
                                matched_char_id = char_name_to_id_map.get(wcl_char_name_lower)

                                # --- Enhanced Debugging for Name Matching ---
                                if not matched_char_id:
                                    print(f"DEBUG-NOMATCH: WCL char '{wcl_char_name_lower}' (from report {report_code}, enc '{encounter_name}') not found in char_name_to_id_map.", flush=True)
                                    continue # Skip if character name from log is not in our DB map
                                # --- End Enhanced Debugging ---
                                
                                # This 'if matched_char_id:' is now slightly redundant due to the 'continue' above, but harmless.
                                if matched_char_id: 
                                    if matched_char_id not in character_performance_scores:
                                        character_performance_scores[matched_char_id] = []
                                    
                                    percentile = char_perf_entry.get('rankPercent')
                                    spec_name = char_perf_entry.get('spec') # WCL provides spec name directly here

                                    if percentile is not None:
                                        print(f"DEBUG-MATCH&PERCENTILE: Report {report_code}, Enc '{encounter_name}': Matched {wcl_char_name_lower} (DB ID: {matched_char_id}), Spec '{spec_name}', adding percentile: {percentile}", flush=True)
                                        character_performance_scores[matched_char_id].append(percentile)
                                        
                                        wcl_performances_to_insert.append(WCLPerformance(
                                            report_code=report_code, 
                                            character_id=matched_char_id,
                                            encounter_id=encounter_id, 
                                            encounter_name=encounter_name,
                                            spec_name=spec_name, 
                                            metric="dps", 
                                            rank_percentile=percentile
                                        ))
                                    else:
                                        print(f"DEBUG-NOPERCENTILE: Report {report_code}, Enc '{encounter_name}': Matched {wcl_char_name_lower} (DB ID: {matched_char_id}), Spec '{spec_name}', but rankPercent is None. Entry: {char_perf_entry}", flush=True)
                        # else: # Optional: log if role_details is not a dict or no 'characters' key
                            # print(f"DEBUG: Report {report_code}, Encounter '{encounter_name}': Role '{role_name}' data is not as expected or has no characters. Details: {role_details}", flush=True)
            else:
                print(f"WARNING: Report {report_code}: Could not process rankings. Rankings data was None or empty after fetch.", flush=True)
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
            print(f"Inserting {len(wcl_performances_to_insert)} WCL performance records...", flush=True)
            db_session.add_all(wcl_performances_to_insert)
            db_session.commit()
            print("WCL performance records inserted.", flush=True)

        print(f"\nDEBUG: character_performance_scores dictionary before updating DB: {character_performance_scores}", flush=True)

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
            print("Updating character average WCL performance...", flush=True)
            update_count = 0
            for char_id, scores in character_performance_scores.items():
                char_to_update = db_session.get(Character, char_id) 
                if char_to_update and scores: 
                    avg_perf = round(sum(scores) / len(scores), 2)
                    print(f"DEBUG: Updating char_id {char_id} ({char_to_update.name}) with avg_perf: {avg_perf} from scores: {scores}", flush=True)
                    char_to_update.avg_wcl_performance = avg_perf
                    update_count +=1
                elif char_to_update and not scores:
                    print(f"DEBUG: Char_id {char_id} ({char_to_update.name}) found in character_performance_scores, but scores list is empty. Not updating avg_wcl_performance.", flush=True)

            db_session.commit()
            print(f"Updated average performance for {update_count} characters.", flush=True)
        else:
            print("No performance scores collected for any character; avg_wcl_performance not updated.", flush=True)


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
