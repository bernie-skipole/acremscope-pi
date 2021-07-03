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
  #      b'newTextVector',
  #      b'newNumberVector',
        b'newSwitchVector'
  #      b'newBLOBVector'
       )

# _STARTTAGS is a tuple of ( b'<newTextVector', ...  ) data received will be tested to start with such a starttag
_STARTTAGS = tuple(b'<' + tag for tag in TAGS)

# _ENDTAGS is a tuple of ( b'</newTextVector>', ...  ) data received will be tested to end with such an endtag
_ENDTAGS = tuple(b'</' + tag + b'>' for tag in TAGS)


_DEVICE = 'Rempico01'


def driver():
    "Blocking call"

    # create a redis connection
    rconn = redis.StrictRedis(host='localhost', port=6379, db=0)

    # create classes which handles the hardware
    led = _LED(rconn)
    temperature = _TEMPERATURE(rconn)
    monitor = _MONITOR(rconn)

    # now start eventloop to read and write to stdin, stdout
    loop = asyncio.get_event_loop()

    connections = _Driver(loop, led, temperature, monitor)

    while True:
        try:
            loop.run_until_complete(connections.handle_data())
        finally:
            loop.close()



class _Driver:

    def __init__(self, loop, led, temperature, monitor):
        "Sets the data used by the data handler"
        self.loop = loop
        self.sender = collections.deque(maxlen=100)
        self.led = led
        self.temperature = temperature
        self._timestamp = datetime.utcnow().isoformat(sep='T')
        self.monitor = monitor
        self._monitor_status = False
        # wait a couple of seconds, for hardware to be updated
        time.sleep(2)



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
        """15 second pico monitor and hourly temperature"""

        while True:
            # every 15 seconds, request a monitor
            for period in range(0, 239):
                # 239 periods of 15 seconds is 3585, so fifteen seconds short of an hour
                await asyncio.sleep(10)          
                # request monitor echo
                self.monitor.update()
                # wait another 5 seconds, giving the pico time to reply
                await asyncio.sleep(5)
                # check for a reply
                echo = self.monitor.status
                if echo and self._monitor_status:
                    # pico is echoing, no change from the current state, continue to next period
                    continue
                self._monitor_status = echo
                # No echo, or the state has changed and echo has just started
                # so send a setLightVector for the monitor alert
                # and update led status which will also show an alert
                # create the setLightVector
                xmldata = ET.Element('setLightVector')
                xmldata.set("device", _DEVICE)
                xmldata.set("name", 'MONITOR')
                # note - limit timestamp characters to :21 to avoid long fractions of a second 
                xmldata.set("timestamp", datetime.utcnow().isoformat(sep='T')[:21])
                le = ET.Element('oneLight')
                le.set("name", 'PICOALIVE')
                if echo:
                    xmldata.set("state", "Ok")
                    le.text = "Ok"
                else:
                    xmldata.set("state", "Alert")
                    le.text = "Alert"
                xmldata.append(le)
                # appends the xml data to be sent to the sender deque object
                self.sender.append(ET.tostring(xmldata))
                if echo:
                    # if echoing, as it has just started again, request led status from pico
                    self.led.update()
                # wait another 5 seconds, giving the pico time to reply
                await asyncio.sleep(5)
                # and update the led status by sending a setSwitchVector
                self.setswitchvector()
                
            # Request temperature  every hour

            # wait another 10 seconds
            await asyncio.sleep(10)
            # request temperature from pico
            self.temperature.update()
            # and after publishing the request, hopefully get a reply 
            # wait another five seconds to give a total time cycle of one hour      
            await asyncio.sleep(5)
            temperature, timestamp = self.temperature.status
            if timestamp == self._timestamp:
                # no update
                continue
            self._timestamp = timestamp
            # create the setNumberVector
            xmldata = ET.Element('setNumberVector')
            xmldata.set("device", _DEVICE)
            xmldata.set("name", 'ATMOSPHERE')
            # note - limit timestamp characters to :21 to avoid long fractions of a second 
            xmldata.set("timestamp", timestamp[:21])
            ne = ET.Element('oneNumber')
            ne.set("name", 'TEMPERATURE')
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
                    ########### RUN HARDWARECONTROL ###############
                    self.hardwarecontrol(root)

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
                ########### RUN HARDWARECONTROL ###############
                self.hardwarecontrol(root)

                # and start again, waiting for a new message
                message = b''
                messagetagnumber = None



    def hardwarecontrol(self, root):
        """Handles the received XML and, if data is to be sent,
           sets xml in the sender deque.
           If getProperties received, send def's of LED and ATMOSPHERE
           If a newSwitchVector is received, set LED """

        if root.tag == "getProperties":

            # expecting something like
            # <getProperties version="1.7" device="Rempico01" name="LED" />

            version = root.get("version")
            if version != "1.7":
                return

            device = root.get("device")
            # device must be None (for all devices), or 'Rempico01' which is this device
            if (not (device is None)) and (device != _DEVICE):
                # not a recognised device
                return

            name = root.get("name")
            # name must be None (for all properties), or 'LED' or 'ATMOSPHERE'
            # of this device
            if (not (name is None)) and (name != 'LED') and (name != 'ATMOSPHERE'):
                # not a recognised property
                return

            if device is None:
                # send def for all devices/properties 
                self.defswitchvector(root)   # the led switch
                self.defnumbervector(root)   # the temperature value
                self.deflightvector(root)    # the monitor value
            elif name is None:
                # This device, all properties
                self.defswitchvector(root)   # the led switch
                self.defnumbervector(root)   # the temperature value
                self.deflightvector(root)    # the monitor value
            elif name is 'LED':
                self.defswitchvector(root)   # the led switch
            elif name is 'ATMOSPHERE':
                self.defnumbervector(root)   # the temperature value
            elif name is 'MONITOR':
                self.deflightvector(root)    # the monitor value

        elif root.tag == "newSwitchVector":

            # expecting something like
            # <newSwitchVector device="Rempico01" name="LED">
            #   <oneSwitch name="LED ON">On</oneSwitch>
            # </newSwitchVector>

            device = root.get("device")
            # device must be  'Rempico01' which is this device
            if device != _DEVICE:
                # not a recognised device
                return

            name = root.get("name")
            # name must be 'LED' which is the only switch
            # of this device
            if name != 'LED':
                # not a recognised property
                return

            self.newswitchvector(root)  # change state of led switch


    def defswitchvector(self, root):
        """Responds to a getProperties, for the 'LED' property, and sets defSwitchVector in the sender deque.
           Returns None"""

        # do an hardware check of the led status
        led = self.led.status

        # create the responce
        xmldata = ET.Element('defSwitchVector')
        xmldata.set("device", _DEVICE)
        xmldata.set("name", 'LED')
        xmldata.set("label", "LED")
        xmldata.set("group", "LED")
        xmldata.set("state", "Ok")
        xmldata.set("perm", "rw")
        xmldata.set("rule", "OneOfMany")

        if self.monitor:
            xmldata.set("state", "Ok")
        else:
            xmldata.set("state", "Alert")
            xmldata.set("message", "State Unknown - pico monitor indicates fault")

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

        # appends the xml data to be sent to the sender deque object
        self.sender.append(ET.tostring(xmldata))


    def defnumbervector(self, root):
        """Responds to a getProperties, for the 'ATMOSPHERE' property, and sets defNumberVector in the sender deque.
           Returns None"""

        temperature, timestamp = self.temperature.status
        self._timestamp = timestamp

        # create the responce
        xmldata = ET.Element('defNumberVector')
        xmldata.set("device", _DEVICE)
        xmldata.set("name", 'ATMOSPHERE')
        xmldata.set("label", "Temperature (Kelvin)")
        xmldata.set("group", "Temperature")
        xmldata.set("state", "Ok")
        xmldata.set("perm", "ro")
        xmldata.set("timestamp", timestamp[:21])

        ne = ET.Element('defNumber')
        ne.set("name", 'TEMPERATURE')
        ne.set("format", "%.2f")
        ne.set("min", "0")
        ne.set("max", "0")   # min== max means ignore
        ne.set("step", "0")    # 0 means ignore
        ne.text = temperature
        xmldata.append(ne)

        # appends the xml data to be sent to the sender deque object
        self.sender.append(ET.tostring(xmldata))



    def newswitchvector(self, root):
        "Set LED state, and return a setSwitchVector"
        # get current led status
        led = self.led.status

        switchlist = root.findall("oneSwitch")
        for setting in switchlist:
            # oneSwitch element name
            en = setting.get("name")
            # get switch On or Off, remove newlines
            content = setting.text.strip()
            if (en == "LED ON") and (content == "On"):
                led = True
            if (en == "LED ON") and (content == "Off"):
                led = False
            if (en == "LED OFF") and (content == "On"):
                led = False
            if (en == "LED OFF") and (content == "Off"):
                led = True

        # send this led state to the pico
        self.led.status = led

        # send setSwitchVector vector
        self.setswitchvector(led)
        return



    def setswitchvector(self, led=None):
        "Reads current led state (if led not given), and sends a setSwitchVector"
        # get current led status
        if led is None:
            led = self.led.status

        # send setSwitchVector vector
        # create the response
        xmldata = ET.Element('setSwitchVector')
        xmldata.set("device", _DEVICE)
        xmldata.set("name", 'LED')
 
        if self.monitor:
            xmldata.set("state", "Ok")
            xmldata.set("message", "Awaiting instruction")
        else:
            xmldata.set("state", "Alert")
            xmldata.set("message", "State Unknown - pico monitor indicates fault")

        # with its two switch states

        se_on = ET.Element('oneSwitch')
        se_on.set("name", "LED ON")
        if led:
            se_on.text = "On"
        else:
            se_on.text = "Off"
        xmldata.append(se_on)

        se_off = ET.Element('oneSwitch')
        se_off.set("name", "LED OFF")
        if led:
            se_off.text = "Off"
        else:
            se_off.text = "On"
        xmldata.append(se_off)

        # appends the xml data to be sent to the sender deque object
        self.sender.append(ET.tostring(xmldata))
        return



    def deflightvector(self, root):
        """Responds to a getProperties, for the 'MONITOR' property, and sets defLightVector in the sender deque.
           Returns None"""

        # create the defLightVector
        xmldata = ET.Element('defLightVector')
        xmldata.set("device", _DEVICE)
        xmldata.set("name", 'MONITOR')
        xmldata.set("label", "REMPICO01 Status")
        xmldata.set("group", "Status")
        le = ET.Element('defLight')
        le.set("name", 'PICOALIVE')
        le.set("label", 'Monitor echo from pico 01')
        if self.monitor.status:
            xmldata.set("state", "Ok")
            le.text = "Ok"
            self._monitor_status = True
        else:
            xmldata.set("state", "Alert")
            le.text = "Alert"
            self._monitor_status = False
        xmldata.append(le)

        # appends the xml data to be sent to the sender deque object
        self.sender.append(ET.tostring(xmldata))





class _LED:

    def __init__(self, rconn):
        "An object with a status property"
        self.rconn = rconn
        self.update()

    def update(self):
        "Request an update from the pico"
        self.rconn.publish('tx_to_pico', 'pico_led')


    @property
    def status(self):
        "Returns the led status, True or False"
        pico_led = self.rconn.get('pico_led')
        if pico_led == b'On':
            return True
        else:
            return False


    @status.setter
    def status(self, newstatus):
        """Called to set a new status value"""
        # send this led state to the pico
        if newstatus:
            self.rconn.publish('tx_to_pico', 'pico_led_On')
        else:
            self.rconn.publish('tx_to_pico', 'pico_led_Off')
    


class _TEMPERATURE:

    conversion_factor = 3.3 / (65535)

    def __init__(self, rconn):
        "An object with a status property"
        self.rconn = rconn
        # start with zero centigrade, which should be immediately overwritten
        self._temperature = "273.15"
        self._timestamp = datetime.utcnow().isoformat(sep='T')
        self.update()

    def update(self):
        "Request an update from the pico"
        self.rconn.publish('tx_to_pico', 'pico_temperature')

    @property
    def status(self):
        "Returns the temperature, timestamp. If not found, returns current self._temperature, self._timestamp"
        reading = self.rconn.get('pico_temperature')
        if reading is None:
            return self._temperature, self._timestamp
        # having read a temperature, delete it. so it is only valid for this timestamp
        self.rconn.delete('pico_temperature')
        # The temperature sensor measures the Vbe voltage of a biased bipolar diode, connected to the fifth ADC channel
        # Typically, Vbe = 0.706V at 27 degrees C, with a slope of -1.721mV (0.001721) per degree. 
        temperature = 27 - (int(reading)*self.conversion_factor - 0.706)/0.001721 + 273.15 # 273.15 added to convert to Kelvin
        self._temperature = str(temperature)
        self._timestamp = datetime.utcnow().isoformat(sep='T')
        return self._temperature, self._timestamp


class _MONITOR:

    def __init__(self, rconn):
        "An object with a status property"
        self.rconn = rconn
        self._count = 0
        self.update()

    def update(self):
        "Request an update from the pico"
        self._count += 1
        if self._count == 255:
            self._count = 0
        self.rconn.publish('tx_to_pico', f'pico_monitor_{self._count}')

    @property
    def status(self):
        "Returns True if receiving echos from the pico, False otherwise"
        monitor_value = self.rconn.get('pico_monitor')
        if monitor_value is None:
            return False
        if self._count == int(monitor_value):
            return True
        else:
            return False





if __name__=="__main__":

    # start this blocking call
    driver()



    





