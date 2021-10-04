#!/home/bernard/acenv/bin/python3


"""picoserial.py

Requires the package pyserial
"""

import os, sys, time

from datetime import datetime

import redis, serial

def receiver(ser, rconn):
    "Serial port receiver - sets received values in redis"
    returnval = ser.read(4) # only four should arrive
    if not returnval:
        return
    # discard received data until synchronised, at which point data
    # should come as four bytes at a time
    if len(returnval) != 4:
        return
    if returnval[3] != 255:
        # out of sync, try receiving a single byte
        # until a timeout or 255 is received
        # to attempt to get into sync
        while True:
            getbyte = ser.read(1)
            if getbyte is None:
                # timed out
                return
            if getbyte == 255:
                # in sync
                return
    # parse the data
    if returnval[0] == 1:
        # LED code, received after sending an led set request
        if (returnval[1] == 25) and (returnval[2] == 0):
            rconn.set('pico_led', 'Off')
        if (returnval[1] == 25) and (returnval[2] == 1):
            rconn.set('pico_led', 'On')
    if returnval[0] == 2:
        # LED code, received after sending an led get state request
        if (returnval[1] == 25) and (returnval[2] == 0):
            rconn.set('pico_led', 'Off')
        if (returnval[1] == 25) and (returnval[2] == 1):
            rconn.set('pico_led', 'On')
    elif (returnval[0] == 3) and (returnval[1] == 0):
        # Echo monitor code
        # value as a one byte number, save to redis as integer
        value = int.from_bytes( [returnval[2]], 'big')
        rconn.set('pico_monitor', value)
    elif returnval[0] == 5:
        # Temperature, as a two byte a to d conversion, save to redis as integer
        value = int.from_bytes( [returnval[1], returnval[2]], 'big')
        rconn.set('pico_temperature', value)
    elif returnval[0] == 7:
        # the status code of the door, save to redis as integer
        if (returnval[1] == 1):
            # it is door motor 1
            rconn.set('pico_roofdoor1', int.from_bytes([returnval[2]], 'big')  )
        else:
            # it is door motor 0
            rconn.set('pico_roofdoor0', int.from_bytes([returnval[2]], 'big')  )
    elif returnval[0] == 12:
        # parameter values for door0
        if (returnval[1] == 1):
            # fast duration
            rconn.set('pico_door0_fast_duration', int.from_bytes([returnval[2]], 'big')  )
        if (returnval[1] == 2):
            # duration
            rconn.set('pico_door0_duration', int.from_bytes([returnval[2]], 'big')  )
        if (returnval[1] == 3):
            # max_running_time
            rconn.set('pico_door0_max_running_time', int.from_bytes([returnval[2]], 'big')  )
        if (returnval[1] == 4):
            # maximum ratio
            rconn.set('pico_door0_maximum', int.from_bytes([returnval[2]], 'big')  )
        if (returnval[1] == 5):
            # minimum ratio ratio
            rconn.set('pico_door0_minimum', int.from_bytes([returnval[2]], 'big')  )
    elif returnval[0] == 13:
        # parameter values for door1
        if (returnval[1] == 1):
            # fast duration
            rconn.set('pico_door1_fast_duration', int.from_bytes([returnval[2]], 'big')  )
        if (returnval[1] == 2):
            # duration
            rconn.set('pico_door1_duration', int.from_bytes([returnval[2]], 'big')  )
        if (returnval[1] == 3):
            # max_running_time
            rconn.set('pico_door1_max_running_time', int.from_bytes([returnval[2]], 'big')  )
        if (returnval[1] == 4):
            # maximum ratio
            rconn.set('pico_door1_maximum', int.from_bytes([returnval[2]], 'big')  )
        if (returnval[1] == 5):
            # minimum ratio ratio
            rconn.set('pico_door1_minimum', int.from_bytes([returnval[2]], 'big')  )

   


def sender(data, ser, rconn):
    "Sends data via the serial port"
    if data == b'pico_led_On':
        # turns on led
        bincode = bytes([1, 25, 1, 255])  # send bytes to pico
        rconn.set('pico_led', 'On')  # save value in redis
    elif data == b'pico_led_Off':
        # turns off led
        bincode = bytes([1, 25, 0, 255])  # send bytes to pico
        rconn.set('pico_led', 'Off')
    elif data == b'pico_led':
        # requests led status
        bincode = bytes([2, 25, 0, 255])
    elif data.startswith(b'pico_monitor_'):
        # monitor data is of the form pico_monitor_0, pico_monitor_1 etc..
        count = int(data[13:])
        # count is the number passed in the data bytes string
        bincode = bytes([3, 0, count, 255])  # send monitor request to pico
    elif data == b'pico_temperature':
        bincode = bytes([5, 4, 0, 255])  # send temperature request to pico
    elif data == b'pico_roof':
        # ask for roof status
        bincode = bytes([6, 1, 0, 255])  # send request for status for door number 0
        ser.write(bincode)
        bincode = bytes([6, 1, 1, 255])  # send request for status for door number 1
    # the following opens/closes both doors
    elif data == b'pico_roof_open':
        bincode = bytes([9, 0, 0, 255])  # send request to open both doors to pico
    elif data == b'pico_roof_close':
        bincode = bytes([9, 0, 1, 255])  # send request to close both doors to pico
    # the following allows control of individual roof doors - not used in normal operation
    elif data == b'pico_roof0_open':
        bincode = bytes([8, 0, 0, 255])  # send request to open door 0 to pico
    elif data == b'pico_roof0_close':
        bincode = bytes([8, 0, 1, 255])  # send request to close door 0 to pico
    elif data == b'pico_roof1_open':
        bincode = bytes([8, 1, 0, 255])  # send request to open door 1 to pico
    elif data == b'pico_roof1_close':
        bincode = bytes([8, 1, 1, 255])  # send request to close door1 to pico

    elif data.startswith(b'pico_set_door0_fast_duration_'):
        # value is the byte after the above string
        value = int(data[29:])
        # value is the number passed in the data bytes string
        bincode = bytes([10, 1, value, 255])  # sends request to pico
    elif data.startswith(b'pico_set_door0_duration_'):
        # value is the byte after the above string
        value = int(data[24:])
        # value is the number passed in the data bytes string
        bincode = bytes([10, 2, value, 255])  # sends request to pico
    elif data.startswith(b'pico_set_door0_max_running_time_'):
        # value is the byte after the above string
        value = int(data[32:])
        # value is the number passed in the data bytes string
        bincode = bytes([10, 3, value, 255])  # sends request to pico
    elif data.startswith(b'pico_set_door0_maximum_'):
        # value is the byte after the above string
        value = int(data[23:])
        # value is the number passed in the data bytes string
        bincode = bytes([10, 4, value, 255])  # sends request to pico
    elif data.startswith(b'pico_set_door0_minimum_'):
        # value is the byte after the above string
        value = int(data[23:])
        # value is the number passed in the data bytes string
        bincode = bytes([10, 5, value, 255])  # sends request to pico
 
    elif data.startswith(b'pico_set_door1_fast_duration_'):
        # value is the byte after the above string
        value = int(data[29:])
        # value is the number passed in the data bytes string
        bincode = bytes([11, 1, value, 255])  # sends request to pico
    elif data.startswith(b'pico_set_door1_duration_'):
        # value is the byte after the above string
        value = int(data[24:])
        # value is the number passed in the data bytes string
        bincode = bytes([11, 2, value, 255])  # sends request to pico
    elif data.startswith(b'pico_set_door1_max_running_time_'):
        # value is the byte after the above string
        value = int(data[32:])
        # value is the number passed in the data bytes string
        bincode = bytes([11, 3, value, 255])  # sends request to pico
    elif data.startswith(b'pico_set_door1_maximum_'):
        # value is the byte after the above string
        value = int(data[23:])
        # value is the number passed in the data bytes string
        bincode = bytes([11, 4, value, 255])  # sends request to pico
    elif data.startswith(b'pico_set_door1_minimum_'):
        # value is the byte after the above string
        value = int(data[23:])
        # value is the number passed in the data bytes string
        bincode = bytes([11, 5, value, 255])  # sends request to pico

    elif data.startswith(b'pico_get_door0_fast_duration'):
        bincode = bytes([12, 1, 0, 255])
    elif data.startswith(b'pico_get_door0_duration'):
        bincode = bytes([12, 2, 0, 255])
    elif data.startswith(b'pico_get_door0_max_running_time'):
        bincode = bytes([12, 3, 0, 255])
    elif data.startswith(b'pico_get_door0_maximum'):
        bincode = bytes([12, 4, 0, 255])
    elif data.startswith(b'pico_get_door0_minimum'):
        bincode = bytes([12, 5, 0, 255])

    elif data.startswith(b'pico_get_door1_fast_duration'):
        bincode = bytes([13, 1, 0, 255])
    elif data.startswith(b'pico_get_door1_duration'):
        bincode = bytes([13, 2, 0, 255])
    elif data.startswith(b'pico_get_door1_max_running_time'):
        bincode = bytes([13, 3, 0, 255])
    elif data.startswith(b'pico_get_door1_maximum'):
        bincode = bytes([13, 4, 0, 255])
    elif data.startswith(b'pico_get_door1_minimum'):
        bincode = bytes([13, 5, 0, 255])

    else:
        return
    ser.write(bincode)



if __name__ == "__main__":

    # create a redis connection
    rconn = redis.StrictRedis(host='localhost', port=6379, db=0)

    ps = rconn.pubsub(ignore_subscribe_messages=True)
    ps.psubscribe('tx_to_pico')

    # open the serial port
    ser = serial.Serial('/dev/serial0', 115200, timeout=0.2)

    # If another process wants to set the LED value, it uses redis to publish on channel 'tx_to_pico'
    # rconn.publish('tx_to_pico', 'pico_led_On')

    # two seconds or so of flashing the led to synchronize data packets
    for count in range(0,5):
        time.sleep(0.1)
        sender(b'pico_led_On', ser, rconn)
        time.sleep(0.1)
        receiver(ser, rconn)
        time.sleep(0.1)
        sender(b'pico_led_Off', ser, rconn)
        time.sleep(0.1)
        receiver(ser, rconn)


    # blocks and communicates between redis and the serial port
    while True:
        time.sleep(0.1)
        # check if anything received, and place values in redis
        receiver(ser, rconn)
        # see if anything published by redis to send
        message = ps.get_message()
        if message:
            # obtain message data payload, and send via serial port
            data = message['data']  # data is a binary string
            sender(data, ser, rconn)


