#!/home/bernard/acenv/bin/python3


"""doordriver.py

Opens and closes, and reports status of the door

device = 'Roll off door'

Consists of a switch vector,

property name = DOME_SHUTTER
elements = SHUTTER_OPEN, SHUTTER_CLOSE

and a light vector which reports open, opening, closing, closed, consists of four
elements

property name = DOOR_STATE
with elements:
OPEN
OPENING
CLOSING
CLOSED

and two number vectors LEFT_DOOR RIGHT_DOOR, each with elements
FAST_DURATION
DURATION
MAX_RUNNING_TIME
MAXIMUM
MINIMUM

If the actual state is none of these, ie unknown, then an alert is needed

"""

import os, sys, collections, asyncio, time

import xml.etree.ElementTree as ET

from datetime import datetime

import redis

sys.path.insert(0, "/home/bernard/indi")

import doors, statuslights, shutter

# All xml data received on the port from the client should be contained in one of the following tags
TAGS = (b'getProperties',
  #      b'newTextVector',
        b'newNumberVector',
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

    # create a deque, data to be sent to indiderver is appended to this
    sender = collections.deque(maxlen=100)

    # create classes which handle the hardware

    leftdoor = doors.Door(_DEVICE, 0, rconn, sender)
    rightdoor = doors.Door(_DEVICE, 1, rconn, sender)

    lights = statuslights.StatusLights(_DEVICE, leftdoor, rightdoor, rconn, sender)

    roof = shutter.Roof(_DEVICE, leftdoor, rightdoor, lights, rconn, sender)

    # now start eventloop to read and write to stdin, stdout
    loop = asyncio.get_event_loop()

    connections = _Driver(loop, sender, leftdoor, rightdoor, lights, roof)

    while True:
        try:
            loop.run_until_complete(connections.handle_data())
        finally:
            loop.close()


class _Driver:

    def __init__(self, loop, sender, leftdoor, rightdoor, lights, roof):
        "Sets the data used by the data handler"
        self.loop = loop
        self.roof = roof
        self.sender = sender
        self.leftdoor = leftdoor
        self.rightdoor = rightdoor
        self.lights = lights

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
        """Runs contiuosly with .2 second breaks, updating any objects, which can in turn add xml to the sender"""
        while True:            
            await asyncio.sleep(0.2)
            # roof switch
            self.roof.update()
            # update left and right doors 
            self.leftdoor.update()
            self.rightdoor.update()
            # update lights vector
            self.lights.update()

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
            # or
            # <getProperties version="1.7" device="Roll off door" name="LEFT_DOOR" />
            # or
            # <getProperties version="1.7" device="Roll off door" name="RIGHT_DOOR" />

            version = root.get("version")
            if version != "1.7":
                return
            # check for valid request
            device = root.get("device")
            # device must be None (for all devices), or 'Roll off door' which is this device
            if device is None:
                # and sets xml into the sender deque
                self.roof.respond()
                self.leftdoor.respond()
                self.rightdoor.respond()
                self.lights.respond()
            elif device == _DEVICE:
                name = root.get("name")
                if name is None:
                    # all properties
                    self.roof.respond()
                    self.leftdoor.respond()
                    self.rightdoor.respond()
                    self.lights.respond()
                elif name == self.lights.name:  # the door OPEN, CLOSING, OPENING, CLOSED LightVector
                    self.lights.respond()
                elif name == self.roof.name:  # DOME_SHUTTER
                    self.roof.respond()
                elif name == self.leftdoor.name:
                    self.leftdoor.respond()
                elif name == self.rightdoor.name:
                    self.rightdoor.respond()

        elif root.tag == "newSwitchVector":
            # the client is requesting a door open/shut
            # expecting something like
            # <newSwitchVector device="Roll off door" name="DOME_SHUTTER">
            #   <oneSwitch name="SHUTTER_OPEN">On</oneSwitch>
            # </newSwitchVector>
            self.roof.newvector(root)

        elif root.tag == "newNumberVector":
            self.leftdoor.newvector(root)
            self.rightdoor.newvector(root)




if __name__=="__main__":

    # start this blocking call
    driver()

