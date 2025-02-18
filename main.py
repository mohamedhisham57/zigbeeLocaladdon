import json
import base64
import requests
import time
import os
import re
from datetime import datetime
from influxdb import InfluxDBClient

# Load Configuration
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

# ✅ Read sensor IDs
sensor_ids_str = config.get("sensor_ids", "")
SENSOR_IDS = re.findall(r"\b\d{8}\b", sensor_ids_str)

if not SENSOR_IDS:
    print("⚠ No valid sensors configured! Please enter valid 8-digit sensor IDs in the Add-on settings.")
    exit(1)

# ✅ Connect to InfluxDB
try:
    client = InfluxDBClient(INFLUXDB_HOST, INFLUXDB_PORT, INFLUXDB_USER, INFLUXDB_PASSWORD, INFLUXDB_DBNAME)
    hold_client = InfluxDBClient(INFLUXDB_HOST, INFLUXDB_PORT, INFLUXDB_USER, INFLUXDB_PASSWORD, "Hold")  # Hold DB
    print(f"✅ Connected to InfluxDB: {INFLUXDB_HOST}:{INFLUXDB_PORT}")
except Exception as e:
    print(f"❌ Failed to connect to InfluxDB: {e}")
    exit(1)

# ✅ Variables to track last sent data
LAST_SENT_DATA = {}

# Function to get current date and time
def get_current_date_time():
    now = datetime.now()
    return now.strftime("%Y/%m/%d"), now.strftime("%H/%M/%S")

# ✅ Store failed readings in "Hold" database
def store_failed_reading(json_object):
    """Only store failed readings in the Hold database"""
    try:
        formatted_time = datetime.utcnow().isoformat() + "Z"  # Use UTC time

        point = {
            "measurement": "unsent_data",
            "tags": {
                "sensor_id": json_object["data"][0]["Sensorid"]
            },
            "time": formatted_time,
            "fields": {
                "json_data": json.dumps(json_object)
            }
        }

        hold_client.write_points([point])
        print(f"⚠ Data stored in Hold DB for sensor {json_object['data'][0]['Sensorid']}")
    except Exception as e:
        print(f"❌ Error storing data in Hold DB: {e}")

# ✅ Retry sending data from Hold database
def retry_failed_readings():
    """Send unsent readings from Hold DB"""
    query = 'SELECT * FROM "unsent_data"'
    try:
        result = hold_client.query(query)
        points = list(result.get_points())

        for point in points:
            json_object = json.loads(point["json_data"])
            sensor_id = json_object["data"][0]["Sensorid"]

            print(f"🔄 Retrying failed reading for sensor {sensor_id}")

            # ✅ Only delete if `send_json_to_server` returns True
            if send_json_to_server(json_object): 
                delete_query = f'''
                    DELETE FROM "unsent_data" WHERE time = '{point["time"]}'
                '''
                hold_client.query(delete_query)
                print(f"✅ Successfully resent and removed reading for sensor {sensor_id}")

    except Exception as e:
        print(f"❌ Error querying Hold database: {e}")

# ✅ Send JSON data to the server
def send_json_to_server(json_object):
    """Send data to server and delete from Hold DB only if responseCode is 200"""
    headers = {"token": ""}  # Empty token for now

    try:
        response = requests.post(ADD_READINGS_URI, headers=headers, json=json_object, verify=False, timeout=5)

        try:
            response_json = response.json()
        except json.JSONDecodeError:
            print(f"❌ Server responded with invalid JSON: {response.status_code} - {response.text}")
            store_failed_reading(json_object)
            return False

        # ✅ Check responseCode == 200
        if response_json.get("responseCode") == 200:
            print("✅ Data successfully sent, deleting from Hold DB")
            return True  
        else:
            print(f"❌ Server error: {response_json}")
            store_failed_reading(json_object)
            return False  

    except requests.RequestException as e:
        print(f"❌ Error sending data to server: {e}")
        store_failed_reading(json_object)
        return False  

# ✅ Fetch latest sensor readings
def fetch_latest_sensor_data(sensor_id):
    """Fetch the latest temperature and humidity for a given sensor"""
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
        humidity = humidity_points[0]["humidity"] if humidity_points else None
        humidity_timestamp = humidity_points[0]["time"] if humidity_points else None

        return temperature, humidity, temp_timestamp, humidity_timestamp

    except Exception as e:
        print(f"❌ InfluxDB query failed for {sensor_id}: {e}")
        return None, None, None, None

# ✅ Main loop to listen for sensor updates
def listen_for_new_data():
    print("🔄 Listening for new sensor updates...")

    while True:
        retry_failed_readings()  # Try to send unsent readings first

        for sensor_id in SENSOR_IDS:
            latest_temperature, latest_humidity, temp_timestamp, humidity_timestamp = fetch_latest_sensor_data(sensor_id)

            if latest_temperature is None and latest_humidity is None:
                continue  # Skip if no new data

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

            # ✅ Only send if data changed since last send
            if LAST_SENT_DATA.get(sensor_id) != json_object:
                LAST_SENT_DATA[sensor_id] = json_object
                send_json_to_server(json_object)

        time.sleep(10)  # ✅ Adjust delay to prevent excessive duplicate readings

# Start the main loop
if __name__ == "__main__":
    listen_for_new_data()
