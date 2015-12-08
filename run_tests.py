import koji
from koji.tasks import BaseTaskHandler
import sys

class RunTestsTask(BaseTaskHandler):
    Methods = ['runTests']
    _taskWeight = 2.0

    def __init__(self, *args, **kwargs):
        super(RunTestsTask, self).__init__(*args, **kwargs)

    def handler(self, build_id):
        print "### RunTests called. ###\n"
        rpms = self.session.listBuildRPMs(build_id)
        kojidir_prefix = "/mnt/koji/packages/"
        s = ""
        for rpm_info in rpms:
            if rpm_info['arch'] == 'src':
                continue
            rpm_fname = "%s.%s.rpm" % (rpm_info['nvr'], rpm_info['arch'])
            rpm_path = kojidir_prefix + "/".join(map(rpm_info.get, ['name', 'version', 'release', 'arch'])) + "/" + rpm_fname
            s += "Installing RPM file: " + rpm_path + "\n"

        return "Result: success; build_id: %s, rpms: %s" % (str(build_id), s)

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
                                                arglist = [build_id],
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

