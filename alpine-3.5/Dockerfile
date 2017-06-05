FROM alpine:3.5
ENV TELEGRAF_VERSION 1.2.1
WORKDIR /home/kontrol
COPY resources ./
RUN echo 'hosts: files dns' >> /etc/nsswitch.conf && \
    apk add --no-cache curl iputils jq libev libzmq python2 python2-dev py2-pip py2-gevent socat && \
    apk add --no-cache --virtual .transient ca-certificates git gnupg g++ make openssl wget && \
    pip install --upgrade pip && \
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
    pip install supervisor git+https://github.com/UnityTech/ads-infra-kontrol.git@zerorpc && \
    mkdir -p /etc/supervisor && \
    mv *.conf /etc/supervisor && \
    chmod +x *.sh && \
    apk del .transient
ENTRYPOINT ["/home/kontrol/boot.sh"]
