
import xml.etree.ElementTree as ET

import os

from datetime import datetime

from time import sleep, monotonic


class StatusLights:

    def __init__(self, device, leftdoor, rightdoor, rconn, sender):
        "A lights object"
        self.device = device
        self.name = "DOOR_STATE"
        self.rconn = rconn
        self.sender = sender
        self.leftdoor = leftdoor
        self.rightdoor = rightdoor
        self.status = "UNKNOWN"


    def update(self):
        """Called by update, to check current door status and return a setLightVector if a change has occurred"""

        status = 'UNKNOWN'
        if self.leftdoor.direction and self.rightdoor.direction:   # open or opening
            if self.leftdoor.moving or self.rightdoor.moving:
                status = "OPENING"
            else:
                status = "OPEN"
        if (not self.leftdoor.direction) and (not self.rightdoor.direction):   # closed or closing
            if self.leftdoor.moving or self.rightdoor.moving:
                status = "CLOSING"
            else:
                status = "CLOSED"

        if status == self.status:
            return

        if status == "UNKNOWN":
            xmldata = self.setlightvector(status, state="Alert", message="Roof status unknown")
        else:
            xmldata = self.setlightvector(status, state="Ok", message="Roof status")

        # appends the xml data to be sent to the sender deque object
        self.sender.append(ET.tostring(xmldata))


    def getvector(self, root):
        """Responds to a getProperties, sets defLightVector for the door in the sender deque.
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
        xmldata.set("label", "Roll Off door status")
        xmldata.set("group", "Control")
        xmldata.set("state", "Ok")
        xmldata.set("timestamp", timestamp)
        xmldata.set("message", "Roof status")

        # five lights
        # OPEN
        # OPENING
        # CLOSING
        # CLOSED
        # UNKNOWN
        # Idle|Ok|Busy|Alert
        e1 = ET.Element('defLight')
        e1.set("name", "OPEN")
        e1.text = "Idle"
        e2 = ET.Element('defLight')
        e2.set("name", "OPENING")
        e2.text = "Idle"
        e3 = ET.Element('defLight')
        e3.set("name", "CLOSING")
        e3.text = "Idle"
        e4 = ET.Element('defLight')
        e4.set("name", "CLOSED")
        e4.text = "Idle"
        e5 = ET.Element('defLight')
        e5.set("name", "UNKNOWN")
        e5.text = "Idle"

        if self.status == "OPEN":
            e1.text = "Ok"
            xmldata.set("message", "Roof status : open")
        elif self.status == "OPENING":
            e2.text = "Ok"
            xmldata.set("message", "Roof status : opening")
        elif self.status == "CLOSING":
            e3.text = "Ok"
            xmldata.set("message", "Roof status : closing")
        elif self.status == "CLOSED":
            e4.text = "Ok"
            xmldata.set("message", "Roof status : closed")
        elif self.status == "UNKNOWN":
            e5.text = "Alert"
            xmldata.set("message", "Roof status : unknown")
        xmldata.append(e1)
        xmldata.append(e2)
        xmldata.append(e3)
        xmldata.append(e4)
        xmldata.append(e5)
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

        # four lights
        # OPEN
        # OPENING
        # CLOSING
        # CLOSED
        # UNKNOWN
        # Idle|Ok|Busy|Alert
        e1 = ET.Element('oneLight')
        e1.set("name", "OPEN")
        e1.text = "Idle"
        e2 = ET.Element('oneLight')
        e2.set("name", "OPENING")
        e2.text = "Idle"
        e3 = ET.Element('oneLight')
        e3.set("name", "CLOSING")
        e3.text = "Idle"
        e4 = ET.Element('oneLight')
        e4.set("name", "CLOSED")
        e4.text = "Idle"
        e5 = ET.Element('oneLight')
        e5.set("name", "UNKNOWN")
        e5.text = "Idle"
        if self.status == "OPEN":
            e1.text = "Ok"
            xmldata.set("message", "Roof status : open")
        elif self.status == "OPENING":
            e2.text = "Ok"
            xmldata.set("message", "Roof status : opening")
        elif self.status == "CLOSING":
            e3.text = "Ok"
            xmldata.set("message", "Roof status : closing")
        elif self.status == "CLOSED":
            e4.text = "Ok"
            xmldata.set("message", "Roof status : closed")
        elif self.status == "UNKNOWN":
            e5.text = "Alert"
            xmldata.set("message", "Roof status : unknown")
        xmldata.append(e1)
        xmldata.append(e2)
        xmldata.append(e3)
        xmldata.append(e4)
        xmldata.append(e5)

        return xmldata



