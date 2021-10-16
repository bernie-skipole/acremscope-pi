
import xml.etree.ElementTree as ET

import os, asyncio

from datetime import datetime

from time import sleep, monotonic


# property name is 'ATMOSPHERE'
# element name is 'TEMPERATURE'



class Temperature:

    conversion_factor = 3.3 / (65535)

    def __init__(self, device, rconn, sender):
        "Reports temperature from pico"
        self.device = device
        self.name = "ATMOSPHERE"
        self.rconn = rconn
        self.sender = sender
        # start with zero centigrade, which should be immediately overwritten
        self.temperature = "273.15"
        self.timestamp = datetime.utcnow().isoformat(sep='T')[:21]



    async def update(self):
        "Request an update from the pico every hour, and report temperature via a setnumbervector"
        # wait an initial ten seconds to allow other processes to start
        await asyncio.sleep(10)
        while True:
            # request to get pico to return the temperature
            self.rconn.publish('tx_to_pico', 'pico_temperature')
            # wait ten seconds, hopefully the pico will have returned a temperature
            await asyncio.sleep(10)
            # then get the temperature and timestamp
            temperature, timestamp = self.get_temperature()
            if (temperature == self.temperature) and (timestamp == self.timestamp):
                # no change, no new reading has occurred
                continue
            # new temperature reading has ben obtained, send it in a setnumbervector
            xmldata = self.setnumbervector(temperature, timestamp)
            self.sender.append(ET.tostring(xmldata))
            # as this is to be done hourly, now wait an hour less ten seconds
            await asyncio.sleep(3590)


    def get_temperature(self):
        "Returns the temperature, timestamp. If not found, returns current self.temperature, self.timestamp"
        reading = self.rconn.get('pico_temperature')
        if reading is None:
            return self.temperature, self.timestamp
        # having read a temperature, delete it. so it is only valid for this timestamp
        self.rconn.delete('pico_temperature')
        # The temperature sensor measures the Vbe voltage of a biased bipolar diode, connected to the fifth ADC channel
        # Typically, Vbe = 0.706V at 27 degrees C, with a slope of -1.721mV (0.001721) per degree. 
        temperature = 27 - (int(reading)*self.conversion_factor - 0.706)/0.001721 + 273.15 # 273.15 added to convert to Kelvin
        return str(temperature), datetime.utcnow().isoformat(sep='T')[:21]


    def getvector(self, root):
        """Responds to a getProperties, sets defnumbervector for the temperature.
           Returns None"""
        # check for valid request
        device = root.get("device")
        # device must be None (for all devices), or this device
        if device is None:
            # requesting all properties from all devices
            xmldata = self.defnumbervector()
            # appends the xml data to be sent to the sender deque object
            self.sender.append(ET.tostring(xmldata))
            return
        elif device != self.device:
            # device specified, but not equal to this device
            return

        name = root.get("name")
        if (name is None) or (name == self.name):
            xmldata = self.defnumbervector()
            # appends the xml data to be sent to the sender deque object
            self.sender.append(ET.tostring(xmldata))


    def defnumbervector(self):
        """Returns a defnumbervector for the temperature"""

        temperature, timestamp = self.get_temperature()
        self.timestamp = timestamp
        self.temperature = temperature

        # create the responce
        xmldata = ET.Element('defNumberVector')
        xmldata.set("device", self.device)
        xmldata.set("name", self.name)
        xmldata.set("label", "Temperature (Kelvin)")
        xmldata.set("group", "Temperature")
        xmldata.set("state", "Ok")
        xmldata.set("perm", "ro")
        xmldata.set("timestamp", self.timestamp)

        ne = ET.Element('defNumber')
        ne.set("name", 'TEMPERATURE')
        ne.set("format", "%.2f")
        ne.set("min", "0")
        ne.set("max", "0")   # min== max means ignore
        ne.set("step", "0")    # 0 means ignore
        ne.text = self.temperature
        xmldata.append(ne)
        return xmldata


    def newvector(self, root):
        "Temperature is read only, so does not accept a newNumberVector"
        return


    def setnumbervector(self, temperature, timestamp, state=None, message=None):
        "create the setnumbervector with the given temperature, return the xml"

        self.temperature = temperature
        self.timestamp = timestamp

        # create the setNumberVector
        xmldata = ET.Element('setNumberVector')
        xmldata.set("device", self.device)
        xmldata.set("name", self.name)
        xmldata.set("timestamp", self.timestamp)

        if state is not None:
            xmldata.set("state", state)

        if message is not None:
            xmldata.set("message", message)

        ne = ET.Element('oneNumber')
        ne.set("name", 'TEMPERATURE')
        ne.text = self.temperature
        xmldata.append(ne)

        return xmldata




