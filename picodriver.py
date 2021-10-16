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

sys.path.insert(0, "/home/bernard/indi")

import picoled, picotemperature, picomonitor

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

    led = picoled.LED(device, rconn, sender)
    temperature = picotemperature.Temperature(device, rconn, sender)
    monitor = picomonitor.Monitor(device, rconn, sender)


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

        # await asyncio.gather(self.reader(reader), self.writer(writer), self.update())


#    async def update(self):
#        """Runs continuosly with .2 second breaks, updating any objects, which can in turn add xml to the sender"""
#        while True:            
#            await asyncio.sleep(0.2)
#            for item in self.items:
#                item.update()

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




if __name__=="__main__":

    # start this blocking call
    driver()

