import sys
import ez_setup

ez_setup.use_setuptools()

from setuptools import setup, find_packages

if sys.version_info < (2, 7):
    raise NotImplementedError("python 2.7 or higher required")

setup(
    name='kontrol',
    version='0.5.0',
    packages=['automaton', 'kontrol'],
    install_requires=
    [
        'pykka>=1.2.0',
        'python-etcd>=0.4.3',
        'pyyaml>=3.12',
        'jsonschema>=2.6.0'
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
                    'automaton = automaton.main:go'
                ]
        },
)