#!/home/bernard/acenv/bin/python3

import os, threading, sys, hashlib, uuid, pathlib

from indi_mr import mqtttoredis, mqtt_server, redis_server, tools

from indiredis import make_wsgi_app

from skipole import WSGIApplication, FailPage, GoTo, ValidateError, ServerError, use_submit_list, skis

from waitress import serve

mqtt_host = mqtt_server(host='localhost', port=1883)
redis_host = redis_server(host='localhost', port=6379)

# This service needs a redis connection to store cookies
rconn = tools.open_redis(redis_host)

PROJ_DATA={"rconn":rconn,                                 # redis connection
           "username":"localcontrol",                         # the username which must be used to log in
           "password": "6f852ab4bb9e13ac5095377eddb251a09afd27dbb95c788e075ca63860f9ce8cac75fa9165bb739c0e629f2be201ddf57f261ab982cfd7f88687412ff0d1ea64"
           }


# The password above is an hashed password, being the result of running
# python3 hashpassword.py, and copying the result here, currently password is 'remscope'


# Set a directory of your choice where blobs will be stored
BLOBS = '/home/bernard/indiblobs'


PROJECTFILES = os.path.dirname(os.path.realpath(__file__))
PROJECT = "indiclient"


def _is_user_logged_in(skicall):
    received_cookies = skicall.received_cookies
    if PROJECT not in received_cookies:
        return False
    # get cookie
    rconn = skicall.proj_data["rconn"]
    # the current cookiestring is stored in redis at key 'cookiestring'
    cookievalue = rconn.get('cookiestring')
    if not cookievalue:
        return False
    cookiestring = cookievalue.decode('utf-8')
    if received_cookies[PROJECT] != cookiestring:
        return False
    return True


def _hash_password(username, password):
    "Return hashed password, as a string, on failure return None"
    seed_password = username +  password
    hashed_password = hashlib.sha512(   seed_password.encode('utf-8')  ).hexdigest()
    return hashed_password

def _create_cookie(skicall):
    "Generates a random cookie, store it in redis, and return the cookie"
    rconn = skicall.proj_data["rconn"]
    # generate a cookie string
    cookiestring = uuid.uuid4().hex
    rconn.set('cookiestring', cookiestring, ex=3600) # expire after one hour
    return cookiestring


def start_call(called_ident, skicall):
    "When a call is initially received this function is called."
    # to serve static files, you can map a url to a server static directory
    # the user does not have to be logged in to access these
    servedfile = skicall.map_url_to_server("images", "/home/bernard/indiblobs")
    if servedfile:
        return servedfile


    if _is_user_logged_in(skicall):
        # The user is logged in, so do not show the index page, or check login page
        if (called_ident == (PROJECT, 1)) or (called_ident == (PROJECT, 10)):
            # instead jump straight to indi client
            return ('indiredis', 1)

    # any other page, such as css or image files are ok
    return called_ident

            


# You may wish to apply the decorator '@use_submit_list' to the submit_data
# function below. See the skipole documentation for details.

def submit_data(skicall):
    "This function is called when a Responder wishes to submit data for processing in some manner"
    if skicall.ident_list[-1] == (PROJECT, 10):
        # this call is to checklogin from the login page
        skicall.call_data['authenticate'] = False
        username = skicall.proj_data["username"]
        if (("login", "input_text1") in skicall.call_data) and (skicall.call_data["login", "input_text1"] == username):
            if ("login", "input_text2") in skicall.call_data:
                password = skicall.call_data["login", "input_text2"]
                hashed = _hash_password(username, password)
                if hashed == skicall.proj_data["password"]:
                    skicall.call_data['authenticate'] = True
        if skicall.call_data['authenticate']:
            return
        else:
            raise FailPage("Invalid input")
    if skicall.ident_list[-1] == (PROJECT, 20):
        # this call is to populate the showfiles page
        serverpath = pathlib.Path(BLOBS)
        serverfiles = [f.name for f in serverpath.iterdir() if f.is_file()]
        if not serverfiles:
            skicall.page_data['nothingfound', 'show'] = True
            skicall.page_data['filelinks', 'show'] = False
            return
        skicall.page_data['nothingfound', 'show'] = False
        skicall.page_data['filelinks', 'show'] = True

        # The widget has links formed from a list of lists
        # 0 : The url, label or ident of the target page of the link
        # 1 : The displayed text of the link
        # 2 : If True, ident is appended to link even if there is no get field
        # 3 : The get field data to send with the link

        serverfiles.sort(reverse=True)
        filelinks = []
        for sf in serverfiles:
            # create a link to urlfolder/sf
            filelinks.append([ "images/" + sf, sf, False, ""])
        skicall.page_data['filelinks', 'nav_links'] = filelinks
        return
    if skicall.ident_list[-1] == (PROJECT, 30):
        # this call is to log out
        skicall.call_data['logout'] = True
    return



def end_call(page_ident, page_type, skicall):
    """This function is called at the end of a call prior to filling the returned page with skicall.page_data,
       it can also return an optional session cookie string."""
    if ('authenticate' in skicall.call_data) and skicall.call_data['authenticate']:
        # a user has logged in, set a cookie
        return _create_cookie(skicall)
    if ('logout' in skicall.call_data) and skicall.call_data['logout']:
        # a user has been logged out, set a new random cookie in redis, and an invalid cookie in the client
        _create_cookie(skicall)
        return "xxxxxxxx"
    return




def check_cookies_function(received_cookies, proj_data):
    """Returns None if call can proceed to sub project"""
    if PROJECT not in received_cookies:
        # no cookie, must go to top login page
        return (PROJECT, 1)
    # get cookie
    rconn = proj_data["rconn"]
    # the current cookiestring is stored in redis at key 'cookiestring'
    cookievalue = rconn.get('cookiestring')
    if not cookievalue:
        return (PROJECT, 1)
    cookiestring = cookievalue.decode('utf-8')
    if received_cookies[PROJECT] != cookiestring:
        # invalid cookie, return to top page
        return (PROJECT, 1)
    return


# The above functions are required as arguments to the skipole.WSGIApplication object
# and will be called as required.

# create the wsgi application
application = WSGIApplication(project=PROJECT,
                              projectfiles=PROJECTFILES,
                              proj_data=PROJ_DATA,
                              start_call=start_call,
                              submit_data=submit_data,
                              end_call=end_call,
                              url="/")



skis_application = skis.makeapp()
application.add_project(skis_application, url='/lib')

indi_application = make_wsgi_app(redis_host, blob_folder=BLOBS)
application.add_project(indi_application, url='/indi', check_cookies=check_cookies_function) 


from skipole import skiadmin, set_debug
set_debug(True)
skiadmin_application = skiadmin.makeapp(editedprojname=PROJECT)
application.add_project(skiadmin_application, url='/skiadmin')

# serve the application with the python waitress web server in its own thread
webapp = threading.Thread(target=serve, args=(application,), kwargs={'host':'0.0.0.0', 'port':8000})
# and start it
webapp.start()

# and start mqtttoredis
mqtttoredis('indi_localclient', mqtt_host, redis_host, blob_folder=BLOBS)



