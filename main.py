import json
import base64
import requests
import time
import re
from datetime import datetime
from influxdb import InfluxDBClient

# ✅ Load Configuration from Home Assistant Add-on Options
CONFIG_FILE = "/data/options.json"

try:
    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)
except FileNotFoundError:
    print("❌ Configuration file not found! Please check your Add-on settings.")
    exit(1)  # Exit if no configuration file

# ✅ Load configuration from Home Assistant UI
INFLUXDB_HOST = config.get("influxdb_host", "")
INFLUXDB_PORT = config.get("influxdb_port", 8086)
INFLUXDB_USER = config.get("influxdb_user", "")
INFLUXDB_PASSWORD = config.get("influxdb_password", "")
INFLUXDB_DBNAME = config.get("influxdb_dbname", "")
HOLD_DBNAME = "Hold"  # ✅ Keeping the same Hold DB name

LOGIN_URI = config.get("login_uri", "")
ADD_READINGS_URI = config.get("add_readings_uri", "")
USERNAME = config.get("username", "")
PASSWORD = config.get("password", "")
GATEWAY_ID = config.get("gateway_id", "")

# ✅ Extract sensor IDs from config
sensor_ids_str = config.get("sensor_ids", "")
SENSOR_IDS = re.findall(r"\b\d{8}\b", sensor_ids_str)

# ✅ Debugging: Print loaded configurations
print("📡 Sensor IDs Loaded:", SENSOR_IDS)
print("🛠 Gateway ID:", GATEWAY_ID)
print(f"🔌 InfluxDB: {INFLUXDB_HOST}:{INFLUXDB_PORT}, User: {INFLUXDB_USER}")

# ✅ Ensure required configurations are provided
if not (INFLUXDB_HOST and INFLUXDB_USER and INFLUXDB_PASSWORD and INFLUXDB_DBNAME and LOGIN_URI and ADD_READINGS_URI and USERNAME and PASSWORD and GATEWAY_ID and SENSOR_IDS):
    print("❌ Missing required configuration! Please fill in the Home Assistant UI fields.")
    exit(1)

# ✅ Store the last known readings
LAST_READINGS = {}

# ✅ Connect to InfluxDB
try:
    client = InfluxDBClient(INFLUXDB_HOST, INFLUXDB_PORT, INFLUXDB_USER, INFLUXDB_PASSWORD)
    client.switch_database(INFLUXDB_DBNAME)
    print(f"✅ Connected to InfluxDB: {INFLUXDB_HOST}:{INFLUXDB_PORT}")
except Exception as e:
    print(f"❌ Failed to connect to InfluxDB: {e}")
    exit(1)

# ✅ Function to get current date and time
def get_current_date_time():
    now = datetime.now()
    return now.strftime("%Y/%m/%d"), now.strftime("%H/%M/%S")

# ✅ Function to log in and retrieve a token
TOKEN = ""

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

# ✅ Store reading in Hold DB if not sent successfully
def store_reading_in_hold(json_object):
    """Store failed reading in Hold DB only if it does not already exist."""
    try:
        sensor_id = json_object["data"][0]["Sensorid"]
        current_time = datetime.utcnow().isoformat() + "Z"

        # ✅ Check if the reading already exists in Hold
        query = f'SELECT * FROM "unsent_data" WHERE sensor_id = \'{sensor_id}\''
        client.switch_database(HOLD_DBNAME)
        result = client.query(query)
        points = list(result.get_points())

        if points:
            print(f"⚠️ Duplicate found! Skipping duplicate store for sensor {sensor_id}")
            return  # ✅ Avoid storing the same failed reading again

        # ✅ If not duplicate, store the reading
        point = {
            "measurement": "unsent_data",
            "tags": {"sensor_id": sensor_id},
            "time": current_time,
            "fields": {"json_data": json.dumps(json_object)}
        }

        client.write_points([point])
        print(f"✅ Stored reading in Hold DB for sensor {sensor_id}")

    except Exception as e:
        print(f"❌ Failed to store reading in Hold database: {e}")

    finally:
        client.switch_database(INFLUXDB_DBNAME)

# ✅ Retry sending failed readings from Hold DB
def retry_failed_readings():
    query = 'SELECT * FROM "unsent_data" LIMIT 10'
    try:
        client.switch_database(HOLD_DBNAME)
        result = client.query(query)
        points = list(result.get_points())

        if not points:
            print("ℹ️ No failed readings to retry.")
            return  # ✅ Exit early if there's nothing to process

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

# ✅ Send data to the server and store in Hold DB if it fails
def send_json_to_server(json_object):
    global TOKEN
    if not TOKEN:
        login()

    headers = {"token": TOKEN}
    try:
        response = requests.post(ADD_READINGS_URI, headers=headers, json=json_object, verify=False, timeout=5)

        try:
            response_json = response.json()
            if response_json.get("responseCode") == 200 and response_json.get("message") == "success":
                print("✅ Data successfully sent")
                return True
            else:
                print(f"❌ Server rejected data: {response_json}")
                store_reading_in_hold(json_object)
                return False

        except json.JSONDecodeError:
            print(f"❌ Server responded with invalid JSON: {response.status_code} - {response.text}")
            store_reading_in_hold(json_object)
            return False

    except requests.RequestException as e:
        print(f"❌ Error sending data to server: {e}")
        store_reading_in_hold(json_object)
        return False

# ✅ Main loop to listen for new sensor updates
def listen_for_new_data():
    print("🔄 Listening for new sensor updates...")

    while True:
        retry_failed_readings()

        for sensor_id in SENSOR_IDS:
            temperature, humidity = fetch_latest_sensor_data(sensor_id)

            last_temp, last_hum = LAST_READINGS.get(sensor_id, (None, None))
            if (temperature, humidity) == (last_temp, last_hum):
                continue  # ✅ Skip if values haven't changed

            LAST_READINGS[sensor_id] = (temperature, humidity)

            current_date, current_time = get_current_date_time()
            json_object = {
                "GatewayId": GATEWAY_ID,  # ✅ Now using the Gateway ID from UI
                "Date": current_date,
                "Time": current_time,
                "data": [{"Sensorid": sensor_id, "humidity": humidity or 0, "temperature": temperature or 0}]
            }

            send_json_to_server(json_object)

        time.sleep(5)

import threading

def start_data_listener():
    """Starts the sensor data listener in a separate thread."""
    listener_thread = threading.Thread(target=listen_for_new_data, daemon=True)
    listener_thread.start()
    print(" Sensor data listener started...")

if __name__ == "__main__":
    start_data_listener()
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print(" Script terminated by user.")
