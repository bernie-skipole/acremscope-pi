## Pi build

With the latest Raspberry Pi os, create the user bernard, with passwords for both the bernard and pi usernames.

adduser bernard

Enable ssh, and generate ssh keys.

ssh-keygen -t rsa -b 4096 -C “bernie@skipole.co.uk”

From laptop, swap ssh keys with the pi, as user bernard:

ssh-copy-id bernard@pi_ip_address

Alternatively, your public key is in
~/.ssh/id_rsa.pub

and this can be copied to a remote file
~/.ssh/authorized_keys

as root - from user pi

sudo /bin/bash

Install redis, indi-bin and mosquitto

apt-get install redis

apt-get install mosquitto

apt-get install indi-bin

as bernard

Create a virtual environment

python3 -m venv /home/bernard/acenv

and activate

source acenv/bin/activate

install python dependencies

pip install indiredis

this also pulls in packages skipole, waitress, redis, paho-mqtt, indi-mr

pip install pyserial

Create a folder ~/indiblobs

Create a folder ~/indi and copy files from this repository into it , and install the services

mqtttunnel.service

remoteaccess.service

remscopedrivers.service

indiclient.service

picoserial.service

For example

As root, set mqtttunnel.service into the system directory as:

/lib/systemd/system/mqtttunnel.service

Enable the service with the following commands:

systemctl daemon-reload

systemctl enable mqtttunnel.service

systemctl start mqtttunnel

Repeat for each service.

mqtttunnel creates a remote ssh tunnel to webparametrics.co.uk allowing acremscope to access the mqtt server running on the pi

remoteaccess creates a remote ssh tunnel to webparametrics.co.uk allowing ssh access to the pi

remscopedrivers runs remscopedrivers.py which calls indi_mr.driverstomqtt to run the indi drivers and communicates to the mqtt server

indiclient runs a web based, password protected indi client on port 8000, for local control of the telescope.










