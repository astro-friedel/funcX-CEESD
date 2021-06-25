import argparse
import random
import time
import uuid

import pytest

from funcx import FuncXClient
from funcx.sdk.executor import FuncXExecutor
from funcx.utils.response_errors import EndpointNotFound


def double(x):
    return x * 2


def failing_task():
    raise IndexError()


def delay_n(n):
    import time

    time.sleep(n)
    return "hello"


def noop():
    return


def split(s):
    return [c for c in s]


def merge(obj1, obj2):
    return obj1.update(obj2)


def random_obj():
    obj = {}
    for _ in range(random.randint(5, 10)):
        key = str(uuid.uuid4())
        obj[key] = random.random()
    return obj


def test_simple(fx, endpoint):
    x = random.randint(0, 100)
    fut = fx.submit(double, x, endpoint_id=endpoint)

    assert fut.result() == x * 2, "Got wrong answer"


def test_loop(fx, endpoint):
    count = 10

    futures = []
    for i in range(count):
        future = fx.submit(double, i, endpoint_id=endpoint)
        futures.append(future)

    for fu in futures:
        print(fu.result())


def test_submit_while_waiting(fx, endpoint):
    fut1 = fx.submit(delay_n, 10, endpoint_id=endpoint)
    time.sleep(1)

    x = random.randint(0, 100)
    fut2 = fx.submit(double, x, endpoint_id=endpoint)

    assert fut2.result() == x * 2, "Got wrong answer"
    assert fut1.done() is False, "First task should not be done"
    assert fut1.result() == "hello", "Got wrong answer"


def test_failing_task(fx, endpoint):
    fut = fx.submit(failing_task, endpoint_id=endpoint)
    with pytest.raises(IndexError):
        fut.result()


def test_bad_ep(fx):
    bad_ep = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
    with pytest.raises(EndpointNotFound):
        fx.submit(failing_task, endpoint_id=bad_ep)


def test_noop(fx, endpoint):
    fut = fx.submit(noop, endpoint_id=endpoint)
    assert fut.result() is None, "Got wrong answer"


def test_split(fx, endpoint):
    s = str(uuid.uuid4())
    fut = fx.submit(split, s, endpoint_id=endpoint)
    assert fut.result() == split(s), "Got wrong answer"


def test_many_merge(fx, endpoint):
    expected_results = []
    futs = []
    for _ in range(random.randint(20, 30)):
        obj1 = random_obj()
        obj2 = random_obj()
        expected_result = merge(obj1, obj2)
        fut = fx.submit(merge, obj1, obj2, endpoint_id=endpoint)
        expected_results.append(expected_result)
        futs.append(fut)

    for i in range(len(futs)):
        fut = futs[i]
        expected_result = expected_results[i]
        assert fut.result() == expected_result, "Got wrong answer"


def test_timing(fx, endpoint):
    fut1 = fx.submit(failing_task, endpoint_id=endpoint)
    time.sleep(1)
    test_loop(fx, endpoint)
    s = str(uuid.uuid4())
    fut2 = fx.submit(split, s, endpoint_id=endpoint)
    fut3 = fx.submit(delay_n, 5, endpoint_id=endpoint)
    with pytest.raises(IndexError):
        fut1.result()
    time.sleep(1)
    assert fut2.result() == split(s), "Got wrong answer"
    assert fut3.result() == "hello", "Got wrong answer"


# test locally: python3 test_executor.py -e <endpoint_id>
# test on dev: python3 test_executor.py -s https://api.dev.funcx.org/v2 -w wss://api.dev.funcx.org/ws/v2/ -e <endpoint_id>
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-s",
        "--service_url",
        default="http://localhost:5000/v2",
        help="URL at which the funcx-web-service is hosted",
    )
    parser.add_argument(
        "-w",
        "--ws_uri",
        default="ws://localhost:6000",
        help="WebSocket URI to get task results",
    )
    parser.add_argument(
        "-e",
        "--endpoint_id",
        required=True,
        help="Target endpoint to send functions to",
    )
    args = parser.parse_args()

    fx = FuncXExecutor(
        FuncXClient(funcx_service_address=args.service_url, results_ws_uri=args.ws_uri)
    )

    print("Running simple test")
    test_simple(fx, args.endpoint_id)
    print("Complete")

    print(f"Running a test with a for loop of {args.count} tasks")
    test_loop(fx, args.endpoint_id)
