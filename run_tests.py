import koji
from koji.tasks import BaseTaskHandler
import sys
import os
import shutil
from tempfile import mkdtemp, mkstemp


# Handler for running post-build tests
class RunTestsTask(BaseTaskHandler):
    Methods = ['runTests']
    _taskWeight = 2.0

    def __init__(self, *args, **kwargs):
        super(RunTestsTask, self).__init__(*args, **kwargs)

    # Executes the command and logs its output to the specified file
    def execLog(self, cmdline, logpath, append=False):
        log_fd = open(logpath, "a" if append else "w")
        log_fd.write("==> " + cmdline + "\n")
        log_fd.close()
        res = os.system(cmdline + " >>" + logpath + " 2>&1")
        return res

    # The task handler
    def handler(self, tag_id, build_id):
        rpms = self.session.listBuildRPMs(build_id)
        tag_info = self.session.getTag(tag_id, strict=True)
        #build_info = self.session.getBuild(build_id)

        # List all available RPM files
        rpm_files = {}
        for rpm_info in rpms:
            if rpm_info['arch'] == 'src':
                # Skip src.rpm
                continue
            rpm_fname = "%s.%s.rpm" % (rpm_info['nvr'], rpm_info['arch'])
            rpm_path = "/mnt/koji/packages/" + "/".join(map(rpm_info.get, ['name', 'version', 'release', 'arch']) + [rpm_fname])
            rpm_files.setdefault(rpm_info['arch'], []).append(rpm_path)

        # Test the packages from each architecture
        for arch in rpm_files.keys():
            # Construct path to the build tag repository
            repo_path = "/mnt/koji/repos/" + tag_info['name'] + "-build/latest/"
            if arch == "i686":
                # Hack for different arch names in packages and repos
                repo_path += "i386/"
            else:
                repo_path += arch + "/"
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

            log_fname = "tests-" + arch + ".log"
            log_fpath = tmp_dir + "/" + log_fname
            cmdline = "yum install -v -y --config=%(yumcfg_file)s --installroot=%(tmp_dir)s " % locals() + " ".join(rpm_files[arch])
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

