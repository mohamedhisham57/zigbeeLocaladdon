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
INFLUXDB_DBNAME = config.get("influxdb_dbname", "")  # Skarpt Database
HOLD_DBNAME = "Hold"  # Hold Database for unsent data

LOGIN_URI = config.get("login_uri", "")
ADD_READINGS_URI = config.get("add_readings_uri", "")
USERNAME = config.get("username", "")
PASSWORD = config.get("password", "")

# ✅ Read sensor IDs as a single comma-separated string and extract valid 8-digit IDs
sensor_ids_str = config.get("sensor_ids", "")
SENSOR_IDS = re.findall(r"\b\d{8}\b", sensor_ids_str)

# Debugging: Print the loaded sensor IDs
print("📡 Sensor IDs Loaded:", SENSOR_IDS)

if not SENSOR_IDS:
    print("⚠ No valid sensors configured! Please enter valid 8-digit sensor IDs in the Add-on settings.")
    exit(1)

# Connect to InfluxDB
try:
    client = InfluxDBClient(INFLUXDB_HOST, INFLUXDB_PORT, INFLUXDB_USER, INFLUXDB_PASSWORD)
    client.switch_database(INFLUXDB_DBNAME)
    print(f"✅ Connected to InfluxDB: {INFLUXDB_HOST}:{INFLUXDB_PORT}")
except Exception as e:
    print(f"❌ Failed to connect to InfluxDB: {e}")
    exit(1)

# Function to get current date and time with "/" as separator
def get_current_date_time():
    now = datetime.now()
    return now.strftime("%Y/%m/%d"), now.strftime("%H/%M/%S")  # Ensures time is formatted with "/"

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

def fetch_latest_sensor_data(sensor_id):
    """Fetch the latest temperature and humidity for a given sensor from Skarpt DB"""
    temp_query = f'SELECT last("value") AS temperature FROM "Skarpt"."autogen"."°C" WHERE "entity_id" = \'{sensor_id}_temperature\''
    humidity_query = f'SELECT last("value") AS humidity FROM "Skarpt"."autogen"."%" WHERE "entity_id" = \'{sensor_id}_humidity\''

    try:
        temp_result = client.query(temp_query)
        temp_points = list(temp_result.get_points())
        temperature = temp_points[0]["temperature"] if temp_points else None

        humidity_result = client.query(humidity_query)
        humidity_points = list(humidity_result.get_points())
        humidity = humidity_points[0]["humidity"] if humidity_points else None

        return temperature, humidity

    except Exception as e:
        print(f"❌ InfluxDB query failed for {sensor_id}: {e}")
        return None, None

def store_reading_in_hold(json_object):
    """Store reading in Hold DB if it's not sent successfully"""
    try:
        current_time = datetime.utcnow().isoformat() + "Z"
        point = {
            "measurement": "unsent_data",
            "tags": {
                "sensor_id": json_object["data"][0]["Sensorid"]
            },
            "time": current_time,
            "fields": {
                "json_data": json.dumps(json_object)
            }
        }
        client.switch_database(HOLD_DBNAME)
        client.write_points([point])
        print(f"✅ Stored reading in Hold DB for sensor {json_object['data'][0]['Sensorid']}")
    except Exception as e:
        print(f"❌ Failed to store reading in Hold database: {e}")
    finally:
        client.switch_database(INFLUXDB_DBNAME)

def retry_failed_readings():
    """Retry sending failed readings from Hold DB"""
    query = 'SELECT * FROM "unsent_data"'

    try:
        client.switch_database(HOLD_DBNAME)
        result = client.query(query)
        points = list(result.get_points())

        for point in points:
            try:
                json_object = json.loads(point["json_data"])
                sensor_id = json_object["data"][0]["Sensorid"]

                print(f"🔄 Retrying failed reading for sensor {sensor_id}")

                if send_json_to_server(json_object): 
                    delete_query = f'DELETE FROM "unsent_data" WHERE time = \'{point["time"]}\''
                    client.query(delete_query)
                    print(f"✅ Successfully resent and removed reading for sensor {sensor_id}")

            except Exception as e:
                print(f"❌ Error processing stored reading: {e}")

    except Exception as e:
        print(f"❌ Error querying Hold database: {e}")

    finally:
        client.switch_database(INFLUXDB_DBNAME)

def send_json_to_server(json_object):
    """Send data to server and delete from Hold DB only if responseCode is 200"""
    global TOKEN
    if not TOKEN:
        login()

    if not ADD_READINGS_URI or not TOKEN:
        print("❌ Missing API endpoint or token. Storing in Hold database.")
        store_reading_in_hold(json_object)
        return False

    headers = {"token": TOKEN}
    try:
        response = requests.post(ADD_READINGS_URI, headers=headers, json=json_object, verify=False, timeout=5)

        try:
            response_json = response.json()
        except json.JSONDecodeError:
            print(f"❌ Server responded with invalid JSON: {response.status_code} - {response.text}")
            store_reading_in_hold(json_object)
            return False

        if response_json.get("responseCode") == 200:
            print("✅ Data successfully sent, deleting from Hold DB")
            return True
        else:
            print(f"❌ Server error: {response_json}")
            store_reading_in_hold(json_object)
            return False

    except requests.RequestException as e:
        print(f"❌ Error sending data to server: {e}")
        store_reading_in_hold(json_object)
        return False

def listen_for_new_data():
    """Main loop to listen for new sensor updates"""
    print("🔄 Listening for new sensor updates...")

    while True:
        if int(time.time()) % 60 == 0:
            retry_failed_readings()

        for sensor_id in SENSOR_IDS:
            temperature, humidity = fetch_latest_sensor_data(sensor_id)
            if temperature is None and humidity is None:
                continue

            current_date, current_time = get_current_date_time()
            json_object = {
                "GatewayId": "87654321",
                "Date": current_date,
                "Time": current_time,
                "data": [{"Sensorid": sensor_id, "humidity": humidity or 0, "temperature": temperature or 0}]
            }

            send_json_to_server(json_object)

        time.sleep(2)

if __name__ == "__main__":
    listen_for_new_data()
