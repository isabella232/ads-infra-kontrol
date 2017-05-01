from logging.config import fileConfig
from os.path import dirname


#
# - load our logging configuration file
#
fileConfig('%s/log.cfg' % dirname(__file__), disable_existing_loggers=True)
