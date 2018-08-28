#!/usr/bin/env python

import argparse
import AutoVivification as av
import pprint as pp
import os
import sys
import copy
from collections import Mapping
import subprocess

# The various steps - they will match the targets in the Makefile
steps = ["{cc}_compile",
         "{simulator}_{target}_build",
         "{simulator}_{target}_elab",
         "{simulator}_{target}_sim",
]

noccomp_steps = [
         "{simulator}_{target}_build",
         "{simulator}_{target}_elab",
         "{simulator}_{target}_sim",
]

buildonly_steps = [
         "{simulator}_{target}_build",
]

c_only_steps = ["{cc}_compile"]

tpl_verilog_parameter = "VERILOG_PARAMETER += +{var_name_lc}={val}\n"
tpl_make_variable     = "{var_name_uc}={val}\n"
tpl_c_define          = " -D{var_name_uc}={val}"
tpl_verilog_define    = "VERILOG_DEFINES += -D{var_name_uc}={val}\n"

tpl_main_makefile_footer  = """

include {config_rel_dir}/gcc.mk
include {config_rel_dir}/icarus.mk
include {config_rel_dir}/verilator.mk
include {config_rel_dir}/xilinx.mk
include {config_rel_dir}/pysim.mk


debug:
	@echo "Hello world"

"""

tpl_main_makefile_header  = """# This Makefile has been generated by runtest.py
TOP={top_rel_dir}
TEST={test_name}
TEST_DIR_FROM_TOP={test_dir_from_top}
TEST_DIR=$(TOP)/$(TEST_DIR_FROM_TOP)

"""

def color_print_result(res,txt):
    class bcolors:
        if sys.stdout.isatty():
            # we are running in a real terminal - we hope it is support colors
            HEADER = '\033[95m'
            OKBLUE = '\033[94m'
            OKGREEN = '\033[92m'
            WARNING = '\033[93m'
            FAIL = '\033[91m'
            ENDC = '\033[0m'
            BOLD = '\033[1m'
            UNDERLINE = '\033[4m'
        else:
            # probably a I/O redirection on going
            HEADER = ''
            OKBLUE = ''
            OKGREEN = ''
            WARNING = ''
            FAIL = ''
            ENDC = ''
            BOLD = ''
            UNDERLINE = ''


    if res=='ok':
        print bcolors.OKGREEN + "[OK]      " + bcolors.ENDC + txt
    if res=='failed':
        print bcolors.FAIL    + "[FAILED]  " + bcolors.ENDC + txt
    if res=='skipped':
        print bcolors.WARNING + "[SKIPPED] " + bcolors.ENDC + txt
    if res=='header':
        print bcolors.HEADER + "==== {} ====".format(txt) + bcolors.ENDC



def top_dir(sim_path):
    "Get top dir of the database if called from sim directory (the one where runtest.py is called)"
    return '/'.join(sim_path.split('/')[:-1])


def merge_dict2(a, b, path=None):
    "merges b into a, with override"
    if path is None: path = []
    for key in b:
        if key in a:
            if isinstance(a[key], dict) and isinstance(b[key], dict):
                merge_dict2(a[key], b[key], path + [str(key)])
            elif a[key] == b[key]:
                pass # same leaf value
            else:
                a[key] = b[key]
                #raise Exception('Conflict at %s' % '.'.join(path + [str(key)]))
        else:
            a[key] = b[key]
    return a

# From http://stackoverflow.com/questions/10306672/how-do-you-iterate-over-two-dictionaries-and-grab-the-values-at-the-same-path

def treeZip(t1,t2, path=[]):
    if isinstance(t1,Mapping) and isinstance(t2,Mapping):
        assert set(t1)==set(t2)
        for k,v1 in t1.items():
            v2 = t2[k]
            for tuple in treeZip(v1,v2, path=path+[k]):
                yield tuple
    elif isinstance( t1, list) and isinstance( t2, list ):
        for idx,item in enumerate(t1):
            v1 = item
            v2 = t2[idx]
            for tuple in treeZip(v1, v2):
                yield tuple
    else:
        yield (path, (t1,t2))




def get_args():
    """
    Get command line arguments
    """

    parser = argparse.ArgumentParser(description="""
A simulation launcher for the Nanorv32 project
                   """)
    parser.add_argument('-l', '--logging', action='store_true', dest='logging',
                        help='Waveform logging (VCD,...)')

    parser.add_argument('-t', '--trace', action='store', dest='trace',
                        help='Activate CPU trace ')

    parser.add_argument('-g', '--gui', action='store_true', dest='gui',
                        default=False,
                        help='Launch simulator GUI if applicable ')


    parser.add_argument('--target', action='store', dest='target',
                        choices = ['rtl','ntl', 'sdf'],
                        default='rtl',
                        help='Simulation type (rtl, sdf,...)')

    parser.add_argument('-c', action='store_true', dest='compile_only',
                        default=False,
                        help='Run only C compilation')

    parser.add_argument('-s', '--simulator', action='store', dest='simulator',
                        default='icarus',
                        choices = ['icarus','xilinx','pysim','verilator'],

                        help='Select simulator (iverilog, xilinx(xlog),...)')

    parser.add_argument('--cc', action='store', dest='cc',
                        default='gcc',
                        choices = ['gcc','llvm'],

                        help='Select C compiler')

    parser.add_argument('--noexec', action='store_true', dest='noexec',
                        default=False,
                        help='Do not execute the command')

    parser.add_argument('--noccomp', action='store_true', dest='noccomp',
                        default=False,
                        help='Skip C compilation')

    parser.add_argument('--buildonly', action='store_true', dest='buildonly',
                        default=False,
                        help='Only run the RTL build step')

    parser.add_argument('--rvc', action='store_true', dest='rvc',
                        default=False,
                        help='Allow usage  RVC (16-bits instructions) for the C compiler')

    parser.add_argument('-f', action='store_true', dest='target_fpga',
                        default=False,
                        help='Define FPGA=1 for C compilation (mainly to use Uart output for printf ) - Likely to be used with -c')

    parser.add_argument('--cdefines', action='store', dest='cdefines',
                        default=None,
                        help='Comma separated list of define/value that will override the extra_defines definition  : XX=1,YY=2')


    parser.add_argument('--map', action='store', dest='map',
                        default=None,
                        help='Map file for profiling')


    parser.add_argument(dest='tests', metavar='tests', nargs='*',
                        help='Path(es) to the test')

    parser.add_argument('-v', '--verbosity', action="count", help='Increase output verbosity')



    parser.add_argument('--version', action='version', version='%(prog)s 0.1')

    return parser.parse_args()

if __name__ == '__main__':
    args = get_args()
    if args.verbosity:
        print "Verbosity set to {}".format(args.verbosity)

    global_args = dict()
    global_args['trace'] =  args.trace
    global_args['simulator'] = args.simulator
    global_args['target'] = args.target
    global_args['cc'] = args.cc
    global_args['noexec'] = args.noexec
    global_args['rvc'] = args.rvc
    global_args['gui'] = args.gui
    global_args['logging'] = args.logging
    global_args['target_fpga'] = args.target_fpga
    global_args['noccomp'] = args.noccomp
    global_args['buildonly'] = args.buildonly

    # process cdefines arguments
    cdefines_txt = ""
    cdefines_dict = av.AutoVivification()

    if args.cdefines:
        cdefines_dict['c_compiler']['extra_defines'] = ""
        cdefines = args.cdefines.split(',')
        for df in cdefines:
            d = dict()
            def_l = df.split('=')
            d['var_name_uc'] = def_l[0]
            d['val'] = def_l[1]
            cdefines_dict['c_compiler']['extra_defines'] += tpl_c_define.format(**d)


    # main loop over tests
    for test in args.tests:
        color_print_result('header',test)

        # we reset everything
        default_opts = av.AutoVivification()
        define_opts = av.AutoVivification()
        override_opts= av.AutoVivification()


        # we start creating some variables regarding test directory and test name
        # that could be useful later

        # For nanorv32, we need to be able to pass
        # a assembly file in addition of the default debaviour
        # (a directory)
        test_dir = '' # we need that later also
        test_is_a_file = False
        test_name = ''
        if(os.path.isdir(test)):
            opt_file = test + '/options.py'
            test_dir = os.path.realpath(test)
            test_is_a_file = False
            test_name = test_dir.split('/')[-1]

        elif(os.path.isfile(test)):
            opt_file = os.path.dirname(test) + '/options.py'
            test_dir = os.path.dirname(test)
            test_is_a_file = True
            test_name = os.path.splitext(os.path.basename (test))[0]
        else:
            color_print_result('failed',"-E- Can't find what or where <{}> is...".format(test))
            sys.exit()

        # we add those variables to the global_args so that the default/config/override files
        # can access them
        global_args['test'] = test
        global_args['test_dir'] = test_dir
        global_args['test_name'] = test_name
        global_args['test_is_a_file'] = test_is_a_file
        global_args['verbosity'] = args.verbosity


        # we parse the default configuration file
        execfile("./config/default.py", global_args, {"cfg": default_opts, "define" : define_opts})

        # and the override file
        execfile("./config/override.py", global_args, {"cfg": override_opts})

        if args.verbosity>3:
            print "Parsing override.py (using test_dir  {})".format(test_dir)
            pp.pprint(override_opts)


        if args.verbosity>0:
            print "Parsing options for test {}".format(test)
        local_opts = av.AutoVivification()

        if os.path.isfile(opt_file):
            execfile(opt_file, global_args, {"cfg": local_opts})

        # We merge (with eventually override) all the definitions
        merge_dict2(default_opts, local_opts)
        merge_dict2(default_opts, override_opts)
        # finally, what is defined on the command line
        if cdefines_dict:
            merge_dict2(default_opts, cdefines_dict)

        # we merge the spec and define dictionnary
        # we get a list of tupples  (  (a,b) (c,d) ....)
        # with the first element being a list representing the "path" in the hierarchy
        # of nested directories (use pp.pprint to see the result eventuall)
        all_data = list(treeZip (default_opts,define_opts, path=[]))
        if args.verbosity >3:
            pp.pprint(all_data)
        # Now we generate the content of the makefile.
        # For each entry, we create a line in the Makefile
        # depending of the expected type (as defined in the define[][]...[])
        # in the default.py

        txt = cdefines_txt
        for path,v  in all_data:

            d = dict() # use for string format(**d)
            var_name = '_'.join(path) # the variable name is derived directly
            # from the path in the dictionnary
            d['var_name_uc'] = var_name.upper()
            d['var_name_lc'] = var_name.lower()
            d['val'] = v[0]
            # The type of the parameters can be a single string
            # or a sequence
            # we convert everything to a list

            val_type = v[1]
            val_l = list()
            if type(val_type) == str:
                val_l.append(val_type)

            elif type(val_type) == tuple:
                val_l = val_type
            else:
                sys.exit("-E- Unrecognized type for {} : {}".format(val_type,type(val_type)))
                pass
            # now val_l contains a list of possible value types
            # for each type, we have a specific way to handle the values
            for t in val_l:
                if t == 'VERILOG_PARAMETER':
                    txt += tpl_verilog_parameter.format(**d)
                elif t == 'MAKE_VARIABLE':
                    txt += tpl_make_variable.format(**d)
                elif t == 'C_DEFINE':
                    txt += tpl_c_define.format(**d)
                elif t == 'VERILOG_DEFINE':
                    txt += tpl_verilog_define.format(**d)

        # Here, txt contains now the Makefile main content
        # We add some extra stuff to include the main Makefiles
        # for the tools (gcc, icarus,...)
        # and some useful variables like the TOP directory
        # of the database

        cwd = os.getcwd()
        topdir = top_dir(cwd)
        # Get test dir
        # Note : we pass a directory to runtest



        test_dir_from_cwd = os.path.relpath(test_dir,cwd)
        test_dir_from_top = os.path.relpath(test_dir,topdir)
        cwd_from_test_dir = os.path.relpath(cwd,test_dir)
        config_rel_dir = cwd_from_test_dir + "/config"
        top_rel_dir = os.path.relpath(topdir,test_dir)

        if args.verbosity>1:
            print "Test name  : {}".format(test_name)
            print "Current directory : {}".format(cwd)
            print "Top directory : {}".format(topdir)
            print "Test directory : {}".format(test_dir)
            print "Test directory (relative to cwd): {}".format(test_dir_from_cwd)
            print "Test directory (relative to top): {}".format(test_dir_from_top)
            print "CWD directory (relative to test dir): {}".format(cwd_from_test_dir)
            print "TOP directory (relative to test dir): {}".format(top_rel_dir)

        d = dict()
        d['config_rel_dir'] = config_rel_dir
        d['top_rel_dir'] = top_rel_dir
        d['test_dir'] = test_dir
        d['test_dir_from_top'] = test_dir_from_top
        d['test_name'] = test_name
        txt += tpl_main_makefile_footer.format(**d)
        txt = tpl_main_makefile_header.format(**d) + txt
        # We are done with the MAkefile content, we write it into the test directory
        makefile = test_dir +"/Makefile"
        with open(makefile,'w') as f:
            f.write(txt)

        # Now, we are ready to launch the various jobs
        # We build the Makefile targets based on c compiler and verilator selections
        if args.compile_only:
            current_steps = c_only_steps
        elif args.noccomp:
            current_steps = noccomp_steps
        elif args.buildonly:
            current_steps = buildonly_steps
        else:
            current_steps = steps
        final_step_list = [s.format(**global_args) for s in current_steps]
        #pp.pprint(final_step_list)

        for s in final_step_list:
            log_file = test_dir+"/" + test_name + "_" + s + ".log"
            # cmd = "make -C {} {} 2>&1 | tee {}".format(test_dir,s,log_file)
            # above coomand line will not work if error is returned by the
            # make command - we get the error code of tee
            cmd = "make -C {} {}".format(test_dir, s)
            if args.verbosity >0:
                print "-I- executing {}".format(cmd)
            if not global_args['noexec']:
                result = -1
                out = ""
                if args.verbosity >0:
                    result = subprocess.call(cmd, shell=True)
                    if result != 0:
                        sys.exit("-E- Error in step {}".format(s))
                    else:
                        print "-I- return value for step {} : {}".format(s,result)
                else:
                    try:
                        output = subprocess.check_output(cmd,
                                                      stderr=subprocess.STDOUT,
                                                      shell=True)
                        with open(log_file,'w') as log:
                            log.write(output)
                        color_print_result('ok',s)
                    except subprocess.CalledProcessError as e:
                        with open(log_file,'w') as log:
                            log.write(e.output)
                        color_print_result('failed',test + " : " + s)
                        sys.exit("-E- Error in step {} - return value {}: ".format(s,e.returncode))
