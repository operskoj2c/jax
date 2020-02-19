# Copyright 2019 Google LLC
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

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

from absl.testing import absltest
from benchmarks import control

import numpy as onp

from jax import lax
from jax import test_util as jtu
import jax.numpy as np

from jax.config import config
config.parse_flags_with_absl()
FLAGS = config.FLAGS


class ControlBenchmarkTest(jtu.JaxTestCase):

  def testTrajectoryCyclicIntegerCounter(self):
    num_states = 3

    def dynamics(t, x, u):
      return (x + u) % num_states

    T = 10

    U = np.ones((T, 1))
    X = control.trajectory(dynamics, U, np.zeros(1))
    expected = np.arange(T + 1) % num_states
    expected = np.reshape(expected, (T + 1, 1))
    self.assertAllClose(X, expected, check_dtypes=True)

    U = 2 * np.ones((T, 1))
    X = control.trajectory(dynamics, U, np.zeros(1))
    expected = np.cumsum(2 * np.ones(T)) % num_states
    expected = np.concatenate((np.zeros(1), expected))
    expected = np.reshape(expected, (T + 1, 1))
    self.assertAllClose(X, expected, check_dtypes=True)

  def testTrajectoryTimeVarying(self):
    T = 6

    def clip(x, lo, hi):
      return np.minimum(hi, np.maximum(lo, x))

    def dynamics(t, x, u):
      return (x + u) * clip(t - T, 0, 1)

    U = np.ones((2 * T, 1))
    X = control.trajectory(dynamics, U, np.zeros(1))
    expected = np.concatenate((np.zeros(T + 1), np.arange(T)))
    expected = np.reshape(expected, (2 * T + 1, 1))
    self.assertAllClose(X, expected, check_dtypes=True)


  def testTrajectoryCyclicIndicator(self):
    num_states = 3

    def position(x):
      '''finds the index of a standard basis vector, e.g. [0, 1, 0] -> 1'''
      x = np.cumsum(x)
      x = 1 - x
      return np.sum(x, dtype=np.int32)

    def dynamics(t, x, u):
      '''moves  the next standard basis vector'''
      idx = (position(x) + u[0]) % num_states
      return lax.dynamic_slice_in_dim(np.eye(num_states), idx, 1)[0]

    T = 8

    U = np.ones((T, 1), dtype=np.int32)
    X = control.trajectory(dynamics, U, np.eye(num_states, dtype=np.int32)[0])
    expected = np.vstack((np.eye(num_states),) * 3)
    self.assertAllClose(X, expected, check_dtypes=True)


if __name__ == '__main__':
  absltest.main()
