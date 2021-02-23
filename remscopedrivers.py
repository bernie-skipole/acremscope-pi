#!/home/bernard/acenv/bin/python3

# sends indi data via mqtt

from indi_mr import driverstomqtt, mqtt_server

# define the host/port where the MQTT server is listenning, this function returns a named tuple.
mqtt_host = mqtt_server(host='localhost', port=1883)

# blocking call which runs the service, communicating between drivers and mqtt
driverstomqtt([ "/home/bernard/indi/leddriver.py",
                "/home/bernard/indi/networkmonitor.py",
                "/home/bernard/indi/temperaturedriver.py",
                "/home/bernard/indi/doordriver.py"], 'pi_01', mqtt_host)

