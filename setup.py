from __future__ import absolute_import, division, print_function
# I didn't import unicode_literals. They break setuptools or Cython in python
# 2.7, but python 3 seems to be happy with them.

import glob
import os
from os import path
from setuptools import setup, Extension
from setuptools.command.build_ext import build_ext as build_ext_
from setuptools.command.develop import develop as develop_
from setuptools.command.install import install as install_
import shutil
import subprocess
import sys
import platform
from cpufeature import CPUFeature
import glob

VERSION_MAJOR = 0
VERSION_MINOR = 3
VERSION_POINT = 6

# Only unset in the 'release' branch and in tags.
VERSION_DEV = 1

VERSION = "%d.%d.%d" % (VERSION_MAJOR, VERSION_MINOR, VERSION_POINT)
if VERSION_DEV:
    VERSION = VERSION + ".dev%d" % VERSION_DEV


COMPILE_FLAGS = ['-O3', '-ffast-math', '-march=native', '-std=c99']
# Cython breaks strict aliasing rules.
COMPILE_FLAGS += ['-fno-strict-aliasing']
COMPILE_FLAGS += ['-fPIC']
COMPILE_FLAGS_MSVC = ['/Ox', '/fp:fast']

MACROS = [
    ('BSHUF_VERSION_MAJOR', VERSION_MAJOR),
    ('BSHUF_VERSION_MINOR', VERSION_MINOR),
    ('BSHUF_VERSION_POINT', VERSION_POINT),
]


# OSX's clang compliler does not support OpenMP.
if sys.platform == 'darwin':
    OMP_DEFAULT = False
else:
    OMP_DEFAULT = True

FALLBACK_CONFIG = {
    'include_dirs': [],
    'library_dirs': [],
    'libraries': [],
    'extra_compile_args': [],
    'extra_link_args': [],
}

# Setup compiler flags for SIMD
# -----------------------------
if sys.platform == 'win32':
    if CPUFeature['AVX2']:
        COMPILE_FLAGS_MSVC += ['/arch:AVX2']
    if CPUFeature['SSE2'] and platform.machine() != 'AMD64':
        # SSE2 enabled by default on Win64
        COMPILE_FLAGS_MSVC += ['/arch:SSE2']

else: # Generic Linux-alike
    if CPUFeature['AVX2']:
        COMPILE_FLAGS += '-mavx2'
    if CPUFeature['SSE2']:
        COMPILE_FLAGS += '-msse2'

# Specific paths for MacOSX and BSD
# ---------------------------------
if sys.platform == 'darwin':
    # putting here both macports and homebrew paths will generate
    # "ld: warning: dir not found" at the linking phase
    FALLBACK_CONFIG['include_dirs'] += ['/opt/local/include']  # macports
    FALLBACK_CONFIG['library_dirs'] += ['/opt/local/lib']      # macports
    FALLBACK_CONFIG['include_dirs'] += ['/usr/local/include']  # homebrew
    FALLBACK_CONFIG['library_dirs'] += ['/usr/local/lib']      # homebrew
elif sys.platform.startswith('freebsd'):
    FALLBACK_CONFIG['include_dirs'] += ['/usr/local/include']  # homebrew
    FALLBACK_CONFIG['library_dirs'] += ['/usr/local/lib']      # homebrew


FALLBACK_CONFIG['include_dirs'] = [d for d in FALLBACK_CONFIG['include_dirs']
                                   if path.isdir(d)]
FALLBACK_CONFIG['library_dirs'] = [d for d in FALLBACK_CONFIG['library_dirs']
                                   if path.isdir(d)]



def pkgconfig(*packages, **kw):
    config = kw.setdefault('config', {})
    optional_args = kw.setdefault('optional', '')
    flag_map = {'include_dirs': ['--cflags-only-I', 2],
                'library_dirs': ['--libs-only-L', 2],
                'libraries': ['--libs-only-l', 2],
                'extra_compile_args': ['--cflags-only-other', 0],
                'extra_link_args': ['--libs-only-other', 0],
                }
    for package in packages:
        try:
            subprocess.check_output(["pkg-config", package])
        except (subprocess.CalledProcessError, OSError):
            print("Can't find %s with pkg-config fallback to "
                  "static config" % package)
            for distutils_key in flag_map:
                config.setdefault(distutils_key, []).extend(
                    FALLBACK_CONFIG[distutils_key])
            config['libraries'].append(package)
        else:
            for distutils_key, (pkg_option, n) in flag_map.items():
                items = subprocess.check_output(
                    ['pkg-config', optional_args, pkg_option, package]
                ).decode('utf8').split()
                opt = config.setdefault(distutils_key, [])
                opt.extend([i[n:] for i in items])
    return config


LZ4_DIR = glob.glob('lz4-*')
if len(LZ4_DIR) > 1:
    raise ValueError('There can be only one LZ4 library subdirectory in the package')
LZ4_DIR = LZ4_DIR[0]

ext_bshuf = Extension(
    "bitshuffle.ext",
    sources=["bitshuffle/ext.pyx", "src/bitshuffle.c",
             "src/bitshuffle_core.c", "src/iochain.c",
             path.join(LZ4_DIR, "lz4.c")],
    include_dirs=["src/", LZ4_DIR],
    depends=["src/bitshuffle.h", "src/bitshuffle_core.h",
             "src/iochain.h", path.join(LZ4_DIR, "lz4.h")],
    libraries=[],
    define_macros=MACROS,
)



EXTENSIONS = [ext_bshuf]
CPATHS = os.environ['CPATH'].split(':') if 'CPATH' in os.environ else []


class develop(develop_):
    def run(self):
        develop_.run(self)


# Custom installation to include installing dynamic filters.
class install(install_):

    def initialize_options(self):
        install_.initialize_options(self)

    def finalize_options(self):
        install_.finalize_options(self)

    def run(self):
        install_.run(self)


# Command line or site.cfg specification of OpenMP.
class build_ext(build_ext_):
    user_options = build_ext_.user_options + [
        ('omp=', None, "Whether to compile with OpenMP threading. Default"
         " on current system is %s." % str(OMP_DEFAULT))
    ]
    boolean_options = build_ext_.boolean_options + ['omp']

    def initialize_options(self):
        build_ext_.initialize_options(self)
        self.omp = OMP_DEFAULT

    def finalize_options(self):
        # For some reason this gets run twice. Careful to print messages and
        # add arguments only one time.
        build_ext_.finalize_options(self)

        if self.omp not in ('0', '1', True, False):
            raise ValueError("Invalid omp argument. Mut be '0' or '1'.")
        self.omp = int(self.omp)

        import numpy as np
        ext_bshuf.include_dirs.append(np.get_include())

        # Required only by old version of setuptools < 18.0
        from Cython.Build import cythonize
        self.extensions = cythonize(self.extensions)
        for ext in self.extensions:
            ext._needs_stub = False


    def build_extensions(self):
        c = self.compiler.compiler_type

        if self.omp not in ('0', '1', True, False):
            raise ValueError("Invalid omp argument. Mut be '0' or '1'.")
        self.omp = int(self.omp)

        openmpflag = ''
        if self.omp:
            if not hasattr(self, "_printed_omp_message"):
                self._printed_omp_message = True
                print("\n#################################")
                print("# Compiling with OpenMP support #")
                print("#################################\n")
            # More portable to pass -fopenmp to linker.
            # self.libraries += ['gomp']
            if self.compiler.compiler_type == 'msvc':
                openmpflag = '/openmp'
                compileflags = COMPILE_FLAGS_MSVC
            else:
                openmpflag = '-fopenmp'
                compileflags = COMPILE_FLAGS
            
        else: # No OMP support
            if self.compiler.compiler_type == 'msvc':
                compileflags = COMPILE_FLAGS_MSVC
            else:
                compileflags = COMPILE_FLAGS

        for e in self.extensions:
            e.extra_compile_args = list(set(e.extra_compile_args).union(compileflags))
            if openmpflag not in e.extra_compile_args:
                e.extra_compile_args += [openmpflag]
            if openmpflag not in e.extra_link_args:
                e.extra_link_args += [openmpflag]

        build_ext_.build_extensions(self)


# Don't install numpy/cython/hdf5 if not needed
for cmd in ["sdist", "clean",
            "--help", "--help-commands", "--version"]:
    if cmd in sys.argv:
        setup_requires = []
        break
else:
    setup_requires = ["Cython>=0.19", "numpy>=1.6.1"]

with open('requirements.txt') as f:
    requires = f.read().splitlines()
    requires = [r.split()[0] for r in requires]

with open('README.rst') as r:
    long_description = r.read()

setup(
    name='bitshuffle',
    version=VERSION,

    packages=['bitshuffle', 'bitshuffle.tests'],
    scripts=[],
    ext_modules=EXTENSIONS,
    cmdclass={'build_ext': build_ext, 'install': install, 'develop': develop},
    setup_requires=setup_requires,
    install_requires=requires,
    package_data={'': ['data/*']},

    # metadata for upload to PyPI
    author="Kiyoshi Wesley Masui",
    author_email="kiyo@physics.ubc.ca",
    description="Bitshuffle filter for improving typed data compression.",
    long_description=long_description,
    license="MIT",
    url="https://github.com/kiyo-masui/bitshuffle",
    download_url=("https://github.com/kiyo-masui/bitshuffle/tarball/%s"
                  % VERSION),
    keywords=['compression', 'numpy'],
)
