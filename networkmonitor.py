#!/usr/bin/python3


"""networkmonitor.py

Sends a text message every ten seconds, client can check its timestamp, and if
longer than, say 15 seconds, then the connection can be presumed down

"""

import os, sys, collections, asyncio

import xml.etree.ElementTree as ET

from datetime import datetime

# All xml data received on the port from the client should be contained in one of the following tags
TAGS = (b'getProperties',
  #      b'newTextVector',
  #      b'newNumberVector',
  #      b'newSwitchVector',
  #      b'newBLOBVector'
       )

# _STARTTAGS is a tuple of ( b'<newTextVector', ...  ) data received will be tested to start with such a starttag
_STARTTAGS = tuple(b'<' + tag for tag in TAGS)

# _ENDTAGS is a tuple of ( b'</newTextVector>', ...  ) data received will be tested to end with such an endtag
_ENDTAGS = tuple(b'</' + tag + b'>' for tag in TAGS)


_DEVICE = 'Network Monitor'
_NAME = 'TenSecondHeartbeat'
_ELEMENT = 'KeepAlive'


def driver():
    "Blocking call"

    # now start eventloop to read and write to stdin, stdout
    loop = asyncio.get_event_loop()

    connections = _MONITOR(loop)

    while True:
        try:
            loop.run_until_complete(connections.handle_data())
        finally:
            loop.close()


class _MONITOR:

    def __init__(self, loop):
        "Sets the data used by the data handler"
        self.loop = loop
        self.sender = collections.deque(maxlen=5)


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


    async def writer(self, writer):
        """Writes data in sender to stdout writer"""
        while True:
            if self.sender:
                # add a new line to help if the software receiving this is line bufferred
                writer.write(self.sender.popleft() + b"\n")
            else:
                # no message to send, do an async pause
                await asyncio.sleep(0.5)


    async def update(self):
        """Writes data every ten seconds to sender """
        while True:
            await asyncio.sleep(10)
            # and update self.sender with a setTextVector
            self.setTextVector()


    async def reader(self, reader):
        """Reads data from stdin reader which is the input stream of the driver
           if a getProperties is received (only entry in TAGS), then puts a
           defTextVector into self.sender"""
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
                    # Run 'fromindi.receive_from_indiserver' in the default loop's executor:
                    try:
                        root = ET.fromstring(message.decode("utf-8"))
                    except Exception:
                        # possible malformed
                        message = b''
                        messagetagnumber = None
                        continue
                    # and sets xml into the sender deque
                    self.deftextvector(root)

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
                # Run 'fromindi.receive_from_indiserver' in the default loop's executor:
                try:
                    root = ET.fromstring(message.decode("utf-8"))
                except Exception:
                    # possible malformed
                    message = b''
                    messagetagnumber = None
                    continue
                # and sets xml into the sender deque
                self.deftextvector(root)

                # and start again, waiting for a new message
                message = b''
                messagetagnumber = None

    def deftextvector(self, root):
        """Responds to a getProperties, and sets message defTextVector in the sender deque.
           Returns None"""

        if root.tag == "getProperties":

            # expecting something like
            # <getProperties version="1.7" device="Network Monitor" name="TenSecondHeartbeat" />

            version = root.get("version")
            if version != "1.7":
                return

            device = root.get("device")
            # device must be None (for all devices), or value of _DEVICE
            if (not (device is None)) and (device != _DEVICE):
                # not a recognised device
                return

            name = root.get("name")
            # name must be None (for all properties), or value of _NAME which is the only property
            # of this device
            if (not (name is None)) and (name != _NAME):
                # not a recognised property
                return

            # create the responce
            xmldata = ET.Element('defTextVector')
            xmldata.set("device", _DEVICE)
            xmldata.set("name", _NAME)
            xmldata.set("label", "Ten second keep-alive")
            xmldata.set("group", "Status")
            xmldata.set("state", "Ok")
            xmldata.set("perm", "ro")
            timestamp = datetime.utcnow().isoformat(sep='T')
            xmldata.set("timestamp", timestamp)

            te = ET.Element('defText')
            te.set("name", _ELEMENT)
            te.set("label", "Message")
            te.text = f"{timestamp}: Keep-alive message from {_DEVICE}"
            xmldata.append(te)
        else:
            # tag not recognised, do not add anything to sender
            return

        # appends the xml data to be sent to the sender deque object
        self.sender.append(ET.tostring(xmldata))
        return


    def setTextVector(self):
        """Appends setTextVector in the sender deque."""

        # create the setTextVector
        xmldata = ET.Element('setTextVector')
        xmldata.set("device", _DEVICE)
        xmldata.set("name", _NAME)
        timestamp = datetime.utcnow().isoformat(sep='T')
        xmldata.set("timestamp", timestamp)
        xmldata.set("message", "Sent every 10 seconds, an older timestamp indicates connection failure")

        te = ET.Element('oneText')
        te.set("name", _ELEMENT)
        te.text = f"{timestamp}: Keep-alive message from {_DEVICE}"
        xmldata.append(te)

        # appends the xml data to be sent to the sender deque object
        self.sender.append(ET.tostring(xmldata))
        return



if __name__=="__main__":

    # start this blocking call
    driver()

