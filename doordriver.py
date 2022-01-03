#!/home/bernard/acenv/bin/python3


"""doordriver.py

Opens and closes, and reports status of the door

device = 'Roll off door'

Consists of a switch vector,

property name = DOME_SHUTTER
elements = SHUTTER_OPEN, SHUTTER_CLOSE

and a light vector
property name = DOOR_STATE
with elements:
OPEN
OPENING
CLOSING
CLOSED
UNKNOWN

and two number vectors LEFT_DOOR RIGHT_DOOR,
each with elements:
ACC_DURATION
FAST_DURATION
DEC_DURATION
SLOW_DURATION
MAXIMUM
MINIMUM

which set the door movement parameters. The Python object which
generates these number vectors uses the parameters to open and
close the doors, on being instructed to do so by the DOME_SHUTTER
switch vector
"""

import os, sys, collections, asyncio, time

import xml.etree.ElementTree as ET

from datetime import datetime

from time import monotonic

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
    rconn = redis.Redis(host='localhost', port=6379, db=0)

    # create a deque, data to be sent to indiserver is appended to this
    sender = collections.deque(maxlen=100)

    # create classes which handle the hardware

    device = 'Roll off door'

    leftdoor = Door(device, 0, rconn, sender)
    # If new door motion values have been set in a file, read them
    leftdoor.read_parameters()

    rightdoor = Door(device, 1, rconn, sender)
    # If new door motion values have been set in a file, read them
    rightdoor.read_parameters()

    lights = StatusLights(device, leftdoor, rightdoor, rconn, sender)
    roof = Roof(device, leftdoor, rightdoor, lights, rconn, sender)

    # intiate a slow close, in case the pi resumes power with the door half open
    leftdoor.startdoor(direction=False, slow=True)
    rightdoor.startdoor(direction=False, slow=True)
    # direction = False sets the direction to close

    connections = _Driver(sender, leftdoor, rightdoor, lights, roof)
    asyncio.run(connections.handle_data())


class _Driver:

    def __init__(self, sender, *items):
        "Sets the data used by the data handler"
        self.sender = sender
        self.items = items

    async def handle_data(self):
        """handle data via stdin and stdout"""
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(loop=loop)
        reader_protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
        await loop.connect_read_pipe( lambda: reader_protocol, sys.stdin)

        writer_transport, writer_protocol = await loop.connect_write_pipe(
                                                       lambda: asyncio.Protocol(),
                                                       sys.stdout)
        writer = asyncio.StreamWriter(writer_transport, writer_protocol, None, loop)

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

######### ROOF OBJECT ###########
#
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
        self.status = "UNKNOWN"


    async def update(self):
        """Check current door status and return a setSwitchVector if a change has occurred"""
        while True:
            await asyncio.sleep(0.2)
            status = self.lights.status
            if status == self.status:
                # no change to status, nothing to report
                continue
            xmldata = self.setswitchvector(status, state="Ok", message=None)
            # appends the xml data to be sent to the sender deque object
            self.sender.append(ET.tostring(xmldata))
 

    def getvector(self, root):
        """Responds to a getProperties, sets defSwitchVector for the roof in the sender deque.
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
                elif self.lights.status == "OPEN":
                    # already open, send ok state back
                    self.sendsetswitchvectorok()
            elif (pn == 'SHUTTER_OPEN') and (content == "Off"):
                if self.lights.status == "OPEN":
                    self.leftdoor.startdoor(False)
                    self.rightdoor.startdoor(False)
                elif self.lights.status == "CLOSED":
                    # already closed, send ok state back
                    self.sendsetswitchvectorok()
            elif (pn == 'SHUTTER_CLOSE') and (content == "On"):
                if self.lights.status == "OPEN":
                    self.leftdoor.startdoor(False)
                    self.rightdoor.startdoor(False)
                elif self.lights.status == "CLOSED":
                    # already closed, send ok state back
                    self.sendsetswitchvectorok()
            elif (pn == 'SHUTTER_CLOSE') and (content == "Off"):
                if self.lights.status == "CLOSED":
                    self.leftdoor.startdoor(True)
                    self.rightdoor.startdoor(True)
                elif self.lights.status == "OPEN":
                    # already open, send ok state back
                    self.sendsetswitchvectorok()


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


    def sendsetswitchvectorok(self):
        "Sends a setswitchvector with a state of ok"
        # note - limit timestamp characters to :21 to avoid long fractions of a second 
        timestamp = datetime.utcnow().isoformat(sep='T')[:21]
        xmldata = ET.Element('setSwitchVector')
        xmldata.set("device", self.device)
        xmldata.set("name", self.name)
        xmldata.set("timestamp", timestamp)
        xmldata.set("state", "Ok")
        # appends the xml data to be sent to the sender deque object
        self.sender.append(ET.tostring(xmldata))


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



############# STATUSLIGHTS OBJECT #######
#
# a light vector
# property name = DOOR_STATE
# with elements:
# OPEN
# OPENING
# CLOSING
# CLOSED
# UNKNOWN


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


    async def update(self):
        """Check current door status and return a setLightVector if a change has occurred"""
        while True:
            await asyncio.sleep(0.2)
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
                # no change, so nothing new to report
                continue

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


######### Door objects
#
# these are numbervectors with elements
# ACC_DURATION
# FAST_DURATION
# DEC_DURATION
# SLOW_DURATION
# MAXIMUM
# MINIMUM
#
# and also have startdoor methods which open and control the door motion



class Door:

    def __init__(self, device, door, rconn, sender):
        "A door object, with door number such as 0 or 1"
        if door:
            self.name = "RIGHT_DOOR"
        else:
            self.name = "LEFT_DOOR"
        self.device = device
        if door:
            self.group = "Right door"
        else:
            self.group = "Left door"
        self.door = door
        self.rconn = rconn
        self.sender = sender

        self.direction = True  # True for open, False for close

        # this is the current pwm ratio being sent to the pico, a value between 0 and 95
        self.pwm_ratio = 0
        
        # this is set to True when the door is moving
        self.moving = False
        self.start_running = 0
        
        # Set these parameters to default
        self._acc_duration = 2
        self._fast_duration = 4
        self._dec_duration = 2
        self._slow_duration = 2
        self._maximum = 95
        self._minimum = 5

        # these will be stored in a file with this property name, and in the same directory
        # as this python file
        self.filename = os.path.join(os.path.dirname(os.path.realpath(__file__)), self.name)

        # If slow is True, this temporarily sets maximum speed to self._minimum+1
        self.slow = False


    @property
    def elements(self):
        return [ str(self._acc_duration),
                 str(self._fast_duration),
                 str(self._dec_duration),
                 str(self._slow_duration),
                 str(self._maximum),
                 str(self._minimum) ]


    def read_parameters(self):
        """Reads the parameters from a file"""
        if not os.path.isfile(self.filename):
            return
        f = open(self.filename, "r")
        parameter_strings = f.readlines()
        f.close()
        if len(parameter_strings) != 6:
            return
        parameter_list = [ int(p.strip()) for p in parameter_strings]
        self._acc_duration = parameter_list[0]
        self._fast_duration = parameter_list[1]
        self._dec_duration = parameter_list[2]
        self._slow_duration = parameter_list[3]
        self._maximum = parameter_list[4]
        self._minimum = parameter_list[5]

    def write_parameters(self):
        """Saves the parameters to a file"""
        f = open(self.filename, "w")
        parameter_list = self.elements
        for p in parameter_list:
            f.write(p+"\n")
        f.close()


    def startdoor(self, direction, slow=False):
        # can only start if the door is not moving
        if self.moving:
            return
        self.start_running = monotonic()
        self.moving = True
        self.slow = slow
        self.direction = direction
        if direction:
            self.rconn.publish('tx_to_pico', f'pico_door{self.door}_direction_1')
        else:
            self.rconn.publish('tx_to_pico', f'pico_door{self.door}_direction_0')


    async def update(self):
        """Sends pwm values to the pico, check pwm every .2 seconds"""
        while True:
            await asyncio.sleep(0.2)
            if not self.moving:
                # the door is not moving, nothing to do
                # and set self.slow to False, so it has to be set to True again to initiate a slow down
                self.slow = False
                continue
            # door is moving, get pwm and send to pico
            running_time = monotonic() - self.start_running
     
            pwm = 0
            
            # If the running time is greater than max allowed, stop the motor
            max_running_time = self._acc_duration + self._fast_duration + self._dec_duration + self._slow_duration
            if running_time >= max_running_time:
                self.pwm_ratio = 0
                self.moving = False
                self.rconn.publish('tx_to_pico', f'pico_door{self.door}_pwm_0')
                continue

            # door is opening or closing, get the pwm ratio
            if self.slow:
                pwm = curve(running_time, self._acc_duration,
                                          self._fast_duration,
                                          self._dec_duration,
                                          self._slow_duration,
                                          self._minimum + 1,
                                          self._minimum)
            else:
                pwm = curve(running_time, self._acc_duration,
                                          self._fast_duration,
                                          self._dec_duration,
                                          self._slow_duration,
                                          self._maximum,
                                          self._minimum)

            # pwm is a number between 0 and self._maximum, #########################
            pwm = int(pwm)
            # has this changed from previous?
            if pwm != self.pwm_ratio:
                # yes it has changed, so send this to the pico, and store new value in self.pwm_ratio
                self.pwm_ratio = pwm
                self.rconn.publish('tx_to_pico', f'pico_door{self.door}_pwm_{pwm}')


    def getvector(self, root):
        """Responds to a getProperties, sets defNumberVector for the door in the sender deque.
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
        """Returns a defNumberVector for the door"""

        # note - limit timestamp characters to :21 to avoid long fractions of a second 
        timestamp = datetime.utcnow().isoformat(sep='T')[:21]

        # create the responce
        xmldata = ET.Element('defNumberVector')
        xmldata.set("device", self.device)
        xmldata.set("name", self.name)
        xmldata.set("label", "Door motion parameters")
        xmldata.set("group", self.group)
        xmldata.set("state", "Ok")
        xmldata.set("perm", "rw")
        xmldata.set("timestamp", timestamp)


        #ACC_DURATION
        ad = ET.Element('defNumber')
        ad.set("name", 'ACC_DURATION')
        ad.set("label", 'Duration of acceleration (seconds)')
        ad.set("format", "%2d")
        ad.set("min", "1")
        ad.set("max", "20")   # min== max means ignore
        ad.set("step", "1")    # 0 means ignore
        ad.text = str(self._acc_duration)
        xmldata.append(ad)

        #FAST_DURATION
        fd = ET.Element('defNumber')
        fd.set("name", 'FAST_DURATION')
        fd.set("label", 'Duration of maximum speed (seconds)')
        fd.set("format", "%2d")
        fd.set("min", "1")
        fd.set("max", "30")   # min== max means ignore
        fd.set("step", "1")    # 0 means ignore
        fd.text = str(self._fast_duration)
        xmldata.append(fd)

        #DEC_DURATION
        dd = ET.Element('defNumber')
        dd.set("name", 'DEC_DURATION')
        dd.set("label", 'Duration of deceleration (seconds)')
        dd.set("format", "%2d")
        dd.set("min", "1")
        dd.set("max", "20")   # min== max means ignore
        dd.set("step", "1")    # 0 means ignore
        dd.text = str(self._dec_duration)
        xmldata.append(dd)

        #SLOW_DURATION
        sd = ET.Element('defNumber')
        sd.set("name", 'SLOW_DURATION')
        sd.set("label", 'Duration of low speed after deceleration (seconds)')
        sd.set("format", "%2d")
        sd.set("min", "1")
        sd.set("max", "30")   # min== max means ignore
        sd.set("step", "1")    # 0 means ignore
        sd.text = str(self._slow_duration)
        xmldata.append(sd)

        #MAXIMUM
        mx = ET.Element('defNumber')
        mx.set("name", 'MAXIMUM')
        mx.set("label", 'High speed pwm ratio (percentage)')
        mx.set("format", "%2d")
        mx.set("min", "2")
        mx.set("max", "95")   # min== max means ignore
        mx.set("step", "1")    # 0 means ignore
        mx.text = str(self._maximum)
        xmldata.append(mx)

        #MINIMUM
        mn = ET.Element('defNumber')
        mn.set("name", 'MINIMUM')
        mn.set("label", 'Low speed pwm ratio (percentage)')
        mn.set("format", "%2d")
        mn.set("min", "1")
        mn.set("max", "50")   # min== max means ignore
        mn.set("step", "1")    # 0 means ignore
        mn.text = str(self._minimum)
        xmldata.append(mn)

        return xmldata


    def setnumbervector(self, elements, state=None, message=None):
        "Sets this objects elements and create the setnumbervector with the given elements, return the xml"

        # note - limit timestamp characters to :21 to avoid long fractions of a second 
        timestamp = datetime.utcnow().isoformat(sep='T')[:21]

        # create the setNumberVector
        xmldata = ET.Element('setNumberVector')
        xmldata.set("device", self.device)
        xmldata.set("name", self.name)
        xmldata.set("timestamp", timestamp)

        if state is not None:
            xmldata.set("state", state)

        if message is not None:
            xmldata.set("message", message)

        int_elements = [ int(e.strip()) for e in elements ]

        if int_elements[0] != self._acc_duration:
            self._acc_duration = int_elements[0]
            ad = ET.Element('oneNumber')
            ad.set("name", 'ACC_DURATION')
            ad.text = str(self._acc_duration)
            xmldata.append(ad)

        if int_elements[1] != self._fast_duration:
            self._fast_duration = int_elements[1]
            fd = ET.Element('oneNumber')
            fd.set("name", 'FAST_DURATION')
            fd.text = str(self._fast_duration)
            xmldata.append(fd)

        if int_elements[2] != self._dec_duration:
            self._dec_duration = int_elements[2]
            dd = ET.Element('oneNumber')
            dd.set("name", 'DEC_DURATION')
            dd.text = str(self._dec_duration)
            xmldata.append(dd)

        if int_elements[3] != self._slow_duration:
            self._slow_duration = int_elements[3]
            sd = ET.Element('oneNumber')
            sd.set("name", 'SLOW_DURATION')
            sd.text = str(self._slow_duration)
            xmldata.append(sd)

        if int_elements[4] != self._maximum:
            self._maximum = int_elements[4]
            mx = ET.Element('oneNumber')
            mx.set("name", 'MAXIMUM')
            mx.text = str(self._maximum)
            xmldata.append(mx)

        if int_elements[5] != self._minimum:
            self._minimum = int_elements[5]
            mn = ET.Element('oneNumber')
            mn.set("name", 'MINIMUM')
            mn.text = str(self._minimum)
            xmldata.append(mn)

        return xmldata


    def newvector(self, root):
        "Having received a newNumberVector, parse and save data, and send setnumbervector back"
        if root.tag != "newNumberVector":
            return
        if root.get("device") != self.device:
            # not this device
            return
        if root.get("name") != self.name:
            # not this NumberVector
            return

        # get newelements receive, starting with a copy of current elements, and then updating
        # it with any changes
        newelements = self.elements.copy()

        elementlist = root.findall("oneNumber")
        # get elements received
        for item in elementlist:
            # oneNumber element name
            numname = item.get("name")
            # get the number, remove newlines
            if numname == 'ACC_DURATION':
                newelements[0] = item.text.strip()
            elif numname == 'FAST_DURATION':
                newelements[1] = item.text.strip()
            elif numname == 'DEC_DURATION':
                newelements[2] = item.text.strip()
            elif numname == 'SLOW_DURATION':
                newelements[3] = item.text.strip()
            elif numname == 'MAXIMUM':
                newelements[4] = item.text.strip()
            elif numname == 'MINIMUM':
                newelements[5] = item.text.strip()

        # check newelements received

        # If no change to elements, send an ok response
        if newelements == self.elements:
            xmldata = self.setnumbervector(self.elements, state="Ok", message='Door motion durations and motor PWM ratio can be set here.')
            self.sender.append(ET.tostring(xmldata))
            return

        # check received values ok, if not, send a setnumbervector with alert, message
        # and with current self.elements, indicting no change to elements
        receivedmax = int(newelements[4])
        receivedmin = int(newelements[5])

        if receivedmin >= receivedmax:
            xmldata = self.setnumbervector(self.elements, state="Alert", message="The maximum pwm must be greater than the minimum")
            self.sender.append(ET.tostring(xmldata))
            return

        if receivedmax > 95:
            xmldata = self.setnumbervector(self.elements, state="Alert", message="The maximum pwm is 95 max")
            self.sender.append(ET.tostring(xmldata))
            return

        if receivedmin > 50:
            xmldata = self.setnumbervector(self.elements, state="Alert", message="The minimum pwm is 50 max")
            self.sender.append(ET.tostring(xmldata))
            return

        if receivedmin < 1:
            xmldata = self.setnumbervector(self.elements, state="Alert", message="The minimum pwm is 1")
            self.sender.append(ET.tostring(xmldata))
            return

        # change of elements are ok, send setnumbervector back and save the new elements
        xmldata = self.setnumbervector(newelements, state="Ok", message="Door motion durations and motor PWM ratio can be set here.")
        self.sender.append(ET.tostring(xmldata))
        self.write_parameters()



def curve(t, acc_t, fast_t, dec_t, slow_t, fast, slow):
    """Returns a speed value between 0 and fast for a given t
       t is the time (a float in seconds), from zero at which point acceleration starts
       typically t would be incrementing in real time as the door is opening/closing
       acc_t is the acceleration time, after which the 'fast' speed is reached
       fast_t is the time spent at the fast speed
       dec_t is the time spent decelarating from fast to slow
       slow_t is the time then spent running at 'slow' speed.
       after acc_t + fast_t + dec_t + slow_t, 0.0 is returned

       fast is the fast speed, provide a float
       slow is the slow speed, provide a float, this is the slow door speed which typically
       would take the door to a limit stop switch."""

    assert fast > slow > 0.0

    duration = acc_t + fast_t + dec_t

    full_duration = duration + slow_t

    if t >= full_duration:
        return 0.0
    if t >= duration:
        return slow

    if t >= acc_t and t <= acc_t + fast_t:
        # during fast time, fast should be returned
        return fast

    # this list must have nine elements describing an acceleration curve from 0.0 to 1.0
    c = [0.0, 0.05, 0.15, 0.3, 0.5, 0.7, 0.85, 0.95, 1.0]

    # increase from 0.0 to fast during acceleration
    if t <= acc_t:
        # first calculate an increase from 0.0 to 1.0
        # scale acc_t to match the list on the acceleration curve
        tacc = t * 8.0 / acc_t    # when acc_t = 8, tacc = t
        lowindex = int(tacc)
        highindex = lowindex + 1
        diff = c[highindex] - c[lowindex]
        y = diff*tacc + c[lowindex] - diff* lowindex
        if y < 0.0:
            y = 0.0
        # scale 1.0 to be fast
        speed = y * fast
        return round(speed, 3)


    # for the deceleration, treat time in the negative direction
    # so rather than a decrease from 1.0 to 0.0, it again looks like
    # an increase from 0.0 to 1.0

    s = duration - t
    if s >= dec_t:
        return fast
    # scale dec_t to match the list on the acceleration curve
    sdec = s * 8.0 / dec_t
    lowindex = int(sdec)
    highindex = lowindex + 1
    diff = c[highindex] - c[lowindex]
    y = diff*sdec + c[lowindex] - diff* lowindex
    if y < 0.0:
        y = 0.0
    # 1.0 should become 'fast' and 0.0 should become 'slow'
    speed = (fast - slow)*y + slow
    return round(speed, 3)



if __name__=="__main__":

    # start this blocking call
    driver()

