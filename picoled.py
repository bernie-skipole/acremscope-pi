
import xml.etree.ElementTree as ET

import os, asyncio

from datetime import datetime


# property name  is 'LED'
# element names are 'LED ON' and 'LED OFF'



class LED:

    def __init__(self, device, rconn, sender):
        "Controls LED on pico"
        self.device = device
        self.name = "LED"
        self.rconn = rconn
        self.sender = sender
        # status, True for LED ON, False for OFF
        self.status = False

    async def update(self):
        """Run once to set led off on startup"""
        # wait five seconds
        await asyncio.sleep(5)
        # ensure the led is off
        self.rconn.publish('tx_to_pico', 'pico_led_Off')
        self.status = False
        # this is not on a loop, so is run once, on startup
        return


    def getvector(self, root):
        """Responds to a getProperties, sets defSwitchVector for the led.
           Returns None"""
        # check for valid request
        device = root.get("device")
        # device must be None (for all devices), or this device
        if device is None:
            # requesting all properties from all devices
            xmldata = self.defswitchvector()
            # appends the xml data to be sent to the sender deque object
            self.sender.append(ET.tostring(xmldata))
            return
        elif device != self.device:
            # device specified, but not equal to this device
            return

        name = root.get("name")
        if (name is None) or (name == self.name):
            xmldata = self.defswitchvector()
            # appends the xml data to be sent to the sender deque object
            self.sender.append(ET.tostring(xmldata))


    def defswitchvector(self):
        """Returns a defSwitchVector for the LED"""

        # note - limit timestamp characters to :21 to avoid long fractions of a second 
        timestamp = datetime.utcnow().isoformat(sep='T')[:21]

        xmldata = ET.Element('defSwitchVector')
        xmldata.set("device", self.device)
        xmldata.set("name", self.name)
        xmldata.set("label", "LED")
        xmldata.set("group", "LED")
        xmldata.set("timestamp", timestamp)
        xmldata.set("perm", "rw")
        xmldata.set("rule", "OneOfMany")
        xmldata.set("state", "Ok")

        se_on = ET.Element('defSwitch')
        se_on.set("name", "LED ON")
        if self.status:
            se_on.text = "On"
        else:
            se_on.text = "Off"
        xmldata.append(se_on)

        se_off = ET.Element('defSwitch')
        se_off.set("name", "LED OFF")
        if self.status:
            se_off.text = "Off"
        else:
            se_off.text = "On"
        xmldata.append(se_off)
        return xmldata


    def newvector(self, root):
        "On receiving a newswitchvector, turn on or off the led"
        if root.tag != "newSwitchVector":
            return
        if root.get("device") != self.device:
            # not this device
            return
        if root.get("name") != self.name:
            # not this SwitchVector
            return

        status = self.status

        switchlist = root.findall("oneSwitch")
        for setting in switchlist:
            # oneSwitch element name
            en = setting.get("name")
            # get switch On or Off, remove newlines
            content = setting.text.strip()
            if (en == "LED ON") and (content == "On"):
                status = True
            if (en == "LED ON") and (content == "Off"):
                status = False
            if (en == "LED OFF") and (content == "On"):
                status = False
            if (en == "LED OFF") and (content == "Off"):
                status = True

        # activate the hardware
        if status:
            self.rconn.publish('tx_to_pico', 'pico_led_On')
        else:
            self.rconn.publish('tx_to_pico', 'pico_led_Off')

        xmldata = self.setswitchvector(status, state="Ok", message=None)
        self.sender.append(ET.tostring(xmldata))
        return



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

        se_on = ET.Element('oneSwitch')
        se_on.set("name", "LED ON")
        if self.status:
            se_on.text = "On"
        else:
            se_on.text = "Off"
        xmldata.append(se_on)

        se_off = ET.Element('oneSwitch')
        se_off.set("name", "LED OFF")
        if self.status:
            se_off.text = "Off"
        else:
            se_off.text = "On"
        xmldata.append(se_off)

        return xmldata




