import argparse
import jsonschema
import logging
import os
import socket
import sys
import time

from kontrol.fsm import diagnostic, MSG, shutdown
from logging import DEBUG, Formatter
from logging.config import fileConfig
from logging.handlers import RotatingFileHandler
from os.path import exists, dirname
from machine import Actor as Machine


#
# - load our logging configuration file
#
fileConfig('%s/log.cfg' % dirname(__file__), disable_existing_loggers=True)

#: Our automaton logger.
logger = logging.getLogger('automaton')

actor = None

def go():

    """
    Entry point for the front-facing automaton script.
    """
    parser = argparse.ArgumentParser(description='automaton', prefix_chars='-')
    parser.add_argument('input', type=str, help='YAML manifest or python script')
    parser.add_argument('-s', '--socket', type=str, default='/var/run/automaton.sock', help='unix socket path')
    parser.add_argument('-l', '--logfile', type=str, default='automaton.log', help='logfile')
    parser.add_argument('-d', '--debug', action='store_true', help='debug logging on')
    args = parser.parse_args()

    #
    # - add a rotating file handler to dump the log into the specified file
    # - if not specified default to automaton.log
    #
    handler = RotatingFileHandler(args.logfile, 'a', 65335, 3)
    handler.setFormatter(Formatter('[automaton] %(asctime)s [%(levelname)s] %(message)s'))
    logger.addHandler(handler)

    if args.debug:
        logger.setLevel(DEBUG)
    
    if exists(args.socket):
        print 'removing %s' % args.socket
        os.remove(args.socket)

    try:

        #
        # - open our UNIX socket
        #
        fd = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        fd.bind(args.socket)
        fd.listen(8)

        try:
            
            #
            # - start our actor
            # - trip it into its initial state using a fake message
            # - no big deal if the initial state is invalid (the machine
            #   will just remain in 'idle' state until it receives something
            #   valid)
            #
            global actor
            actor = Machine.start(args)
            while True:

                #
                # - read/buffer
                # - forward to the actor
                # - pass down the connection object in case we need to
                #   write back to the socket
                #
                buf = ''
                cnx, addr = fd.accept()
                while True: 

                    raw = cnx.recv(1024)
                    if not raw:
                        break
                    buf += raw

                snippet = buf.rstrip('\n')
                logger.debug('socket -> "%s"' % snippet)
                msg = MSG({'request': 'cmd', 'raw': buf.rstrip('\n')})
                msg.cnx = cnx
                actor.tell(msg)

        finally:
            if actor:
                msg = MSG({'request': 'cmd', 'raw': 'DIE'})
                msg.cnx = None
                actor.tell(msg)
                shutdown(actor)

    except KeyboardInterrupt:
        pass

    except Exception as failure:
        print 'unexpected failure -> %s' % diagnostic(failure)

    finally:
        fd.close()
    
    #
    # - cleanup the socket file
    #
    os.remove(args.socket)
    sys.exit(0)