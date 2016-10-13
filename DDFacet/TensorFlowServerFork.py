def fork_tensorflow_server():
    import atexit
    import os
    import select
    import signal
    import subprocess
    import sys
    import time
    import DDFacet

    import DDFacet.Other.MyLogger as MyLogger
    log=MyLogger.getLogger(" TensorflowServerFork").logger

    script = '{d}/tf_server_fork.py'.format(d=os.path.dirname(DDFacet.__file__))

    proc = subprocess.Popen([sys.executable, script],
        preexec_fn=os.setsid,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)

    # Register a handler to kill the child process on exit
    atexit.register(lambda p: os.kill(p.pid, signal.SIGKILL), proc)

    try:
        # Wait for 10 seconds to see if the child stdout is ready to read
        r, w, e = select.select([proc.stdout], [], [], 10)

        # No output was received, fall over
        if proc.stdout not in r:
            raise ValueError("Tensorflow child process "
                "did not return it's server target.")

        # Some output was received, try and get the server target
        tf_server_target = proc.stdout.readline().rstrip()

        # Do some sanity checking here
        if not tf_server_target.startswith("grpc://localhost"):
            raise ValueError("Tensorflow child process did not return a "
                "valid server target. Received '%s'." % tf_server_target)
    except ValueError:
        # Log exception, kill the child process and rethrow to fall over
        log.exception('Exception spawning tensorflow server:')
        os.kill(proc.pid, signal.SIGKILL)
        raise
    else:
        log.info("Tensorflow server target is '%s'" % tf_server_target)

    return tf_server_target

