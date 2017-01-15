#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import enum
import os
import subprocess
import sys
import warnings

warnings.simplefilter('default')

_GCC_URL = 'git://gcc.gnu.org/git/gcc.git'
_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.join(_DIR, 'src')
_GCC_SRC = os.path.join(_SRC_DIR, 'gcc')

_WORK_DIR = os.path.join(_DIR, 'work')
_GCC_WORK = os.path.join(_WORK_DIR, '{}-gcc')

class CrossException(Exception):
    pass

def get_build():
    proc = subprocess.run('/usr/share/gnuconfig/config.guess',
                          check=True,
                          stdout=subprocess.PIPE,
                          universal_newlines=True)
    return proc.stdout.strip()


def get_args(build, host, target):
    return ['--build={}'.format(build), '--host={}'.format(host), '--target={}'.format(target)]


class Canonicalize(argparse.Action):

    def __call__(self, parser, namespace, values, option_string=None):
        proc = subprocess.run(['/usr/share/gnuconfig/config.sub', values],
                              stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT,
                              universal_newlines=True)
        output = proc.stdout.strip()
        if proc.returncode:
            raise CrossException(output)
        setattr(namespace, self.dest, output)


class Target(enum.Enum):
    BUILD = 1
    HOST = 2
    TARGET = 3
    CANADIAN = 4


class Builder(object):

    def __init__(self, args):
        self.build = args.build
        self.host = args.host
        self.target = args.target
        self.is_canadian = self.build != self.host
        self.is_cross = self.host != self.target

    def fetch(self):
        if not os.path.exists(_GCC_SRC):
            subprocess.run(['git', 'clone', _GCC_URL, _GCC_SRC], check=True)

    def format_args(self, target):
        work_dir = args = None
        if target == Target.HOST:
            work_dir = _GCC_WORK.format(self.host)
            args = get_args(self.build, self.build, self.host)
        elif target == Target.TARGET:
            work_dir = _GCC_WORK.format(self.target)
            args = get_args(self.build, self.build, self.target)
        elif target == Target.CANADIAN:
            work_dir = _GCC_WORK.format("-".join(self.host, self.target))
            args = get_args(self.build, self.host, self.target)
        else:
            raise CrossException("You shouldn't be building GCC for build.")
        return work_dir, args

    def build_gcc(self, target):
        work_dir, args = self.format_args(target)
        if not os.path.exists(work_dir):
            os.makedirs(work_dir)
        if not os.path.exists(os.path.join(work_dir, 'Makefile')):
            subprocess.run([os.path.join(_GCC_SRC, 'configure')] + args, check=True, cwd=work_dir)

    def compile(self):
        if self.is_cross:
            self.build_gcc(Target.TARGET)
        if self.is_canadian:
            self.build_gcc(Target.HOST)
            self.build_gcc(Target.CANADIAN)


def main():
    parser = argparse.ArgumentParser()
    build_triple = get_build()
    parser.add_argument('--build', action=Canonicalize, default=build_triple)
    parser.add_argument('--host', action=Canonicalize, default=build_triple)
    parser.add_argument('--target', action=Canonicalize, default=build_triple)
    args = parser.parse_args()
    builder = Builder(args)
    builder.fetch()
    builder.compile()


if __name__ == '__main__':
    main()
