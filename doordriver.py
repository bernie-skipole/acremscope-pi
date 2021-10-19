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
FAST_DURATION
DURATION
MAX_RUNNING_TIME
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
    rconn = redis.StrictRedis(host='localhost', port=6379, db=0)

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

    # now start eventloop to read and write to stdin, stdout
    loop = asyncio.get_event_loop()

    connections = _Driver(loop, sender, leftdoor, rightdoor, lights, roof)

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
# FAST_DURATION
# DURATION
# MAX_RUNNING_TIME
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
        self._fast_duration = 4
        self._duration = 8
        self._max_running_time = 10
        self._maximum = 95
        self._minimum = 5

        # these will be stored in a file with this property name, and in the same directory
        # as this python file
        self.filename = os.path.join(os.path.dirname(os.path.realpath(__file__)), self.name)

        # If slow is True, this temporarily sets self._maximum to low, to self._minimum+1
        self.slow = False


    @property
    def elements(self):
        return [ str(self._fast_duration),
                 str(self._duration),
                 str(self._max_running_time),
                 str(self._maximum),
                 str(self._minimum) ]


    def read_parameters(self):
        """Reads the parameters from a file"""
        if not os.path.isfile(self.filename):
            return
        f = open(self.filename, "r")
        parameter_strings = f.readlines()
        f.close()
        if len(parameter_strings) != 5:
            return
        parameter_list = [ int(p.strip()) for p in parameter_strings]
        self._fast_duration = parameter_list[0]
        self._duration = parameter_list[1]
        self._max_running_time = parameter_list[2]
        self._maximum = parameter_list[3]
        self._minimum = parameter_list[4]

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
            if running_time >= self._max_running_time:
                self.pwm_ratio = 0
                self.moving = False
                self.rconn.publish('tx_to_pico', f'pico_door{self.door}_pwm_0')
                continue

            # door is opening or closing, get the pwm ratio
            pwm = pwmratio(running_time, self._fast_duration, self._duration)

            # pwm is a number between 0 and 1,
            # change to integer between 0 and 100
            # and reduce the value so instead of 100 the maximum ratio is given

            # max_ratio is normally self._maximum but can be self._minimum+1 if self.slow is True
            max_ratio = self._minimum+1 if self.slow else self._maximum          

            if running_time<self._duration/2.0:
                # the start up, scale everything by max_ratio, so 1 becomes max_ratio
                pwm = pwm*max_ratio
            else:
                # the slow-down, scale 1 to be max_ratio, zero to be self.minimum
                m = max_ratio-self._minimum
                pwm = m*pwm + self._minimum

                # pwm = 1,     (max-min) + min  -> max
                # pwm = 0.5,   (max-min) * 0.5 + min  -> 0.5max + 0.5min = (max+min) / 2
                # pwm = 0,     -> min

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

        #FAST_DURATION
        fd = ET.Element('defNumber')
        fd.set("name", 'FAST_DURATION')
        fd.set("label", 'Duration of maximum speed (seconds)')
        fd.set("format", "%2d")
        fd.set("min", "1")
        fd.set("max", "238")   # min== max means ignore
        fd.set("step", "1")    # 0 means ignore
        fd.text = str(self._fast_duration)
        xmldata.append(fd)

        #DURATION
        du = ET.Element('defNumber')
        du.set("name", 'DURATION')
        du.set("label", 'Duration of travel between limit switches (seconds)')
        du.set("format", "%2d")
        du.set("min", "2")
        du.set("max", "239")   # min== max means ignore
        du.set("step", "1")    # 0 means ignore
        du.text = str(self._duration)
        xmldata.append(du)
     
        #MAX_RUNNING_TIME
        mrt = ET.Element('defNumber')
        mrt.set("name", 'MAX_RUNNING_TIME')
        mrt.set("label", 'Maximum running time to cut out if limit switches fail (seconds)')
        mrt.set("format", "%2d")
        mrt.set("min", "3")
        mrt.set("max", "240")   # min== max means ignore
        mrt.set("step", "1")    # 0 means ignore
        mrt.text = str(self._max_running_time)
        xmldata.append(mrt)

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

        if int_elements[0] != self._fast_duration:
            self._fast_duration = int_elements[0]
            fd = ET.Element('oneNumber')
            fd.set("name", 'FAST_DURATION')
            fd.text = str(self._fast_duration)
            xmldata.append(fd)

        if int_elements[1] != self._duration:
            self._duration = int_elements[1]
            du = ET.Element('oneNumber')
            du.set("name", 'DURATION')
            du.text = str(self._duration)
            xmldata.append(du)

        if int_elements[2] != self._max_running_time:
            self._max_running_time = int_elements[2]
            mrt = ET.Element('oneNumber')
            mrt.set("name", 'MAX_RUNNING_TIME')
            mrt.text = str(self._max_running_time)
            xmldata.append(mrt)

        if int_elements[3] != self._maximum:
            self._maximum = int_elements[3]
            mx = ET.Element('oneNumber')
            mx.set("name", 'MAXIMUM')
            mx.text = str(self._maximum)
            xmldata.append(mx)

        if int_elements[4] != self._minimum:
            self._minimum = int_elements[4]
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
            if numname == 'FAST_DURATION':
                newelements[0] = item.text.strip()
            elif numname == 'DURATION':
                newelements[1] = item.text.strip()
            elif numname == 'MAX_RUNNING_TIME':
                newelements[2] = item.text.strip()
            elif numname == 'MAXIMUM':
                newelements[3] = item.text.strip()
            elif numname == 'MINIMUM':
                newelements[4] = item.text.strip()

        # check newelements received

        # If no change to elements, send an ok response
        if newelements == self.elements:
            xmldata = self.setnumbervector(self.elements, state="Ok", message='Door motion durations and motor PWM ratio can be set here.')
            self.sender.append(ET.tostring(xmldata))
            return


        # if not ok, send a setnumbervector with alert, message
        # and with current self.elements, indicting no change to elements
        intelements = [int(i) for i in newelements]

        if intelements[0] >= intelements[1]:
            xmldata = self.setnumbervector(self.elements, state="Alert", message="High speed duration must be shorter than the duration of travel")
            self.sender.append(ET.tostring(xmldata))
            return

        if intelements[1] >= intelements[2]:
            xmldata = self.setnumbervector(self.elements, state="Alert", message="The maximum running time must be longer than the duration of travel")
            self.sender.append(ET.tostring(xmldata))
            return

        if intelements[4] >= intelements[3]:
            xmldata = self.setnumbervector(self.elements, state="Alert", message="The maximum pwm must be greater than the minimum")
            self.sender.append(ET.tostring(xmldata))
            return

        # change of elements are ok, send setnumbervector back and save the new elements
        xmldata = self.setnumbervector(newelements, state="Ok", message="Door motion durations and motor PWM ratio can be set here.")
        self.sender.append(ET.tostring(xmldata))
        self.write_parameters()



########### door motion control functions
#
# these control the pwm ratio of the motors
# giving an acceleration curve against time


def pwmratio(t, fast_duration, duration):
    """Returns a value between 0 and 1 for a given t
       duration is the duration of the perod, after which the value returned is 0.0
       fast_duration is the period where the value returned will be 1
       for example, if duration is 60, and fast_duration is 40, then the ratio will climb from 0 to 1
       given a t of 0-10, then 1 for 10-50, and finally, ramp down to 0 for 50-60
       and beyond 60 will stay at 0"""

    if t >= duration:
        return 0.0
    if fast_duration >= duration:
        if t < duration:
            return 1.0
    # so t is less than duration
    # scale t and duration
    acc_time = (duration - fast_duration)/2.0
    scale = 8.0/acc_time
    # the value of 8.0 is used as the following call to curve
    # is set with an acceleration time of 8
    # The curve function returns a pwm ration beteen 0 and 1
    return curve(t*scale, duration*scale)



def curve(t, duration):
    """Returns a value between 0 and 1.0 for a given t
       with an eight second acceleration and deceleration
       For t from 0 to 8 increases from 0 up to 1.0
       For t from duration-8 to duration decreases to 0
       For t beyond duration, returns 0"""

    if t >= duration:
        return 0

    half = duration/2.0
    if t<=half:
        # for the first half of duration, increasing speed to a maximum of 1.0 after 8 seconds
        if t>8.0:
            return 1.0
    else:
        # for the second half of duration, decreasing speed to zero when there are 8 seconds left
        if duration-t>8.0:
            return 1.0
        t = 20 - (duration-t)

    # This curve is a fit increasing to 1 (or at least near to 1) with t from 0 to 8,
    # and decreasing with t from 12 to 20
    a = -0.0540937
    b = 0.330319
    c = -0.0383795
    d = 0.00218635
    e = -5.46589e-05
    y = a + b*t + c*t*t + d*t*t*t + e*t*t*t*t
    if y < 0.0:
        y = 0.0
    if y > 1.0:
        y = 1.0
    return round(y, 2)



if __name__=="__main__":

    # start this blocking call
    driver()

