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
import time


def get_time():
    try:
        return time.perf_counter()
    except:
        return time.clock()


def count_lines(file_name):
    count = 0
    with open(file_name, 'r') as file:
        for line in file:
            count += 1
    return count


def is_gcc_command(cmd):
    try:
        program = os.path.basename(shlex.split(cmd)[0]).lower()
        return ('gcc' in program) or ('g++' in program) or ('clang' in program) or ('clang++' in program)
    except:
        return False


def dummy_preprocess_file(cmd, dir):
    return { 'bytes': 0, 'lines': 0, 'header_files': [], 'time': 0.0 }


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
            t1 = get_time()
            res = subprocess.check_output(opts, stderr=subprocess.STDOUT, cwd=dir)
            t2 = get_time()
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

            result = { 'bytes': size, 'lines': num_lines, 'header_files': header_files, 'time': t2 - t1 }
        except:
            print('*** Preprocessing the source file failed.', file=sys.stderr)
            result = dummy_preprocess_file()

    finally:
        # We're done with the temporary file.
        os.close(temp_fd)
        if os.path.isfile(cpp_file):
            os.remove(cpp_file)

    return result


def preprocess_file(cmd, dir):
    if is_gcc_command(cmd):
        return gcc_preprocess_file(cmd, dir)
    else:
        return dummy_preprocess_file(cmd, dir)


def get_original_size(src_file):
    return { 'bytes': os.stat(src_file).st_size, 'lines': count_lines(src_file) }


def run_cmd(cmd, dir):
    t1 = get_time()
    try:
        subprocess.check_output(cmd, stderr=subprocess.STDOUT, cwd=dir, shell=True)
    except subprocess.CalledProcessError as e:
        print('*** Running the command failed:\n{}'.format(e.stdout.decode()), file=sys.stderr)
    except:
        print('*** Running the command failed.', file=sys.stderr)
    t2 = get_time()
    return { 'time': t2 - t1 }


def dummy_run_cmd(cmd, dir):
    return { 'time': 0.0 }


def is_system_header(file_name):
    # TODO(m): Better heuristics.
    return file_name.startswith('/usr/') or file_name.startswith('/System/')


def collect_metrics(dir, file, command, do_run_command=False):
    print(file, file=sys.stderr)

    src_file = os.path.abspath(os.path.join(dir, file))

    # Get the original size for this file.
    src_info = get_original_size(src_file)

    # Run the preprocessor to collect size metrics.
    pp_info = preprocess_file(command, dir)

    # Run the command to collect performance metrics.
    if do_run_command:
        run_info = run_cmd(command, dir)
    else:
        run_info = dummy_run_cmd(command, dir)

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
            'lines_pp': pp_info['lines'],
            'time_pp': pp_info['time'],
            'time_run': run_info['time']}


def record(num_jobs, do_run_command=False):
    build_dir = os.getcwd()

    # Check if we can find a compile database.
    compile_db_file = os.path.join(build_dir, 'compile_commands.json')
    if (not os.path.isfile(compile_db_file)):
        print("Could not find compile_commands.json in the current directory.", file=sys.stderr)
        sys.exit()

    # Read the compile database.
    with open(compile_db_file, 'r') as file:
        compile_db = json.loads(file.read())

    # Use a thread pool to build the information database in parallel.
    record_db = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_jobs) as executor:
        results = []
        for item in compile_db:
            results.append(executor.submit(collect_metrics, item['directory'], item['file'], item['command'], do_run_command))

        for future in results:
            try:
                record_db.append(future.result())
            except Exception as exc:
                print('*** Exception: %s' % (exc), file=sys.stderr)

    # Write information database.
    record_db_file = os.path.join(build_dir, '.ccperf')
    with open(record_db_file, 'w') as file:
        json.dump(record_db, file, sort_keys=True, indent=2, separators=(',', ': '))

def load_info_db():
    build_dir = os.getcwd()
    info_db_file = os.path.join(build_dir, '.ccperf')
    if (not os.path.isfile(info_db_file)):
        print("Could not find .ccperf in the current directory. Please use --record.", file=sys.stderr)
        sys.exit()

    # Read the information database.
    with open(info_db_file, 'r') as file:
        info_db = json.loads(file.read())

    return info_db


def generate_csv():
    info_db = load_info_db()

    # Dump the information database as CSV.
    # TODO(m): Implement more sophisticated report generators.
    print('File\tHeaders (all)\tSystem headers\tBytes\tLines\tBytes preproc.\tLines preproc.\tTime preproc.\tTime run')
    for item in info_db:
        print('%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s' % (
            item['file'],
            item['headers_all'],
            item['headers_sys'],
            item['bytes'],
            item['lines'],
            item['bytes_pp'],
            item['lines_pp'],
            item['time_pp'],
            item['time_run']))


def num_hw_threads():
    try:
        cpu_count = multiprocessing.cpu_count()
    except:
        cpu_count = 8
    return int(cpu_count * 1.1 + 0.5)

def main():
    num_jobs = num_hw_threads()
    parser = argparse.ArgumentParser(description='Collect and present build performance metrics for a C/C++ project.')
    parser.add_argument('--record', action='store_true',
                        help='record performance metrics')
    parser.add_argument('-j', metavar='N', type=int, dest='num_jobs', default=num_jobs,
                        help='number of parallel jobs (default: %d)' % num_jobs)
    parser.add_argument('--no-run', action='store_true',
                        help='do not run the actual compile commands')
    args = parser.parse_args()

    if args.record:
        record(args.num_jobs, not args.no_run)

    generate_csv()

if __name__ == "__main__":
    main()
