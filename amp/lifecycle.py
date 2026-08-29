"""Connection recovery and command-execution worker lifecycle."""

import threading
import time


class LifecycleManager:
    TICK_SECONDS = 0.05
    DEATH_RECONNECT_DELAY_SECONDS = 1.0

    def __init__(self, connection, executor, identity, on_idle=None,
                 before_reconnect=None):
        self._connection = connection
        self._executor = executor
        self._identity = identity
        self._on_idle = on_idle
        self._before_reconnect = before_reconnect
        self._execution_thread = None
        self._execution_stop = threading.Event()
        self._reconnect_thread = None
        self._reconnect_lock = threading.Lock()

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

    def _stop_execution(self):
        self._execution_stop.set()
        cancel_pending = getattr(self._executor, "cancel_pending", None)
        if callable(cancel_pending):
            cancel_pending()

    def reconnect_after_death(self):
        """Replace the dead play session without blocking the packet listener."""
        with self._reconnect_lock:
            if self._reconnect_thread and self._reconnect_thread.is_alive():
                return
            self._reconnect_thread = threading.Thread(
                target=self._reconnect_after_death, daemon=True
            )
            self._reconnect_thread.start()

    def _reconnect_after_death(self):
        print("Death detected; reconnecting to rebuild world and entity state")
        self._stop_execution()
        self._connection.disconnect()
        worker = self._execution_thread
        if worker and worker is not threading.current_thread():
            worker.join(1)
        if self._before_reconnect is not None:
            self._before_reconnect()
        time.sleep(self.DEATH_RECONNECT_DELAY_SECONDS)
        self.handle_failure(ConnectionError("death recovery"))

    def start_execution(self):
        current = self._execution_thread
        if not self._connection._connected or (current and current.is_alive()):
            return
        self._execution_stop.clear()
        self._execution_thread = threading.Thread(
            target=self._execution_loop, daemon=True
        )
        self._execution_thread.start()

    def _execution_loop(self):
        while not self._execution_stop.is_set():
            try:
                self._execution_step()
            except Exception as error:
                if self._execution_stop.is_set() or not self._connection._connected:
                    return
                print(f"Execution error: {error}")
                return

    def _execution_step(self):
        started = time.monotonic()
        result = self._executor.execute_queue()
        if (
            result is None
            and self._on_idle is not None
            and not self._execution_stop.is_set()
            and self._on_idle()
        ):
            self._executor.execute_queue()
        if self._execution_stop.is_set():
            return
        self._executor.end_tick()
        remaining = self.TICK_SECONDS - (time.monotonic() - started)
        if remaining > 0:
            time.sleep(remaining)

    def handle_failure(self, error):
        if not isinstance(error, ConnectionError):
            return

        print(
            f"Connection failure: {error}, attempting reconnect "
            "(3 attempts before system shutdown)"
        )
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
