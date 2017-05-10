#!/bin/sh

#
# - specify our entrypoint upon worker init to boot the actors
# - set the same graceful shutdown timeout as what supervisord uses
#
cat << EOT > /tmp/cfg.py
loglevel = 'error'
daemon = True
bind = '0.0.0.0:8000'
timeout = 5
graceful_timeout = 60
worker_class = 'eventlet'
workers = 1
from kontrol.endpoint import up
def post_worker_init(worker):
    up()
EOT

#
# - start gunicorn as a daemon & loop forever
# - track its pidfile
# - trap and ask for graceful shutdown (http POST + kill -15)
# - this will wait for the endpoint to fully shutdown all actors
#
PIDFILE=/tmp/gunicorn
rm -f $PIDFILE
gunicorn -p $PIDFILE -c /tmp/cfg.py kontrol.endpoint:http &
while [ ! -f $PIDFILE ]
do
  sleep 1
done
trap "curl -s -XPOST localhost:8000/down && kill -15 $(cat $PIDFILE) && exit" 1 2 3 9 15
while true
do
    sleep 60
done
