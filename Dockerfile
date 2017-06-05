FROM alpine:3.5
RUN echo 'hosts: files dns' >> /etc/nsswitch.conf && \
    apk add --no-cache curl iputils jq libzmq python2 python2-dev py2-pip py2-gevent socat && \
    apk add --no-cache --virtual .transient ca-certificates git gnupg g++ make openssl wget && \
    pip install --upgrade pip && \
    pip install zerorpc
