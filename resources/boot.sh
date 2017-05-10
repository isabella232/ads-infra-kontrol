#!/bin/sh

#
# - default $KONTROL_MODE to slave
# - attempt to retrieve the pod metadata via the service API
# - timeout at 3 seconds per call
# - retry with exponential backoff for up to 5 tries until we can fetch the IP
# - if we find 'debug' in $KONTROL_MODE bypass the API call and default $POD to {}
#
POD=''
export KONTROL_MODE=${KONTROL_MODE:=slave}
echo $KONTROL_MODE | grep debug
if [ $? -eq 0 ]; then
    POD='{}'
else
    N=1
    while true
    do
        BEARER_TOKEN_PATH=/var/run/secrets/kubernetes.io/serviceaccount/token
        TOKEN="$(cat $BEARER_TOKEN_PATH)"
        URL=https://$KUBERNETES_SERVICE_HOST/api/v1/namespaces/default/pods/$HOSTNAME
        POD=$(curl -s -f -m 3 $URL --insecure --header "Authorization: Bearer $TOKEN")
        echo $POD >> /tmp/pod.json
        IP=$(echo $POD | jq -r '.status.podIP')

        #
        # - we need to treat 2 cases
        #   o the response is empty
        #   o the response is valid json but missing the IP
        # - the container is likely to be launched while the pod's metadata is not
        #   yet finalized when querying the master
        # - this check/backoff is therefore required
        #
        if [ -z $IP ] || [ "$IP" = "null" ]; then
            N=$((N+1))
            if [ $N -gt 5 ]; then
                exit
            fi
            sleep $(echo "(2^$N - 1) / 2" | bc)
        else
            break
        fi
    done
fi

#
# - set the rest of the $KONTROL_* variables
# - the damper & keepalive TTL are defaulted to 10 and 25 seconds
# - the fail over ($KONTROL_FOVER) is defaulted to 60 seconds
# - default $KONTROL_ETCD to the docker host (right now the assumption
#   is that each etcd2 proxy listens on 0.0.0.0 so that we can reach it
#   from within the pod)
# - $KONTROL_ID is derived from the kubernetes pod name
# - $KONTROL_IP and $KONTROL_LABELS are derived from the pod metadata
#   and can't be overriden
#
# @todo how will we implement key isolation and/or authorization ?
#
export KONTROL_HOST=${KONTROL_HOST:=$(echo $POD | jq -r '.status.hostIP')}
export KONTROL_ETCD=${KONTROL_ETCD:=$KONTROL_HOST}
export KONTROL_DAMPER=${KONTROL_DAMPER:=10}
export KONTROL_TTL=${KONTROL_TTL:=25}
export KONTROL_FOVER=${KONTROL_FOVER:=60}
export KONTROL_ID=$(echo $POD | jq -r '.metadata.name')
export KONTROL_IP=$(echo $POD | jq -r '.status.podIP')
export KONTROL_LABELS=$(echo $POD | jq -r '.metadata.labels')

#
# - exec supervisord which in turn will run kontrol
# - track its PID file under /tmp/supervisord
#
PIDFILE=/tmp/supervisord
rm -f $PIDFILE
exec /usr/bin/supervisord --pidfile $PIDFILE -c /etc/supervisor/supervisord.conf
