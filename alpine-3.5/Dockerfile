FROM alpine:3.5
WORKDIR /home/kontrol
RUN apk add --no-cache git openssl socat curl jq g++ make python2 python2-dev ca-certificates py2-pip && \
    adduser -D kontrol && \
    chown kontrol /home/kontrol && \
    pip install --upgrade pip pyyaml jsonschema eventlet shell supervisor cython gunicorn flask requests

#
# - pip install kontrol + automaton straight from github
# - move the supervisord configuration file under /etc/supervisor
#
COPY resources ./
RUN pip install git+https://github.com/opaugam-unity/kontrol.git && \
    mkdir -p /etc/supervisor && \
    mv *.conf /etc/supervisor && \
    apk del g++ make && \
    chmod +x *.sh

#
# - our entry point is to simply spawn supervisord
# - use the default configuration under /etc/supervisor
# - derived images are expected to add their jobs under /etc/supervisor/conf.d
#
ENTRYPOINT ["supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf"]