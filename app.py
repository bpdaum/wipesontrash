# app.py
# Import the Flask library
from flask import Flask, render_template
import os # Import os to potentially use environment variables later

# Create an instance of the Flask class.
# __name__ is a special Python variable that gets the name of the current module.
# Flask uses this to know where to look for resources like templates and static files.
app = Flask(__name__)

# Define a route for the homepage ('/') of the website.
# The @app.route decorator tells Flask what URL should trigger our function.
@app.route('/')
def home():
  """
  This function runs when someone visits the root URL ('/').
  It renders the 'index.html' template.
  """
  # The render_template function looks for HTML files in the 'templates' folder.
  # We can pass variables to the template here if needed in the future.
  guild_name = "Your Guild Name" # Example variable
  return render_template('index.html', guild_name=guild_name)

# This block ensures the development server runs only when the script is executed directly
# (not when imported as a module).
# It's useful for local testing. Heroku uses the Procfile and Gunicorn instead.
if __name__ == '__main__':
  # Get port from environment variable for Heroku compatibility, default to 5000 locally
  port = int(os.environ.get('PORT', 5000))
  # Run the app. debug=True enables auto-reloading and detailed error pages during development.
  # Set host='0.0.0.0' to make the server accessible on your network.
  # Set debug=False when deploying to production (Heroku).
  app.run(host='0.0.0.0', port=port, debug=True) # Set debug=False for production