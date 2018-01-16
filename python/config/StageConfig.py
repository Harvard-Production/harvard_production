
from ConfigException import ConfigException

class StageConfigException(ConfigException):
    ''' Custom exception for stages'''

    def __init__(self, key = None, name=None):
        message = 'Error Configuring Stage'
        if key is not None:
            message += ': Missing keyword {} in stage {}'.format(key, name)
        super(StageConfigException, self).__init__(message)

class StageConfig(object):
    '''
    Stage Configuration Object
    Stores the information from the larsoft configuration and includes
    helpful functions
    '''
    def __init__(self, yml_dict, name, previous_stage=None):
        super(StageConfig, self).__init__()
        required_keys=['fcl','n_jobs','events_per_job','input','output']
        required_subkeys={'input'  : ['type', 'location'],
                          'output' : ['type', 'location']}
        for key in required_keys:
            if key not in yml_dict:
                raise StageConfigException(key, name)
            # Check for required subkeys:
            if key in required_subkeys.keys():
                for subkey in required_subkeys[key]:
                    if subkey not in yml_dict[key]:
                        raise StageConfigException(subkey, "{}/{}".format(name,key))

        self.name = name
        self.yml_dict = yml_dict
        self.previous_stage=previous_stage

    def __getitem__(self, key):
        return self.yml_dict[key]

    def output_directory(self):
        return self.yml_dict['output']['location']

    def output_file(self):
        '''
        Return the output file for this job
        '''
        if not self.has_input():
            return

    def get_next_files(self, n, db=None):
        '''
        Function to interface with file interface tools
        and fetch files.  Returns absolute paths
        '''

        # If the input is none, we return None:
        if self.yml_dict['input']['type'] == 'none' or self.previous_stage is None:
            return None

        else:
            if db is None:
                raise Exception("Can not list next files if no database.")
            # Otherwise, access the data base and consume files:
            print db.consume_files(self.previous_stage, ftype=0, max_n_files=n)

    def n_jobs(self):
        '''
        Return the number of jobs to launch for this stage
        '''
        return int(self.yml_dict['n_jobs'])

    def events_per_job(self):
        '''
        Return the number of events to process per job
        '''

        if int(self.yml_dict['events_per_job']) != -1:
            return int(self.yml_dict['events_per_job'])
        else:
            return None

    def n_files(self):
        '''
        Return the number of files to process in a single job, default is one
        '''

        if 'n_files' in self.yml_dict['input']:
            return int(self.yml_dict['input']['n_files'])
        return 1

    def fcl(self):
        '''
        Return the fcl file for this stage.
        '''
        return self.yml_dict['fcl']

    def has_input(self):
        '''
        Return whether this stage has input or not
        '''
        if self.yml_dict['input']['type'] == 'none':
            return False
        else:
            return True