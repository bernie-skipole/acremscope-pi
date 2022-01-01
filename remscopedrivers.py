#!/home/bernard/acenv/bin/python3

# sends indi data via mqtt

from indi_mr import driverstomqtt, mqtt_server

# define the host/port where the MQTT server is listenning, this function returns a named tuple.
mqtt_host = mqtt_server(host='localhost', port=1883)

# blocking call which runs the service, communicating between drivers and mqtt
driverstomqtt([ "/home/bernard/acremscope-pi/picodriver.py",
                "/home/bernard/acremscope-pi/networkmonitor.py",
                "/home/bernard/acremscope-pi/doordriver.py",
                "indi_simulator_telescope",
                "indi_simulator_ccd"], 'pi_01', mqtt_host)

