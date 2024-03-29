#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import collections
import enum
import glob
import os
import signal
import subprocess
import sys
import warnings

from typing import Any, Dict, List, Sequence, Tuple, Union

warnings.simplefilter('default')

_PKGS = collections.defaultdict(dict)  # type: Dict[str, Dict[str, str]]

_DEPS = {}
_DEPS['gmp'] = 'https://gmplib.org/download/gmp/gmp-6.2.1.tar.xz'
_DEPS['mpfr'] = 'https://www.mpfr.org/mpfr-current/mpfr-4.1.0.tar.xz'
_DEPS['mpc'] = 'https://ftp.gnu.org/gnu/mpc/mpc-1.2.1.tar.gz'
_DEPS['isl'] = 'http://isl.gforge.inria.fr/isl-0.24.tar.xz'

_DIR = os.path.dirname(os.path.abspath(__file__))

_INSTALL_DIR = os.path.join(_DIR, 'install')
_INSTALL_BIN = os.path.join(_INSTALL_DIR, 'bin')

_LOG_DIR = os.path.join(_DIR, 'logs')
_PKGS['binutils']['log'] = 'binutils{}-{}.log'
_PKGS['gcc']['log'] = 'gcc{}-{}.log'
_PKGS['glibc']['log'] = 'glibc{}-{}.log'
_PKGS['linux']['log'] = 'linux{}-{}.log'

_SRC_DIR = os.path.join(_DIR, 'src')
_PKGS['binutils']['src'] = os.path.join(_SRC_DIR, 'binutils')
_PKGS['gcc']['src'] = os.path.join(_SRC_DIR, 'gcc')
_PKGS['glibc']['src'] = os.path.join(_SRC_DIR, 'glibc')
_PKGS['linux']['src'] = os.path.join(_SRC_DIR, 'linux')

_WORK_DIR = os.path.join(_DIR, 'work')
_PKGS['binutils']['work'] = os.path.join(_WORK_DIR, 'binutils{}-{}')
_PKGS['gcc']['work'] = os.path.join(_WORK_DIR, 'gcc{}-{}')
_PKGS['glibc']['work'] = os.path.join(_WORK_DIR, 'glibc{}-{}')
_PKGS['linux']['work'] = os.path.join(_WORK_DIR, 'linux{}-{}')


class CrossException(Exception):
    pass


def get_build() -> str:
    proc = subprocess.run('/usr/share/gnuconfig/config.guess',
                          check=True,
                          stdout=subprocess.PIPE,
                          universal_newlines=True)
    return str(proc.stdout.strip())


def get_args(build: str, host: str, target: str) -> List[str]:
    return ['--build={}'.format(build), '--host={}'.format(host), '--target={}'.format(target)]


def fetch() -> None:
    subprocess.run(['git', 'submodule', 'update', '--progress'], check=True)
    for dep, url in _DEPS.items():
        dest = os.path.join(_PKGS['gcc']['src'], dep)
        if not os.path.exists(dest):
            subprocess.run(['wget', '-N', '-P', _SRC_DIR, url], check=True)
            tar_files = glob.glob(os.path.join(_SRC_DIR, '*{}*'.format(dep)))
            os.mkdir(dest)
            subprocess.run(['tar', 'xf', tar_files[0], '--strip-components=1', '-C', dest],
                           check=True)


def get_log_path(stage: str, pkg: str, triple: str, action: List[str]) -> str:
    return os.path.join(_LOG_DIR, triple, _PKGS[pkg]['log'].format(stage, action[0]))


def get_arch(arch: str) -> str:
    arch, _ = arch.split('-', maxsplit=1)
    if arch == 'alpha':
        return 'alpha'
    if arch == 'powerpc':
        return 'powerpc'
    if arch == 'aarch64':
        return 'arm64'
    if arch == 'x86_64':
        return 'x86'
    raise Exception('Unknown arch {}'.format(arch))


def ensure_stubs(directory: str) -> None:
    # Glibc doesn't create this file when cross-compiling.
    stubs_path = os.path.join(directory, 'include', 'gnu', 'stubs.h')
    if not os.path.exists(stubs_path):
        with open(stubs_path, 'w') as stubs:
            stubs.write('')


class Canonicalize(argparse.Action):

    def __call__(self,
                 parser: argparse.ArgumentParser,
                 namespace: argparse.Namespace,
                 values: Union[str, Sequence[Any], None],
                 option_string: str = None) -> None:
        proc = subprocess.run(
            ['/usr/share/gnuconfig/config.sub', str(values)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True)
        output = proc.stdout.strip()
        if proc.returncode:
            raise CrossException(output)
        setattr(namespace, getattr(self, 'dest'), output)


class Target(enum.Enum):
    BUILD = 1
    HOST = 2
    TARGET = 3
    CANADIAN = 4


class Builder(object):

    def __init__(self, args: argparse.Namespace) -> None:
        self.build = args.build
        self.host = args.host
        self.target = args.target
        self.dry_run = args.dry_run
        self.host_dir = os.path.join(_INSTALL_DIR, self.host)
        self.target_dir = os.path.join(_INSTALL_DIR, self.target)
        self.host_arch = get_arch(self.host)
        self.target_arch = get_arch(self.target)
        self.make_cmd = ['make', '-j{}'.format(args.jobs)]
        self.is_canadian = self.build != self.host
        self.is_cross = self.host != self.target
        self.common_args = ['--prefix={}'.format(_INSTALL_DIR)]
        self.gcc_args = self.common_args + ['--enable-languages=all']
        # We can't build shared libgcc w/o glibc.
        # glibc needs a cross g++
        self.bootstrap_args = self.common_args + ['--disable-shared', '--enable-languages=c,c++']

    def format_args(self,
                    stage: str,
                    pkg: str,
                    target: Target,
                    host_only=False) -> Tuple[str, str, List[str]]:
        work = _PKGS[pkg]['work']
        name = work_dir = args = None
        host = self.build
        # If we're building a host-only library, we need to tell it to build for target.
        if host_only:
            if target == Target.TARGET:
                host = self.target
            elif target == Target.HOST:
                host = self.host
            else:
                raise CrossException("What are you doing?")
        if target == Target.HOST:
            name = self.host
            work_dir = work.format(stage, self.host)
            args = get_args(self.build, host, self.host)
        elif target == Target.TARGET:
            name = self.target
            work_dir = work.format(stage, self.target)
            args = get_args(self.build, host, self.target)
        elif target == Target.CANADIAN:
            name = "_".join([self.host, self.target])
            work_dir = work.format(stage, name)
            args = get_args(self.build, self.host, self.target)
        else:
            raise CrossException("You shouldn't be building anything for build.")
        return name, work_dir, args

    def build_pkg(self,
                  pkg: str,
                  target: List[str],
                  system: Target,
                  extra_args: List[str],
                  stage: str = '') -> None:
        env = os.environ.copy()
        host_only = False
        if pkg == 'glibc':
            host_only = True
        if pkg == 'glibc' or system == Target.CANADIAN:
            env['PATH'] = '{}:{}'.format(os.environ['PATH'], _INSTALL_BIN)
        triple, work_dir, config_args = self.format_args(stage, pkg, system, host_only)
        if not self.dry_run:
            if not os.path.exists(os.path.join(_LOG_DIR, triple)):
                os.makedirs(os.path.join(_LOG_DIR, triple))
            if not os.path.exists(work_dir):
                os.makedirs(work_dir)

        if not os.path.exists(os.path.join(work_dir, 'Makefile')):
            configure_path = os.path.join(_PKGS[pkg]['src'], 'configure')
            # Linux doesn't use autoconf.
            if os.path.exists(configure_path):
                self.run_command([configure_path] + config_args + extra_args,
                                 get_log_path(stage, pkg, triple, ['config']), work_dir, env)
        self.run_command(self.make_cmd + target, get_log_path(stage, pkg, triple, target), work_dir,
                         env)

    def run_command(self, args: List[str], log_path: str, work_dir: str, env: Dict[str,
                                                                                   str]) -> None:
        if self.dry_run:
            cmd = ' '.join(args)
            print('{}, cwd={}'.format(cmd, work_dir).replace('{}/'.format(_DIR), ''))
            return
        proc = None
        try:
            with open(log_path, 'w') as log_file:
                proc = subprocess.Popen(args,
                                        stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT,
                                        universal_newlines=True,
                                        cwd=work_dir,
                                        env=env)
                for line in proc.stdout:
                    sys.stdout.write(line)
                    log_file.write(line)
                proc.stdout.close()
            if proc.wait():
                raise CrossException('Command {} failed in {}.'.format(' '.join(args), work_dir))
        finally:
            if not proc.returncode:
                proc.send_signal(signal.SIGINT)
                proc.wait()
            if hasattr(proc, 'stdout'):
                proc.stdout.close()

    def do_canadian(self):
        canadian_args = ['--prefix={}'.format(os.path.join(_INSTALL_DIR, self.host_dir))]
        self.build_pkg('gcc', ['all'], Target.CANADIAN, canadian_args)
        self.build_pkg('gcc', ['install'], Target.CANADIAN, canadian_args)

    def do_linux(self, system: Target, header_prefix: str):
        arch = self.host_arch if system == Target.HOST else self.target_arch
        self.build_pkg('linux', [
            'headers_install', 'ARCH={}'.format(arch), '-C', _PKGS['linux']['src'], 'O=`pwd`',
            'INSTALL_HDR_PATH={}'.format(header_prefix)
        ], system, [])

    def do_glibc_headers(self, system: Target, header_prefix: str):
        glibc_args = ['--prefix={}'.format(header_prefix)]
        self.build_pkg('glibc', ['install-headers'], system, glibc_args)

    def compile(self):
        to_build = []
        if self.is_cross:
            to_build.append(Target.TARGET)
        if self.is_canadian:
            to_build.append(Target.HOST)
            to_build.append(Target.CANADIAN)
        for system in to_build:
            if system == Target.CANADIAN:
                self.do_canadian()
                return
            self.build_pkg('binutils', ['all'], system, self.common_args)
            self.build_pkg('binutils', ['install'], system, self.common_args)
            # This needs to come before glibc is configured,
            # otherwise we'll pick up the wrong gcc and fail when we try to actually build the library.
            self.build_pkg('gcc', ['all-gcc'], system, self.bootstrap_args)
            self.build_pkg('gcc', ['install-gcc'], system, self.bootstrap_args)
            # glibc requires linux headers to be available.
            header_prefix = self.target_dir if system == Target.TARGET else self.host_dir
            self.do_linux(system, header_prefix)
            self.do_glibc_headers(system, header_prefix)
            if not self.dry_run:
                ensure_stubs(header_prefix)
            # glibc links against libgcc, so we need to build it first.
            self.build_pkg('gcc', ['all-target-libgcc'], system, self.bootstrap_args)
            self.build_pkg('gcc', ['install-target-libgcc'], system, self.bootstrap_args)
            glibc_args = ['--prefix={}'.format(header_prefix)]
            self.build_pkg('glibc', ['all'], system, glibc_args)
            self.build_pkg('glibc', ['install'], system, glibc_args)
            # We need to build a new gcc to get shared libraries, which need to link with glibc.
            self.build_pkg('gcc', ['all'], system, self.gcc_args, '2')
            self.build_pkg('gcc', ['install'], system, self.gcc_args, '2')


def main():
    parser = argparse.ArgumentParser()
    build_triple = get_build()
    parser.add_argument('--build', action=Canonicalize, default=build_triple)
    parser.add_argument('--host', action=Canonicalize, default=build_triple)
    parser.add_argument('--target', action=Canonicalize, default=build_triple)
    parser.add_argument('--jobs', '-j', default=os.cpu_count() + 1, type=int)
    parser.add_argument('--dry-run', '-n', action='store_true')
    args = parser.parse_args()

    print('build: {}, host: {}, target: {}'.format(args.build, args.host, args.target))
    if not args.dry_run:
        fetch()
    Builder(args).compile()


if __name__ == '__main__':
    main()
