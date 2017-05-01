import etcd
import hashlib
import json
import kontrol
import logging
import os
import requests
import time

from kontrol.fsm import Aborted, FSM, MSG


#: our ochopod logger
logger = logging.getLogger('kontrol')


class Actor(FSM):

    """
    """

    tag = 'leader'

    def __init__(self, cfg):
        super(Actor, self).__init__()

        self.cfg = cfg
        self.client = etcd.Client(host=cfg['etcd'], port=2379)
        self.path = '%s actor' % self.tag
        self.snapshot = {}
        self.md5 = None

    def reset(self, data):

        if hasattr(data, 'lock'):
            try:

                #
                # - make sure to proactively delete the lock key
                #
                self.client.delete(data.lock.key)
            except Exception:
                pass

        if self.terminate:
            super(Actor, self).reset(data)

        return 'initial', data, 0.0

    def initial(self, data):

        #
        # - setup our lock key which has a unique sequential id
        # - this key will live with a TTL of 10 seconds under locks/ and be prefixed by "leader-"
        #
        data.trigger = 0
        data.dirty = False
        data.lock = self.client.write('/kontrol/%s/locks/leader' % self.cfg['labels']['app'], '', append=True, ttl=10).key
        logger.debug('%s : created lock key #%d' % (self.path, int(data.lock[data.lock.rfind('/')+1:])))
        return 'acquire', data, 0.0

    def acquire(self, data):
        
        if self.terminate:
            raise Aborted('resetting')

        #
        # - make sure we refresh our lock key
        # - a failure means we lagged too much and the key timed out
        #
        try:
            self.client.refresh(data.lock, ttl=10)
        except EtcdKeyNotFound:
            raise Aborted('lost key %s (excessive lag ?)' % data.lock.key)

        #
        # - query the lock directory
        # - sort the keys and compare against ours
        # - if we're first we own the lock
        #
        logger.debug('%s : attempting to grab lock' % self.path)
        items = [item for item in self.client.read('/kontrol/%s/locks' % self.cfg['labels']['app'], recursive=True).leaves] 
        ordered = sorted(item.key for item in items)        
        if data.lock == ordered[0]:
            logger.info('%s : now acting as leader' % self.path)
            return 'watch', data, 0.0

        return 'acquire', data, 5.0

    def watch(self, data):

        if self.terminate:
            raise Aborted('resetting')
        
        #
        # - make sure we refresh our lock key
        # - a failure means we lagged too much and the key timed out
        #
        try:
            self.client.refresh(data.lock, ttl=10)
        except EtcdKeyNotFound:
            raise Aborted('lost key %s (excessive lag ?)' % data.lock.key)

        #
        # - grab the latest snapshot of our reporting pods
        # - order by the sequence index generated in state.py
        # - compute the corresponding MD5 digest
        # - compare against our last hash
        #
        # @todo can we possibly use a watch and/or track indices?
        #
        now = time.time()
        hashed = hashlib.md5()
        raw = self.client.read('/kontrol/%s/pods' % self.cfg['labels']['app'], recursive=True)
        pods = [json.loads(item.value) for item in raw.leaves if item.value]
        self.snapshot = sorted(pods, key=lambda pod: pod['seq'])
        hashed.update(json.dumps(self.snapshot))
        md5 = ':'.join(c.encode('hex') for c in hashed.digest())
        
        #
        # - compare the new digest against the last one
        # - if they differ trigger a callback after a cool-down period
        #
        if md5 != self.md5:
            data.dirty = True
            self.md5 = md5
            damper = int(self.cfg['damper'])
            data.trigger = now + damper
            logger.debug('%s : change detected, script invokation in %d s' % (self.path, damper))

        if data.dirty and now > data.trigger:
                        
            #
            # - reset the trigger
            # - package the $PODS and $MD5 environment variables
            # - post to the update actor if $KONTROL_CALLBACK is defined
            # - the $STATE variable will be added by the callback actor
            #
            data.dirty = False
            logger.debug('%s : invoking callback, MD5 digest -> %s' % (self.path, md5))
            self.client.write('/kontrol/%s/md5' % self.cfg['labels']['app'], md5)
            if 'callback' in self.cfg:

                msg = MSG({'request': 'invoke'})
                msg.cmd = self.cfg['callback']
                msg.env = {'MD5': md5, 'PODS': json.dumps(self.snapshot)}     
                kontrol.actors['callback'].tell(msg)
         
            else:
                logger.warning('%s: $KONTROL_CALLBACK is not set (user error ?)' % self.path)
               
        return 'watch', data, 1.0

