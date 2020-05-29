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


from functools import partial
import itertools as it
from unittest import SkipTest

import numpy as np
from absl.testing import absltest, parameterized
from jax.interpreters.masking import shape_as_value, ShapeError, \
  parse_spec, Poly, Mon
from jax import numpy as jnp, test_util as jtu, mask, vmap, jit, grad, lax, \
  shapecheck, api
from jax.config import config
from jax.numpy.lax_numpy import _polymorphic_slice_indices
from jax.scipy.special import expit

config.parse_flags_with_absl()


# These are 'manual' tests for masking. The more exhaustive,
# more systematic tests should live in lax_test.py.

def constant_poly(c):
  return Poly({Mon(): c})

class ShapesTest(jtu.JaxTestCase):

  @parameterized.parameters([
      ['(m, n)', 'ShapeSpec(m, n)'],
      ['(m * n)', 'ShapeSpec(m n)'],
      ['m * n', 'ShapeSpec(m n)'],
      ['(m * n,)', 'ShapeSpec(m n)'],
      ['(3, m)', 'ShapeSpec(3, m)'],
      ['(10, m)', 'ShapeSpec(10, m)'],
      ['(-10, m)', 'ShapeSpec(-10, m)'],
      ['(3 * m)', 'ShapeSpec(3 m)'],
      ['m', 'ShapeSpec(m)'],
      ['', 'ShapeSpec()'],
      ['m + n', 'ShapeSpec(m + n)'],
      ['m + n * k', 'ShapeSpec(m + k n)'],
      ['m + 3 * k', 'ShapeSpec(3 k + m)'],
      ['', 'ShapeSpec()'],
      ['_', 'ShapeSpec(_)'],
  ])
  def test_parse_spec(self, spec, ans):
    self.assertEqual(str(parse_spec(spec)), ans)

  def test_Poly_equal(self):
    assert constant_poly(3) == 3
    assert np.array(3, np.int64) == constant_poly(3)
    assert np.array(3, np.int64)[()] == constant_poly(3)
    assert not np.array(3, np.int64) != constant_poly(3)
    assert constant_poly(4) != 3
    assert 3 == constant_poly(3)
    assert 4 != constant_poly(3)
    assert constant_poly(4) == constant_poly(4)
    assert constant_poly(3) != constant_poly(4)
    assert Poly({Mon(): 3, Mon({'n': 1}): 4}) == Poly({Mon({'n': 1}): 4, Mon(): 3})
    assert Poly({Mon(): 3, Mon({'n': 1}): 4}) != Poly({Mon(): 3, Mon({'n': 2}): 4})
    assert Poly({Mon(): 3, Mon({'m': 1}): 4}) != Poly({Mon(): 3, Mon({'n': 1}): 4})

  def test_Poly_hash(self):
    assert not len(set(hash(Poly({Mon(): i})) for i in range(10))) == 1
    assert hash(Poly({Mon(): 3, Mon({'n': 1}): 4})) == hash(Poly({Mon({'n': 1}): 4, Mon(): 3}))

  def test_Poly_compare(self):
    poly = Poly({Mon(): 3, Mon({'n': 1}): 4})
    # Assume poly > 0 to make various shape rules work with polymorphic shapes:
    assert poly >= 0
    assert poly >= 1
    assert poly > 0

    assert 0 <= poly
    assert 0 < poly
    assert constant_poly(3) >= 1
    assert constant_poly(3) > 1
    self.assertRaisesRegex(ValueError, "", lambda: poly >= 2)
    self.assertRaisesRegex(ValueError, "", lambda: poly > 1)

  def test_Poly_divmod(self):
    n = Poly({Mon({'n': 1}): 1})
    assert (n, 1) == divmod(2*n+1, 2)
    assert (2*n, 0) == divmod(10*n, 5)
    assert (2*n+4, 3) == divmod(10*n+23, 5)

  def test_Poly_rsub(self):
    n = Poly({Mon({'n': 1}): 1})
    assert -1 - n == -n - 1

  def test_add_broadcast(self):
    @shapecheck(['n', '(m, n)'], '(m, n)')
    @shapecheck(['(m, n)', 'n'], '(m, n)')
    @shapecheck(['n', ''], 'n')
    def add(a, b):
      return a + b

  def test_sum(self):
    @shapecheck(['(m, n)'], '')
    def sum(x):
      return jnp.sum(x)

  def test_prod(self):
    @shapecheck(['(m, n)'], '')
    def prod(x):
      return jnp.prod(x)

  def test_max(self):
    @shapecheck(['(m, n)'], '')
    def prod(x):
      return jnp.max(x)

  def test_min(self):
    @shapecheck(['(m, n)'], '')
    def prod(x):
      return jnp.min(x)

  def test_dot(self):
    @shapecheck(['(m, n)', 'n'], 'm')
    def matvec(A, b):
      return jnp.dot(A, b)

    def thunk():
      @shapecheck(['(m, n)', 'n'], 'm')
      def matvec(A, b):
        return lax.dot_general(A, b, [((0,), (0,)), ((), ())])
    self.assertRaisesRegex(TypeError, "", thunk)

  def test_flatten(self):
    @shapecheck(['(m, n)'], 'm * n')
    def flatten(x):
      return lax.reshape(x, (x.shape[0] * x.shape[1],))

  def test_concatenate(self):
    @shapecheck(['m', 'n', 'm'], '3*m + n')
    def cat(x, y, z):
      return lax.concatenate([x, y, x, z], 0)

    def thunk():
      @shapecheck(['m', 'n', 'm'], '3*m + n')
      def cat(x, y, z):
        return lax.concatenate([x, y, x], 0)
    self.assertRaisesRegex(ShapeError, "", thunk)

  def test_device_put(self):
    @shapecheck(['n'], 'n')
    def d_put(x):
      return api.device_put(x)

  def test_broadcast_in_dim(self):
    x = jnp.zeros(7)

    @shapecheck(['(n,)'], '(3, n, 4)')
    def broadcast_in_dim(x):
      return lax.broadcast_in_dim(x, shape=(3, x.shape[0], 4), broadcast_dimensions=(1,))
    x = jnp.zeros((7, 1))

    @shapecheck(['(n, 1)'], '(3, n, 4, 1)')
    def broadcast_in_dim(x):
      return lax.broadcast_in_dim(x, shape=(3, x.shape[0], 4, x.shape[1]), broadcast_dimensions=(1, 3))

  def test_jit(self):
    @shapecheck(['n'], '2*n')
    @jit
    def concat(x):
      return lax.concatenate([x, x], 0)

    # TODO:
    # @shapecheck(['n'], 'n')
    # @jit
    # @grad
    # def sum_square(x):
    #   return jnp.sum(x ** 2)

  def test_pad(self):
    @shapecheck(['n'], '2*n+1')
    def p(x):
      return lax.pad(x, jnp.array(0., x.dtype), [(1, 1, 1)])

  def test_numpy_pad(self):
    @shapecheck(['n'], 'n+1')
    def p(x):
      return jnp.pad(x, (0, 1))

  @parameterized.named_parameters(jtu.cases_from_list(
    {
      'testcase_name': "strides={}_padding={}_lhs_dilation={}_dimension_numbers"
                       "={}_lhs_perm={}_rhs_perm={}_out_perm={}".format(
        strides, padding, lhs_dilation, dimension_numbers, lhs_perm, rhs_perm, out_perm),
      'strides': strides, 'padding': padding, 'lhs_dilation': lhs_dilation,
      'dimension_numbers': dimension_numbers, 'lhs_perm': lhs_perm,
      'rhs_perm': rhs_perm, 'out_perm': out_perm}
    for strides in [(1, 1), (2, 1)]
    for padding in ['SAME', 'VALID', ((1, 0), (2, 0))]
    for lhs_dilation in (None, (1, 2))
    for dimension_numbers, (lhs_perm, rhs_perm, out_perm) in (
            (("NCHW", "OIHW", "NCHW"), ((0, 1, 2, 3), (0, 1, 2, 3), (0, 1, 2, 3))),
            (("NHWC", "HWIO", "NHWC"), ((0, 2, 3, 1), (2, 3, 1, 0), (0, 2, 3, 1))),
            (("NCHW", "HWIO", "NHWC"), ((0, 1, 2, 3), (2, 3, 1, 0), (0, 2, 3, 1)))
    )
    # String padding is not implemented for transposed convolution, see conv_general_dilated implementation:
    if (lhs_dilation is None or not isinstance(padding, str)) and
    # only test strides with same padding:
    (strides[0] == 1 or padding == 'SAME')))
  def test_conv(self, strides, padding, lhs_dilation,
                dimension_numbers, lhs_perm, rhs_perm, out_perm):
    valid = padding == 'VALID'
    is_strided = strides[0] != 1
    lhs_shape = '({}, {}, {}, {})'.format(*np.take(['n', 'i', '2*h' if is_strided else 'h', 'w'], lhs_perm))
    rhs_shape = '({}, {}, {}, {})'.format(*np.take(['o', 'i', '2', '3'], rhs_perm))
    out_shape = '({}, {}, {}, {})'.format(*np.take([
      'n', 'o', 'h+-1' if valid and not is_strided else 'h',
      ('w+-2' if valid else 'w') if lhs_dilation is None else '2*w+-1'], out_perm))

    @shapecheck([lhs_shape, rhs_shape], out_shape)
    def conv(lhs, rhs):
      return lax.conv_general_dilated(
        lhs, rhs, strides, padding,
        lhs_dilation=lhs_dilation, dimension_numbers=dimension_numbers)

  def test_indexing(self):
    @shapecheck(['n'], '')
    def first(x):
      return x[0]

    @shapecheck(['n'], '')
    def last(x):
      return x[-1]

    @shapecheck(['(n,m,a)'], 'n,m')
    @vmap
    @shapecheck(['(n,a)'], 'n')
    def last_column(x):
      return x[..., -1]

  def test_slicing(self):
    @shapecheck(['n'], 'n+-1')
    def slice(x):
      return x[1:]

    @shapecheck(['n'], 'n+-1')
    def slice(x):
      return x[:-1]

    @shapecheck(['n'], 'n+-1')
    def inverse(x):
      return x[:0:-1]

    @shapecheck(['n'], 'n+-1')
    def inverse(x):
      return x[-2::-1]

  def test_poly_slicing(self):
    @shapecheck(['n'], 'n+-1')
    def slice_poly_stop(x):
      return x[:x.shape[0] - 1]

    # TODO: @shapecheck(['n'], '1')
    def slice_poly_start(x):
      return x[x.shape[0] - 1:]

  def test_iota(self):
    raise SkipTest("not yet implemented")
    # https://travis-ci.org/github/google/jax/jobs/682086351
    @shapecheck(['n'], 'n')
    def range_like(x):
      return lax.iota(jnp.int32, x.shape[0])

  def test_arange(self):
    raise SkipTest("not yet implemented")
    # https://travis-ci.org/github/google/jax/jobs/682086351
    @shapecheck(['n'], 'n')
    def arange_like(x):
      return jnp.arange(x.shape[0], dtype=jnp.int32)

  def test_expit(self):
    @shapecheck(['n'], 'n')
    def expit_(x):
      return expit(x)

  def test_reshape(self):
    @shapecheck(['n, a, b'], 'n, a*b')
    def flatten(x):
      return jnp.reshape(x, (x.shape[0], x.shape[1] * x.shape[2]))

  def test_ravel(self):
    a = jnp.array(1)

    @shapecheck(['n'], '')
    def thunk(n):
      return -(a + n.ravel()[0] * 0)

class MaskingTest(jtu.JaxTestCase):

  def test_sum(self):
    @partial(mask, in_shapes=['n'], out_shape='')
    def padded_sum(x):
      return jnp.sum(x)

    ans = padded_sum([jnp.array([3, 1, 4, 1, 5])], dict(n=3))
    expected = 8
    self.assertAllClose(ans, expected, check_dtypes=False)

    ans = padded_sum([jnp.array([3, 1, 4, 1, 5])], dict(n=4))
    expected = 9
    self.assertAllClose(ans, expected, check_dtypes=False)

  def test_sum_vmap(self):
    @partial(mask, in_shapes=['n'], out_shape='')
    def padded_sum(x):
      return jnp.sum(x)

    ans = vmap(padded_sum)([jnp.ones((5, 10))], dict(n=jnp.arange(5)))
    expected = np.array([0, 1, 2, 3, 4])
    self.assertAllClose(ans, expected, check_dtypes=False)

  def test_add(self):
    @partial(mask, in_shapes=['n', 'n'], out_shape='n')
    def addvecs(x, y):
      return x + y

    x = jnp.array([3, 1, 4, 1, 5, 9])
    y = jnp.array([2, 6, 5, 3, 5, 8])
    ans = addvecs([x, y], dict(n=3))
    expected = np.array([5, 7, 9])
    self.assertAllClose(ans[:3], expected, check_dtypes=False)

    thunk = lambda: addvecs([jnp.arange(5), jnp.arange(6)], dict(n=3))
    self.assertRaisesRegex(ShapeError, "", thunk)

  def test_scan(self):
    @partial(mask, in_shapes=['n'], out_shape='')
    def cumsum(arr):
      out, _ = lax.scan(lambda c, x: (c + x, ()), 0, arr)
      return out

    ans = cumsum([jnp.array([5, 2, 9, 1, 4])], dict(n=3))
    expected = 16
    self.assertAllClose(ans, expected, check_dtypes=False)

  def test_scan_vmap(self):
    @partial(mask, in_shapes=['n'], out_shape='')
    def cumsum(arr):
      out, _ = lax.scan(lambda c, x: (c + x, ()), 0, arr)
      return out

    ans = vmap(cumsum)([jnp.arange(6).reshape(2, 3)], dict(n=jnp.array([1, 2])))
    expected = np.array([0, 7])
    self.assertAllClose(ans, expected, check_dtypes=False)

  def test_scan_jit(self):
    @partial(mask, in_shapes=['n'], out_shape='')
    def cumsum(arr):
      out, _ = lax.scan(lambda c, x: (c + x, ()), 0, arr)
      return out

    @jit
    def jit_cumsum(args, shape_env):
      assert python_should_be_executing
      return cumsum(args, shape_env)

    python_should_be_executing = True
    ans = jit_cumsum([jnp.array([5, 2, 9, 1, 4])], dict(n=3))
    expected = 16
    self.assertAllClose(ans, expected, check_dtypes=False)

    python_should_be_executing = False
    ans = jit_cumsum([jnp.array([5, 2, 9, 1, 4])], dict(n=4))
    expected = 17
    self.assertAllClose(ans, expected, check_dtypes=False)

    python_should_be_executing = False
    ans = jit_cumsum([jnp.array([5, 2, 9, 1, 4])], dict(n=1))
    expected = 5
    self.assertAllClose(ans, expected, check_dtypes=False)

  def test_concatenate(self):
    @partial(mask, in_shapes=['n', 'm', 'n'], out_shape='m + 2 * n')
    def cat(x, y, z):
      return lax.concatenate([x, y, z], 0)

    ans = cat([jnp.array([1, 9]), jnp.array([2, 4, 9]), jnp.array([3, 9])],
              dict(n=1, m=2))
    expected = np.array([1, 2, 4, 3])
    self.assertAllClose(ans[:4], expected, check_dtypes=False)

  def test_dot(self):
    @partial(mask, in_shapes=['(m, k)', '(k, n)'], out_shape='(m, n)')
    def dot(x, y):
      return lax.dot(x, y)

    x = np.arange(6, dtype=np.float32).reshape((2, 3))
    y = np.arange(12, dtype=np.float32).reshape((3, 4))
    ans = dot([x, y], dict(m=2, k=2, n=2))
    expected = np.dot(x[:2, :2], y[:2, :2])
    self.assertAllClose(ans[:2, :2], expected, check_dtypes=False)

  def test_mean(self):
    @partial(mask, in_shapes=['n'], out_shape='')
    def padded_sum(x):
      return jnp.sum(x) / shape_as_value(x.shape)[0]

    ans = padded_sum([jnp.array([3, 1, 4, 1, 5])], dict(n=3))
    expected = 8 / 3
    self.assertAllClose(ans, expected, check_dtypes=False)

  def test_arithmetic(self):
    @partial(mask, in_shapes=['(n, m)', 'm'], out_shape='(n, m)')
    def times(x, y):
      return x * y

    # TODO(shoyer): enable this check when broadcast_in_dim supports masking
    with self.assertRaisesRegex(KeyError, 'broadcast_in_dim'):
      ans = times([jnp.array([[1, 2], [3, 4], [5, 6]]), jnp.array([1, 2])],
                  dict(n=4, m=5))
      # expected = np.array([[1, 2, 3], [8, 10, 12]])
      # self.assertAllClose(ans, expected, check_dtypes=False)

  def test_stack(self):
    @partial(mask, in_shapes=['n','n'], out_shape='(2, n)')
    def stack(x, y):
      return jnp.stack([x, y], 0)

    # TODO(shoyer): enable this check when broadcast_in_dim supports masking
    with self.assertRaisesRegex(KeyError, 'broadcast_in_dim'):
      ans = stack([jnp.array([1, 2, 3]), jnp.array([4, 5, 6])], dict(n=10))
      # expected = np.array([[1, 2, 3], [4, 5, 6]])
      # self.assertAllClose(ans, expected, check_dtypes=False)

  def test_monomorphic(self):
    @partial(mask, in_shapes=['(_, n)'], out_shape='')
    def padded_sum(x):
      return jnp.sum(x)

    ans = padded_sum([jnp.array([[3, 4], [5, 6]])], dict(n=1))
    expected = 8
    self.assertAllClose(ans, expected, check_dtypes=False)

  def test_monomorphic2(self):
    @partial(mask, in_shapes=['(_, n)'], out_shape='n')
    def padded_sum(x):
      return jnp.sum(x, axis=0)

    ans = padded_sum([jnp.array([[3, 4], [5, 6]])], dict(n=2))
    expected = jnp.array([8, 10])
    self.assertAllClose(ans, expected, check_dtypes=False)

  def test_monomorphic3(self):
    @partial(mask, in_shapes=['(_, n)'], out_shape='_')
    def padded_sum(x):
      return jnp.sum(x, axis=1)

    ans = padded_sum([jnp.array([[3, 4], [5, 6]])], dict(n=1))
    expected = jnp.array([3, 5])
    self.assertAllClose(ans, expected, check_dtypes=False)

  def test_rnn(self):
    n = 3

    @partial(mask, in_shapes=['(_, _)', '(t, _)'], out_shape='_')
    def rnn(W, xs):
      def step(h, x):
        new_h = jnp.dot(W, h) + jnp.dot(W, x)
        return new_h, ()
      predicted, _ = lax.scan(step, jnp.zeros(n), xs)
      return predicted

    rng = np.random.RandomState(0)
    W = jnp.eye(n)
    xs = rng.randn(10, n).astype(jnp.float_)
    ans = rnn([W, xs], dict(t=4))
    expected = xs[:4].sum(0)
    self.assertAllClose(ans, expected, check_dtypes=False)

  def test_rnn_grad(self):
    n = 3

    @partial(mask, in_shapes=['(_, _)', '(t, _)', '_'], out_shape='')
    def rnn(W, xs, target):
      def step(h, x):
        new_h = jnp.tanh(jnp.dot(W, h) + jnp.dot(W, x))
        return new_h, ()
      predicted, _ = lax.scan(step, jnp.zeros(n), xs)
      return jnp.sum((predicted - target)**2)

    rng = np.random.RandomState(0)
    W = rng.randn(n, n).astype(jnp.float_)
    xs = rng.randn(10, n).astype(jnp.float_)
    y = rng.randn(n).astype(jnp.float_)

    ans = grad(lambda W: rnn([W, xs, y], dict(t=4)))(W)

    def rnn_reference(W, xs, target):
      h = jnp.zeros(n)
      for x in xs:
        h = jnp.tanh(jnp.dot(W, h) + jnp.dot(W, x))
      predicted = h
      return jnp.sum((predicted - target)**2)

    expected = grad(lambda W: rnn_reference(W, xs[:4], y))(W)

    self.assertAllClose(ans, expected, check_dtypes=False)

  def test_ragged_batched_rnn(self):
    n = 3

    @partial(mask, in_shapes=('(_, _)', '(t, _)', '_'), out_shape='')
    def rnn(W, xs, target):
      def step(h, x):
        new_h = jnp.tanh(jnp.dot(W, h) + jnp.dot(W, x))
        return new_h, ()
      predicted, _ = lax.scan(step, jnp.zeros(n), xs)
      return jnp.sum((predicted - target)**2)

    rng = np.random.RandomState(0)
    W = rng.randn(n, n).astype(jnp.float_)
    seqs = rng.randn(3, 10, n).astype(jnp.float_)
    ts = jnp.array([2, 5, 4])
    ys = rng.randn(3, n)

    ans = grad(lambda W: vmap(rnn, ((None, 0, 0), 0))((W, seqs, ys), dict(t=ts)).sum())(W)

    def rnn_reference(W, seqs, targets):
      total_loss = jnp.array(0, jnp.float_)
      for xs, target in zip(seqs, targets):
        h = jnp.zeros(n)
        for x in xs:
          h = jnp.tanh(jnp.dot(W, h) + jnp.dot(W, x))
        predicted = h
        total_loss = total_loss + jnp.sum((predicted - target)**2)
      return total_loss

    seqs_ = [xs[:t] for xs, t in zip(seqs, ts)]
    expected = grad(lambda W: rnn_reference(W, seqs_, ys).sum())(W)

    self.assertAllClose(
        ans, expected, check_dtypes=False,
        rtol=2e-2 if jtu.device_under_test() == "tpu" else 1e-5)

  def test_nesting(self):
    raise SkipTest("not yet implemented")

    @partial(mask, in_shapes=['n'], out_shape='')
    def padded_sum(x):
      return jnp.sum(x)

    batched_sum = vmap(padded_sum)

    @partial(mask, in_shapes=['(m, _)', 'm'], out_shape='')
    def fun(x, ns):
      return batched_sum([x], dict(n=ns)).sum()

    x = jnp.array([[3, 1, 4, 1],
                  [5, 9, 2, 6],
                  [5, 3, 5, 8]])
    ns = jnp.array([2, 3, 2])
    ans = fun([x, ns], dict(m=2))
    expected = 3+1 + 5+9+2
    self.assertAllClose(ans, expected, check_dtypes=False)

  def test_arange(self):
    raise SkipTest("not yet implemented")

    @partial(mask, in_shapes=['n'], out_shape='n')
    def padded_add(x):
      return x + lax.iota(x.shape[0])

    ans = padded_add([jnp.array([3, 1, 4, 1, 5])], dict(n=3))
    expected = np.array([3, 2, 6])
    self.assertAllClose(ans[:3], expected, check_dtypes=False)

  @parameterized.named_parameters(jtu.cases_from_list(
      {"testcase_name": "_start={}_stop={}_step={}_length={}"
       .format(start, stop, step, length),
       "start": start, "stop": stop, "step": step, "length": length}
      for length in range(1, 5)
      for start, stop, step
      in it.product(it.chain([None], range(-10, 10)), repeat=3)
      if step != 0))
  def test_slice_indices(self, start, stop, step, length):
    s = slice(start, stop, step)
    assert _polymorphic_slice_indices(s, length) == s.indices(length)

  def test_slice_index_poly_start(self):
    n = Poly({Mon({'n': 1}): 1})
    s = slice(n, None, None)
    assert (n, 2 * n, 1) == _polymorphic_slice_indices(s, 2 * n)


  def test_slice_oob_indexing(self):
    # https://github.com/google/jax/issues/2245
    self.assertAllClose(jnp.ones(5), jnp.ones(5)[:10], check_dtypes=True)
    self.assertAllClose(jnp.ones(5), jnp.ones(5)[-10:], check_dtypes=True)

if __name__ == '__main__':
  absltest.main()
