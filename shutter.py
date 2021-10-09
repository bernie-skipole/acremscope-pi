
import xml.etree.ElementTree as ET

import os

from datetime import datetime

from time import sleep, monotonic


# Consists of a switch vector,
# property name = DOME_SHUTTER
# elements = SHUTTER_OPEN, SHUTTER_CLOSE


class Roof:

    def __init__(self, device, leftdoor, rightdoor, lights, rconn, sender):
        "A switch object"
        self.device = device
        self.name = "DOME_SHUTTER"
        self.rconn = rconn
        self.sender = sender
        self.leftdoor = leftdoor
        self.rightdoor = rightdoor
        self.lights = lights
        self.status = lights.status


    def update(self):
        """Called by update, to check current door status and return a setSwitchVector if a change has occurred"""
        status = self.lights.status
        if status == self.status:
            # no change to status
            return
        xmldata = self.setswitchvector(status, state="Ok", message=None)
        # appends the xml data to be sent to the sender deque object
        self.sender.append(ET.tostring(xmldata))
 

    def respond(self):
        """Responds to a getProperties, sets defSwitchVector for the roof in the sender deque.
           Returns None"""
        xmldata = self.defswitchvector()
        # appends the xml data to be sent to the sender deque object
        self.sender.append(ET.tostring(xmldata))


    def newvector(self, root):
        "On receiving a newswitchvector, start opening or closing the door"
        if root.tag != "newSwitchVector":
            return
        if root.get("device") != self.device:
            # not this device
            return
        if root.get("name") != self.name:
            # not this SwitchVector
            return

        switchlist = root.findall("oneSwitch")
        for setting in switchlist:
            # property name
            pn = setting.get("name")
            # get switch On or Off, remove newlines
            content = setting.text.strip()
            if (pn == 'SHUTTER_OPEN') and (content == "On"):
                if self.lights.status == "CLOSED":
                    self.leftdoor.startdoor(True)
                    self.rightdoor.startdoor(True)
            elif (pn == 'SHUTTER_OPEN') and (content == "Off"):
                if self.lights.status == "OPEN":
                    self.leftdoor.startdoor(False)
                    self.rightdoor.startdoor(False)
            elif (pn == 'SHUTTER_CLOSE') and (content == "On"):
                if self.lights.status == "OPEN":
                    self.leftdoor.startdoor(False)
                    self.rightdoor.startdoor(False)
            elif (pn == 'SHUTTER_CLOSE') and (content == "Off"):
                if self.lights.status == "CLOSED":
                    self.leftdoor.startdoor(True)
                    self.rightdoor.startdoor(True)


    def defswitchvector(self):
        """Returns a defSwitchVector for the roof control"""

        # note - limit timestamp characters to :21 to avoid long fractions of a second 
        timestamp = datetime.utcnow().isoformat(sep='T')[:21]

        xmldata = ET.Element('defSwitchVector')
        xmldata.set("device", self.device)
        xmldata.set("name", self.name)
        xmldata.set("label", "Control")
        xmldata.set("group", "Control")
        xmldata.set("timestamp", timestamp)
        xmldata.set("perm", "rw")
        xmldata.set("rule", "OneOfMany")
        xmldata.set("state", "Ok")

        se_open = ET.Element('defSwitch')
        se_open.set("name", 'SHUTTER_OPEN')
        if self.lights.status == "OPEN":
            se_open.text = "On"
        elif self.lights.status == "OPENING":
            se_open.text = "On"
            xmldata.set("state", "Busy")
        else:
            se_open.text = "Off"
        xmldata.append(se_open)

        se_close = ET.Element('defSwitch')
        se_close.set("name", 'SHUTTER_CLOSE')
        if self.lights.status == "CLOSED":
            se_close.text = "On"
        elif self.lights.status == "CLOSING":
            se_close.text = "On"
            xmldata.set("state", "Busy")
        else:
            se_close.text = "Off"
        xmldata.append(se_close)

        return xmldata


    def setswitchvector(self, status, state=None, message=None):
        """create the setswitchvector with the given status, return the xml"""

        # note - limit timestamp characters to :21 to avoid long fractions of a second 
        timestamp = datetime.utcnow().isoformat(sep='T')[:21]

        xmldata = ET.Element('setSwitchVector')
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

        # with its two switch states

        se_open = ET.Element('oneSwitch')
        se_open.set("name", 'SHUTTER_OPEN')
        if status == "OPEN":
            se_open.text = "On"
        elif status == "OPENING":
            se_open.text = "On"
            xmldata.set("state", "Busy")
        else:
            se_open.text = "Off"
        xmldata.append(se_open)

        se_close = ET.Element('oneSwitch')
        se_close.set("name", 'SHUTTER_CLOSE')
        if status == "CLOSED":
            se_close.text = "On"
        elif status == "CLOSING":
            se_close.text = "On"
            xmldata.set("state", "Busy")
        else:
            se_close.text = "Off"
        xmldata.append(se_close)

        return xmldata


