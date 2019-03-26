from jax.scan import scan, scan_reference
from jax.core import pack
import jax.core as core
import jax.numpy as np
from jax import jvp

# scan :: (a -> c -> (b,c)) -> c -> [a] -> ([b],c)

def f(x, carry):
  carry = carry + x
  y = pack((carry**2, -carry))
  return pack((y, carry))

print scan(f, 0.0, np.arange(4))
print scan_reference(f, 0.0, np.arange(4))

# def cumsum(xs):
#   def f(x, carry):
#     carry = carry + x
#     return pack((carry, carry))

#   ys, _ = scan(f, 0.0, xs)
#   return ys

# x = np.linspace(0, 3, 4)

# print x
# print np.cumsum(x)
# print cumsum(x)


# print jvp(np.cumsum, (x,), (x*0.1,))
# print jvp(cumsum, (x,), (x*0.1,))
