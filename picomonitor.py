
import xml.etree.ElementTree as ET

import os, asyncio

from datetime import datetime

# property name is 'MONITOR'
# element name is 'PICOALIVE'


class Monitor:

    def __init__(self, device, rconn, sender):
        "A lights object"
        self.device = device
        self.name = "MONITOR"
        self.rconn = rconn
        self.sender = sender

        self._count = 0

        # status is True if pico echoing back ok
        self.status = True



    async def update(self):
        "Call the pico every 15 seconds, and check an ech back is received"
        while True:
            # every 15 seconds, request a monitor

            await asyncio.sleep(10)          
            # request monitor echo
            self._count += 1
            if self._count == 255:
                self._count = 0
            self.rconn.publish('tx_to_pico', f'pico_monitor_{self._count}')

            # wait another 5 seconds, giving the pico time to reply
            await asyncio.sleep(5)
            # check for a reply with the same count value as that sent
            monitor_value = self.rconn.get('pico_monitor')
            if monitor_value is None:
                status = False
            elif self._count == int(monitor_value):
                status = True
            else:
                status = False
            if status == self.status:
                # no change to the current status, do not send anything
                continue
            # status has changed
            if status:
                xmldata = self.setlightvector(status, state="Alert")
            else:
                xmldata = self.setlightvector(status, state="Ok")
            self.sender.append(ET.tostring(xmldata))


    def getvector(self, root):
        """Responds to a getProperties, sets defLightVector in the sender deque.
           Returns None"""
        # check for valid request
        device = root.get("device")
        # device must be None (for all devices), or this device
        if device is None:
            # requesting all properties from all devices
            xmldata = self.deflightvector()
            # appends the xml data to be sent to the sender deque object
            self.sender.append(ET.tostring(xmldata))
            return
        elif device != self.device:
            # device specified, but not equal to this device
            return

        name = root.get("name")
        if (name is None) or (name == self.name):
            xmldata = self.deflightvector()
            # appends the xml data to be sent to the sender deque object
            self.sender.append(ET.tostring(xmldata))


    def deflightvector(self):
        """Returns a defLightVector for the roof status"""

        # note - limit timestamp characters to :21 to avoid long fractions of a second 
        timestamp = datetime.utcnow().isoformat(sep='T')[:21]

        # create the responce
        xmldata = ET.Element('defLightVector')
        xmldata.set("device", self.device)
        xmldata.set("name", self.name)
        xmldata.set("label", "REMPICO01 Status")
        xmldata.set("group", "Status")
        xmldata.set("timestamp", timestamp)

        le = ET.Element('defLight')
        le.set("name", 'PICOALIVE')
        le.set("label", 'Monitor echo from pico 01')
        if self.status:
            xmldata.set("state", "Ok")
            le.text = "Ok"
        else:
            xmldata.set("state", "Alert")
            le.text = "Alert"
        xmldata.append(le)
        return xmldata


    def newvector(self, root):
        "A lightvector does not receive a newvector, so this is a placeholder only"
        return


    def setlightvector(self, status, state=None, message=None):
        """create the setlightvector with the given status, return the xml"""

        # note - limit timestamp characters to :21 to avoid long fractions of a second 
        timestamp = datetime.utcnow().isoformat(sep='T')[:21]

        xmldata = ET.Element('setLightVector')
        xmldata.set("device", self.device)
        xmldata.set("name", self.name)
        xmldata.set("timestamp", timestamp)

        if state is not None:
            xmldata.set("state", state)

        if message is not None:
            xmldata.set("message", message)

        if status == self.status:
            return xmldata

        self.status = status

        le = ET.Element('oneLight')
        le.set("name", 'PICOALIVE')
        if self.status:
            xmldata.set("state", "Ok")
            le.text = "Ok"
        else:
            xmldata.set("state", "Alert")
            le.text = "Alert"
        xmldata.append(le)

        return xmldata

