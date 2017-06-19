import argparse
import gevent
import json
import logging
import os
import signal
import sys
import urllib3
import zerorpc

from collections import OrderedDict
from gevent.queue import Queue
from logging import DEBUG
from logging.config import fileConfig
from kontrol.fsm import MSG, diagnostic, shutdown
from kontrol.lru import LRU
from os.path import dirname
from pykka import ThreadingFuture, Timeout
from signal import signal, SIGINT, SIGTERM


#: set of shared actors implementing various state-machines (as a ordered dict)
actors = OrderedDict()

#: Our automaton logger.
logger = logging.getLogger('kontrol')

#: gevent queue for outgoing RPC requests
outgoing = Queue()


class API(object):

    """
    RPC front-end API with two requests: ping() and execute(). The startup logic with all
    the actor setup is done in the ctor.
    """

    def __init__(self):

        #
        # - disable the default 3 retries that urllib3 enforces
        # - that causes the etcd watch to potentially wait 3X
        #
        from urllib3.util import Retry
        urllib3.util.retry.Retry.DEFAULT = Retry(1)
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
        assert all(key in js for key in ['id', 'etcd', 'ip', 'labels', 'annotations', 'mode', 'damper', 'ttl', 'fover']), '1+ environment variables missing'
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
                'labels': {'app':'test', 'role': 'test'},
                'annotations': {'kontrol.unity3d.com/master': '%s' % ip}
            }
            js.update(overrides)

        from kontrol.script import Actor as Script
        from kontrol.callback import Actor as Callback
        from kontrol.keepalive import Actor as KeepAlive
        from kontrol.leader import Actor as Leader
        from kontrol.sequence import Actor as Sequence
        
        #
        # - slave mode just requires the KeepAlive and Script actors
        # - split the comma separated list of masters
        # - turn each into a KeepAlive actor
        # - don't forget to add the Script actor as well
        #
        if 'slave' in tokens:
            assert 'kontrol.unity3d.com/master' in js['annotations'], 'invalid annotations: "kontrol.unity3d.com/master" missing (bug?)'
            masters = js['annotations']['kontrol.unity3d.com/master'].split(',')
            stubs += [(KeepAlive, token) for token in masters] + [Script]

        #
        # - master mode requires the Callback, Leader and Sequence actors
        #
        if 'master' in tokens:
            stubs += [Leader, Sequence, Callback]

        #
        # - start our various actors
        # - we rely on the "app" label to identify the pod
        # - the etcd prefix for all our keys is /kontrol/<namespace>/<app>
        # - the "role" label is also used when sending keepalive updates
        # - please note the dict is ordered and the actors will be shutdown in the same order 
        #
        assert 'NAMESPACE' in os.environ, '$NAMESPACE undefined (bug ?)'
        assert all(key in js['labels'] for key in ['app', 'role']), '1+ labels missing'
        js['prefix'] = '/kontrol/%s/%s' % (os.environ['NAMESPACE'], js['labels']['app'])
        for stub in stubs:
            if type(stub) is tuple:

                #
                # - if we have a (class, arg, ...) tuple pass the extra
                #   arguments during the call to start()
                #
                actor, tag =  stub[0].start(js, *stub[1:]), stub[0].tag
            else:
                actor, tag = stub.start(js), stub.tag
            
            logger.debug('starting actor <%s>' % tag)
            actors[tag] = actor

    def ping(self, raw):

        """
        RPC API: keepalive from a slave. This request does not return anything.

        :type raw: str
        :param raw: serialized json payload
        :rtype: None
        """

        try:
            js = json.loads(raw)
            logger.debug('RPC ping() <- %s [%s]' % (js['ip'], js['app']))
            actors['sequence'].tell({'request': 'update', 'state': js})

        except Exception:
            pass

    def invoke(self, raw):

        """
        RPC API: shell invokation on behalf of the master. The code is run by
        the script actor and its stdout returned back to the caller.

        :type raw: str
        :param raw: serialized json payload
        :rtype: the shell script stdout upon succes, None upon failure
        """

        try:
            js = json.loads(raw)

            logger.debug('RPC invoke() <- "%s"' % js['cmd'])
            msg = MSG({'request': 'invoke'})
            msg.cmd = js['cmd']
            msg.env = {'INPUT': json.dumps(js)}
            msg.latch = ThreadingFuture()     

            #
            # - block on a latch and reply with whatever the shell script
            #   wrote to its standard output
            #
            actors['script'].tell(msg)
            return msg.latch.get(timeout=60)
            
        except Exception as failure:
            return None

def go():

    """
    Entry point for the front-facing kontrol script.
    """
    parser = argparse.ArgumentParser(description='kontrol', prefix_chars='-')
    parser.add_argument('-d', '--debug', action='store_true', help='debug logging on')
    args = parser.parse_args()

    #
    # - load our logging configuration from the local log.cfg resource
    # - make sure to disable any existing logger otherwise urllib3 will flood us
    # - set to DEBUG if required
    #
    fileConfig('%s/log.cfg' % dirname(__file__), disable_existing_loggers=True)
    if args.debug:
        logger.setLevel(DEBUG)
    
    def _handler(id, _):

        #
        # - shutdown all actors in sequence
        # - exit
        #
        for key, actor in actors.items():
            logger.debug('terminating actor <%s>' % key)
            shutdown(actor)

        logger.warning('all actors now terminated, exiting')
        sys.exit(1)

    signal(SIGINT, _handler)
    signal(SIGTERM, _handler)
    try:

        #
        # - start the RPC server
        # - bind on TCP 8000
        # - use a separate greenlet to use the RPC client (otherwise you assert
        #   all over the place)
        # - the only entity to emit RPC requests from within the kontrol process is
        #   the keepalive actor
        #
        assert 'KONTROL_PORT' in os.environ, '$KONTROL_PORT undefined (configuration error ?)'
        port = int(os.environ['KONTROL_PORT'])
        server = zerorpc.Server(API())
        server.bind('tcp://0.0.0.0:%d' % port)
        def _piper():
            lru = LRU(evicted=lambda client: client.close())
            while 1:
                host, js = outgoing.get()
                try:

                    #
                    # - use a simple LRU cache with eviction to manage
                    #   the RPC clients
                    #
                    client = lru[host]
                    if not client:
                        client = zerorpc.Client()
                        client.connect('tcp://%s:%d' % (host, port))
                        lru[host] = client
                        
                    client.ping(js)

                except Exception as failure:
                    logger.error('RPC : unable to ping() @ %s' % host)

        #
        # - start the server and piper as greenlets
        #
        threads = [gevent.spawn(func) for func in [server.run, _piper]]
        gevent.joinall(threads)

    except KeyboardInterrupt:
        pass

    except Exception as failure:
        print 'unexpected failure -> %s' % diagnostic(failure)
    
    sys.exit(0)