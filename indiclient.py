import threading, sys

from indi_mr import mqtttoredis, mqtt_server, redis_server

from indiredis import make_wsgi_app

# any wsgi web server can serve the wsgi application produced by
# make_wsgi_app, in this example the web server 'waitress' is used

from waitress import serve

mqtt_host = mqtt_server(host='localhost', port=1883)
redis_host = redis_server(host='localhost', port=6379)

# Set a directory of your choice where blobs will be stored
BLOBS = '/home/bernard/indiblobs'

# create a wsgi application
application = make_wsgi_app(redis_host, blob_folder=BLOBS)
if application is None:
    print("ERROR:Are you sure the skipole framework is installed?")
    sys.exit(1)

# serve the application with the python waitress web server in its own thread
webapp = threading.Thread(target=serve, args=(application,), kwargs={'host':'0.0.0.0', 'port':8000})
# and start it
webapp.start()

# and start mqtttoredis
mqtttoredis('indi_localclient', mqtt_host, redis_host, blob_folder=BLOBS)

