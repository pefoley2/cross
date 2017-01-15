#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse
import os
import subprocess
import warnings
warnings.simplefilter('default')

_GCC_URL = 'git://gcc.gnu.org/git/gcc.git'
_DIR = os.path.dirname(__file__)
_SRC_DIR = os.path.join(_DIR, 'src')
_GCC_SRC = os.path.join(_SRC_DIR, 'gcc')

_WORK_DIR = os.path.join(_DIR, 'work')
_GCC_WORK = os.path.join(_WORK_DIR, 'gcc')


def get_build():
    proc = subprocess.run('/usr/share/gnuconfig/config.guess',
                          check=True, stdout=subprocess.PIPE, universal_newlines=True)
    return proc.stdout.strip()


def get_args(build, host, target):
    return ['--build={}'.format(build), '--host={}'.format(host), '--target={}'.format(target)]


def build_gcc(args):
    if not os.path.exists(_GCC_WORK):
        os.makedirs(_GCC_WORK)
    subprocess.run([os.path.join(_GCC_SRC, 'configure')] + args, check=True, cwd=_GCC_WORK)


def get_source():
    if not os.path.exists(_GCC_SRC):
        subprocess.run(['git', 'clone', _GCC_URL, _GCC_SRC], check=True)


def build(args: argparse.Namespace):
    if args.host != args.build:
        args = get_args(args.build, args.build, args.host)
        build_gcc(args)
    if args.target != args.host:
        args = get_args(args.build, args.build, args.target)
        build_gcc(args)


def main():
    parser = argparse.ArgumentParser()
    build_triple = get_build()
    parser.add_argument('--build', default=build_triple)
    parser.add_argument('--host', default=build_triple)
    parser.add_argument('--target', default=build_triple)
    args = parser.parse_args()
    get_source()
    build(args)


if __name__ == '__main__':
    main()
