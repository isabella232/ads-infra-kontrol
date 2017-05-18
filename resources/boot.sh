#!/bin/sh

#
# - grab the current namespace from the downward api
# - we expect it to be passed as $NAMESPACE in the container definition
# - if not found revert to 'default'
#
NAMESPACE=${NAMESPACE:=default}

#
# - default $KONTROL_MODE to nothing
# - attempt to retrieve the pod metadata via the service API
# - make sure to use the proper namespace
# - retry with exponential backoff for up to 9 tries until we can fetch the IP
# - if we find 'debug' in $KONTROL_MODE bypass the API call and default $POD to {}
#
export KONTROL_MODE=${KONTROL_MODE:=}
echo $KONTROL_MODE | grep debug
if [ $? -eq 0 ]; then
    POD='{}'
    NODE='{}'
else
    N=1
    while true
    do
        BEARER_TOKEN_PATH=/var/run/secrets/kubernetes.io/serviceaccount/token
        TOKEN="$(cat $BEARER_TOKEN_PATH)"
        URL=https://$KUBERNETES_SERVICE_HOST/api/v1/namespaces/$NAMESPACE/pods/$HOSTNAME
        POD=$(curl -s -f -m 5 $URL --insecure --header "Authorization: Bearer $TOKEN")
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
            if [ $N -gt 9 ]; then
                exit
            fi
            sleep $(echo "(2^$N - 1) / 2" | bc)
        else
            break
        fi
    done

    #
    # - query the node on which we are running
    # - the API lookup is done using the ip-x-x-x-x.ec2.internal hostname
    # - use the EC2 API to retrieve it (provided we're on EC2 of course)
    # - please note that on a local minikube setup $NODE will be empty
    #
    # @todo support multiple providers, not just AWS/EC2
    #
    LOCAL=$(curl -m 5 http://169.254.169.254/latest/meta-data/local-hostname)
    URL=https://$KUBERNETES_SERVICE_HOST/api/v1/nodes/$LOCAL
    NODE=$(curl -s -f -m 5 $URL --insecure --header "Authorization: Bearer $TOKEN")
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
# - $KONTROL_ANNOTATIONS is used to pass custom settings down
# - the $KONTROL_NODE_* variables hold the same information but for the
#   underlying host
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
export KONTROL_ANNOTATIONS=$(echo $POD | jq -r '.metadata.annotations')
export KONTROL_NODE_LABELS=$(echo $NODE | jq -r '.metadata.labels')
export KONTROL_NODE_ANNOTATIONS=$(echo $NODE | jq -r '.metadata.annotations')

#
# - remove the canned telegraf configuration
# - render a new one and include only specific labels
# - don't forget to trim the qualifier to avoid ending up with long keys
# - the opentsdb target endpoint is specified via a annotation
# - if not defined the script below will silently die
#
rm -f /etc/telegraf/telegraf.conf
python - <<-EOF
import fnmatch
import json
import os
from jinja2 import Template

labels = {}
js = json.loads(os.environ['KONTROL_ANNOTATIONS'])
endpoint = js['kontrol.unity3d.com/opentsdb']
accepted = ['app', 'role', '*unity3d.com*', '*/hostname']
def _fetch(var):
    try:
        for key, value in json.loads(os.environ[var]).items():
            for pat in accepted:
                if fnmatch.fnmatch(key, pat):
                    labels[key[key.find('/')+1:]] = value
                    break
    except (KeyError, TypeError, ValueError):
        pass

_fetch('KONTROL_NODE_LABELS')
_fetch('KONTROL_LABELS')
raw = \
"""
    [global_tags]
    {%- for key in labels | sort %}
    "{{key}}"="{{labels[key]}}"
    {%- endfor %}

    [agent]
    interval = "10s"
    round_interval = true
    metric_batch_size = 1000
    metric_buffer_limit = 10000
    collection_jitter = "0s"
    flush_interval = "10s"
    flush_jitter = "0s"
    precision = ""
    debug = false
    quiet = true
    logfile = ""

    [[outputs.opentsdb]]
    host = "tcp://{{endpoint}}"
    port = 4242

"""
with open('/etc/telegraf/telegraf.conf', 'wb') as fd:
    fd.write(Template(raw).render(labels=labels, endpoint=endpoint))
EOF

#
# - exec supervisord which in turn will run kontrol
# - telegraf will not be started by default
# - track its PID file under /tmp/supervisord
#
PIDFILE=/tmp/supervisord
rm -f $PIDFILE
exec /usr/bin/supervisord --pidfile $PIDFILE -c /etc/supervisor/supervisord.conf
