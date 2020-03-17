# Copyright 2020 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""To run on CPU with 500 CPU devices:

CUDA_VISIBLE_DEVICES= XLA_FLAGS=--xla_force_host_platform_device_count=500 \
python3 pmap_benchmark.py

To make it run faster, set env var TARGET_TOTAL_SECS to a low number (e.g. 2).
"""
import numpy as onp

from benchmark import benchmark_suite
import jax
import jax.numpy as np
from jax import pmap


def pmap_shard_args_benchmark():
  """Pmap benchmark focusing on shard_args fast path.

  This is intended to measure how long it takes to dispatch a correctly-sharded
  ShardedDeviceArray to pmap.
  """

  def get_benchmark_fn(nargs, nshards):
    pmap_fn = pmap(lambda *args: np.sum(args))
    shape = (nshards, 4)
    args = [onp.random.random(shape) for _ in range(nargs)]
    sharded_args = pmap(lambda x: x)(args)
    assert all(type(arg) == jax.pxla.ShardedDeviceArray for arg in sharded_args)
    def benchmark_fn():
      for _ in range(100):
        pmap_fn(*sharded_args)
    return benchmark_fn

  params = []
  for nargs in (10, 100, 101, 500):
    nshards = min(4, jax.local_device_count())
    params.append({"nargs": nargs, "nshards": nshards})
  for nshards in (2, 4, 8, 100, 500):
    if nshards > jax.local_device_count(): continue
    params.append({"nargs": 10, "nshards": nshards})
  benchmark_suite(get_benchmark_fn, params, "pmap_shard_args")


def pmap_shard_outputs_benchmark():
  """Pmap benchmark focusing on array_result_handler path.

  This is intended to measure how long it takes to construct ShardedDeviceArrays
  from pmap.
  """
  def get_benchmark_fn(nouts, nshards):
    pmap_fn = pmap(lambda x: [x + i for i in range(nouts)])
    shape = (nshards, 4)
    arg = onp.random.random(shape)
    def benchmark_fn():
      for _ in range(100):
        pmap_fn(arg)
    return benchmark_fn

  params = []
  for nouts in (10, 100, 500):
    nshards = min(4, jax.local_device_count())
    params.append({"nouts": nouts, "nshards": nshards})
  for nshards in (2, 4, 8, 100, 500):
    if nshards > jax.local_device_count(): continue
    params.append({"nouts": 10, "nshards": nshards})
  benchmark_suite(get_benchmark_fn, params, "pmap_shard_outputs")


def run_all_benchmarks():
  pmap_shard_args_benchmark()
  pmap_shard_outputs_benchmark()


if __name__ == "__main__":
  run_all_benchmarks()
