import koji
from koji.tasks import BaseTaskHandler
import sys
import os
from tempfile import mkdtemp, mkstemp
import shutil


# Handler for running post-build tests
class RunTestsTask(BaseTaskHandler):
    Methods = ['runTests']
    _taskWeight = 2.0

    def __init__(self, *args, **kwargs):
        super(RunTestsTask, self).__init__(*args, **kwargs)

    def handler(self, tag_id, build_id):
        print "### RunTests called. ###\n"
        rpms = self.session.listBuildRPMs(build_id)
        kojidir_prefix = "/mnt/koji/packages/"
        s = ""
        for rpm_info in rpms:
            if rpm_info['arch'] == 'src':
                continue
            rpm_fname = "%s.%s.rpm" % (rpm_info['nvr'], rpm_info['arch'])
            rpm_path = kojidir_prefix + "/".join(map(rpm_info.get, ['name', 'version', 'release', 'arch']) + [rpm_fname])
            s += "Installing RPM file: " + rpm_path + "\n"

            taginfo = self.session.getTag(tag_id, strict=True)
            repo_url = "/mnt/koji/repos/" + taginfo['name'] + "-build/latest/" + rpm_info['arch'] + "/"
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
[repository]
name=VirtuozzoLinux
baseurl=%s
enabled=1
gpgcheck=0
""" % (repo_url))
            os.close(yumcfg_fd)
            tmpdir = mkdtemp(prefix='koji-test-root-')
            res = os.system("yum install -y -c " + yumcfg_file + " --installroot=" + tmpdir + " " + rpm_path + ' >/tmp/koji-plugin-test.log 2>&1')
            shutil.rmtree(tmpdir)
            os.unlink(yumcfg_file)

        return "Result: success; build_id: %s\ndata: %s\nres: %s" % (str(build_id), s, str(res))

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

            #XXX - add more post tests
            task_id = self.session.host.subtask(method = 'runTests',
                                                arglist = [tag_id, build_id],
                                                label = 'test',
                                                parent = self.id,
                                                arch = 'noarch')
            self.wait(task_id)

            self.session.host.tagBuild(self.id, tag_id, build_id, force=force, fromtag=fromtag)
            self.session.host.tagNotification(True, tag_id, fromtag, build_id, user_id, ignore_success)
        except Exception, e:
            exctype, value = sys.exc_info()[:2]
            self.session.host.tagNotification(False, tag_id, fromtag, build_id, user_id, ignore_success, "%s: %s" % (exctype, value))
            raise e

