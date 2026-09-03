#########################################################################
#
# CDEadmin - Multi-engine Database Administration
#
# Copyright (C) 2013 - 2026, The pgAdmin Development Team
# This software is released under the PostgreSQL Licence
#
##########################################################################

import builtins
import os
import sys
from codecs import open
from importlib.machinery import SourceFileLoader

from setuptools import setup


# Load a source file
def load_source(name, path):
    if not os.path.exists(path):
        print("ERROR: Could not find %s" % path)
        sys.exit(1)

    return SourceFileLoader(name, path).load_module()


# Ensure the global server mode is set.
builtins.SERVER_MODE = None

# Get the requirements list for the current version of Python
req_file = '../requirements.txt'

with open(req_file, 'r') as req_lines:
    all_requires = req_lines.read().splitlines()

requires = []
kerberos_extras = []
# Ensure the Wheel will use psycopg-binary, not the source distro, and stick
# gssapi in it's own list
for index, req in enumerate(all_requires):
    if 'psycopg[c]' in req:
        req = req.replace('psycopg[c]', 'psycopg[binary]')

    if 'gssapi' in req:
        kerberos_extras.append(req)
    else:
        requires.append(req)

# Get the version
path = '../web/'
if not os.path.exists(path):
    print("ERROR: Could not find %s" % path)
    sys.exit(1)
sys.path.append(path)
import config

setup(
    name='cdeadmin',

    version='0.1.0.dev0',

    description='Multi-engine and multi-model database administration',
    long_description='CDEadmin is an independent hard fork of pgAdmin 4 '
                     '9.17. It provides provider-driven administration for '
                     'ScratchBird and independent relational, non-relational, '
                     'analytic, and distributed database engines. Upstream '
                     'pgAdmin copyright and attribution are retained.',

    author='CDEadmin contributors',

    license='PostgreSQL Licence',

    # See https://pypi.python.org/pypi?%3Aaction=list_classifiers
    classifiers=[
        'Development Status :: 3 - Alpha',

        # Supported programming languages
        'Programming Language :: Python :: 3.9',
        'Programming Language :: Python :: 3.10',
        'Programming Language :: Python :: 3.11',
        'Programming Language :: Python :: 3.12',
        'Programming Language :: Python :: 3.13',
        'Programming Language :: Python :: 3.14'
    ],

    keywords='cdeadmin,scratchbird,database,administration,multi-engine',

    packages=["pgadmin4"],

    include_package_data=True,

    install_requires=requires,

    extras_require={
        "kerberos": kerberos_extras,
    },

    entry_points={
        'console_scripts': ['cdeadmin=pgadmin4.CDEadmin:main',
                            'cdeadmin-cli=pgadmin4.setup:main'],
    },

)
