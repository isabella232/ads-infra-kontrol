Automaton
============


Introduction
_______________


Overview
********

*Automaton* is a small Python_ tool that implements a finite state machine running
shell scripts. It is easily configured via a YAML manifest and will implement whatever
lifecycle you wish to enforce. The tool will install itself under */usr/local/bin* or
*/usr/bin* depending on the environment.

*Automaton* will flow from state to state and run a user-defined script at each transition.
When starting *Automaton* will setup a unix socket and listen for commands. The machine will
start in the prescribed state and always transition to its terminal state before shutting down
(which is convenient to implement graceful shutdown procedures).


Getting started
***************

Write a first simple YAML manifest called *bot.yml* with 3 states *A*, *B* and *C*. *A*
can switch to *B* and *B* will pause for 5 seconds and write to a local *foo* file. Note
*B* can transition to itself but not *A*. *C* is the terminal state the machine will
transition to upon shutdown.

.. figure:: png/A-B.png
   :align: center
   :width: 75%

The YAML manifest will for instance look like:

.. code-block:: YAML

    initial: A
    terminal: C
    states:
    - tag: A
      shell: echo starting
      next: 
        - B
    - tag: B
      shell: |
        sleep 5
        echo $INPUT > foo
      next: 
        - A
        - B
    - tag: C
      shell: |
        echo terminating


Each block in the *states* array must contain the state tag, a valid shell snippet and what 
transitions are allowed via the *next* array. Please note you can use a glob pattern and that
not specifying anything means the state is final.

Simply run *Automaton* on the command line and specify our YAML manifest and the name of
the socket to create:

.. code-block:: shell

    $ automaton bot.yml -d -s /tmp/sock

The machine will start and automatically switch to its initial state. You can test it is now
in the *A* state by using socat to write to the socket:

.. code-block:: shell

    $ echo STATE | socat - /tmp/sock
    A

Now let's trip it to the B state. After 5 seconds you should be able to see that *foo* file.

.. code-block:: shell

    $echo GOTO B | socat - /tmp/sock
    OK
    $ls -s foo
    0 foo

Since *B* can transition to itself let's trip again but this time we'll specify some input
payload. What's echoed to the socat after the state will be passed down verbatim to the shell
script as the **$INPUT** environment variable. For instance:

.. code-block:: shell

    $echo GOTO B hello | socat - /tmp/sock
    OK
    $cat foo
    hello


States
______


Transitioning
*************

You can transition to a target state either asynchronously using *GOTO* or blocking using
*WAIT*. Both commands will send an acknowledgement back: either **OK** if the transition was
successful or **KO** if the target state is invalid. Please be aware that depending on the
script a *WAIT* command might take some time: be sure to *socat* with a timeout in that
case.

You can pass arbitrary payload as well after the state. This payload will be passed down 
during the transition to the shell script via the **$INPUT** variable. This variable is free-form
and can be wathever. For instance:

.. code-block:: shell

    $echo GOTO B '{"counter": 123}' | socat - /tmp/sock
    OK

Whenever transitioning to a state the associate shell script will be executed and run from
where the *automaton* command was invoked. The shell script standard outputs will be piped
and logged in debug mode.

The unix socket used for communication is always passed down as the $SOCKET variable. You can
in addition set variables at any moment by using the *SET* command. Those variables will be
set for any subsequent shell script invokations. For instance if you wish **$COUNTER** to be
made available at the next transition and set it to "123" you can do:

.. code-block:: shell

    $echo SET COUNTER 123 | socat - /tmp/sock
    OK

Please note you can send commands to the machine from *within* a script. This is handy to
implement cycles or to trip the machine based on some condition. For instance the following
state will transition to itself every minute:

.. code-block:: yaml

    - tag X
      shell: |
        echo looping state
        sleep 60
        echo GOTO X | socat - $SOCKET

Whenever transitioning the current shell script will be forcefully killed (provided it is
still running). The running script will be given a grace period of a few seconds to complete
after which it will abort on a *SIGKILL*.

Initial & terminal states
*************************

When *automaton* is invoked it will automatically transition into its *initial* state. Whenever
the process terminates it will first transition the machine to its *terminal* state. This
state can be reached from any other state and will run last. You can take advantage of this
mechanism to perform some cleanup tasks as an example.


.. include:: links.rst