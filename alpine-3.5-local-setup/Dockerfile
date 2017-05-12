FROM alpine:3.5
ENV TELEGRAF_VERSION 1.2.1
WORKDIR /home/kontrol
RUN echo 'hosts: files dns' >> /etc/nsswitch.conf && \
    apk add --no-cache bc iputils git openssl socat curl jq g++ make python2 python2-dev ca-certificates py2-pip && \
    apk add --no-cache --virtual .build-deps wget gnupg tar && \
    update-ca-certificates && \
    gpg --keyserver hkp://ha.pool.sks-keyservers.net --recv-keys 05CE15085FC09D18E99EFB22684A14CF2582E0C5 && \
    wget -q https://dl.influxdata.com/telegraf/releases/telegraf-${TELEGRAF_VERSION}-static_linux_amd64.tar.gz.asc && \
    wget -q https://dl.influxdata.com/telegraf/releases/telegraf-${TELEGRAF_VERSION}-static_linux_amd64.tar.gz && \
    gpg --batch --verify telegraf-${TELEGRAF_VERSION}-static_linux_amd64.tar.gz.asc telegraf-${TELEGRAF_VERSION}-static_linux_amd64.tar.gz && \
    mkdir -p /usr/src /etc/telegraf && \
    tar -C /usr/src -xzf telegraf-${TELEGRAF_VERSION}-static_linux_amd64.tar.gz && \
    mv /usr/src/telegraf*/telegraf.conf /etc/telegraf/ && \
    chmod +x /usr/src/telegraf*/* && \
    cp -a /usr/src/telegraf*/* /usr/bin/ && \
    rm -rf *.tar.gz* /usr/src /root/.gnupg && \
    adduser -D kontrol && \
    chown kontrol /home/kontrol && \
    pip install --upgrade cython eventlet gunicorn pip supervisor && \
    mkdir -p /etc/supervisor
COPY resources ./
COPY ./ ./
RUN python setup.py install && \
    mv *.conf /etc/supervisor && \
    chmod +x *.sh && \
    apk del g++ make .build-deps
ENTRYPOINT ["/home/kontrol/boot.sh"]
