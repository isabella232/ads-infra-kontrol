#!/bin/sh

#
# - attempt to retrieve the pod metadata via the service API
# - timeout at 5 seconds
#
# @todo retry if the API call fails
#
BEARER_TOKEN_PATH=/var/run/secrets/kubernetes.io/serviceaccount/token
TOKEN="$(cat $BEARER_TOKEN_PATH)"
URL=https://$KUBERNETES_SERVICE_HOST/api/v1/namespaces/default/pods/$HOSTNAME
POD=$(curl -s -f -m 5 $URL --insecure --header "Authorization: Bearer $TOKEN")
if [ 0 -ne $? ]; then POD='{}'; fi;

#
# - set the required $KONTROL_* variables
# - default $KONTROL_MODE to slave
# - the damper & keepalive TTL are defaulted to 10 and 25 seconds
# - the fail over ($KONTROL_FOVER) is defaulted to 60 seconds
# - default $KONTROL_ETCD to the docker host (right now the assumption
#   is that each etcd2 proxy listens on 0.0.0.0 so that we can reach it
#   from within the pod)
# - $KONTROL_ID is derived from the kubernetes pod name
# - $KONTROL_IP and $KONTROL_LABELS are derived from the pod metadata
#   and can't be overriden
#
#
# @todo how will we implement key isolation and/or authorization ?
#
export KONTROL_HOST=${KONTROL_HOST:=$(echo $POD | jq -r '.status.hostIP')}
export KONTROL_ETCD=${KONTROL_ETCD:=$KONTROL_HOST}
export KONTROL_MODE=${KONTROL_MODE:=slave}
export KONTROL_DAMPER=${KONTROL_DAMPER:=10}
export KONTROL_TTL=${KONTROL_TTL:=25}
export KONTROL_FOVER=${KONTROL_FOVER:=60}
export KONTROL_ID=$(echo $POD | jq -r '.metadata.name')
export KONTROL_IP=$(echo $POD | jq -r '.status.podIP')
export KONTROL_LABELS=$(echo $POD | jq -r '.metadata.labels')

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
# - track its PID as /tmp/pid
# - trap and ask for graceful shutdown (http POST + kill -15)
# - this will wait for the endpoint to fully shutdown all actors
#
rm -f /tmp/pid
gunicorn -p /tmp/pid -c /tmp/cfg.py kontrol.endpoint:http &
while [ ! -f /tmp/pid ]
do
  sleep 1
done
trap "curl -s -XPOST localhost:8000/down && kill -15 $(cat /tmp/pid) && exit" 1 2 3 9 15
while true
do
    sleep 60
done
