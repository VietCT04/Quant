"""One bounded read-ahead block, with cooperative cancellation and error propagation."""

from queue import Empty, Full, Queue
from threading import Event, Thread


def prefetch(source):
    ready = Queue(maxsize=1)
    closed = Event()
    end = object()

    def put(value):
        while not closed.is_set():
            try:
                ready.put(value, timeout=.05)
                return True
            except Full:
                pass
        return False

    def read():
        try:
            for value in source:
                if not put((value, None)):
                    return
        except BaseException as error:
            put((end, error))
        else:
            put((end, None))
        finally:
            source.close()

    thread = Thread(target=read, name="quant-market-prefetch", daemon=True)
    thread.start()
    try:
        while True:
            data, failure = ready.get()
            if failure is not None:
                raise failure
            if data is end:
                break
            yield data
    finally:
        closed.set()
        thread.join()
