# Copyright 2018 Google LLC
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


import collections
from contextlib import contextmanager
import enum
from functools import partial
import itertools
import warnings

from absl.testing import absltest
from absl.testing import parameterized

import numpy as np

import jax
from jax import dtypes
from jax import numpy as jnp
from jax import ops
from jax import test_util as jtu
from jax._src import util

from jax.config import config
config.parse_flags_with_absl()

# We disable the whitespace continuation check in this file because otherwise it
# makes the test name formatting unwieldy.
# pylint: disable=bad-continuation


ARRAY_MSG = r"Using a non-tuple sequence for multidimensional indexing is not allowed.*arr\[array\(seq\)\]"
TUPLE_MSG = r"Using a non-tuple sequence for multidimensional indexing is not allowed.*arr\[tuple\(seq\)\]"


float_dtypes = jtu.dtypes.floating
default_dtypes = float_dtypes + jtu.dtypes.integer
all_dtypes = default_dtypes + jtu.dtypes.boolean

IndexSpec = collections.namedtuple("IndexTest", ["shape", "indexer"])


def check_grads(f, args, order, atol=None, rtol=None, eps=None):
  # TODO(mattjj,dougalm): add higher-order check
  default_tol = 1e-6 if config.x64_enabled else 1e-2
  atol = atol or default_tol
  rtol = rtol or default_tol
  eps = eps or default_tol
  jtu.check_jvp(f, partial(jax.jvp, f), args, atol, rtol, eps)
  jtu.check_vjp(f, partial(jax.vjp, f), args, atol, rtol, eps)


STATIC_INDEXING_TESTS = [
    ("OneIntIndex", [
        IndexSpec(shape=(3,), indexer=1),
        IndexSpec(shape=(3, 3), indexer=0),
        IndexSpec(shape=(3, 4, 5), indexer=2),
        IndexSpec(shape=(3,), indexer=-1),
        IndexSpec(shape=(3,), indexer=-2),
    ]),
    ("TwoIntIndices", [
        IndexSpec(shape=(3, 3), indexer=(2, 1)),
        IndexSpec(shape=(3, 4, 5), indexer=(1, 2)),
        IndexSpec(shape=(3, 4, 5), indexer=(-1, 2)),
    ]),
    ("ThreeIntIndices", [IndexSpec((3, 4, 5), indexer=(1, 2, 3))]),
    ("OneSliceIndex", [
        IndexSpec(shape=(10,), indexer=slice(1, 3)),
        IndexSpec(shape=(10,), indexer=slice(1, -1)),
        IndexSpec(shape=(10,), indexer=slice(None, -1)),
        IndexSpec(shape=(10,), indexer=slice(None, None, None)),
        IndexSpec(shape=(10, 8), indexer=slice(1, 3)),
        IndexSpec(shape=(10, 8), indexer=slice(1, None)),
        IndexSpec(shape=(10, 8), indexer=slice(None, 3)),
        IndexSpec(shape=(10, 8), indexer=slice(-3, None)),
    ]),
    ("OneSliceIndexNegativeStride", [
        IndexSpec(shape=(10,), indexer=slice(3, 1, -1)),
        IndexSpec(shape=(10,), indexer=slice(1, 8, -1)),  # empty result
        IndexSpec(shape=(10,), indexer=slice(None, 1, -2)),
        IndexSpec(shape=(10,), indexer=slice(None, None, -1)),
        IndexSpec(shape=(10, 8), indexer=slice(3, 1, -1)),
        IndexSpec(shape=(10, 8), indexer=slice(0, 8, -1)),  # empty result
        IndexSpec(shape=(10, 8), indexer=slice(None, None, -1)),
    ]),
    ("OneSliceIndexNonUnitStride", [
        IndexSpec(shape=(10,), indexer=slice(0, 8, 2)),
        IndexSpec(shape=(10,), indexer=slice(0, 8, 3)),
        IndexSpec(shape=(10,), indexer=slice(1, 3, 2)),
        IndexSpec(shape=(10,), indexer=slice(1, None, 2)),
        IndexSpec(shape=(10,), indexer=slice(None, 1, -2)),
        IndexSpec(shape=(10, 8), indexer=slice(1, 8, 3)),
        IndexSpec(shape=(10, 8), indexer=slice(None, None, 2)),
        IndexSpec(shape=(10, 8), indexer=slice(None, 1, -2)),
        IndexSpec(shape=(10, 8), indexer=slice(None, None, -2)),
    ]),
    ("TwoSliceIndices", [
        IndexSpec(shape=(10, 8), indexer=(slice(1, 3), slice(0, 2))),
        IndexSpec(shape=(10, 8), indexer=(slice(1, None), slice(None, 2))),
        IndexSpec(
            shape=(10, 8), indexer=(slice(None, None, -1), slice(None, 2))),
        IndexSpec(shape=(10, 8, 3), indexer=(slice(1, 3), slice(0, 2))),
        IndexSpec(shape=(10, 8, 3), indexer=(slice(1, 3), slice(0, None))),
        IndexSpec(shape=(10, 8, 3), indexer=(slice(1, None), slice(0, 2))),
    ]),
    ("OneColonIndex", [
        IndexSpec(shape=(3,), indexer=slice(None)),
        IndexSpec(shape=(3, 4), indexer=slice(None)),
    ]),
    ("MultipleColonIndices", [
        IndexSpec(shape=(3, 4), indexer=(slice(None), slice(None))),
        IndexSpec(shape=(3, 4, 5), indexer=(slice(None), slice(None))),
    ]),
    ("MixedSliceIndices", [
        IndexSpec(shape=(10, 4), indexer=(slice(None), slice(0, 2))),
        IndexSpec(shape=(10, 4), indexer=(1, slice(None))),
    ]),
    ("EllipsisIndex", [
        IndexSpec(shape=(3,), indexer=Ellipsis),
        IndexSpec(shape=(3, 4), indexer=Ellipsis),
        IndexSpec(shape=(3, 4, 5), indexer=(0, Ellipsis)),
        IndexSpec(shape=(3, 4, 5), indexer=(Ellipsis, 2, 3)),
    ]),
    ("NoneIndex", [
        IndexSpec(shape=(), indexer=None),
        IndexSpec(shape=(), indexer=(None, None)),
        IndexSpec(shape=(), indexer=(Ellipsis, None)),
        IndexSpec(shape=(3,), indexer=None),
        IndexSpec(shape=(3, 4), indexer=None),
        IndexSpec(shape=(3, 4), indexer=(Ellipsis, None)),
        IndexSpec(shape=(3, 4), indexer=(0, None, Ellipsis)),
        IndexSpec(shape=(3, 4, 5), indexer=(1, None, Ellipsis)),
    ]),
    ("EmptyIndex", [
        IndexSpec(shape=(), indexer=()),
        IndexSpec(shape=(3,), indexer=()),
        IndexSpec(shape=(3, 4), indexer=()),
    ]),
    ("TupleOfIntAndSliceAndIntArray", [
        IndexSpec(shape=(3, 2, 3), indexer=(0, slice(None), np.arange(3))),
        IndexSpec(shape=(3, 2, 3), indexer=(np.int32(1), slice(None), np.arange(3))),
        IndexSpec(shape=(3, 2, 3), indexer=(np.array(2), slice(None), np.arange(3))),
    ]),
]


ADVANCED_INDEXING_TESTS = [
    ("One1DIntArrayIndex",
     [IndexSpec(shape=(3,), indexer=np.array([0, 1])),
     IndexSpec(shape=(3, 3), indexer=np.array([1, 2, 1])),
     IndexSpec(shape=(3, 4, 5), indexer=np.array([0, 2, 0, 1])),
     IndexSpec(shape=(3,), indexer=np.array([-1, 1])),
     IndexSpec(shape=(3,), indexer=np.array([-2, -1])),
     IndexSpec(shape=(0,), indexer=np.array([], dtype=np.int32)),
     ]),
    ("One2DIntArrayIndex",
     [IndexSpec(shape=(3,), indexer=np.array([[0, 0]])),
     IndexSpec(shape=(3, 3), indexer=np.array([[1, 2, 1],
                                                [0, 1, -1]])),
     IndexSpec(shape=(3, 4, 5), indexer=np.array([[0, 2, 0, 1],
                                                   [-1, -2, 1, 0]])),
     ]),
    ("Two1DIntArrayIndicesNoBroadcasting",
     [IndexSpec(shape=(3, 3), indexer=(np.array([0, 1]),
                                       np.array([1, 2]))),
     IndexSpec(shape=(3, 4, 5), indexer=(np.array([0, 2, 0, 1]),
                                         np.array([-1, 0, -1, 2]))),
     ]),
    ("Two1DIntArrayIndicesWithBroadcasting",
     [IndexSpec(shape=(3, 3), indexer=(np.array([[0, 1]]),
                                       np.array([1, 2]))),
     IndexSpec(shape=(3, 4, 5), indexer=(np.array([[0, 2, 0, 1]]),
                                         np.array([-1, 0, -1, 2]))),
     ]),
    ("ArrayOfInts",
     [IndexSpec(shape=(3,), indexer=np.array([0, 1, 0])),
     IndexSpec(shape=(3, 4, 5), indexer=np.array([0, -1])),
     ]),
    ("TupleOfListsOfPythonInts",
     [IndexSpec(shape=(3, 4, 5), indexer=([0, 1],)),
     IndexSpec(shape=(3, 4, 5), indexer=([[0], [-1]], [[2, 3, 0, 3]])),
     ]),
    ("TupleOfPythonIntsAndIntArrays",
     [IndexSpec(shape=(3, 4, 5), indexer=(0, np.array([0, 1]))),
     IndexSpec(shape=(3, 4, 5), indexer=(0, 1,
                                         np.array([[2, 3, 0, 3]]))),
     ]),
    ("TupleOfListsOfPythonIntsAndIntArrays",
     [IndexSpec(shape=(3, 4, 5), indexer=([0, 1], np.array([0]))),
     IndexSpec(shape=(3, 4, 5), indexer=([[0], [-1]],
                                         np.array([[2, 3, 0, 3]]))),
     ]),
]

ADVANCED_INDEXING_TESTS_NO_REPEATS = [
    ("One1DIntArrayIndex",
     [IndexSpec(shape=(3,), indexer=np.array([0, 1])),
      IndexSpec(shape=(3, 3), indexer=np.array([1, 2, 0])),
      IndexSpec(shape=(3, 4, 5), indexer=np.array([0, 2, 1])),
      IndexSpec(shape=(3,), indexer=np.array([-1, 1])),
      IndexSpec(shape=(3,), indexer=np.array([-2, -1])),
      IndexSpec(shape=(0,), indexer=np.array([], dtype=np.int32)),
     ]),
    ("One2DIntArrayIndex",
     [IndexSpec(shape=(3,), indexer=np.array([[0, 1]])),
      IndexSpec(shape=(6, 6), indexer=np.array([[1, 2, 0],
                                                 [3, 4, -1]])),
     ]),
    ("Two1DIntArrayIndicesNoBroadcasting",
     [IndexSpec(shape=(3, 3), indexer=(np.array([0, 1]),
                                       np.array([1, 2]))),
      IndexSpec(shape=(4, 5, 6), indexer=(np.array([0, 2, 1, 3]),
                                          np.array([-1, 0, -2, 1]))),
     ]),
    ("Two1DIntArrayIndicesWithBroadcasting",
     [IndexSpec(shape=(3, 3), indexer=(np.array([[0, 1]]),
                                       np.array([1, 2]))),
      IndexSpec(shape=(4, 5, 6), indexer=(np.array([[0, 2, -1, 1]]),
                                          np.array([-1, 0, -2, 2]))),
     ]),
    ("ArrayOfInts",
     [IndexSpec(shape=(3,), indexer=np.array([0, 2, 1])),
      IndexSpec(shape=(3, 4, 5), indexer=np.array([0, -1])),
     ]),
    ("TupleOfListsOfPythonInts",
     [IndexSpec(shape=(3, 4, 5), indexer=([0, 1],)),
      IndexSpec(shape=(3, 4, 5), indexer=([[0], [-1]], [[2, 3, 0]])),
     ]),
    ("TupleOfPythonIntsAndIntArrays",
     [IndexSpec(shape=(3, 4, 5), indexer=(0, np.array([0, 1]))),
      IndexSpec(shape=(3, 4, 5), indexer=(0, 1,
                                          np.array([[2, 3, 0]]))),
     ]),
    ("TupleOfListsOfPythonIntsAndIntArrays",
     [IndexSpec(shape=(3, 4, 5), indexer=([0, 1], np.array([0]))),
      IndexSpec(shape=(3, 4, 5), indexer=([[0], [-1]],
                                          np.array([[2, 3, 0]]))),
     ]),
]

ADVANCED_INDEXING_TESTS_NO_REPEATS_SORTED = [
    ("One1DIntArrayIndex",
     [IndexSpec(shape=(3,), indexer=np.array([0, 1])),
      IndexSpec(shape=(3, 3), indexer=np.array([0, 1, 2])),
      IndexSpec(shape=(3, 4, 5), indexer=np.array([0, 1, 2])),
      IndexSpec(shape=(3,), indexer=np.array([-1, 1])),
      IndexSpec(shape=(3,), indexer=np.array([-2, -1])),
      IndexSpec(shape=(0,), indexer=np.array([], dtype=np.int32)),
     ]),
    ("One2DIntArrayIndex",
     [IndexSpec(shape=(3,), indexer=np.array([[0, 1]])),
      IndexSpec(shape=(6, 6), indexer=np.array([[-1, 0, 1],
                                                 [ 2, 3, 4]])),
     ]),
    ("Two1DIntArrayIndicesNoBroadcasting",
     [IndexSpec(shape=(3, 3), indexer=(np.array([0, 1]),
                                       np.array([1, 2]))),
      IndexSpec(shape=(4, 5, 6), indexer=(np.array([0, 1, 2, 3]),
                                          np.array([-2, -1, 0, 1]))),
     ]),
    ("Two1DIntArrayIndicesWithBroadcasting",
     [IndexSpec(shape=(3, 3), indexer=(np.array([[0, 1]]),
                                       np.array([1, 2]))),
      IndexSpec(shape=(4, 5, 6), indexer=(np.array([[-1, 0, 1, 2]]),
                                          np.array([-2, -1, 0, 2]))),
     ]),
    ("TupleOfListsOfPythonInts",
     [IndexSpec(shape=(3, 4, 5), indexer=([0, 1],)),
      IndexSpec(shape=(3, 4, 5), indexer=([[0], [-1]], [[0, 2, 3]])),
     ]),
    ("TupleOfPythonIntsAndIntArrays",
     [IndexSpec(shape=(3, 4, 5), indexer=(0, np.array([0, 1]))),
      IndexSpec(shape=(3, 4, 5), indexer=(0, 1,
                                          np.array([[0, 2, 3]]))),
     ]),
    ("TupleOfListsOfPythonIntsAndIntArrays",
     [IndexSpec(shape=(3, 4, 5), indexer=([0, 1], np.array([0]))),
      IndexSpec(shape=(3, 4, 5), indexer=([[0], [-1]],
                                          np.array([[0, 2, 3]]))),
     ]),
]

MIXED_ADVANCED_INDEXING_TESTS_NO_REPEATS = [
    ("SlicesAndOneIntArrayIndex",
     [IndexSpec(shape=(2, 3), indexer=(np.array([0, 1]), slice(1, 2))),
     IndexSpec(shape=(2, 3), indexer=(slice(0, 2),
                                      np.array([0, 2]))),
     IndexSpec(shape=(3, 4, 5), indexer=(Ellipsis,
                                         np.array([0, 2]),
                                         slice(None))),
     IndexSpec(shape=(3, 4, 5), indexer=(Ellipsis,
                                         np.array([[0, 2], [1, 3]]),
                                         slice(None))),
     ]),
    ("SlicesAndTwoIntArrayIndices",
     [IndexSpec(shape=(3, 4, 5), indexer=(Ellipsis,
                                          np.array([0, 2]),
                                          np.array([-1, 2]))),
     IndexSpec(shape=(3, 4, 5), indexer=(np.array([0, 2]),
                                         Ellipsis,
                                         np.array([-1, 2]))),
     IndexSpec(shape=(3, 4, 5), indexer=(np.array([0, 2]),
                                         np.array([-1, 2]),
                                         Ellipsis)),
     IndexSpec(shape=(3, 4, 5), indexer=(np.array([0, 2]),
                                         np.array([-1, 2]),
                                         slice(1, 3))),
     IndexSpec(shape=(3, 4, 5), indexer=(np.array([0, 2]),
                                         slice(1, 3),
                                         np.array([-1, 2]))),
     IndexSpec(shape=(3, 4, 5), indexer=(np.array([0, 2, -2]),
                                         slice(None, None, 2),
                                         np.array([-1, 2, 1]))),
     ]),
    ("NonesAndIntArrayIndices",
     [IndexSpec(shape=(3, 4, 5), indexer=(np.array([0, 2]),
                                          None,
                                          np.array([-1, 2]))),
     IndexSpec(shape=(3, 4, 5), indexer=(np.array([0, 2]),
                                         None,
                                         None,
                                         np.array([-1, 2]))),
     IndexSpec(shape=(3, 4, 5), indexer=(Ellipsis,
                                         np.array([0, 2]),
                                         None,
                                         None,
                                         np.array([-1, 2]))),
     ]),
    ("IntArrayWithInt32Type",
     [IndexSpec(shape=(3, 4), indexer=(Ellipsis, np.array(1, dtype=np.int32)))
     ]),
]

MIXED_ADVANCED_INDEXING_TESTS = MIXED_ADVANCED_INDEXING_TESTS_NO_REPEATS + [
    ("SlicesAndOneIntArrayIndex",
     [
     IndexSpec(shape=(3, 4, 5), indexer=(Ellipsis,
                                         np.array([[0, 2], [1, 1]]),
                                         slice(None))),
     ]),
    ("SlicesAndTwoIntArrayIndices",
     [IndexSpec(shape=(3, 4, 5), indexer=(np.array([0, 2, -2]),
                                         slice(None, None, 2),
                                         np.array([-1, 2, -1]))),
      IndexSpec(shape=(3, 4, 5), indexer=(np.array([[0, 2], [2, 0]]),
                                          Ellipsis,
                                          np.array([[1, 0], [1, 0]]))),
     ]),]

@jtu.with_config(jax_numpy_rank_promotion="raise")
class IndexingTest(jtu.JaxTestCase):
  """Tests for Numpy indexing translation rules."""

  @parameterized.named_parameters(jtu.cases_from_list({
      "testcase_name": "{}_inshape={}_indexer={}".format(
          name, jtu.format_shape_dtype_string( shape, dtype), indexer),
       "shape": shape, "dtype": dtype, "indexer": indexer
  } for name, index_specs in STATIC_INDEXING_TESTS
    for shape, indexer in index_specs
    for dtype in all_dtypes))
  def testStaticIndexing(self, shape, dtype, indexer):
    rng = jtu.rand_default(self.rng())
    args_maker = lambda: [rng(shape, dtype)]
    np_fun = lambda x: np.asarray(x)[indexer]
    jnp_fun = lambda x: jnp.asarray(x)[indexer]
    self._CheckAgainstNumpy(np_fun, jnp_fun, args_maker)
    self._CompileAndCheck(jnp_fun, args_maker)

  @parameterized.named_parameters(jtu.cases_from_list({
      "testcase_name": "{}_inshape={}_indexer={}".format(
          name, jtu.format_shape_dtype_string( shape, dtype), indexer),
       "shape": shape, "dtype": dtype, "indexer": indexer
  } for name, index_specs in STATIC_INDEXING_TESTS
    for shape, indexer in index_specs
    for dtype in all_dtypes))
  def testStaticIndexingWithAtGet(self, shape, dtype, indexer):
    rng = jtu.rand_default(self.rng())
    args_maker = lambda: [rng(shape, dtype)]
    np_fun = lambda x: np.asarray(x)[indexer]
    jnp_fun = lambda x: jnp.asarray(x).at[indexer].get()
    self._CheckAgainstNumpy(np_fun, jnp_fun, args_maker)
    self._CompileAndCheck(jnp_fun, args_maker)

  @parameterized.named_parameters({
      "testcase_name":
          "{}_inshape={}_indexer={}".format(name,
                                            jtu.format_shape_dtype_string(
                                                shape, dtype), indexer),
      "shape": shape, "dtype": dtype, "indexer": indexer
  } for name, index_specs in STATIC_INDEXING_TESTS
    for shape, indexer in index_specs
    for dtype in float_dtypes)
  def testStaticIndexingGrads(self, shape, dtype, indexer):
    rng = jtu.rand_default(self.rng())
    tol = 1e-2 if jnp.finfo(dtype).bits == 32 else None
    arg = rng(shape, dtype)
    fun = lambda x: jnp.asarray(x)[indexer]**2
    check_grads(fun, (arg,), 2, tol, tol, tol)

  def _ReplaceSlicesWithTuples(self, idx):
    """Helper method to replace slices with tuples for dynamic indexing args."""
    if isinstance(idx, slice):
      triple = idx.start, idx.stop, idx.step
      isnone = [i for i, elt in enumerate(triple) if elt is None]
      zeros = itertools.repeat(0)
      nones = itertools.repeat(None)
      out = util.subvals(triple, zip(isnone, zeros))
      return out, lambda out: slice(*util.subvals(out, zip(isnone, nones)))
    elif isinstance(idx, (tuple, list)) and idx:
      t = type(idx)
      elts, packs = zip(*map(self._ReplaceSlicesWithTuples, idx))
      return elts, lambda elts: t((pack(i) for pack, i in zip(packs, elts)))
    else:
      return idx, lambda x: x

  @parameterized.named_parameters(
      {"testcase_name": "{}_inshape={}_indexer={}"
       .format(name, jtu.format_shape_dtype_string(shape, dtype), indexer),
       "shape": shape, "dtype": dtype, "indexer": indexer}
      for name, index_specs in [
          ("OneSliceIndex",
           [IndexSpec(shape=(5,), indexer=slice(1, 3)),
            IndexSpec(shape=(5, 4), indexer=slice(1, 3))]),
          ("TwoSliceIndices",
           [IndexSpec(shape=(5, 4), indexer=(slice(1, 3), slice(0, 2))),
            IndexSpec(shape=(5, 4, 3), indexer=(slice(1, 3), slice(0, 2)))]),
          ("NonUnitStrides", [
              IndexSpec(shape=(3,), indexer=slice(None, None, -1)),
              IndexSpec(shape=(3, 3), indexer=slice(0, 3, -2)),
              IndexSpec(shape=(3, 4, 5), indexer=slice(0, 4, 2))
          ]),
          ("OnlyStartOrStopDynamic", [
              IndexSpec(shape=(5, 4), indexer=(slice(None, 3), slice(0, 2))),
              IndexSpec(shape=(5, 4, 3), indexer=(slice(1, 3), slice(0, None)))
          ]),
      ]
      for shape, indexer in index_specs
      for dtype in all_dtypes)
  def testDynamicIndexingWithSlicesErrors(self, shape, dtype, indexer):
    rng = jtu.rand_default(self.rng())
    unpacked_indexer, pack_indexer = self._ReplaceSlicesWithTuples(indexer)

    @jax.jit
    def fun(x, unpacked_indexer):
      indexer = pack_indexer(unpacked_indexer)
      return x[indexer]

    args_maker = lambda: [rng(shape, dtype), unpacked_indexer]
    self.assertRaises(IndexError, lambda: fun(*args_maker()))

  @parameterized.named_parameters(
      {"testcase_name": "{}_inshape={}_indexer={}"
       .format(name, jtu.format_shape_dtype_string(shape, dtype), indexer),
       "shape": shape, "dtype": dtype, "indexer": indexer}
      for name, index_specs in [
          ("OneIntIndex",
           [IndexSpec(shape=(3,), indexer=1),
            IndexSpec(shape=(3, 3), indexer=0),
            IndexSpec(shape=(3, 4, 5), indexer=2),
            IndexSpec(shape=(3,), indexer=-1),
            IndexSpec(shape=(3,), indexer=-2)]),
          ("TwoIntIndices",
           [IndexSpec(shape=(3, 3), indexer=(2, 1)),
            IndexSpec(shape=(3, 4, 5), indexer=(1, 2)),
            IndexSpec(shape=(3, 4, 5), indexer=(-1, 2))]),
          ("ThreeIntIndices",
           [IndexSpec((3, 4, 5), indexer=(1, 2, 3))]),
      ]
      for shape, indexer in index_specs
      for dtype in all_dtypes)
  def testDynamicIndexingWithIntegers(self, shape, dtype, indexer):
    rng = jtu.rand_default(self.rng())
    unpacked_indexer, pack_indexer = self._ReplaceSlicesWithTuples(indexer)

    def np_fun(x, unpacked_indexer):
      indexer = pack_indexer(unpacked_indexer)
      return np.asarray(x)[indexer]

    def jnp_fun(x, unpacked_indexer):
      indexer = pack_indexer(unpacked_indexer)
      return jnp.array(x)[indexer]

    args_maker = lambda: [rng(shape, dtype), unpacked_indexer]
    self._CheckAgainstNumpy(np_fun, jnp_fun, args_maker)
    self._CompileAndCheck(jnp_fun, args_maker)

  @parameterized.named_parameters(
      {"testcase_name": "{}_inshape={}_indexer={}"
       .format(name, jtu.format_shape_dtype_string(shape, dtype), indexer),
       "shape": shape, "dtype": dtype, "indexer": indexer}
      for name, index_specs in [
          ("OneIntIndex",
           [IndexSpec(shape=(3,), indexer=1),
            IndexSpec(shape=(3, 3), indexer=0),
            IndexSpec(shape=(3, 4, 5), indexer=2),
            IndexSpec(shape=(3,), indexer=-1),
            IndexSpec(shape=(3,), indexer=-2),
            ]),
          ("TwoIntIndices",
           [IndexSpec(shape=(3, 3), indexer=(2, 1)),
            IndexSpec(shape=(3, 4, 5), indexer=(1, 2)),
            IndexSpec(shape=(3, 4, 5), indexer=(-1, 2)),
            ]),
          ("ThreeIntIndices",
           [IndexSpec((3, 4, 5), indexer=(1, 2, 3))]),
      ]
      for shape, indexer in index_specs
      for dtype in float_dtypes)
  def testDynamicIndexingWithIntegersGrads(self, shape, dtype, indexer):
    rng = jtu.rand_default(self.rng())
    tol = 1e-2 if jnp.finfo(dtype).bits == 32 else None
    unpacked_indexer, pack_indexer = self._ReplaceSlicesWithTuples(indexer)

    @jax.jit
    def fun(unpacked_indexer, x):
      indexer = pack_indexer(unpacked_indexer)
      return x[indexer]

    arr = rng(shape, dtype)
    check_grads(partial(fun, unpacked_indexer), (arr,), 2, tol, tol, tol)

  @parameterized.named_parameters(
      {"testcase_name": "{}_inshape={}_indexer={}"
       .format(name, jtu.format_shape_dtype_string(shape, dtype), indexer),
       "shape": shape, "dtype": dtype, "indexer": indexer}
      for name, index_specs in ADVANCED_INDEXING_TESTS
      for shape, indexer in index_specs
      for dtype in all_dtypes)
  def testAdvancedIntegerIndexing(self, shape, dtype, indexer):
    rng = jtu.rand_default(self.rng())
    args_maker = lambda: [rng(shape, dtype), indexer]
    np_fun = lambda x, idx: np.asarray(x)[idx]
    jnp_fun = lambda x, idx: jnp.asarray(x)[idx]
    self._CheckAgainstNumpy(np_fun, jnp_fun, args_maker)
    self._CompileAndCheck(jnp_fun, args_maker)

  @parameterized.named_parameters(
      {"testcase_name": "{}_inshape={}_indexer={}"
       .format(name, jtu.format_shape_dtype_string(shape, dtype), indexer),
       "shape": shape, "dtype": dtype, "indexer": indexer}
      for name, index_specs in [
          ("One1DIntArrayIndex",
           [IndexSpec(shape=(3,), indexer=np.array([0, 1])),
            IndexSpec(shape=(3, 3), indexer=np.array([1, 2, 1])),
            IndexSpec(shape=(3, 4, 5), indexer=np.array([0, 2, 0, 1])),
            IndexSpec(shape=(3,), indexer=np.array([-1, 1])),
            IndexSpec(shape=(3,), indexer=np.array([-2, -1])),
            ]),
          ("One2DIntArrayIndex",
           [IndexSpec(shape=(3,), indexer=np.array([[0, 0]])),
            IndexSpec(shape=(3, 3), indexer=np.array([[1, 2, 1],
                                                       [0, 1, -1]])),
            IndexSpec(shape=(3, 4, 5), indexer=np.array([[0, 2, 0, 1],
                                                          [-1, -2, 1, 0]])),
            ]),
          ("Two1DIntArrayIndicesNoBroadcasting",
           [IndexSpec(shape=(3, 3), indexer=(np.array([0, 1]),
                                             np.array([1, 2]))),
            IndexSpec(shape=(3, 4, 5), indexer=(np.array([0, 2, 0, 1]),
                                                np.array([-1, 0, -1, 2]))),
            ]),
          ("Two1DIntArrayIndicesWithBroadcasting",
           [IndexSpec(shape=(3, 3), indexer=(np.array([[0, 1]]),
                                             np.array([1, 2]))),
            IndexSpec(shape=(3, 4, 5), indexer=(np.array([[0, 2, 0, 1]]),
                                                np.array([-1, 0, -1, 2]))),
            ]),
          ("TupleOfPythonIntsAndIntArrays",
           [IndexSpec(shape=(3, 4, 5), indexer=(0, np.array([0, 1]))),
            IndexSpec(shape=(3, 4, 5), indexer=(0, 1,
                                                np.array([[2, 3, 0, 3]]))),
            ]),
          ("TupleOfListsOfPythonIntsAndIntArrays",
           [IndexSpec(shape=(3, 4, 5), indexer=([0, 1], np.array([0]))),
            IndexSpec(shape=(3, 4, 5), indexer=([[0], [-1]],
                                                np.array([[2, 3, 0, 3]]))),
            ]),
      ]
      for shape, indexer in index_specs
      for dtype in float_dtypes)
  def testAdvancedIntegerIndexingGrads(self, shape, dtype, indexer):
    rng = jtu.rand_default(self.rng())
    tol = 1e-2 if jnp.finfo(dtype).bits == 32 else None
    arg = rng(shape, dtype)
    fun = lambda x: jnp.asarray(x)[indexer]
    check_grads(fun, (arg,), 2, tol, tol, eps=1.)

  @parameterized.named_parameters(
      {"testcase_name": "{}_inshape={}_indexer={}"
       .format(name, jtu.format_shape_dtype_string(shape, dtype), indexer),
       "shape": shape, "dtype": dtype, "indexer": indexer}
      for name, index_specs in MIXED_ADVANCED_INDEXING_TESTS
      for shape, indexer in index_specs
      for dtype in all_dtypes)
  def testMixedAdvancedIntegerIndexing(self, shape, dtype, indexer):
    rng = jtu.rand_default(self.rng())
    indexer_with_dummies = [e if isinstance(e, np.ndarray) else ()
                            for e in indexer]
    substitutes = [(i, e) for i, e in enumerate(indexer)
                   if not isinstance(e, np.ndarray)]
    args_maker = lambda: [rng(shape, dtype), indexer_with_dummies]

    def jnp_fun(x, indexer_with_dummies):
      idx = type(indexer)(util.subvals(indexer_with_dummies, substitutes))
      return jnp.asarray(x)[idx]

    def np_fun(x, indexer_with_dummies):
      idx = type(indexer)(util.subvals(indexer_with_dummies, substitutes))
      return np.asarray(x)[idx]

    self._CheckAgainstNumpy(np_fun, jnp_fun, args_maker)
    self._CompileAndCheck(jnp_fun, args_maker)

  def testAdvancedIndexingManually(self):
    x = np.random.RandomState(0).randn(3, 4, 5)
    index_array = np.array([0, 2, -1, 0])

    op = lambda x, index_array: x[..., index_array, :]
    cop = jax.jit(op)

    a1 = op(x, index_array)
    a2 = cop(x, index_array)

    self.assertAllClose(a1, a2)

    op = lambda x, index_array: x[..., index_array, :, index_array, None]
    cop = jax.jit(op)

    a1 = op(x, index_array)
    a2 = cop(x, index_array)

    self.assertAllClose(a1, a2)

    op = lambda x, index_array: x[index_array, ..., index_array[:, None], None]
    cop = jax.jit(op)

    a1 = op(x, index_array)
    a2 = cop(x, index_array)

    self.assertAllClose(a1, a2)

  def testUnpacking(self):

    def foo(x):
      a, b, c = x
      return a + b + c

    cfoo = jax.jit(foo)

    a1 = foo(np.arange(3))
    a2 = cfoo(np.arange(3))

    self.assertAllClose(a1, a2)

  def testBooleanIndexingArray1D(self):
    idx = np.array([True, True, False])
    x = jax.device_put(np.arange(3))
    ans = x[idx]
    expected = np.arange(3)[idx]
    self.assertAllClose(ans, expected, check_dtypes=False)

  def testBooleanIndexingList1D(self):
    idx = [True, True, False]
    x = jax.device_put(np.arange(3))
    with self.assertRaisesRegex(TypeError, ARRAY_MSG):
      x[idx]

  def testBooleanIndexingArray2DBroadcast(self):
    idx = np.array([True, True, False, True])
    x = np.arange(8).reshape(4, 2)
    ans = jax.device_put(x)[idx]
    expected = x[idx]
    self.assertAllClose(ans, expected, check_dtypes=False)

  def testBooleanIndexingList2DBroadcast(self):
    idx = [True, True, False, True]
    x = np.arange(8).reshape(4, 2)
    with self.assertRaisesRegex(TypeError, ARRAY_MSG):
      jax.device_put(x)[idx]

  def testBooleanIndexingArray2D(self):
    idx = np.array([[True, False],
                     [False, True],
                     [False, False],
                     [True, True]])
    x = np.arange(8).reshape(4, 2)
    ans = jax.device_put(x)[idx]
    expected = x[idx]
    self.assertAllClose(ans, expected, check_dtypes=False)

  def testBooleanIndexingDynamicShapeError(self):
    x = np.zeros(3)
    i = np.array([True, True, False])
    self.assertRaises(IndexError, lambda: jax.jit(lambda x, i: x[i])(x, i))

  def testScalarBooleanIndexingNotImplemented(self):
    msg = "JAX arrays do not support boolean scalar indices"
    with self.assertRaisesRegex(TypeError, msg):
      jnp.arange(4)[True]
    with self.assertRaisesRegex(TypeError, msg):
      jnp.arange(4)[False]

  def testIssue187(self):
    x = jnp.ones((5, 5))
    x[[0, 2, 4], [0, 2, 4]]  # doesn't crash

    x = np.arange(25).reshape((5, 5))
    ans = jax.jit(lambda x: x[[0, 2, 4], [0, 2, 4]])(x)
    expected = x[[0, 2, 4], [0, 2, 4]]
    self.assertAllClose(ans, expected, check_dtypes=False)

  def testJVPOfGradOfIndexing(self):
    # Should return a value, even though we didn't pass a symbolic zero as the
    # index tangent.
    x = jnp.ones((3, 4), jnp.float32)
    i = jnp.ones((3,), jnp.int32)
    f = lambda x, i: jnp.sum(x[i])
    primals, tangents = jax.jvp(jax.grad(f), (x, i),
                                (x, np.zeros(i.shape, dtypes.float0)))
    expected = np.broadcast_to(
      np.array([0, 3, 0], dtype=np.float32)[:, None], (3, 4))
    self.assertAllClose(expected, primals)
    self.assertAllClose(np.zeros_like(x), tangents)

  def testTrivialGatherIsntGenerated(self):
    # https://github.com/google/jax/issues/1621
    jaxpr = jax.make_jaxpr(lambda x: x[:, None])(np.arange(4))
    self.assertEqual(len(jaxpr.jaxpr.eqns), 1)
    self.assertNotIn('gather', str(jaxpr))

  def testIndexingEmptyDimension(self):
    # Issue 2671: XLA error when indexing into dimension of size 0
    x = jnp.ones((2, 0))
    # The following work, even on axis 1 of size 0
    with jax.numpy_rank_promotion('allow'):
      _ = x[0, :] + x[0, None] + x[0, 1:] + x[0, 1:3:2]

    with self.assertRaisesRegex(IndexError,
                                "index .* is out of bounds for axis .* with size 0"):
      _ = np.ones((2, 0))[0, 0]  # The numpy error
    with self.assertRaisesRegex(IndexError,
                                "index is out of bounds for axis .* with size 0"):
      _ = x[0, 0]  # JAX indexing
    with self.assertRaisesRegex(IndexError,
                                "index is out of bounds for axis .* with size 0"):
      jax.jit(lambda i: x[0, i])(0)  # JAX indexing under jit

  def testBooleanIndexingWithEmptyResult(self):
    # based on a TensorFlow Probability test that started failing after #1622
    x = jnp.array([-1])
    mask = jnp.array([False])
    ans = x[mask]  # doesn't crash

    expected =  np.array([-1])[np.array([False])]
    self.assertAllClose(ans, expected, check_dtypes=False)

  def testBooleanIndexingShapeMismatch(self):
    # Regression test for https://github.com/google/jax/issues/7329
    x = jnp.arange(4)
    idx = jnp.array([True, False])
    with self.assertRaisesRegex(IndexError, "boolean index did not match shape.*"):
      x[idx]

  def testNontrivialBooleanIndexing(self):
    # Test nontrivial corner case in boolean indexing shape validation
    rng = jtu.rand_default(self.rng())
    index = (rng((2, 3), np.bool_), rng((6,), np.bool_))

    args_maker = lambda: [rng((2, 3, 6), np.int32)]
    np_fun = lambda x: np.asarray(x)[index]
    jnp_fun = lambda x: jnp.asarray(x)[index]

    self._CheckAgainstNumpy(np_fun, jnp_fun, args_maker)
    self._CompileAndCheck(jnp_fun, args_maker)

  def testFloatIndexingError(self):
    BAD_INDEX_TYPE_ERROR = "Indexer must have integer or boolean type, got indexer with type"
    with self.assertRaisesRegex(TypeError, BAD_INDEX_TYPE_ERROR):
      jnp.zeros(2)[0.]
    with self.assertRaisesRegex(TypeError, BAD_INDEX_TYPE_ERROR):
      jnp.zeros((2, 2))[(0, 0.)]
    with self.assertRaisesRegex(TypeError, BAD_INDEX_TYPE_ERROR):
      jnp.zeros((2, 2))[(0, 0.)]
    with self.assertRaisesRegex(TypeError, BAD_INDEX_TYPE_ERROR):
      jax.jit(lambda idx: jnp.zeros((2, 2))[idx])((0, 0.))
    with self.assertRaisesRegex(TypeError, BAD_INDEX_TYPE_ERROR):
      ops.index_add(jnp.zeros(2), 0., 1.)
    with self.assertRaisesRegex(TypeError, BAD_INDEX_TYPE_ERROR):
      ops.index_update(jnp.zeros(2), 0., 1.)

  def testIndexOutOfBounds(self):  # https://github.com/google/jax/issues/2245
    array = jnp.ones(5)
    self.assertAllClose(array, array[:10])


def _broadcastable_shapes(shape):
  """Returns all shapes that broadcast to `shape`."""
  def f(rshape):
    yield []
    if rshape:
      for s in f(rshape[1:]):
        yield rshape[0:1] + s
      if rshape[0] != 1:
        for s in f(rshape[1:]):
          yield [1] + s
  for x in f(list(reversed(shape))):
    yield list(reversed(x))


def _update_shape(shape, indexer):
  return np.zeros(shape)[indexer].shape


class UpdateOps(enum.Enum):
  UPDATE = 0
  ADD = 1
  MUL = 2
  DIV = 3
  POW = 4
  MIN = 5
  MAX = 6

  def np_fn(op, indexer, x, y):
    x = x.copy()
    x[indexer] = {
      UpdateOps.UPDATE: lambda: y,
      UpdateOps.ADD: lambda: x[indexer] + y,
      UpdateOps.MUL: lambda: x[indexer] * y,
      UpdateOps.DIV: jtu.ignore_warning(category=RuntimeWarning)(
        lambda: x[indexer] / y.astype(x.dtype)),
      UpdateOps.POW: jtu.ignore_warning(category=RuntimeWarning)(
        lambda: x[indexer] ** y.astype(x.dtype)),
      UpdateOps.MIN: lambda: np.minimum(x[indexer], y),
      UpdateOps.MAX: lambda: np.maximum(x[indexer], y),
    }[op]()
    return x

  def jax_fn(op, indexer, x, y, indices_are_sorted=False,
             unique_indices=False):
    return {
      UpdateOps.UPDATE: ops.index_update,
      UpdateOps.ADD: ops.index_add,
      UpdateOps.MUL: ops.index_mul,
      UpdateOps.MIN: ops.index_min,
      UpdateOps.MAX: ops.index_max,
    }[op](x, indexer, y, indices_are_sorted=indices_are_sorted,
          unique_indices=unique_indices)

  def sugar_fn(op, indexer, x, y, indices_are_sorted=False,
             unique_indices=False):
    x = jnp.array(x)
    return {
      UpdateOps.UPDATE: x.at[indexer].set,
      UpdateOps.ADD: x.at[indexer].add,
      UpdateOps.MUL: x.at[indexer].multiply,
      UpdateOps.DIV: x.at[indexer].divide,
      UpdateOps.POW: x.at[indexer].power,
      UpdateOps.MIN: x.at[indexer].min,
      UpdateOps.MAX: x.at[indexer].max,
    }[op](y, indices_are_sorted=indices_are_sorted,
          unique_indices=unique_indices)

  def dtypes(op):
    if op == UpdateOps.UPDATE:
      return all_dtypes
    elif op == UpdateOps.DIV or op == UpdateOps.POW:
      return jtu.dtypes.inexact
    else:
      return default_dtypes

def _update_tol(op):
  if op == UpdateOps.POW:
    tol = {np.complex64: 1e-4 if jtu.device_under_test() == "tpu" else 1e-5,
           np.complex128: 1e-14}
  else:
    tol = {np.complex128: 1e-14}
  return tol

@jtu.with_config(jax_numpy_rank_promotion="raise")
class IndexedUpdateTest(jtu.JaxTestCase):

  @parameterized.named_parameters(jtu.named_cases_from_sampler(lambda s: ({
      "testcase_name": "{}_inshape={}_indexer={}_update={}_sugared={}_op={}".format(
          name, jtu.format_shape_dtype_string(shape, dtype), indexer,
          jtu.format_shape_dtype_string(update_shape, update_dtype), sugared, op.name),
       "shape": shape, "dtype": dtype, "indexer": indexer,
       "update_shape": update_shape, "update_dtype": update_dtype,
       "op": op, "sugared": sugared
  } for name, index_specs in s(STATIC_INDEXING_TESTS)
    for shape, indexer in s(index_specs)
    for op in s(UpdateOps)
    for dtype in s(UpdateOps.dtypes(op))
    for update_shape in s(_broadcastable_shapes(_update_shape(shape, indexer)))
    for update_dtype in s([dtype] if op == UpdateOps.ADD else all_dtypes)
    for sugared in (s([True, False]) if op not in [UpdateOps.DIV, UpdateOps.POW] else [True]))))
  def testStaticIndexing(self, shape, dtype, update_shape, update_dtype,
                         indexer, sugared, op):
    rng = jtu.rand_default(self.rng())
    args_maker = lambda: [rng(shape, dtype), rng(update_shape, update_dtype)]
    np_fn = lambda x, y: UpdateOps.np_fn(op, indexer, x, y)
    if sugared:
      jax_fn = lambda x, y: UpdateOps.sugar_fn(op, indexer, x, y)
    else:
      jax_fn = lambda x, y: UpdateOps.jax_fn(op, indexer, x, y)
    self._CheckAgainstNumpy(np_fn, jax_fn, args_maker, tol=_update_tol(op))
    self._CompileAndCheck(jax_fn, args_maker)

  @parameterized.named_parameters(jtu.named_cases_from_sampler(lambda s: ({
      "testcase_name": "{}_inshape={}_indexer={}_update={}_op={}".format(
          name, jtu.format_shape_dtype_string(shape, dtype), indexer,
          jtu.format_shape_dtype_string(update_shape, update_dtype), op.name),
       "shape": shape, "dtype": dtype, "indexer": indexer,
       "update_shape": update_shape, "update_dtype": update_dtype,
       "op": op
  } for name, index_specs in s(ADVANCED_INDEXING_TESTS_NO_REPEATS)
    for shape, indexer in s(index_specs)
    for op in s(UpdateOps)
    for dtype in s(UpdateOps.dtypes(op))
    for update_shape in s(_broadcastable_shapes(_update_shape(shape, indexer)))
    for update_dtype in s([dtype] if op == UpdateOps.ADD else all_dtypes))))
  def testAdvancedIndexing(self, shape, dtype, update_shape, update_dtype,
                           indexer, op):
    rng = jtu.rand_default(self.rng())
    args_maker = lambda: [rng(shape, dtype), rng(update_shape, update_dtype)]
    np_fn = lambda x, y: UpdateOps.np_fn(op, indexer, x, y)
    jax_fn = lambda x, y: UpdateOps.sugar_fn(op, indexer, x, y,
                                             unique_indices=True)
    self._CheckAgainstNumpy(np_fn, jax_fn, args_maker, tol=_update_tol(op))
    self._CompileAndCheck(jax_fn, args_maker)

  @parameterized.named_parameters(jtu.named_cases_from_sampler(lambda s: ({
      "testcase_name": "{}_inshape={}_indexer={}_update={}_op={}".format(
          name, jtu.format_shape_dtype_string(shape, dtype), indexer,
          jtu.format_shape_dtype_string(update_shape, update_dtype), op.name),
       "shape": shape, "dtype": dtype, "indexer": indexer,
       "update_shape": update_shape, "update_dtype": update_dtype,
       "op": op
  } for name, index_specs in s(ADVANCED_INDEXING_TESTS_NO_REPEATS_SORTED)
    for shape, indexer in s(index_specs)
    for op in s(UpdateOps)
    for dtype in s(UpdateOps.dtypes(op))
    for update_shape in s(_broadcastable_shapes(_update_shape(shape, indexer)))
    for update_dtype in s([dtype] if op == UpdateOps.ADD else all_dtypes))))
  def testAdvancedIndexingSorted(self, shape, dtype, update_shape, update_dtype,
                           indexer, op):
    rng = jtu.rand_default(self.rng())
    args_maker = lambda: [rng(shape, dtype), rng(update_shape, update_dtype)]
    np_fn = lambda x, y: UpdateOps.np_fn(op, indexer, x, y)
    jax_fn = lambda x, y: UpdateOps.sugar_fn(
      op, indexer, x, y, indices_are_sorted=True, unique_indices=True)
    self._CheckAgainstNumpy(np_fn, jax_fn, args_maker, check_dtypes=True,
                            tol=_update_tol(op))
    self._CompileAndCheck(jax_fn, args_maker, check_dtypes=True)

  @parameterized.named_parameters(jtu.named_cases_from_sampler(lambda s: ({
      "testcase_name": "{}_inshape={}_indexer={}_update={}_op={}".format(
          name, jtu.format_shape_dtype_string(shape, dtype), indexer,
          jtu.format_shape_dtype_string(update_shape, update_dtype), op.name),
       "shape": shape, "dtype": dtype, "indexer": indexer,
       "update_shape": update_shape, "update_dtype": update_dtype,
       "op": op
  } for name, index_specs in s(MIXED_ADVANCED_INDEXING_TESTS_NO_REPEATS)
    for shape, indexer in s(index_specs)
    for op in s(UpdateOps)
    for dtype in s(UpdateOps.dtypes(op))
    for update_shape in s(_broadcastable_shapes(_update_shape(shape, indexer)))
    for update_dtype in s([dtype] if op == UpdateOps.ADD else all_dtypes))))
  def testMixedAdvancedIndexing(self, shape, dtype, update_shape, update_dtype,
                                indexer, op):
    rng = jtu.rand_default(self.rng())
    args_maker = lambda: [rng(shape, dtype), rng(update_shape, update_dtype)]
    np_fn = lambda x, y: UpdateOps.np_fn(op, indexer, x, y)
    jax_fn = lambda x, y: UpdateOps.sugar_fn(op, indexer, x, y)
    self._CheckAgainstNumpy(np_fn, jax_fn, args_maker, tol=_update_tol(op))
    self._CompileAndCheck(jax_fn, args_maker)

  @parameterized.named_parameters(jtu.cases_from_list({
      "testcase_name": "{}_inshape={}_indexer={}_update={}_op={}".format(
          name, jtu.format_shape_dtype_string(shape, dtype), indexer,
          jtu.format_shape_dtype_string(update_shape, update_dtype), op.name),
       "shape": shape, "dtype": dtype, "indexer": indexer,
       "update_shape": update_shape, "update_dtype": update_dtype,
       "op": op
  } for name, index_specs in STATIC_INDEXING_TESTS
    for shape, indexer in index_specs
    for op in [UpdateOps.ADD, UpdateOps.MUL, UpdateOps.UPDATE]
    for dtype in float_dtypes
    for update_shape in _broadcastable_shapes(_update_shape(shape, indexer))
    for update_dtype in ([dtype] if op == UpdateOps.ADD else float_dtypes)))
  def testStaticIndexingGrads(self, shape, dtype, update_shape, update_dtype,
                              indexer, op):
    rng = jtu.rand_default(self.rng())
    jax_fn = lambda x, y: UpdateOps.sugar_fn(op, indexer, x, y)
    x = rng(shape, dtype)
    y = rng(update_shape, update_dtype)
    check_grads(jax_fn, (x, y), 2, rtol=1e-3, atol=1e-3, eps=1.)

  @parameterized.named_parameters(jtu.named_cases_from_sampler(lambda s: ({
      "testcase_name": "{}_inshape={}_indexer={}_update={}_op={}".format(
          name, jtu.format_shape_dtype_string(shape, dtype), indexer,
          jtu.format_shape_dtype_string(update_shape, update_dtype), op.name),
       "shape": shape, "dtype": dtype, "indexer": indexer,
       "update_shape": update_shape, "update_dtype": update_dtype,
       "op": op
  } for name, index_specs in s(ADVANCED_INDEXING_TESTS_NO_REPEATS)
    for shape, indexer in s(index_specs)
    for op in s([UpdateOps.ADD, UpdateOps.MUL, UpdateOps.UPDATE])
    for dtype in s(float_dtypes)
    for update_shape in s(_broadcastable_shapes(_update_shape(shape, indexer)))
    for update_dtype in s([dtype] if op == UpdateOps.ADD else float_dtypes))))
  def testAdvancedIndexingGrads(self, shape, dtype, update_shape, update_dtype,
                                indexer, op):
    rng = jtu.rand_default(self.rng())
    jax_fn = lambda x, y: UpdateOps.sugar_fn(op, indexer, x, y,
                                             unique_indices=True)
    x = rng(shape, dtype)
    y = rng(update_shape, update_dtype)
    check_grads(jax_fn, (x, y), 2, rtol=1e-3, atol=1e-3, eps=1.)

  def testSegmentSumBehavior(self):
    # testAdvancedIndexing compares against NumPy, and as a result doesn't check
    # repeated indices. This test is just a simple manual check, based on
    # https://www.tensorflow.org/api_docs/python/tf/math/segment_sum
    data = np.array([5, 1, 7, 2, 3, 4, 1, 3])
    segment_ids = np.array([0, 0, 0, 1, 2, 2, 3, 3])

    ans = ops.index_add(np.zeros(np.max(segment_ids) + 1), segment_ids, data)
    expected = np.array([13, 2, 7, 4])
    self.assertAllClose(ans, expected, check_dtypes=False)

  def testSegmentSum(self):
    data = jnp.array([5, 1, 7, 2, 3, 4, 1, 3])
    segment_ids = jnp.array([0, 0, 0, 1, 2, 2, 3, 3])

    # test with explicit num_segments
    ans = ops.segment_sum(data, segment_ids, num_segments=4)
    expected = jnp.array([13, 2, 7, 4])
    self.assertAllClose(ans, expected, check_dtypes=False)

    # test with explicit num_segments larger than the higher index.
    ans = ops.segment_sum(data, segment_ids, num_segments=5)
    expected = jnp.array([13, 2, 7, 4, 0])
    self.assertAllClose(ans, expected, check_dtypes=False)

    # test without explicit num_segments
    ans = ops.segment_sum(data, segment_ids)
    expected = jnp.array([13, 2, 7, 4])
    self.assertAllClose(ans, expected, check_dtypes=False)

    # test with negative segment ids and segment ids larger than num_segments,
    # that will be wrapped with the `mod`.
    segment_ids = jnp.array([0, 4, 8, 1, 2, -6, -1, 3])
    ans = ops.segment_sum(data, segment_ids, num_segments=4)
    expected = jnp.array([5, 2, 3, 3])
    self.assertAllClose(ans, expected, check_dtypes=False)

    # test with negative segment ids and without without explicit num_segments
    # such as num_segments is defined by the smaller index.
    segment_ids = jnp.array([3, 3, 3, 4, 5, 5, -7, -6])
    ans = ops.segment_sum(data, segment_ids)
    expected = jnp.array([0, 0, 0, 13, 2, 7])
    self.assertAllClose(ans, expected, check_dtypes=False)


  @parameterized.named_parameters(itertools.chain.from_iterable(
      jtu.cases_from_list({
        "testcase_name": "_{}_{}_num_segments={}_bucket_size={}".format(
          jtu.format_shape_dtype_string(shape, dtype),
          reducer.__name__, num_segments, bucket_size),
        "dtype": dtype, "shape": shape,
        "reducer": reducer, "op": op, "identity": identity,
        "num_segments": num_segments, "bucket_size": bucket_size}
      for dtype in default_dtypes
      for shape in [(8,), (7, 4), (6, 4, 2)]
      for bucket_size in [None, 2]
      for num_segments in [None, 1, 3])
    for reducer, op, identity in [
      (ops.segment_sum, np.add, 0),
      (ops.segment_prod, np.multiply, 1),
      (ops.segment_min, np.minimum, float('inf')),
      (ops.segment_max, np.maximum, -float('inf')),
    ]))
  def testSegmentReduce(self, shape, dtype, reducer, op, identity, num_segments, bucket_size):
    rng = jtu.rand_default(self.rng())
    idx_rng = jtu.rand_int(self.rng(), low=-2, high=3)
    args_maker = lambda: [rng(shape, dtype), idx_rng(shape[:1], jnp.int32)]

    if np.issubdtype(dtype, np.integer):
      if np.isposinf(identity):
        identity = np.iinfo(dtype).max
      elif np.isneginf(identity):
        identity = np.iinfo(dtype).min

    jnp_fun = lambda data, segment_ids: reducer(
      data, segment_ids, num_segments=num_segments, bucket_size=bucket_size)

    def np_fun(data, segment_ids):
      size = num_segments if num_segments is not None else (segment_ids.max() + 1)
      out = np.full((size,) + shape[1:], identity, dtype)
      for i, val in zip(segment_ids, data):
        if 0 <= i < size:
          out[i] = op(out[i], val).astype(dtype)
      return out

    self._CheckAgainstNumpy(np_fun, jnp_fun, args_maker)
    if num_segments is not None:
      self._CompileAndCheck(jnp_fun, args_maker)

  def testIndexDtypeError(self):
    # https://github.com/google/jax/issues/2795
    jnp.array(1)  # get rid of startup warning
    with warnings.catch_warnings(record=True) as w:
      warnings.simplefilter("error")
      jnp.zeros(5).at[::2].set(1)
      self.assertLen(w, 0)

  @contextmanager
  def assertNoWarnings(self):
    with warnings.catch_warnings(record=True) as caught_warnings:
      yield
    self.assertEmpty(caught_warnings)

  @parameterized.named_parameters(jtu.cases_from_list({
      "testcase_name": "idx={}".format(idx), "idx": idx, "idx_type": idx_type}
    for idx, idx_type in [
      ([0], "array"),
      ([0, 0], "array"),
      ([[0, 0]], "tuple"),
      ([0, [0, 1]], "tuple"),
      ([0, np.arange(2)], "tuple"),
      ([0, None], "tuple"),
      ([0, slice(None)], "tuple"),
    ]))
  def testIndexSequenceDeprecation(self, idx, idx_type):
    normalize = {"array": np.array, "tuple": tuple}[idx_type]
    msg = {"array": ARRAY_MSG, "tuple": TUPLE_MSG}[idx_type]
    x = jnp.arange(6).reshape(3, 2)

    with self.assertRaisesRegex(TypeError, msg):
      x[idx]
    with self.assertNoWarnings():
      x[normalize(idx)]

    with self.assertRaisesRegex(TypeError, msg):
      x.at[idx].set(0)
    with self.assertNoWarnings():
      x.at[normalize(idx)].set(0)


if __name__ == "__main__":
  absltest.main(testLoader=jtu.JaxTestLoader())
