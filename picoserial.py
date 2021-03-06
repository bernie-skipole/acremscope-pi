

"""picoserial.py

Requires the package pyserial
"""

import os, sys, collections, serial, time, threading

from datetime import datetime

# serial port transmit que
TX_DATA = collections.deque(maxlen=16)


# state variables

# LED is True, or False, for On or Off
LED = False


def sender(ser):
    "Serial port sender - send anything in the TX_DATA deque"
    global TX_DATA
    while True:
        if not TX_DATA:
            time.sleep(0.1)
            continue
        # get code to send from the TX_DATA deque, and add byte 255 as a terminator
        data = list(TX_DATA.popleft()) + [255]
        bincode = bytes(data)
        ser.write(bincode)


def receiver(ser):
    "Serial port receiver - sets global state variables"
    global LED
    while True:
        returnval = ser.read(10) # only four should arrive
        if not returnval:
            continue
        # discard received data until synchronised, at which points data
        # should come as four bytes at a time
        if len(returnval) != 4:
            continue
        if returnval[3] != 255:
            continue
        # parse the data
        if returnval[0] == 1:
            # LED code
            if (returnval[1] == 25) and (returnval[2] == 0):
                LED = False
            if (returnval[1] == 25) and (returnval[2] == 1):
                LED = True



# start threads controlling the serial port
ser = serial.Serial('/dev/serial0', 115200, timeout=0.2)
worker1 = threading.Thread(target=sender, args=(ser,))
worker1.start()
worker2 = threading.Thread(target=receiver, args=(ser,))
worker2.start()


def set_led(state):
    "if state is True, turn on the LED, False, turn it off"
    global LED
    # code:  1
    # pin:  25
    # state: 1 or 0
    if state:
        TX_DATA.append((1, 25, 1))
        LED = True
    else:
        TX_DATA.append((1, 25, 0))
        LED = False

def get_led():
    "Return the value of the LED, True or False"
    global LED
    return LED

