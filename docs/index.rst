JAX reference documentation
===========================

Composable transformations of Python+NumPy programs: differentiate, vectorize,
JIT to GPU/TPU, and more.

For an introduction to JAX, start at the
`JAX GitHub page <https://github.com/google/jax>`_.

.. toctree::
   :maxdepth: 1
   :caption: Getting Started

   notebooks/quickstart
   notebooks/thinking_in_jax
   notebooks/Common_Gotchas_in_JAX

.. toctree::
   :maxdepth: 2

   jax-101/index

.. toctree::
   :maxdepth: 1
   :caption: Advanced JAX Tutorials

   notebooks/convolutions
   notebooks/autodiff_cookbook
   notebooks/vmapped_log_probs
   notebooks/neural_network_with_tfds_data
   notebooks/Custom_derivative_rules_for_Python_code
   notebooks/How_JAX_primitives_work
   notebooks/Writing_custom_interpreters_in_Jax
   notebooks/Neural_Network_and_Data_Loading
   notebooks/XLA_in_Python
   notebooks/maml
   notebooks/score_matching


.. toctree::
   :maxdepth: 1
   :caption: Notes

   changelog
   faq
   errors
   jaxpr
   async_dispatch
   concurrency
   gpu_memory_allocation
   profiling
   device_memory_profiling
   pytrees
   rank_promotion_warning
   type_promotion
   custom_vjp_update
   glossary

.. toctree::
   :maxdepth: 2
   :caption: Developer documentation

   developer
   jax_internal_api
   autodidax

.. toctree::
   :maxdepth: 3
   :caption: API documentation

   jax


Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
