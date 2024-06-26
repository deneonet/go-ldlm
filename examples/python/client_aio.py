#!/usr/bin/python
#
# Copyright 2024 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import sys


def main(argv):
    pass


if __name__ == '__main__':
    main(sys.argv)
"""
python3 -m grpc_tools.protoc -I../../ --python_out=./protos --grpc_python_out=./protos ../../ldlm.proto

"""
import asyncio
import os
import sys
import functools
from contextlib import asynccontextmanager

import grpc
from grpc._channel import _InactiveRpcError
import exceptions

SCRIPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "protos")
sys.path.append(SCRIPT_DIR)

from protos import ldlm_pb2 as pb2
from protos import ldlm_pb2_grpc as pb2grpc


class RefreshLockTimer:
    """
    Timer implementation for refreshing a lock

    Parameters:
            stub (object): The gRPC stub object used to communicate with the server.
            name (str): The name of the lock to refresh.
            key (str): The key associated with the lock to refresh.
            lock_timeout_seconds (int): The timeout in seconds for acquiring the lock.

    """

    def __init__(self, stub: object, name: str, key: str, lock_timeout_seconds: int):
        self.interval = max(lock_timeout_seconds - 30, 10)
        self.fn = functools.partial(refresh_lock, stub, name, key, lock_timeout_seconds)

    async def start(self):
        self.task = asyncio.create_task(self.run())

    async def run(self):
        while True:
            await asyncio.sleep(self.interval)
            await self.fn()

    def cancel(self):
        if self.task is not None and not self.task.done():
            self.task.cancel()


async def refresh_lock(stub, name: str, key: str, lock_timeout_seconds: int):
    """
    Attempts to refresh a lock.

    Args:
            stub (object): The gRPC stub object used to communicate with the server.
            name (str): The name of the lock to refresh.
            key (str): The key associated with the lock to refresh.
            lock_timeout_seconds (int): The timeout in seconds for acquiring the lock.

    Returns:
            object: The response object returned by the gRPC server indicating the result of the lock attempt.
    """
    rpc_msg = pb2.RefreshLockRequest(
        name=name,
        key=key,
        lock_timeout_seconds=lock_timeout_seconds,
    )

    return await rpc_with_retry(stub.RefreshLock, rpc_msg)


async def rpc_with_retry(
    rpc_func: callable,
    rpc_message: object,
    interval_seconds: int = 5,
    max_retries: int = 0,
):
    """
    Executes an RPC call with retries in case of errors.

    :param rpc_func: The RPC function to call.
    :param rpc_message: The message to send in the RPC call.
    :param interval_seconds: (Optional) The interval in seconds between retry attempts. Default is 5 seconds.
    :param max_retries: (Optional) The maximum number of retries. Default is 0 (no retries).

    :return: The response from the RPC call.
    """
    retries = 0
    while True:
        try:
            resp = await rpc_func(rpc_message)
            if resp.HasField("error"):
                raise exceptions.from_rpc_error(resp.error)
            return resp
        except _InactiveRpcError as e:
            if max_retries > 0 and retries > max_retries:
                raise
            print(
                f"Encountered error {e} while trying rpc_call. "
                f"Retrying in {interval_seconds} seconds."
            )
            await asyncio.sleep(interval_seconds)
        retries += 1


@asynccontextmanager
async def lock(
    stub,
    name: str,
    wait_timeout_seconds: int = 0,
    lock_timeout_seconds: int = 0,
    raise_on_wait_timeout: bool = False,
):
    """
    A context manager that attempts to acquire a lock with the given name.

    Args:
        stub (object): The gRPC stub object used to communicate with the server.
        name (str): The name of the lock to acquire.
        wait_timeout_seconds (int, optional): How long to wait to acquire lock. Defaults to 0 - no timeout.
        lock_timeout_seconds (int, optional): The lifetime of the lock in seconds (refresh to renew). Defaults to 0 - no timeout.
        raise_on_wait_timeout (bool, optional): Whether to raise a LockTimeoutError if the wait timeout is exceeded. Defaults to False.

    Yields:
        object: The response object returned by the gRPC server indicating the result of the lock attempt.

    Raises:
        RuntimeError: If the lock cannot be released after being acquired.
        LockTimeoutError: If the wait timeout is exceeded and raise_on_wait_timeout is True.

    Example:
        with lock(stub, "my_lock", wait_timeout_seconds=10, lock_timeout_seconds=600) as response:
            if response.locked:
                # Lock acquired, do something
            else:
                # Lock not acquired, handle accordingly
    """

    rpc_msg = pb2.LockRequest(
        name=name,
    )
    if wait_timeout_seconds:
        rpc_msg.wait_timeout_seconds = wait_timeout_seconds
    if lock_timeout_seconds:
        rpc_msg.lock_timeout_seconds = lock_timeout_seconds

    try:
        r = await rpc_with_retry(stub.Lock, rpc_msg)
    except exceptions.LockTimeoutError:
        if raise_on_wait_timeout:
            raise

    if r.locked and lock_timeout_seconds:
        timer = RefreshLockTimer(stub, name, r.key, lock_timeout_seconds)
        await timer.start()
    else:
        timer = None

    yield r

    if not r.locked:
        return

    if timer:
        timer.cancel()

    rpc_msg = pb2.UnlockRequest(
        name=name,
        key=r.key,
    )

    r = await rpc_with_retry(stub.Unlock, rpc_msg)
    if not r.unlocked:
        raise RuntimeError(f"Failed to unlock {name}")


@asynccontextmanager
async def try_lock(stub, name: str, lock_timeout_seconds: int = 0):
    """
    A context manager that attempts to acquire a lock with the given name.

    Args:
        stub (object): The gRPC stub object used to communicate with the server.
        name (str): The name of the lock to acquire.
        lock_timeout_seconds (int, optional): The lifetime of the lock in seconds (refresh to renew). Defaults to 0 - no timeout.

    Yields:
        object: The response object returned by the gRPC server indicating the result of the lock attempt.

    Raises:
        RuntimeError: If the lock cannot be released after being acquired.

    Example:
        with try_lock(stub, "my_lock", 10) as response:
            if response.locked:
                # Lock acquired, do something
            else:
                # Lock not acquired, handle accordingly
    """
    rpc_msg = pb2.TryLockRequest(
        name=name,
    )
    if lock_timeout_seconds:
        rpc_msg.lock_timeout_seconds = lock_timeout_seconds

    r = await rpc_with_retry(stub.TryLock, rpc_msg)

    if r.locked and lock_timeout_seconds:
        timer = RefreshLockTimer(stub, name, r.key, lock_timeout_seconds)
        await timer.start()
    else:
        timer = None

    yield r

    if not r.locked:
        return

    if timer:
        timer.cancel()

    rpc_msg = pb2.UnlockRequest(
        name=name,
        key=r.key,
    )

    r = await rpc_with_retry(stub.Unlock, rpc_msg)
    if not r.unlocked:
        raise RuntimeError(f"Failed to unlock {name}")


async def run():
    async with grpc.aio.insecure_channel("localhost:3144") as ch:
        stub = pb2grpc.LDLMStub(ch)
        async with try_lock(stub, "work-item-aio1", lock_timeout_seconds=20) as r:
            if r.locked:
                print(f"Lock for {r.name} acquired. Doing some work...")
                await asyncio.sleep(30)
                print("Done!")
            else:
                print("Failed to acquire lock")


if __name__ == "__main__":
    asyncio.run(run())
