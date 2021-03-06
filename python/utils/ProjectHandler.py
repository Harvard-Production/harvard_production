import os
import subprocess
import time
import shutil

from database import DatasetReader, ProjectUtils, ProjectReader

from config import ProjectConfig

class ProjectHandler(object):
    '''
    This class takes the input from the command line, parses,
    and takes the action needed.
    '''
    def __init__(self, config_file, action, stage=None):
        super(ProjectHandler, self).__init__()
        self.config_file = config_file
        self.stage = stage
        self.action = action

        self.stage_actions = ['submit', 'clean', 'status', 'check', 'statistics']
        self.project_actions = ['check', 'clean']

        if stage is None and self.action not in self.project_actions:
            raise Exception("Action {} not available".format(self.action))
        elif stage is not None and self.action not in self.stage_actions:
            raise Exception("Action {} not available".format(self.action))

        # Build the configuration class:
        self.config = ProjectConfig(config_file)

        # Make sure the stage requested is in the file:
        if stage is not None and stage not in self.config.stages:
            raise Exception('Stage {0} not in configuration file.'.format(stage))

        # Create the work directory:
        self.project_work_dir = self.config['top_dir'] + '/work/'

        if stage is not None:
            self.stage_work_dir = self.project_work_dir + stage + '/'


    def build_directory(self):

        self.make_directory(self.project_work_dir)


        self.make_directory(self.stage_work_dir)



    def act(self):
        if self.action == 'submit':
            self.submit()
        elif self.action == 'clean':
            self.clean()
        elif self.action == 'status':
            self.status()
        elif self.action == 'check':
            self.check()
        elif self.action == 'makeup':
            self.makeup()
        elif self.action == 'statistics':
            self.statistics()
        else:
            return



    def submit(self, makeup = False):
        '''
        Build a submission script, then call it to launch
        batch jobs.

        Slurm copies environment variables from the process that launches jobs,
        so we will make a child of the launching process in python and launch jobs
        with larsoft env variables set up.
        '''

        self.build_directory()

        # Get the active stage:
        stage = self.config.stage(self.stage)

        # First part of 'submit' is to make sure the input, work
        # and output directories exist
        print('Verifying output directory ..........')
        self.make_directory(stage.output_directory())
        print('Verifying project work directory ....')
        self.make_directory(self.project_work_dir)
        print('Verifying stage work directory ......')
        self.make_directory(self.stage_work_dir)

        if not makeup:
            print('Initializing database entries .......')
            # Make sure the datasets for this project are initialized:
            proj_util = ProjectUtils()

            proj_util.create_dataset(dataset = stage.output_dataset(),
                                     parents = stage.input_dataset())


        # If the stage work directory is not empty, force the user to clean it:
        if os.listdir(self.stage_work_dir) != [] and not makeup:
            print('Error: stage work directory is not empty.')
            raise Exception('Please clean the work directory and resubmit.')

        print('Building submission script ..........')
        # Next, build a submission script to actually submit the jobs
        job_name = self.config['name'] + '.' + stage.name
        script_name = self.stage_work_dir + '{0}_submission_script.slurm'.format(job_name)
        with open(script_name, 'w') as script:
            script.write('#!/bin/bash\n')
            script.write('#SBATCH --job-name={0}\n'.format(job_name))
            script.write('#SBATCH --ntasks=1\n')
            script.write('#SBATCH -p guenette\n')
            script.write('#SBATCH --mem={0}mb\n'.format(stage['memory']))
            script.write('#SBATCH --time={0}\n'.format(stage['time']))
            script.write('#SBATCH --output=array_%A-%a.log\n')
            script.write('\n')
            script.write('pwd; hostname; date;\n')
            script.write('whoami;\n')
            script.write('echo \"about to execute run_job.py.\";\n')
            script.write('unset module\n')
            script.write('unset helmod\n')
            script.write('\n')
            script.write('#Below is the python script that runs on each node:\n')
            script.write('run_job.py {0} {1} \n'.format(
                os.environ['PWD'] + '/' + self.config_file,
                self.stage))
            script.write('date;\n')
            script.write('\n')

        # Maximum running jobs is not set by default, but can be specified:

        n_jobs = stage.n_jobs()-1
        if makeup:
            with open(self.stage_work_dir + "makeup_jobs.txt", 'r') as _mj:
                n_jobs = int(_mj.readline())


        # Here is the command to actually submit jobs:
        command = ['sbatch', '-a', '0-{0}%{1}'.format(stage.n_jobs()-1, stage.concurrent_jobs()), script_name]

        with open(self.stage_work_dir + '/slurm_submission_command.txt', 'w') as _com:
            _com.write(' '.join(command))

        print("Submitting jobs ...")
        # Run the command:
        proc = subprocess.Popen(command,
                                cwd = self.stage_work_dir,
                                stdout = subprocess.PIPE,
                                stderr = subprocess.PIPE,
                                env = dict(os.environ))
        retval=proc.poll()
        # the loop executes to wait till the command finish running
        stdout=''
        stderr=''
        while retval is None:
            time.sleep(1.0)
            # while waiting, fetch stdout (including STDERR) to avoid crogging the pipe
            for line in iter(proc.stdout.readline, b''):
                stdout += line
            for line in iter(proc.stderr.readline, b''):
                stderr += line
            # update the return value
            retval = proc.poll()

        with open(self.stage_work_dir + '/submission_log.out', 'w') as _log:
            _log.write(stdout)
        with open(self.stage_work_dir + '/submission_log.err', 'w') as _log:
            _log.write(stderr)


        return_code = proc.returncode
        if return_code == 0:
            print("Submitted jobs successfully.")

            # Make sure to store the currently running jobID:
            jobid = int(stdout.split(' ')[-1])
            with open(self.stage_work_dir + 'current_running_jobid', 'w') as _log:
                _log.write(str(jobid))
        else:
            print("sbatch exited with status {0}, check output logs in the work directory".format(return_code))


    def make_directory(self, path):
        '''
        Make a directory safely
        '''
        try:
            os.makedirs(path)
        except OSError:
            if not os.path.isdir(path):
                raise

    def clean(self):
        '''
        Clean the project.  If stage is none clean the whole thing.
        Otherwise, clean only that stage.  If cleaning everything, clean the database file
        Only when files are deleted
        '''

        proj_utils = ProjectUtils()
        dataset_reader = DatasetReader()

        if not self.get_clean_confirmation():
            return
        # If stage is set, clean that stage only:
        if self.stage is not None:
            stage = self.config.stages[self.stage]
            # Remove files from the database and purge them from disk:
            for f in dataset_reader.list_file_locations(dataset=stage.output_dataset()):
                os.remove(f)
            # Clean the files from the database:
            proj_utils.drop_dataset(stage.output_dataset())

            shutil.rmtree(stage.output_directory())
            shutil.rmtree(self.stage_work_dir)
        else:
            # Clean ALL stages plus the work directory and the top level directory
            for name, stage in self.config.stages.iteritems():
                # Remove files from the database and purge them from disk:
                for f in dataset_reader.list_file_locations(dataset=stage.output_dataset()):
                    os.remove(f)
                proj_utils.drop_dataset(stage.output_dataset())
                if os.path.isdir(stage.output_directory()):
                    shutil.rmtree(stage.output_directory())
            if os.path.isdir(self.project_work_dir):
                shutil.rmtree(self.project_work_dir)
            if os.path.isdir(self.config['top_dir']):
                shutil.rmtree(self.config['top_dir'])


    def get_clean_confirmation(self):
        '''
        Force the user to confirm he/she wants to clean things up
        '''
        print 'You are requesting to clean the following stages:'
        if self.stage is not None:
            print '  {0}'.format(self.stage)
        else:
            for name, stage in self.config.stages.iteritems():
                print '  {0}'.format(stage.name)
            print('Additionally, this will delete:')
            print('  {0}'.format(self.project_work_dir))
            print('  {0}'.format(self.config['top_dir']))
        confirmation = raw_input('Please confirm this is the intended action (type \"y\"): ')
        if confirmation.lower() in ['y', 'yes']:
            return True
        return False

    def squeue_parse(self, jobid):

        # Going to use squeue for this command and parse the output

        command = ['/usr/bin/squeue', '--format=%.25i %.9P %.8j %.8u %.8T %.10M %.9l %.6D %R', '-j', str(jobid)]

        proc = subprocess.Popen(command,
                                cwd = self.stage_work_dir,
                                stdout = subprocess.PIPE,
                                stderr = subprocess.PIPE,
                                env = dict(os.environ))
        retval=proc.poll()
        # the loop executes to wait till the command finish running
        stdout=''
        stderr=''
        while retval is None:
            time.sleep(1.0)
            # while waiting, fetch stdout (including STDERR) to avoid crogging the pipe
            for line in iter(proc.stdout.readline, b''):
                stdout += line
            for line in iter(proc.stderr.readline, b''):
                stderr += line
            # update the return value
            retval = proc.poll()

        if retval != 0:

            raise Exception('Error when querying the job status.')

        # Now, start digging through the output
        lines = stdout.split('\n')
        if len(lines) <= 1:
            # No jobs running
            return None

        # Else, sort the jobs.
        job_status_counts = dict()
        keys = lines[0].split()
        state_index = -1
        jobid_index = -1
        i = 0
        for key in keys:
            if key == 'STATE':
                state_index = i
            if key == 'JOBID':
                jobid_index = i
            i += 1

        for line in lines[1:]:
            line  = line.split()
            if len(line) == 0:
                continue
            state = line[state_index]
            jobid = line[jobid_index]
            if state == 'PENDING':
                # have to do something special to count the number of pending jobs
                pnd_split = jobid.split('_')[-1]
                pnd_split = pnd_split.replace('[', '').replace(']', '')
                pnd_split = pnd_split.split('%')[0]
                n_jobs = int(pnd_split.split('-')[-1]) - int(pnd_split.split('-')[0]) + 1
                job_status_counts[state] = n_jobs
            else:
                if state not in job_status_counts.keys():
                    job_status_counts[state] = 1
                else:
                    job_status_counts[state] += 1

        return job_status_counts

    def status(self):
        '''
        The status function reads in the job id number from the work directory
        and queries the scheduler to get job status.
        '''
        # The job submission output is stored in the work directory.

        # Get the job ID from the submission script:


        if self.stage is None:
            raise Exception('Please specify a stage.')

        # Get the jobid, first:
        jobid = self.job_id()

        job_status_counts = self.squeue_parse(jobid)

        print('Condensed information for jobid {0}:'.format(jobid))
        for state, count in job_status_counts.iteritems():
            print('  {0} jobs in state {1}'.format(count, state))

    def job_id(self):
        '''Look up the job id

        '''
        # Get the job ID from the submission script:
        submission_log = self.stage_work_dir + '/current_running_jobid'
        with open(submission_log, 'r') as sl:
            line = sl.readline()
            job_id = int(line.split(' ')[-1])

        return job_id

    def is_running_jobs(self):
        '''Find out how many jobs are running or queued

        '''

        # Get the jobid, first:
        jobid = self.job_id()

        if self.squeue_parse(jobid) is None:
            return False
        else:
            return True

    def check(self):
        '''
        The check function parses the data base and prints out information
        about number of completed files and number of events processed
        '''

        if self.stage is not None:
            stage = self.config.stage(self.stage)
            self.check_stage(stage)
        else:
            for stage_name, stage in self.config.stages.iteritems():
                self.check_stage(stage)
        pass


    def print_check_information(self):
        pass


    def check_stage(self, stage):
        '''Check only a single stage

        Figure out what the goals of this stage were, and the results were

        Arguments:
            stage {StageConfig} -- stage identifier
        '''

        # First figure out what are the goals of this stage
        total_out_events = stage.total_output_events()
        total_ana_events = stage.total_output_events()
        if stage['output']['anaonly']:
            total_out_events = 0

        dataset_reader = DatasetReader()
        project_reader = ProjectReader()

        # Next, count the events declared to the database for this stage:
        n_ana_events = dataset_reader.sum(
            dataset=stage.output_dataset(),
            target='nevents',
            type=1)
        n_out_events = dataset_reader.sum(
            dataset=stage.output_dataset(),
            target='nevents',
            type=0)

        n_ana_files = dataset_reader.count_files(
            dataset=stage.output_dataset(),
            type=1)
        n_out_files = dataset_reader.count_files(
            dataset=stage.output_dataset(),
            type=0)

        print('Report for stage {0}: '.format(stage.name))
        print('  Completed {n_ana} events of {target} specified, across {n_ana_files} ana files.'.format(
            n_ana = n_ana_events, target = total_ana_events, n_ana_files=n_ana_files))
        print('  Completed {n_out} events of {target} specified, across {n_out_files} output files.'.format(
            n_out = n_out_events, target = total_out_events, n_out_files=n_out_files))

        # If this stage has an input, and therefore a consumption table,
        # Find out how many files are remaining to be processed and
        # How many are yielded but not consumed.

        if project_reader.has_parents(stage.output_dataset()):
            n_consumed = dataset_reader.count_consumption_files(
                dataset=stage.output_dataset(),
                state='consumed')
            n_unyielded = dataset_reader.count_consumption_files(
                dataset=stage.output_dataset(),
                state='unyielded')
            n_yielded = dataset_reader.count_consumption_files(
                dataset=stage.output_dataset(),
                state='yielded')
            print('  {0} files have been consumed from the input'.format(n_consumed))
            print('  {0} files have been yielded from the input without finishing'.format(n_yielded))
            print('  {0} files are unprocessed from the input'.format(n_unyielded))

        #Calculate how many makeup jobs to run
        # Look at:
        # how many events are supposed to be there
        # how many events were produced, over how many files
        # how many files per job are consumed (if using an input)

        # Since we don't always know how many events are in each job,
        # compare the number of produced events to the number of produced files:
        n_missing_events = 0
        out_events_per_file = 0
        if stage['output']['anaonly']:
            if n_ana_events is None or n_ana_events == 0:
                n_makeup_jobs = stage.n_jobs()
            else:
                n_missing_events = total_ana_events - n_ana_events
                out_events_per_file = n_ana_events / n_ana_files
                n_makeup_jobs = int(n_missing_events / out_events_per_file + 1)

        else:
            if n_out_events is None or n_out_events == 0:
                n_makeup_jobs = stage.n_jobs()
            else:
                n_missing_events = total_out_events - n_out_events
                out_events_per_file = n_out_events / n_out_files
                n_makeup_jobs = int(n_missing_events / out_events_per_file + 1)


        # How many events were produced over how many files?
        print('  Need to run {0} makeup jobs, makeup is not implemented yet.'.format(n_makeup_jobs))

        # Write the number of required makeup jobs to the work directory:
        makeup_log = self.stage_work_dir + "makeup_jobs.txt"
        with open(makeup_log, 'w') as _ml:
            _ml.write(str(n_makeup_jobs))


    def makeup(self):
        '''Run makeup jobs

        Search the list of completed jobs, and query how many jobs are not running

        If no jobs are running, submit jobs to complete the previous stage of running.
        '''

        # Makeup behavior is different for jobs with input than without.
        # For jobs without input, we look at the target number of events,
        # compare with the produced number of events/per file, calculate
        # the approximate number of needed jobs to meet the target, and submit that

        # For jobs with input, we reset the consumption status of failed jobs,
        # then compare the number of files per job in the yml to the number of unprocessed
        # files.  We submit the number of needed jobs to process remaining files.


        # First, make sure there are no jobs running for the current submission
        # of this project

        # Now, move the file containing the job id to a list of old job ids, and
        # clean the old file to make room for the new one.

        # First,
        n_makeup_jobs

        # Makeup command requires a check stage command first
        print ('Submission of makeup jobs is not implemented yet.')

    def statistics(self):

        ''' Call sacct to get the statistics for this job in long form.

        Saves to a file in the work area for this job.
        '''
        command = ['sacct']


        format_list = [
            'jobid%20',
            'jobname%50',
            'partition%30',
            'account%20',
            'maxvmsize',
            'avevmsize',
            'maxrss',
            'reqmem',
            'averss',
            'avecpu',
            'avecpufreq',
            'elapsed',
            'state',
            'exitcode',
            ]

        command.append('--format=' + ','.join(format_list) + '')
        # command.append('--long')


        command.append('-j')
        command.append(str(self.job_id()))

        proc = subprocess.Popen(command,
                                cwd = self.stage_work_dir,
                                stdout = subprocess.PIPE,
                                stderr = subprocess.PIPE,
                                env = dict(os.environ))
        retval=proc.poll()
        # the loop executes to wait till the command finish running
        stdout=''
        stderr=''
        while retval is None:
            time.sleep(1.0)
            # while waiting, fetch stdout (including STDERR) to avoid crogging the pipe
            for line in iter(proc.stdout.readline, b''):
                stdout += line
            for line in iter(proc.stderr.readline, b''):
                stderr += line
            # update the return value
            retval = proc.poll()

        if retval != 0:

            raise Exception('Error when querying the sacct database.')



        # Finished querying, write the output to a log file.
        file_name = "/sacct_long_job_{0}.out".format(self.job_id())
        with open(self.stage_work_dir + file_name, 'w') as _job_sacct_log:
            _job_sacct_log.write(stdout)


        print('sacct files for job_id {job_id} have been written to {path}'.format(
            job_id=self.job_id(),
            path=self.stage_work_dir + file_name))