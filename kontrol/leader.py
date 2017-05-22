import etcd
import hashlib
import json
import kontrol
import logging
import os
import requests
import time
import statsd

from kontrol.fsm import Aborted, FSM, MSG


#: our ochopod logger
logger = logging.getLogger('kontrol')


class Actor(FSM):

    """
    Leader watch/MD5 logic which a) attempts to grab a lock and b) runs a dirty
    check to message the callback actor upon any MD5 change
    """

    tag = 'leader'

    def __init__(self, cfg):
        super(Actor, self).__init__()

        self.cfg = cfg
        self.client = etcd.Client(host=cfg['etcd'], port=2379)
        self.md5 = None
        self.path = '%s actor' % self.tag
        self.snapshot = {}
        self.statsd = statsd.StatsClient('127.0.0.1', 8125)

        if 'callback' not in cfg:
            logger.warning('%s: $KONTROL_CALLBACK is not set (user error ?)' % self.path)

    def reset(self, data):

        if hasattr(data, 'lock'):
            try:

                #
                # - make sure to proactively delete the lock key
                # - this will allow to quickly fail-over provided the pod
                #   is gracefully shutdown
                #
                logger.debug('%s : clearing the lock' % self.path)
                self.client.delete(data.lock)
           
            except etcd.EtcdKeyNotFound:
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
        # - use $KONTROL_FOVER to set the lock ttl
        #
        try:
            self.client.refresh(data.lock, ttl=self.cfg['fover'])
      
        except EtcdKeyNotFound:
            raise Aborted('lost key %s (excessive lag ?)' % data.lock)

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
            self.statsd.incr('lock_obtained,tier=kontrol')
            return 'watch', data, 0.0

        #
        # - retry after pausing for 1/8th of $KONTROL_FOVER
        #
        return 'acquire', data, int(self.cfg['fover'] * 0.125)

    def watch(self, data):

        if self.terminate:
            raise Aborted('resetting')
        
        #
        # - make sure we refresh our lock key
        # - a failure means we lagged too much and the key timed out
        # - use $KONTROL_FOVER to set the lock ttl
        #
        try:
            self.client.refresh(data.lock, ttl=self.cfg['fover'])
        
        except etcd.EtcdKeyNotFound:
            raise Aborted('lost key %s (excessive lag ?)' % data.lock)

        try:

            #
            # - block/wait on the dirty watch set off by the sequence actor
            # - use a timeout of 0.75 x $KONTROL_FOVER and *divide by 2* as it appears python-etcd
            #   blocks for twice the prescribed timeout (!?)
            # - silently skip timeouts (worst case scenario)
            #
            tick = time.time()
            self.client.watch('/kontrol/%s/_dirty' % self.cfg['labels']['app'], timeout=int(self.cfg['fover'] * 0.375))
            logger.debug('%s : dirty watch triggered' % self.path)

        except (etcd.EtcdWatchTimedOut, etcd.EtcdConnectionFailed):
            pass

        #
        # - grab the latest snapshot of our reporting pods
        # - order by the sequence index generated in state.py
        # - filter out any pod with the down trigger
        # - compute the corresponding MD5 digest
        # - compare the new digest against the last one
        # - if they differ trigger a callback after a cool-down period
        #
        now = time.time()
        hasher = hashlib.md5()
        logger.debug('%s : waited on the trigger for %3.2f s, computing hash...' % (self.path, now - tick))
        raw = self.client.read('/kontrol/%s/pods' % self.cfg['labels']['app'], recursive=True)
        pods = [json.loads(item.value) for item in raw.leaves if item.value]
        self.snapshot = sorted([pod for pod in pods if 'down' not in pod], key=lambda pod: pod['seq'])
        hasher.update(json.dumps(self.snapshot))
        md5 = ':'.join(c.encode('hex') for c in hasher.digest())
        logger.debug('%s : MD5 -> %s' % (self.path, md5))
        if md5 != self.md5:
            self.md5 = md5
            if 'callback' in self.cfg:

                #
                # - request a callback run
                # - use the damper to specify when to run
                # - please note this may lead to multiple requests buffered by
                #   the callback actor
                #
                msg = MSG({'request': 'invoke'})
                msg.cmd = self.cfg['callback']
                msg.env = {'MD5': md5, 'PODS': json.dumps(self.snapshot)}   
                msg.ttl = now + int(self.cfg['damper'])
                kontrol.actors['callback'].tell(msg)
                self.statsd.incr('md5_changed,tier=kontrol')
                logger.debug('%s : MD5 update, requesting callback' % self.path)

        return 'watch', data, 0.0