import json
import base64
import requests
import time
import os
from datetime import datetime
from influxdb import InfluxDBClient

# Load Configuration from Home Assistant Add-on Options
CONFIG_FILE = "/data/options.json"
try:
    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)
except FileNotFoundError:
    print("❌ Configuration file not found! Please check your Add-on settings.")
    config = {}

# *Load InfluxDB Configuration from UI*
INFLUXDB_HOST = config.get("influxdb_host", "")
INFLUXDB_PORT = config.get("influxdb_port", 8086)
INFLUXDB_USER = config.get("influxdb_user", "")
INFLUXDB_PASSWORD = config.get("influxdb_password", "")
INFLUXDB_DBNAME = config.get("influxdb_dbname", "")

# *Load API Configuration from UI*
LOGIN_URI = config.get("login_uri", "")
ADD_READINGS_URI = config.get("add_readings_uri", "")
USERNAME = config.get("username", "")
PASSWORD = config.get("password", "")
TOKEN = ""

# *Load Sensor IDs from UI*
SENSOR_IDS = config.get("sensor_ids", [])

# *Warn if No Sensors Are Configured*
if not SENSOR_IDS:
    print("⚠ No sensors configured! Please enter sensor IDs in the Add-on settings.")

# *Store last known humidity values & timestamps for each sensor*
LAST_HUMIDITY = {sensor_id: None for sensor_id in SENSOR_IDS}
LAST_TEMP_TIMESTAMP = {sensor_id: None for sensor_id in SENSOR_IDS}
LAST_HUMIDITY_TIMESTAMP = {sensor_id: None for sensor_id in SENSOR_IDS}

# *Connect to InfluxDB AFTER Loading Configuration*
try:
    client = InfluxDBClient(INFLUXDB_HOST, INFLUXDB_PORT, INFLUXDB_USER, INFLUXDB_PASSWORD, INFLUXDB_DBNAME)
    print(f"✅ Connected to InfluxDB: {INFLUXDB_HOST}:{INFLUXDB_PORT}")
except Exception as e:
    print(f"❌ Failed to connect to InfluxDB: {e}")


# *Function to get current date and time*
def get_current_date_time():
    now = datetime.now()
    return now.strftime("%Y/%m/%d"), now.strftime("%H/%M/%S")


# *Function to log in and retrieve a token*
def login():
    global TOKEN
    basic_auth = f"{USERNAME}:{PASSWORD}"
    encoded_u = base64.b64encode(basic_auth.encode()).decode()
    headers = {"Authorization": f"Basic {encoded_u}"}

    try:
        response = requests.get(LOGIN_URI, headers=headers, verify=False, timeout=5)
        if response.status_code == 200:
            TOKEN = response.json().get('entity', [{}])[0].get('token', "")
            print("✅ Token acquired successfully")
        else:
            print(f"❌ Login failed: {response.status_code} - {response.text}")
    except requests.RequestException as e:
        print(f"❌ Login request failed: {e}")


# *Function to send data to cloud*
def send_json_to_server(json_object):
    global TOKEN
    if not TOKEN:
        login()

    headers = {"token": TOKEN}
    try:
        response = requests.post(ADD_READINGS_URI, headers=headers, json=json_object, verify=False, timeout=5)
        if response.status_code == 200:
            print("✅ Data successfully sent")
            return True
        else:
            print(f"❌ Failed to send data: {response.status_code} {response.text}")
            return False
    except requests.RequestException as e:
        print(f"❌ Error sending data to server: {e}")
        return False


# *Function to fetch the latest temperature and humidity for a given sensor*
def fetch_latest_sensor_data(sensor_id):
    global LAST_HUMIDITY  # Track last known humidity value

    # Query for latest temperature
    temp_query = f'''
    SELECT last("value") AS temperature, time 
    FROM "Skarpt"."autogen"."°C" 
    WHERE "entity_id" = '{sensor_id}_temperature'
    '''

    # Query for latest humidity
    humidity_query = f'''
    SELECT last("value") AS humidity, time 
    FROM "Skarpt"."autogen"."%" 
    WHERE "entity_id" = '{sensor_id}_humidity'
    '''

    try:
        # Fetch Temperature Data
        temp_result = client.query(temp_query)
        temp_points = list(temp_result.get_points())
        temperature = temp_points[0]["temperature"] if temp_points else None
        temp_timestamp = temp_points[0]["time"] if temp_points else None  # Temperature timestamp

        # Fetch Humidity Data
        humidity_result = client.query(humidity_query)
        humidity_points = list(humidity_result.get_points())
        humidity = humidity_points[0]["humidity"] if humidity_points else LAST_HUMIDITY.get(sensor_id, None)
        humidity_timestamp = humidity_points[0]["time"] if humidity_points else None  # Humidity timestamp

        # Update last known humidity
        if humidity_points:
            LAST_HUMIDITY[sensor_id] = humidity

        return temperature, humidity, temp_timestamp, humidity_timestamp

    except Exception as e:
        print(f"❌ InfluxDB query failed for {sensor_id}: {e}")
        return None, None, None, None


# *Function to listen for new sensor updates for all sensors*
def listen_for_new_data():
    print("🔄 Listening for new sensor updates...")

    while True:
        for sensor_id in SENSOR_IDS:
            latest_temperature, latest_humidity, temp_timestamp, humidity_timestamp = fetch_latest_sensor_data(sensor_id)

            # *Check if temperature changed*
            temp_changed = temp_timestamp and temp_timestamp != LAST_TEMP_TIMESTAMP.get(sensor_id, None)
            humidity_changed = humidity_timestamp and humidity_timestamp != LAST_HUMIDITY_TIMESTAMP.get(sensor_id, None)

            # If *either timestamp* is new, process the data
            if temp_changed or humidity_changed:
                print(f"📢 New sensor reading detected: {sensor_id} | {latest_temperature}°C, {latest_humidity}% at {temp_timestamp if temp_changed else humidity_timestamp}")

                # *Update timestamps individually*
                if temp_changed:
                    LAST_TEMP_TIMESTAMP[sensor_id] = temp_timestamp
                if humidity_changed:
                    LAST_HUMIDITY_TIMESTAMP[sensor_id] = humidity_timestamp

                current_date, current_time = get_current_date_time()

                json_object = {
                    "GatewayId": sensor_id,  # Use sensor ID as Gateway ID
                    "Date": current_date,
                    "Time": current_time,
                    "data": [
                        {
                            "Sensorid": sensor_id,  # Use sensor ID as Sensor ID
                            "humidity": latest_humidity if latest_humidity is not None else 0,
                            "temperature": latest_temperature if latest_temperature is not None else 0
                        }
                    ]
                }

                send_json_to_server(json_object)  # Send data to cloud

        time.sleep(2)  # Small delay to avoid excessive queries


# *Start listening for updates*
listen_for_new_data()
