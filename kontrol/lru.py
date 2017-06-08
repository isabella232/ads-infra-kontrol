import logging
import time
import weakref

from collections import deque
from threading import Thread, RLock


#: our ochopod logger
logger = logging.getLogger('kontrol')


class LRU(object):

    """
    Simple LRU cache with temporal eviction. Used to manage the RPC clients.
    """

    tag = 'callback'

    def __init__(self, grace=60.0, capacity=None, evicted=None):
       
        self.capacity = capacity
        self.dict = {}
        self.evicted = evicted
        self.grace = grace
        self.last = deque()
        self.lock = RLock()
        
        class _Cleaner(Thread):
            daemon = True

            def __init__(self, cache, every=5.0):
                super(_Cleaner, self).__init__()
                self.cache = weakref.ref(cache)
                self.every = every

            def run(self):
                while 1:
                    cache = self.cache()
                    if cache is None:
                        return
                    cache.evict()
                    cache = None
                    time.sleep(self.every)
        
        _Cleaner(self).start()

    def __getitem__(self, key):
        with self.lock:
            if not key in self.dict:
                return None
            self.last.remove(key)
            self.last.appendleft(key)
            val, _ = self.dict[key]
            payload = (val, time.time())
            self.dict[key] = payload
            cur = len(self.dict)
            return val

    def __setitem__(self, key, val):
        with self.lock:
            if key in self.dict:
                self.last.remove(key)
            
            payload = (val, time.time())
            self.last.appendleft(key)
            self.dict[key] = payload
            cur = len(self.dict)
            logger.debug('lru cache : + key "%s" (%d keys)' % (key, cur))
            if self.capacity is not None and cur > self.capacity:
                key = self.last.pop()
                val, _ = self.dict[key]
                if self.evicted is not None:
                    self.evicted(val)
                del self.dict[key]
                logger.debug('lru cache : - key "%s"' % key)


    def evict(self):
        with self.lock:
            now = time.time()
            for key, payload in self.dict.items():
                val, tick = payload
                if now - tick > self.grace:
                    self.last.remove(key)
                    val, _ = self.dict[key]
                    if self.evicted is not None:
                        self.evicted(val)
                    del self.dict[key]
                    logger.debug('lru cache : - key "%s"' % key)
                




