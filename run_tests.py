import koji
from koji.tasks import BaseTaskHandler
import sys
import os
import re
import shutil
from tempfile import mkdtemp, mkstemp
import ConfigParser
import json
import subprocess

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
        if cp.has_option('general', 'exceptions'):
            self.tests_exceptions = json.loads(cp.get('general', 'exceptions'))
        else:
            self.tests_exceptions = []
        if cp.has_option('general', 'tag_exceptions'):
            self.tests_tag_exceptions = json.loads(cp.get('general', 'tag_exceptions'))
        else:
            self.tests_tag_exceptions = []

    # Executes the command and logs its output to the specified file
    def execLog(self, cmdline, logpath, append=False):
        log_fd = open(logpath, "a" if append else "w")
        log_fd.write("==> " + cmdline + "\n")
        log_fd.write("Exceptions: " + str(self.tests_exceptions))
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
        if tag_info['name'] in self.tests_tag_exceptions:
            return "Tag is in the exceptions list, skipping"

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
            elif buildTask['arch'] == 'i686' \
                 or buildTask['arch'] == 'i386' \
                 or buildTask['arch'] == 'i486' \
                 or buildTask['arch'] == 'i586':
                # Fix arch name for repository
                archs = ['i386']
            elif buildTask['arch'] == 'noarch':
                # We don't build 32bit version for many packages
                archs = ['x86_64']
            else:
                raise koji.PostBuildError, "Unsupported build architecture: %s" % (buildTask['arch'])

            taskResult = self.session.getTaskResult(buildTask['id'])
            rpms = [('/mnt/koji/work/' + rpm) for rpm in taskResult['rpms'] if not any([name in rpm for name in self.tests_exceptions]) and not "debuginfo" in rpm]
            if len(rpms) == 0:
                return "No packages to check - packages are either missing or included in the exception list"

            for arch in archs:
                # Construct path to the build tag repository
                try:
                    p = subprocess.Popen(['koji', 'list-targets', '--name', tag_info['name'], '--quiet'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    out, err = p.communicate()
                    target_repo = out.split()[1]
                except:
                    return "Couldn't detect target repo - this task is likely not subjected for tests, skipping..."

                repo_path = "/mnt/koji/repos/" + target_repo + "/latest/" + arch + "/"
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
exclude=*debuginfo*
""" % (repo_path))
                os.close(yumcfg_fd)

                # Root directory for installation test
                tmp_dir = mkdtemp(prefix='koji-test-root-')

                log_fname = "tests-b" + str(buildTask['id']) + '-' + arch + ".log"
                log_fpath = tmp_dir + "/" + log_fname
                # Have to use dnf here even in CentOS 7 since yum will fail with parse error
                # if it meets rich dependencies
                cmdline = "dnf install -v -y --config=%(yumcfg_file)s --installroot=%(tmp_dir)s " % locals() + " ".join(rpms)
                res = self.execLog(cmdline, log_fpath)

                # Check result
                if res != 0:
                    # Even if we failed, this is not the ultimate results.
                    # It can arise from tich dependencies aka '(foo if bar)' which can't be resolved by
                    # dnf/rpm at CentOS 7
                    success = 1
                    with open(log_fpath) as f:
                        for l in f.readlines():
                            if 'is needed' not in l or 'rpmlib(RichDependencies)' in l:
                                continue
                            m = re.search(r'\((\S+) if ([^)]+)\) is needed', l)
                            if m:
                                # If we found a rich dep, let's check if its first part can be always installed
                                p = m.groups(0)[0]
                                cmdline = "dnf install -v -y --config=%(yumcfg_file)s --installroot=%(tmp_dir)s " % locals() + p
                                res2 = self.execLog(cmdline, log_fpath, append=True)
                                if res2 != 0:
                                    success = 0
                            else:
                                success = 0

                    self.uploadFile(log_fpath)
                    shutil.rmtree(tmp_dir)
                    os.unlink(yumcfg_file)
                    if success != 1:
                        raise koji.PostBuildError, "Installation test failed, see %s for details." % (log_fname)
                else:
                    self.uploadFile(log_fpath)
                    shutil.rmtree(tmp_dir)
                    os.unlink(yumcfg_file)

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

