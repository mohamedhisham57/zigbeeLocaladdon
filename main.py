import asyncore
import binascii
import json
import signal
import sys
from datetime import datetime
from influxdb import InfluxDBClient
import json

with open("/data/options.json", "r") as config_file:
    config = json.load(config_file)

# Fetch the values
DATABASE_PORT = config.get('database_port', '8086')  # Default to '8086' if not set
USERNAME_DATABASE = config.get('username_database', 'default_username')
PASSWORD_DATABASE = config.get('password_database', 'default_password')
INTERNAL_BACKUP_DATABASE_NAME = config.get('internal_backup_database_name', 'default_backup_db')
INTERNAL_DATABASE_NAME = config.get('internal_database_name', 'default_internal_db')
DATABASE_IP = config.get('database_ip', '127.0.0.1')
measurement = config.get('measurement', 'default_measurement')
full_packet_list = []
print("Database Port:", DATABASE_PORT)
print("Username:", USERNAME_DATABASE)
print("Password:", PASSWORD_DATABASE)
print("Backup DB Name:", INTERNAL_BACKUP_DATABASE_NAME)
print("Internal DB Name:", INTERNAL_DATABASE_NAME)
print("Database IP:", DATABASE_IP)
print("Measurement:", measurement)
def Update_ACK(Packetindex):
    global responsePacket, response2
    # str = '@CMD,*000000,@ACK,'+Packetindex+'#,#'
    str1 = '@ACK,' + Packetindex + '#'
    str1 = str1.encode('utf-8')
    responsePacket = str1.hex()
    response2 = "Server UTC time:" + str(datetime.now())[:19]
    response2 = response2.encode('utf-8')
    response2 = response2.hex()
def ConvertRTCtoTime(RTC):
    Year, Month, Day, Hours, Min, Sec = RTC[0:2], RTC[2:4], RTC[4:6], RTC[6:8], RTC[8:10], RTC[10:12]
    Year, Month, Day, Hours, Min, Sec = int(Year, 16), int(Month, 16), int(Day, 16), int(Hours, 16), int(Min, 16), int(
        Sec, 16)
    print("Date is ", Year, "/", Month, "/", Day)
    print("Time is ", Hours, "/", Min, "/", Sec)
    Date = str(Year) + "/" + str(Month) + "/" + str(Day)
    Time = str(Hours) + "/" + str(Min) + "/" + str(Sec)
    # return  Year, Month, Day, Hours, Min, Sec
    return Date, Time

def TempFun(temp):
    sign = ''
    hexadecimal = temp
    end_length = len(hexadecimal) * 4
    hex_as_int = int(hexadecimal, 16)
    hex_as_binary = bin(hex_as_int)
    padded_binary = hex_as_binary[2:].zfill(end_length)
    normalbit = padded_binary[0]
    postitive = padded_binary[1]
    value = padded_binary[2:]
    if str(normalbit) == '0':
        pass
    else:
        return "Sensor error"

    if str(postitive) == '0':
        sign = '+'
    else:
        sign = '-'

    if sign == '+':
        return str(int(value, 2) / 10)

    else:
        return "-" + str(int(value, 2) / 10)


def HumFun(hum):
    hexadecimal = hum
    end_length = len(hexadecimal) * 4
    hex_as_int = int(hexadecimal, 16)
    hex_as_binary = bin(hex_as_int)
    padded_binary = hex_as_binary[2:].zfill(end_length)
    normalbit = padded_binary[0]
    value = padded_binary[1:]
    if str(normalbit) == '0':
        pass
    else:
        return "Sensor error"
    return str(int(value, 2))

def Save_IndexNum(index) :
    textfile = open("IndexNum.txt", "w")
    textfile.write(str (index))
    textfile.close()
def Load_IndexNum () :
    text_file = open("IndexNum.txt", "r")
    lines = text_file.readlines()
    Nlist = [i.replace("\n","").strip() for i in lines ]
    return int (Nlist[0])
def Set_IndexNumber () :
    Save_IndexNum(0)
def BuildJsonDataBase(Date, Time, Temp, Hum, Battery, GateWayID, SensorID):
    listofdate = Date.split("/")
    Year, Month, day = listofdate
    listoftime = Time.split("/")
    Hour, Mins, Sec = listoftime
    Year = "20" + Year
    ReadingTime = datetime(int(Year), int(Month), int(day), int(Hour), int(Mins), int(Sec)).isoformat() + "Z"
    JsonData = [
        {
            "measurement": measurement,
            "tags": {
                "SensorID": SensorID,
                "GatewayID": GateWayID
            },
            "time": ReadingTime,
            "fields": {
                "Temperature": float(Temp),
                "Humidity": float(Hum),
                "Battery": float(Battery)
            }
        }
    ]
    return JsonData

def SendToInternalDataBase(dectionarylist):

    client = InfluxDBClient(DATABASE_IP, DATABASE_PORT, USERNAME_DATABASE, PASSWORD_DATABASE, INTERNAL_DATABASE_NAME)
    for i in dectionarylist:
        DataPoint = BuildJsonDataBase(i["Date"], i["Time"], i["temperature"], i["humidity"], i["SensorBattary"],
                                      i["GatewayId"], i["Sensorid"])
        client.write_points(DataPoint)
    #del dectionarylist


# def SendToHold(dectionarylist):
#
#     client = InfluxDBClient(DATABASE_IP, DATABASE_PORT, USERNAME_DATABASE, PASSWORD_DATABASE,
#                             INTERNAL_BACKUP_DATABASE_NAME)
#     for i in dectionarylist:
#         DataPoint = BuildJsonDataBase(i["Date"], i["Time"], i["temperature"], i["humidity"], i["SensorBattary"],
#                                       i["GatewayId"], i["Sensorid"])
#         client.write_points(DataPoint)
#     del dectionarylist
def SendPacketHoldingDataBase(packet) :
    from influxdb import InfluxDBClient
    client = InfluxDBClient(DATABASE_IP, DATABASE_PORT, USERNAME_DATABASE, PASSWORD_DATABASE, INTERNAL_BACKUP_DATABASE_NAME)
    try:
        index = Load_IndexNum()
    except :
        Set_IndexNumber()
        index =Load_IndexNum()

    DataPoint = [
        {
            "measurement": measurement,
            "tags" : {
                "id": index
            },
            "fields": {
                "Packet": packet
            }
        }
    ]
    index += 1
    Save_IndexNum(index)
    client.write_points(DataPoint)
def ConvertSensorsToReadings(GatwayId, date, time, GatewayBattary, GatewayPower, NumberOfSensors, Sensorhexlist):
    sensor_id_list = []
    sensor_temp_list = []
    sensor_hum_list = []
    sensor_battary_list = []
    jsonlist = []
    dectionarylist = []
    for packet in Sensorhexlist:
        sensor_id_list.append(packet[0:8])
        sensor_battary_list.append(int(packet[10:14], 16) / 1000)
        sensor_temp_list.append(TempFun(packet[14:18]))
        sensor_hum_list.append(HumFun(packet[18:20]))
    print(sensor_id_list)
    print(sensor_temp_list)
    print(sensor_hum_list)
    print(sensor_battary_list)
    for index in range(NumberOfSensors):
        jsonname = {"GatewayId": GatwayId, "GatewayBattary": GatewayBattary, "GatewayPower": GatewayPower, "Date": date,
                    "Time": time,
                    "Sensorid": sensor_id_list[index], "SensorBattary": sensor_battary_list[index],
                    "temperature": sensor_temp_list[index], "humidity": sensor_hum_list[index]
                    }
        dectionarylist.append(jsonname)
        print(json.dumps(jsonname))
        jsonlist.append(json.dumps(jsonname))
    # mqttsend(jsonlist,sensor_id_list)
    SendToInternalDataBase(dectionarylist)

    del jsonname, jsonlist, sensor_id_list, sensor_temp_list, sensor_hum_list, sensor_battary_list, GatwayId, date, time, GatewayBattary, GatewayPower, NumberOfSensors, Sensorhexlist



def ConvertPacketIntoElemets(packet):
    #threading.Thread(target=logic, args=[packet]).start()

    sensorfound = False
    NumberOfSensors = 0
    Sensorhexlist = []
    Packetindex = packet[-12:-8]
    print(Packetindex)
    Update_ACK(str(int(Packetindex, 16)))
    Packetsensorlength = packet[76:80]
    if Packetsensorlength == "0000":
        return 0
    if int(Packetsensorlength, 16) != 0:
        sensorfound = True
        NumberOfSensors = packet[82:84]
        NumberOfSensors = int(NumberOfSensors, 16)
        print("Number Of Sensors", NumberOfSensors, "Sensor")
        result = 0
        for i in range(NumberOfSensors):
            i = i + result
            Sensorhexlist.append(packet[86 + i:108 + i])
            result += 21
    GatwayId = packet[24:40]
    print(GatwayId)
    RTC = packet[40:52]
    date, time = ConvertRTCtoTime(RTC)
    GatewayBattary = packet[68:72]
    GatewayBattary = int(GatewayBattary, 16) / 100
    print("Battary of Gateway ", GatewayBattary, "Volt")
    GatewayPower = packet[72:76]
    GatewayPower = int(GatewayPower, 16) / 100
    print("Power of Gateway ", GatewayPower, "Volt")
    print(sensorfound, NumberOfSensors, Sensorhexlist)
    SendPacketHoldingDataBase(packet)
    ConvertSensorsToReadings(GatwayId, date, time, GatewayBattary, GatewayPower, NumberOfSensors, Sensorhexlist)

def check_packet(data):
    return True
    check_code = data[-8:- 4]
    # The range is from Protocol type to Packet index(include Protocol type and Packet index)
    hex_data = data[8:-8]
    our_model = PyCRC.CRC_16_MODBUS
    crc = CRC.CRC(hex_data, our_model)

    if check_code.lower() == crc.lower():
        return True
    else:
        return False

def preprocess_packet(data):
    global full_packet_list

    data = str(binascii.hexlify(data).decode())
    print(data)
    data = data.strip()
    if data.startswith("545a") and data.endswith("0d0a"):
        full_packet_list = []
        if check_packet(data):
            ConvertPacketIntoElemets(data)
        return [binascii.unhexlify(responsePacket.strip()), binascii.unhexlify(response2.strip())]
    elif data.endswith("0d0a") and not data.startswith("545a") and full_packet_list:
        collecting_packet = ''
        for packet_part in full_packet_list:
            collecting_packet += packet_part
        collecting_packet += data
        if check_packet(collecting_packet):
            ConvertPacketIntoElemets(collecting_packet)
        full_packet_list = []
        return [binascii.unhexlify(responsePacket.strip()), binascii.unhexlify(response2.strip())]
    else:
        full_packet_list.append(data)

    return 0






class EchoHandler(asyncore.dispatcher_with_send):

    def handle_read(self):
        data = self.recv(8192)
        if data:
            try:
                send_list = preprocess_packet(data)
                print(send_list)
                if send_list != 0:
                    for i in send_list:
                        self.send(i)
            except Exception as e:
                print(f"Error while processing packet: {e}")
                pass


class EchoServer(asyncore.dispatcher):

    def __init__(self, host, port):
        asyncore.dispatcher.__init__(self)
        self.create_socket()
        self.set_reuse_addr()
        self.bind((host, port))
        self.listen(5)

    def handle_accepted(self, sock, addr):
        print('Incoming connection from %s' % repr(addr))
        handler = EchoHandler(sock)


server = EchoServer('', 2000)
asyncore.loop()
