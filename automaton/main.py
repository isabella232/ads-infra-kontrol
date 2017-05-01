import argparse
import jsonschema
import logging
import os
import socket
import sys
import time
import yaml

from jsonschema import ValidationError
from kontrol.fsm import MSG, shutdown, diagnostic
from logging import DEBUG
from os.path import exists
from machine import Actor as Machine
from yaml import YAMLError


#: Our automaton logger.
logger = logging.getLogger('automaton')

#: The YAML manifest schema
schema = \
"""
type: object
properties:
    initial:
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


def go():

    """
    Entry point for the front-facing automaton script.
    """
    parser = argparse.ArgumentParser(description='automaton', prefix_chars='-')
    parser.add_argument('manifest', type=str, help='YAML manifest')
    parser.add_argument('-s', '--socket', type=str, default='/var/run/automaton.sock', help='unix socket path')
    parser.add_argument('-d', '--debug', action='store_true', help='debug logging on')
    args = parser.parse_args()

    if args.debug:
        logger.setLevel(DEBUG)
    
    if exists(args.socket):
        print 'removing %s' % args.socket
        os.remove(args.socket)

    try:

        #
        # - open our UNIX socket
        #
        actor = None
        fd = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        fd.bind(args.socket)
        fd.listen(8)

        try:

            #
            # - load the YAML manifest
            # - validate against our schema
            #
            with open(args.manifest, 'r') as f:
                cfg = MSG(yaml.load(f.read()))
            
            jsonschema.validate(cfg, yaml.load(schema))
            
            #
            # - start our actor
            # - trip it into its initial state using a fake message
            # - no big deal if the initial state is invalid (the machine
            #   will just remain in 'idle' state until it receives something
            #   valid)
            #
            cfg.args = args
            actor = Machine.start(cfg)
            msg = MSG({'request': 'cmd', 'raw': 'GOTO %s' % cfg['initial']})
            msg.cnx = None
            actor.tell(msg)
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
                shutdown(actor)

    except KeyboardInterrupt:
        pass

    except ValidationError:
        print 'invalid YAML manifest syntax'

    except YAMLError:
        print 'cannot load the YAML manifest'

    except Exception as failure:
        print 'unexpected failure -> %s' % diagnostic(failure)

    finally:
        fd.close()
    
    #
    # - cleanup the socket file
    #
    os.remove(args.socket)
    sys.exit(0)