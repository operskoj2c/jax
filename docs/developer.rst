Building from source
====================

First, obtain the JAX source code.

.. code-block:: shell

    git clone https://github.com/google/jax
    cd jax


You must also install some prerequisites:
 * a C++ compiler (g++ or clang)
 * Numpy
 * Scipy
 * Cython

On Ubuntu 18.04 or Debian you can install the necessary prerequisites with:

.. code-block:: shell

 sudo apt-get install g++ python python3-dev python3-numpy python3-scipy cython3


If you are building on a Mac, make sure XCode and the XCode command line tools
are installed.

You can also install the necessary Python dependencies using ``pip``:

.. code-block:: shell

 pip install numpy scipy cython


To build ``jaxlib`` with CUDA support, you can run

.. code-block:: shell

    python build/build.py --enable_cuda
    pip install -e build  # installs jaxlib (includes XLA)
    pip install -e .      # installs jax (pure Python)


See ``python build/build.py --help`` for configuration options, including ways to
specify the paths to CUDA and CUDNN, which you must have installed. The build
also depends on NumPy, and a compiler toolchain corresponding to that of
Ubuntu 16.04 or newer.

To build ``jaxlib`` without CUDA GPU support (CPU only), drop the ``--enable_cuda``:

.. code-block:: shell

  python build/build.py
  pip install -e build  # installs jaxlib (includes XLA)
  pip install -e .      # installs jax


To upgrade to the latest version from GitHub, just run ``git pull`` from the JAX
repository root, and rebuild by running ``build.py`` if necessary. You shouldn't have
to reinstall because ``pip install -e`` sets up symbolic links from site-packages
into the repository.

Running the tests
=================

To run all the JAX tests, we recommend using ``pytest-xdist``, which can run tests in
parallel. First, install ``pytest-xdist`` by running ``pip install pytest-xdist``.
Then, from the repository root directory run

.. code-block:: shell

 pytest -n auto tests


JAX generates test cases combinatorially, and you can control the number of
cases that are generated and checked for each test (default 10):

.. code-block:: shell

 JAX_NUM_GENERATED_CASES=100 pytest -n auto tests


You can run a more specific set of tests using
`pytest <https://docs.pytest.org/en/latest/usage.html#specifying-tests-selecting-tests>`_'s
built-in selection mechanisms, or alternatively you can run a specific test
file directly to see more detailed information about the cases being run:

.. code-block:: shell

 python tests/lax_numpy_test.py --num_generated_cases=5


Update documentation
====================

To rebuild the documentation,
install the ``sphinx`` and ``sphinx_rtd_theme`` pip
packages and then run at the top-level:

.. code-block:: shell

  sphinx-build -M html docs docs/build


You can then see the generated documentation in
``docs/build/html/index.html``.

Documentation building on readthedocs.io
----------------------------------------

JAX's auto-generated documentations is at `jax.readthedocs.io <https://jax.readthedocs.io/>`_.

The documentation building is controlled for the entire project by the 
`readthedocs JAX settings <https://readthedocs.org/dashboard/jax>`_. The current settings
trigger a documentation build as soon as code is pushed to the GitHub ``master`` branch.
For each code version, the building process is driven by the 
:file:`.readthedocs.yml` and the ``docs/conf.py`` configuration files.

For each automated documentation build you can see the 
`documentation build logs <https://readthedocs.org/projects/jax/builds/>`_.

If you want to test the documentation generation on Readthedocs, you can push code to the ``test-docs``
branch. That branch is also built automatically, and you can 
see the generated documentation `here <https://jax.readthedocs.io/en/test-docs/>`_.

For a local test, I was able to do it in a fresh directory by replaying the commands
I saw in the Readthedocs logs:

.. code-block:: shell

    mkvirtualenv jax-docs  # A new virtualenv
    mkdir jax-docs  # A new directory
    cd jax-docs
    git clone --no-single-branch --depth 50 https://github.com/google/jax
    cd jax
    git checkout --force origin/test-docs
    git clean -d -f -f
    
    python -m pip install --upgrade --no-cache-dir pip
    python -m pip install --upgrade --no-cache-dir -I Pygments==2.3.1 setuptools==41.0.1 docutils==0.14 mock==1.0.1 pillow==5.4.1 alabaster>=0.7,<0.8,!=0.7.5 commonmark==0.8.1 recommonmark==0.5.0 'sphinx<2' 'sphinx-rtd-theme<0.5' 'readthedocs-sphinx-ext<1.1'
    python -m pip install --exists-action=w --no-cache-dir -r docs/requirements.txt
    
    python `which sphinx-build` -T -E -b html -d _build/doctrees-readthedocs -D language=en . _build/html

