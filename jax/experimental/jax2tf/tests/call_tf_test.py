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
"""Tests for call_tf."""

from typing import Callable, Dict, Tuple
import unittest

from absl.testing import absltest
from absl.testing import parameterized

import jax
from jax import lax
from jax import numpy as jnp
from jax import test_util as jtu
from jax.config import config
from jax.experimental import jax2tf
from jax.experimental.jax2tf.tests import tf_test_util

import numpy as np

try:
  import tensorflow as tf  # type: ignore[import]
except ImportError:
  tf = None

config.parse_flags_with_absl()


def _maybe_jit(with_jit: bool, func: Callable) -> Callable:
  if with_jit:
    return jax.jit(func)
  else:
    return func


parameterized_jit = parameterized.named_parameters(
    dict(testcase_name="_jit" if with_jit else "", with_jit=with_jit)
    for with_jit in [True, False])


class CallTfTest(jtu.JaxTestCase):

  def setUp(self):
    if tf is None:
      raise unittest.SkipTest("Test requires tensorflow")
    # TODO(b/171320191): this line works around a missing context initialization
    # bug in TensorFlow.
    _ = tf.add(1, 1)
    super().setUp()

  #@parameterized_jit
  def test_eval_scalar_arg(self, with_jit=True):
    def f_tf(x):
      return tf.math.sin(x)
    x = 3.
    res = _maybe_jit(with_jit, jax2tf.call_tf(f_tf))(x)
    self.assertAllClose(jnp.sin(x), res, check_dtypes=False)

  @parameterized_jit
  def test_eval_scalar_res(self, with_jit=False):
    x = 3.
    res = _maybe_jit(with_jit, jax2tf.call_tf(lambda x: 4.))(x)
    self.assertAllClose(4., res, check_dtypes=False)

  @parameterized_jit
  def test_eval_numpy_arg(self, with_jit=False):
    x = np.ones((2, 3), dtype=np.float32)
    res = _maybe_jit(with_jit, jax2tf.call_tf(tf.math.sin))(x)
    self.assertAllClose(jnp.sin(x), res, check_dtypes=False)

  @parameterized_jit
  def test_eval_numpy_res(self, with_jit=False):
    x = np.ones((2, 3), dtype=np.float32)
    res = _maybe_jit(with_jit, jax2tf.call_tf(lambda _: x))(x)
    self.assertAllClose(x, res, check_dtypes=False)

  def test_eval_numpy_no_copy(self):
    if jtu.device_under_test() != "cpu":
      raise unittest.SkipTest("no_copy test works only on CPU")
    # For ndarray, zero-copy only works for sufficiently-aligned arrays.
    x = np.ones((16, 16), dtype=np.float32)
    res = jax2tf.call_tf(lambda x: x)(x)
    self.assertAllClose(x, res)
    self.assertTrue(np.shares_memory(x, res))

  @parameterized_jit
  def test_eval_devicearray_arg(self, with_jit=False):
    x = jnp.ones((2, 3), dtype=np.float32)
    res = _maybe_jit(with_jit, jax2tf.call_tf(tf.math.sin))(x)
    self.assertAllClose(jnp.sin(x), res, check_dtypes=False)

  def test_eval_devicearray_no_copy(self):
    if jtu.device_under_test() != "cpu":
      # TODO(necula): add tests for GPU and TPU
      raise unittest.SkipTest("no_copy test works only on CPU")
    # For DeviceArray zero-copy works even if not aligned
    x = jnp.ones((3, 3), dtype=np.float32)
    res = jax2tf.call_tf(lambda x: x)(x)
    self.assertAllClose(x, res)
    self.assertTrue(np.shares_memory(x, res))

  @parameterized_jit
  def test_eval_pytree(self, with_jit=True):

    def fun_tf(x: Dict, y: Tuple) -> Tuple:
      return (x["first"] * x["second"], y[0] + y[1])

    x = dict(first=np.float32(3.), second=np.float32(4.))
    y = (np.float64(5.), np.float64(6.))
    fun_jax = _maybe_jit(with_jit, jax2tf.call_tf(fun_tf))
    res = fun_jax(x, y)
    self.assertAllClose((np.float32(12.), np.float64(11.)), res)

  def test_eval_non_compileable(self):
    # Check that in op-by-op we call a function in eager mode.
    def f_tf_non_compileable(x):
      return tf.strings.length(tf.strings.format("Hello {}!", [x]))

    f_jax = jax2tf.call_tf(f_tf_non_compileable)
    x = np.float32(0.7)
    self.assertAllClose(f_tf_non_compileable(x).numpy(), f_jax(x))


  @parameterized_jit
  def test_control_flow(self, with_jit=True):

    def times_5_tf(x):
      # Multiply x * 5 using a loop
      c = lambda i, acc: tf.less(i, 5)
      b = lambda i, acc: (tf.add(i, 1), tf.add(acc, x))
      _, acc = tf.while_loop(c, b, [tf.constant(0), tf.constant(0.)])
      return acc

    def fun_jax(x):
      # Calls times_5_tf 3 times in a loop
      def body(_, acc):
        return jax2tf.call_tf(times_5_tf)(acc)

      return lax.fori_loop(0, 3, body, x)

    x = np.float32(3.)
    res = _maybe_jit(with_jit, fun_jax)(x)
    self.assertAllClose(np.float32(x * 5 * 5 * 5), res)

  @parameterized.named_parameters(
      dict(
          testcase_name=f"_{dtype.__name__}{'_jit' if with_jit else ''}",
          dtype=dtype,
          with_jit=with_jit)
      # TF does not support yet add for uint16 and uint64
      for dtype in set(jtu.dtypes.all) - set([np.bool_, np.uint16, np.uint64])
      for with_jit in [True, False])
  def test_dtypes(self, dtype=np.int32, with_jit=True):

    def fun_tf(x):
      # AddV2 supports more types
      return tf.raw_ops.AddV2(x=x, y=tf.constant(3, dtype=dtype))

    def fun_jax(x):
      return jax2tf.call_tf(fun_tf)(x) + x

    x = np.ones((3,), dtype=dtype)
    res = _maybe_jit(with_jit, fun_jax)(x)
    self.assertAllClose(dtype(2 * x + 3), res)

  @parameterized_jit
  def test_bool(self, with_jit=False):

    def fun_tf(x, y):
      return tf.math.logical_and(x, y)

    x = np.array([True, False, True, False], dtype=np.bool_)
    y = np.array([True, True, False, False], dtype=np.bool_)
    res = _maybe_jit(with_jit, jax2tf.call_tf(fun_tf))(x, y)
    self.assertAllClose(
        np.array([True, False, False, False], dtype=np.bool_), res)

  @parameterized_jit
  def test_with_var_read(self, with_jit=True):
    if jtu.device_under_test() == "gpu":
      raise unittest.SkipTest("Test fails on GPU")
    outer_var_array = np.array([3., 4.], dtype=np.float32)
    outer_var = tf.Variable(outer_var_array)

    def fun_tf(x):
      return x * outer_var + 1.

    x = np.array([2., 5.,], dtype=np.float32)
    res = _maybe_jit(with_jit, jax2tf.call_tf(fun_tf))(x)
    self.assertAllClose(x * outer_var_array + 1., res, check_dtypes=False)

  def test_with_var_different_shape(self):
    # See https://github.com/google/jax/issues/6050
    if jtu.device_under_test() == "gpu":
      raise unittest.SkipTest("Test fails on GPU")
    v = tf.Variable((4., 2.), dtype=tf.float32)

    def tf_func(x):
      return v + x
    x = np.float32(123.)
    tf_out = tf_func(x)

    jax_func = jax.jit(jax2tf.call_tf(tf_func))
    jax_out = jax_func(x)

    self.assertAllClose(tf_out, jax_out, check_dtypes=False)

  @parameterized_jit
  def test_with_var_write_error(self, with_jit=True):
    if with_jit:
      raise unittest.SkipTest("variable writes not yet working")
    outer_var = tf.Variable(3., dtype=np.float32)

    def fun_tf(x):
      outer_var.assign(tf.constant(4.))
      return x * outer_var + 1.

    x = np.float32(2.)
    res = _maybe_jit(with_jit, jax2tf.call_tf(fun_tf))(x)
    self.assertAllClose(x * 4. + 1, res, check_dtypes=False)

  @parameterized_jit
  def test_with_tensor_capture(self, with_jit=False):
    outer_tensor = tf.constant(3., dtype=np.float32)

    def fun_tf(x):
      return x * outer_tensor + 1.

    x = np.float32(2.)
    res = _maybe_jit(with_jit, jax2tf.call_tf(fun_tf))(x)
    self.assertAllClose(x * 3. + 1., res, check_dtypes=False)

  @parameterized_jit
  def test_with_multiple_capture(self, with_jit=True):
    if jtu.device_under_test() == "gpu":
      raise unittest.SkipTest("Test fails on GPU")
    v2 = tf.Variable(2., dtype=np.float32)
    v3 = tf.Variable(3., dtype=np.float32)
    t4 = tf.constant(4., dtype=np.float32)
    t5 = tf.constant(5., dtype=np.float32)

    def fun_tf(x):
      return (x * v3 + t4 + v2) * v3 + t5

    x = np.float32(2.)
    res = _maybe_jit(with_jit, jax2tf.call_tf(fun_tf))(x)
    self.assertAllClose((x * 3. + 4. + 2.) * 3. + 5., res, check_dtypes=False)

  @parameterized_jit
  def test_grad(self, with_jit=False):
    x = np.float32(3.)
    res = _maybe_jit(with_jit, jax.grad(jax2tf.call_tf(tf.math.sin)))(x)
    self.assertAllClose(np.cos(x), res)

  @parameterized_jit
  def test_grad_pytree(self, with_jit=False):

    def fun_tf(x: Dict, y: Tuple) -> Tuple:
      return (x["first"] * x["second"] + 3. * y[0] + 4. * y[1])

    x = dict(first=np.float32(3.), second=np.float32(4.))
    y = (np.float32(5.), np.float32(6.))
    grad_x = _maybe_jit(with_jit, jax.grad(jax2tf.call_tf(fun_tf)))(x, y)
    self.assertAllClose(
        dict(first=np.float32(4.), second=np.float32(3.)), grad_x)

  @parameterized_jit
  def test_grad_custom(self, with_jit=False):

    @tf.custom_gradient
    def func_square_tf(x):
      # Like x ** 2, but with custom grad 3. * x
      def grad(dy, variables=None):
        # dy, = dys
        return 3. * x * dy,

      return x * x, grad

    x = np.float32(4.)
    grad_x = _maybe_jit(with_jit, jax.grad(jax2tf.call_tf(func_square_tf)))(x)
    self.assertAllClose(np.float32(3.) * x, grad_x)

  @parameterized.named_parameters(
      dict(
          testcase_name=f"_degree={degree}{'_jit' if with_jit else ''}",
          degree=degree,
          with_jit=with_jit)
      for degree in [1, 2, 3, 4]
      for with_jit in [True, False])
  def test_higher_order_grad(self, degree=2, with_jit=False):

    def fun_tf(x):
      return 2. * x * x * x

    def fun_jax(x):
      return 3. * _maybe_jit(with_jit, jax2tf.call_tf(fun_tf))(x)

    def fun_jax_pure(x):
      return 3. * fun_tf(x)

    grad_jax = fun_jax
    grad_jax_pure = fun_jax_pure
    for _ in range(degree):
      grad_jax = jax.grad(grad_jax)
      grad_jax_pure = jax.grad(grad_jax_pure)

    res_jax = grad_jax(np.float32(5.))
    print(f"Grad of {degree} degree is {res_jax}")
    self.assertAllClose(res_jax, grad_jax_pure(np.float32(5.)))

  def test_pmap(self):
    print(f"Running test_pmap on {jax.local_device_count()} devices")

    def plus_2_tf(x):
      return tf.math.add(2., x)

    def fun_jax(x):
      return np.float32(3.) * jax2tf.call_tf(plus_2_tf)(x)

    x = np.arange(jax.local_device_count(), dtype=np.float32)
    res = jax.pmap(fun_jax)(x)
    self.assertAllClose(np.float32(3. * (x + 2)), res)

  def test_round_trip(self):
    f_jax = jnp.sin
    f_jax_rt = jax2tf.call_tf(jax2tf.convert(f_jax))
    x = np.float32(0.7)
    self.assertAllClose(f_jax(x), f_jax_rt(x))

  def test_round_trip_custom_grad(self):
    @jax.custom_vjp
    def f(x):
      return x * x

    # f_fwd: a -> (b, residual)
    def f_fwd(x):
      return f(x), np.float32(3.) * x
    # f_bwd: (residual, CT b) -> [CT a]
    def f_bwd(residual, ct_b):
      return residual * ct_b,

    f.defvjp(f_fwd, f_bwd)

    f_rt = jax2tf.call_tf(jax2tf.convert(f, with_gradient=True))
    x = np.float32(0.7)
    self.assertAllClose(f(x), f_rt(x))
    self.assertAllClose(jax.grad(f)(x), jax.grad(f_rt)(x))

  def test_round_trip_shape_poly(self):
    f_jax = jnp.sin
    f_jax_rt = jax2tf.call_tf(jax2tf.convert(f_jax,
                                             polymorphic_shapes=["(b, ...)"]))
    x = np.array([0.7, 0.8], dtype=np.float32)
    self.assertAllClose(f_jax(x), f_jax_rt(x))

  def test_round_trip_saved_model_shape_poly(self):
    tracing_count = 0
    def f_jax(x):
      nonlocal tracing_count
      tracing_count += 1
      return jnp.sin(x)

    f_tf = jax2tf.convert(f_jax, polymorphic_shapes=["(b, ...)"])
    x = np.array([0.7, 0.8], dtype=np.float32)
    res_jax = f_jax(x)
    self.assertEqual(1, tracing_count)
    # Will trace twice, it seems. Once to get the result signature, and once again
    # for the actual saving.
    restored_f = tf_test_util.SaveAndLoadFunction(f_tf, [tf.TensorSpec([None], x.dtype)])
    self.assertGreaterEqual(tracing_count, 2)
    tracing_count = 0
    f_jax_rt = jax2tf.call_tf(restored_f)
    self.assertAllClose(res_jax, f_jax_rt(x))
    # Ensure that restored_f works at other batch size as well
    y = np.concatenate([x, x])
    self.assertEqual(0, tracing_count)
    res_jax_y = f_jax(y)
    self.assertEqual(1, tracing_count)
    # No more tracing for f_jax_rt
    self.assertAllClose(res_jax_y, f_jax_rt(y))
    self.assertEqual(1, tracing_count)

  def test_round_trip_custom_grad_saved_model(self):
    @jax.custom_vjp
    def f(x):
      return x * x

    # f_fwd: a -> (b, residual)
    def f_fwd(x):
      return f(x), np.float32(3.) * x
    # f_bwd: (residual, CT b) -> [CT a]
    def f_bwd(residual, ct_b):
      return residual * ct_b,

    f.defvjp(f_fwd, f_bwd)
    def g(x):
      return jnp.sum(f(x))

    g_tf = tf_test_util.SaveAndLoadFunction(
        jax2tf.convert(g, with_gradient=True, polymorphic_shapes=["b, ..."]),
        [tf.TensorSpec([None], dtype=tf.float32)])
    g_rt = jax2tf.call_tf(g_tf)
    x = np.array([0.7], dtype=np.float32)
    self.assertAllClose(g(x), g_rt(x))
    self.assertAllClose(jax.grad(g)(x), jax.grad(g_rt)(x))

  def test_round_trip_without_gradient_saved_model(self):
    # Explicitly with_gradient=False
    f_jax = jnp.sum

    x = np.array([0.7, 0.8], dtype=np.float32)
    f_tf = tf_test_util.SaveAndLoadFunction(
        jax2tf.convert(f_jax, with_gradient=False),
        [tf.TensorSpec(x.shape, dtype=x.dtype)])
    f_rt = jax2tf.call_tf(f_tf)

    self.assertAllClose(f_jax(x), f_rt(x))
    with self.assertRaisesRegex(Exception,
                                "Gradient explicitly disabled.*jax2tf-converted function does not support gradients. Use `with_gradient` parameter to enable gradients"):
      jax.grad(f_rt)(x)

  def test_round_trip_saved_model_no_gradients(self):
    # Save without gradients
    f_jax = jnp.sum

    x = np.array([0.7, 0.8], dtype=np.float32)
    f_tf = tf_test_util.SaveAndLoadFunction(
        jax2tf.convert(f_jax, with_gradient=True),
        [tf.TensorSpec(x.shape, dtype=x.dtype)],
        save_gradients=False)
    f_rt = jax2tf.call_tf(f_tf)

    self.assertAllClose(f_jax(x), f_rt(x))
    # TODO: clean this up b/191117111: it should fail with a clear error
    # The following results in a confusing error:
    # TypeError: An op outside of the function building code is being passed
    # a "Graph" tensor. It is possible to have Graph tensors
    # leak out of the function building context by including a
    # tf.init_scope in your function building code.
    # For example, the following function will fail:
    #   @tf.function
    #   def has_init_scope():
    #     my_constant = tf.constant(1.)
    #     with tf.init_scope():
    #       added = my_constant * 2
    # The graph tensor has name: args_0:0
    # g = jax.grad(f_rt)(x)

  def test_module_documentation(self):
    def cos_tf(x):
      return tf.math.cos(x)

    # Compute cos with TF and sin with JAX
    def cos_tf_sin_jax(x):
      return jax.numpy.sin(jax2tf.call_tf(cos_tf)(x))

    # Calls `cos_tf` in TF eager mode
    x = np.float32(1.)
    cos_tf_sin_jax(x)

    # Compiles `cos_tf` using TF and embeds the XLA computation into the JAX
    # XLA computation (containing `sin`). The XLA compiler may even be able to
    # fuse through JAX-TF computations.
    jax.jit(cos_tf_sin_jax)(x)

    # Uses TF gradient for `cos_tf` and JAX gradient for `sin`
    jax.grad(cos_tf_sin_jax)(x)

    print(jax.make_jaxpr(cos_tf_sin_jax)(x))
    print(jax.xla_computation(cos_tf_sin_jax)(x).as_hlo_text())

  def test_round_trip_reverse(self):
    f_tf = tf.math.sin
    f_tf_rt = jax2tf.convert(jax2tf.call_tf(f_tf))
    x = np.float32(0.7)
    self.assertAllClose(f_tf(x).numpy(), f_tf_rt(x).numpy())


if __name__ == "__main__":
  absltest.main(testLoader=jtu.JaxTestLoader())
