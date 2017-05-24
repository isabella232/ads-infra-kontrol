import api
import jsonschema
import fnmatch
import json
import logging
import os
import signal
import sys
import time
import yaml

from collections import deque
from kontrol.fsm import Aborted, diagnostic, FSM, MSG
from os.path import abspath
from os import getpgid, killpg, path
from subprocess import Popen, PIPE, STDOUT


#: our ochopod logger
logger = logging.getLogger('automaton')

#: imported module (only used when using a python script as input) 
module = None

#: The YAML manifest schema
schema = \
"""
type: object
properties:
    initial:
        type: string
    terminal:
        type: string
    states:
        type: array
        items:
            type: object
            additionalProperties: false
            required:
                - tag
                - shell
            properties:
                tag:
                    type: string
                shell:
                    type: string
                next:
                    type: array
                    items:
                        type: string
"""

class Actor(FSM):

    """
    Actor emulating a simple state machine that runs shell scripts and can
    be tripped at any time to a desired state. The states and transitions are
    describes in the YAML manifest.

    Tripping the machine while its shell script is still running will cause it
    to be killed. Transition requests are buffered and processed in order.
    """

    tag = 'machine'

    def __init__(self, args):
        super(Actor, self).__init__()

        #
        # - if the argument is ending with .py try to import it
        # - the code will be turned into a valid YAML manifest whose states
        #   import & invoke individual functions 
        #
        self.path = '%s actor' % self.tag
        if args.input.endswith('.py'):

            global module
            assert path.isfile(args.input), '%s is not a file' % args.input
            absolute = path.abspath(args.input)
            sys.path.insert(0, path.dirname(absolute))
            module = path.basename(absolute[:-3])
            __import__(module)
            manifest = api.raw
            logger.debug('%s : translated %s to YAML' % (self.path, args.input))

        else:

            with open(args.input, 'r') as f:
                manifest = f.read()

        #
        # - load the YAML manifest
        # - validate against our schema
        #
        try:
            cfg = MSG(yaml.load(manifest))            
            jsonschema.validate(cfg, yaml.load(schema))
            self.cfg = cfg

        except jsonschema.ValidationError:
            print 'invalid YAML manifest syntax'
            raise

        except yaml.YAMLError:
            print 'cannot load the YAML manifest'
            raise

        #
        # - set the current state to 'idle' and let it transition to anything
        #
        self.cfg.args = args
        self.cur = {'tag': 'idle', 'shell': '', 'next': ['*']}
        self.env = os.environ
        self.fifo = deque()
        self.states = {js['tag']:js for js in self.cfg['states']}

        #
        # - transition to the initial state
        #
        msg = MSG()
        msg.cnx = None
        msg.state = self.cfg['initial']
        msg.extra = ''
        msg.wait = False
        msg.tick = time.time()
        self.fifo.append(msg)

    def reset(self, data):
       
        if self.terminate:
            super(Actor, self).reset(data)

        logger.warning('%s : uncaught exception -> %s' % (self.path, data.diagnostic))
        return 'initial', data, 0.0

    def initial(self, data):
        
        if self.terminate and not self.fifo:
            raise Aborted('resetting')

        while self.fifo:

            #
            # - peek at the next transition in our FIFO
            # - always add the terminal state as a valid transition
            # - make sure it is valid
            # - proceed with the first one matching the pattern
            #
            msg = self.fifo[0]
            try:

                assert msg.state in self.states, 'unknown state "%s"' % msg.state
                allowed = [] 
                if self.cur['tag'] != self.cfg['terminal']:
                    allowed += self.cur['next'] if 'next' in self.cur else []
                    allowed.append(self.cfg['terminal'])

                logger.debug('%s : %s can go to %s' % (self.path, self.cur['tag'], ', '.join(allowed)))
                for pattern in allowed:
                    if fnmatch.fnmatch(msg.state, pattern):
                
                        #
                        # - the transition is valid
                        # - switch the state
                        #
                        logger.info('%s : %s -> %s' % (self.path, self.cur['tag'], msg.state))
                        self.cur = self.states[msg.state] 

                        #
                        # - invoke the shell snippet
                        # - then spin and check on its status
                        # - $SOCKET is the absolute filepath of our UNIX socket
                        # - $INPUT is optional and set to whatever was specified in the GOTO
                        #   request
                        #
                        self.env.update(
                        {
                            'SOCKET': abspath(self.cfg.args.socket),
                            'INPUT': msg.extra
                        })

                        data.tick = time.time()
                        data.pid = Popen(self.cur['shell'],
                        close_fds=True,
                        bufsize=0,
                        shell=True,
                        env=self.env,
                        preexec_fn=os.setsid,
                        stderr=STDOUT,
                        stdout=PIPE)
                        logger.debug('%s : invoking script (pid %s)' % (self.path, data.pid.pid))

                        #
                        # - if we are not blocking send the 'OK' ack immediately
                        # - close the socket
                        #
                        if not msg.wait:
                            self._ack(msg, 'OK')

                        return 'wait_for_completion', data, 0.25

                logger.warning('%s : %s -> %s is not allowed, skipping' % (self.path, self.cur['tag'], msg.state))

            except Exception as failure:
                
                logger.warning('%s : %s' % (self.path, failure))

            #
            # - we failed to transition for whatever reason
            # - pop the FIFO
            # - send back the 'KO' ack to signal the failure
            # 
            self.fifo.popleft()
            if msg.cnx:
                self._ack(msg, 'KO')
            
        return 'initial', data, 0.25

    def wait_for_completion(self, data):

        #
        # - check if the subprocess is done or not
        #
        now = time.time()
        complete = data.pid.poll() is not None

        #
        # - the process either completed or we have buffered state transitions
        #   in our FIFO
        # - pop the FIFO and cycle back to the initial state
        # - if transitions are buffered forcelly terminate the running script
        # - make sure to add a little damper otherwise any shell script that tries to
        #   socat to the machine would kill itself
        # - display the process standard outputs
        #
        if not complete and len(self.fifo) > 1 and (now - self.fifo[1].tick) > 1.0:
            logger.debug('%s : killing pid %s (fifo -> #%d items)' % (self.path, data.pid.pid, len(self.fifo)))

            #
            # - use killpg to kill the whole sub-progress group
            # - simply using the popen kill() method won't work
            #
            killpg(getpgid(data.pid.pid), signal.SIGTERM)
            complete = True

        if complete:
            lapse = now - data.tick
            code = data.pid.returncode
            stdout = [line.rstrip('\n') for line in iter(data.pid.stdout.readline, b'')]
            logger.debug('%s : script took %2.1f s (pid %s, exit %s)' % (self.path, lapse, data.pid.pid, code if code is not None else '_'))
            if stdout:
                logger.debug('%s : pid %s -> \n  . %s' % (self.path, data.pid.pid, '\n  . '.join(stdout)))

            #
            # - if blocking send back the 'OK' ack
            # - close the socket
            #
            msg = self.fifo[0]
            if msg.wait:
                self._ack(msg, 'OK')

            data.pid = None
            self.fifo.popleft()
            return 'initial', data, 0
        
        return 'wait_for_completion', data, 0.25

    def specialized(self, msg):
        assert 'request' in msg, 'bogus message received ?'
        req = msg['request']
        if req == 'cmd':

            #
            # - parse the incoming command
            # - right now we support WAIT, GOTO, SET, STATE and DIE
            #
            try:
                tokens = msg['raw'].split(' ')
                assert tokens[0] in ['STATE', 'GOTO', 'WAIT', 'SET', 'DIE'], 'invalid command'
                if tokens[0] == 'STATE':
                    self._ack(msg, self.cur['tag'])

                elif tokens[0] == 'DIE':

                    #
                    # - transition to the terminal state
                    #
                    msg.state = self.cfg['terminal']
                    msg.extra = ''
                    msg.wait = False
                    msg.tick = time.time()
                    self.fifo.append(msg)

                elif tokens[0] == 'SET':

                    #
                    # - set the specified key/value pair onto the environment dict
                    #   used when invoking the shell script
                    #
                    self.env[tokens[1]] = ' '.join(tokens[2:])
                
                elif tokens[0] in ['GOTO', 'WAIT']:

                    #
                    # - pass the incoming message
                    # - depending on the command the socket will be replied to
                    #   immediately or later
                    #
                    msg.state = tokens[1]
                    msg.extra = ' '.join(tokens[2:]) if len(tokens) > 2 else ''
                    msg.wait = tokens[0] == 'WAIT'
                    msg.tick = time.time()
                    self.fifo.append(msg)
           
            except Exception:
                self._ack(msg, 'KO')

        else:
            super(Actor, self).specialized(msg)

    def _ack(self, msg, code):
        if msg.cnx is not None:
            try:
                msg.cnx.send(code)
                msg.cnx.close()

            except IOError:
                pass
                
