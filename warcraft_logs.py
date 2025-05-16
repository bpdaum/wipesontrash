# warcraft_logs.py
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

# --- Import from helper_functions ---
# Ensure helper_functions.py is in the same directory or Python path
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
# Define all models that this script interacts with or that are related for schema integrity

class PlayableClass(Base): # Needed for Character relationship
    __tablename__ = 'playable_class'
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)
    characters = relationship("Character", back_populates="playable_class")

class Character(Base):
    __tablename__ = 'character'
    id = Column(Integer, primary_key=True) # Blizzard Character ID
    name = Column(String(100), nullable=False)
    realm_slug = Column(String(100), nullable=False) # For matching with Blizzard API data if needed
    class_id = Column(Integer, ForeignKey('playable_class.id')) # For linking to PlayableClass
    class_name = Column(String(50)) # Populated by update_roster_data.py
    # Add other fields if needed for context, but this script primarily updates WCL fields
    raid_attendance_percentage = Column(Float, default=0.0, nullable=True)
    avg_wcl_performance = Column(Float, nullable=True)

    attendances = relationship("WCLAttendance", back_populates="character", cascade="all, delete-orphan")
    performances = relationship("WCLPerformance", back_populates="character", cascade="all, delete-orphan")
    playable_class = relationship("PlayableClass", back_populates="characters")

    __table_args__ = ( UniqueConstraint('name', 'realm_slug', name='_name_realm_uc'), ) # Ensure this matches other scripts
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
# REGION is used by helper_functions if it makes Blizzard API calls (not directly by this script's WCL part)
REGION = os.environ.get('REGION', 'us').lower()


# --- Warcraft Logs API Endpoints (already in helper_functions but good to have here for context) ---
# WCL_TOKEN_URL = "https://www.warcraftlogs.com/oauth/token"
# WCL_API_ENDPOINT = "https://www.warcraftlogs.com/api/v2/client"

# --- Timezone ---
CENTRAL_TZ = pytz.timezone('America/Chicago')


# --- WCL Data Fetching Functions ---

def fetch_wcl_guild_reports_for_processing(limit=50): # Fetch more to ensure we find 8 valid ones
    """
    Fetches recent raid reports for the guild from WCL API,
    filters for the last 8 raid nights on Wed/Fri in Central Time that are for "Liberation of Undermine".
    """
    if not WCL_GUILD_ID:
        print("Error: WCL_GUILD_ID not set.", flush=True)
        return None
    try:
        guild_id_int = int(WCL_GUILD_ID)
    except ValueError:
        print(f"Error: WCL_GUILD_ID '{WCL_GUILD_ID}' is not valid.", flush=True)
        return None

    access_token = get_wcl_access_token() # From helper_functions
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
    # Using WCL_API_ENDPOINT from helper_functions
    data = make_api_request(os.environ.get("WCL_API_ENDPOINT", "https://www.warcraftlogs.com/api/v2/client"), params=None, headers=headers, is_wcl=True, wcl_query=query)


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

        is_raid_day = ct_dt.weekday() == 2 or ct_dt.weekday() == 4 # Wednesday or Friday
        is_target_raid = target_raid_name_wcl.lower() in zone_name.lower()

        if is_raid_day and is_target_raid:
             report['start_time_dt'] = utc_dt
             report['end_time_dt'] = datetime.fromtimestamp(report.get('endTime', 0) / 1000, tz=pytz.utc) if report.get('endTime') else None
             filtered_reports.append(report)
             print(f"  -> Keeping Report: {report['code']} - {report['title']} (Zone: {zone_name}, Started: {ct_dt.strftime('%Y-%m-%d %H:%M %Z')})", flush=True)
             if len(filtered_reports) == 8:
                 break
    print(f"Filtered down to {len(filtered_reports)} relevant Wed/Fri WCL reports for '{target_raid_name_wcl}'.", flush=True)
    return filtered_reports


def fetch_wcl_report_data_for_processing(report_code, metric="dps"):
    """Fetches player actors (for attendance) and rankings for a specific WCL report."""
    if not report_code: return None
    access_token = get_wcl_access_token() # From helper_functions
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
          rankings(playerMetric: {metric}, compare: Parses) {{
            # This field returns a JSON string, so no sub-selection here.
          }}
        }}
      }}
    }}
    """
    graphql_variables = {"reportCode": report_code}
    headers = {"Authorization": f"Bearer {access_token}"}
    data = make_api_request(os.environ.get("WCL_API_ENDPOINT", "https://www.warcraftlogs.com/api/v2/client"), params=None, headers=headers, is_wcl=True, wcl_query=query, wcl_variables=graphql_variables)

    actors = None
    parsed_rankings_data = None

    if data and data.get('data', {}).get('reportData', {}).get('report'):
        report_content = data['data']['reportData']['report']
        if report_content.get('masterData', {}).get('actors'):
            actors = report_content['masterData']['actors']
        
        rankings_json_string = report_content.get('rankings') # This is a JSON string
        if rankings_json_string:
            try:
                parsed_rankings = json.loads(rankings_json_string) # Parse the string
                if parsed_rankings and isinstance(parsed_rankings, dict) and parsed_rankings.get('data'):
                    parsed_rankings_data = parsed_rankings['data']
                else:
                    print(f"WCL Rankings data for report {report_code} is not in expected format after parsing JSON.", flush=True)
            except json.JSONDecodeError as je:
                print(f"Error decoding WCL rankings JSON for report {report_code}: {je}", flush=True)
                print(f"Rankings JSON string was: {rankings_json_string}", flush=True)
            except Exception as e:
                 print(f"Unexpected error parsing WCL rankings JSON for report {report_code}: {e}", flush=True)
    else:
        print(f"Failed to fetch or parse data for WCL report {report_code}.", flush=True)
        if data: print(f"WCL Response (or error part): {json.dumps(data, indent=2)}", flush=True)

    return {"actors": actors, "rankings": parsed_rankings_data}


# --- Main Processing Function ---
def process_and_store_wcl_data():
    print("Starting WCL data processing and storage...", flush=True)
    db_session = SessionLocal()

    try:
        # Clear WCL-specific tables before inserting new data for the processed reports
        print("Clearing WCL-specific tables (WCLPerformance, WCLAttendance, WCLReport)...", flush=True)
        # We need to be careful here. If we only fetch 8 reports, we should only clear data related to those 8.
        # For simplicity now, and because we re-fetch the latest 8, let's clear all.
        # A more advanced approach would be to only delete records for reports being reprocessed.
        db_session.query(WCLPerformance).delete(synchronize_session=False)
        db_session.query(WCLAttendance).delete(synchronize_session=False)
        db_session.query(WCLReport).delete(synchronize_session=False)
        db_session.commit()
        print("WCL-specific tables cleared.", flush=True)

        # Fetch characters from DB to map names to IDs
        # Only fetch characters that could potentially be in logs (e.g., based on status or if they exist)
        # For simplicity, fetch all characters that might have their attendance/perf updated.
        # The update_roster_data.py script is the source of truth for the Character table.
        # This script *updates* Character records.
        characters_in_db = db_session.query(Character.id, Character.name, Character.realm_slug).all()
        if not characters_in_db:
            print("No characters found in the database. Run update_roster_data.py first.", flush=True)
            db_session.close()
            return
        
        # Create a map of Name-RealmSlug to Character ID for easier lookup
        # Note: WCL names might not include realm, so primary matching will be by name.
        # This could be an issue with duplicate names across realms if your guild has that.
        char_name_to_id_map = {char.name.lower(): char.id for char in characters_in_db}
        # More robust mapping might involve realm as well if WCL provides it consistently for actors
        # char_name_realm_to_id_map = {(char.name.lower(), char.realm_slug.lower()): char.id for char in characters_in_db}


        wcl_reports_to_process = fetch_wcl_guild_reports_for_processing()
        
        if not wcl_reports_to_process:
            print("No relevant WCL reports found to process.", flush=True)
            db_session.close()
            return

        wcl_reports_in_db = []
        wcl_attendances_to_insert = []
        wcl_performances_to_insert = []
        character_attendance_raw_counts = {} # {character_db_id: raw_attendance_count}
        character_performance_scores = {}    # {character_db_id: [score1, score2, ...]}
        
        successfully_processed_wcl_reports_for_attendance = 0
        successfully_processed_wcl_reports_for_performance = 0

        print(f"Processing {len(wcl_reports_to_process)} WCL reports for attendance & performance...", flush=True)
        for report_data in wcl_reports_to_process:
            report_code = report_data.get('code')
            if not report_code: continue

            new_report = WCLReport(
                code=report_code, title=report_data.get('title'),
                start_time=report_data.get('start_time_dt'), end_time=report_data.get('end_time_dt'),
                owner_name=report_data.get('owner', {}).get('name')
            )
            wcl_reports_in_db.append(new_report)

            report_details = fetch_wcl_report_data_for_processing(report_code, metric="dps")
            actors_data = report_details.get("actors")
            rankings_data = report_details.get("rankings")

            if actors_data:
                successfully_processed_wcl_reports_for_attendance += 1
                player_names_in_log = {actor.get('name').lower() for actor in actors_data if actor.get('name')}
                
                for wcl_player_name_lower in player_names_in_log:
                    # Attempt to find matching character in our DB
                    # This simple name match might need refinement if players have alts with same name on different realms
                    # or if WCL names differ slightly from Blizzard names.
                    matched_char_id = char_name_to_id_map.get(wcl_player_name_lower)
                    if matched_char_id:
                        wcl_attendances_to_insert.append(WCLAttendance(report_code=report_code, character_id=matched_char_id))
                        character_attendance_raw_counts[matched_char_id] = character_attendance_raw_counts.get(matched_char_id, 0) + 1
                    # else:
                        # print(f"  Unmatched WCL player for attendance: {wcl_player_name_lower} in report {report_code}", flush=True)
            else:
                print(f"Warning: Could not get player list for WCL report {report_code} (attendance).", flush=True)

            if rankings_data:
                successfully_processed_wcl_reports_for_performance +=1
                for rank_entry in rankings_data:
                    char_info = rank_entry.get('character', {})
                    wcl_char_name = char_info.get('name')
                    if not wcl_char_name: continue

                    wcl_char_name_lower = wcl_char_name.lower()
                    matched_char_id = char_name_to_id_map.get(wcl_char_name_lower)
                    
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
                                spec_name=rank_entry.get('spec',{}).get('name'), # WCL spec name
                                metric="dps", rank_percentile=percentile
                            ))
                    # else:
                        # print(f"  Unmatched WCL player for performance: {wcl_char_name} in report {report_code}", flush=True)

            else:
                print(f"Warning: Could not get rankings for WCL report {report_code}.", flush=True)
            time.sleep(0.2) # Be respectful to WCL API

        # Batch insert WCL data
        if wcl_reports_in_db:
            print(f"\nInserting {len(wcl_reports_in_db)} WCL reports...", flush=True)
            db_session.add_all(wcl_reports_in_db)
            db_session.commit() # Commit reports first due to FK constraints
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

        # Update Character table with aggregated data
        if character_attendance_raw_counts:
            print("Updating character attendance percentages...", flush=True)
            update_count = 0
            if successfully_processed_wcl_reports_for_attendance > 0:
                for char_id, raw_count in character_attendance_raw_counts.items():
                    char_to_update = db_session.query(Character).get(char_id)
                    if char_to_update:
                        attendance_percentage = round((raw_count / successfully_processed_wcl_reports_for_attendance) * 100, 2)
                        char_to_update.raid_attendance_percentage = attendance_percentage
                        update_count += 1
                db_session.commit()
                print(f"Updated attendance percentage for {update_count} characters based on {successfully_processed_wcl_reports_for_attendance} successfully processed reports.", flush=True)
            else:
                print("No WCL reports were successfully processed for attendance details; cannot calculate attendance percentage.", flush=True)

        if character_performance_scores:
            print("Updating character average WCL performance...", flush=True)
            update_count = 0
            for char_id, scores in character_performance_scores.items():
                char_to_update = db_session.query(Character).get(char_id)
                if char_to_update and scores:
                    avg_perf = round(sum(scores) / len(scores), 2)
                    char_to_update.avg_wcl_performance = avg_perf
                    update_count +=1
            db_session.commit()
            print(f"Updated average performance for {update_count} characters.", flush=True)

    except IntegrityError as ie:
        print(f"Database Integrity Error during WCL data processing: {ie}", flush=True)
        db_session.rollback()
    except Exception as e:
        print(f"Error during WCL data processing: {e}", flush=True)
        db_session.rollback()
    finally:
        db_session.close()

    end_time = time.time()
    print(f"\nWCL data processing finished in {round(end_time - start_time, 2)} seconds.", flush=True)


# --- Main Execution ---
if __name__ == "__main__":
    # This script is intended to be run separately, e.g., via a scheduler
    # It assumes that update_roster_data.py has already run and populated the Character table.
    # It also assumes populate_item_db.py has run to create PlayableClass, PlayableSpec tables.
    
    # Ensure all tables are created if they don't exist (idempotent)
    # This is important if this script is run before update_roster_data.py has a chance to create all tables
    # or if a table was manually dropped.
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
