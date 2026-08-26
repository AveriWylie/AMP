"""Connection recovery and command-execution worker lifecycle."""

import threading
import time


class LifecycleManager:
    def __init__(self, connection, executor, identity):
        self._connection = connection
        self._executor = executor
        self._identity = identity
        self._execution_thread = None

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
        self._connection.disconnect()

    def start_execution(self):
        current = self._execution_thread
        if not self._connection._connected or (current and current.is_alive()):
            return
        self._execution_thread = threading.Thread(
            target=self._execution_loop, daemon=True
        )
        self._execution_thread.start()

    def _execution_loop(self):
        while True:
            try:
                self._executor.execute_queue()
                time.sleep(0.05)
            except Exception as error:
                print(f"Execution error: {error}")
                return

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
