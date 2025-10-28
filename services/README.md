to start the app

python -m venv venv
venv/scripts/activate
pip install -r requirements.txt

uvicorn main:app --reload
