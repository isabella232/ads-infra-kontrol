import etcd
import json
import logging
import requests
import time

from collections import deque
from etcd import EtcdAlreadyExist, EtcdKeyNotFound
from kontrol.fsm import Aborted, FSM


#: our ochopod logger
logger = logging.getLogger('kontrol')


class Actor(FSM):

    """
    Actor in charge of writing incoming keepalive payloads to etcd. The
    catch is that we enforce sequential indexing: any payload not matching
    any existing pod (e.g without a key already in etcd) will bump the
    index value. This sequence index is then persisted along the the
    pod keepalive payload.

    This ordering is critical to properly compute the MD5 digest and
    enforce consistency when for instance rendering zookeeper templates or
    anything relying on integer indices.
    """

    tag = 'sequence'

    def __init__(self, cfg):
        super(Actor, self).__init__()

        self.cfg = cfg
        self.client = etcd.Client(host=cfg['etcd'], port=2379)
        self.fifo = deque()
        self.path = '%s actor' % self.tag

    def reset(self, data):

        if self.terminate:
            super(Actor, self).reset(data)

        return 'initial', data, 0.0

    def initial(self, data):
                
        if self.terminate and not self.fifo:
            raise Aborted('resetting')

        while self.fifo:

            #
            # - lookup the key given the application label and the pod id
            # - they etcd key is prefix by the master's application label
            # - attempt to read its payload
            #
            now = time.time()
            nxt = self.fifo[0]
            path = '/kontrol/%s/pods/%s' % (self.cfg['labels']['app'], nxt['key'])
            try:
                js = json.loads(self.client.read(path).value)
            
            except EtcdKeyNotFound:

                #
                # - if the read fails this is the first time that pod is reporting
                # - in that case generate a new monotonic sequence index
                # - attach it to the persisted pod payload
                #
                js = {'seq': self._next()}

            js.update(nxt)

            #
            # - make sure to sort the keys in the json being serialized to etcd
            # - otherwise that could artifically change the MD5 digest
            #
            ttl = int(self.cfg['ttl'])
            self.client.write(path, json.dumps(js, sort_keys=True), ttl=ttl)
            logger.debug('%s : keepalive from %s (pod #%d)' % (self.path, js['key'], js['seq']))
            self.fifo.popleft()

        return 'initial', data, 0.25

    def specialized(self, msg):
        assert 'request' in msg, 'bogus message received ?'
        req = msg['request']
        if req == 'update':

            #
            # - buffer the incoming payload in our fifo
            # - we'll dequeue it upon the next spin
            #
            assert 'state' in msg, 'invalid message -> "%s" (bug ?)' % msg
            self.fifo.append(msg['state'])
        else:
            super(Actor, self).specialized(msg)
        
    def _next(self):

        #
        # - simple CAS incrementing a monotonic integer counter
        # - this counter is used as a sequence to order our pods in a deterministic way  
        #
        key = '/kontrol/%s/seq' % self.cfg['labels']['app']
        while True:
            try:

                cur = int(self.client.read(key).value)
                nxt = int(self.client.write(key, cur + 1, prevValue=cur).value)
                assert nxt == cur + 1, 'CAS failed (another party updated the counter)'
                logger.debug('%s : counter @ %d' % (self.path, nxt))
                return nxt

            except AssertionError:
                
                #
                # - the CAS assert clause failed
                # - just ignore and spin
                #
                pass

            except EtcdKeyNotFound:

                #
                # - the sequence key does not exist yet
                # - attempt to initialize it to -1 (so that the first returned value is 0)
                # - that could fail on a EtcdAlreadyExist depending on timing
                #
                try:
                    self.client.write(key, -1, prevExist=False)
                except EtcdAlreadyExist:
                    pass
                
