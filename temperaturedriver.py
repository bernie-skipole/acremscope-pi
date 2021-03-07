#!/home/bernard/acenv/bin/python3


"""temperaturedriver.py

Gets temperature from the pi sensor, and sends it at regular intervals

Initially, this is a simulator, using metoffice data

"""

import os, sys, collections, asyncio, time

import urllib.request, json     # required for met office communications

import xml.etree.ElementTree as ET

from datetime import datetime

import redis

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


_DEVICE = 'Rempi01 Temperature'
_NAME = 'ATMOSPHERE'
_ELEMENT = 'TEMPERATURE'

_MET_OFFICE_KEY = ''


class _TEMPERATURE:

    conversion_factor = 3.3 / (65535)

    def __init__(self, rconn, loop):
        "Sets the data used by the data handler"
        self.loop = loop
        self.rconn = rconn
        self.sender = collections.deque(maxlen=100)
        # start with zero centigrade, which should be immediately overwritten
        self.temperature = "273.15"
        self.timestamp = datetime.utcnow().isoformat(sep='T')
        # request temperature from pico
        self.rconn.publish('tx_to_pico', 'pico_temperature')
        # wait a couple of seconds
        time.sleep(2)
        # and hopefully get the latest temperature
        self.temperature, self.timestamp = self.get_temperature()


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
        return str(temperature), datetime.utcnow().isoformat(sep='T')


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
        """Gets an updated temperature, and creates a setNumberVector placing it into self.sender for transmission"""
        # Check every hour
        while True:
            await asyncio.sleep(3595)
            # request temperature from pico
            self.rconn.publish('tx_to_pico', 'pico_temperature')
            # and after publishing the request, hopefully get a reply       
            await asyncio.sleep(5)
            temperature, timestamp = self.get_temperature()
            if timestamp == self.timestamp:
                # no update
                continue
            self.temperature = temperature
            self.timestamp = timestamp
            # create the setNumberVector
            xmldata = ET.Element('setNumberVector')
            xmldata.set("device", _DEVICE)
            xmldata.set("name", _NAME)
            xmldata.set("timestamp", timestamp)
            ne = ET.Element('oneNumber')
            ne.set("name", _ELEMENT)
            ne.text = temperature
            xmldata.append(ne)
            # appends the xml data to be sent to the sender deque object
            self.sender.append(ET.tostring(xmldata))


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
        """Reads data from stdin reader which is the input stream of the driver
           if a getProperties is received (only entry in TAGS), then puts a
           defNumberVector into self.sender"""
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
                    ########## does not measure temperature, just gets last measured value,
                    # and sets xml into the sender deque
                    self.defnumbervector(root)

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
                ########## does not measure temperature, just gets last measured value,
                # and sets xml into the sender deque
                self.defnumbervector(root)

                # and start again, waiting for a new message
                message = b''
                messagetagnumber = None

    def defnumbervector(self, root):
        """Responds to a getProperties, and sets temperature defNumberVector in the sender deque.
           Returns None"""

        if root.tag == "getProperties":

            # expecting something like
            # <getProperties version="1.7" device="Rempi01 Temperature" name="Temperature" />

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
            xmldata = ET.Element('defNumberVector')
            xmldata.set("device", _DEVICE)
            xmldata.set("name", _NAME)
            xmldata.set("label", "Temperature (Kelvin)")
            xmldata.set("group", "Status")
            xmldata.set("state", "Ok")
            xmldata.set("perm", "ro")
            xmldata.set("timestamp", self.timestamp)

            ne = ET.Element('defNumber')
            ne.set("name", _ELEMENT)
            ne.set("format", "%.2f")
            ne.set("min", "0")
            ne.set("max", "0")   # min== max means ignore
            ne.set("step", "0")    # 0 means ignore
            ne.text = self.temperature
            xmldata.append(ne)
        else:
            # tag not recognised, do not add anything to sender
            return

        # appends the xml data to be sent to the sender deque object
        self.sender.append(ET.tostring(xmldata))
        return



if __name__=="__main__":

    # create a redis connection
    rconn = redis.StrictRedis(host='localhost', port=6379, db=0)

    # start eventloop to read and write to stdin, stdout
    loop = asyncio.get_event_loop()

    connections = _TEMPERATURE(rconn, loop)

    while True:
        try:
            loop.run_until_complete(connections.handle_data())
        finally:
            loop.close()

