import koji
from koji.tasks import BaseTaskHandler
import sys

import time  # DBG

class RunTestsPlugin(BaseTaskHandler):
    Methods = ['runtests']
    _taskWeight = 2.0

    def __init__(self, *args, **kwargs):
        super(RunTestsPlugin, self).__init__(*args, **kwargs)

    def handler(self, *args, **kwargs):
        print "### RunTests called. ###\n"
        time.sleep(15)
        #raise Exception
        return "run_tests/builder(%s, %s): success" % (str(args), str(kwargs))

# Override tagBuild task handler to add post-build tests
class TagBuildTask(BaseTaskHandler):

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
            task_id = self.session.host.subtask(method = 'runtests',
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

