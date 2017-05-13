#!/usr/bin/env python
import argparse
import json
import os
import re
import shlex
import sys
import subprocess


def count_lines(file_name):
    count = 0
    with open(file_name, 'r') as file:
        for line in file:
            count += 1
    return count


def gcc_get_preprocessed_size(cmd, dir):
    # TODO(m): Use a unique temporary file name!
    cpp_file = '/tmp/ccperf-1234.i'

    opts = shlex.split(cmd)
    for i in range(len(opts) - 1, 0, -1):
        opt = opts[i].strip()
        if opt == '':
            # Drop empty items.
            opts.pop(i)
        if opt == '-c':
            # We only do the preprocessing step.
            opts[i] = '-E'
        elif opt == '-o' and (i + 1) < len(opts):
            # Output to a temporary file.
            opts[i + 1] = cpp_file
        elif opt in ['-M', '-MM', '-MG', '-MP', '-MD', '-MMD']:
            # Drop dependency generation.
            opts.pop(i)
        elif opt in ['-MF', '-MT', '-MQ'] and (i + 1) < len(opts):
            # Drop dependency generation.
            opts.pop(i + 1)
            opts.pop(i)

    try:
        subprocess.check_call(opts, stderr=subprocess.STDOUT, cwd=dir)
        size = os.stat(cpp_file).st_size
        return { 'bytes': size, 'lines': count_lines(cpp_file) }
    except:
        print 'FAIL: Unable to get preprocessed size.'
        return { 'bytes': 0, 'lines': 0 }


def get_preprocessed_size(cmd, dir):
    # TODO(m): Here we assume that we're running a compiler that understands GCC options.
    return gcc_get_preprocessed_size(cmd, dir)


def get_original_size(src_file):
    return { 'bytes': os.stat(src_file).st_size, 'lines': count_lines(src_file) }


def gcc_get_included_files(cmd, dir):
    opts = shlex.split(cmd)
    for i in range(len(opts) - 1, 0, -1):
        opt = opts[i].strip()
        if opt == '':
            # Drop empty items.
            opts.pop(i)
        if opt == '-c':
            # We only do the preprocessing step.
            opts[i] = '-E'
        elif opt == '-o' and (i + 1) < len(opts):
            # Do not ouptut anything.
            # TODO(m): Support Windows.
            opts[i + 1] = '/dev/null'
        elif opt in ['-M', '-MM', '-MG', '-MP', '-MD', '-MMD']:
            # Drop dependency generation.
            opts.pop(i)
        elif opt in ['-MF', '-MT', '-MQ'] and (i + 1) < len(opts):
            # Drop dependency generation.
            opts.pop(i + 1)
            opts.pop(i)
    # Add the -H option to output all included header files.
    opts.append('-H')

    try:
        res = subprocess.check_output(opts, stderr=subprocess.STDOUT, cwd=dir)
        files = []
        lines = res.split('\n')
        re_prg = re.compile('^\.+ ')
        for line in lines:
            if re_prg.match(line):
                file_name = line[(line.index(' ') + 1):].strip()
                if not os.path.isabs(file_name):
                    file_name = os.path.abspath(os.path.join(dir, file_name))
                files.append(file_name)
        return list(set(files))
    except:
        print 'FAIL: Unable to query number of included header files.'
        return []


def collect_header_files(cmd, dir):
    # TODO(m): Here we assume that we're running a compiler that understands GCC options.
    return gcc_get_included_files(cmd, dir)


def is_system_header(file_name):
    # TODO(m): Better heuristics.
    return file_name.startswith('/usr/') or file_name.startswith('/System/')


def record():
    build_dir = os.getcwd()

    # Check if we can find a compile database.
    compile_db_file = os.path.join(build_dir, 'compile_commands.json')
    if (not os.path.isfile(compile_db_file)):
        print "Could not find compile_commands.json in the current directory."
        sys.exit()

    # Read the compile database.
    with open(compile_db_file, 'r') as file:
        compile_db = json.loads(file.read())

    # Build information database.
    record_db = {}
    for item in compile_db:
        print item['file']

        dir = item['directory']
        src_file = os.path.abspath(os.path.join(dir, item['file']))

        # Count header files for this source file.
        header_files = collect_header_files(item['command'], dir)
        headers_all = 0
        headers_sys = 0
        for header_file in header_files:
            headers_all += 1
            if is_system_header(header_file):
                headers_sys += 1

        # Get the original size for this file.
        size = get_original_size(src_file)

        # Get the preprocessed size for this source file.
        size_pp = get_preprocessed_size(item['command'], dir)

        record_db[src_file] = {'headers_all': headers_all, 'headers_sys': headers_sys, 'size': size['bytes'],
                               'size_pp': size_pp['bytes'], 'lines': size['lines'], 'lines_pp': size_pp['lines']}

    # Write information database.
    record_db_file = os.path.join(build_dir, '.ccperf')
    with open(record_db_file, 'w') as file:
        json.dump(record_db, file, sort_keys=True, indent=2, separators=(',', ': '))

def main():
    parser = argparse.ArgumentParser(description='Generate IDE projects from Meson.')
    parser.add_argument('--record', action='store_true',
                        help='record performance metrics')
    parser.add_argument('--report', action='store_true',
                        help='report performance metrics')
    args = parser.parse_args()

    if args.record:
        record()
    elif args.report:
        report()
    else:
        print "Please specify a mode of operation."

if __name__ == "__main__":
    main()
