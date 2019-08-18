import re
from contextlib import contextmanager
import subprocess
from fastlmm.util.filecache import FileCache

#!!!cmk update everyting and confirm testing

class FileShare(FileCache): #!!!cmk rename FileShare to PeerToPeer (and put all these classes in their own file)
    #Later features:
    #   Limit the amount of copying for a given file
    #   Work even when multiple tasks on same node ask for the same file
    #   Get open_read,close to work with python With statement
    #   Have a way to clean up lost files
    def __init__(self,directory,local_lambda,leave_space=0):
        super(FileShare, self).__init__()
        if isinstance(directory,str):
            directory = LocalCache(directory)
        self.directory = directory
        self.local_lambda = local_lambda
        self.leave_space = leave_space
        assert not self.directory._simple_file_exists("main.txt"), "A directory cannot exist where a file already exists."

    def __repr__(self): 
        return "{0}('{1}','{2}')".format(self.__class__.__name__,self.directory,self.leave_space)

    @property
    def name(self):
        return self.directory

    def _simple_file_exists(self,simple_file_name):
        return self.directory.file_exists(simple_file_name+"/main.txt")

    def _far_file_sequence(self,dir_path):
        storage_list = [f for f in dir_path.walk() if self.copy_main_pattern.match(f)]
        random.shuffle(storage_list)
        
        for storage_item in storage_list:
            storage_path = dir_path.load(storage_item)
            yield storage_path, len(storage_list)

    copy_main_pattern = re.compile("^((main)|(copy_.*))\.txt$")
    copy_pattern = re.compile("^copy_.*\.txt$")

    def copy_time_stamp(self,storage_path,local_path):
        timestamp = os.path.getmtime(storage_path)
        with open(local_path, 'a'): #Touch the local file so that its mod time will be later than the remote time (see http://stackoverflow.com/questions/1158076/implement-touch-using-python)
            os.utime(local_path, (timestamp,timestamp))

    @contextmanager
    def _simple_open_read(self,simple_file_name,updater=None):

        logging.debug("open_read('{0}')".format(simple_file_name))
        #
        #Returns name of local file and locks that local until released
        #

        #Try to Get locally, return it if worked
        #Is there enough room? If not delete some files. If still not, fail
        #Ask origin for randomized list of copies (the last one will be the origin, but not all have to be included)
        #Try getting each copy. If all fail, then fail
        #Register local copy with the origin
        dir_path=self.directory.join(simple_file_name)
        unique_name, root = self.local_lambda()
        local_path = root + "/" + simple_file_name
        pstutil.create_directory_if_necessary(local_path,isfile=True)
        copy_name = "copy_{0}.txt".format(unique_name)
        main_path = self._robust_load_main(dir_path)
        file_size = os.path.getsize(main_path)

        if os.path.exists(local_path):
            if main_path==local_path or dir_path.file_exists(copy_name):
                logging.info("\tfound local")
                assert file_size == os.path.getsize(local_path), "Local file doesn't have the same size as the main file"
                yield local_path
                return
            else:
                logging.info("\tlost local found. Will remove")
                os.remove(local_path)
                pstutil.create_directory_if_necessary(local_path,isfile=True)

        dib_lib = DibLib(unique_name,dir_path,dir_path,"dibs")
        try:
            dib_lib.wait_for_turn()

            #Now we can copy. Choose a source at random
            for storage_path, storage_count in self._far_file_sequence(dir_path): # In a loop because in the future may want to handle copies being deleted.
                try: #If something goes wrong, try the next one
                    self._net_use(storage_path)
                    assert file_size == os.path.getsize(storage_path), "File to copy from doesn't have the same size as the main file"
                    if psutil.disk_usage(os.path.dirname(local_path)).free - file_size < self.leave_space: #clean up the directories
                        #Check every local file and if it is not in the directory (e.g. a temp file from other programs), remove it
                        local_dir = os.path.split(local_path)[0]
                        for other_file in os.listdir(local_dir):
                            full_file = local_dir + "/" + other_file
                            if os.path.isfile(full_file) and not self._simple_file_exists(other_file):
                                logging.info("\tNeed space and file isn't in directory so removing it. '{0}'".format(full_file))
                                os.remove(full_file)
                    logging.info("\tshutil.copyfile('{0}','{1}')".format(storage_path,local_path))
                    then = datetime.datetime.now()
                    shutil.copyfile(storage_path,local_path)
                    shutil.copystat(storage_path,local_path)
                    self.copy_time_stamp(storage_path,local_path)
                    dir_path.save(copy_name,local_path)
                    delta_sec = max((datetime.datetime.now()-then).total_seconds(),1.)
                    try: #The 'try' stops this logging message from getting a div by zero error some times
                        logging.info("Copy time is {0}. Copy speed is {1} Mbps".format(_format_delta(delta_sec), 8.0*float(file_size)/1e6/delta_sec))
                    except:
                        logging.info("Copy time is {0}. Copy speed can't be calculated Mbps".format(_format_delta(delta_sec)))
                    break
                except Exception, e:
                    if os.path.exists(local_path):
                        logging.warning("If a local file was created, but something went wrong (e.g. the source disappeared part way through the copying), so we remove it")
                        os.remove(local_path)
                    logging.warning("Ignore exception {0}".format(e))
            assert file_size == os.path.getsize(local_path), "File did not copy or did not copy completely."
            
        finally:
            dib_lib.remove_dibs()

        yield local_path

        assert dir_path._simple_file_exists("main.txt"), "File doesn't exist: '{0}'".format(path)

    @staticmethod
    def _net_use(path):
        if not path.startswith(r"\\"):
            return
        path = path.replace('/','\\')
        path = '\\'.join(path.split('\\')[:4])
        if True:
            subprocess.call(r'net use {0} "" /u:""'.format(path),shell=True)
        else:
            process = subprocess.Popen(r'net use {0} "" /u:""'.format(path), shell=True,stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out, err = process.communicate() # wait for the process to terminate
            if ("Multiple connections to a server or shared resource by the same user, using more than one user name, are not allowed." in err or
                "System error 5" in err):
                return
            logging.warning(err)
            #errcode = process.returncode
        
    def _robust_load_main(self,dir_path):
        main_path = dir_path.load("main.txt")
        try:
            self._net_use(main_path)
        except Exception, e:
            logging.info("ignoring exception when doing net use on {0}, {1}".format(main_path,e.message))
        if os.path.exists(main_path):
            return main_path
        logging.info("'{0}' does not exist, so looking for other copies.".format(main_path))
        for copy_file in dir_path._simple_walk():
            if not self.copy_pattern.match(copy_file):
                continue
            copy_path = dir_path.load(copy_file)
            try:
                self._net_use(copy_path)
            except Exception, e:
                logging.info("ignoring exception when doing net use on {0}, {1}".format(copy_path,e.message))
            if os.path.exists(copy_path):
                logging.info("Doing a copy to main repair. Not sure if this is safe")
                dir_path._simple_remove("main.txt")
                dir_path.save("main.txt",copy_path)
                dir_path._simple_remove(copy_file)
                return copy_path
        raise Exception("The main copy of the file can't be found and no replacement is available. '{0}'".format(main_path))

    def _remove_local_if_any(self,path):
        if path is None:
            unique_name, root = self.local_lambda()
            if os.path.exists(root):
                shutil.rmtree(root)   
        else:
            directory, simple_file_name = self._split(path)
            unique_name, root = directory.local_lambda()
            local_path = root + "/" + simple_file_name
            if os.path.exists(local_path):
                logging.info("Removing '{0}' to save space".format(local_path))
                os.remove(local_path)

    @contextmanager
    def _simple_open_write(self,simple_file_name,size=0,updater=None):
                
        logging.info("open_write('{0}',size={1})".format(simple_file_name,size))
        #Register the file name in the directory
        dir_path=self.directory.join(simple_file_name)
        assert not dir_path._simple_file_exists("main.txt"), "Can't open a file for write if it already exists."
        assert not self._at_least_one(dir_path._simple_walk()), "Can't open a file for write if a directory with files already has the same name ({0},{1})".format(self,simple_file_name)
        unique_name, root = self.local_lambda()
        local_path = root + "/" + simple_file_name
        #Anything in storage (file or directory) can be cleaned up.
        self._create_directory(local_path)
        if os.path.exists(local_path):
            logging.info("File with the same name found locally, but without a directory entry, so removing it. '{0}'".format(local_path))
            os.remove(local_path)
        assert psutil.disk_usage(os.path.dirname(local_path)).free - size > self.leave_space, "Not enough space for '{0}. Need to make space".format(local_path) 

        yield local_path

        logging.info("close('{0}')".format(simple_file_name))
        assert os.path.exists(local_path), "Expect file at '{0}'".format(local_path)
        assert psutil.disk_usage(os.path.dirname(local_path)).free > self.leave_space, "Not enough space for '{0}'. Need to make space".format(path)

        dir_path.save("main.txt",local_path)


    def _remove_internal(self, path):
        for storage_item in self.directory.walk(path):
            storage_path = self.directory.load(storage_item)
            if storage_path != "":  #Dibs files don't point anywhere, so we don't need to delete the file they point to.
                try:
                    logging.debug("/tos.remove('{0}')".format(storage_path))
                    os.remove(storage_path) #In storage, we remove the files, but not the folder
                except:
                    logging.debug("Can't remove (because it's not there): '{0}'".format(storage_path))
        self.directory.rmtree(path)

    def _simple_rmtree(self,log_writer=None):
        logging.info("rmtree -- {0}".format(self.directory))
        self._remove_internal(None)

    def _simple_remove(self,simple_file_name,log_writer=None):
        assert self._simple_file_exists(simple_file_name), "Expect file to exist (and be a file)"
        self._remove_internal(simple_file_name)

    def _simple_getmtime(self,simple_file_name):
        return self.directory._simple_getmtime(simple_file_name + "/main.txt")

    def _simple_join(self,path):
        def closure():
            unique_name, root = self.local_lambda()
            return unique_name, root+"/"+path
        fs = FileShare(self.directory._simple_join(path), local_lambda=closure,leave_space=self.leave_space)
        return fs

    def _simple_walk(self):
        for storage_item in self.directory._simple_walk():
            head, tail = os.path.split(storage_item)
            if tail == "main.txt":
                yield head

