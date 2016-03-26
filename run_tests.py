import koji
from koji.tasks import BaseTaskHandler
import sys
import os
import shutil
from tempfile import mkdtemp, mkstemp
import ConfigParser

CONFIG_FILE = '/etc/kojid/run_tests.conf'

# Handler for running post-build tests
class RunTestsTask(BaseTaskHandler):
    Methods = ['runTests']
    _taskWeight = 2.0

    def __init__(self, *args, **kwargs):
        super(RunTestsTask, self).__init__(*args, **kwargs)

    def _read_config(self):
        cp = ConfigParser.SafeConfigParser()
        cp.read(CONFIG_FILE)
        if cp.has_option('general', 'tests_enabled'):
            self.tests_enabled = cp.getboolean('general', 'tests_enabled')
        else:
            self.tests_enabled = True

    # Executes the command and logs its output to the specified file
    def execLog(self, cmdline, logpath, append=False):
        log_fd = open(logpath, "a" if append else "w")
        log_fd.write("==> " + cmdline + "\n")
        log_fd.close()
        res = os.system(cmdline + " >>" + logpath + " 2>&1")
        return res

    # The task handler
    def handler(self, tag_id, build_id):
        self._read_config()
        if not self.tests_enabled:
            return "Tests are disabled by config, skipping"


        # Retrieve all necessary information
        tag_info = self.session.getTag(tag_id, strict=True)
        build_info = self.session.getBuild(build_id)
        subtasks = self.session.getTaskChildren(build_info['task_id'])

        # For each build task, get the list of built RPM files
        # and try to test-install them using the current build tag repos.
        for buildTask in subtasks:
            if buildTask['method'] != 'buildArch':
                # Skip everything except actual build tasks
                continue

            # Configure repositories to test packages with:
            # 1. for arch-specific build, using the corresponding repository;
            # 2. for noarch build, testing with both repositories.
            archs = []
            if buildTask['arch'] == 'x86_64':
                archs = ['x86_64']
            elif buildTask['arch'] == 'i686'
                 or buildTask['arch'] == 'i386'
                 or buildTask['arch'] == 'i486'
                 or buildTask['arch'] == 'i586':
                # Fix arch name for repository
                archs = ['i386']
            elif buildTask['arch'] == 'noarch':
                archs = ['i386', 'x86_64']
            else:
                raise koji.PostBuildError, "Unsupported build architecture: %s" % (buildTask['arch'])

            taskResult = self.session.getTaskResult(buildTask['id'])
            rpms = [('/mnt/koji/work/' + rpm) for rpm in taskResult['rpms']]

            for arch in archs:
                # Construct path to the build tag repository
                repo_path = "/mnt/koji/repos/" + tag_info['name'].replace("-candidate", "") + "-build/latest/" + arch + "/"
                # Yum config for installation test
                (yumcfg_fd, yumcfg_file) = mkstemp(prefix='koji-test-yum-', suffix='.cfg', text=True)
                os.write(yumcfg_fd, """
[main]
cachedir=/var/cache/yum
debuglevel=1
logfile=/var/log/yum.log
reposdir=/dev/null
retries=20
obsoletes=1
gpgcheck=0
assumeyes=1
tsflags=test
[repository]
name=VirtuozzoLinux
baseurl=file://%s
enabled=1
gpgcheck=0
""" % (repo_path))
                os.close(yumcfg_fd)

                # Root directory for installation test
                tmp_dir = mkdtemp(prefix='koji-test-root-')

                log_fname = "tests-b" + str(buildTask['id']) + '-' + arch + ".log"
                log_fpath = tmp_dir + "/" + log_fname
                cmdline = "yum install -v -y --config=%(yumcfg_file)s --installroot=%(tmp_dir)s " % locals() + " ".join(rpms)
                res = self.execLog(cmdline, log_fpath)
                self.uploadFile(log_fpath)
                shutil.rmtree(tmp_dir)
                os.unlink(yumcfg_file)

                # Check result
                if res != 0:
                    raise koji.PostBuildError, "Installation test failed, see %s for details." % (log_fname)

        return "Result: success"

# Override tagBuild task handler to add post-build tests
class TagBuildWithTestsTask(BaseTaskHandler):

    Methods = ['tagBuild']
    #XXX - set weight?

    def handler(self, tag_id, build_id, force=False, fromtag=None, ignore_success=False):
        task = self.session.getTaskInfo(self.id)
        user_id = task['owner']
        try:
            build = self.session.getBuild(build_id, strict=True)
            tag = self.session.getTag(tag_id, strict=True)

            #several basic sanity checks have already been run (and will be run
            #again when we make the final call). Our job is to perform the more
            #computationally expensive 'post' tests.

            try:
                task_id = self.session.host.subtask(method = 'runTests',
                                                    arglist = [tag_id, build_id],
                                                    label = 'test',
                                                    parent = self.id,
                                                    arch = 'noarch')
                self.wait(task_id)
            except koji.PostBuildError, e:
                # getPerms() always returns empty list; using explicit user ID instead
                if not (force and ('admin' in self.session.getUserPerms(user_id))):
                    raise e

            self.session.host.tagBuild(self.id, tag_id, build_id, force=force, fromtag=fromtag)
            self.session.host.tagNotification(True, tag_id, fromtag, build_id, user_id, ignore_success)
        except Exception, e:
            exctype, value = sys.exc_info()[:2]
            self.session.host.tagNotification(False, tag_id, fromtag, build_id, user_id, ignore_success, "%s: %s" % (exctype, value))
            raise e

