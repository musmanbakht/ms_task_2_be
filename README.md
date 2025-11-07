To Start The App Backend

python -m venv venv
venv/scripts/activate
pip install -r requirements.txt

uvicorn main:app --reload

GOOGLE EARTH ENGINE
Signup
CREATE a Project
REgister for nonn commercial use

Prerequisites:

Go to Google Cloud Console > APIs & Services > Credentials.
Create a new OAuth 2.0 Client ID:

Application type: Web application.
Authorized JavaScript origins: http://localhost:3000 (your React app URL).
Authorized redirect URIs: http://localhost:8000/api/auth/callback (your FastAPI endpoint).

Download the client secrets JSON (contains client_id and client_secret).
Enable the Earth Engine API in your project (if not already).
In the backend code, load the client secrets (e.g., from a file or env vars). I've shown it as a dict for simplicity.
Users must have signed up for Earth Engine (to grant the scope).
