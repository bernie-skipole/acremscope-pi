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
   


def sender(data, ser, rconn):
    "Sends data via the serial port"
    bincode = None
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


    elif data.startswith(b'pico_door0_pwm_'):
        # door pwm data of the form pico_door0_pwm_XX
        pwm = int(data[15:])
        bincode = bytes([16, 0, pwm, 255])  # send pwm to pico
    elif data.startswith(b'pico_door1_pwm_'):
        # door pwm data of the form pico_door1_pwm_XX
        pwm = int(data[15:])
        bincode = bytes([16, 1, pwm, 255])  # send pwm to pico

    elif data == b'pico_door0_direction_1':
        # door pwm direction
        bincode = bytes([17, 0, 1, 255])  # send direction to pico
    elif data == b'pico_door0_direction_0':
        # door pwm direction
        bincode = bytes([17, 0, 0, 255])  # send direction to pico
    elif data == b'pico_door1_direction_1':
        # door pwm direction
        bincode = bytes([17, 1, 1, 255])  # send direction to pico
    elif data == b'pico_door1_direction_0':
        # door pwm direction
        bincode = bytes([17, 1, 0, 255])  # send direction to pico

    else:
        return
    if bincode:
        ser.write(bincode)



if __name__ == "__main__":

    # create a redis connection
    rconn = redis.Redis(host='localhost', port=6379, db=0)

    ps = rconn.pubsub(ignore_subscribe_messages=True)
    ps.psubscribe('tx_to_pico')

    # open the serial port
    ser = serial.Serial('/dev/serial0', 115200, timeout=0.2)

    # If another process wants to set the LED value, it uses redis to publish on channel 'tx_to_pico'
    # rconn.publish('tx_to_pico', 'pico_led_On')

    print("Serial port /dev/serial0 opened at 115200 bit rate")

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


