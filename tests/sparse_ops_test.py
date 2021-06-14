# Copyright 2021 Google LLC
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

from functools import partial
import itertools
import unittest

from absl.testing import absltest
from absl.testing import parameterized

import jax
from jax import api
from jax import config
from jax import dtypes
from jax.experimental import sparse_ops
from jax import lax
from jax.lib import cusparse
from jax.lib import xla_bridge
from jax import jit
from jax import test_util as jtu
from jax import xla
import jax.numpy as jnp
from jax import jvp
import numpy as np
from scipy import sparse
config.parse_flags_with_absl()
FLAGS = config.FLAGS

MATMUL_TOL = {
  np.float32: 1E-5,
  np.float64: 1E-10,
  np.complex64: 1e-5,
  np.complex128: 1E-10,
}

all_dtypes = jtu.dtypes.integer + jtu.dtypes.floating + jtu.dtypes.complex


def rand_sparse(rng, nnz=0.5, post=lambda x: x):
  def _rand_sparse(shape, dtype, nnz=nnz):
    rand = jtu.rand_default(rng)
    size = np.prod(shape)
    if 0 <= nnz < 1:
      nnz = nnz * size
    nnz = min(size, int(nnz))
    M = rand(shape, dtype)
    indices = rng.choice(size, size - nnz, replace=False)
    M.flat[indices] = 0
    return post(M)
  return _rand_sparse


class cuSparseTest(jtu.JaxTestCase):
  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_{}".format(jtu.format_shape_dtype_string(shape, dtype)),
       "shape": shape, "dtype": dtype}
      for shape in [(5, 8), (8, 5), (5, 5), (8, 8)]
      for dtype in jtu.dtypes.floating + jtu.dtypes.complex))
  def test_csr_todense(self, shape, dtype):
    rng = rand_sparse(self.rng(), post=sparse.csr_matrix)
    M = rng(shape, dtype)

    args = (M.data, M.indices, M.indptr)
    todense = lambda *args: sparse_ops.csr_todense(*args, shape=M.shape)

    self.assertArraysEqual(M.toarray(), todense(*args))
    self.assertArraysEqual(M.toarray(), jit(todense)(*args))

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_{}".format(jtu.format_shape_dtype_string(shape, dtype)),
       "shape": shape, "dtype": dtype}
      for shape in [(5, 8), (8, 5), (5, 5), (8, 8)]
      for dtype in jtu.dtypes.floating + jtu.dtypes.complex))
  def test_csr_fromdense(self, shape, dtype):
    rng = rand_sparse(self.rng())
    M = rng(shape, dtype)
    M_csr = sparse.csr_matrix(M)

    nnz = M_csr.nnz
    index_dtype = jnp.int32
    fromdense = lambda M: sparse_ops.csr_fromdense(M, nnz=nnz, index_dtype=jnp.int32)

    data, indices, indptr = fromdense(M)
    self.assertArraysEqual(data, M_csr.data.astype(dtype))
    self.assertArraysEqual(indices, M_csr.indices.astype(index_dtype))
    self.assertArraysEqual(indptr, M_csr.indptr.astype(index_dtype))

    data, indices, indptr = jit(fromdense)(M)
    self.assertArraysEqual(data, M_csr.data.astype(dtype))
    self.assertArraysEqual(indices, M_csr.indices.astype(index_dtype))
    self.assertArraysEqual(indptr, M_csr.indptr.astype(index_dtype))

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_{}_T={}".format(jtu.format_shape_dtype_string(shape, dtype), transpose),
       "shape": shape, "dtype": dtype, "transpose": transpose}
      for shape in [(5, 8), (8, 5), (5, 5), (8, 8)]
      for dtype in jtu.dtypes.floating + jtu.dtypes.complex
      for transpose in [True, False]))
  def test_csr_matvec(self, shape, dtype, transpose):
    op = lambda M: M.T if transpose else M

    v_rng = jtu.rand_default(self.rng())
    rng = rand_sparse(self.rng(), post=sparse.csr_matrix)
    M = rng(shape, dtype)
    v = v_rng(op(M).shape[1], dtype)

    args = (M.data, M.indices, M.indptr, v)
    matvec = lambda *args: sparse_ops.csr_matvec(*args, shape=M.shape, transpose=transpose)

    self.assertAllClose(op(M) @ v, matvec(*args), rtol=MATMUL_TOL)
    self.assertAllClose(op(M) @ v, jit(matvec)(*args), rtol=MATMUL_TOL)

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_{}_T={}".format(jtu.format_shape_dtype_string(shape, dtype), transpose),
       "shape": shape, "dtype": dtype, "transpose": transpose}
      for shape in [(5, 8), (8, 5), (5, 5), (8, 8)]
      for dtype in jtu.dtypes.floating + jtu.dtypes.complex
      for transpose in [True, False]))
  def test_csr_matmat(self, shape, dtype, transpose):
    op = lambda M: M.T if transpose else M

    B_rng = jtu.rand_default(self.rng())
    rng = rand_sparse(self.rng(), post=sparse.csr_matrix)
    M = rng(shape, dtype)
    B = B_rng((op(M).shape[1], 4), dtype)

    args = (M.data, M.indices, M.indptr, B)
    matmat = lambda *args: sparse_ops.csr_matmat(*args, shape=shape, transpose=transpose)

    self.assertAllClose(op(M) @ B, matmat(*args), rtol=MATMUL_TOL)
    self.assertAllClose(op(M) @ B, jit(matmat)(*args), rtol=MATMUL_TOL)

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_{}".format(jtu.format_shape_dtype_string(shape, dtype)),
       "shape": shape, "dtype": dtype}
      for shape in [(5, 8), (8, 5), (5, 5), (8, 8)]
      for dtype in jtu.dtypes.floating + jtu.dtypes.complex))
  def test_coo_todense(self, shape, dtype):
    rng = rand_sparse(self.rng(), post=sparse.coo_matrix)
    M = rng(shape, dtype)

    args = (M.data, M.row, M.col)
    todense = lambda *args: sparse_ops.coo_todense(*args, shape=M.shape)

    self.assertArraysEqual(M.toarray(), todense(*args))
    self.assertArraysEqual(M.toarray(), jit(todense)(*args))

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_{}".format(jtu.format_shape_dtype_string(shape, dtype)),
       "shape": shape, "dtype": dtype}
      for shape in [(5, 8), (8, 5), (5, 5), (8, 8)]
      for dtype in jtu.dtypes.floating + jtu.dtypes.complex))
  def test_coo_fromdense(self, shape, dtype):
    rng = rand_sparse(self.rng())
    M = rng(shape, dtype)
    M_coo = sparse.coo_matrix(M)

    nnz = M_coo.nnz
    index_dtype = jnp.int32
    fromdense = lambda M: sparse_ops.coo_fromdense(M, nnz=nnz, index_dtype=jnp.int32)

    data, row, col = fromdense(M)
    self.assertArraysEqual(data, M_coo.data.astype(dtype))
    self.assertArraysEqual(row, M_coo.row.astype(index_dtype))
    self.assertArraysEqual(col, M_coo.col.astype(index_dtype))

    data, indices, indptr = jit(fromdense)(M)
    self.assertArraysEqual(data, M_coo.data.astype(dtype))
    self.assertArraysEqual(row, M_coo.row.astype(index_dtype))
    self.assertArraysEqual(col, M_coo.col.astype(index_dtype))

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_{}_T={}".format(jtu.format_shape_dtype_string(shape, dtype), transpose),
       "shape": shape, "dtype": dtype, "transpose": transpose}
      for shape in [(5, 8), (8, 5), (5, 5), (8, 8)]
      for dtype in jtu.dtypes.floating + jtu.dtypes.complex
      for transpose in [True, False]))
  def test_coo_matvec(self, shape, dtype, transpose):
    op = lambda M: M.T if transpose else M

    v_rng = jtu.rand_default(self.rng())
    rng = rand_sparse(self.rng(), post=sparse.coo_matrix)
    M = rng(shape, dtype)
    v = v_rng(op(M).shape[1], dtype)

    args = (M.data, M.row, M.col, v)
    matvec = lambda *args: sparse_ops.coo_matvec(*args, shape=M.shape, transpose=transpose)

    self.assertAllClose(op(M) @ v, matvec(*args), rtol=MATMUL_TOL)
    self.assertAllClose(op(M) @ v, jit(matvec)(*args), rtol=MATMUL_TOL)

  @unittest.skipIf(jtu.device_under_test() != "gpu", "test requires GPU")
  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_{}_T={}".format(jtu.format_shape_dtype_string(shape, dtype), transpose),
       "shape": shape, "dtype": dtype, "transpose": transpose}
      for shape in [(5, 8), (8, 5), (5, 5), (8, 8)]
      for dtype in jtu.dtypes.floating + jtu.dtypes.complex
      for transpose in [True, False]))
  def test_coo_matmat(self, shape, dtype, transpose):
    op = lambda M: M.T if transpose else M

    B_rng = jtu.rand_default(self.rng())
    rng = rand_sparse(self.rng(), post=sparse.coo_matrix)
    M = rng(shape, dtype)
    B = B_rng((op(M).shape[1], 4), dtype)

    args = (M.data, M.row, M.col, B)
    matmat = lambda *args: sparse_ops.coo_matmat(*args, shape=shape, transpose=transpose)

    self.assertAllClose(op(M) @ B, matmat(*args), rtol=MATMUL_TOL)
    self.assertAllClose(op(M) @ B, jit(matmat)(*args), rtol=MATMUL_TOL)

    y, dy = jvp(lambda x: sparse_ops.coo_matmat(M.data, M.row, M.col, x, shape=shape, transpose=transpose).sum(), (B, ), (jnp.ones_like(B), ))
    self.assertAllClose((op(M) @ B).sum(), y, rtol=MATMUL_TOL)

    y, dy = jvp(lambda x: sparse_ops.coo_matmat(x, M.row, M.col, B, shape=shape, transpose=transpose).sum(), (M.data, ), (jnp.ones_like(M.data), ))
    self.assertAllClose((op(M) @ B).sum(), y, rtol=MATMUL_TOL)

  @unittest.skipIf(jtu.device_under_test() != "gpu", "test requires GPU")
  def test_gpu_translation_rule(self):
    version = xla_bridge.get_backend().platform_version
    cuda_version = None if version == "<unknown>" else int(version.split()[-1])
    if cuda_version is None or cuda_version < 11000:
      self.assertFalse(cusparse and cusparse.is_supported)
      self.assertNotIn(sparse_ops.csr_todense_p, xla.backend_specific_translations["gpu"])
    else:
      self.assertTrue(cusparse and cusparse.is_supported)
      self.assertIn(sparse_ops.csr_todense_p, xla.backend_specific_translations["gpu"])

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_{}_{}".format(
         jtu.format_shape_dtype_string(shape, dtype), mat_type),
       "shape": shape, "dtype": dtype, "mat_type": mat_type}
      for shape in [(5, 8), (8, 5), (5, 5), (8, 8)]
      for dtype in jtu.dtypes.floating + jtu.dtypes.complex
      for mat_type in ['csr', 'coo']))
  def test_extra_nnz(self, shape, dtype, mat_type):
    rng = rand_sparse(self.rng())
    M = rng(shape, dtype)
    nnz = (M != 0).sum() + 5
    fromdense = getattr(sparse_ops, f"{mat_type}_fromdense")
    todense = getattr(sparse_ops, f"{mat_type}_todense")
    args = fromdense(M, nnz=nnz, index_dtype=jnp.int32)
    M_out = todense(*args, shape=M.shape)
    self.assertArraysEqual(M, M_out)

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_{}".format(jtu.format_shape_dtype_string(shape, dtype)),
       "shape": shape, "dtype": dtype}
      for shape in [(5, 8), (8, 5), (5, 5), (8, 8)]
      for dtype in jtu.dtypes.floating + jtu.dtypes.complex))
  def test_coo_todense_ad(self, shape, dtype):
    rng = rand_sparse(self.rng(), post=jnp.array)
    M = rng(shape, dtype)
    data, row, col = sparse_ops.coo_fromdense(M, nnz=(M != 0).sum())
    f = lambda data: sparse_ops.coo_todense(data, row, col, shape=M.shape)

    # Forward-mode
    primals, tangents = api.jvp(f, [data], [jnp.ones_like(data)])
    self.assertArraysEqual(primals, f(data))
    self.assertArraysEqual(tangents, jnp.zeros_like(M).at[row, col].set(1))

    # Reverse-mode
    primals, vjp_fun = api.vjp(f, data)
    data_out, = vjp_fun(primals)
    self.assertArraysEqual(primals, f(data))
    self.assertArraysEqual(data_out, data)

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_{}".format(jtu.format_shape_dtype_string(shape, dtype)),
       "shape": shape, "dtype": dtype}
      for shape in [(5, 8), (8, 5), (5, 5), (8, 8)]
      for dtype in jtu.dtypes.floating + jtu.dtypes.complex))
  def test_coo_fromdense_ad(self, shape, dtype):
    rng = rand_sparse(self.rng(), post=jnp.array)
    M = rng(shape, dtype)
    nnz = (M != 0).sum()
    f = lambda M: sparse_ops.coo_fromdense(M, nnz=nnz)

    # Forward-mode
    primals, tangents = api.jvp(f, [M], [jnp.ones_like(M)])
    self.assertArraysEqual(primals[0], f(M)[0])
    self.assertArraysEqual(primals[1], f(M)[1])
    self.assertArraysEqual(primals[2], f(M)[2])
    self.assertArraysEqual(tangents[0], jnp.ones(nnz, dtype=dtype))
    self.assertEqual(tangents[1].dtype, dtypes.float0)
    self.assertEqual(tangents[2].dtype, dtypes.float0)

    # Reverse-mode
    primals, vjp_fun = api.vjp(f, M)
    M_out, = vjp_fun(primals)
    self.assertArraysEqual(primals[0], f(M)[0])
    self.assertArraysEqual(primals[1], f(M)[1])
    self.assertArraysEqual(primals[2], f(M)[2])
    self.assertArraysEqual(M_out, M)

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_{}_{}".format(
        jtu.format_shape_dtype_string(shape, dtype),
        jtu.format_shape_dtype_string(bshape, dtype)),
       "shape": shape, "dtype": dtype, "bshape": bshape}
      for shape in [(5, 8), (8, 5), (5, 5), (8, 8)]
      for bshape in [shape[-1:] + s for s in [()]]  # TODO: matmul autodiff
      for dtype in jtu.dtypes.floating + jtu.dtypes.complex))  # TODO: other types

  def test_coo_matvec_ad(self, shape, dtype, bshape):
    tol = {np.float32: 1E-6, np.float64: 1E-13, np.complex64: 1E-6, np.complex128: 1E-13}

    rng = rand_sparse(self.rng(), post=jnp.array)
    rng_b = jtu.rand_default(self.rng())

    M = rng(shape, dtype)
    data, row, col = sparse_ops.coo_fromdense(M, nnz=(M != 0).sum())
    x = rng_b(bshape, dtype)
    xdot = rng_b(bshape, dtype)

    # Forward-mode with respect to the vector
    f_dense = lambda x: M @ x
    f_sparse = lambda x: sparse_ops.coo_matvec(data, row, col, x, shape=M.shape)
    v_sparse, t_sparse = api.jvp(f_sparse, [x], [xdot])
    v_dense, t_dense = api.jvp(f_dense, [x], [xdot])
    self.assertAllClose(v_sparse, v_dense, atol=tol, rtol=tol)
    self.assertAllClose(t_sparse, t_dense, atol=tol, rtol=tol)

    # Reverse-mode with respect to the vector
    primals_dense, vjp_dense = api.vjp(f_dense, x)
    primals_sparse, vjp_sparse = api.vjp(f_sparse, x)
    out_dense, = vjp_dense(primals_dense)
    out_sparse, = vjp_sparse(primals_sparse)
    self.assertAllClose(primals_dense[0], primals_sparse[0], atol=tol, rtol=tol)
    self.assertAllClose(out_dense, out_sparse, atol=tol, rtol=tol)

    # Forward-mode with respect to nonzero elements of the matrix
    f_sparse = lambda data: sparse_ops.coo_matvec(data, row, col, x, shape=M.shape)
    f_dense = lambda data: sparse_ops.coo_todense(data, row, col, shape=M.shape) @ x
    data = rng((len(data),), data.dtype)
    data_dot = rng((len(data),), data.dtype)
    v_sparse, t_sparse = api.jvp(f_sparse, [data], [data_dot])
    v_dense, t_dense = api.jvp(f_dense, [data], [data_dot])

    self.assertAllClose(v_sparse, v_dense, atol=tol, rtol=tol)
    self.assertAllClose(t_sparse, t_dense, atol=tol, rtol=tol)

    # Reverse-mode with respect to nonzero elements of the matrix
    primals_dense, vjp_dense = api.vjp(f_dense, data)
    primals_sparse, vjp_sparse = api.vjp(f_sparse, data)
    out_dense, = vjp_dense(primals_dense)
    out_sparse, = vjp_sparse(primals_sparse)
    self.assertAllClose(primals_dense[0], primals_sparse[0], atol=tol, rtol=tol)
    self.assertAllClose(out_dense, out_sparse, atol=tol, rtol=tol)

class BCOOTest(jtu.JaxTestCase):
  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_{}_nbatch={}_ndense={}".format(
        jtu.format_shape_dtype_string(shape, dtype), n_batch, n_dense),
       "shape": shape, "dtype": dtype, "n_batch": n_batch, "n_dense": n_dense}
      for shape in [(5,), (5, 8), (8, 5), (3, 4, 5), (3, 4, 3, 2)]
      for dtype in jtu.dtypes.floating + jtu.dtypes.complex
      for n_batch in range(len(shape) + 1)
      for n_dense in range(len(shape) + 1 - n_batch)))
  def test_bcoo_dense_round_trip(self, shape, dtype, n_batch, n_dense):
    rng = rand_sparse(self.rng())
    M = rng(shape, dtype)
    n_sparse = M.ndim - n_batch - n_dense
    nse = int(sparse_ops._bcoo_nse(M, n_batch=n_batch, n_dense=n_dense))
    data, indices = sparse_ops.bcoo_fromdense(M, n_batch=n_batch, n_dense=n_dense)
    # TODO: test fromdense JIT

    assert data.dtype == dtype
    assert data.shape == shape[:n_batch] + (nse,) + shape[n_batch + n_sparse:]
    assert indices.dtype == jnp.int32  # TODO: test passing this arg
    assert indices.shape == shape[:n_batch] + (n_sparse, nse)

    todense = partial(sparse_ops.bcoo_todense, shape=shape)
    self.assertArraysEqual(M, todense(data, indices))
    self.assertArraysEqual(M, jit(todense)(data, indices))

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_{}_nbatch={}_ndense={}".format(
        jtu.format_shape_dtype_string(shape, dtype), n_batch, n_dense),
       "shape": shape, "dtype": dtype, "n_batch": n_batch, "n_dense": n_dense}
      for shape in [(5,), (5, 8), (8, 5), (3, 4, 5), (3, 4, 3, 2)]
      for dtype in jtu.dtypes.floating
      for n_batch in range(len(shape) + 1)
      for n_dense in range(len(shape) + 1 - n_batch)))
  def test_bcoo_todense_ad(self, shape, dtype, n_batch, n_dense):
    rng = rand_sparse(self.rng())
    M = rng(shape, dtype)
    data, indices = sparse_ops.bcoo_fromdense(M, n_batch=n_batch, n_dense=n_dense)

    todense = partial(sparse_ops.bcoo_todense, indices=indices, shape=shape)
    j1 = jax.jacfwd(todense)(data)
    j2 = jax.jacrev(todense)(data)
    hess = jax.hessian(todense)(data)
    self.assertArraysAllClose(j1, j2)
    self.assertEqual(j1.shape, M.shape + data.shape)
    self.assertEqual(hess.shape, M.shape + 2 * data.shape)

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_{}_nbatch={}_ndense={}".format(
        jtu.format_shape_dtype_string(shape, dtype), n_batch, n_dense),
       "shape": shape, "dtype": dtype, "n_batch": n_batch, "n_dense": n_dense}
      for shape in [(5,), (5, 8), (8, 5), (3, 4, 5), (3, 4, 3, 2)]
      for dtype in jtu.dtypes.floating
      for n_batch in range(len(shape) + 1)
      for n_dense in range(len(shape) + 1 - n_batch)))
  def test_bcoo_fromdense_ad(self, shape, dtype, n_batch, n_dense):
    rng = rand_sparse(self.rng())
    M = rng(shape, dtype)
    nse = int(sparse_ops._bcoo_nse(M, n_batch=n_batch, n_dense=n_dense))

    def fromdense(M):
      return sparse_ops.bcoo_fromdense(M, nse=nse, n_batch=n_batch, n_dense=n_dense)[0]
    data = fromdense(M)

    j1 = jax.jacfwd(fromdense)(M)
    j2 = jax.jacrev(fromdense)(M)
    hess = jax.hessian(fromdense)(M)
    self.assertArraysAllClose(j1, j2)
    self.assertEqual(j1.shape, data.shape + M.shape)
    self.assertEqual(hess.shape, data.shape + 2 * M.shape)

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_{}_nbatch={}_ndense={}".format(
        jtu.format_shape_dtype_string(shape, dtype), n_batch, n_dense),
       "shape": shape, "dtype": dtype, "n_batch": n_batch, "n_dense": n_dense}
      for shape in [(5,), (5, 8), (8, 5), (3, 4, 5), (3, 4, 3, 2)]
      for dtype in jtu.dtypes.floating + jtu.dtypes.complex
      for n_batch in range(len(shape) + 1)
      for n_dense in range(len(shape) + 1 - n_batch)))
  def test_bcoo_dense_round_trip_batched(self, shape, dtype, n_batch, n_dense):
    rng = rand_sparse(self.rng())
    M = rng(shape, dtype)
    n_sparse = M.ndim - n_batch - n_dense
    nse = int(sparse_ops._bcoo_nse(M, n_batch=n_batch, n_dense=n_dense))

    fromdense = partial(sparse_ops.bcoo_fromdense, nse=nse, n_dense=n_dense)
    todense = partial(sparse_ops.bcoo_todense, shape=shape[n_batch:])
    for i in range(n_batch):
      fromdense = jax.vmap(fromdense)
      todense = jax.vmap(todense)

    data, indices = fromdense(M)

    assert data.dtype == dtype
    assert data.shape == shape[:n_batch] + (nse,) + shape[n_batch + n_sparse:]
    assert indices.dtype == jnp.int32  # TODO: test passing this arg
    assert indices.shape == shape[:n_batch] + (n_sparse, nse)

    self.assertArraysEqual(M, todense(data, indices))
    self.assertArraysEqual(M, jit(todense)(data, indices))

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_{}_nbatch={}_ndense={}".format(
        jtu.format_shape_dtype_string(shape, dtype), n_batch, n_dense),
       "shape": shape, "dtype": dtype, "n_batch": n_batch, "n_dense": n_dense}
      for shape in [(5,), (5, 8), (8, 5), (3, 4, 5), (3, 4, 3, 2)]
      for dtype in jtu.dtypes.floating + jtu.dtypes.complex
      for n_batch in range(len(shape) + 1)
      for n_dense in range(len(shape) + 1 - n_batch)))
  def test_bcoo_extract(self, shape, dtype, n_batch, n_dense):
    rng = rand_sparse(self.rng())
    M = rng(shape, dtype)
    data, indices = sparse_ops.bcoo_fromdense(M)
    data2 = sparse_ops.bcoo_extract(indices, M)
    self.assertArraysEqual(data, data2)
    data3 = jit(sparse_ops.bcoo_extract)(indices, M)
    self.assertArraysEqual(data, data3)

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_{}_nbatch={}_ndense={}".format(
        jtu.format_shape_dtype_string(shape, dtype), n_batch, n_dense),
       "shape": shape, "dtype": dtype, "n_batch": n_batch, "n_dense": n_dense}
      for shape in [(5,), (5, 8), (8, 5), (3, 4, 5), (3, 4, 3, 2)]
      for dtype in jtu.dtypes.floating
      for n_batch in range(len(shape) + 1)
      for n_dense in range(len(shape) + 1 - n_batch)))
  def test_bcoo_extract_ad(self, shape, dtype, n_batch, n_dense):
    rng = rand_sparse(self.rng())
    M = rng(shape, dtype)
    data, indices = sparse_ops.bcoo_fromdense(M, n_batch=n_batch, n_dense=n_dense)

    extract = partial(sparse_ops.bcoo_extract, indices)
    j1 = jax.jacfwd(extract)(M)
    j2 = jax.jacrev(extract)(M)
    hess = jax.hessian(extract)(M)
    self.assertArraysAllClose(j1, j2)
    self.assertEqual(j1.shape, data.shape + M.shape)
    self.assertEqual(hess.shape, data.shape + 2 * M.shape)

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_{}_nbatch={}_ndense={}".format(
        jtu.format_shape_dtype_string(shape, dtype), n_batch, n_dense),
       "shape": shape, "dtype": dtype, "n_batch": n_batch, "n_dense": n_dense}
      for shape in [(5, 8), (8, 5), (3, 4, 5), (3, 4, 3, 2)]
      for dtype in jtu.dtypes.floating + jtu.dtypes.complex
      for n_batch in range(1, len(shape) + 1)
      for n_dense in range(len(shape) + 1 - n_batch)))
  def test_bcoo_todense_partial_batch(self, shape, dtype, n_batch, n_dense):
    rng = rand_sparse(self.rng())
    M = rng(shape, dtype)
    data, indices = sparse_ops.bcoo_fromdense(M, n_batch=n_batch, n_dense=n_dense)

    M1 = sparse_ops.bcoo_todense(data, indices[:1], shape=M.shape)
    M2 = sparse_ops.bcoo_todense(data, jnp.stack(shape[0] * [indices[0]]), shape=M.shape)
    self.assertAllClose(M1, M2)

    M3 = sparse_ops.bcoo_todense(data[:1], indices, shape=M.shape)
    M4 = sparse_ops.bcoo_todense(jnp.stack(shape[0] * [data[0]]), indices, shape=M.shape)
    self.assertAllClose(M3, M4)

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name":
       "_lhs_shape={}_rhs_shape={}_lhs_contracting={}_rhs_contracting={}_n_dense={}"
       .format(jtu.format_shape_dtype_string(lhs_shape, dtype),
               jtu.format_shape_dtype_string(rhs_shape, dtype),
               lhs_contracting, rhs_contracting, n_dense),
       "lhs_shape": lhs_shape, "rhs_shape": rhs_shape, "dtype": dtype,
       "lhs_contracting": lhs_contracting, "rhs_contracting": rhs_contracting,
       "n_dense": n_dense}
      for lhs_shape, rhs_shape, lhs_contracting, rhs_contracting in [
          [(5,), (6,), [], []],
          [(5,), (5,), [0], [0]],
          [(5, 7), (5,), [0], [0]],
          [(7, 5), (5,), [1], [0]],
          [(3, 5), (2, 5), [1], [1]],
          [(5, 3), (5, 2), [0], [0]],
          [(5, 3, 2), (5, 2, 4), [0], [0]],
          [(5, 3, 2), (5, 2, 4), [0,2], [0,1]],
          [(5, 3, 2), (3, 5, 2, 4), [0,2], [1,2]],
          [(1, 2, 2, 3), (1, 2, 3, 1), [1], [1]],
          [(3, 2), (2, 4), [1], [0]],
      ]
      for n_dense in range(len(lhs_shape) - max(lhs_contracting, default=0))
      for dtype in jtu.dtypes.floating + jtu.dtypes.complex))
  def test_bcoo_dot_general_contract_only(self, lhs_shape, rhs_shape, dtype,
                                         lhs_contracting, rhs_contracting, n_dense):
    rng = jtu.rand_small(self.rng())
    rng_sparse = rand_sparse(self.rng())
    def args_maker():
      lhs = rng_sparse(lhs_shape, dtype)
      rhs = rng(rhs_shape, dtype)
      data, indices = sparse_ops.bcoo_fromdense(lhs, n_dense=n_dense)
      return data, indices, lhs, rhs
    dimension_numbers = ((lhs_contracting, rhs_contracting), ([], []))

    def f_dense(data, indices, lhs, rhs):
      return lax.dot_general(lhs, rhs, dimension_numbers=dimension_numbers)

    def f_sparse(data, indices, lhs, rhs):
      return sparse_ops.bcoo_dot_general(data, indices, rhs,
                                         lhs_shape=lhs.shape,
                                         dimension_numbers=dimension_numbers)

    self._CheckAgainstNumpy(f_dense, f_sparse, args_maker)
    self._CheckAgainstNumpy(f_dense, jit(f_sparse), args_maker)
    # In rare cases, this fails python_should_be_executing check. Why?
    # self._CompileAndCheck(f_sparse, args_maker)

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name":
       "_lhs_shape={}_rhs_shape={}_dimension_numbers={}_n_batch={}_n_dense={}"
       .format(jtu.format_shape_dtype_string(lhs_shape, dtype),
               jtu.format_shape_dtype_string(rhs_shape, dtype),
               dimension_numbers, n_batch, n_dense),
       "lhs_shape": lhs_shape, "rhs_shape": rhs_shape, "dtype": dtype,
       "dimension_numbers": dimension_numbers,
       "n_batch": n_batch, "n_dense": n_dense}
      for lhs_shape, rhs_shape, dimension_numbers, n_batch, n_dense in [
          ((3, 3, 2), (3, 2, 4), (([2], [1]), ([0], [0])), 1, 0),
          ((3, 3, 2), (3, 2, 4), (([2], [1]), ([0], [0])), 2, 0),
          ((3, 3, 2), (2, 3, 4), (([2], [0]), ([0], [1])), 1, 0),
          ((3, 3, 2), (2, 3, 4), (([2], [0]), ([0], [1])), 2, 0),
          ((3, 4, 2, 4), (3, 4, 3, 2), (([2], [3]), ([0, 1], [0, 1])), 2, 0),
          ((3, 4, 2, 4), (3, 4, 3, 2), (([2], [3]), ([0, 1], [0, 1])), 2, 1),
      ]
      for dtype in jtu.dtypes.floating + jtu.dtypes.complex))
  def test_bcoo_dot_general_contract_and_batch(self, lhs_shape, rhs_shape, dtype,
                                               dimension_numbers, n_batch, n_dense):
    rng = jtu.rand_small(self.rng())
    rng_sparse = rand_sparse(self.rng())
    def args_maker():
      lhs = rng_sparse(lhs_shape, dtype)
      rhs = rng(rhs_shape, dtype)
      data, indices = sparse_ops.bcoo_fromdense(lhs, n_batch=n_batch, n_dense=n_dense)
      return data, indices, lhs, rhs

    def f_dense(data, indices, lhs, rhs):
      return lax.dot_general(lhs, rhs, dimension_numbers=dimension_numbers)

    def f_sparse(data, indices, lhs, rhs):
      return sparse_ops.bcoo_dot_general(data, indices, rhs,
                                         lhs_shape=lhs.shape,
                                         dimension_numbers=dimension_numbers)

    self._CheckAgainstNumpy(f_dense, f_sparse, args_maker)
    self._CheckAgainstNumpy(f_dense, jit(f_sparse), args_maker)
    # In rare cases, this fails python_should_be_executing check. Why?
    # self._CompileAndCheck(f_sparse, args_maker)

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name":
       "_lhs_shape={}_rhs_shape={}_dimension_numbers={}_n_batch={}_n_dense={}"
       .format(jtu.format_shape_dtype_string(lhs_shape, dtype),
               jtu.format_shape_dtype_string(rhs_shape, dtype),
               dimension_numbers, n_batch, n_dense),
       "lhs_shape": lhs_shape, "rhs_shape": rhs_shape, "dtype": dtype,
       "dimension_numbers": dimension_numbers,
       "n_batch": n_batch, "n_dense": n_dense}
      for lhs_shape, rhs_shape, dimension_numbers, n_batch, n_dense in [
          ((3, 3, 2), (3, 2, 4), (([2], [1]), ([0], [0])), 1, 0),
          ((3, 3, 2), (3, 2, 4), (([2], [1]), ([0], [0])), 2, 0),
          ((3, 3, 2), (2, 3, 4), (([2], [0]), ([0], [1])), 1, 0),
          ((3, 3, 2), (2, 3, 4), (([2], [0]), ([0], [1])), 2, 0),
          ((3, 4, 2, 4), (3, 4, 3, 2), (([2], [3]), ([0, 1], [0, 1])), 2, 0),
          ((3, 4, 2, 4), (3, 4, 3, 2), (([2], [3]), ([0, 1], [0, 1])), 2, 1),
      ]
      for dtype in jtu.dtypes.floating + jtu.dtypes.complex))
  def test_bcoo_dot_general_partial_batch(self, lhs_shape, rhs_shape, dtype,
                                          dimension_numbers, n_batch, n_dense):
    rng = jtu.rand_small(self.rng())
    rng_sparse = rand_sparse(self.rng())

    X = rng_sparse(lhs_shape, dtype)
    data, indices = sparse_ops.bcoo_fromdense(X, n_batch=n_batch, n_dense=n_dense)
    Y = rng(rhs_shape, dtype)

    def f_dense(X, Y):
      return lax.dot_general(X, Y, dimension_numbers=dimension_numbers)

    def f_sparse(data, indices, Y):
      return sparse_ops.bcoo_dot_general(data, indices, Y, lhs_shape=X.shape,
                                        dimension_numbers=dimension_numbers)

    for data, indices in itertools.product([data, data[:1]], [indices, indices[:1]]):
      X = sparse_ops.bcoo_todense(data, indices, shape=X.shape)
      self.assertAllClose(f_dense(X, Y), f_sparse(data, indices, Y))

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name":
       "_lhs_shape={}_rhs_shape={}_dimension_numbers={}_n_batch={}_n_dense={}"
       .format(jtu.format_shape_dtype_string(lhs_shape, dtype),
               jtu.format_shape_dtype_string(rhs_shape, dtype),
               dimension_numbers, n_batch, n_dense),
       "lhs_shape": lhs_shape, "rhs_shape": rhs_shape, "dtype": dtype,
       "dimension_numbers": dimension_numbers,
       "n_batch": n_batch, "n_dense": n_dense}
      for lhs_shape, rhs_shape, dimension_numbers, n_batch, n_dense in [
          ((3, 3, 2), (3, 2, 4), (([2], [1]), ([0], [0])), 1, 0),
          ((3, 3, 2), (2, 3, 4), (([2], [0]), ([0], [1])), 1, 0),
          ((3, 4, 2, 4), (3, 4, 3, 2), (([2], [3]), ([0, 1], [0, 1])), 2, 0),
          # These require contraction over batch & dense dimensions
          # which is not yet implemented:
          # ((3, 3, 2), (3, 2, 4), (([2], [1]), ([0], [0])), 2, 0),
          # ((3, 3, 2), (2, 3, 4), (([2], [0]), ([0], [1])), 2, 0),
          # ((3, 4, 2, 4), (3, 4, 3, 2), (([2], [3]), ([0, 1], [0, 1])), 2, 1),
      ]
      for dtype in jtu.dtypes.floating))
  def test_bcoo_dot_general_ad(self, lhs_shape, rhs_shape, dtype,
                               dimension_numbers, n_batch, n_dense):
    rng = jtu.rand_small(self.rng())
    rng_sparse = rand_sparse(self.rng())

    X = rng_sparse(lhs_shape, dtype)
    data, indices = sparse_ops.bcoo_fromdense(X, n_batch=n_batch, n_dense=n_dense)
    Y = rng(rhs_shape, dtype)

    def f_dense(Y):
      return lax.dot_general(X, Y, dimension_numbers=dimension_numbers)

    def f_sparse(Y):
      return sparse_ops.bcoo_dot_general(data, indices, Y, lhs_shape=X.shape,
                                        dimension_numbers=dimension_numbers)

    jf_dense = jax.jacfwd(f_dense)(Y)
    jr_dense = jax.jacrev(f_dense)(Y)
    jf_sparse = jax.jacfwd(f_sparse)(Y)
    jr_sparse = jax.jacrev(f_sparse)(Y)

    tol = {}
    if jtu.device_under_test() == "tpu":
      tol = {np.float32: 5E-3}

    self.assertAllClose(jf_dense, jf_sparse, rtol=tol)
    self.assertAllClose(jr_dense, jr_sparse, rtol=tol)
    self.assertAllClose(jf_sparse, jr_sparse, rtol=tol)

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_{}_nbatch={}_ndense={}".format(
        jtu.format_shape_dtype_string(shape, dtype), n_batch, n_dense),
       "shape": shape, "dtype": dtype, "n_batch": n_batch, "n_dense": n_dense}
      for shape in [(5,), (5, 8), (8, 5), (3, 4, 5), (3, 4, 3, 2)]
      for dtype in jtu.dtypes.floating + jtu.dtypes.complex
      for n_batch in range(len(shape) + 1)
      for n_dense in range(len(shape) + 1 - n_batch)))
  def test_bcoo_dedupe(self, shape, dtype, n_batch, n_dense):
    rng = self.rng()
    rng_sparse = rand_sparse(self.rng())
    M = rng_sparse(shape, dtype)
    data, indices = sparse_ops.bcoo_fromdense(M, n_batch=n_batch, n_dense=n_dense)
    for i, s in enumerate(shape[n_batch:len(shape) - n_dense]):
      indices = indices.at[..., i, :].set(rng.randint(0, s, size=indices.shape[-1]))
    data2, indices2 = sparse_ops._dedupe_bcoo(data, indices)
    M1 = sparse_ops.bcoo_todense(data, indices, shape=shape)
    M2 = sparse_ops.bcoo_todense(data2, indices2, shape=shape)

    self.assertAllClose(M1, M2)

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_{}_nbatch={}_ndense={}_axes={}".format(
        jtu.format_shape_dtype_string(shape, dtype), n_batch, n_dense, axes),
       "shape": shape, "dtype": dtype, "n_batch": n_batch, "n_dense": n_dense, "axes": axes}
      for shape in [(5,), (5, 8), (8, 5), (3, 4, 5), (3, 4, 3, 2)]
      for dtype in jtu.dtypes.floating + jtu.dtypes.complex
      for n_batch in range(len(shape) + 1)
      for n_dense in range(len(shape) + 1 - n_batch)
      for naxes in range(len(shape))
      for axes in itertools.combinations(range(len(shape)), naxes)))
  def test_bcoo_reduce_sum(self, shape, dtype, n_batch, n_dense, axes):
    rng = rand_sparse(self.rng())
    M = rng(shape, dtype)
    data, indices = sparse_ops.bcoo_fromdense(M, n_batch=n_batch, n_dense=n_dense)
    data_out, indices_out, shape_out = sparse_ops.bcoo_reduce_sum(data, indices, shape=shape, axes=axes)
    result_dense = M.sum(axes)
    result_sparse = sparse_ops.bcoo_todense(data_out, indices_out, shape=shape_out)
    tol = {np.float32: 1E-6, np.float64: 1E-14}
    self.assertAllClose(result_dense, result_sparse, atol=tol, rtol=tol)

  @unittest.skipIf(jtu.device_under_test() == "tpu", "TPU has insufficient precision")
  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_{}_{}".format(
        jtu.format_shape_dtype_string(lhs_shape, lhs_dtype),
        jtu.format_shape_dtype_string(rhs_shape, rhs_dtype)),
       "lhs_shape": lhs_shape, "lhs_dtype": lhs_dtype,
       "rhs_shape": rhs_shape, "rhs_dtype": rhs_dtype,
      }
      for lhs_shape, rhs_shape in [[(3,), (3,)],
                                   [(3, 4), (4,)],
                                   [(4,), (4, 5)],
                                   [(3, 4), (4, 5)]]
      for lhs_dtype in all_dtypes
      for rhs_dtype in all_dtypes))
  def test_bcoo_matmul(self, lhs_shape, lhs_dtype, rhs_shape, rhs_dtype):
    rng = jtu.rand_default(self.rng())
    lhs = jnp.array(rng(lhs_shape, lhs_dtype))
    rhs = jnp.array(rng(rhs_shape, rhs_dtype))

    out1 = lhs @ rhs
    out2 = sparse_ops.BCOO.fromdense(lhs) @ rhs
    out3 = lhs @ sparse_ops.BCOO.fromdense(rhs)

    tol = {np.float64: 1E-13, np.complex128: 1E-13,
           np.float32: 1E-6, np.complex64: 1E-6}
    self.assertAllClose(out1, out2, rtol=tol)
    self.assertAllClose(out1, out3, rtol=tol)


class SparseObjectTest(jtu.JaxTestCase):
  @parameterized.named_parameters(
    {"testcase_name": "_{}".format(Obj.__name__), "Obj": Obj}
    for Obj in [sparse_ops.CSR, sparse_ops.CSC, sparse_ops.COO, sparse_ops.BCOO])
  def test_attrs(self, Obj, shape=(5, 8), dtype=np.float16):
    rng = rand_sparse(self.rng(), post=Obj.fromdense)
    M = rng(shape, dtype)

    assert isinstance(M, Obj)
    assert M.shape == shape
    assert M.dtype == dtype
    assert M.nnz == (M.todense() != 0).sum()
    assert M.data.dtype == dtype

    if isinstance(M, sparse_ops.CSR):
      assert len(M.data) == len(M.indices)
      assert len(M.indptr) == M.shape[0] + 1
    elif isinstance(M, sparse_ops.CSC):
      assert len(M.data) == len(M.indices)
      assert len(M.indptr) == M.shape[1] + 1
    elif isinstance(M, sparse_ops.COO):
      assert len(M.data) == len(M.row) == len(M.col)
    elif isinstance(M, sparse_ops.BCOO):
      assert M.data.shape[M.n_batch] == M.indices.shape[-1]
      assert M.indices.shape[-2] == M.n_sparse
    else:
      raise ValueError("Obj={Obj} not expected.")

  @parameterized.named_parameters(itertools.chain.from_iterable(
    jtu.cases_from_list(
      {"testcase_name": "_{}_Obj={}".format(
        jtu.format_shape_dtype_string(shape, dtype), Obj.__name__),
       "shape": shape, "dtype": dtype, "Obj": Obj}
      for shape in [(5, 8), (8, 5), (5, 5), (8, 8)]
      for dtype in jtu.dtypes.floating + jtu.dtypes.complex)
    for Obj in [sparse_ops.CSR, sparse_ops.CSC, sparse_ops.COO, sparse_ops.BCOO]))
  def test_dense_round_trip(self, shape, dtype, Obj):
    rng = rand_sparse(self.rng())
    M = rng(shape, dtype)
    Msparse = Obj.fromdense(M)
    self.assertArraysEqual(M, Msparse.todense())

  @parameterized.named_parameters(itertools.chain.from_iterable(
    jtu.cases_from_list(
      {"testcase_name": "_{}_Obj={}".format(
        jtu.format_shape_dtype_string(shape, dtype), Obj.__name__),
       "shape": shape, "dtype": dtype, "Obj": Obj}
      for shape in [(5, 8), (8, 5), (5, 5), (8, 8)]
      for dtype in jtu.dtypes.floating + jtu.dtypes.complex)
    for Obj in [sparse_ops.CSR, sparse_ops.CSC, sparse_ops.COO, sparse_ops.BCOO]))
  def test_transpose(self, shape, dtype, Obj):
    rng = rand_sparse(self.rng())
    M = rng(shape, dtype)
    Msparse = Obj.fromdense(M)
    self.assertArraysEqual(M.T, Msparse.T.todense())

  @unittest.skipIf(jtu.device_under_test() == "tpu", "TPU has insufficient precision")
  @parameterized.named_parameters(itertools.chain.from_iterable(
    jtu.cases_from_list(
      {"testcase_name": "_{}_Obj={}_bshape={}".format(
        jtu.format_shape_dtype_string(shape, dtype), Obj.__name__, bshape),
       "shape": shape, "dtype": dtype, "Obj": Obj, "bshape": bshape}
      for shape in [(5, 8), (8, 5), (5, 5), (8, 8)]
      for bshape in [shape[-1:] + s for s in [(), (3,), (4,)]]
      for dtype in jtu.dtypes.floating + jtu.dtypes.complex)
    for Obj in [sparse_ops.CSR, sparse_ops.CSC, sparse_ops.COO, sparse_ops.BCOO]))
  def test_matmul(self, shape, dtype, Obj, bshape):
    rng = rand_sparse(self.rng(), post=jnp.array)
    rng_b = jtu.rand_default(self.rng())
    M = rng(shape, dtype)
    Msp = Obj.fromdense(M)
    x = rng_b(bshape, dtype)
    x = jnp.asarray(x)

    self.assertAllClose(M @ x, Msp @ x, rtol=MATMUL_TOL)


if __name__ == "__main__":
  absltest.main(testLoader=jtu.JaxTestLoader())
