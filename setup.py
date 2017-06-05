import sys
import ez_setup

ez_setup.use_setuptools()

from setuptools import setup, find_packages

if sys.version_info < (2, 7):
    raise NotImplementedError("python 2.7 or higher required")

setup(
    name='kontrol',
    version='1.0.0',
    packages=['automaton', 'kontrol'],
    install_requires=
    [
        'jinja2>=2.9.6',
        'jsonschema>=2.6.0',
        'pykka>=1.2.0',
        'python-etcd>=0.4.3',
        'pyyaml>=3.12',
        'statsd>=2.0.0',
        'zerorpc>=0.6.1'
    ],
    package_data={
        'kontrol':
            [
                'log.cfg'
            ],
        'automaton':
            [
                'log.cfg'
            ]
    },
    entry_points=
        {
            'console_scripts':
                [
                    'automaton = automaton.main:go',
                    'kontrol = kontrol.main:go'
                ]
        },
)