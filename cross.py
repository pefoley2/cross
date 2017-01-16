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

_DIR = os.path.dirname(os.path.abspath(__file__))

_INSTALL_DIR = os.path.join(_DIR, 'install')

_LOG_DIR = os.path.join(_DIR, 'logs')
_PKGS['binutils']['log'] = os.path.join(_LOG_DIR, 'binutils-{}-{}.log')
_PKGS['gcc']['log'] = os.path.join(_LOG_DIR, 'gcc-{}-{}.log')

_SRC_DIR = os.path.join(_DIR, 'src')
_PKGS['binutils']['src'] = os.path.join(_SRC_DIR, 'binutils')
_PKGS['gcc']['src'] = os.path.join(_SRC_DIR, 'gcc')

_WORK_DIR = os.path.join(_DIR, 'work')
_PKGS['binutils']['work'] = os.path.join(_WORK_DIR, 'binutils-{}')
_PKGS['gcc']['work'] = os.path.join(_WORK_DIR, 'gcc-{}')


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


def get_log_path(pkg: str, triple: str, action: str) -> str:
    return _PKGS[pkg]['log'].format(triple, action)


def run_command(args: List[str], log_path: str, work_dir: str) -> None:
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
        self.target = args.target
        self.make_cmd = ['make', '-j{}'.format(args.jobs)]
        self.is_canadian = self.build != self.host
        self.is_cross = self.host != self.target

    def format_args(self, pkg: str, target: Target) -> Tuple[str, str, List[str]]:
        work = _PKGS[pkg]['work']
        name = work_dir = args = None
        if target == Target.HOST:
            name = self.host
            work_dir = work.format(self.host)
            args = get_args(self.build, self.build, self.host)
        elif target == Target.TARGET:
            name = self.target
            work_dir = work.format(self.target)
            args = get_args(self.build, self.build, self.target)
        elif target == Target.CANADIAN:
            name = "_".join([self.host, self.target])
            work_dir = work.format(name)
            args = get_args(self.build, self.host, self.target)
        else:
            raise CrossException("You shouldn't be building anything for build.")
        return name, work_dir, args

    def build_pkg(self, pkg: str, target: Target, extra_args: List[str]) -> None:
        triple, work_dir, config_args = self.format_args(pkg, target)
        if not os.path.exists(_LOG_DIR):
            os.makedirs(_LOG_DIR)
        if not os.path.exists(work_dir):
            os.makedirs(work_dir)

        if not os.path.exists(os.path.join(work_dir, 'Makefile')):
            args = [os.path.join(_PKGS[pkg]['src'], 'configure')] + config_args
            run_command(args + extra_args, get_log_path(pkg, triple, 'config'), work_dir)
        run_command(self.make_cmd, get_log_path(pkg, triple, 'build'), work_dir)
        run_command(self.make_cmd + ['install'], get_log_path(pkg, triple, 'install'), work_dir)

    def compile(self) -> None:
        binutils_args = ['--disable-gdb', '--prefix={}'.format(_INSTALL_DIR)]
        gcc_args = ['--prefix={}'.format(_INSTALL_DIR)]
        if self.is_cross:
            self.build_pkg('binutils', Target.TARGET, binutils_args)
            self.build_pkg('gcc', Target.TARGET, gcc_args)
        if self.is_canadian:
            self.build_pkg('binutils', Target.HOST, binutils_args)
            self.build_pkg('gcc', Target.HOST, gcc_args)
            self.build_pkg('binutils', Target.CANADIAN, binutils_args)
            self.build_pkg('gcc', Target.CANADIAN, gcc_args)


def main() -> None:
    parser = argparse.ArgumentParser()
    build_triple = get_build()
    parser.add_argument('--build', action=Canonicalize, default=build_triple)
    parser.add_argument('--host', action=Canonicalize, default=build_triple)
    parser.add_argument('--target', action=Canonicalize, default=build_triple)
    parser.add_argument('--jobs', '-j', default=os.cpu_count() + 1, type=int)
    args = parser.parse_args()

    print('build: {}, host: {}, target: {}'.format(args.build, args.host, args.target))
    fetch()
    Builder(args).compile()


if __name__ == '__main__':
    main()
