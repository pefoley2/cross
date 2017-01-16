#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import collections
import enum
import os
import subprocess
import sys
import warnings

from typing import Any, List, Sequence, Tuple, Union

warnings.simplefilter('default')

_PKGS = collections.defaultdict(dict)  # type: Dict[str, Dict[str, str]]

_PKGS['binutils']['url'] = 'git://sourceware.org/git/binutils-gdb.git'
_PKGS['gcc']['url'] = 'git://gcc.gnu.org/git/gcc.git'
_PKGS['glibc']['url'] = 'git://sourceware.org/git/glibc.git'
_PKGS['linux']['url'] = 'git://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git'

_DIR = os.path.dirname(os.path.abspath(__file__))

_INSTALL_DIR = os.path.join(_DIR, 'install')

_LOG_DIR = os.path.join(_DIR, 'logs')
_PKGS['binutils']['log'] = os.path.join(_LOG_DIR, 'binutils{}-{}-{}.log')
_PKGS['gcc']['log'] = os.path.join(_LOG_DIR, 'gcc{}-{}-{}.log')
_PKGS['glibc']['log'] = os.path.join(_LOG_DIR, 'glibc{}-{}-{}.log')
_PKGS['linux']['log'] = os.path.join(_LOG_DIR, 'linux{}-{}-{}.log')

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
    for pkg in _PKGS.values():
        if not os.path.exists(pkg['src']):
            subprocess.run(['git', 'clone', '--branch', 'master', pkg['url'], pkg['src']],
                           check=True)
        subprocess.run(['git', 'pull'], check=True, cwd=pkg['src'])


def get_log_path(stage: str, pkg: str, triple: str, action: str) -> str:
    return _PKGS[pkg]['log'].format(stage, triple, action[0])


def get_arch(arch: str) -> str:
    arch, _ = arch.split('-', maxsplit=1)
    if arch == 'alpha':
        return 'alpha'
    raise Exception('Unknown arch {}'.format(arch))


def run_command(args: List[str], log_path: str, work_dir: str) -> None:
    proc = None
    try:
        with open(log_path, 'w') as log_file:
            proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                cwd=work_dir)
            for line in proc.stdout:
                sys.stdout.write(line)
                log_file.write(line)
            proc.stdout.close()
        if proc.wait():
            raise CrossException('Command {} failed.'.format(' '.join(args)))
    except:
        raise
    finally:
        if hasattr(proc, 'stdout'):
            proc.stdout.close()


class Canonicalize(argparse.Action):

    def __call__(self,
                 parser: argparse.ArgumentParser,
                 namespace: argparse.Namespace,
                 values: Union[str, Sequence[Any], None],
                 option_string: str=None) -> None:
        proc = subprocess.run(['/usr/share/gnuconfig/config.sub', str(values)],
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
        self.host_dir = os.path.join(_INSTALL_DIR, self.host)
        self.target = args.target
        self.target_dir = os.path.join(_INSTALL_DIR, self.target)
        self.arch = get_arch(self.target)
        self.make_cmd = ['make', '-j{}'.format(args.jobs)]
        self.is_canadian = self.build != self.host
        self.is_cross = self.host != self.target

    def format_args(self, stage: str, pkg: str, target: Target,
                    host_only=False) -> Tuple[str, str, List[str]]:
        work = _PKGS[pkg]['work']
        name = work_dir = args = None
        # If we're building a host-only library, we need to tell it to build for target.
        host = self.target if host_only else self.build
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
                  stage: str='',
                  host_only: bool=False) -> None:
        triple, work_dir, config_args = self.format_args(stage, pkg, system, host_only)
        if not os.path.exists(_LOG_DIR):
            os.makedirs(_LOG_DIR)
        if not os.path.exists(work_dir):
            os.makedirs(work_dir)

        if not os.path.exists(os.path.join(work_dir, 'Makefile')):
            configure_path = os.path.join(_PKGS[pkg]['src'], 'configure')
            # Linux doesn't use autoconf.
            if os.path.exists(configure_path):
                run_command([configure_path] + config_args + extra_args,
                            get_log_path(stage, pkg, triple, 'config'), work_dir)
            else:
                run_command(self.make_cmd + ['defconfig', 'O={}'.format(work_dir)],
                            get_log_path(stage, pkg, triple, target), _PKGS['linux']['src'])
        run_command(self.make_cmd + target, get_log_path(stage, pkg, triple, target), work_dir)

    def ensure_stubs(self) -> None:
        # Glibc doesn't create this file when cross-compiling.
        stubs_path = os.path.join(self.target_dir, 'include', 'gnu', 'stubs.h')
        if not os.path.exists(stubs_path):
            with open(stubs_path, 'w') as stubs:
                stubs.write('')

    def compile(self) -> None:
        os.environ['PATH'] = '{}:{}'.format(os.environ['PATH'], os.path.join(_INSTALL_DIR, 'bin'))
        common_args = ['--prefix={}'.format(_INSTALL_DIR)]
        binutils_args = ['--disable-gdb'] + common_args
        gcc_args = common_args  #+ ['--disable-shared', '--disable-libssp', '--disable-libquadmath', '--disable-libgomp' '--enable-languages=c']
        # target is host for glibc.
        glibc_args = ['--prefix={}'.format(self.target_dir)]
        to_build = []
        if self.is_cross:
            to_build.append(Target.TARGET)
        if self.is_canadian:
            to_build.append(Target.HOST)
            to_build.append(Target.CANADIAN)
        for system in to_build:
            self.build_pkg('binutils', ['all'], system, binutils_args)
            self.build_pkg('binutils', '[install'], system, binutils_args)
            #self.build_pkg('glibc', ['install-headers'], system, glibc_args, host_only=True)
            self.ensure_stubs()
            self.build_pkg('gcc', ['all-gcc'], system, gcc_args)
            self.build_pkg('gcc', ['install-gcc'], system, gcc_args)
            self.build_pkg('linux', [
                'headers_install', 'ARCH={}'.format(self.arch),
                'INSTALL_HDR_PATH={}'.format(self.target_dir)
            ], system, [])
            self.build_pkg('glibc', ['all'], system, glibc_args, host_only=True)
            self.build_pkg('glibc', ['install'], system, glibc_args, host_only=True)
            self.build_pkg('gcc', ['all'], system, gcc_args, '2')
            self.build_pkg('gcc', ['install'], system, gcc_args, '2')


def main() -> None:
    parser = argparse.ArgumentParser()
    build_triple = get_build()
    parser.add_argument('--build', action=Canonicalize, default=build_triple)
    parser.add_argument('--host', action=Canonicalize, default=build_triple)
    parser.add_argument('--target', action=Canonicalize, default=build_triple)
    parser.add_argument('--jobs', '-j', default=os.cpu_count() + 1, type=int)
    args = parser.parse_args()

    print('build: {}, host: {}, target: {}'.format(args.build, args.host, args.target))
    #fetch()
    Builder(args).compile()


if __name__ == '__main__':
    main()
