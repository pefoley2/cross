#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import enum
import os
import subprocess
import sys
import warnings

from typing import Any, List, Sequence, Tuple, Union

warnings.simplefilter('default')

_GCC_URL = 'git://gcc.gnu.org/git/gcc.git'
_BINUTILS_URL = 'git://sourceware.org/git/binutils-gdb.git'

_DIR = os.path.dirname(os.path.abspath(__file__))

_LOG_DIR = os.path.join(_DIR, 'logs')
_BINUTILS_LOG = os.path.join(_LOG_DIR, 'binutils-{}.log')
_GCC_LOG = os.path.join(_LOG_DIR, 'gcc-{}.log')

_SRC_DIR = os.path.join(_DIR, 'src')
_BINUTILS_SRC = os.path.join(_SRC_DIR, 'binutils')
_GCC_SRC = os.path.join(_SRC_DIR, 'gcc')

_WORK_DIR = os.path.join(_DIR, 'work')
_BINUTILS_WORK = os.path.join(_WORK_DIR, 'binutils-{}')
_GCC_WORK = os.path.join(_WORK_DIR, 'gcc-{}')


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

    def fetch(self) -> None:
        for src, url in [(_BINUTILS_SRC, _BINUTILS_URL), (_GCC_SRC, _GCC_URL)]:
            if not os.path.exists(src):
                subprocess.run(['git', 'clone', url, src], check=True)
            subprocess.run(['git', 'pull'], check=True, cwd=src)

    def format_args(self, work: str, target: Target) -> Tuple[str, str, List[str]]:
        log = work.replace(_WORK_DIR, _LOG_DIR)
        log_dir = work_dir = args = None
        if target == Target.HOST:
            work_dir = work.format(self.host)
            log_dir = log.format(self.host)
            args = get_args(self.build, self.build, self.host)
        elif target == Target.TARGET:
            work_dir = work.format(self.target)
            log_dir = log.format(self.target)
            args = get_args(self.build, self.build, self.target)
        elif target == Target.CANADIAN:
            name = "_".join([self.host, self.target])
            work_dir = work.format(name)
            log_dir = log.format(name)
            args = get_args(self.build, self.host, self.target)
        else:
            raise CrossException("You shouldn't be building GCC for build.")
        return log_dir, work_dir, args

    def configure_pkg(self, src: str, log: str, work: str, args: List[str]) -> None:
        with open(log, 'w') as f:
            proc = subprocess.Popen(
                [os.path.join(src, 'configure')] + args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                cwd=work)
            for line in proc.stdout:
                sys.stdout.write(line)
                f.write(line)
            if proc.wait():
                raise CrossException('Configuration of {} failed.'.format(src))

    def build_pkg(self, src: str, work: str, target: Target) -> None:
        log_dir, work_dir, args = self.format_args(work, target)
        if not os.path.exists(_LOG_DIR):
            os.makedirs(_LOG_DIR)
        if not os.path.exists(work_dir):
            os.makedirs(work_dir)

        if not os.path.exists(os.path.join(work_dir, 'Makefile')):
            self.configure_pkg(src, log_dir, work_dir, args)
        subprocess.run(self.make_cmd, check=True, cwd=work_dir)

    def build_gcc(self, target: Target) -> None:
        self.build_pkg(_GCC_SRC, _GCC_WORK, target)

    def build_binutils(self, target: Target) -> None:
        self.build_pkg(_BINUTILS_SRC, _BINUTILS_WORK, target)

    def compile(self) -> None:
        if self.is_cross:
            self.build_binutils(Target.TARGET)
            self.build_gcc(Target.TARGET)
        if self.is_canadian:
            self.build_binutils(Target.HOST)
            self.build_gcc(Target.HOST)
            self.build_binutils(Target.CANADIAN)
            self.build_gcc(Target.CANADIAN)


def main() -> None:
    parser = argparse.ArgumentParser()
    build_triple = get_build()
    parser.add_argument('--build', action=Canonicalize, default=build_triple)
    parser.add_argument('--host', action=Canonicalize, default=build_triple)
    parser.add_argument('--target', action=Canonicalize, default=build_triple)
    parser.add_argument('--jobs', '-j', default=os.cpu_count() + 1, type=int)
    args = parser.parse_args()

    print('build: {}, host: {}, target: {}'.format(args.build, args.host, args.target))
    builder = Builder(args)
    builder.fetch()
    builder.compile()


if __name__ == '__main__':
    main()
