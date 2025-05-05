# create_tables.py
import os
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
# Import Base and engine definition from update_roster_data
# Assuming update_roster_data.py is in the same directory
# Need to handle potential import errors if run before dependencies installed
try:
    from update_roster_data import Base, engine
except ImportError as e:
    print(f"Error importing from update_roster_data: {e}")
    print("Ensure update_roster_data.py exists and all dependencies in requirements.txt are installed.")
    exit(1)
except Exception as e:
    print(f"An unexpected error occurred during import: {e}")
    exit(1)


print("Attempting to create database tables...")

try:
    # Create tables based on the Base metadata
    # This command is idempotent - it won't hurt if tables already exist
    Base.metadata.create_all(bind=engine)
    print("Tables created successfully (or already exist).")
except OperationalError as e:
    print(f"Database connection error: {e}. Check DATABASE_URL and network.")
    exit(1)
except Exception as e:
    print(f"An error occurred during table creation: {e}")
    exit(1)

print("Script finished.")