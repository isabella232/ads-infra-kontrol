import logging
import os
import types
import yaml

from jinja2 import Template
from subprocess import Popen


#: YAML shell block template, as a jinja2 template
wrapped = \
"""
python - <<-EOF
import inspect
import os
from {{module}} import {{func}}
spec = inspect.getargspec({{func}})
if len(spec.args) == 1:
    {{func}}(os.environ['INPUT'] if 'INPUT' in os.environ else None)
else:
    {{func}}()
EOF
"""

def goto(tag, arg=''):

    #
    # - simply popen the usual echo|socat snippet
    # - this is the only way to proceed since we won't have access to
    #   the actor (we're in a different process)
    #
    Popen("echo GOTO %s '%s' | socat - $SOCKET" % (tag, arg), close_fds=True, shell=True)


class State(object):

    def __init__(self, func, transitions=None):
        assert type(func) is types.FunctionType, 'the argument is not a function'
        self.transitions = transitions if transitions else []
        self.func = func


class States(object):

    def __init__(self, states, initial=None, terminal=None):
        assert states, 'you need to specify at least one state'

        manifest = \
        {
            'initial': initial,
            'terminal': terminal,
            'states': []
        }

        global raw
        from machine import module
        for state in states:
            tag = state.func.__name__
            assert isinstance(state, State), '%s is not deriving from State' % tag

            js = \
            {
                'tag': tag,
                'shell': Template(wrapped).render(func=tag, module=module),
                'next': state.transitions
            }
            manifest['states'].append(js)
        
        #
        # - dump our internal YAML manifest
        # - this will then by parsed by machine.py as if it came from the user
        #
        raw = yaml.dump(manifest)