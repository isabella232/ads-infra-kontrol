Kontrol
=======

Introduction
____________

Overview
********

*Kontrol* is a small Python_ package which implements a REST/HTTP endpoint plus a set of
Pykka_ state-machines. It is used to report periodic keepalive messages from a set of
pods and aggregate them in Etcd_.

.. figure:: png/overview.png
   :align: center
   :width: 90%

The goal is to detect any global change within this set of pods and react to it via a
user-defined callback. This callback is provided the ordered list of all participating
pods with details such as their IPv4 address and so on.

*Kontrol* is designed to address a few common use cases: passive monitoring and alerting,
distributed configuration or self-healing. You can use it in various topologies: separate
master & slave tiers, mixed mode (e.g self-managing pod ensemble) or even setup a chain of
masters (e.g monitoring the monitor).

System design
*************

*Kontrol* operates in either slave, master or mixed mode. A **slave** will periodically emit a
keepalive message to its **master** tier. This message include information about the pod itself
plus some optional json payload retrieved from a configurable file on disk. If this feature is
enabled the slave will track any update to that file and force a keepalive upon modification. This
mechanism is meant to be used in conjunction with *automaton* in which case the local state machine
could for instance update the file on disk whenever it transitions.

Please note pods always must define at least the *app* and *role* labels. Those are use internally
for indexing.

The masters will receive those keepalives and maintain a MD5 digest reflecting the global system
state. Any time this digest changes for whatever reason a user defined callback is scheduled after
a configurable delay. This amortization mechanism is designed to minimize spurrious callback runs
in case the multiple changes are detected within a short period of time. The MD5 hash will change
whenever pods go up, down or update their payload.

The masters are HA and will fail-over in case of problem. They are typically run via a Kubernetes_
deployment fronted by a service. Any master can receive keepalives but only one at any given time
is in charge of tracking the digest and executing the callback. All the locking, leader election
and persistence is done via Etcd_.

.. figure:: png/schematic.png
   :align: center
   :width: 90%

Please note you can run in both **master**/**slave** meaning the same pod deployment can run its
own monitoring logic.


Pod ordering
************

It is crucial to keep consistent ordering for the pods that are being monitored. *Kontrol* does it
by first identifying pods using their base 62 shortened Kubernetes_ IPv4 address. A *monotonic*
integer sequence is also assigned to each pod the first time they emit a keepalive. This sequence
counter is then persisted as long as the pod is alive.


Telemetry
*********

*Kontrol* also allows slaves to execute arbitrary commands on behalf of the master. This mechanism
is the primary way to actively control your pod ensemble. Those shell commands are run by the *kontrol*
user and anything written to the standard output is sent back to the master.

It is also important to note that the callback has the ability to persist its own stateful data across
multiple invokations. This is critical to maintain consistent runtime information describing how
the overall system is evolving. A typical use-case would be to assign and track custom ids or to
be able to re-assign existing data to new pods.


Configuration
_____________


Environment variables
*********************

Kontrol is configured via a few environments variables. Those are mostly defaulted based on what
the Kubernetes_ pod provides. A few can be specified in the manifest.

- **$KONTROL_HOST**: IPv4 address for the kube proxy (defaulted).
- **$KONTROL_IP**: IPv4 address for the pod (defaulted).
- **$KONTROL_ID**: pod identifier (defaulted).
- **$KONTROL_ETCD**: IPv4 address for a Etcd_ proxy (defaulted).
- **$KONTROL_ANNOTATIONS**: pod's annotation dictionary (defaulted).
- **$KONTROL_LABELS**: pod's label dictionary (defaulted).
- **$KONTROL_MODE**: pod operating mode, see below (defaulted).
- **$KONTROL_DAMPER**: reactivity damper (defaulted).
- **$KONTROL_TTL**: pod keepalive cutoff (defaulted).
- **$KONTROL_FOVER**: master fail-over delay (defaulted).
- **$KONTROL_CALLBACK**: executable to run upon callback (optional).
- **$KONTROL_PAYLOAD**: local json file on disk to add to the keepalives (optional).

The labels are picked for you from the Kubernetes_ pod metadata. However you **must** at least
define the *app* and *role* labels.

YAML manifest
*************

The manifest used to define a pod using *kontrol* should at least include the pod namespace via
the downward API. It is expected to be mounted locally under */hints/namespace*. If the namespace
is not passed the pod information will be queried at runtime using the *default* namespace. For
instance:

.. code-block:: YAML

    apiVersion: extensions/v1beta1
    kind: Deployment
    metadata:
    name: test
    namespace: test
    spec:
    replicas: 1
    template:
        metadata:
        labels:
            app: test
            role: example
        spec:
        volumes:
        - name: hints
            downwardAPI:
            items:
                - path: "namespace"
                fieldRef:
                    fieldPath: metadata.namespace

        containers:
        - image: registry2.applifier.info:5005/ads-infra-kontrol-alpine-3.5
            name: kontrol
            volumeMounts:
            - name: hints
            mountPath: /hints
            readOnly: true


Operating mode
**************

Kontrol can run in different modes. The **$KONTROL_MODE** variable is a comma separated list of tokens
indicating what underlying actors to run. Valid token values include *slave*, *master*, *debug* and *verbose*.
The default value is set to *slave* meaning that Kontrol will just attempt to report keepalive messages.
Specifying *master* will enable receiving keepalives and tracking the MD5 digest. Please note you can
specify both *master* and *slave* at the same time.

Slave pods will use the **kontrol.unity3d.com/master** annotation to send keepalive. This annotation should
contain a valid identifier resolvable via the internal DNS (e.g a valid service CNAME record). The following
manifest will for instance define slaves that report keepalives to a service called "foo":

.. code-block:: YAML

    apiVersion: extensions/v1beta1
    kind: Deployment
    metadata:
    name: test
    namespace: test
    spec:
    replicas: 1
    template:
        metadata:
        labels:
            app: test
            role: example
        annotations:
            kontrol.unity3d.com/master: foo.test.svc
        spec:
        volumes:
        - name: hints
            downwardAPI:
            items:
                - path: "namespace"
                fieldRef:
                    fieldPath: metadata.namespace

        containers:
        - image: registry2.applifier.info:5005/ads-infra-kontrol-alpine-3.5
            name: kontrol
            volumeMounts:
            - name: hints
            mountPath: /hints
            readOnly: true


The *verbose* token will turn debug logs on. Those are piped to the container standard output.

Adding *debug* will allow to run in local debugging mode. In that case *slave* and *master* will be added
as well and **$KONTROL_HOST** used for both the pod IPv4 and Etcd_. In other words you can run a self contained
master/slave instance of your Kontrol image by doing:

.. code-block:: shell

    $ sudo ifconfig lo0 alias 172.16.123.1
    $ docker run -e KONTROL_MODE=debug -e KONTROL_HOST=172.16.123.1 -p 8000:8000 <image>

Please note this assumes you have a local Etcd_ running on your local host and listening on all interfaces.


Etcd
****

The **$KONTROL_ETCD** variable is defaulted to the kube proxy IPv4. This assumes the Etcd_ proxy running in there
is listening on all interfaces. If you want to use a dedicated Etcd_ proxy you can override this variable.


JSON Payload 
************

Slaves have the ability to include arbirary json payload in their keepalives. Simply set the **$KONTROL_PAYLOAD**
variable to point to a valid file on disk containing serialized JSON. This content will be parsed and included
in the keepalives. Any modification to that file will cause the slave to parse it and force a keepalive.

If the variable is not set or if the file does not exist or contains invalid JSON this process will be skipped.


Callback
********

Kontrol will periodically run a user-defined callback whenever a global change is detected. This callback
is an arbitrary shell command you can specify via the **$KONTROL_CALLBACK** variable. This subprocess is
tracked and its standard error and output piped back. The shell invokation is done using the *kontrol* user.

The callback subprocess will be passed 3 environment variables:

- **$HASH**: latest MD5 digest.
- **$PODS**: ordered list of pods as a JSON array.
- **$STATE**: optional persistent state as a JSON entity.

The **$PODS** variable contains a snapshot of the current pod ensemble. It is passed as a serialized JSON
array whose entries are consistently ordered. Anything written on the standard output is assumed to be
valid JSON syntax, will be persisted in Etcd_ and passed back upon the next invokation as the **$STATE**
variable.

Each entry in the **$POD** array is a small object containing a few fields. For instance:

.. code-block:: json

    {
        "app": "my-service",
        "id" : "redis-3621538101-fnnfp",
        "ip": 172.16.123.1,
        "key" : "39mysN",
        "payload": {"some": "stuff"},
        "seq": 3,
        "role": "redis"
    }

The key and sequence counter are guaranteed to be unique amongst all the monitored pods. The payload field is
optional and set if the slaves have **$KONTROL_PAYLOAD** set and tracking a valid json file on disk.

The following Python_ callback script will for instance display the key and IPv4 address assigned to each pod:

.. code-block:: python

    #!/usr/bin/python

    import os
    import sys
    import json

    if __name__ == '__main__':

        for pod in json.loads(os.environ['PODS']):
            print >> sys.stderr, ' - #%d (%s) -> %s' % (pod['seq'], pod['key'], pod['ip'])


.. include:: links.rst