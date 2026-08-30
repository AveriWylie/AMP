# imports
import threading
import time


"""
--------------------------------------------------------------------------------------------
Class Header - Lifecycle manager
--------------------------------------------------------------------------------------------
Owns starting, stopping and recovering the two things that outlive a single call, the socket
connection and the execution worker thread. Bot composes it rather than doing this itself so
that reconnection logic lives in one place instead of being spread across every caller that
might notice a dropped connection.

It holds the connection and executor rather than creating them. Lifecycle decides when they
run, not what they are, which keeps it testable with fakes and keeps transport details out.

identity is only carried for the startup message. It is a tuple rather than three fields
because nothing here interprets it, it is passed through to be printed.

on_idle is the hook for anything that has to happen on a tick where no command was queued,
gravity being the reason it exists. Falling is not something the planner asks for, it happens
to the bot, so it needs a tick to run on that is not driven by the queue.

TICK_SECONDS is the client tick, 20 per second, matching what a real client sends. It is a
class attribute rather than a literal so the loop and anything reasoning about timing agree on
one number.
--------------------------------------------------------------------------------------------
"""
class LifecycleManager:

    TICK_SECONDS = 0.05
    # a moment for the server to finish tearing the dead session down before we knock again
    DEATH_RECONNECT_DELAY_SECONDS = 1.0

    def __init__(self, connection, executor, identity, on_idle=None, before_reconnect=None):
        self._connection = connection
        self._executor = executor
        self._identity = identity
        self._on_idle = on_idle
        self._before_reconnect = before_reconnect
        self._execution_thread = None
        # set to tell the worker to stop, checked at every point it could otherwise send a
        # packet down a socket that is being replaced
        self._execution_stop = threading.Event()
        self._reconnect_thread = None
        self._reconnect_lock = threading.Lock()


    # ConnectionError is separated from everything else because only it is worth retrying.
    # A refused connection may come back, an unexpected error means the state is unknown, so
    # that path disconnects rather than reconnecting on top of a broken setup.
    def start(self):
        try:
            self._connection.connect()
            self.start_execution()
            username, host, port = self._identity
            print(f"Bot '{username}' started on {host}:{port}")
        except ConnectionError as error:
            print(f"Failed to start: {error}")
            self.handle_failure(error)
        except Exception as error:
            print(f"Unexpected error during start: {error}")
            self._connection.disconnect()


    def disconnect(self):
        self._stop_execution()
        self._connection.disconnect()


    # Stops the worker and throws away whatever it had queued. Both matter, a queue left full
    # would replay actions planned for the old session against the new one.
    def _stop_execution(self):
        self._execution_stop.set()
        cancel_pending = getattr(self._executor, "cancel_pending", None)
        # duck-typed so a test double without the method still works
        if callable(cancel_pending):
            cancel_pending()


    """
    --------------------------------------------------------------------------------------------
    Function Header - Death recovery
    --------------------------------------------------------------------------------------------
    Replaces the dead play session without blocking the packet listener.

    Reconnecting is the blunt way to recover from death, and it is here because the alternative
    is worse. A respawn leaves entity IDs reassigned and chunks re-sent, and reconciling all of
    that in place has more ways to go subtly wrong than simply starting the session again.

    The work runs on its own thread because the call arrives from the packet listener, which
    decoded the death. Disconnecting from inside that thread would tear down the socket it is
    reading, so it is handed off and the listener returns immediately.

    The lock plus the liveness check make it idempotent. Several packets can imply death within
    a few milliseconds, and each would otherwise start its own reconnect.

    The worker is joined with a timeout rather than indefinitely, and only when it is not the
    calling thread, so a wedged worker delays recovery by a second instead of deadlocking it.

    before_reconnect runs after the old session is down and before the new one opens, which is
    the only point where clearing tracked state cannot race with packets from either.
    --------------------------------------------------------------------------------------------
    """
    def reconnect_after_death(self):
        # several packets can imply one death, so only the first gets a thread
        with self._reconnect_lock:
            if self._reconnect_thread and self._reconnect_thread.is_alive():
                return

            self._reconnect_thread = threading.Thread(target=self._reconnect_after_death, daemon=True)
            self._reconnect_thread.start()


    def _reconnect_after_death(self):
        print("Death detected; reconnecting to rebuild world and entity state")
        self._stop_execution()
        self._connection.disconnect()
        worker = self._execution_thread

        # bounded, and never joins itself, so a wedged worker costs a second not a deadlock
        if worker and worker is not threading.current_thread():
            worker.join(1)

        # old session down, new one not yet open, the only point clearing state cannot race
        if self._before_reconnect is not None:
            self._before_reconnect()

        time.sleep(self.DEATH_RECONNECT_DELAY_SECONDS)
        # reuses the ordinary retry path rather than duplicating its backoff and logging
        self.handle_failure(ConnectionError("death recovery"))


    """
    --------------------------------------------------------------------------------------------
    Function Header - Execution worker
    --------------------------------------------------------------------------------------------
    Starts the thread that drains the executor's queue. Guarded twice, once on the connection
    being live and once on a thread already running, because start_execution is called both on
    first start and again after every successful reconnect. Without the second guard a
    reconnect would leave two workers draining the same queue.

    The thread is a daemon so a stuck worker cannot hold the interpreter open at shutdown. The
    loop sleeps rather than spinning, and it returns on any exception rather than continuing,
    because a worker that keeps looping after a failure would print the same error forever
    while doing nothing useful.
    --------------------------------------------------------------------------------------------
    """
    def start_execution(self):
        current = self._execution_thread

        if not self._connection._connected or (current and current.is_alive()):
            return

        # cleared here rather than in _stop_execution, so a stop stays in force until a
        # deliberate restart rather than being undone by whatever runs next
        self._execution_stop.clear()
        self._execution_thread = threading.Thread(target=self._execution_loop, daemon=True)
        self._execution_thread.start()


    def _execution_loop(self):

        while not self._execution_stop.is_set():
            try:
                self._execution_step()
            except Exception as error:
                # a stopped worker or a closed socket makes this expected teardown noise, so
                # leave quietly rather than reporting a failure nobody needs to act on
                if self._execution_stop.is_set() or not self._connection._connected:
                    return

                print(f"Execution error: {error}")
                return


    """
    --------------------------------------------------------------------------------------------
    Function Header - Execution step
    --------------------------------------------------------------------------------------------
    One client tick. Runs a queued command if there is one, gives the idle hook a turn if there
    is not, closes the tick, then sleeps out whatever is left of the 50 ms.

    The sleep is the remainder rather than a flat 0.05, so the tick rate stays at 20 per second
    regardless of how long the work took. A flat sleep would make every tick cost 50 ms plus the
    command, drifting further behind the server the busier the bot got.

    on_idle only runs when execute_queue returned None, meaning nothing was queued. That is what
    keeps gravity from fighting deliberate movement, a bot walking a path is not also falling.
    Its return value says whether it enqueued anything, and if it did, that command is executed
    in the same tick rather than waiting for the next one, so a fall drops at the right rate
    instead of half speed.

    end_tick runs on every tick including idle ones, since the tick boundary is something the
    server expects to see regardless of whether the bot did anything.

    Nothing here is guarded, exceptions propagate up to _execution_loop which prints and exits
    the thread. A worker that kept looping after a failure would print the same error 20 times
    a second.
    --------------------------------------------------------------------------------------------
    """
    def _execution_step(self):
        started = time.monotonic()
        result = self._executor.execute_queue()

        # nothing was queued, so let gravity or anything else idle-driven take the tick, and
        # run what it queued immediately rather than losing a tick to it
        if (result is None and self._on_idle is not None and not self._execution_stop.is_set() and self._on_idle()):
            self._executor.execute_queue()

        # checked again, the stop can arrive while the idle hook was running
        if self._execution_stop.is_set():
            return

        # every tick, busy or idle, the server expects the boundary either way
        self._executor.end_tick()
        # sleep the remainder, not a flat 50 ms, or the tick rate drifts with the workload
        remaining = self.TICK_SECONDS - (time.monotonic() - started)

        if remaining > 0:
            time.sleep(remaining)


    """
    --------------------------------------------------------------------------------------------
    Function Header - Reconnection
    --------------------------------------------------------------------------------------------
    Three attempts, then give up. Bounded rather than infinite because a server that has
    refused three times is usually down or has rejected AMP outright, and an unbounded retry
    loop would sit there reconnecting silently while the operator assumes it is playing.

    Every failure path disconnects before the next attempt. Reconnecting on top of a half-open
    socket is what leaves a connection that looks alive and reads nothing.

    Returns immediately on anything that is not a ConnectionError, since the caller has already
    handled those and retrying a protocol or logic error would just reproduce it.
    --------------------------------------------------------------------------------------------
    """
    def handle_failure(self, error):
        if not isinstance(error, ConnectionError):
            return

        print(f"Connection failure: {error}, attempting reconnect (3 attempts before system shutdown)")

        for attempt in range(1, 4):
            try:
                self._connection.connect()
                self.start_execution()
                return
            except ConnectionError as retry_error:
                print(f"Protocol error: {retry_error}.\nDISCONNECTING.")
                self._connection.disconnect()
            except ValueError as retry_error:
                print(f"Protocol error: {retry_error}.\nDISCONNECTING.")
                self._connection.disconnect()
            except Exception as retry_error:
                print(f"Unexpected error: {retry_error}, shutting down")
                self._connection.disconnect()

            if attempt == 3:
                return
