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

from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import itertools as it
from collections import namedtuple, Counter, defaultdict

from .. import core
from .. import linear_util as lu
from ..abstract_arrays import ShapedArray, ConcreteArray
from ..linear_util import thunk, transformation, transformation_with_aux
from ..util import unzip2, safe_zip, safe_map, toposort, partial
from ..core import (Trace, Tracer, new_master, Jaxpr, JaxprEqn, get_aval, pack,
                    AbstractValue, AbstractTuple, unit, unitvar, Primitive,
                    call_p, TypedJaxpr)

map = safe_map
zip = safe_zip
def identity(x): return x

class JaxprTrace(Trace):
  def pure(self, val):
    return self.new_const(val)

  def lift(self, val):
    return self.new_const(val)

  def sublift(self, val):
    return JaxprTracer(self, val.pval, FreeVar(val))

  def new_const(self, val):
    if isinstance(val, Tracer) and val.trace.level == self.level:
      raise Exception
    return JaxprTracer(self, PartialVal((None, val)), unit)

  def new_instantiated_const(self, val):
    return JaxprTracer(self, PartialVal((get_aval(val), unit)), ConstVar(val))

  def new_arg(self, pval):
    _, const = pval
    return JaxprTracer(self, pval, LambdaBinding())

  def instantiate_const(self, tracer):
    pv, const = tracer.pval
    if isinstance(pv, AbstractValue):
      return tracer
    elif isinstance(pv, JaxprTracerTuple):
      return pack(map(lambda t: self.instantiate_const(self.full_raise(t)), tracer))
    elif pv is None:
      return self.new_instantiated_const(const)
    else:
      raise TypeError(pv)

  def process_primitive(self, primitive, tracers, params):
    if primitive in custom_partial_eval_rules:
      partial_eval = custom_partial_eval_rules[primitive]
      return partial_eval(self, *tracers, **params)
    else:
      tracers = map(self.instantiate_const, tracers)
      avals = [t.aval for t in tracers]
      out_aval = primitive.abstract_eval(*avals, **params)
      partial_val = PartialVal((out_aval, unit))
      eqn = JaxprEqn(tracers, None, primitive, (), False, False, params)
      return JaxprTracer(self, partial_val, eqn)

  def pack(self, tracers):
    eqn = JaxprEqn(tracers, None, core.pack_p, (), False, False, {})
    pval = pack_pvals([t.pval for t in tracers])
    return JaxprTracer(self, pval, eqn)

  def process_call(self, call_primitive, f, tracers, params):
    if call_primitive in map_primitives:
      return self.process_map(call_primitive, f, tracers, params)
    in_pvs, in_consts = unzip2([t.pval for t in tracers])
    fun, aux = partial_eval(f, self, in_pvs)
    out_pv_const, consts = call_primitive.bind(fun, *in_consts, **params)
    out_pv, jaxpr, env = aux()
    const_tracers = map(self.new_instantiated_const, consts)
    env_tracers = map(self.full_raise, env)
    bound_subjaxpr = (jaxpr, const_tracers, env_tracers)
    eqn = JaxprEqn(tracers, None, call_primitive, (bound_subjaxpr,),
                   False, False, params)
    return JaxprTracer(self, PartialVal((out_pv, out_pv_const)), eqn)

  def process_map(self, call_primitive, f, tracers, params):
    in_pvs, in_consts = unzip2([t.pval for t in tracers])
    reduced_pvs = map(remove_axis_from_pv, in_pvs)
    fun, aux = partial_eval(f, self, reduced_pvs)
    out_const, consts = call_primitive.bind(fun, *in_consts, **params)
    out_pv_reduced, jaxpr, env = aux()
    out_pv = add_axis_to_pv(params['axis_size'], out_pv_reduced)
    const_tracers = map(self.new_instantiated_const, consts)
    env_tracers = map(self.full_raise, env)
    jaxpr_converted = jaxpr.copy()
    jaxpr_converted.constvars = []
    jaxpr_converted.invars = list(it.chain(jaxpr.constvars, jaxpr.invars))
    invars = tuple(it.chain(const_tracers, tracers))
    bound_subjaxpr = (jaxpr_converted, (), env)
    eqn = JaxprEqn(invars, None, call_primitive, (bound_subjaxpr,),
                   False, False, params)
    return JaxprTracer(self, PartialVal((out_pv, out_const)), eqn)

  def post_process_call(self, call_primitive, out_tracer):
    # TODO(mattjj): post_process_map
    jaxpr, consts, env = tracers_to_jaxpr([], out_tracer)
    out_pv, out_pv_const = out_tracer.pval
    out = pack((out_pv_const, pack(consts)))
    master = self.master
    def todo(x):
      out_pv_const, consts = x
      trace = JaxprTrace(master, core.cur_sublevel())
      const_tracers = map(trace.new_instantiated_const, consts)
      env_tracers = map(trace.full_raise, env)
      bound_subjaxpr = (jaxpr, const_tracers, env_tracers)
      eqn = JaxprEqn([], None, call_primitive, (bound_subjaxpr,),
                     False, False, {})
      return JaxprTracer(trace, PartialVal((out_pv, out_pv_const)), eqn)

    return out, todo

map_primitives = set()

def unzip_scan_jaxpr(jaxpr, consts, init, xs, avals):
  f = lu.wrap_init(partial(core.eval_jaxpr, jaxpr))


  assert False


def scan_process_primitive(trace, consts, init, xs, avals, jaxpr):
  jaxpr1, jaxpr2, avals1, avals2, ans_pv = unzip_scan_jaxpr(
    jaxpr, consts, init, xs, avals)
  const_pv , consts_const = consts
  init_pv  , inits_const = init
  xs_pv    , xs_const = xs

  ans = scan_p.bind(consts_const, inits_const, xs_const,
                    avals=avals1, jaxpr=jaxpr1)

  params_out = {'avals' : avals2, 'jaxpr' : jaxpr2}
  eqn = JaxprEqn([consts, init, xs], None, scan_p, (), False, False, params_out)
  return JaxprTracer(trace, PartialVal((ans, ans_pv)),  )

    # in_pvs, in_consts = unzip2([t.pval for t in tracers])
    # fun, aux = partial_eval(f, self, in_pvs)
    # out_pv_const, consts = call_primitive.bind(fun, *in_consts, **params)
    # out_pv, jaxpr, env = aux()
    # const_tracers = map(self.new_instantiated_const, consts)
    # env_tracers = map(self.full_raise, env)
    # bound_subjaxpr = (jaxpr, const_tracers, env_tracers)
    # eqn = JaxprEqn(tracers, None, call_primitive, (bound_subjaxpr,), False, params)
    # return JaxprTracer(self, PartialVal((out_pv, out_pv_const)), eqn)



    # tracers = map(self.instantiate_const, tracers)
    # avals = [t.aval for t in tracers]
    # out_aval = primitive.abstract_eval(*avals, **params)
    # eqn = JaxprEqn(tracers, None, primitive, (), False, params)
    # return JaxprTracer(self, PartialVal((out_aval, unit)), eqn)
  assert False



def remove_axis_from_pv(pv):
  if pv is None:
    return pv
  elif isinstance(pv, AbstractValue):
    return remove_axis_from_aval(pv)
  elif type(pv) is JaxprTracerTuple:
    return JaxprTracerTuple(map(remove_axis_from_pv, pv))
  else:
    raise TypeError(type(pv))

def remove_axis_from_aval(aval):
  if type(aval) is AbstractTuple:
    return AbstractTuple(map(remove_axis_from_aval, aval))
  elif isinstance(aval, ShapedArray):
    # might be raising abstraction level from Concrete here
    return ShapedArray(aval.shape[1:], aval.dtype)
  else:
    raise NotImplementedError  # TODO(mattjj)

def add_axis_to_pv(size, pv):
  if pv is None:
    return pv
  elif isinstance(pv, AbstractValue):
    return add_axis_to_aval(size, pv)
  elif type(pv) is JaxprTracerTuple:
    return JaxprTracerTuple(map(partial(add_axis_to_pv, size), pv))
  else:
    raise TypeError(type(pv))

def add_axis_to_aval(size, aval):
  if type(aval) is AbstractTuple:
    return AbstractTuple(map(partial(add_axis_to_aval, size), aval))
  elif isinstance(aval, ShapedArray):
    return ShapedArray((size,) + aval.shape, aval.dtype)
  else:
    raise NotImplementedError  # TODO(mattjj)


def partial_eval(f, trace, pvs):
  f = trace_to_subjaxpr(f, trace.master, False)
  return partial_eval_wrapper(f, tuple(pvs))


@transformation_with_aux
def partial_eval_wrapper(avals, *consts):
  jaxpr, (out_pval, consts, env) = yield (map(PartialVal, zip(avals, consts)),)
  out_pv, out_const = out_pval
  out = pack((out_const, pack(consts)))
  yield out, (out_pv, jaxpr, env)


def abstract_eval_fun(fun, *avals, **params):
  pvs_in = [PartialVal((a, unit)) for a in avals]
  _, pvout, _ = trace_to_jaxpr(lu.wrap_init(fun, params), pvs_in)
  aval_out, _ = pvout
  return aval_out


class JaxprTracer(Tracer):
  __slots__ = ['pval', 'recipe']

  def __init__(self, trace, pval, recipe):
    assert isinstance(pval, PartialVal)
    pv, const = pval
    if isinstance(const, Tracer):
      assert const.trace.level < trace.level
    self.trace = trace
    self.pval = pval
    self.recipe = recipe

  def __repr__(self):
    return 'Traced<{}:{}>'.format(self.aval, self.trace)

  @property
  def aval(self):
    pv, const = self.pval
    return partial_val_aval(pv, const)

  @property
  def parents(self):
    if isinstance(self.recipe, JaxprEqn):
      return eqn_parents(self.recipe)
    elif isinstance(self.recipe, Destructuring):
      return eqn_parents(self.recipe.eqn)
    else:
      return []

  def ispure(self):
    pv, _ = self.pval
    return pv is None

  def full_lower(self):
    if self.ispure():
      _, const = self.pval
      return core.full_lower(const)
    else:
      return self

  def unpack(self):
    pv, const = self.pval
    if isinstance(pv, (AbstractValue, JaxprTracerTuple)):
      n = len(pv)
      if isinstance(pv, AbstractValue):
        const = [unit for _ in range(n)]
      key = object()
      eqn = JaxprEqn([self], [None]*n, core.identity_p, (), False, True, {})
      def child_tracer(i, pval, c):
        d = Destructuring(i, eqn, key)
        return JaxprTracer(self.trace, PartialVal((pval, c)), d).full_lower()
      return map(child_tracer, range(n), pv, const)
    elif pv is None:
      return const
    else:
      raise TypeError(pv)

class JaxprTracerTuple(tuple): pass

Destructuring = namedtuple('Destructuring', ['i', 'eqn', 'key'])

class PartialVal(tuple):
  def __new__(cls, xs):
    assert core.skip_checks or (
        isinstance(xs[0], valid_pv_types)
        and isinstance(xs[1], core.Tracer) or core.valid_jaxtype(xs[1])
    ), xs
    return tuple.__new__(cls, xs)

valid_pv_types = (AbstractValue, JaxprTracerTuple, type(None))


abstract_unit = core.AbstractTuple()

def merge_pvals(val, pval):
  pv, const = pval
  if isinstance(pv, AbstractValue):
    return val
  elif isinstance(pv, JaxprTracerTuple):
    return pack(map(merge_pvals, val, zip(pv, const)))
  elif pv is None:
    return const
  else:
    raise TypeError(pv)

def join_pvals(pval1, pval2):
  pv1, const1 = pval1
  pv2, const2 = pval2
  if pv1 is None and pv2 is None:
    aval1, aval2 = core.get_aval(const1), core.get_aval(const2)
    if aval1 == aval2:
      return pval1  # both pvals known, equal constants
    else:
      aval = core.lattice_join(aval1, aval2)
      return PartialVal((aval, unit))  # both pvals known, different constants
  elif pv1 is None and isinstance(pv2, AbstractValue):
    aval = pv2
    return PartialVal((aval, unit))  # first pval known, second not known
  elif isinstance(pv1, AbstractValue) and pv2 is None:
    aval = pv1
    return PartialVal((aval, unit))  # first pval not known, second known
  elif isinstance(pv1, AbstractValue) and isinstance(pv2, AbstractValue):
    aval = core.lattice_join(pv1, pv2)
    return PartialVal((aval, unit))  # neither is known
  else:
    # the pvals are tuples with some mixtures of known/unknown
    assert isinstance(pv1, JaxprTracerTuple) or isinstance(pv2, JaxprTracerTuple)
    pv1 = [None] * len(pv2) if pv1 is None else pv1
    pv2 = [None] * len(pv1) if pv2 is None else pv2
    pvals1, pvals2 = zip(pv1, const1), zip(pv2, const2)
    join_pvs, join_consts = unzip2(map(join_pvals, pvals1, pvals2))
    if all(isinstance(pv, AbstractValue) for pv in join_pvs):
      return PartialVal((AbstractTuple(join_pvs), tuple(join_consts)))
    else:
      return PartialVal((JaxprTracerTuple(join_pvs), tuple(join_consts)))

def as_abstract_val(pv):
  if isinstance(pv, AbstractValue):
    return pv
  elif isinstance(pv, JaxprTracerTuple):
    return AbstractTuple(map(as_abstract_val, pv))
  elif pv is None:
    raise TypeError("{} is not abstract".format(pv))

def partial_val_aval(pv, const):
  if isinstance(pv, AbstractValue):
    return pv
  elif isinstance(pv, JaxprTracerTuple):
    return AbstractTuple(map(partial_val_aval, pv, const))
  elif pv is None:
    return get_aval(const)
  else:
    raise TypeError(pv)

def pack_pvals(pvals):
  pvs, consts = unzip2(pvals)
  if all(pv is None for pv in pvs):
    pv_out = None
  elif all(isinstance(pv, AbstractValue) for pv in pvs):
    pv_out = AbstractTuple(pvs)
  else:
    pv_out = JaxprTracerTuple(pvs)
  return PartialVal((pv_out, pack(consts)))



def abstractify(x):
  return PartialVal((core.concrete_aval(x), unit))

def trace_unwrapped_to_jaxpr(fun, pvals, **kwargs):
  return trace_to_jaxpr(lu.wrap_init(fun), pvals, **kwargs)

def trace_to_jaxpr(fun, pvals, **kwargs):
  """Traces a function, given abstract inputs, to a jaxpr."""
  instantiate = kwargs.pop('instantiate', False)
  with new_master(JaxprTrace) as master:
    fun = trace_to_subjaxpr(fun, master, instantiate)
    jaxpr, (out_pval, consts, env) = fun.call_wrapped(pvals, **kwargs)
    assert not env
    del master

  return jaxpr, out_pval, consts

@transformation
def trace_to_subjaxpr(master, instantiate, pvals):
  assert all([isinstance(pv, PartialVal) for pv in pvals]), pvals
  trace = JaxprTrace(master, core.cur_sublevel())
  in_tracers = map(trace.new_arg, pvals)
  out_tracer = yield in_tracers
  out_tracer = trace.full_raise(out_tracer)

  if instantiate:
    out_tracer = trace.instantiate_const(out_tracer)

  jaxpr, consts, env = tracers_to_jaxpr(in_tracers, out_tracer)
  out_pval = out_tracer.pval
  del trace, in_tracers, out_tracer
  yield jaxpr, (out_pval, consts, env)


FreeVar = namedtuple('FreeVar', ['val'])
ConstVar = namedtuple('ConstVar', ['val'])
LambdaBinding = namedtuple('LambdaBinding', [])

def eqn_tracer_to_var(var, outvars, eqn):
  invars, _, primitive, bound_subjaxprs, restructure, destructure, params = eqn
  if not restructure:
    invars = map(var, invars)
  else:
    invars = [tuple(map(var, v)) if type(v) is tuple else var(v)
              for v in invars]
  new_bound_subjaxprs = [(j, map(var, c), map(var, f))
                         for j, c, f in bound_subjaxprs]
  return JaxprEqn(invars, outvars, primitive,
                  new_bound_subjaxprs, restructure, destructure, params)


def tracers_to_jaxpr(in_tracers, out_tracer):
  newvar = gensym('')
  t_to_var = defaultdict(newvar)
  var = lambda t: t_to_var[id(t)]
  sorted_tracers = toposort(out_tracer)
  invars = map(var, in_tracers)
  eqns = []
  env = {}
  consts = {}
  destructuring_vars = {}
  for t in sorted_tracers:
    recipe = t.recipe
    if isinstance(recipe, JaxprEqn):
      eqns.append(eqn_tracer_to_var(var, [var(t)], recipe))
    elif isinstance(recipe, LambdaBinding):
      assert in_tracers, "Lambda binding with no args"
    elif isinstance(recipe, FreeVar):
      env[var(t)] = recipe.val
    elif isinstance(recipe, ConstVar):
      consts[var(t)] = recipe.val
    elif isinstance(recipe, Destructuring):
      i, eqn, key = recipe
      if key not in destructuring_vars:
        outvars = [newvar() for _ in eqn.outvars]
        eqns.append(eqn_tracer_to_var(var, outvars, eqn))
        destructuring_vars[key] = outvars
      else:
        outvars = destructuring_vars[key]
      t_to_var[id(t)] = outvars[i]
    elif recipe is unit:
      t_to_var[id(t)] = unitvar
    else:
      raise TypeError(recipe)

  env_vars, env_vals = unzip2(env.items())
  const_vars, const_vals = unzip2(consts.items())
  jaxpr = Jaxpr(const_vars, env_vars, invars, var(out_tracer), eqns)
  core.skip_checks or core.check_jaxpr(jaxpr)
  return jaxpr, const_vals, env_vals


def gensym(suffix):
  counter = it.count()
  return lambda: Var(next(counter), suffix)

class Var(object):
  def __init__(self, count, suffix):
    self.count = count
    self.suffix = suffix

  def __repr__(self):
    rem = self.count
    s = ''
    while True:
      rem, i = rem // 26, rem % 26
      s = chr(97 + i % 26) + s
      if not rem:
        break
    return s + self.suffix

def eqn_parents(eqn):
  subjaxpr_tracers = [it.chain(c, f) for _, c, f in eqn.bound_subjaxprs]
  if not eqn.restructure:
    return list(it.chain(eqn.invars,  *subjaxpr_tracers))
  else:
    invars = []
    for v in eqn.invars:
      if type(v) is tuple:
        invars.extend(v)
      else:
        invars.append(v)
    return list(it.chain(invars, *subjaxpr_tracers))


def compiled_call_impl(fun, *args, **kwargs):
  with new_master(JaxprTrace, True) as master:
    pvals = map(abstractify, args)
    jaxpr, (pval, consts, env) = trace_to_subjaxpr(fun, master, False).call_wrapped(pvals)
    jaxpr_ans = eval_jaxpr_raw(jaxpr, consts, env, *args)
    ans = merge_pvals(jaxpr_ans, pval)
    del master, pvals, pval, consts, env, jaxpr_ans, jaxpr
    return ans

compiled_call_p = Primitive('compiled_call')
compiled_call = partial(core.call_bind, compiled_call_p)
compiled_call_p.def_custom_bind(compiled_call)
compiled_call_p.def_impl(compiled_call_impl)



# @transformation_with_aux
# def partial_eval_traceable(first_components, pvals):
  


def unzip_tracer_tuple(pvals):
  pvs, consts = unzip2(pvals)
  return PartialVal((JaxprTracerTuple(pvs), pack(consts)))

def as_pval(aval, is_known, val):
  t = type(is_known)
  if t is tuple:
    return unzip_tracer_tuple(map(as_pval, aval, is_known, val))
  elif t is bool:
    if is_known:
      return PartialVal((None, val))
    else:
      return PartialVal((aval, core.unit))
  else:
    raise TypeError(t)

def as_pval2(aval, is_known):
  t = type(is_known)
  if t is tuple:
    return unzip_tracer_tuple(map(as_pval2, aval, is_known))
  elif t is bool:
    if is_known:
      return PartialVal((aval, core.unit))
    else:
      return PartialVal((core.AbstractTuple(()), core.unit))
  else:
    raise TypeError(t)

def isnone(x):
  if x is None:
    return True
  elif type(x) is JaxprTracerTuple:
    return tuple(map(isnone, x))
  elif isinstance(x, AbstractValue):
    return False
  else:
    raise TypeError(type(x))

# TODO revise for typedjaxprs
def jaxpr_as_fun(jaxpr, consts, *args):
  consts = core.full_lower(consts)
  args = map(core.full_lower, args)
  return core.eval_jaxpr(jaxpr, consts, (), *args)

_partial_eval_gensym = gensym('_peval')

def partial_eval_jaxpr(jaxpr, consts, avals, first_components):
  # jaxpr :: a -> b -> c
  f = lu.wrap_init(partial(jaxpr_as_fun, jaxpr, consts))

  cell = []
  def fun(*vals):
    pvals = map(as_pval, avals, first_components, vals)
    jaxpr_2, out_pval, consts_2 = trace_to_jaxpr(f, pvals)
    out_pv, out_const = out_pval
    cell.append((out_pv, jaxpr_2))
    return pack((out_const, pack(consts_2)))

  pvals = map(as_pval2, avals, first_components)
  jaxpr_1, out_pval, consts_1 = trace_to_jaxpr(
      lu.wrap_init(fun), pvals, instantiate=True)
  out_pv_2, jaxpr_2 = cell[0]
  lifted_jaxpr_2 = _closure_convert_jaxpr(jaxpr_2, _partial_eval_gensym)
  fc_out = isnone(out_pv_2)
  # jaxpr_1 :: a1 -> b1 -> (c1, res)
  # lifted_jaxpr_2 :: res -> a2 -> b2 -> c2
  return (jaxpr_1, consts_1), (lifted_jaxpr_2, ()), out_pv_2, fc_out


def _closure_convert_jaxpr(jaxpr, newvar):
  lifted_jaxpr = jaxpr.copy()
  lifted_jaxpr.constvars = ()
  consts_var = newvar()
  lifted_jaxpr.invars = [consts_var] + jaxpr.invars
  lifted_jaxpr.eqns = (
      [_unpack_eqn(consts_var, jaxpr.constvars)] + list(jaxpr.eqns))
  return lifted_jaxpr

def _unpack_eqn(invar, outvars):
  return core.JaxprEqn([invar], outvars, core.identity_p, (), False, True, {})

def _pack_eqn(invars, outvar):
  return core.JaxprEqn(invars, [outvar], core.pack_p, (), False, False, {})


def partial_eval_jaxpr2(jaxpr, first_components):
  # jaxpr :: d -> c -> a -> (c, b)
  f = lu.wrap_init(core.jaxpr_as_fun(jaxpr))

  cell = []
  # we do some final-style output munging to place residuals
  # fun :: d1 -> c1 -> a1 -> (c1, (b1, res))
  def fun(*vals):
    pvals = map(as_pval, jaxpr.in_avals, first_components, vals)
    jaxpr_2, out_pval, consts_2 = trace_to_jaxpr(f, pvals)
    (out_pv_c, out_pv_b), (out_const_c, out_const_b) = out_pval
    cell.append((out_pv_c, out_pv_b, jaxpr_2))
    return pack((out_const_c, pack((out_const_b, pack(consts_2)))))

  pvals = map(as_pval2, jaxpr.in_avals, first_components)
  jaxpr_1, out_pval, consts_1 = trace_to_jaxpr(
      lu.wrap_init(fun), pvals, instantiate=True)
  out_pv_c, out_pv_b, jaxpr_2 = cell[0]

  #               jaxpr_1 :: d1 -> c1 -> a1 -> (c1, (b1, res))
  #               jaxpr_2 :: res | d2 -> c2 -> a2 -> (c2, b2)
  #        lifted_jaxpr_2 :: res -> d2 -> c2 -> a2 -> (c2, b2)
  # doubly_lifted_jaxpr_2 :: d2 -> c2 -> (a2, res) -> (c2, b2)
  lifted_jaxpr_2 = _closure_convert_jaxpr(jaxpr_2, _partial_eval_gensym)
  doubly_lifted_jaxpr_2 = _move_and_pair_arg(lifted_jaxpr_2, _partial_eval_gensym)
  fc_out = fc_c_out, fc_b_out = isnone(out_pv_c), isnone(out_pv_b)

  in_avals_1, in_avals_2 = unzip2(map(_split_avals, first_components,
                                      jaxpr.in_avals))
  out_aval_1, out_aval_2 = _split_avals(fc_out, jaxpr.out_aval)

  # in_avals_1 is already (d1, c1, a1), and out_aval_2 is already (c2, b2), but
  # we must munge:
  # 1. form out_aval_1 to include the residuals as (c1, (b1, res))
  # 2. form in_avals_2 to include the residuals as (d2, c2, (a2, res))

  out_pv, _ = out_pval
  _, (_, res) = out_pv
  assert isinstance(res, AbstractValue)

  c1, b1 = out_aval_1
  lifted_out_aval_1 = AbstractTuple((c1, AbstractTuple((b1, res))))

  d2, c2, a2 = in_avals_2
  lifted_in_avals_2 = (d2, c2, AbstractTuple((a2, res)))

  typed_jaxpr_1 = TypedJaxpr(jaxpr_1, consts_1, in_avals_1, lifted_out_aval_1)
  typed_jaxpr_2 = TypedJaxpr(doubly_lifted_jaxpr_2, (), lifted_in_avals_2,
                             out_aval_2)
  return typed_jaxpr_1, typed_jaxpr_2, fc_out

def _move_and_pair_arg(jaxpr, newvar):
  moved_jaxpr = jaxpr.copy()
  res, d, c, a = jaxpr.invars
  pair_var = newvar()
  moved_jaxpr.invars = [d, c, pair_var]
  moved_jaxpr.eqns = (
      [_unpack_eqn(pair_var, [a, res])] + list(jaxpr.eqns))
  return moved_jaxpr

def _split_avals(first_component, aval):
  t = type(first_component)
  if t is tuple:
    assert type(aval) is AbstractTuple
    avals1, avals2 = unzip2(map(_split_avals, first_component, aval))
    return AbstractTuple(avals1), AbstractTuple(avals2)
  elif t is bool:
    if first_component:
      return aval, AbstractTuple(())
    else:
      return AbstractTuple(()), aval
  else:
    raise TypeError(t)


custom_partial_eval_rules = {}
