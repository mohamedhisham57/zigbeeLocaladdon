import json
import base64
import requests
import time
import os
import re
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

# Debugging: Print the loaded configuration
print("🔍 Loaded Configuration:", json.dumps(config, indent=4))

# Load configuration from UI
INFLUXDB_HOST = config.get("influxdb_host", "")
INFLUXDB_PORT = config.get("influxdb_port", 8086)
INFLUXDB_USER = config.get("influxdb_user", "")
INFLUXDB_PASSWORD = config.get("influxdb_password", "")
INFLUXDB_DBNAME = config.get("influxdb_dbname", "")

LOGIN_URI = config.get("login_uri", "")
ADD_READINGS_URI = config.get("add_readings_uri", "")
USERNAME = config.get("username", "")
PASSWORD = config.get("password", "")

# ✅ Read sensor IDs as a **single comma-separated string** and split into 8-digit IDs
sensor_ids_str = config.get("sensor_ids", "")

# ✅ Extract only valid 8-digit numeric sensor IDs using regex
SENSOR_IDS = re.findall(r"\b\d{8}\b", sensor_ids_str)

# Debugging: Print the loaded sensor IDs
print("📡 Sensor IDs Loaded:", SENSOR_IDS)

if not SENSOR_IDS:
    print("⚠ No valid sensors configured! Please enter valid 8-digit sensor IDs in the Add-on settings.")
    exit(1)  # Exit script if no sensors are configured

# Ensure SENSOR_IDS is loaded before using
LAST_HUMIDITY = {sensor_id: None for sensor_id in SENSOR_IDS}
LAST_TEMP_TIMESTAMP = {sensor_id: None for sensor_id in SENSOR_IDS}
LAST_HUMIDITY_TIMESTAMP = {sensor_id: None for sensor_id in SENSOR_IDS}

# Connect to InfluxDB
try:
    client = InfluxDBClient(INFLUXDB_HOST, INFLUXDB_PORT, INFLUXDB_USER, INFLUXDB_PASSWORD, INFLUXDB_DBNAME)
    hold_client = InfluxDBClient(INFLUXDB_HOST, INFLUXDB_PORT, INFLUXDB_USER, INFLUXDB_PASSWORD, "Hold")
    print(f"✅ Connected to InfluxDB: {INFLUXDB_HOST}:{INFLUXDB_PORT}")
except Exception as e:
    print(f"❌ Failed to connect to InfluxDB: {e}")
    exit(1)  # Exit if InfluxDB connection fails

# Function to get current date and time
def get_current_date_time():
    now = datetime.now()
    return now.strftime("%Y/%m/%d"), now.strftime("%H/%M/%S")

# Function to log in and retrieve a token
TOKEN = ""

def login():
    global TOKEN
    if not LOGIN_URI or not USERNAME or not PASSWORD:
        print("❌ Login credentials are missing in the config!")
        return

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

# Function to store unsent data in the "Hold" database
def store_unsent_data(json_object):
    json_body = [
        {
            "measurement": "unsent_data",
            "tags": {"sensor_id": json_object["GatewayId"]},  # ✅ FIXED
            "fields": {
                "json_data": json.dumps(json_object)
            }
        }
    ]
    hold_client.write_points(json_body)
    print(f"⚠ Data stored in Hold DB for {json_object['GatewayId']}")

# Function to retry sending unsent data
def retry_sending_unsent_data():
    query = 'SELECT * FROM "Hold"."autogen"."unsent_data"'
    result = hold_client.query(query)
    points = list(result.get_points())

    for point in points:
        json_data = json.loads(point["json_data"])
        if send_json_to_server(json_data):  # If successful, remove from Hold DB
            hold_client.query(f'DELETE FROM "unsent_data" WHERE sensor_id = \'{json_data["GatewayId"]}\'')
            print(f"✅ Successfully sent and removed from Hold DB: {json_data['GatewayId']}")

# Function to send data to cloud
def send_json_to_server(json_object):
    global TOKEN
    if not TOKEN:
        login()

    if not ADD_READINGS_URI or not TOKEN:
        print("❌ Missing API endpoint or token. Skipping data send.")
        store_unsent_data(json_object)  # Store in Hold DB
        return False

    headers = {"token": TOKEN}
    try:
        response = requests.post(ADD_READINGS_URI, headers=headers, json=json_object, verify=False, timeout=5)
        if response.status_code == 200:
            print("✅ Data successfully sent")
            retry_sending_unsent_data()  # Try to send stored data when online
            return True
        else:
            print(f"❌ Failed to send data: {response.status_code} {response.text}")
            store_unsent_data(json_object)  # Store in Hold DB
            return False
    except requests.RequestException as e:
        print(f"❌ Error sending data to server: {e}")
        store_unsent_data(json_object)  # Store in Hold DB
        return False
def fetch_latest_sensor_data(sensor_id):
    global LAST_HUMIDITY  

    temp_query = f'''
    SELECT last("value") AS temperature, time 
    FROM "Skarpt"."autogen"."°C" 
    WHERE "entity_id" = '{sensor_id}_temperature'
    '''
    
    humidity_query = f'''
    SELECT last("value") AS humidity, time 
    FROM "Skarpt"."autogen"."%" 
    WHERE "entity_id" = '{sensor_id}_humidity'
    '''

    try:
        temp_result = client.query(temp_query)
        temp_points = list(temp_result.get_points())
        temperature = temp_points[0]["temperature"] if temp_points else None
        temp_timestamp = temp_points[0]["time"] if temp_points else None

        humidity_result = client.query(humidity_query)
        humidity_points = list(humidity_result.get_points())
        humidity = humidity_points[0]["humidity"] if humidity_points else LAST_HUMIDITY.get(sensor_id, None)
        humidity_timestamp = humidity_points[0]["time"] if humidity_points else None

        if humidity_points:
            LAST_HUMIDITY[sensor_id] = humidity

        return temperature, humidity, temp_timestamp, humidity_timestamp

    except Exception as e:
        print(f"❌ InfluxDB query failed for {sensor_id}: {e}")
        return None, None, None, None

# Function to listen for new sensor updates
def listen_for_new_data():
    print("🔄 Listening for new sensor updates...")

    while True:
        for sensor_id in SENSOR_IDS:
            latest_temperature, latest_humidity, temp_timestamp, humidity_timestamp = fetch_latest_sensor_data(sensor_id)

            if latest_temperature is None and latest_humidity is None:
                continue  # No data, skip

            current_date, current_time = get_current_date_time()

            json_object = {
                "GatewayId": "87654321", 
                "Date": current_date,
                "Time": current_time,
                "data": []
            }

            if latest_temperature is not None:
                json_object["data"].append({"Sensorid": sensor_id, "temperature": latest_temperature})

            if latest_humidity is not None:
                json_object["data"].append({"Sensorid": sensor_id, "humidity": latest_humidity})

            send_json_to_server(json_object)

        time.sleep(2)

# Start listening for updates
listen_for_new_data()
