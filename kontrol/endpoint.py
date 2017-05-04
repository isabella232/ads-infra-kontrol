import json
import logging
import kontrol
import os
import sys
import urllib3
import signal

from flask import Flask, request
from logging import DEBUG
from logging.config import fileConfig
from kontrol.fsm import MSG, diagnostic, shutdown
from kontrol.script import Actor as Script
from kontrol.callback import Actor as Callback
from kontrol.keepalive import Actor as KeepAlive
from kontrol.leader import Actor as Leader
from kontrol.sequence import Actor as Sequence
from os.path import dirname
from pykka import ThreadingFuture, Timeout


#: our ochopod logger
logger = logging.getLogger('kontrol')

#: our flask endpoint (fronted by gunicorn)
http = Flask('kontrol')


@http.route('/down', methods=['POST'])
def _down():

    #
    # - special request terminating all the actors
    # - this is triggered during shutdown by kontrol.sh
    #
    # @todo make sure the request only comes from localhost
    #
    try:
        for key, actor in kontrol.actors.items():
            logger.debug('terminating actor <%s>' % key)
            shutdown(actor)

        logger.warning('all actors now terminated, endpoint is idle')
        return '', 200

    except Exception:
        return '', 500


@http.route('/ping', methods=['PUT'])
def _ping():

    #
    # - PUT /ping (e.g keepalive updates from supervised containers)
    # - post to the sequence actor (please note this of course will only
    #   work in master mode)
    #
    try:
        js = request.get_json(silent=True, force=True)
        logger.debug('PUT /ping <- keepalive from %s' % js['ip'] )
        kontrol.actors['sequence'].tell({'request': 'update', 'state': js})
        return '', 200

    except Exception:
        return '', 500

@http.route('/state', methods=['GET'])
def _state():

    #
    # - GET /state (e.g retrieves the cluster state)
    # - simply ask the callback actor (this will only work in master mode)
    #
    try:
        logger.debug('GET /state')      
        return kontrol.actors['callback'].ask({'request': 'state'}), 200

    except Exception:
        return '', 500


@http.route('/script', methods=['PUT'])
def _script():

    #
    # - PUT /script (e.g script evaluation request from the controller)
    # - post it to the script actor (this will only work in slave mode)
    # - we block on a latch that is released at some point by the
    #   the actor
    #
    try:
        js = request.get_json(silent=True, force=True)

        msg = MSG({'request': 'invoke'})
        msg.cmd = js['cmd']
        msg.env = {'INPUT': json.dumps(js)}
        msg.latch = ThreadingFuture()     

        #
        # - block on a latch and reply with whatever the shell script
        #   wrote to its standard output
        #
        logger.debug('PUT /script <- invoking "%s"' % msg.cmd)
        kontrol.actors['script'].tell(msg)
        return msg.latch.get(timeout=60), 200
        
    except Exception as e:
        return '', 500

def up():

    """
    Entry point for the gunicorn worker. This will parse the environment
    variables and boot all the required actors.
    """
    
    #
    # - disable the default 3 retries that urllib3 enforces
    # - that causes the etcd watch to potentially wait 3X
    #
    from urllib3.util import Retry
    urllib3.util.retry.Retry.DEFAULT = Retry(1)

    #
    # - load our logging configuration from the local log.cfg resource
    # - make sure to disable any existing logger otherwise urllib3 will flood us
    #
    fileConfig('%s/log.cfg' % dirname(__file__), disable_existing_loggers=True)
    try:

        def _try(key):
            value = os.environ[key]
            try:
                return json.loads(value)
            except ValueError:
                return value

        #
        # - grep the env. variables we need
        # - anything prefixed by KONTROL_ will be kept around
        # - $KONTROL_MODE is a comma separated list of tokens used to define
        #   the operation mode (e.g slave,debug)
        #
        stubs = []
        keys = [key for key in os.environ if key.startswith('KONTROL_')]            
        js = {key[8:].lower():_try(key) for key in keys}
        [logger.info(' - $%s -> %s' % (key, os.environ[key])) for key in keys]
        assert all(key in js for key in ['id', 'etcd', 'ip', 'labels', 'mode', 'damper', 'ttl', 'fover']), '1+ environment variables missing'
        tokens = set(js['mode'].split(','))
        assert all(key in ['slave', 'master', 'debug', 'verbose'] for key in tokens), 'invalid $KONTROL_MODE value'

        #
        # - if $KONTROL_MODE contains "debug" switch the debug/local mode on
        # - this will force etcd and the local http/rest endpoint to be either
        #   127.0.0.1 or whateer $KONTROL_HOST is set at
        # - if you want to test drive your container locally alias lo0 to some
        #   ip (e.g sudo ifconfig lo0 alias <ip>)
        # - then docker run as follow:
        #     docker run -e KONTROL_MODE=verbose,debug -e KONTROL_HOST=<ip> -p 8000:8000 <image>
        #
        if 'verbose' in tokens:
            logger.setLevel(DEBUG)

        if 'debug' in tokens:
            tokens |= set(['master', 'slave'])
            ip = js['host'] if 'host' in js else '127.0.0.1'
            logger.debug('switching debug mode on (host ip @ %s)' % ip)
            overrides = \
            {
                'etcd': ip,
                'ip': ip,
                'id': 'local',
                'labels': {'app':'test', 'role': 'test', 'master': ip}
            }
            js.update(overrides)
        
        #
        # - slave mode just requires the KeepAlive and Script actors
        #
        if 'slave' in tokens:
            stubs += [KeepAlive, Script]
        
        #
        # - master mode requires the Callback, Leader and Sequence actors
        #
        if 'master' in tokens:
            stubs += [Callback, Leader, Sequence]

        #
        # - start our various actors
        # - we rely on the "app" label to identify the pod
        # - the "role" label is also used when sending keepalive updates
        #
        assert all(key in js['labels'] for key in ['app', 'role']), '1+ labels missing'
        for stub in stubs:
            logger.debug('starting actor <%s>' % stub.tag)
            kontrol.actors[stub.tag] = stub.start(js)
    
    except Exception as failure:

        #
        # - bad, probably some missing environment variables
        # - abort the worker
        #
        why = diagnostic(failure)
        logger.error('top level failure -> %s' % why)
    