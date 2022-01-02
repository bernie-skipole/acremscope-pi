## Pi build

With the latest Raspberry Pi os, Using raspi-config:

Enable SSH,

Enable the UART for connection to a pico:

Choose Interfacing Options

Choose Serial

Select No for login shell

Select Yes for serial port hardware to be enabled.

Create the user bernard, with passwords for both the bernard and pi usernames.

apt-get update

apt-get upgrade

adduser bernard

Then log in as user bernard on the pi and generate ssh keys.

ssh-keygen -t rsa -b 4096 -C “bernie@skipole.co.uk”

The public key needs to be passed to the remscope web server, so copy the contents fro the pi file:

~/.ssh/id_rsa.pub

and set it in the web server file

~/.ssh/authorized_keys


From laptop, swap ssh keys with the pi, as user bernard on laptop:

ssh-copy-id bernard@pi-ip-address

Alternatively, your public key is in
~/.ssh/id_rsa.pub

and this can be copied to a remote file on the pi
~/.ssh/authorized_keys

Back on pi, as user pi (bernard does not have sudo access)

sudo /bin/bash

Install redis, indi-bin and mosquitto

apt-get install redis-server

apt-get install mosquitto

apt-get install indi-bin

redis-server --version

gives version 6.xxxxx

apt-get install python3-venv

apt-get install git

login as bernard

Create a virtual environment

python3 -m venv /home/bernard/acenv

and activate

source acenv/bin/activate

pip install -U pip setuptools wheel

install python dependencies

pip install pyserial

pip install indiredis

this also pulls in packages skipole, waitress, redis, paho-mqtt, indi-mr

Create a folder ~/indiblobs

From home folder ~ clone files from this repository

git clone https://github.com/bernie-skipole/acremscope-pi.git

This creates directory ~/acremscope-pi with the repository files beneath it

The following services now need to be copied:

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










