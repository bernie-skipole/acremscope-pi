#!/usr/bin/python3


"""leddriver.py

Gets and sets LED on the pi

rw value
device is 'Rempi01 LED'
property name  is 'LED'
element names are 'LED ON' and 'LED OFF'
"""

import os, sys, collections, asyncio

import xml.etree.ElementTree as ET

from datetime import datetime

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


def driver(led=False):
    "Blocking call, led is the initial LED state, True for ON, False for OFF"

    # now start eventloop to read and write to stdin, stdout
    loop = asyncio.get_event_loop()

    connections = _LED(led, loop)

    while True:
        try:
            loop.run_until_complete(connections.handle_data())
        finally:
            loop.close()


class _LED:

    def __init__(self, led, loop):
        "Sets the data used by the data handler"
        self.loop = loop
        self.led = led
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

        await asyncio.gather(self.reader(reader), self.writer(writer))


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
                    # Run 'fromindi.receive_from_indiserver' in the default loop's executor:
                    try:
                        root = ET.fromstring(message.decode("utf-8"))
                    except Exception:
                        # possible malformed
                        message = b''
                        messagetagnumber = None
                        continue
                    ########### RUN HARDWARECONTROL ###############

                    # if blocking
                    # self.led = await self.loop.run_in_executor(None, _hardwarecontrol, root, self.sender, self.led)

                    # if not blocking
                    self.led = _hardwarecontrol(root, self.sender, self.led)

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
                ########### RUN HARDWARECONTROL ###############

                # if blocking
                # self.led = await self.loop.run_in_executor(None, _hardwarecontrol, root, self.sender, self.led)

                # if not blocking
                self.led = _hardwarecontrol(root, self.sender, self.led)

                # and start again, waiting for a new message
                message = b''
                messagetagnumber = None



def _hardwarecontrol(root, sender, led):
    """Handles the received XML and, if data is to be sent,
       sets xml in the sender deque. Returns the new led state"""

    # this timestamp is the time at which the data is received
    # timestamp = datetime.utcnow().isoformat(sep='T')

    if root.tag == "getProperties":

        # expecting something like
        # <getProperties version="1.7" device="Rempi01 LED" name="LED" />

        version = root.get("version")
        if version != "1.7":
            return led

        device = root.get("device")
        # device must be None (for all devices), or 'Rempi01 LED' which is this device
        if (not (device is None)) and (device != 'Rempi01 LED'):
            # not a recognised device
            return led

        name = root.get("name")
        # name must be None (for all properties), or 'LED' which is the only property
        # of this device
        if (not (name is None)) and (name != 'LED'):
            # not a recognised property
            return led

        # normally would do an hardware check of the led status

        # create the responce
        xmldata = ET.Element('defSwitchVector')
        xmldata.set("device", 'Rempi01 LED')
        xmldata.set("name", 'LED')
        xmldata.set("label", "LED")
        xmldata.set("group", "Status")
        xmldata.set("state", "Ok")
        xmldata.set("perm", "rw")
        xmldata.set("rule", "OneOfMany")

        se_on = ET.Element('defSwitch')
        se_on.set("name", "LED ON")
        if led:
            se_on.text = "On"
        else:
            se_on.text = "Off"
        xmldata.append(se_on)

        se_off = ET.Element('defSwitch')
        se_off.set("name", "LED OFF")
        if led:
            se_off.text = "Off"
        else:
            se_off.text = "On"
        xmldata.append(se_off)
        newled = led
    elif root.tag == "newSwitchVector":
        # normally the new value would be set in hardware, in this case it is set
        # into the led value returned

        # expecting something like
        # <newSwitchVector device="Rempi01 LED" name="LED">
        #   <oneSwitch name="LED ON">On</oneSwitch>
        # </newSwitchVector>

        device = root.get("device")
        # device must be  'Rempi01 LED' which is this device
        if device != 'Rempi01 LED':
            # not a recognised device
            return led

        name = root.get("name")
        # name must be 'LED' which is the only property
        # of this device
        if name != 'LED':
            # not a recognised property
            return led

        newled = led

        switchlist = root.findall("oneSwitch")
        for setting in switchlist:
            # property name
            pn = setting.get("name")
            # get switch On or Off, remove newlines
            content = setting.text.strip()
            if (pn == "LED ON") and (content == "On"):
                newled = True
            if (pn == "LED ON") and (content == "Off"):
                newled = False
            if (pn == "LED OFF") and (content == "On"):
                newled = False
            if (pn == "LED OFF") and (content == "Off"):
                newled = True

        # send setSwitchVector vector
        # create the response
        xmldata = ET.Element('setSwitchVector')
        xmldata.set("device", 'Rempi01 LED')
        xmldata.set("name", 'LED')
        xmldata.set("state", "Ok")

        # with its two switch states

        se_on = ET.Element('oneSwitch')
        se_on.set("name", "LED ON")
        if newled:
            se_on.text = "On"
        else:
            se_on.text = "Off"
        xmldata.append(se_on)

        se_off = ET.Element('oneSwitch')
        se_off.set("name", "LED OFF")
        if newled:
            se_off.text = "Off"
        else:
            se_off.text = "On"
        xmldata.append(se_off)

    else:
        # tag not recognised, do not add anything to sender
        # return current state of led
        return led

    # appends the xml data to be sent to the sender deque object
    sender.append(ET.tostring(xmldata))
    return newled

    

if __name__=="__main__":

    # start this blocking call, with an initial LED value of False
    driver(led=False)

