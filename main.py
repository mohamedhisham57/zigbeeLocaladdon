import json
import base64
import requests
import time
import re
import aiohttp
import asyncio
import threading
from datetime import datetime
from influxdb import InfluxDBClient

# ✅ Load Configuration from Home Assistant Add-on Options
CONFIG_FILE = "/data/options.json"

try:
    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)
except FileNotFoundError:
    print("❌ Configuration file not found! Please check your Add-on settings.")
    exit(1)

# ✅ Load configuration from Home Assistant UI
INFLUXDB_HOST = config.get("influxdb_host", "")
INFLUXDB_PORT = config.get("influxdb_port", 8086)
INFLUXDB_USER = config.get("influxdb_user", "")
INFLUXDB_PASSWORD = config.get("influxdb_password", "")
INFLUXDB_DBNAME = config.get("influxdb_dbname", "")
HOLD_DBNAME = "Hold"

LOGIN_URI = config.get("login_uri", "")
ADD_READINGS_URI = config.get("add_readings_uri", "")
USERNAME = config.get("username", "")
PASSWORD = config.get("password", "")
GATEWAY_ID = config.get("gateway_id", "")

# ✅ Extract sensor IDs from config
sensor_ids_str = config.get("sensor_ids", "")
SENSOR_IDS = re.findall(r"\b\d{8}\b", sensor_ids_str)

LAST_READINGS = {}
ACTIVE_SENSORS = set(SENSOR_IDS)  # Keeps track of sensors that should still send data

# ✅ Connect to InfluxDB
try:
    client = InfluxDBClient(INFLUXDB_HOST, INFLUXDB_PORT, INFLUXDB_USER, INFLUXDB_PASSWORD)
    client.switch_database(INFLUXDB_DBNAME)
    print(f"✅ Connected to InfluxDB: {INFLUXDB_HOST}:{INFLUXDB_PORT}")
except Exception as e:
    print(f"❌ Failed to connect to InfluxDB: {e}")
    exit(1)

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


# ✅ Function to get current date and time
def get_current_date_time():
    now = datetime.now()
    return now.strftime("%Y/%m/%d"), now.strftime("%H/%M/%S")


# ✅ Function to check if a sensor is available in Home Assistant
async def check_sensor_availability(sensor_id):
    url = f'http://192.168.0.127:8123/api/states/sensor.{sensor_id}_temperature'
    headers = {
        'Authorization': 'Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiIwMjFkY2I5ZDU2NmQ0MzZhYjYzZTUzZmYwNTdjZjMxMCIsImlhdCI6MTc0MDQ3MzM0MywiZXhwIjoyMDU1ODMzMzQzfQ.ySMx8xhVOI4HWSUn3bWdUlhfo0td_fm1347gKOIPdxY',  # Ensure 'Bearer' is included
        'Content-Type': 'application/json',
    }

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                state_data = await response.json()
                sensor_state = state_data['state']

                if sensor_state == "unavailable":
                    print(f"⚠️ Sensor {sensor_id} is unavailable! Stopping updates for this sensor.")
                    ACTIVE_SENSORS.discard(sensor_id)  # ✅ Stop sending updates but keep checking
                elif sensor_id not in ACTIVE_SENSORS:
                    print(f"✅ Sensor {sensor_id} is back online! Resuming updates.")
                    ACTIVE_SENSORS.add(sensor_id)  # ✅ Add sensor back if it recovers

                return sensor_state

            elif response.status == 404:
                print(f"🚨 Sensor {sensor_id} no longer exists! Removing it permanently.")
                ACTIVE_SENSORS.discard(sensor_id)  # ✅ Remove it permanently

            else:
                print(f"❌ Error fetching state for {sensor_id}: {response.status}")
                return None

# ✅ Function to check all sensors asynchronously
async def check_all_sensors():
    tasks = [check_sensor_availability(sensor_id) for sensor_id in SENSOR_IDS]
    await asyncio.gather(*tasks)


# ✅ Fetch latest temperature and humidity from InfluxDB
def fetch_latest_sensor_data(sensor_id):
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

#*********************************************************************************************************
def store_reading_in_hold(json_object):
    """Store an unsent reading in Hold DB only if it has not been stored already."""
    try:
        sensor_id = json_object["data"][0]["Sensorid"]
        json_data = json.dumps(json_object)  # Convert JSON to string for exact match
        current_time = datetime.utcnow().isoformat() + "Z"

        # ✅ Check if this exact JSON payload is already stored
        query = f'''
            SELECT COUNT(*) FROM "unsent_data"
            WHERE "sensor_id" = '{sensor_id}'
            AND "json_data" = '{json_data}'
        '''
        client.switch_database(HOLD_DBNAME)
        result = client.query(query)

        # ✅ If COUNT > 0, it means this reading has already been stored
        if result and list(result.get_points())[0]['count'] > 0:
            print(f"⚠️ Reading for sensor {sensor_id} already in Hold DB. Skipping save.")
            return  # ✅ Avoid saving the same failed retry

        # ✅ If not stored yet, save the unsent reading
        point = {
            "measurement": "unsent_data",
            "tags": {"sensor_id": sensor_id},
            "time": current_time,
            "fields": {"json_data": json_data}
        }

        client.write_points([point])
        print(f"✅ Stored new unsent reading in Hold DB for sensor {sensor_id}")

    except Exception as e:
        print(f"❌ Failed to store reading in Hold database: {e}")

    finally:
        client.switch_database(INFLUXDB_DBNAME)  # Switch back to the main database

#*********************************************************************************************************

# ✅ Function to retry sending failed readings
def retry_failed_readings():
    query = 'SELECT * FROM "unsent_data" LIMIT 10'
    try:
        client.switch_database(HOLD_DBNAME)
        result = client.query(query)
        points = list(result.get_points())

        if not points:
            print("ℹ️ No failed readings to retry.")
            return

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


# ✅ Function to listen for new data and send only available sensor readings
def listen_for_new_data():
    print("🔄 Listening for new sensor updates...")
    while True:
        # ✅ Check all sensors before fetching data
        asyncio.run(check_all_sensors())
        retry_failed_readings()

        for sensor_id in list(ACTIVE_SENSORS):
            temperature, humidity = fetch_latest_sensor_data(sensor_id)

            last_temp, last_hum = LAST_READINGS.get(sensor_id, (None, None))

            if temperature is not None and humidity is not None:
                LAST_READINGS[sensor_id] = (temperature, humidity)
            else:
                temperature, humidity = last_temp, last_hum
                print(
                    f"⚠️ No new data for {sensor_id}, reusing last known values: Temp={temperature}, Humidity={humidity}")

            if temperature is not None and humidity is not None:
                current_date, current_time = get_current_date_time()
                json_object = {
                    "GatewayId": GATEWAY_ID,
                    "Date": current_date,
                    "Time": current_time,
                    "data": [{"Sensorid": sensor_id, "humidity": humidity or 0, "temperature": temperature or 0}]
                }
                send_json_to_server(json_object)

        time.sleep(60)  # ✅ Wait 1 minute before checking again


# ✅ Start data listener in a separate thread
def start_data_listener():
    listener_thread = threading.Thread(target=listen_for_new_data, daemon=True)
    listener_thread.start()
    print("✅ Sensor data listener started...")


if __name__ == "__main__":
    start_data_listener()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("🚫 Script terminated by user.")
        time.sleep(1)
