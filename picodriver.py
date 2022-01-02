#!/home/bernard/acenv/bin/python3


"""picodriver.py

Gets and sets LED on the pico
Sends/receives monitoring count to the pico
Receives temperature values from the pico

device is 'Rempico01'

property name  is 'LED'
element names are 'LED ON' and 'LED OFF'

property name is 'MONITOR'
element name is 'PICOALIVE'

property name is 'ATMOSPHERE'
element name is 'TEMPERATURE'
"""

import os, sys, collections, asyncio, time

import xml.etree.ElementTree as ET

from datetime import datetime

import redis


# All xml data received on the port from the client should be contained in one of the following tags
TAGS = (b'getProperties',
        b'newTextVector',
        b'newNumberVector',
        b'newSwitchVector',
        b'newBLOBVector'
       )

# _STARTTAGS is a tuple of ( b'<newTextVector', ...  ) data received will be tested to start with such a starttag
_STARTTAGS = tuple(b'<' + tag for tag in TAGS)

# _ENDTAGS is a tuple of ( b'</newTextVector>', ...  ) data received will be tested to end with such an endtag
_ENDTAGS = tuple(b'</' + tag + b'>' for tag in TAGS)


def driver():
    "Blocking call"

    # create a redis connection
    rconn = redis.StrictRedis(host='localhost', port=6379, db=0)

    # create a deque, data to be sent to indiserver is appended to this
    sender = collections.deque(maxlen=100)

    # create classes which handle the hardware

    device = 'Rempico01'

    led = LED(device, rconn, sender)
    temperature = Temperature(device, rconn, sender)
    monitor = Monitor(device, rconn, sender)


    # now start eventloop to read and write to stdin, stdout
    loop = asyncio.get_event_loop()

    connections = _Driver(loop, sender, led, temperature, monitor)

    while True:
        try:
            loop.run_until_complete(connections.handle_data())
        finally:
            loop.close()


class _Driver:

    def __init__(self, loop, sender, *items):
        "Sets the data used by the data handler"
        self.loop = loop
        self.sender = sender
        self.items = items

    async def handle_data(self):
        """handle data via stdin and stdout"""
        reader = asyncio.StreamReader(loop=self.loop)
        reader_protocol = asyncio.StreamReaderProtocol(reader, loop=self.loop)
        await self.loop.connect_read_pipe( lambda: reader_protocol, sys.stdin)

        writer_transport, writer_protocol = await self.loop.connect_write_pipe(
                                                       lambda: asyncio.Protocol(),
                                                       sys.stdout)
        writer = asyncio.StreamWriter(writer_transport, writer_protocol, None, self.loop)

        # list of item update methods, each of which should be an awaitable
        itemlist = list(item.update() for item in self.items)
        await asyncio.gather(self.reader(reader), self.writer(writer), *itemlist)


    async def writer(self, writer):
        """Writes data in sender to stdout writer"""
        while True:
            if self.sender:
                # add a new line to help if the software receiving this is line bufferred
                writer.write(self.sender.popleft() + b"\n")
            else:
                # no message to send, do an async pause
                await asyncio.sleep(0.2)


    async def reader(self, reader):
        """Reads data from stdin reader which is the input stream of the driver"""
        # get received data, and put it into message
        message = b''
        messagetagnumber = None
        while True:
            # get blocks of data
            try:
                data = await reader.readuntil(separator=b'>')
            except asyncio.LimitOverrunError:
                data = await reader.read(n=32000)
            if not message:
                # data is expected to start with <tag, first strip any newlines
                data = data.strip()
                for index, st in enumerate(_STARTTAGS):
                    if data.startswith(st):
                        messagetagnumber = index
                        break
                else:
                    # data does not start with a recognised tag, so ignore it
                    # and continue waiting for a valid message start
                    continue
                # set this data into the received message
                message = data
                # either further children of this tag are coming, or maybe its a single tag ending in "/>"
                if message.endswith(b'/>'):
                    # the message is complete, handle message here
                    try:
                        root = ET.fromstring(message.decode("utf-8"))
                    except Exception:
                        # possible malformed
                        message = b''
                        messagetagnumber = None
                        continue
                    # respond to the received xml ############
                    self.respond(root)
                    # and start again, waiting for a new message
                    message = b''
                    messagetagnumber = None
                # and read either the next message, or the children of this tag
                continue
            # To reach this point, the message is in progress, with a messagetagnumber set
            # keep adding the received data to message, until an endtag is reached
            message += data
            if message.endswith(_ENDTAGS[messagetagnumber]):
                # the message is complete, handle message here
                try:
                    root = ET.fromstring(message.decode("utf-8"))
                except Exception:
                    # possible malformed
                    message = b''
                    messagetagnumber = None
                    continue
                # respond to the received xml ############
                self.respond(root)
                # and start again, waiting for a new message
                message = b''
                messagetagnumber = None

    def respond(self, root):
        "Respond to received xml, as set in root"
        if root.tag == "getProperties":
            version = root.get("version")
            if version != "1.7":
                return
            for item in self.items:
                item.getvector(root)
        else:
            # root.tag will be either newSwitchVector, newNumberVector,.. etc, one of the tags in TAGS
            for item in self.items:
                item.newvector(root)



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
                xmldata = self.setlightvector(status, message="Communicating with pico OK")
                # send a site message
                xmlmessage = ET.Element('message')
                xmlmessage.set("message", "Pico operational")
                self.sender.append(ET.tostring(xmlmessage))
            else:
                xmldata = self.setlightvector(status, message="Communicating with pico has failed")
                # send a site message
                xmlmessage = ET.Element('message')
                xmlmessage.set("message", "Cannot access pico, operations via the pico board may not be valid")
                self.sender.append(ET.tostring(xmlmessage))
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



if __name__=="__main__":

    # start this blocking call
    driver()

