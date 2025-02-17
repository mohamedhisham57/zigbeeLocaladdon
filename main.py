import json
import base64
import requests
import time
from datetime import datetime
from influxdb import InfluxDBClient

# InfluxDB Configuration
INFLUXDB_HOST = "192.168.0.127"  # Replace with Home Assistant IP
INFLUXDB_PORT = 8086
INFLUXDB_USER = "skarpt"
INFLUXDB_PASSWORD = "skarpt"
INFLUXDB_DBNAME = "Skarpt"

# API Server details
login_uri = 'https://iot.skarpt.net/java_bk/login'
add_readings_uri = 'https://iot.skarpt.net/java_bk/reports/addReadingsList'
token = ""

# Connect to InfluxDB
client = InfluxDBClient(INFLUXDB_HOST, INFLUXDB_PORT, INFLUXDB_USER, INFLUXDB_PASSWORD, INFLUXDB_DBNAME)

# Authentication Credentials
USERNAME = "demo@skarpt.net"  # Replace with actual username
PASSWORD = "Demo@2021"  # Replace with actual password


# Function to get current date and time
def get_current_date_time():
    now = datetime.now()
    return now.strftime("%Y/%m/%d"), now.strftime("%H/%M/%S")


# Function to log in and retrieve a token
def login():
    global token
    basic_auth = f"{USERNAME}:{PASSWORD}"
    encoded_u = base64.b64encode(basic_auth.encode()).decode()
    headers = {"Authorization": f"Basic {encoded_u}"}

    try:
        response = requests.get(login_uri, headers=headers, verify=False)
        if response.status_code == 200:
            token = response.json()['entity'][0]['token']
            print("Token acquired")
        else:
            print("Login failed:", response.text)
    except Exception as e:
        print("Login request failed:", e)


# Function to send data to cloud
def send_json_to_server(json_object):
    global token
    if not token:
        login()

    headers = {"token": token}
    try:
        response = requests.post(add_readings_uri, headers=headers, json=json_object, verify=False)
        if response.status_code == 200:
            print("Data successfully sent")
            return True
        else:
            print("Failed to send data:", response.status_code, response.text)
            return False
    except Exception as e:
        print("Error sending data to server:", e)
        return False


# Function to fetch data from InfluxDB and send to cloud
def fetch_from_influxdb():
    while True:
        print("Fetching data from InfluxDB...")
        query = '''
        SELECT "value", time
        FROM "autogen"."°C"
        WHERE time > now() - 10s
        ORDER BY time DESC
        '''

        try:
            result = client.query(query)
        except Exception as e:
            print("InfluxDB query failed:", e)
            time.sleep(10)
            continue

        current_date, current_time = get_current_date_time()

        json_object = {
            "GatewayId": "87654321",  # Fixed Gateway ID
            "GatewayBattery": 95,
            "GatewayPower": 3.2,
            "Date": current_date,
            "Time": current_time,
            "data": []
        }

        # Loop through all data points retrieved from InfluxDB
        for point in result.get_points():
            temperature = point["value"]  # Get the real temperature value

            json_object["data"].append({
                "Sensorid": "87654321",  # Fixed Sensor ID
                "humidity": 54,  # If humidity data is available, replace None with its value
                "temperature": temperature
            })

        if json_object["data"]:
            send_json_to_server(json_object)
        else:
            print("No new data found")

        time.sleep(10)


# Start fetching data from InfluxDB
fetch_from_influxdb()
