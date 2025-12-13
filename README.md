# restrouterservice
Rest api router service


TO DO 
add db/file to store conditions 
add api route to fetch and update conditions
and try and catch to filtering
build web interface to manage conditions
make conditions case insensitive (lowercase all)
containerize full app
log payloads that match no condition
rules export and import

ADD auth support for destination APIs

uvicorn main:app --host 0.0.0.0 --port 5000 --reload
python -m uvicorn main:app --host 0.0.0.0 --port 5000 --reload
