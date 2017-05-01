import fnmatch
import json
import logging
import time

from collections import deque
from kontrol.fsm import Aborted, FSM, diagnostic
from os.path import abspath
from subprocess import Popen, PIPE, STDOUT


#: our ochopod logger
logger = logging.getLogger('automaton')


class Actor(FSM):

    """
    Actor emulating a simple state machine that runs shell scripts and can
    be tripped at any time to a desired state. The states and transitions are
    describes in the YAML manifest.

    Tripping the machine while its shell script is still running will cause it
    to be killed. Transition requests are buffered and processed in order.
    """

    tag = 'machine'

    def __init__(self, cfg):
        super(Actor, self).__init__()

        self.cfg = cfg

        #
        # - set the current state to 'idle' and let it transition
        #   to anything
        #
        self.cur = {'tag': 'idle', 'shell': '', 'next': ['*']}
        self.fifo = deque()
        self.path = '%s actor' % self.tag
        self.states = {js['tag']:js for js in cfg['states']}

    def reset(self, data):
       
        if self.terminate:
            super(Actor, self).reset(data)

        logger.warning('%s : uncaught exception -> %s' % (self.path, data.diagnostic))
        return 'initial', data, 0.0

    def initial(self, data):
        
        if self.terminate:
            raise Aborted('resetting')

        while self.fifo:

            #
            # - peek at the next transition in our FIFO
            # - make sure it is valid
            # - proceed with the first one matching the pattern
            #
            msg = self.fifo[0]
            try:

                assert msg.state in self.states, 'unknown state "%s"' % msg.state
                allowed = self.cur['next'] if 'next' in self.cur else []
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
                        env = \
                        {
                            'SOCKET': abspath(self.cfg.args.socket),
                            'INPUT': msg.extra
                        }

                        data.tick = time.time()
                        data.pid = Popen(self.cur['shell'],
                        close_fds=True,
                        bufsize=0,
                        shell=True,
                        env=env,
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
        complete = data.pid.poll() is not None

        #
        # - the process either completed or we have buffered state transitions
        #   in our FIFO
        # - if transitions are buffered forcelly terminate the running script
        # - display the process standard outputs
        # - pop the FIFO and cycle back to the initial state
        #
        if complete or len(self.fifo) > 1:
            if not complete:
                logger.debug('%s : killing pid %s (%d transitions pending)' % (self.path, data.pid.pid, len(self.fifo) - 1))
                data.pid.kill()

            lapse = time.time() - data.tick
            code = data.pid.returncode if complete else '_'
            stdout = [line.rstrip('\n') for line in iter(data.pid.stdout.readline, b'')]
            logger.debug('%s : script took %2.1f s (pid %s, exit %s)' % (self.path, lapse, data.pid.pid, code))
            if stdout:
                logger.debug('%s : stderr (pid %s) -> \n  . %s' % (self.path, data.pid.pid, '\n  . '.join(stdout)))

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
            # - right now we support WAIT, GOTO and STATE
            #
            tokens = msg['raw'].split(' ')
            assert tokens[0] in ['STATE', 'GOTO', 'WAIT'], 'invalid command'

            if tokens[0] == 'STATE':
                self._ack(msg, self.cur['tag'])
            
            elif tokens[0] in ['GOTO', 'WAIT']:
                msg.state = tokens[1]
                msg.extra = ' '.join(tokens[2:]) if len(tokens) > 2 else ''
                msg.wait = tokens[0] == 'WAIT'
                self.fifo.append(msg)

        else:
            super(Actor, self).specialized(msg)

    def _ack(self, msg, code):
        if msg.cnx is not None:
            try:
                msg.cnx.send(code)
                msg.cnx.close()

            except IOError:
                pass
                
