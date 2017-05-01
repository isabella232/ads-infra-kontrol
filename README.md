## Kontrol

### Overview

This project packages a small [**Python**](https://www.python.org/) REST endpoint you can
include to any [**Kubernetes**](https://github.com/GoogleCloudPlatform/kubernetes) pod. It
relies on [**Etcd**](https://github.com/coreos/etcd) for synchronization, leader election
and persistence and will allow you to run code whenever a change occurs within the a set
of monitored pods.

It also offers a simple command-line tool to run a finite state machine that is controlled
via a local unix socket. This machine can be used to script the lifecycle of whatever
process is managed by Kontrol.

### Building the image

Pick a distro and build from the top-level directory. For instance:

```
$ docker build -f alpine-3.5/Dockerfile .
```

Please note the two packages can be installed directly from github via *pip* (especially
if you wish to include them in your own images or on your local dev box). For instance:

```
$ sudo pip install git+https://github.com/opaugam-unity/kontrol.git
```

The image entrypoint is *supervisord* which is started from the */home/kontrol* directory. By
default *kontrol* is not started and you have to explicitely add it to the supervisor jobs.
Anything with extension *conf* found under */home/kontrol* will be included as a supervisor
configuration file.

### Documentation

Please look at the URL attached to this repository. It will take you to its latest github page.
The [**Sphinx**](http://sphinx-doc.org/) materials can be found under docs/. Just go in there
and build for your favorite target, for instance:

```
$ cd docs
$ make html
```

The docs will be written to _docs/_build/html_. This is all Sphinx based and you have many
options and knobs to tweak should you want to customize the output.

### Support

Contact olivierp@unity3d.com for more information about this project.