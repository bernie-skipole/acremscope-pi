#!/home/bernard/acenv/bin/python3


"""doordriver.py

Opens and closes, and reports status of the door

device = 'Roll off door'

Consists of a switch vector,

property name = DOME_SHUTTER
elements = SHUTTER_OPEN, SHUTTER_CLOSE

and a light vector which reports open, openning, closing, close, consists of four
elements

property name = DOOR_STATE
with elements:
OPEN
OPENING
CLOSING
CLOSED

If the actual state is none of these, ie unknown, then an alert is needed


"""

import os, sys, collections, asyncio, time

import xml.etree.ElementTree as ET

from datetime import datetime

import redis

# All xml data received on the port from the client should be contained in one of the following tags
TAGS = (b'getProperties',
  #      b'newTextVector',
  #      b'newNumberVector',
        b'newSwitchVector'
  #      b'newBLOBVector'
       )

# _STARTTAGS is a tuple of ( b'<newTextVector', ...  ) data received will be tested to start with such a starttag
_STARTTAGS = tuple(b'<' + tag for tag in TAGS)

# _ENDTAGS is a tuple of ( b'</newTextVector>', ...  ) data received will be tested to end with such an endtag
_ENDTAGS = tuple(b'</' + tag + b'>' for tag in TAGS)

_DEVICE = 'Roll off door'
_NAME = 'DOME_SHUTTER'


def driver():
    "Blocking call"

    # create a redis connection
    rconn = redis.StrictRedis(host='localhost', port=6379, db=0)

    # create a class which handles the hardware
    # this has a status property,  one of OPEN, OPENING, CLOSING, CLOSED
    door = _DOOR(rconn)

    # now start eventloop to read and write to stdin, stdout
    loop = asyncio.get_event_loop()

    connections = _Driver(loop, door)

    while True:
        try:
            loop.run_until_complete(connections.handle_data())
        finally:
            loop.close()


class _Driver:

    def __init__(self, loop, hardware):
        "Sets the data used by the data handler"
        self.loop = loop
        self.hardware = hardware
        self.status = hardware.status
        self.sender = collections.deque(maxlen=100)

    async def handle_data(self):
        """handle data via stdin and stdout"""
        reader = asyncio.StreamReader(loop=self.loop)
        reader_protocol = asyncio.StreamReaderProtocol(reader, loop=self.loop)
        await self.loop.connect_read_pipe( lambda: reader_protocol, sys.stdin)

        writer_transport, writer_protocol = await self.loop.connect_write_pipe(
                                                       lambda: asyncio.Protocol(),
                                                       sys.stdout)
        writer = asyncio.StreamWriter(writer_transport, writer_protocol, None, self.loop)

        await asyncio.gather(self.reader(reader), self.writer(writer), self.update())


    async def update(self):
        """Gets an updated door status, and if status has changed
           create a setLightVector and a setSwitchVector placing them
           into self.sender for transmission"""
        # check every second, but sender is only updated if a status changes
        while True:            
            await asyncio.sleep(1)
            # call setLightVector, which sets the vector into the sender deque if the door status has changed.
            status = self.hardware.status
            if status == self.status:
                # There has been no change to the status
                continue
            # There has been a change in the status
            self.status = status
            # set the lights to show the new status, this puts xml data into sender
            self.setLightVector()
            # also set the switch
            self.setSwitchVector()


    async def writer(self, writer):
        """Writes data in sender to stdout writer"""
        while True:
            if self.sender:
                # add a new line to help if the software receiving this is line bufferred
                writer.write(self.sender.popleft() + b"\n")
            else:
                # no message to send, do an async pause
                await asyncio.sleep(0.5)


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
            # expecting something like
            # <getProperties version="1.7" />
            # or
            # <getProperties version="1.7" device="Roll off door" />
            # or
            # <getProperties version="1.7" device="Roll off door" name="DOME_SHUTTER" />
            # or
            # <getProperties version="1.7" device="Roll off door" name="DOOR_STATE" />

            version = root.get("version")
            if version != "1.7":
                return
            # check for valid request
            device = root.get("device")
            # device must be None (for all devices), or 'Roll off door' which is this device
            if device is None:
                # and sets xml into the sender deque
                self.defLightVector()             # only one lightvector, so no need to specify further
                self.defSwitchVector()
            elif device == _DEVICE:
                name = root.get("name")
                if name is None:
                    # all properties
                    self.defLightVector()
                    self.defSwitchVector()
                elif name == "DOOR_STATE":  # the door OPEN, CLOSING, OPENING, CLOSED LightVector
                    self.defLightVector()
                elif name == _NAME:  # DOME_SHUTTER
                    self.defSwitchVector()

        elif root.tag == "newSwitchVector":
            # the client is requesting a door open/shut
            # expecting something like
            # <newSwitchVector device="Roll off door" name="DOME_SHUTTER">
            #   <oneSwitch name="SHUTTER_OPEN">On</oneSwitch>
            # </newSwitchVector>

            device = root.get("device")
            if device != _DEVICE:
                # not a recognised device
                return

            name = root.get("name")
            # name must be 'DOME_SHUTTER' which is the only property
            # of this device
            if name != _NAME:
                # not a recognised property
                return

            newstatus = None

            switchlist = root.findall("oneSwitch")
            for setting in switchlist:
                # property name
                pn = setting.get("name")
                # get switch On or Off, remove newlines
                content = setting.text.strip()
                if (pn == 'SHUTTER_OPEN') and (content == "On"):
                    newstatus = "OPENING"
                elif (pn == 'SHUTTER_OPEN') and (content == "Off"):
                    newstatus = "CLOSING"
                elif (pn == 'SHUTTER_CLOSE') and (content == "On"):
                    newstatus = "CLOSING"
                elif (pn == 'SHUTTER_CLOSE') and (content == "Off"):
                    newstatus = "OPENING"

            if newstatus is None:
                return

            # set this action in hardware
            self.hardware.status = newstatus

            # the self.update() method will detect the change in status
            # and will send the appropriate setXXXVectors



    def defSwitchVector(self):
        """Sets defSwitchVector in the sender deque """
        timestamp = datetime.utcnow().isoformat(sep='T')

        xmldata = ET.Element('defSwitchVector')
        xmldata.set("device", 'Roll off door')
        xmldata.set("name", _NAME)
        xmldata.set("label", "Roll Off door control")
        xmldata.set("group", "Status")
        xmldata.set("timestamp", timestamp)
        xmldata.set("perm", "rw")
        xmldata.set("rule", "OneOfMany")

        se_open = ET.Element('defSwitch')
        se_open.set("name", 'SHUTTER_OPEN')
        if self.status == "OPEN":
            se_open.text = "On"
            xmldata.set("state", "Ok")
        elif self.status == "OPENING":
            se_open.text = "On"
            xmldata.set("state", "Busy")
        else:
            se_open.text = "Off"
        xmldata.append(se_open)

        se_close = ET.Element('defSwitch')
        se_close.set("name", 'SHUTTER_CLOSE')
        if self.status == "CLOSED":
            se_close.text = "On"
            xmldata.set("state", "Ok")
        elif self.status == "CLOSING":
            se_close.text = "On"
            xmldata.set("state", "Busy")
        else:
            se_close.text = "Off"
        xmldata.append(se_close)
        # appends the xml data to be sent to the sender deque object
        self.sender.append(ET.tostring(xmldata))
        return


    def setSwitchVector(self):
        """Sets setSwitchVector in the sender deque """
        timestamp = datetime.utcnow().isoformat(sep='T')

        xmldata = ET.Element('setSwitchVector')
        xmldata.set("device", 'Roll off door')
        xmldata.set("name", _NAME)
        xmldata.set("timestamp", timestamp)

        # with its two switch states

        se_open = ET.Element('oneSwitch')
        se_open.set("name", 'SHUTTER_OPEN')
        if self.status == "OPEN":
            se_open.text = "On"
            xmldata.set("state", "Ok")
        elif self.status == "OPENING":
            se_open.text = "On"
            xmldata.set("state", "Busy")
        else:
            se_open.text = "Off"
        xmldata.append(se_open)

        se_close = ET.Element('oneSwitch')
        se_close.set("name", 'SHUTTER_CLOSE')
        if self.status == "CLOSED":
            se_close.text = "On"
            xmldata.set("state", "Ok")
        elif self.status == "CLOSING":
            se_close.text = "On"
            xmldata.set("state", "Busy")
        else:
            se_close.text = "Off"
        xmldata.append(se_close)
        # appends the xml data to be sent to the sender deque object
        self.sender.append(ET.tostring(xmldata))
        return


    def defLightVector(self):
        """Sets defLightVector in the sender deque """
        timestamp = datetime.utcnow().isoformat(sep='T')

        xmldata = ET.Element('defLightVector')
        xmldata.set("device", 'Roll off door')
        xmldata.set("name", 'DOOR_STATE')
        xmldata.set("label", "Roll Off door status")
        xmldata.set("group", "Status")
        xmldata.set("state", "Ok")
        xmldata.set("timestamp", timestamp)
        # four lights
        # OPEN
        # OPENING
        # CLOSING
        # CLOSED
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
        if self.status == "OPEN":
            e1.text = "Ok"
        elif self.status == "OPENING":
            e2.text = "Ok"
        elif self.status == "CLOSING":
            e3.text = "Ok"
        elif self.status == "CLOSED":
            e4.text = "Ok"
        xmldata.append(e1)
        xmldata.append(e2)
        xmldata.append(e3)
        xmldata.append(e4)
        # appends the xml data to be sent to the sender deque object
        self.sender.append(ET.tostring(xmldata))
        return

    def setLightVector(self):
        """Sets door status setLightVector in the sender deque """
        timestamp = datetime.utcnow().isoformat(sep='T')
        xmldata = ET.Element('setLightVector')
        xmldata.set("device", _DEVICE)
        xmldata.set("name", 'DOOR_STATE')
        xmldata.set("timestamp", timestamp)
        # four lights
        # OPEN
        # OPENING
        # CLOSING
        # CLOSED
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

        if self.status == "OPEN":
            e1.text = "Ok"
        elif self.status == "OPENING":
            e2.text = "Ok"
        elif self.status == "CLOSING":
            e3.text = "Ok"
        elif self.status == "CLOSED":
            e4.text = "Ok"
        xmldata.append(e1)
        xmldata.append(e2)
        xmldata.append(e3)
        xmldata.append(e4)
        # appends the xml data to be sent to the sender deque object
        self.sender.append(ET.tostring(xmldata))
        return


class _DOOR:

    def __init__(self, rconn):
        "An object with a status property"
        # status will be one of OPEN CLOSED OPENING CLOSING
        self._status = "CLOSED"
        self.rconn = rconn
        self.update()

    def update(self):
        "Request an update from the pico"
        self.rconn.publish('tx_to_pico', 'pico_roof')

    @property
    def status(self):
        """Monitors the door, and returns the door status, one of OPEN CLOSED OPENING CLOSING"""
        roof_status1 = self.rconn.get('pico_roofdoor1')
        roof_status0 = self.rconn.get('pico_roofdoor0')
        if roof_status0 is None:
            return
        if roof_status1 is None:
            return
        # both doors must be the same to set the status
        if roof_status0 != roof_status1:
            return self._status
        status = int(roof_status0)
        # status is a numeric code
        # 1 : open
        # 2 : opening
        # 3 : closed
        # 4 : closing
        if status == 1:
            self._status = "OPEN"
        elif status == 2:
            self._status = "OPENING"
        elif status == 3:
            self._status = "CLOSED"
        elif status == 4:
            self._status = "CLOSING"
        return self._status

    @status.setter
    def status(self, newstatus):
        """Called to set a new status value"""
        # send this led state to the pico
        if newstatus == "CLOSING":
            self.rconn.publish('tx_to_pico', 'pico_roof_close')
        elif newstatus == "OPENING":
            self.rconn.publish('tx_to_pico', 'pico_roof_open')

    

if __name__=="__main__":

    # start this blocking call
    driver()

