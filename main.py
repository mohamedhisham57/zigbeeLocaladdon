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

# ✅ Read sensor IDs as a single comma-separated string and split into 8-digit IDs
sensor_ids_str = config.get("sensor_ids", "")

# ✅ Extract only valid 8-digit numeric sensor IDs using regex
SENSOR_IDS = re.findall(r"\b\d{8}\b", sensor_ids_str)

# Debugging: Print the loaded sensor IDs
print("📡 Sensor IDs Loaded:", SENSOR_IDS)

if not SENSOR_IDS:
    print("⚠ No valid sensors configured! Please enter valid 8-digit sensor IDs in the Add-on settings.")
    exit(1)  # Exit script if no sensors are configured

# Initialize last known values
LAST_HUMIDITY = {sensor_id: None for sensor_id in SENSOR_IDS}
LAST_TEMP_TIMESTAMP = {sensor_id: None for sensor_id in SENSOR_IDS}
LAST_HUMIDITY_TIMESTAMP = {sensor_id: None for sensor_id in SENSOR_IDS}

# Connect to InfluxDB
try:
    client = InfluxDBClient(INFLUXDB_HOST, INFLUXDB_PORT, INFLUXDB_USER, INFLUXDB_PASSWORD, INFLUXDB_DBNAME)
    print(f"✅ Connected to InfluxDB: {INFLUXDB_HOST}:{INFLUXDB_PORT}")
except Exception as e:
    print(f"❌ Failed to connect to InfluxDB: {e}")
    exit(1)

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

def store_failed_reading(json_object):
    """Store failed reading in Home Assistant's InfluxDB Hold database"""
    try:
        # Convert the JSON object to InfluxDB point format
        current_time = datetime.now()
        point = {
            "measurement": "unsent_data",  # Changed to match your database structure
            "tags": {
                "sensor_id": json_object["data"][0]["Sensorid"]
            },
            "time": current_time,
            "fields": {
                "json_data": json.dumps(json_object)  # Store the entire JSON as a string
            }
        }
        
        # Specify the database name as 'Hold'
        client.write_points([point], database='Hold')
        print(f"✅ Failed reading stored in Hold database for sensor {json_object['data'][0]['Sensorid']}")
        return True
    except Exception as e:
        print(f"❌ Failed to store reading in Hold database: {e}")
        return False

def retry_failed_readings():
    """Attempt to resend failed readings from Hold database"""
    query = 'SELECT * FROM "unsent_data"'

    try:
        client.switch_database('Hold')  # Ensure querying the correct database
        result = client.query(query)
        points = list(result.get_points())

        for point in points:
            try:
                json_object = json.loads(point["json_data"])
                sensor_id = json_object["data"][0]["Sensorid"]
                
                print(f"🔄 Retrying failed reading for sensor {sensor_id}")

                # ✅ Only delete after a **successful** send
                if send_json_to_server(json_object):
                    delete_query = f'''
                        DELETE FROM "unsent_data" WHERE time = '{point["time"]}'
                    '''
                    client.query(delete_query)
                    print(f"✅ Successfully resent and removed reading for sensor {sensor_id}")

            except Exception as e:
                print(f"❌ Error processing stored reading: {e}")
                continue

    except Exception as e:
        print(f"❌ Error querying Hold database: {e}")

    finally:
        client.switch_database(INFLUXDB_DBNAME)  # Switch back to main DB

def send_json_to_server(json_object):
    """Send data to server with error handling"""
    global TOKEN
    if not TOKEN:
        login()

    if not ADD_READINGS_URI or not TOKEN:
        print("❌ Missing API endpoint or token. Storing in Hold database.")
        return store_failed_reading(json_object)

    headers = {"token": TOKEN}
    try:
        response = requests.post(ADD_READINGS_URI, headers=headers, json=json_object, verify=False, timeout=5)
        if response.status_code == 200:
            print("✅ Data successfully sent")
            return True
        else:
            print(f"❌ Failed to send data: {response.status_code} {response.text}")
            return store_failed_reading(json_object)
    except requests.RequestException as e:
        print(f"❌ Error sending data to server: {e}")
        return store_failed_reading(json_object)

def fetch_latest_sensor_data(sensor_id):
    """Fetch the latest temperature and humidity for a given sensor"""
    global LAST_HUMIDITY

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
        temp_timestamp = temp_points[0]["time"] if temp_points else None

        # Fetch Humidity Data
        humidity_result = client.query(humidity_query)
        humidity_points = list(humidity_result.get_points())
        humidity = humidity_points[0]["humidity"] if humidity_points else LAST_HUMIDITY.get(sensor_id, None)
        humidity_timestamp = humidity_points[0]["time"] if humidity_points else None

        # Update last known humidity
        if humidity_points:
            LAST_HUMIDITY[sensor_id] = humidity

        return temperature, humidity, temp_timestamp, humidity_timestamp

    except Exception as e:
        print(f"❌ InfluxDB query failed for {sensor_id}: {e}")
        return None, None, None, None

def listen_for_new_data():
    """Main loop to listen for new sensor updates"""
    print("🔄 Listening for new sensor updates...")

    while True:
        # Try to resend failed readings every minute
        if int(time.time()) % 60 == 0:
            retry_failed_readings()
        
        for sensor_id in SENSOR_IDS:
            latest_temperature, latest_humidity, temp_timestamp, humidity_timestamp = fetch_latest_sensor_data(sensor_id)

            temp_changed = temp_timestamp and temp_timestamp != LAST_TEMP_TIMESTAMP.get(sensor_id, None)
            humidity_changed = humidity_timestamp and humidity_timestamp != LAST_HUMIDITY_TIMESTAMP.get(sensor_id, None)

            if temp_changed or humidity_changed:
                print(f"📢 New reading: {sensor_id} | {latest_temperature}°C, {latest_humidity}% at {temp_timestamp if temp_changed else humidity_timestamp}")

                if temp_changed:
                    LAST_TEMP_TIMESTAMP[sensor_id] = temp_timestamp
                if humidity_changed:
                    LAST_HUMIDITY_TIMESTAMP[sensor_id] = humidity_timestamp

                current_date, current_time = get_current_date_time()

                json_object = {
                    "GatewayId": '87654321',
                    "Date": current_date,
                    "Time": current_time,
                    "data": [
                        {
                            "Sensorid": sensor_id,
                            "humidity": latest_humidity if latest_humidity is not None else 0,
                            "temperature": latest_temperature if latest_temperature is not None else 0
                        }
                    ]
                }

                send_json_to_server(json_object)

        time.sleep(2)

# Start the main loop
if __name__ == "__main__":
    listen_for_new_data()
