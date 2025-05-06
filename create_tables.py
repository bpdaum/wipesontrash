# create_tables.py
import os
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
# Import Base and engine definition from update_roster_data
# Assuming update_roster_data.py is in the same directory
# Need to handle potential import errors if run before dependencies installed
try:
    # We need the Base object which contains the table metadata
    # and the engine object to connect to the database.
    from update_roster_data import Base, engine
except ImportError as e:
    print(f"Error importing from update_roster_data: {e}")
    print("Ensure update_roster_data.py exists and all dependencies in requirements.txt are installed.")
    exit(1) # Exit if import fails
except Exception as e:
    print(f"An unexpected error occurred during import: {e}")
    exit(1) # Exit on other import errors


print("Attempting to create database tables...")

try:
    # Connect to the database and create tables based on the Base metadata.
    # This command is idempotent, meaning it's safe to run even if the tables already exist.
    # It will only create tables that are missing.
    print(f"Binding metadata to engine: {engine}")
    Base.metadata.create_all(bind=engine)
    print("Tables created successfully (or already exist).")
# Catch specific database connection errors
except OperationalError as e:
    print(f"Database connection error: {e}. Check DATABASE_URL and network connectivity.")
    exit(1) # Exit if connection fails
# Catch other potential errors during table creation
except Exception as e:
    print(f"An error occurred during table creation: {e}")
    exit(1) # Exit on other errors

print("Script finished.")
