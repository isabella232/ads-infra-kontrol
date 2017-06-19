import etcd
import json
import logging
import os
import time
import statsd

from collections import deque
from etcd import EtcdKeyNotFound
from kontrol.fsm import Aborted, FSM
from subprocess import Popen, PIPE, STDOUT
from threading import Thread


#: our ochopod logger
logger = logging.getLogger('kontrol')


class Actor(FSM):

    """
    State machine responsible for running the update callback (e.g whenever the observed
    MD5 digest changes). Please note this should only ever be scheduled on one single
    pod at any given time (e.g on the leading pod).
    """

    tag = 'callback'

    def __init__(self, cfg):
        super(Actor, self).__init__()

        self.cfg = cfg
        self.client = etcd.Client(host=cfg['etcd'], port=2379)
        self.fifo = deque()
        self.path = '%s actor' % self.tag
        self.statsd = statsd.StatsClient('127.0.0.1', 8125)
        self.data.left = None

    def reset(self, data):

        if self.terminate:
            super(Actor, self).reset(data)

        return 'initial', data, 0.0

    def initial(self, data):
                
        if self.terminate and not self.fifo:
            raise Aborted('resetting')

        #
        # - just spin if there is nothing to invoke
        # - if we have buffered 1+ requests just peek at the latest one
        #   and cycle back if its ttl has not been exceeded (e.g it's too
        #   early to execute the script)
        #
        now = time.time()
        if not self.fifo:
            return 'initial', data, 0.25
            
        lapse = self.fifo[-1].ttl - now
        if lapse > 0:
            left = int(lapse)
            if left != data.left:
                data.left = left
                logger.debug('%s : callback invokation in %d seconds' % (self.path, left))
            
            return 'initial', data, 0.25

        #
        # - it's time to run the script
        # - consider the latest request we received
        #
        data.left = None
        msg = self.fifo[-1]
        try:
            raw = self.client.read('%s/state' % self.cfg['prefix']).value
            if raw:
                msg.env['STATE'] = raw
        except EtcdKeyNotFound:
            pass

        #
        # - override with the current environment
        # - spawn the subprocess
        #
        msg.env.update(os.environ)
        try:
            data.tick = now
            data.pid = Popen(msg.cmd.split(' '),
            shell=True,
            close_fds=True,
            bufsize=0,
            env=msg.env,
            stderr=PIPE,
            stdout=PIPE)
    
        except OSError:
            logger.warning('%s : script "%s" could not be found (config bug ?)' % (self.path, msg.cmd))   
            self.fifo.popleft()
            return 'initial', data, 0.0

        self.statsd.incr('callback_invoked,tier=kontrol')
        logger.debug('%s : invoking script "%s" (pid %s)' % (self.path, msg.cmd, data.pid.pid))
        return 'wait_for_completion', data, 0.25

    def wait_for_completion(self, data):

        #
        # - stop spinning once the process has exited
        # - both stderr and stdout are piped
        #
        if data.pid.poll() is not None:
            code = data.pid.returncode
            stdout = [line.rstrip('\n') for line in iter(data.pid.stdout.readline, b'')]
            stderr = [line.rstrip('\n') for line in iter(data.pid.stderr.readline, b'')]
            lapse = time.time() - data.tick
            logger.info('%s: callback took %2.1f s (pid %s, exit %d)' % (self.path, lapse, data.pid.pid, code))
            if stderr:
                logger.debug('%s : stderr (pid %s) -> \n  . %s' % (self.path, data.pid.pid, '\n  . '.join(stderr)))
            
            #
            # - attempt to parse stdout into a json object
            #
            try:
                self.client.write('%s/state' % self.cfg['prefix'], ''.join(stdout))
            except ValueError:
                logger.warning('%s : unable to parse stdout into json (script error ?)' % self.path)

            #
            # - cleanup the FIFO (e.g drop all buffered requests)
            # - go back to the initial state
            #
            data.pid = None
            self.fifo.clear()
            return 'initial', data, 0

        return 'wait_for_completion', data, 0.25
    

    def specialized(self, msg):
        assert 'request' in msg, 'bogus message received ?'
        req = msg['request']
        if req == 'invoke':

            #
            # - buffer the incoming script in our fifo
            # - we'll dequeue it upon the next spin
            #
            self.fifo.append(msg)

        elif req == 'state':

            #
            # - request from GET /state
            # - simply read the state from etcd and return it
            #
            try:
                return self.client.read('%s/state' % self.cfg['prefix']).value

            except EtcdKeyNotFound:
                pass
       
        else:
            super(Actor, self).specialized(msg)
        