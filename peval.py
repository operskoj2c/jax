from functools import partial
import numpy as onp
import jax.numpy as np
from jax import jit, pjit, grad, linearize, jvp, make_jaxpr
from jax.lax import psum

@partial(pjit, axis_name='i', axis_size=1)
def f(x):
  return np.sin(x)

x = onp.arange(2).reshape(1, 2).astype(onp.float32)
print f(x)

def splitjvp(x):
  _, jvp = linearize(f, x)
  return jvp(np.ones_like(x))

print splitjvp(x)
# print make_jaxpr(splitjvp)(x)

# TODO TODO register process_map stuff
