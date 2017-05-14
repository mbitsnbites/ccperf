#!/usr/bin/env python3
import argparse
import concurrent.futures
import json
import multiprocessing
import os
import re
import shlex
import sys
import subprocess
import tempfile

def count_lines(file_name):
    count = 0
    with open(file_name, 'r') as file:
        for line in file:
            count += 1
    return count


def gcc_preprocess_file(cmd, dir):
    # Create a temporary file.
    temp_fd, cpp_file = tempfile.mkstemp('.i', 'ccperf')
    try:
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

        # Add the -H option to output all included header files.
        opts.append('-H')

        try:
            res = subprocess.check_output(opts, stderr=subprocess.STDOUT, cwd=dir)
            try:
                res = res.decode()
            except AttributeError:
                pass

            # Get a list of all included header files.
            header_files = []
            lines = res.split('\n')
            re_prg = re.compile('^\.+ ')
            for line in lines:
                if re_prg.match(line):
                    file_name = line[(line.index(' ') + 1):].strip()
                    if not os.path.isabs(file_name):
                        file_name = os.path.abspath(os.path.join(dir, file_name))
                    header_files.append(file_name)
            header_files = list(set(header_files))

            # Get the size of the preprocessed file.
            size = os.stat(cpp_file).st_size
            num_lines = count_lines(cpp_file)

            result = { 'bytes': size, 'lines': num_lines, 'header_files': header_files }
        except:
            print('*** Preprocessing the source file failed.')
            result = { 'bytes': 0, 'lines': 0, 'header_files': [] }

    finally:
        # We're done with the temporary file.
        os.close(temp_fd)
        if os.path.isfile(cpp_file):
            os.remove(cpp_file)

    return result

def preprocess_file(cmd, dir):
    # TODO(m): Here we assume that we're running a compiler that understands GCC options.
    return gcc_preprocess_file(cmd, dir)


def get_original_size(src_file):
    return { 'bytes': os.stat(src_file).st_size, 'lines': count_lines(src_file) }


def is_system_header(file_name):
    # TODO(m): Better heuristics.
    return file_name.startswith('/usr/') or file_name.startswith('/System/')

def collect_metrics(dir, file, command):
    print(file)

    src_file = os.path.abspath(os.path.join(dir, file))

    # Get the original size for this file.
    src_info = get_original_size(src_file)

    # Run the preprocessor to collect size metrics.
    pp_info = preprocess_file(command, dir)

    # Count header files for this source file.
    headers_all = 0
    headers_sys = 0
    for header_file in pp_info['header_files']:
        headers_all += 1
        if is_system_header(header_file):
            headers_sys += 1

    return {'file': src_file,
            'headers_all': headers_all,
            'headers_sys': headers_sys,
            'bytes': src_info['bytes'],
            'bytes_pp': pp_info['bytes'],
            'lines': src_info['lines'],
            'lines_pp': pp_info['lines']}


def record(num_jobs):
    build_dir = os.getcwd()

    # Check if we can find a compile database.
    compile_db_file = os.path.join(build_dir, 'compile_commands.json')
    if (not os.path.isfile(compile_db_file)):
        print("Could not find compile_commands.json in the current directory.")
        sys.exit()

    # Read the compile database.
    with open(compile_db_file, 'r') as file:
        compile_db = json.loads(file.read())

    # Use a thread pool to build the information database in parallel.
    record_db = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_jobs) as executor:
        results = []
        for item in compile_db:
            results.append(executor.submit(collect_metrics, item['directory'], item['file'], item['command']))

        for future in results:
            try:
                record_db.append(future.result())
            except Exception as exc:
                print('*** Exception: %s' % (exc))

    # Write information database.
    record_db_file = os.path.join(build_dir, '.ccperf')
    with open(record_db_file, 'w') as file:
        json.dump(record_db, file, sort_keys=True, indent=2, separators=(',', ': '))

def report():
    # TODO(m): Implement me!
    return

def num_hw_threads():
    try:
        cpu_count = multiprocessing.cpu_count()
    except:
        cpu_count = 8
    return int(cpu_count * 1.1 + 0.5)

def main():
    num_jobs = num_hw_threads()
    parser = argparse.ArgumentParser(description='Generate IDE projects from Meson.')
    parser.add_argument('--record', action='store_true',
                        help='record performance metrics')
    parser.add_argument('--report', action='store_true',
                        help='report performance metrics')
    parser.add_argument('-j', metavar='N', type=int, dest='num_jobs', default=num_jobs,
                        help='number of parallel jobs (default: %d)' % num_jobs)
    args = parser.parse_args()

    if args.record:
        record(args.num_jobs)
    elif args.report:
        report()
    else:
        print("Please specify a mode of operation.")

if __name__ == "__main__":
    main()
